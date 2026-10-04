# ============================================================
# AQUAHOLICS ROBOTX 2026
# LINK A: VEHICLE BACKEND -> OCS
#
# Real vehicle telemetry for the RobotX heartbeats. The vehicle backend
# (MissionPlanner_dissected/backend, .NET) talks MAVLink to each vehicle and
# serves a local WebSocket (default ws://127.0.0.1:5080/linka) with JSON:
#
#   hb       {ch:'hb', vehicle, t, type, state, lat, lng, spd_mps, heading_deg,
#             roll_deg, pitch_deg, altitude_hae_m, flight_phase, missing:[...]}
#            2 Hz per vehicle, ONLY while that vehicle's link is LIVE.
#   backend  {ch:'backend', t, links:{USV1:{state, error}}}   1 Hz + on change
#
# Rules (MissionPlanner_dissected/CLAUDE.md, "Three links"):
# - Never publish old data as current: a heartbeat is published only from an
#   hb received less than LINK_A_STALE_S ago, and each hb at most once.
# - If link A goes stale, flag the vehicle to the operator (on_event).
# - A field the backend does not know is absent and listed in `missing`; it is
#   left unset in the protobuf, never filled with a made-up value.
#
# Standard library only (a minimal RFC 6455 client), so the validated OCS
# dependencies (paho-mqtt, protobuf) do not change.
# ============================================================

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
from urllib.parse import urlparse


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WebSocketClosed(Exception):
    pass


