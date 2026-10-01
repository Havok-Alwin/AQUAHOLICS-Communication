#!/usr/bin/env python3
"""Single-file RoboCommand simulator for a two-laptop RobotX OCS test.

Run this on the RoboCommand laptop, alongside an MQTT broker that listens on
its Ethernet address (normally Mosquitto on TCP 1883).  The OCS laptop should
receive its Ethernet address and default gateway from DHCP; its gateway must
be this laptop's Ethernet address.  DHCP is an operating-system network
service, so this Python application does not configure or start DHCP itself.

Example, on the RoboCommand laptop (from RobotX_2026):
    python3 -m pip install -r rc_test/requirements.txt
    python3 rc_test/robocommand_simulator.py --interactive

The app publishes a retained test RxCourse on startup.  When it receives a
valid RunDeclaration, it immediately publishes a matching RunStart.  This is
deliberate: the supplied OCS skeleton has no real telemetry source yet, so it
cannot provide the STATE_AUTO heartbeats that the repository's older test
server waits for.

Interactive commands:
    course alpha|bravo|charlie|delta   publish another retained test course
    start TEAM_ID                      send another RunStart for a team
    assistance TEAM_ID usv|uuv|uav     send a Task 4 AssistanceRequest
    malformed-course                   publish invalid bytes (OCS error test)
    status                             show declarations, runs, and reports
    log                                show buffered RxReport heartbeat log (last 500)
    help                               show commands
    quit                               stop

The course coordinates below are deliberately synthetic test boundaries.
They let you verify that your OCS changes its stored course ID, boundary and
pinger frequency. They are not real competition-course geometry.
"""
from __future__ import annotations

import argparse
import collections
import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import paho.mqtt.client as mqtt
from google.protobuf.message import DecodeError
from google.protobuf.timestamp_pb2 import Timestamp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GEN_PYTHON = PROJECT_ROOT / "gen" / "python"
if str(GEN_PYTHON) not in sys.path:
    sys.path.insert(0, str(GEN_PYTHON))

import common_pb2  # noqa: E402
from robotx import rx_commands_pb2, rx_common_pb2, rx_course_pb2, rx_reports_pb2, rx_requests_pb2  # noqa: E402


TOPIC_ROOT = "robocommand/robotx"
COURSE_TOPIC = f"{TOPIC_ROOT}/course"
MQTT_QOS = 1

# Closed, valid polygons with deliberately different IDs/frequencies/shapes.
COURSE_PROFILES = {
    "alpha": ("ALPHA_TEST", 25000, [(1.28000, 103.85500), (1.28000, 103.85600),
                                      (1.28100, 103.85600), (1.28100, 103.85500),
                                      (1.28000, 103.85500)]),
    "bravo": ("BRAVO_TEST", 26000, [(1.28200, 103.85700), (1.28200, 103.85820),
                                      (1.28290, 103.85820), (1.28290, 103.85700),
                                      (1.28200, 103.85700)]),
    "charlie": ("CHARLIE_TEST", 27000, [(1.28400, 103.85400), (1.28400, 103.85520),
                                          (1.28520, 103.85480), (1.28400, 103.85400)]),
    "delta": ("DELTA_TEST", 28000, [(1.28600, 103.85600), (1.28600, 103.85730),
                                      (1.28730, 103.85730), (1.28730, 103.85600),
                                      (1.28600, 103.85600)]),
}
VEHICLE_TYPES = {
    "usv": rx_common_pb2.TYPE_USV,
    "uuv": rx_common_pb2.TYPE_UUV,
    "uav": rx_common_pb2.TYPE_UAV,
}


def now() -> Timestamp:
    value = Timestamp()
    value.GetCurrentTime()
    return value


@dataclass
class TeamRun:
    declaration_seq: int
    vehicles: set[str]
    run_id: int | None = None
    report_sequences: dict[str, int] = field(default_factory=dict)


