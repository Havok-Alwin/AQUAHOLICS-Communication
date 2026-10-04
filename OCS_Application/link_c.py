# ============================================================
# AQUAHOLICS ROBOTX 2026
# LINK C: OCS -> OPERATOR DISPLAY (read-only)
#
# Server-Sent Events on http://127.0.0.1:5081/linkc (config.LINK_C_*).
# Every message is one `data:` line of JSON:
#
#   state    {ch:'state', t, ...}  the whole OCS picture: RoboCommand
#            connection, run, preflight, commands, course, geofence, Task 4.
#            Every second and right after a change.
#   log      {ch:'log', t, kind, text}  each [COMMAND] / [ERROR] / [VEHICLE]
#            console line, in order. A new client first gets the last ones.
#
# Rules (MissionPlanner_dissected/CLAUDE.md, "Three links"):
# - Link C must never block the OCS. OCS threads only append to memory and
#   set an event; each display has its own thread doing the socket writes.
#   A slow display loses old log lines (counted in state.log_dropped) and
#   is dropped if a write blocks for WRITE_TIMEOUT_S; it reconnects by itself.
# - Read-only: nothing the display sends can reach RoboCommand.
# - Only pages from allowed origins may read it (CORS); no Origin header
#   (curl, tests) is allowed.
#
# Standard library only, like vehicle_link.py.
# ============================================================

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


WRITE_TIMEOUT_S = 2.0
REPLAY_LOG_LINES = 100
CLIENT_LOG_MAX = 500


def _now_ms():
    return int(time.time() * 1000)


class Task4State:
    """What the operator (and the map) must see of Task 4, from the accepted commands.

    keep_out_zone adds a zone for its vehicle type; all_clear removes that
    type's zones; moving_object_alert replaces the moving object (the display
    extrapolates it and marks it STALE when old). assistance_request and
    readiness_confirm are kept as the latest of each.
    """

    def __init__(self, rx_common_pb2=None):
        self._types = rx_common_pb2
        self.keep_out_zones = []
        self.moving_object = None
        self.assistance_request = None
        self.readiness_confirm = None

    def _type_name(self, value):
        if self._types is None:
            return str(value)
        try:
            return self._types.VehicleType.Name(value).replace("TYPE_", "")
        except ValueError:
            return str(value)

    def apply(self, command_type, body, seq, at_ms=None):
        at = at_ms if at_ms is not None else _now_ms()
        if command_type == "keep_out_zone":
            self.keep_out_zones.append({
                "vehicle_type": self._type_name(body.vehicle_type),
                "center": [body.center.latitude, body.center.longitude],
                "radius_m": body.radius_m,
                "seq": seq,
                "at": at,
            })
        elif command_type == "all_clear":
            cleared = self._type_name(body.vehicle_type)
            self.keep_out_zones = [z for z in self.keep_out_zones if z["vehicle_type"] != cleared]
        elif command_type == "moving_object_alert":
            self.moving_object = {
                "position": [body.position.latitude, body.position.longitude],
                "heading_deg": body.heading_deg,
                "speed_mps": body.speed_mps,
                "affected": [self._type_name(v) for v in body.affected_vehicle_types],
                "seq": seq,
                "at": at,
            }
        elif command_type == "assistance_request":
            self.assistance_request = {
                "position": [body.position.latitude, body.position.longitude],
                "vehicle_type": self._type_name(body.vehicle_type),
                "seq": seq,
                "at": at,
            }
        elif command_type == "readiness_confirm":
            self.readiness_confirm = {"report_seq": body.report_seq, "vehicle_id": body.vehicle_id,
                                      "seq": seq, "at": at}

    def reset(self):
        """A new run: nothing from the previous run may stay on the map."""
        self.__init__(self._types)

    def as_dict(self):
        return {
            "keep_out_zones": list(self.keep_out_zones),
            "moving_object": self.moving_object,
            "assistance_request": self.assistance_request,
            "readiness_confirm": self.readiness_confirm,
        }


class _Client:
    def __init__(self):
        self.wake = threading.Event()
        self.log = deque()
        self.dropped = 0


class LinkCServer:
    """snapshot() must return the state dict (without ch/t); it is called on the
    display threads, never on the OCS threads that call log() / notify()."""

    def __init__(self, host, port, allowed_origins, snapshot, period_s=1.0):
        self.host = host
        self.port = port
        self.allowed_origins = set(allowed_origins)
        self.snapshot = snapshot
        self.period_s = period_s
        self._lock = threading.Lock()
        self._clients = set()
        self._history = deque(maxlen=REPLAY_LOG_LINES)
        self._stop = threading.Event()
        self._server = None

    # ---------- called by the OCS: O(1), never blocks on a display ----------

    def notify(self):
        with self._lock:
            clients = list(self._clients)
        for c in clients:
            c.wake.set()

    def log(self, kind, text):
        entry = {"ch": "log", "t": _now_ms(), "kind": kind, "text": text}
        with self._lock:
            self._history.append(entry)
            for c in self._clients:
                if len(c.log) >= CLIENT_LOG_MAX:
                    c.log.popleft()
                    c.dropped += 1
                c.log.append(entry)
                c.wake.set()

    @property
    def client_count(self):
        with self._lock:
            return len(self._clients)

    # ---------- server ----------

    def start(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass  # the OCS log stays readable

            def do_GET(self):
                origin = self.headers.get("Origin")
                if self.path.split("?")[0] != "/linkc":
                    self.send_error(404)
                    return
                if origin and origin not in server.allowed_origins:
                    self.send_error(403, "origin not allowed")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                if origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.connection.settimeout(WRITE_TIMEOUT_S)
                server._serve(self)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="robotx-link-c", daemon=True).start()

    def stop(self):
        self._stop.set()
        self.notify()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    def _serve(self, handler):
        client = _Client()
        with self._lock:
            client.log.extend(self._history)  # recent lines for a new or reloaded display
            self._clients.add(client)
        try:
            next_state = 0.0
            while not self._stop.is_set():
                woke = client.wake.wait(timeout=max(0.0, next_state - time.monotonic()))
                client.wake.clear()
                with self._lock:
                    lines = list(client.log)
                    client.log.clear()
                    dropped = client.dropped
                out = [self._frame(line) for line in lines]
                if woke or time.monotonic() >= next_state:
                    state = dict(self.snapshot())
                    state.update({"ch": "state", "t": _now_ms(), "log_dropped": dropped})
                    out.append(self._frame(state))
                    next_state = time.monotonic() + self.period_s
                if out:
                    handler.wfile.write(b"".join(out))
                    handler.wfile.flush()
        except (OSError, ValueError):
            pass  # display closed or too slow: drop it, it reconnects by itself
        finally:
            with self._lock:
                self._clients.discard(client)

    @staticmethod
    def _frame(msg):
        return b"data: " + json.dumps(msg, separators=(",", ":")).encode() + b"\n\n"