class MiniWebSocket:
    """Blocking WebSocket client: text frames in, control frames handled, masked frames out."""

    def __init__(self, url, timeout=5.0):
        u = urlparse(url)
        if u.scheme != "ws":
            raise ValueError(f"only ws:// is supported (local link): {url}")
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or 80
        self.path = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self._buf = b""
        self._message, self._message_opcode = b"", None  # fragments so far (survive a timeout)
        self._handshake()

    def _handshake(self):
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        while b"\r\n\r\n" not in self._buf:
            self._fill()
        head, self._buf = self._buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if not lines[0].startswith("HTTP/1.1 101"):
            raise ConnectionError(f"WebSocket upgrade refused: {lines[0]}")
        headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:])}
        expected = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise ConnectionError("WebSocket upgrade: bad Sec-WebSocket-Accept")

    def _fill(self):
        chunk = self.sock.recv(65536)
        if not chunk:
            raise WebSocketClosed("connection closed by the backend")
        self._buf += chunk

    def _take(self, n):
        while len(self._buf) < n:
            self._fill()
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def send_frame(self, opcode, payload=b""):
        # Client frames must be masked (RFC 6455 5.3).
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv_text(self):
        """Next complete text message. Answers pings; raises WebSocketClosed on close.
        A socket timeout (set with settimeout) raises socket.timeout and loses no data."""
        while True:
            self._peek_frame()  # may raise socket.timeout before anything is consumed
            b0, b1 = self._take(2)
            fin, opcode = b0 & 0x80, b0 & 0x0F
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._take(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._take(8))[0]
            mask = self._take(4) if b1 & 0x80 else None
            payload = self._take(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == OP_PING:
                self.send_frame(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                try:
                    self.send_frame(OP_CLOSE, payload[:2])
                except OSError:
                    pass
                raise WebSocketClosed("closed by the backend")
            if opcode in (OP_TEXT, OP_BINARY):
                self._message, self._message_opcode = payload, opcode
            elif opcode == OP_CONT:
                self._message += payload
            if fin:
                message, kind = self._message, self._message_opcode
                self._message, self._message_opcode = b"", None
                if kind == OP_TEXT:
                    return message.decode("utf-8")
                # binary: not used on link A

    def _peek_frame(self):
        # Wait for a whole frame header + payload before consuming, so a timeout mid-frame
        # never desynchronises the stream.
        while True:
            if len(self._buf) >= 2:
                n = self._buf[1] & 0x7F
                need = 2 + (2 if n == 126 else 8 if n == 127 else 0)
                if len(self._buf) >= need:
                    if n == 126:
                        n = struct.unpack("!H", self._buf[2:4])[0]
                    elif n == 127:
                        n = struct.unpack("!Q", self._buf[2:10])[0]
                    need += (4 if self._buf[1] & 0x80 else 0) + n
                    if len(self._buf) >= need:
                        return
            self._fill()

    def settimeout(self, seconds):
        self.sock.settimeout(seconds)

    def close(self):
        try:
            self.send_frame(OP_CLOSE, struct.pack("!H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ============================================================
# HEARTBEAT FIELDS
# ============================================================

def heartbeat_fields(hb, current_task, common_pb2, rx_common_pb2):
    """RxReport.heartbeat keyword arguments from one link A `hb` message.

    Only fields the backend sent are set; absent ones stay unset in the protobuf
    (proto3 optional), never a placeholder value.
    """
    fields = {
        "state": common_pb2.RobotState.Value("STATE_" + hb.get("state", "UNKNOWN")),
        "current_task": current_task,
    }
    if hb.get("type") in ("USV", "UAV"):
        fields["vehicle_type"] = rx_common_pb2.VehicleType.Value("TYPE_" + hb["type"])
    if "lat" in hb and "lng" in hb:
        fields["position"] = common_pb2.LatLng(latitude=hb["lat"], longitude=hb["lng"])
    for name in ("spd_mps", "heading_deg", "roll_deg", "pitch_deg", "altitude_hae_m"):
        if name in hb:
            fields[name] = float(hb[name])
    if hb.get("flight_phase") in ("GROUNDED", "AIRBORNE"):
        fields["flight_phase"] = rx_common_pb2.FlightPhase.Value("FLIGHT_PHASE_" + hb["flight_phase"])
    return fields


# ============================================================
# CLIENT
# ============================================================

class VehicleLinkClient:
    """Keeps link A open (reconnecting), holds the latest hb per vehicle, and
    reports changes the operator must see through on_event(text, level)."""

    def __init__(self, url, vehicle_ids, stale_s=1.0, on_event=None,
                 reconnect_min_s=0.5, reconnect_max_s=5.0):
        self.url = url
        self.vehicle_ids = list(vehicle_ids)
        self.stale_s = stale_s
        self.on_event = on_event or (lambda text, level="info": None)
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread = None
        self._ws = None
        self.connected = False
        # vehicle -> (hb dict, monotonic receive time, serial number)
        self._latest = {}
        self._serial = 0
        self._links = {}            # vehicle -> {"state", "error"} from the backend message
        self._fresh = {}            # vehicle -> bool, last reported freshness
        self._missing = {}          # vehicle -> tuple, last reported missing fields

    # ---------- lifecycle ----------

    def start(self):
        self._thread = threading.Thread(target=self._run, name="robotx-link-a", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        ws = self._ws
        if ws is not None:
            ws.close()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ---------- for the heartbeat publisher ----------

    def wait_for_new(self, after_serial, timeout):
        """Fresh hb messages newer than after_serial, as [(vehicle, hb, serial)].
        Blocks up to timeout for at least one. Stale ones are never returned."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                now = time.monotonic()
                fresh = [(vid, hb, serial) for vid, (hb, at, serial) in self._latest.items()
                         if serial > after_serial and now - at <= self.stale_s]
                if fresh or now >= deadline or self._stop.is_set():
                    return sorted(fresh, key=lambda x: x[2])
                self._cond.wait(deadline - now)

    def status(self):
        """{vehicle: text} for the operator status line."""
        now = time.monotonic()
        out = {}
        with self._cond:
            for vid in self.vehicle_ids:
                entry = self._latest.get(vid)
                link = self._links.get(vid)
                if not self.connected:
                    out[vid] = "NO BACKEND"
                elif entry and now - entry[1] <= self.stale_s:
                    out[vid] = "LIVE"
                elif link:
                    out[vid] = link["state"].upper()
                else:
                    out[vid] = "NOT CONFIGURED"
        return out

    # ---------- internals ----------

    def _run(self):
        delay = self.reconnect_min_s
        while not self._stop.is_set():
            try:
                ws = MiniWebSocket(self.url, timeout=3.0)
            except (OSError, ConnectionError, ValueError) as error:
                if self.connected is not False or delay == self.reconnect_min_s:
                    self.on_event(f"[VEHICLE LINK] vehicle backend not reachable at {self.url}: {error}", "error")
                self._set_connected(False)
                self._stop.wait(delay)
                delay = min(delay * 2, self.reconnect_max_s)
                continue
            self._ws = ws
            delay = self.reconnect_min_s
            self._set_connected(True)
            try:
                ws.settimeout(0.25)
                while not self._stop.is_set():
                    try:
                        text = ws.recv_text()
                    except socket.timeout:
                        text = None
                    if text is not None:
                        self._handle(text)
                    self._check_freshness()
            except (OSError, WebSocketClosed) as error:
                if not self._stop.is_set():
                    self.on_event(f"[VEHICLE LINK] lost the vehicle backend: {error}", "error")
            finally:
                ws.close()
                self._ws = None
                self._set_connected(False)

    def _set_connected(self, value):
        if value and not self.connected:
            self.on_event(f"[VEHICLE LINK] connected to the vehicle backend at {self.url}", "info")
        with self._cond:
            self.connected = value
            if not value:
                self._links.clear()
            self._cond.notify_all()
        if not value:
            self._check_freshness()

    def _handle(self, text):
        try:
            msg = json.loads(text)
        except ValueError:
            self.on_event("[VEHICLE LINK] ignored a message that is not JSON", "warning")
            return
        if not isinstance(msg, dict):
            return
        if msg.get("ch") == "hb" and msg.get("vehicle") in self.vehicle_ids:
            vid = msg["vehicle"]
            with self._cond:
                self._serial += 1
                self._latest[vid] = (msg, time.monotonic(), self._serial)
                self._cond.notify_all()
            missing = tuple(msg.get("missing") or ())
            if self._missing.get(vid) != missing:
                self._missing[vid] = missing
                if missing:
                    self.on_event(f"[VEHICLE] {vid} heartbeat published WITHOUT {', '.join(missing)} "
                                  "(not known by the vehicle backend)", "warning")
                else:
                    self.on_event(f"[VEHICLE] {vid} heartbeat complete", "info")
        elif msg.get("ch") == "backend" and isinstance(msg.get("links"), dict):
            for vid in self.vehicle_ids:
                link = msg["links"].get(vid)
                with self._cond:
                    old = self._links.get(vid)
                    self._links[vid] = link
                if link != old and link is not None and link.get("state") != "live":
                    reason = f": {link['error']}" if link.get("error") else ""
                    self.on_event(f"[VEHICLE] {vid} link {link['state'].upper()}{reason}", "warning")
        self._check_freshness()

    def _check_freshness(self):
        now = time.monotonic()
        for vid in self.vehicle_ids:
            with self._cond:
                entry = self._latest.get(vid)
                fresh = bool(entry) and self.connected and now - entry[1] <= self.stale_s
            was = self._fresh.get(vid)
            if was is None and not fresh:
                self._fresh[vid] = False
                continue
            if fresh != was:
                self._fresh[vid] = fresh
                if fresh:
                    self.on_event(f"[VEHICLE] {vid} live telemetry: heartbeats to RoboCommand running", "info")
                else:
                    age = f"{now - entry[1]:.1f} s" if entry else "never"
                    self.on_event(f"[VEHICLE] {vid} telemetry STALE (last update {age} ago): "
                                  "heartbeats to RoboCommand STOPPED", "error")
