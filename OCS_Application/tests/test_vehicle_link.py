# Link A client (vehicle_link.py) against a small stdlib WebSocket server.
# Run from OCS_Application:  python3 -m unittest discover -s tests
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
OCS = os.path.dirname(HERE)
sys.path.insert(0, OCS)
sys.path.insert(0, os.path.join(os.path.dirname(OCS), "Robocmd_Application", "gen", "python"))

import vehicle_link  # noqa: E402

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class FakeBackend:
    """Accepts one WebSocket client at a time; the test sends frames with send_* methods."""

    def __init__(self):
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.url = f"ws://127.0.0.1:{self.port}/linka"
        self.conn = None
        self.accepted = threading.Event()
        self.client_frames = []  # (opcode, payload) received from the client, unmasked
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(4096)
            key = [l.split(":", 1)[1].strip() for l in request.decode().split("\r\n")
                   if l.lower().startswith("sec-websocket-key")][0]
            accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
            conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                          f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            self.conn = conn
            self.accepted.set()
            threading.Thread(target=self._read_loop, args=(conn,), daemon=True).start()

    def _read_loop(self, conn):
        buf = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while len(buf) >= 6:
                    b0, b1 = buf[0], buf[1]
                    n = b1 & 0x7F
                    if n >= 126 or len(buf) < 6 + n:
                        break
                    assert b1 & 0x80, "client frames must be masked"
                    mask, payload = buf[2:6], buf[6:6 + n]
                    self.client_frames.append((b0 & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(payload))))
                    buf = buf[6 + n:]
        except OSError:
            return

    def send_frame(self, opcode, payload, fin=True):
        n = len(payload)
        header = struct.pack("!BB", (0x80 if fin else 0) | opcode, n) if n < 126 else struct.pack("!BBH", (0x80 if fin else 0) | opcode, 126, n)
        self.conn.sendall(header + payload)

    def send_json(self, msg):
        self.send_frame(0x1, json.dumps(msg).encode())

    def drop_client(self):
        self.accepted.clear()
        self.conn.shutdown(socket.SHUT_RDWR)
        self.conn.close()

    def close(self):
        self.listener.close()
        if self.conn:
            self.conn.close()


def hb(vehicle="USV1", **extra):
    msg = {"ch": "hb", "vehicle": vehicle, "t": 1, "type": "USV", "state": "AUTO",
           "lat": 1.2966, "lng": 103.7764, "spd_mps": 1.5, "heading_deg": 90.0,
           "roll_deg": 1.0, "pitch_deg": -0.5, "missing": []}
    msg.update(extra)
    return msg


def wait_for(condition, what, seconds=5.0):
    until = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > until:
            raise AssertionError(f"timed out waiting for: {what}")
        time.sleep(0.01)


