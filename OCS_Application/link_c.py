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
# - Read-only, with one exception: the operator actions passed in `actions`
#   (POST, JSON in and out). Today only POST /task4/ready (the operator's
#   ReadinessReport, task4.py). Anything else the display could send is refused.
# - Only pages from allowed origins may read it or post (CORS, Origin
#   checked on every request); no Origin header (curl, tests) is allowed.
#
# Standard library only, like vehicle_link.py.
# ============================================================

import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


WRITE_TIMEOUT_S = 2.0
MAX_ACTION_BODY = 4096
REPLAY_LOG_LINES = 100
CLIENT_LOG_MAX = 500


def _now_ms():
    return int(time.time() * 1000)


class Task4State:
    """What the operator (and the map) must see of Task 4, from the accepted commands.

    keep_out_zone adds a zone for its vehicle type; all_clear removes that
    type's zones; moving_object_alert replaces the moving object (the display
    extrapolates it and marks it STALE when old). The latest assistance_request
    carries its response chain: IncidentAck, operator ReadinessReport and
    RoboCommand's ReadinessConfirm (task4.py). Thread-safe.
    """

    def __init__(self, rx_common_pb2=None):
        self._types = rx_common_pb2
        self._lock = threading.RLock()
        self.keep_out_zones = []
        self.moving_object = None
        self.assistance_request = None
        self.readiness_confirm = None
        self.last_all_clear = None
        self._acks = {}  # command seq -> ack dict (task4.Task4Responder)

    def type_name(self, value):
        if self._types is None:
            return str(value)
        try:
            return self._types.VehicleType.Name(value).replace("TYPE_", "")
        except ValueError:
            return str(value)

    def apply(self, command_type, body, seq, at_ms=None):
        at = at_ms if at_ms is not None else _now_ms()
        with self._lock:
            if command_type == "keep_out_zone":
                self.keep_out_zones.append({
                    "vehicle_type": self.type_name(body.vehicle_type),
                    "center": [body.center.latitude, body.center.longitude],
                    "radius_m": body.radius_m,
                    "seq": seq,
                    "at": at,
                    "ack": None,
                })
            elif command_type == "all_clear":
                cleared = self.type_name(body.vehicle_type)
                self.keep_out_zones = [z for z in self.keep_out_zones if z["vehicle_type"] != cleared]
                self.last_all_clear = {"vehicle_type": cleared, "seq": seq, "at": at, "ack": None}
            elif command_type == "moving_object_alert":
                self.moving_object = {
                    "position": [body.position.latitude, body.position.longitude],
                    "heading_deg": body.heading_deg,
                    "speed_mps": body.speed_mps,
                    "affected": [self.type_name(v) for v in body.affected_vehicle_types],
                    "seq": seq,
                    "at": at,
                }
            elif command_type == "assistance_request":
                self.assistance_request = {
                    "position": [body.position.latitude, body.position.longitude],
                    "vehicle_type": self.type_name(body.vehicle_type),
                    "seq": seq,
                    "at": at,
                    "ack": None,
                    "readiness": None,   # {report_seq, ok, at}: the operator's ReadinessReport
                    "confirmed": None,   # {seq, at}: RoboCommand's ReadinessConfirm
                }
            elif command_type == "readiness_confirm":
                self.readiness_confirm = {"report_seq": body.report_seq, "vehicle_id": body.vehicle_id,
                                          "seq": seq, "at": at}
                a = self.assistance_request
                if a and a["readiness"] and a["readiness"]["report_seq"] == body.report_seq:
                    a["confirmed"] = {"seq": seq, "at": at}

    def set_ack(self, command_seq, vehicle, report_seq, ok, detail=""):
        """The IncidentAck outcome for a command (task4.py)."""
        ack = {"vehicle": vehicle, "report_seq": report_seq, "ok": ok, "detail": detail, "at": _now_ms()}
        with self._lock:
            self._acks[command_seq] = ack
            for item in self.keep_out_zones + [self.assistance_request, self.last_all_clear]:
                if item and item["seq"] == command_seq:
                    item["ack"] = ack

    def set_readiness(self, command_seq, report_seq, ok):
        with self._lock:
            a = self.assistance_request
            if a and a["seq"] == command_seq:
                a["readiness"] = {"report_seq": report_seq, "ok": ok, "at": _now_ms()}

    def reset(self):
        """A new run: nothing from the previous run may stay on the display."""
        self.__init__(self._types)

    def as_dict(self):
        with self._lock:
            copy = lambda d: None if d is None else {k: (dict(v) if isinstance(v, dict) else v) for k, v in d.items()}
            return {
                "keep_out_zones": [copy(z) for z in self.keep_out_zones],
                "moving_object": copy(self.moving_object),
                "assistance_request": copy(self.assistance_request),
                "readiness_confirm": copy(self.readiness_confirm),
                "last_all_clear": copy(self.last_all_clear),
            }


class _Client:
    def __init__(self):
        self.wake = threading.Event()
        self.log = deque()
        self.dropped = 0


class LinkCServer:
    """snapshot() must return the state dict (without ch/t); it is called on the
    display threads, never on the OCS threads that call log() / notify()."""

    def __init__(self, host, port, allowed_origins, snapshot, period_s=1.0, actions=None):
        self.host = host
        self.port = port
        self.allowed_origins = set(allowed_origins)
        self.snapshot = snapshot
        self.period_s = period_s
        # path -> callable(dict) -> (http_status, dict); runs on the request's own thread.
        self.actions = dict(actions or {})
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

            def _origin_ok(self):
                origin = self.headers.get("Origin")
                return origin is None or origin in server.allowed_origins

            def _cors(self):
                origin = self.headers.get("Origin")
                if origin:
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")

            def do_OPTIONS(self):  # CORS preflight for the JSON POST
                if self.path not in server.actions or not self._origin_ok():
                    self.send_error(403)
                    return
                self.send_response(204)
                self._cors()
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Max-Age", "600")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                action = server.actions.get(self.path)
                if action is None:
                    self.send_error(404)
                    return
                if not self._origin_ok():
                    self.send_error(403, "origin not allowed")
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > MAX_ACTION_BODY:
                    self.send_error(400, "body missing or too large")
                    return
                try:
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("not an object")
                except ValueError:
                    self.send_error(400, "body must be a JSON object")
                    return
                try:
                    status, result = action(body)
                except Exception as error:  # an action bug must not kill the server thread
                    status, result = 500, {"ok": False, "detail": f"action failed: {error}"}
                payload = json.dumps(result).encode()
                self.send_response(status)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

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
