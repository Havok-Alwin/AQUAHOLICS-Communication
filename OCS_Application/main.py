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

def validate_course(course):

    if not course.course_id:

        output(
            "[ERROR] course_id missing",
            "error"
        )

        return False


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

def send_run_declaration(
    client
):

    global run_declaration_sent
    global declaration_seq


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
        in config.LOCAL_UAV_GEOFENCE

    ]


    declaration = (
        rx_requests_pb2.RunDeclaration(

            vehicle_ids=[
                config.USV_ID,
                config.UAV_ID
            ],


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
