# ============================================================
# AQUAHOLICS ROBOTX 2026
# OPERATOR CONTROL STATION
#
# LOCAL DEVELOPMENT / PoR IMPLEMENTATION
#
# RoboCommand <-> OCS
# MQTT TCP 1883
# Protocol Buffers
#
# ============================================================

import os
import sys
import argparse
import ipaddress
import json


# ============================================================
# COMMAND-LINE FLAGS
#
# Flags are translated to the ROBOTX_* environment variables that
# config.py reads, so this must run before `import config`.
# ============================================================

def apply_cli_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="AQUAHOLICS RobotX 2026 OCS. Default is REAL mode: no simulated "
                    "telemetry/reports/Task 4 responses and no localhost fallback; "
                    "the OCS waits for the DHCP default gateway (RoboCommand).",
        epilog="""examples:
  python3 main.py --team-id RMKE --subnet 192.168.10.0/24 --robocommand-ip 192.168.10.1
  python3 main.py --task1-tier core --task2-tier disruptive --task3-tier disruptive --task4-tier disruptive

Flags override ROBOTX_* environment variables. Other options (ROBOTX_LOCAL_TEST,
ROBOTX_BROKER, ROBOTX_PORT, ROBOTX_NETWORK_STRICT, ...) are environment-only.""",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subnet", metavar="CIDR",
                        help="expected course subnet, e.g. 192.168.10.0/24 (ROBOTX_SUBNET)")
    parser.add_argument("--robocommand-ip", metavar="IP",
                        help="expected RoboCommand IP = default gateway (ROBOTX_ROBOCOMMAND_IP)")
    parser.add_argument("--course-id", metavar="ID",
                        help="expected course ID; a different received course_id is rejected (ROBOTX_COURSE_ID)")
    parser.add_argument("--uav-geofence", metavar="FILE",
                        help="JSON file with the closed UAV geofence as [[lat, lon], ...] (ROBOTX_UAV_GEOFENCE)")
    parser.add_argument("--team-id", metavar="ID",
                        help="assigned team ID (ROBOTX_TEAM_ID, default from config.py)")
    tiers = ["none", "core", "advanced", "disruptive"]
    for n in range(1, 5):
        parser.add_argument(f"--task{n}-tier", choices=tiers, metavar="TIER",
                            help=f"Task {n} tier: {'|'.join(tiers)} (ROBOTX_TASK{n}_TIER)")
    args = parser.parse_args(argv)

    env = os.environ
    if args.subnet:
        env["ROBOTX_SUBNET"] = args.subnet
    if args.robocommand_ip:
        env["ROBOTX_ROBOCOMMAND_IP"] = args.robocommand_ip
    if args.course_id:
        env["ROBOTX_COURSE_ID"] = args.course_id
    if args.uav_geofence:
        env["ROBOTX_UAV_GEOFENCE"] = args.uav_geofence
    if args.team_id:
        env["ROBOTX_TEAM_ID"] = args.team_id
    for n in range(1, 5):
        value = getattr(args, f"task{n}_tier")
        if value:
            env[f"ROBOTX_TASK{n}_TIER"] = value


if __name__ == "__main__":
    apply_cli_args()

import time
import threading
import queue
import logging
import socket
import platform
import subprocess


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

ROBOCMD_ROOT = os.path.join(
    PROJECT_ROOT,
    "Robocmd_Application"
)


GEN_PYTHON_PATH = os.path.join(
    ROBOCMD_ROOT,
    "gen",
    "python"
)


if os.path.isdir(GEN_PYTHON_PATH) and GEN_PYTHON_PATH not in sys.path:
    sys.path.insert(0, GEN_PYTHON_PATH)


# ============================================================
# IMPORTS
# ============================================================

import paho.mqtt.client as mqtt

from google.protobuf.message import (
    DecodeError
)

from google.protobuf.timestamp_pb2 import (
    Timestamp
)


import config
import startup_checks

from logger import get_logger, safe_log

from sequence_manager import (
    next_request_sequence,
    next_report_sequence
)



import common_pb2

from robotx import rx_course_pb2
from robotx import rx_requests_pb2
from robotx import rx_reports_pb2
from robotx import rx_commands_pb2


# ============================================================
# LOGGER
# ============================================================

logger = get_logger()


def output(message="", level="info"):
    """Best-effort operator output; display/log errors never escape into MQTT logic."""
    try:
        print(message, flush=True)
    except Exception:
        pass
    log_level = getattr(logging, str(level).upper(), logging.INFO)
    safe_log(logger, log_level, "%s", message)


def set_connection_state(value, detail=""):
    global connection_state
    with state_lock:
        connection_state = value
    suffix = f" | {detail}" if detail else ""
    output(f"[MQTT STATUS] {value}{suffix}", "warning" if value in {"Reconnecting", "Disconnected"} else "info")


# ============================================================
# GLOBAL STATE
# ============================================================

state_lock = threading.Lock()

stop_event = threading.Event()


run_declaration_sent = False

declaration_seq = None

validated_course = None

run_started = False

run_id = None


heartbeat_thread_started = False


processed_command_sequences = set()


connection_count = 0
connection_state = "Disconnected"
message_queue = queue.Queue(maxsize=256)
message_queue_dropped = 0
message_queue_stale = 0


# ============================================================
# ENUM MAPS
# ============================================================

TIER_MAP = {

    "unknown":
    common_pb2.TIER_UNKNOWN,

    "none":
    common_pb2.TIER_NONE,

    "core":
    common_pb2.TIER_CORE,

    "advanced":
    common_pb2.TIER_ADVANCED,

    "disruptive":
    common_pb2.TIER_DISRUPTIVE,

}


