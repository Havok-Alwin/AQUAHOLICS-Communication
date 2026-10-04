# Link C (link_c.py): the read-only SSE feed for the operator display, and Task 4 state.
# Run from OCS_Application:  python3 -m unittest discover -s tests
import http.client
import json
import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
OCS = os.path.dirname(HERE)
sys.path.insert(0, OCS)
sys.path.insert(0, os.path.join(os.path.dirname(OCS), "Robocmd_Application", "gen", "python"))

import link_c  # noqa: E402

ORIGIN = "http://127.0.0.1:5080"


class SseReader:
    """Reads `data:` events from the feed on a background thread."""

    def __init__(self, port, origin=ORIGIN):
        self.conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Origin": origin} if origin else {}
        self.conn.request("GET", "/linkc", headers=headers)
        self.response = self.conn.getresponse()
        self.messages = []
        if self.response.status == 200:
            threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for raw in self.response:
                line = raw.decode().strip()
                if line.startswith("data: "):
                    self.messages.append(json.loads(line[6:]))
        except (OSError, ValueError):
            pass

    def of(self, ch):
        return [m for m in list(self.messages) if m["ch"] == ch]

    def close(self):
        self.conn.close()


def wait_for(condition, what, seconds=5.0):
    until = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > until:
            raise AssertionError(f"timed out waiting for: {what}")
        time.sleep(0.01)


class LinkCServerTests(unittest.TestCase):

    def setUp(self):
        self.state = {"connection": "Connected", "run": {"run_id": 7}}
        self.server = link_c.LinkCServer("127.0.0.1", 0, [ORIGIN], lambda: dict(self.state), period_s=0.3)
        # Port 0: pick a free port, then read it back.
        self.server.start()
        self.port = self.server._server.server_address[1]

    def tearDown(self):
        self.server.stop()

    def test_state_is_sent_regularly_with_cors_for_the_display(self):
        reader = SseReader(self.port)
        self.assertEqual(200, reader.response.status)
        self.assertEqual(ORIGIN, reader.response.getheader("Access-Control-Allow-Origin"))
        self.assertEqual("text/event-stream", reader.response.getheader("Content-Type"))
        wait_for(lambda: len(reader.of("state")) >= 3, "three states")
        self.assertEqual("Connected", reader.of("state")[-1]["connection"])
        reader.close()

    def test_other_origins_are_refused(self):
        reader = SseReader(self.port, origin="http://evil.example")
        self.assertEqual(403, reader.response.status)
        reader.close()
        no_origin = SseReader(self.port, origin=None)  # not a browser page
        self.assertEqual(200, no_origin.response.status)
        no_origin.close()

    def test_change_is_pushed_at_once(self):
        self.server.period_s = 30  # only the first state comes on its own
        reader = SseReader(self.port)
        wait_for(lambda: len(reader.of("state")) == 1, "first state")
        self.state["connection"] = "Reconnecting"
        self.server.notify()
        wait_for(lambda: reader.of("state")[-1]["connection"] == "Reconnecting", "pushed state", seconds=2)
        reader.close()

    def test_log_lines_are_in_order_and_replayed_to_a_new_display(self):
        self.server.log("command", "[COMMAND] received seq=1 type=run_start")
        self.server.log("command", "[COMMAND] accepted RunStart seq=1")
        reader = SseReader(self.port)
        wait_for(lambda: len(reader.of("log")) == 2, "replayed log")
        self.server.log("error", "[ERROR] something")
        wait_for(lambda: len(reader.of("log")) == 3, "live log line")
        self.assertEqual(["command", "command", "error"], [m["kind"] for m in reader.of("log")])
        reader.close()

    def test_a_display_that_does_not_read_never_blocks_the_ocs(self):
        # Connect, then never read. log() must stay fast while the socket fills up.
        silent = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        silent.request("GET", "/linkc", headers={"Origin": ORIGIN})
        wait_for(lambda: self.server.client_count == 1, "client registered")
        text = "[COMMAND] " + "x" * 2000
        start = time.monotonic()
        for _ in range(5000):
            self.server.log("command", text)
        self.assertLess(time.monotonic() - start, 2.0)
        silent.close()


class Task4StateTests(unittest.TestCase):

    def setUp(self):
        import common_pb2
        from robotx import rx_commands_pb2, rx_common_pb2
        self.c, self.common, self.rx = rx_commands_pb2, common_pb2, rx_common_pb2
        self.task4 = link_c.Task4State(rx_common_pb2)

    def ll(self, lat, lon):
        return self.common.LatLng(latitude=lat, longitude=lon)

    def test_keep_out_zones_until_all_clear_for_that_vehicle_type(self):
        usv, uav = self.rx.TYPE_USV, self.rx.TYPE_UAV
        self.task4.apply("keep_out_zone", self.c.KeepOutZone(center=self.ll(1.28, 103.85), radius_m=15, vehicle_type=usv), 2)
        self.task4.apply("keep_out_zone", self.c.KeepOutZone(center=self.ll(1.281, 103.851), radius_m=10, vehicle_type=uav), 3)
        zones = self.task4.as_dict()["keep_out_zones"]
        self.assertEqual(["USV", "UAV"], [z["vehicle_type"] for z in zones])
        self.assertEqual([1.28, 103.85], zones[0]["center"])
        self.assertEqual(15, zones[0]["radius_m"])

        self.task4.apply("all_clear", self.c.AllClear(vehicle_type=usv), 4)
        self.assertEqual(["UAV"], [z["vehicle_type"] for z in self.task4.as_dict()["keep_out_zones"]])

    def test_moving_object_is_replaced_by_the_latest_alert(self):
        self.task4.apply("moving_object_alert", self.c.MovingObjectAlert(
            position=self.ll(1.28, 103.85), heading_deg=90, speed_mps=1.5,
            affected_vehicle_types=[self.rx.TYPE_USV]), 5, at_ms=1000)
        self.task4.apply("moving_object_alert", self.c.MovingObjectAlert(
            position=self.ll(1.2801, 103.8502), heading_deg=95, speed_mps=1.4,
            affected_vehicle_types=[self.rx.TYPE_USV, self.rx.TYPE_UAV]), 6, at_ms=2000)
        mo = self.task4.as_dict()["moving_object"]
        self.assertEqual([1.2801, 103.8502], mo["position"])
        self.assertEqual(["USV", "UAV"], mo["affected"])
        self.assertEqual(2000, mo["at"])

    def test_new_run_clears_everything(self):
        self.task4.apply("keep_out_zone", self.c.KeepOutZone(center=self.ll(1, 2), radius_m=5, vehicle_type=self.rx.TYPE_USV), 2)
        self.task4.reset()
        self.assertEqual({"keep_out_zones": [], "moving_object": None, "assistance_request": None,
                          "readiness_confirm": None}, self.task4.as_dict())


if __name__ == "__main__":
    unittest.main()
