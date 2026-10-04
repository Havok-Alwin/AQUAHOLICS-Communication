# Task 4 response chains (task4.py) and the link C operator action.
# Run from OCS_Application:  python3 -m unittest discover -s tests
import http.client
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
OCS = os.path.dirname(HERE)
sys.path.insert(0, OCS)
sys.path.insert(0, os.path.join(os.path.dirname(OCS), "Robocmd_Application", "gen", "python"))

import common_pb2  # noqa: E402
import link_c  # noqa: E402
import task4  # noqa: E402
from robotx import rx_commands_pb2 as c, rx_common_pb2 as rc  # noqa: E402

ORIGIN = "http://127.0.0.1:5080"


class FakeClient:
    def __init__(self, connected=True):
        self.connected = connected

    def is_connected(self):
        return self.connected


class Task4ResponderTests(unittest.TestCase):

    def setUp(self):
        self.state = link_c.Task4State(rc)
        self.acks, self.readies, self.lines = [], [], []
        self.next_report = 10

        def publish(kind):
            def fn(client, vehicle_id, command_seq):
                self.next_report += 1
                (self.acks if kind == "ack" else self.readies).append((vehicle_id, command_seq, self.next_report))
                return True, self.next_report
            return fn

        self.responder = task4.Task4Responder(
            self.state, lambda t: {"USV": "USV1", "UAV": "UAV1"}.get(t),
            publish("ack"), publish("ready"), lambda text, level="info": self.lines.append(text))
        self.client = FakeClient()

    def command(self, seq, **body):
        return c.RxCommand(team_id="RMKE", seq=seq, **body)

    def receive(self, cmd):
        command_type = cmd.WhichOneof("body")
        self.state.apply(command_type, getattr(cmd, command_type), cmd.seq)
        self.responder.on_command(self.client, cmd, command_type)

    def ll(self):
        return common_pb2.LatLng(latitude=1.28, longitude=103.85)

    def test_assistance_chain(self):
        # AssistanceRequest -> IncidentAck (automatic) -> ReadinessReport (operator) -> ReadinessConfirm
        self.receive(self.command(5, assistance_request=c.AssistanceRequest(position=self.ll(), vehicle_type=rc.TYPE_UAV)))
        self.assertEqual([("UAV1", 5, 11)], self.acks)
        self.assertTrue(self.state.as_dict()["assistance_request"]["ack"]["ok"])
        self.assertEqual([], self.readies)  # never automatic in real mode

        ok, _, report_seq = self.responder.report_ready(self.client, 5)
        self.assertTrue(ok)
        self.assertEqual([("UAV1", 5, 12)], self.readies)

        self.receive(self.command(6, readiness_confirm=c.ReadinessConfirm(report_seq=report_seq, vehicle_id="UAV1")))
        a = self.state.as_dict()["assistance_request"]
        self.assertEqual(6, a["confirmed"]["seq"])
        self.assertEqual(1, len(self.acks))  # ReadinessConfirm is not answered

    def test_keep_out_and_all_clear_are_each_acked(self):
        self.receive(self.command(7, keep_out_zone=c.KeepOutZone(center=self.ll(), radius_m=10, vehicle_type=rc.TYPE_USV)))
        self.receive(self.command(8, all_clear=c.AllClear(vehicle_type=rc.TYPE_USV)))
        self.assertEqual([("USV1", 7, 11), ("USV1", 8, 12)], self.acks)
        self.assertTrue(self.state.as_dict()["last_all_clear"]["ack"]["ok"])

    def test_moving_object_alert_is_not_acked(self):
        self.receive(self.command(9, moving_object_alert=c.MovingObjectAlert(
            position=self.ll(), heading_deg=0, speed_mps=1, affected_vehicle_types=[rc.TYPE_USV])))
        self.assertEqual([], self.acks)

    def test_no_vehicle_of_that_domain_means_no_ack(self):
        self.receive(self.command(10, keep_out_zone=c.KeepOutZone(center=self.ll(), radius_m=10, vehicle_type=rc.TYPE_UUV)))
        self.assertEqual([], self.acks)
        self.assertFalse(self.state.as_dict()["keep_out_zones"][0]["ack"]["ok"])

    def test_readiness_needs_an_acked_request_once_and_a_connection(self):
        self.assertFalse(self.responder.report_ready(self.client, 99)[0])  # unknown request
        self.receive(self.command(5, assistance_request=c.AssistanceRequest(position=self.ll(), vehicle_type=rc.TYPE_USV)))
        self.assertFalse(self.responder.report_ready(FakeClient(connected=False), 5)[0])
        self.assertTrue(self.responder.report_ready(self.client, 5)[0])
        ok, detail, _ = self.responder.report_ready(self.client, 5)
        self.assertFalse(ok)
        self.assertIn("already reported", detail)
        self.assertEqual(1, len(self.readies))

    def test_new_run_forgets_the_chain(self):
        self.receive(self.command(5, assistance_request=c.AssistanceRequest(position=self.ll(), vehicle_type=rc.TYPE_USV)))
        self.state.reset()
        self.assertFalse(self.responder.report_ready(self.client, 5)[0])


class LinkCActionTests(unittest.TestCase):

    def setUp(self):
        self.calls = []

        def ready(body):
            self.calls.append(body)
            return 200, {"ok": True, "detail": "queued", "report_seq": 3}

        self.server = link_c.LinkCServer("127.0.0.1", 0, [ORIGIN], lambda: {}, actions={"/task4/ready": ready})
        self.server.start()
        self.port = self.server._server.server_address[1]

    def tearDown(self):
        self.server.stop()

    def post(self, path, body, origin=ORIGIN):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        conn.request("POST", path, body=body if isinstance(body, bytes) else json.dumps(body).encode(), headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r, data

    def test_allowed_page_can_report_ready(self):
        r, data = self.post("/task4/ready", {"command_seq": 5})
        self.assertEqual(200, r.status)
        self.assertEqual(ORIGIN, r.getheader("Access-Control-Allow-Origin"))
        self.assertEqual(3, json.loads(data)["report_seq"])
        self.assertEqual([{"command_seq": 5}], self.calls)

    def test_other_origins_unknown_paths_and_bad_bodies_are_refused(self):
        self.assertEqual(403, self.post("/task4/ready", {"command_seq": 5}, origin="http://evil.example")[0].status)
        self.assertEqual(404, self.post("/anything", {"x": 1})[0].status)
        self.assertEqual(400, self.post("/task4/ready", b"not json")[0].status)
        self.assertEqual(400, self.post("/task4/ready", b"[1]")[0].status)
        self.assertEqual([], self.calls)

    def test_preflight(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("OPTIONS", "/task4/ready", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
        r = conn.getresponse()
        r.read()
        self.assertEqual(204, r.status)
        self.assertEqual("POST", r.getheader("Access-Control-Allow-Methods"))
        self.assertEqual("Content-Type", r.getheader("Access-Control-Allow-Headers"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