# ============================================================
# TIMESTAMP
# ============================================================

def current_timestamp():

    timestamp = Timestamp()

    timestamp.GetCurrentTime()

    return timestamp


# ============================================================
# TIER CONVERSION
# ============================================================

def tier_value(name):

    value = TIER_MAP.get(
        name.lower()
    )


    if value is None:

        raise ValueError(
            f"Invalid task tier: "
            f"{name}"
        )


    return value


# ============================================================
# COURSE VALIDATION
# ============================================================

def _segments_cross(p1, p2, p3, p4):
    """True if open segments p1-p2 and p3-p4 properly intersect."""
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def boundary_problem(corners):
    """Return a description of why the boundary is unusable, or None if it is fine."""
    points = [(c.latitude, c.longitude) for c in corners]
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]      # an explicitly closed ring repeats its first point
    if len(points) < 3:
        return f"boundary needs at least 3 corners (got {len(points)})"
    if len(set(points)) != len(points):
        return "boundary has duplicate corners"
    count = len(points)
    for i in range(count):
        for j in range(i + 1, count):
            if j == i + 1 or (i == 0 and j == count - 1):
                continue          # adjacent edges share a corner
            if _segments_cross(points[i], points[(i + 1) % count],
                               points[j], points[(j + 1) % count]):
                return "boundary is self-intersecting"
    area2 = sum(points[i][0] * points[(i + 1) % len(points)][1]
                - points[(i + 1) % len(points)][0] * points[i][1]
                for i in range(len(points)))
    if abs(area2) < 1e-12:
        return "boundary is degenerate (zero area / collinear corners)"
    return None


def validate_course(course):

    if not course.course_id:

        output(
            "[ERROR] course_id missing",
            "error"
        )

        return False


    if config.EXPECTED_COURSE_ID:

        if course.course_id != config.EXPECTED_COURSE_ID:

            output(
                f"[ERROR] course_id {course.course_id!r} != "
                f"expected {config.EXPECTED_COURSE_ID!r}",
                "error"
            )

            return False

    else:

        output(
            "[COURSE] WARNING: expected course ID not configured "
            "(--course-id); comparison skipped",
            "warning"
        )


    if len(course.corners) == 0:

        output(
            "[ERROR] "
            "Course boundary missing",
            "error"
        )

        return False


    for index, corner in enumerate(
        course.corners,
        start=1
    ):

        if not (
            -90.0
            <= corner.latitude
            <= 90.0
        ):

            output(

                f"[ERROR] Invalid latitude "
                f"at corner {index}",

                "error"

            )

            return False


        if not (
            -180.0
            <= corner.longitude
            <= 180.0
        ):

            output(

                f"[ERROR] Invalid longitude "
                f"at corner {index}",

                "error"

            )

            return False


    problem = boundary_problem(course.corners)

    if problem:

        output(
            f"[ERROR] {problem}",
            "error"
        )

        return False


    if not course.HasField(
        "sent_at"
    ):

        output(
            "[ERROR] "
            "Course timestamp missing",
            "error"
        )

        return False


    return True


# ============================================================
# HEARTBEAT
# ============================================================

def publish_heartbeat(
    client,
    vehicle_id,
    vehicle_type,
    latitude,
    longitude,
    speed,
    heading
):

    sequence = (
        next_report_sequence(
            vehicle_id
        )
    )


    heartbeat = rx_reports_pb2.Heartbeat(

        state=
        common_pb2.STATE_AUTO,


        position=
        common_pb2.LatLng(

            latitude=
            latitude,

            longitude=
            longitude

        ),


        spd_mps=
        speed,


        heading_deg=
        heading,


        vehicle_type=
        vehicle_type

    )


    report = rx_reports_pb2.RxReport(

        team_id=
        config.TEAM_ID,


        vehicle_id=
        vehicle_id,


        seq=
        sequence,


        sent_at=
        current_timestamp(),


        heartbeat=
        heartbeat

    )


    try:
        result = client.publish(
            topic=config.report_topic(vehicle_id),
            payload=report.SerializeToString(),
            qos=config.MQTT_QOS
        )
        success = result.rc == mqtt.MQTT_ERR_SUCCESS
        result_code = result.rc
    except Exception as error:
        success = False
        result_code = f"exception:{error}"
    safe_log(logger, logging.INFO if success else logging.ERROR,
             "MQTT TX %s | topic=%s | team=%s | vehicle=%s | run=%s | seq=%s | rc=%s",
             "queued" if success else "failed", config.report_topic(vehicle_id),
             config.TEAM_ID, vehicle_id, run_id, sequence, result_code)
    output(f"[PUBLISH] {'queued' if success else 'failed'} topic={config.report_topic(vehicle_id)} vehicle={vehicle_id} seq={sequence} rc={result_code}", "info" if success else "error")
    return success, sequence


# ============================================================
# HEARTBEAT LOOP
# ============================================================

def heartbeat_loop(client):

    if not config.LOCAL_TEST_MODE:
        output("[VEHICLE] Waiting for real vehicle telemetry integration.")
        return

    from simulation.telemetry import get_telemetry

    first_cycle = True

    while not stop_event.is_set():

        if not client.is_connected():
            time.sleep(0.25)
            continue

        results = []
        for vid in config.VEHICLE_IDS:
            telem = get_telemetry(vid)
            if telem is None:
                continue
            ok, seq = publish_heartbeat(
                client=client,
                vehicle_id=vid,
                vehicle_type=telem["vehicle_type"],
                latitude=telem["latitude"],
                longitude=telem["longitude"],
                speed=telem["speed"],
                heading=telem["heading"],
            )
            results.append((vid, ok, seq))

        if first_cycle:
            output()
            for vid, ok, seq in results:
                if ok:
                    output(f"[HEARTBEAT] {vid} heartbeat published  seq={seq}  STATE_AUTO")
            first_cycle = False

        stop_event.wait(config.HEARTBEAT_PERIOD)