class VehicleLinkClientTests(unittest.TestCase):

    def setUp(self):
        self.backend = FakeBackend()
        self.events = []
        self.client = vehicle_link.VehicleLinkClient(
            self.backend.url, ["USV1", "UAV1"], stale_s=0.4,
            on_event=lambda text, level="info": self.events.append((level, text)),
            reconnect_min_s=0.1, reconnect_max_s=0.2)
        self.client.start()
        self.assertTrue(self.backend.accepted.wait(5))

    def tearDown(self):
        self.client.stop()
        self.backend.close()

    def texts(self):
        return [t for _, t in list(self.events)]

    def test_fresh_heartbeat_is_returned_once_then_goes_stale(self):
        self.backend.send_json(hb())
        got = self.client.wait_for_new(0, timeout=2)
        self.assertEqual([("USV1", 1)], [(v, h["t"]) for v, h, _ in got])
        serial = got[0][2]

        # Each hb at most once.
        self.assertEqual([], self.client.wait_for_new(serial, timeout=0.1))

        # Nothing new for longer than stale_s: never returned, operator told heartbeats stopped.
        time.sleep(0.5)
        wait_for(lambda: any("USV1 telemetry STALE" in t for t in self.texts()), "STALE event")
        self.assertEqual("NOT CONFIGURED", self.client.status()["USV1"])

    def test_stale_message_is_never_returned(self):
        self.backend.send_json(hb())
        wait_for(lambda: self.client.status()["USV1"] == "LIVE", "LIVE")
        time.sleep(0.5)  # older than stale_s before anyone asked
        self.assertEqual([], self.client.wait_for_new(0, timeout=0.1))

    def test_missing_fields_and_link_state_are_reported(self):
        self.backend.send_json(hb(vehicle="UAV1", type="UAV", missing=["altitude_hae_m"]))
        self.backend.send_json({"ch": "backend", "t": 1, "links": {
            "USV1": {"state": "closed", "error": "no vehicle on /dev/ttyUSB0"},
            "UAV1": {"state": "live", "error": None}}})
        wait_for(lambda: any("UAV1 heartbeat published WITHOUT altitude_hae_m" in t for t in self.texts()), "missing")
        wait_for(lambda: any("USV1 link CLOSED: no vehicle on /dev/ttyUSB0" in t for t in self.texts()), "link")
        self.assertEqual("CLOSED", self.client.status()["USV1"])

    def test_ping_is_answered_and_fragments_are_joined(self):
        self.backend.send_frame(0x9, b"hi")
        text = json.dumps(hb()).encode()
        self.backend.send_frame(0x1, text[:10], fin=False)
        time.sleep(0.3)  # a client read timeout between the fragments
        self.backend.send_frame(0x0, text[10:])
        got = self.client.wait_for_new(0, timeout=2)
        self.assertEqual(1, len(got))
        wait_for(lambda: (0xA, b"hi") in self.backend.client_frames, "pong")

    def test_reconnects_after_the_backend_drops(self):
        self.backend.send_json(hb())
        serial = self.client.wait_for_new(0, timeout=2)[0][2]
        self.backend.drop_client()
        wait_for(lambda: any("lost the vehicle backend" in t for t in self.texts()), "lost event")
        self.assertTrue(self.backend.accepted.wait(5), "client reconnected")
        self.backend.send_json(hb(t=2))
        got = self.client.wait_for_new(serial, timeout=2)
        self.assertEqual([2], [h["t"] for _, h, _ in got])


class HeartbeatFieldsTests(unittest.TestCase):

    def setUp(self):
        import common_pb2
        from robotx import rx_common_pb2, rx_reports_pb2
        self.common_pb2, self.rx_common_pb2, self.rx_reports_pb2 = common_pb2, rx_common_pb2, rx_reports_pb2

    def build(self, msg):
        fields = vehicle_link.heartbeat_fields(msg, self.rx_common_pb2.TASK_NONE, self.common_pb2, self.rx_common_pb2)
        return self.rx_reports_pb2.Heartbeat(**fields)

    def test_usv_heartbeat(self):
        h = self.build(hb())
        self.assertEqual(self.common_pb2.STATE_AUTO, h.state)
        self.assertAlmostEqual(1.2966, h.position.latitude)
        self.assertEqual(self.rx_common_pb2.TYPE_USV, h.vehicle_type)
        self.assertEqual(self.rx_common_pb2.TASK_NONE, h.current_task)
        self.assertFalse(h.HasField("altitude_hae_m"))
        self.assertFalse(h.HasField("flight_phase"))
        self.assertFalse(h.HasField("depth_m"))

    def test_unknown_values_stay_unset(self):
        msg = hb(state="UNKNOWN", missing=["position"])
        del msg["lat"], msg["lng"]
        h = self.build(msg)
        self.assertFalse(h.HasField("position"))  # never 0,0
        self.assertEqual(self.common_pb2.STATE_UNKNOWN, h.state)

    def test_uav_heartbeat(self):
        h = self.build(hb(vehicle="UAV1", type="UAV", state="MANUAL", altitude_hae_m=23.456, flight_phase="AIRBORNE"))
        self.assertEqual(self.rx_common_pb2.TYPE_UAV, h.vehicle_type)
        self.assertEqual(self.common_pb2.STATE_MANUAL, h.state)
        self.assertAlmostEqual(23.456, h.altitude_hae_m, places=4)  # float in the schema
        self.assertEqual(self.rx_common_pb2.FLIGHT_PHASE_AIRBORNE, h.flight_phase)
        h.SerializeToString()


if __name__ == "__main__":
    unittest.main()