class RoboCommandSimulator:
    def __init__(self, host: str, port: int, initial_course: str, auto_start: bool) -> None:
        self.host = host
        self.port = port
        self.selected_course = initial_course
        self.auto_start = auto_start
        self.teams: dict[str, TeamRun] = {}
        self.command_seq: dict[str, int] = {}
        self.next_run_id = 1
        self.stop_event = threading.Event()
        self.command_input: queue.Queue[str] = queue.Queue()
        self._report_log: collections.deque[str] = collections.deque(maxlen=500)
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                                  client_id="robocommand_simulator", reconnect_on_failure=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def log(self, level: int, text: str, *args: object) -> None:
        logging.log(level, text, *args)

    def on_connect(self, client: mqtt.Client, userdata: object, flags: object,
                   reason_code: object, properties: object) -> None:
        if getattr(reason_code, "value", reason_code) != 0:
            self.log(logging.ERROR, "MQTT connection refused: %s", reason_code)
            return
        self.log(logging.INFO, "Connected to MQTT broker at %s:%s", self.host, self.port)
        client.subscribe(f"{TOPIC_ROOT}/+/request", qos=MQTT_QOS)
        client.subscribe(f"{TOPIC_ROOT}/+/+/report", qos=MQTT_QOS)
        self.log(logging.INFO, "Subscribed to OCS requests and reports")
        self.publish_course(self.selected_course)

    def on_disconnect(self, client: mqtt.Client, userdata: object, disconnect_flags: object,
                      reason_code: object, properties: object) -> None:
        self.log(logging.WARNING, "MQTT disconnected (%s); Paho will reconnect", reason_code)

    def publish_course(self, profile_name: str) -> None:
        course_id, frequency, points = COURSE_PROFILES[profile_name]
        course = rx_course_pb2.RxCourse(
            course_id=course_id,
            pinger_freq_hz=frequency,
            corners=[common_pb2.LatLng(latitude=latitude, longitude=longitude)
                     for latitude, longitude in points],
            sent_at=now(),
        )
        result = self.client.publish(COURSE_TOPIC, course.SerializeToString(), qos=MQTT_QOS, retain=True)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"RxCourse publish failed: MQTT rc={result.rc}")
        self.selected_course = profile_name
        self.log(logging.INFO, "PUBLISHED retained RxCourse: %s, pinger=%s Hz, corners=%s",
                 course_id, frequency, len(points))

    def next_command_sequence(self, team_id: str) -> int:
        self.command_seq[team_id] = self.command_seq.get(team_id, 0) + 1
        return self.command_seq[team_id]

    def publish_run_start(self, team_id: str) -> None:
        team = self.teams.get(team_id)
        if team is None:
            raise ValueError(f"No RunDeclaration received for {team_id}")
        team.run_id = self.next_run_id
        self.next_run_id += 1
        command = rx_commands_pb2.RxCommand(
            team_id=team_id,
            seq=self.next_command_sequence(team_id),
            sent_at=now(),
            run_start=rx_commands_pb2.RunStart(declaration_seq=team.declaration_seq, run_id=team.run_id),
        )
        self.publish_command(team_id, command, "RunStart")

    def publish_assistance_request(self, team_id: str, vehicle_type: str) -> None:
        if team_id not in self.teams or self.teams[team_id].run_id is None:
            raise ValueError(f"{team_id} has no active run; send/accept RunDeclaration first")
        command = rx_commands_pb2.RxCommand(
            team_id=team_id,
            seq=self.next_command_sequence(team_id),
            sent_at=now(),
            assistance_request=rx_commands_pb2.AssistanceRequest(
                position=common_pb2.LatLng(latitude=1.28100, longitude=103.85600),
                vehicle_type=VEHICLE_TYPES[vehicle_type],
            ),
        )
        self.publish_command(team_id, command, f"AssistanceRequest ({vehicle_type.upper()})")

    def publish_command(self, team_id: str, command: object, description: str) -> None:
        topic = f"{TOPIC_ROOT}/{team_id}/command"
        result = self.client.publish(topic, command.SerializeToString(), qos=MQTT_QOS)
        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"{description} publish failed: MQTT rc={result.rc}")
        self.log(logging.INFO, "PUBLISHED %s to %s, seq=%s", description, topic, command.seq)

    def on_message(self, client: mqtt.Client, userdata: object, message: mqtt.MQTTMessage) -> None:
        try:
            parts = message.topic.split("/")
            if len(parts) == 4 and parts[-1] == "request":
                self.handle_request(parts, message.payload)
            elif len(parts) == 5 and parts[-1] == "report":
                self.handle_report(parts, message.payload)
            else:
                self.log(logging.WARNING, "Ignored unexpected topic: %s", message.topic)
        except DecodeError as error:
            self.log(logging.ERROR, "Malformed protobuf ignored on %s: %s", message.topic, error)
        except Exception:
            logging.exception("Error handling inbound message on %s", message.topic)

    def handle_request(self, topic_parts: list[str], payload: bytes) -> None:
        request = rx_requests_pb2.RxRequest()
        request.ParseFromString(payload)
        topic_team = topic_parts[2]
        if request.team_id != topic_team:
            raise ValueError(f"request topic team {topic_team!r} != message team {request.team_id!r}")
        if request.WhichOneof("body") != "run_declaration":
            raise ValueError("RxRequest must contain RunDeclaration")
        vehicles = set(request.run_declaration.vehicle_ids)
        if not vehicles:
            raise ValueError("RunDeclaration has no vehicle IDs")
        self.teams[request.team_id] = TeamRun(declaration_seq=request.seq, vehicles=vehicles)
        self.log(logging.INFO, "RECEIVED RunDeclaration: team=%s seq=%s vehicles=%s",
                 request.team_id, request.seq, sorted(vehicles))
        if self.auto_start:
            self.publish_run_start(request.team_id)

    def handle_report(self, topic_parts: list[str], payload: bytes) -> None:
        report = rx_reports_pb2.RxReport()
        report.ParseFromString(payload)
        topic_team, topic_vehicle = topic_parts[2], topic_parts[3]
        if report.team_id != topic_team or report.vehicle_id != topic_vehicle:
            raise ValueError("report topic team/vehicle does not match RxReport envelope")
        team = self.teams.get(report.team_id)
        if team is None:
            raise ValueError(f"report from {report.team_id} before RunDeclaration")
        if report.vehicle_id not in team.vehicles:
            raise ValueError(f"report vehicle {report.vehicle_id} absent from RunDeclaration")
        previous = team.report_sequences.get(report.vehicle_id, 0)
        if report.seq <= previous:
            raise ValueError(f"stale report seq={report.seq} for {report.vehicle_id}; last={previous}")
        team.report_sequences[report.vehicle_id] = report.seq
        body = report.WhichOneof("body")
        entry = (f"{time.strftime('%H:%M:%S')} RxReport: team={report.team_id}"
                 f" vehicle={report.vehicle_id} seq={report.seq} body={body}")
        self._report_log.append(entry)

    def print_status(self) -> None:
        if not self.teams:
            print("No RunDeclaration received yet.")
            return
        for team_id, team in sorted(self.teams.items()):
            print(f"{team_id}: declaration_seq={team.declaration_seq}, run_id={team.run_id}, "
                  f"vehicles={sorted(team.vehicles)}, report_seq={team.report_sequences}")

    def handle_console_command(self, line: str) -> None:
        words = line.strip().split()
        if not words:
            return
        action = words[0].lower()
        if action == "help":
            print(__doc__.split("Interactive commands:", 1)[1].split("The course", 1)[0].strip())
        elif action == "course" and len(words) == 2:
            profile = words[1].lower()
            if profile not in COURSE_PROFILES:
                raise ValueError("course must be alpha, bravo, charlie, or delta")
            self.publish_course(profile)
        elif action == "start" and len(words) == 2:
            self.publish_run_start(words[1])
        elif action == "assistance" and len(words) == 3:
            vehicle_type = words[2].lower()
            if vehicle_type not in VEHICLE_TYPES:
                raise ValueError("vehicle type must be usv, uuv, or uav")
            self.publish_assistance_request(words[1], vehicle_type)
        elif action == "malformed-course" and len(words) == 1:
            self.client.publish(COURSE_TOPIC, b"this-is-not-a-protobuf", qos=MQTT_QOS, retain=False)
            self.log(logging.INFO, "PUBLISHED intentionally malformed course payload")
        elif action == "status" and len(words) == 1:
            self.print_status()
        elif action == "log" and len(words) == 1:
            if not self._report_log:
                print("No report log entries yet.")
            else:
                for entry in self._report_log:
                    print(entry)
                print(f"-- {len(self._report_log)} entries (last {self._report_log.maxlen} kept) --")
        elif action in {"quit", "exit"} and len(words) == 1:
            self.stop_event.set()
        else:
            raise ValueError("Unknown command. Type help.")

    def console_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                line = input("RoboCommand> ")
            except EOFError:
                self.stop_event.set()
                return
            self.command_input.put(line)

    def run(self, interactive: bool) -> None:
        while not self.stop_event.is_set():
            try:
                self.log(logging.INFO, "Connecting to MQTT broker at %s:%s", self.host, self.port)
                self.client.connect(self.host, self.port, keepalive=60)
                break
            except OSError as error:
                self.log(logging.WARNING, "MQTT connection failed: %s; retrying in 2 seconds", error)
                self.stop_event.wait(2)
        if self.stop_event.is_set():
            return
        self.client.loop_start()
        if interactive:
            threading.Thread(target=self.console_loop, name="robocommand-console", daemon=True).start()
            print("RoboCommand simulator ready. Type help for commands.")
        try:
            while not self.stop_event.wait(0.2):
                try:
                    self.handle_console_command(self.command_input.get_nowait())
                except queue.Empty:
                    pass
                except Exception as error:
                    self.log(logging.ERROR, "Command rejected: %s", error)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_event.set()
            self.client.disconnect()
            self.client.loop_stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RobotX two-laptop RoboCommand MQTT simulator")
    parser.add_argument("--broker", default="127.0.0.1", help="MQTT broker address on this laptop")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker TCP port")
    parser.add_argument("--course", choices=sorted(COURSE_PROFILES), default="alpha", help="initial retained test course")
    parser.add_argument("--interactive", action="store_true", help="enable the console command prompt")
    parser.add_argument("--manual-start", action="store_true", help="do not automatically send RunStart after RunDeclaration")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    RoboCommandSimulator(args.broker, args.port, args.course, auto_start=not args.manual_start).run(args.interactive)


if __name__ == "__main__":
    main()