# ============================================================
# START HEARTBEAT THREAD
# ============================================================

def start_heartbeats(
    client
):

    global heartbeat_thread_started


    with state_lock:

        if heartbeat_thread_started:

            return


        heartbeat_thread_started = True


    thread = threading.Thread(

        target=
        heartbeat_loop,


        args=(
            client,
        ),


        daemon=True,


        name=
        "robotx-heartbeat"

    )


    thread.start()


# ============================================================
# RUN DECLARATION
# ============================================================

def _on_segment(p, a, b, eps=1e-12):
    cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
    if abs(cross) > eps:
        return False
    return (min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
            and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps)


def point_in_polygon(point, polygon):
    """Ray-casting test; a point on the polygon's edge counts as inside."""
    count = len(polygon)
    inside = False
    for i in range(count):
        a, b = polygon[i], polygon[(i + 1) % count]
        if _on_segment(point, a, b):
            return True
        if (a[1] > point[1]) != (b[1] > point[1]):
            x = a[0] + (point[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if point[0] < x:
                inside = not inside
    return inside


def load_uav_geofence(course):
    """Return (points, error). points is a list of (lat, lon) tuples."""
    if config.UAV_GEOFENCE_FILE:
        try:
            with open(config.UAV_GEOFENCE_FILE, encoding="utf-8") as handle:
                data = json.load(handle)
            return [(float(lat), float(lon)) for lat, lon in data], None
        except (OSError, ValueError, TypeError) as error:
            return None, f"cannot read UAV geofence file {config.UAV_GEOFENCE_FILE!r}: {error}"
    if config.LOCAL_TEST_MODE and course is not None:
        # Local test only: the course shrunk to 50% about its centre.
        ring = [(c.latitude, c.longitude) for c in course.corners]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring = ring[:-1]
        lat0 = sum(p[0] for p in ring) / len(ring)
        lon0 = sum(p[1] for p in ring) / len(ring)
        shrunk = [(lat0 + 0.5 * (a - lat0), lon0 + 0.5 * (b - lon0)) for a, b in ring]
        return shrunk + [shrunk[0]], None
    return None, "no UAV geofence configured (--uav-geofence FILE)"


def geofence_problem(geofence, course_corners):
    """Return why the geofence is unusable for this course, or None if it is fine."""
    if len(geofence) < 4:
        return f"geofence needs at least 3 distinct points plus the closing point (got {len(geofence)} points)"
    if geofence[0] != geofence[-1]:
        return "geofence is not closed (first point must equal last point)"
    ring = geofence[:-1]
    problem = boundary_problem([type("P", (), {"latitude": a, "longitude": b}) for a, b in ring])
    if problem:
        return f"geofence invalid: {problem}"
    course = [(c.latitude, c.longitude) for c in course_corners]
    if len(course) > 1 and course[0] == course[-1]:
        course = course[:-1]
    for index, point in enumerate(ring, start=1):
        if not point_in_polygon(point, course):
            return f"geofence point {index} {point} is outside the course boundary"
    for i in range(len(ring)):
        for j in range(len(course)):
            if _segments_cross(ring[i], ring[(i + 1) % len(ring)],
                               course[j], course[(j + 1) % len(course)]):
                return f"geofence edge {i + 1} crosses the course boundary"
    return None


def send_run_declaration(
    client
):

    global run_declaration_sent
    global declaration_seq


    with state_lock:

        if run_declaration_sent:

            return

        course = validated_course


    geofence_points, error = load_uav_geofence(course)

    if not error and course is None:
        error = "no validated course to check the UAV geofence against"

    if not error:
        error = geofence_problem(geofence_points, course.corners)

    if error:
        output(f"[ERROR] RunDeclaration BLOCKED: {error}", "error")
        safe_log(logger, logging.ERROR, "RunDeclaration blocked | %s", error)
        return

    output(f"[GEOFENCE] UAV geofence OK: closed, {len(geofence_points) - 1} points, inside course {course.course_id}")


    with state_lock:

        if run_declaration_sent:

            return

        declaration_seq = (
            next_request_sequence()
        )


    geofence = [

        common_pb2.LatLng(

            latitude=latitude,

            longitude=longitude

        )

        for latitude, longitude
        in geofence_points

    ]


    declaration = (
        rx_requests_pb2.RunDeclaration(

            vehicle_ids=list(config.VEHICLE_IDS),


            task1_tier=
            tier_value(
                config.TASK1_TIER
            ),


            task2_tier=
            tier_value(
                config.TASK2_TIER
            ),


            task3_tier=
            tier_value(
                config.TASK3_TIER
            ),


            task4_tier=
            tier_value(
                config.TASK4_TIER
            ),


            uav_geofence=
            geofence

        )
    )


    request = (
        rx_requests_pb2.RxRequest(

            team_id=
            config.TEAM_ID,


            seq=
            declaration_seq,


            sent_at=
            current_timestamp(),


            run_declaration=
            declaration

        )
    )


    try:
        result = client.publish(
            topic=config.REQUEST_TOPIC,
            payload=request.SerializeToString(),
            qos=config.MQTT_QOS
        )
        publish_ok = result.rc == mqtt.MQTT_ERR_SUCCESS
        result_code = result.rc
    except Exception as error:
        publish_ok = False
        result_code = f"exception:{error}"
    safe_log(logger, logging.INFO if publish_ok else logging.ERROR,
             "MQTT TX %s | topic=%s | team=%s | request_seq=%s | run=%s | rc=%s",
             "queued" if publish_ok else "failed",
             config.REQUEST_TOPIC, config.TEAM_ID, declaration_seq, run_id, result_code)
    if not publish_ok:
        output(f"[PUBLISH] RunDeclaration failed topic={config.REQUEST_TOPIC} team={config.TEAM_ID} seq={declaration_seq} rc={result_code}", "error")
        return


    with state_lock:

        run_declaration_sent = True


    output()

    output(
        "[08] RunDeclaration publish queued"
    )

    output(
        f"     team_id = "
        f"{config.TEAM_ID}"
    )

    output(
        f"     {config.USV_ID}"
    )

    output(
        f"     {config.UAV_ID}"
    )

    output(
        f"     declaration_seq = "
        f"{declaration_seq}"
    )

    output(
        f"     task1 = "
        f"{config.TASK1_TIER.upper()}"
    )

    output(
        f"     task2 = "
        f"{config.TASK2_TIER.upper()}"
    )

    output(
        f"     task3 = "
        f"{config.TASK3_TIER.upper()}"
    )

    output(
        f"     task4 = "
        f"{config.TASK4_TIER.upper()}"
    )

    output(
        f"     Topic: "
        f"{config.REQUEST_TOPIC}"
    )


    start_heartbeats(
        client
    )




# ============================================================
# PROCESS RxCommand
# ============================================================

def process_command(
    client,
    message
):

    global run_started
    global run_id


    try:

        command = (
            rx_commands_pb2
            .RxCommand()
        )


        command.ParseFromString(
            message.payload
        )


    except (DecodeError, ValueError, TypeError) as error:
        output(f"[COMMAND] rejected malformed protobuf topic={message.topic}: {error}", "error")
        return
    except Exception as error:
        output(f"[COMMAND] rejected due to decode error topic={message.topic}: {error}", "error")
        return


    # ========================================================
    # TEAM VALIDATION
    # ========================================================

    if (
        command.team_id
        != config.TEAM_ID
    ):

        output(
            f"[COMMAND] rejected team identity mismatch topic={message.topic} "
            f"decoded_team={command.team_id!r} expected_team={config.TEAM_ID!r}",
            "error"
        )

        output(
            f"        expected = "
            f"{config.TEAM_ID}"
        )

        output(
            f"        received = "
            f"{command.team_id}"
        )

        return

    # The MQTT command topic is team scoped. When RoboCommand supplies a
    # vehicle_id in the protobuf envelope (or command body), verify it before
    # dispatch so a misrouted command cannot reach the wrong vehicle path.
    if command.vehicle_id and command.vehicle_id not in config.VEHICLE_IDS:
        output(f"[COMMAND] rejected unknown vehicle_id={command.vehicle_id!r} topic={message.topic}", "error")
        return


    # ========================================================
    # DUPLICATE COMMAND
    # ========================================================

    with state_lock:

        if (
            command.seq
            in processed_command_sequences
        ):

            output(f"[COMMAND] ignored duplicate seq={command.seq} run={run_id}", "warning")

            return


    command_type = (
        command.WhichOneof(
            "body"
        )
    )


    allowed_commands = {
        "run_start", "assistance_request", "keep_out_zone", "all_clear",
        "moving_object_alert", "readiness_confirm",
    }
    if command_type not in allowed_commands:
        output(f"[COMMAND] rejected unexpected body type={command_type!r} seq={command.seq}", "error")
        return
    command_body = getattr(command, command_type)
    body_vehicle_id = getattr(command_body, "vehicle_id", "")
    if body_vehicle_id:
        if body_vehicle_id not in config.VEHICLE_IDS:
            output(f"[COMMAND] rejected unknown body vehicle_id={body_vehicle_id!r} seq={command.seq}", "error")
            return
        if command.vehicle_id and body_vehicle_id != command.vehicle_id:
            output(f"[COMMAND] rejected envelope/body vehicle mismatch envelope={command.vehicle_id!r} body={body_vehicle_id!r} seq={command.seq}", "error")
            return
    if not command.team_id or command.seq <= 0:
        output(f"[COMMAND] rejected missing team_id or invalid seq={command.seq}", "error")
        return
    if command_type == "run_start":
        start = command.run_start
        if not start.run_id or start.declaration_seq <= 0:
            output(f"[COMMAND] rejected RunStart missing run_id/declaration_seq seq={command.seq}", "error")
            return
    else:
        body = getattr(command, command_type)
        if not body.ListFields():
            output(f"[COMMAND] rejected empty command body type={command_type} seq={command.seq}", "error")
            return
    if command_type != "run_start" and not run_started:
        output(f"[COMMAND] ignored task command before current RunStart seq={command.seq} type={command_type}", "warning")
        return
    elif hasattr(command, "run_id") and command.run_id and command.run_id != run_id:
        output(f"[COMMAND] ignored command for another run seq={command.seq} received_run={command.run_id!r} current_run={run_id!r}", "warning")
        return


    safe_log(logger, logging.INFO,
             "COMMAND received | topic=%s | team=%s | run=%s | seq=%s | type=%s",
             message.topic, command.team_id, run_id, command.seq, command_type)
    output(f"[COMMAND] received seq={command.seq} type={command_type} run={run_id or 'not-started'}")


    # ========================================================
    # RUN START
    # ========================================================

    if (
        command_type
        == "run_start"
    ):

        output()

        output(
            "[13] RunStart received"
        )

        output(
            f"     command_seq = "
            f"{command.seq}"
        )


        received_declaration_seq = (
            command
            .run_start
            .declaration_seq
        )


        if (
            received_declaration_seq
            != declaration_seq
        ):

            output(
                "[ERROR] "
                "declaration_seq mismatch",
                "error"
            )

            output(
                f"        expected = "
                f"{declaration_seq}"
            )

            output(
                f"        received = "
                f"{received_declaration_seq}"
            )

            return


        output(
            "[14] "
            "declaration_seq verified"
        )
        output(f"[COMMAND] accepted RunStart seq={command.seq}")


        new_run_id = command.run_start.run_id
        with state_lock:
            if run_id != new_run_id:
                processed_command_sequences.clear()
            run_id = new_run_id
            run_started = True
            processed_command_sequences.add(command.seq)


        output(
            "[15] run_id recorded"
        )

        output(
            f"     run_id = "
            f"{run_id}"
        )


        output()

        output(
            "===================================================="
        )

        output(
            "RUN START COMPLETE"
        )

        output(
            "===================================================="
        )


        if config.LOCAL_TEST_MODE:
            from simulation.task_reports import start as start_sim_task_test
            start_sim_task_test(client, output)


        return


    # ========================================================
    # TASK 4 COMMANDS
    # ========================================================

    task4_commands = {

        "assistance_request",

        "keep_out_zone",

        "all_clear",

        "moving_object_alert",

        "readiness_confirm",

    }


    if (
        command_type
        in task4_commands
    ):

        output()

        output(
            f"[COMMAND] accepted type={command_type} seq={command.seq} run={run_id}"
        )
        output(
            "[TASK4] "
            f"{command_type} received"
        )

        output(
            f"        command_seq = "
            f"{command.seq}"
        )


        # Print decoded body for operator visibility.

        try:

            body = getattr(
                command,
                command_type
            )


            body_text = str(
                body
            ).strip()


            if body_text:

                output(
                    body_text
                )


        except Exception:

            pass


        with state_lock:

            processed_command_sequences.add(
                command.seq
            )


        if config.LOCAL_TEST_MODE:
            from simulation.task4_responses import respond as sim_task4_respond
            sim_task4_respond(client, command, command_type, run_id, output)


        return


    # ========================================================
    # UNKNOWN / FUTURE COMMAND
    # ========================================================

    output()

    output(
        "[INFO] "
        f"Unhandled RxCommand: "
        f"{command_type}"
    )


    with state_lock:

        processed_command_sequences.add(
            command.seq
        )


# ============================================================
# RxCourse PROCESSING
# ============================================================

def get_validated_course():
    """Latest RxCourse that passed validation, or None."""
    with state_lock:
        return validated_course


def process_course(
    client,
    payload
):

    try:

        course = (
            rx_course_pb2
            .RxCourse()
        )


        course.ParseFromString(
            payload
        )


    except (DecodeError, ValueError, TypeError) as error:
        output(f"[COURSE] rejected malformed protobuf topic={config.COURSE_TOPIC}: {error}", "error")
        return
    except Exception as error:
        output(f"[COURSE] rejected due to decode error topic={config.COURSE_TOPIC}: {error}", "error")
        return


    output(
        "[06] RxCourse decoded"
    )


    if not validate_course(
        course
    ):

        output(
            "[ERROR] "
            "Course configuration rejected",
            "error"
        )

        return


    global validated_course

    with state_lock:

        validated_course = course

    safe_log(logger, logging.INFO, "Course validated | course_id=%s corners=%s pinger_hz=%s",
             course.course_id, len(course.corners), course.pinger_freq_hz)

    output(
        "[07] "
        "Course configuration validated"
    )


    output()

    output(
        "===================================================="
    )

    output(
        "COURSE INFORMATION"
    )

    output(
        "===================================================="
    )

    output(
        f"Course ID       : "
        f"{course.course_id}"
    )

    output(
        f"Pinger Frequency: "
        f"{course.pinger_freq_hz} Hz"
    )

    output(
        f"Course Corners  : "
        f"{len(course.corners)}"
    )

    output(
        "===================================================="
    )


    send_run_declaration(
        client
    )


# ============================================================
# MQTT CONNECT CALLBACK
#
# This runs again after reconnect, therefore subscriptions
# are restored automatically.
# ============================================================

def on_connect(
    client,
    userdata,
    flags,
    reason_code,
    properties
):

    global connection_count


    if reason_code != 0:
        set_connection_state("Reconnecting", f"connect failed reason={reason_code}")
        safe_log(logger, logging.ERROR, "MQTT connect failed | broker=%s:%s reason=%s", config.MQTT_BROKER, config.MQTT_PORT, reason_code)
        return


    with state_lock:

        connection_count += 1

        this_connection = (
            connection_count
        )


    set_connection_state("Connected", f"broker={config.MQTT_BROKER}:{config.MQTT_PORT}")
    safe_log(logger, logging.INFO, "MQTT connected | broker=%s:%s connection=%s", config.MQTT_BROKER, config.MQTT_PORT, this_connection)
    if this_connection == 1:

        output(
            "[02] "
            "MQTT connection established"
        )


    else:

        output()

        output(
            "[MQTT] "
            "Connection restored"
        )


    # ========================================================
    # SUBSCRIBE EVERY TIME WE CONNECT
    #
    # This restores subscriptions after reconnect.
    # ========================================================

    course_result, _ = (
        client.subscribe(

            config.COURSE_TOPIC,

            qos=config.MQTT_QOS

        )
    )


    command_result, _ = (
        client.subscribe(

            config.COMMAND_TOPIC,

            qos=config.MQTT_QOS

        )
    )


    if this_connection == 1:

        output(
            "[03] Subscribed:"
        )

        output(
            f"     "
            f"{config.COURSE_TOPIC}"
        )


        output(
            "[04] Subscribed:"
        )

        output(
            f"     "
            f"{config.COMMAND_TOPIC}"
        )


    else:

        output(
            "[MQTT] "
            "Subscriptions restored"
        )


    safe_log(logger, logging.INFO,
             "MQTT subscriptions | course_topic=%s rc=%s | command_topic=%s rc=%s",
             config.COURSE_TOPIC, course_result, config.COMMAND_TOPIC, command_result)
    if course_result != mqtt.MQTT_ERR_SUCCESS or command_result != mqtt.MQTT_ERR_SUCCESS:
        output("[MQTT STATUS] subscription request failed", "error")


# ============================================================
# MQTT DISCONNECT CALLBACK
# ============================================================

def on_disconnect(
    client,
    userdata,
    disconnect_flags,
    reason_code,
    properties
):

    if stop_event.is_set():
        set_connection_state("Disconnected", "application stopping")
        safe_log(logger, logging.INFO, "MQTT disconnected normally | reason=%s", reason_code)
        output("[MQTT] Normal disconnection")

    else:
        set_connection_state("Reconnecting", f"connection lost reason={reason_code}")
        safe_log(logger, logging.WARNING, "MQTT connection lost | reason=%s", reason_code)

        output()

        output(
            "[MQTT] "
            "Connection lost",
            "warning"
        )

        output(
            f"       reason_code = "
            f"{reason_code}",
            "warning"
        )

        output(
            "[MQTT] "
            "Automatic reconnect enabled",
            "warning"
        )


# ============================================================
# MQTT MESSAGE CALLBACK
# ============================================================

def on_message(client, userdata, message):
    """Only enqueue in Paho's network thread; parsing, logging, and display happen elsewhere."""
    global message_queue_dropped
    try:
        with state_lock:
            epoch = connection_count
        message_queue.put_nowait((epoch, client, message))
    except Exception:
        # Avoid disk/console I/O in Paho's callback if the bounded queue is overloaded.
        message_queue_dropped += 1


def message_worker():
    global message_queue_stale
    while not stop_event.is_set():
        try:
            item = message_queue.get(timeout=0.25)
        except queue.Empty:
            continue
        if item is None:
            message_queue.task_done()
            break
        epoch, client, message = item
        try:
            with state_lock:
                active_epoch = connection_count
                active_state = connection_state
            if epoch != active_epoch or active_state != "Connected":
                message_queue_stale += 1
                safe_log(logger, logging.WARNING,
                         "MQTT RX discarded message outside active connection | queued_connection=%s active_connection=%s state=%s topic=%s",
                         epoch, active_epoch, active_state, message.topic)
                continue
            topic = message.topic
            safe_log(logger, logging.INFO, "MQTT RX | topic=%s | bytes=%s | retained=%s",
                     topic, len(message.payload), bool(getattr(message, "retain", False)))
            if topic not in {config.COURSE_TOPIC, config.COMMAND_TOPIC}:
                output(f"[MQTT RX] rejected unexpected topic={topic!r}", "warning")
            elif topic == config.COMMAND_TOPIC:
                topic_parts = topic.split("/")
                if len(topic_parts) != 4 or topic_parts[2] != config.TEAM_ID:
                    output(f"[MQTT RX] rejected command topic team mismatch topic={topic!r}", "error")
                elif getattr(message, "retain", False):
                    output(f"[COMMAND] ignored retained (stale) command topic={topic}", "warning")
                else:
                    process_command(client, message)
            else:
                output("[05] RxCourse received")
                process_course(client, message.payload)
        except Exception as error:
            output(f"[MQTT RX] message handling error safely contained topic={message.topic}: {error}", "error")
        finally:
            message_queue.task_done()


# ============================================================
# MQTT CLIENT
# ============================================================

def build_mqtt_client():

    client = mqtt.Client(

        callback_api_version=
        mqtt.CallbackAPIVersion.VERSION2,


        client_id=
        f"{config.TEAM_ID}_OCS",


        reconnect_on_failure=True,
        clean_session=True

    )


    client.on_connect = (
        on_connect
    )

    client.on_disconnect = (
        on_disconnect
    )

    client.on_message = (
        on_message
    )


    client.reconnect_delay_set(

        min_delay=
        config.RECONNECT_MIN_DELAY,


        max_delay=
        config.RECONNECT_MAX_DELAY

    )


    return client


# ============================================================
# INITIAL MQTT CONNECTION
# ============================================================

def connect_with_retry(
    client
):

    while not stop_event.is_set():

        try:

            if not config.MQTT_BROKER_EXPLICIT:
                discovered_broker = discover_default_gateway()
                if discovered_broker:
                    config.MQTT_BROKER = discovered_broker
                elif config.LOCAL_TEST_MODE:
                    # Preserve out-of-the-box local broker testing when no
                    # DHCP gateway is present; a later retry can discover one.
                    config.MQTT_BROKER = "localhost"
                else:
                    config.MQTT_BROKER = None

            if not config.MQTT_BROKER:
                set_connection_state("Waiting for network", "waiting for DHCP default gateway")
                output("[NETWORK] No default gateway discovered yet; waiting for DHCP and retrying in 2 seconds.", "warning")
                stop_event.wait(2)
                continue

            set_connection_state("Waiting for network", f"checking route to broker={config.MQTT_BROKER}")
            if not network_ready():
                output(f"[NETWORK] No usable route to broker {config.MQTT_BROKER} yet; retrying in 2 seconds.", "warning")
                stop_event.wait(2)
                continue

            if not check_network(config.MQTT_BROKER):
                set_connection_state("Waiting for network", "network check failed")
                stop_event.wait(2)
                continue

            set_connection_state("Connecting", f"broker={config.MQTT_BROKER}:{config.MQTT_PORT}")
            output(f"Connecting to MQTT broker {config.MQTT_BROKER}:{config.MQTT_PORT}...")


            client.connect(

                host=
                config.MQTT_BROKER,


                port=
                config.MQTT_PORT,


                keepalive=
                config.MQTT_KEEPALIVE

            )


            return True


        except KeyboardInterrupt:

            return False


        except Exception as error:

            output(

                "[MQTT] "
                f"Connection failed: "
                f"{error}",

                "warning"

            )

            output(

                "[MQTT] "
                "Retrying in 2 seconds...",

                "warning"

            )


            stop_event.wait(
                2
            )


    return False


def discover_default_gateway():
    """Return the system's IPv4 default gateway, if one is configured by DHCP."""
    # Linux exposes the IPv4 route table without requiring an external command.
    try:
        with open("/proc/net/route", encoding="ascii") as routes:
            next(routes, None)
            for row in routes:
                fields = row.split()
                if len(fields) < 4 or fields[1] != "00000000":
                    continue
                flags = int(fields[3], 16)
                if flags & 0x1:
                    gateway_bytes = bytes.fromhex(fields[2])
                    gateway = socket.inet_ntoa(gateway_bytes[::-1])
                    if gateway != "0.0.0.0":
                        return gateway
    except (OSError, ValueError):
        pass

    # Support macOS and Windows hosts as well as Linux.
    system = platform.system()
    commands = {
        "Darwin": ["route", "-n", "get", "default"],
        "Windows": ["route", "print", "-4"],
    }
    command = commands.get(system)
    if command:
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=3, check=False)
            for line in result.stdout.splitlines():
                parts = line.split()
                if system == "Darwin" and len(parts) == 2 and parts[0] == "gateway:":
                    return parts[1]
                if system == "Windows" and len(parts) >= 4 and parts[0:2] == ["0.0.0.0", "0.0.0.0"]:
                    return parts[2]
        except (OSError, subprocess.SubprocessError):
            pass

    # The `ip` utility is available on most Linux distributions even when
    # /proc route data is restricted by the runtime environment.
    if system == "Linux":
        try:
            result = subprocess.run(["ip", "-4", "route", "show", "default"],
                                    capture_output=True, text=True,
                                    timeout=3, check=False)
            fields = result.stdout.split()
            if len(fields) >= 3 and fields[0] == "default" and fields[1] == "via":
                return fields[2]
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def local_route_info(broker):
    """Return (interface, local_ip) the OS would use to reach the broker."""
    interface, local_ip = None, None
    try:
        result = subprocess.run(["ip", "-4", "route", "get", broker], capture_output=True,
                                text=True, timeout=3, check=False)
        fields = result.stdout.split()
        if "dev" in fields:
            interface = fields[fields.index("dev") + 1]
        if "src" in fields:
            local_ip = fields[fields.index("src") + 1]
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    if not local_ip:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect((broker, config.MQTT_PORT))
                local_ip = probe.getsockname()[0]
            finally:
                probe.close()
        except OSError:
            pass
    return interface, local_ip


last_network_report = None


def check_network(broker):
    """Verify the course network. Returns True when the connection may proceed.

    DHCP mode, bridging and Internet sharing cannot be verified here and stay
    manual operator checks.
    """
    global last_network_report
    interface, local_ip = local_route_info(broker)
    gateway = discover_default_gateway()
    problems = []

    try:
        loopback = ipaddress.ip_address(broker).is_loopback or broker == "localhost"
    except ValueError:
        loopback = broker == "localhost"

    if loopback:
        report = (interface or "lo", local_ip or "127.0.0.1", gateway, [])
    else:
        if not local_ip:
            problems.append("no local IP address found on the route to the broker")
        else:
            addr = ipaddress.ip_address(local_ip)
            if addr.is_link_local:
                problems.append(f"local IP {local_ip} is link-local (169.254.x.x): DHCP lease not received")
            if config.EXPECTED_SUBNET:
                try:
                    if addr not in ipaddress.ip_network(config.EXPECTED_SUBNET, strict=False):
                        problems.append(f"local IP {local_ip} is not in expected subnet {config.EXPECTED_SUBNET}")
                except ValueError:
                    problems.append(f"invalid expected subnet {config.EXPECTED_SUBNET!r}")
        if config.EXPECTED_ROBOCOMMAND_IP:
            if gateway != config.EXPECTED_ROBOCOMMAND_IP:
                problems.append(f"default gateway {gateway} != expected RoboCommand IP {config.EXPECTED_ROBOCOMMAND_IP}")
            if broker != config.EXPECTED_ROBOCOMMAND_IP:
                problems.append(f"broker {broker} != expected RoboCommand IP {config.EXPECTED_ROBOCOMMAND_IP}")
        report = (interface, local_ip, gateway, problems)

    if report != last_network_report:
        last_network_report = report
        output(f"[NETWORK] interface={interface or 'unknown'} ip={local_ip} gateway={gateway} broker={broker}")
        safe_log(logger, logging.INFO, "Network | interface=%s ip=%s gateway=%s broker=%s subnet=%s robocommand_ip=%s",
                 interface, local_ip, gateway, broker, config.EXPECTED_SUBNET, config.EXPECTED_ROBOCOMMAND_IP)
        if loopback:
            output("[NETWORK] loopback broker: course network checks skipped")
        else:
            if not config.EXPECTED_SUBNET or not config.EXPECTED_ROBOCOMMAND_IP:
                output("[NETWORK] WARNING: expected subnet / RoboCommand IP not configured "
                       "(--subnet, --robocommand-ip); those checks skipped", "warning")
            output("[NETWORK] Manual checks: interface is DHCP, bridging off, Internet sharing off")
            for problem in problems:
                output(f"[NETWORK] CHECK FAILED: {problem}", "error")
                safe_log(logger, logging.ERROR, "Network check failed | %s", problem)
            if not problems:
                output("[NETWORK] network checks passed")

    if problems and config.NETWORK_STRICT and not config.LOCAL_TEST_MODE:
        return False
    return True


def network_ready():
    """Return true once the broker resolves and the OS has a usable route.

    A UDP route probe asks the kernel which local interface it would use; it
    does not send application data. Local development on localhost is allowed.
    """
    try:
        if not config.MQTT_BROKER:
            return False
        addresses = socket.getaddrinfo(config.MQTT_BROKER, config.MQTT_PORT, type=socket.SOCK_DGRAM)
        if config.MQTT_BROKER in {"localhost", "127.0.0.1", "::1"}:
            return True
        for family, socktype, proto, _, address in addresses:
            probe = socket.socket(family, socktype, proto)
            try:
                probe.connect(address)
                local_ip = probe.getsockname()[0]
                if local_ip and not local_ip.startswith("127.") and local_ip != "::1":
                    return True
            finally:
                probe.close()
    except OSError as error:
        safe_log(logger, logging.INFO, "Network readiness probe pending | broker=%s | error=%s", config.MQTT_BROKER, error)
    return False


# ============================================================
# MAIN
# ============================================================

def run_startup_checks():
    """Schema and vehicle ID checks. Returns False when the OCS must not start."""
    schema_info, schema_problems = startup_checks.check_schema()
    for line in schema_info:
        output(f"[SCHEMA] {line}", "warning" if line.startswith("WARNING") else "info")
        safe_log(logger, logging.INFO, "Schema | %s", line)

    id_problems = startup_checks.check_vehicle_ids()
    if not id_problems:
        output(f"[VEHICLES] IDs OK: {', '.join(config.VEHICLE_IDS)} "
               f"(used in RunDeclaration and report topics, e.g. {config.report_topic(config.VEHICLE_IDS[0])})")

    blocking = False
    for problem in id_problems:
        output(f"[VEHICLES] CHECK FAILED: {problem}", "error")
        safe_log(logger, logging.ERROR, "Vehicle ID check failed | %s", problem)
        blocking = True
    for problem in schema_problems:
        if config.LOCAL_TEST_MODE:
            output(f"[SCHEMA] WARNING: {problem}", "warning")
        else:
            output(f"[SCHEMA] CHECK FAILED: {problem}", "error")
            blocking = True
        safe_log(logger, logging.ERROR, "Schema check failed | %s", problem)

    if blocking:
        output("[STARTUP] Startup checks failed; OCS not started.", "error")
    return not blocking


def main():

    output(
        "===================================================="
    )

    output(
        "AQUAHOLICS ROBOTX OCS"
    )

    output(
        "===================================================="
    )

    output(
        "OCS STARTED"
    )


    if config.LOCAL_TEST_MODE:

        output(
            "[01] Local test mode enabled "
            "(broker auto-discovery active unless ROBOTX_BROKER is set)"
        )


    else:

        output(
            "[01] OCS started in REAL NETWORK mode"
        )

        output(
            "     Real vehicle telemetry adapter "
            "still required."
        )


    if not run_startup_checks():
        return


    output(f"     team_id = {config.TEAM_ID}")
    output(f"     vehicles = {', '.join(config.VEHICLE_IDS)}")
    output(f"     task1 = {config.TASK1_TIER.upper()}")
    output(f"     task2 = {config.TASK2_TIER.upper()}")
    output(f"     task3 = {config.TASK3_TIER.upper()}")
    output(f"     task4 = {config.TASK4_TIER.upper()}")
    output("     >>> Operator: confirm team_id and task tiers above before the run <<<")
    safe_log(logger, logging.INFO, "Startup config | team=%s vehicles=%s tiers=%s/%s/%s/%s local_test=%s",
             config.TEAM_ID, config.VEHICLE_IDS, config.TASK1_TIER, config.TASK2_TIER,
             config.TASK3_TIER, config.TASK4_TIER, config.LOCAL_TEST_MODE)
    output()


    client = (
        build_mqtt_client()
    )


    if not connect_with_retry(
        client
    ):

        return


    worker = threading.Thread(target=message_worker, name="robotx-mqtt-message-worker", daemon=True)
    worker.start()
    client.loop_start()


    try:
        last_status_report = 0
        while True:
            time.sleep(1)
            now = time.monotonic()
            if now - last_status_report >= 5:
                with state_lock:
                    current_state = connection_state
                output(f"[MQTT CURRENT STATE] {current_state} | run={run_id or 'not-started'} | dropped_messages={message_queue_dropped} | stale_messages={message_queue_stale}")
                last_status_report = now


    except KeyboardInterrupt:

        output()

        output(
            "Stopping AQUAHOLICS OCS..."
        )


    finally:

        stop_event.set()
        try:
            message_queue.put_nowait(None)
        except queue.Full:
            pass


        try:

            client.disconnect()

        except Exception:

            pass


        client.loop_stop()


        output(
            "MQTT disconnected"
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    main()
