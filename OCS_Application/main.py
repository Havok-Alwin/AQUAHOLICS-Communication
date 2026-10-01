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


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)


LOCAL_PACKAGES_PATH = os.path.join(
    PROJECT_ROOT,
    "rc_test",
    "local_packages"
)


GEN_PYTHON_PATH = os.path.join(
    PROJECT_ROOT,
    "gen",
    "python"
)


sys.path.insert(
    0,
    LOCAL_PACKAGES_PATH
)

sys.path.insert(
    1,
    GEN_PYTHON_PATH
)


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

from task_reports import (
    publish_pipeline_survey,
    publish_resource_delivery,
    publish_docking_report,
    publish_firefighting_report,
    publish_incident_ack,
    publish_readiness_report
)


import common_pb2

from robotx import rx_course_pb2
from robotx import rx_requests_pb2
from robotx import rx_reports_pb2
from robotx import rx_commands_pb2
from robotx import rx_common_pb2


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

local_task_test_started = False


processed_command_sequences = set()


connection_count = 0
connection_state = "Disconnected"
message_queue = queue.Queue(maxsize=256)
message_queue_dropped = 0


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
# LOCAL HEARTBEAT LOOP
#
# SIMULATED TELEMETRY
# ============================================================

def heartbeat_loop(
    client
):

    first_cycle = True


    while not stop_event.is_set():

        if not client.is_connected():

            time.sleep(
                0.25
            )

            continue


        # ====================================================
        # IMPORTANT
        #
        # Real vehicle telemetry is NOT integrated yet.
        #
        # Prevent fake competition telemetry.
        # ====================================================

        if not config.LOCAL_TEST_MODE:

            output(
                "[VEHICLE] "
                "Waiting for real vehicle telemetry integration."
            )

            return


        # ====================================================
        # USV1 - LOCAL SIMULATION
        # ====================================================

        usv_ok, usv_seq = (
            publish_heartbeat(

                client=
                client,


                vehicle_id=
                config.USV_ID,


                vehicle_type=
                rx_common_pb2.TYPE_USV,


                latitude=
                1.28090,


                longitude=
                103.85548,


                speed=
                1.4,


                heading=
                123.0

            )
        )


        # ====================================================
        # UAV1 - LOCAL SIMULATION
        # ====================================================

        uav_ok, uav_seq = (
            publish_heartbeat(

                client=
                client,


                vehicle_id=
                config.UAV_ID,


                vehicle_type=
                rx_common_pb2.TYPE_UAV,


                latitude=
                1.28070,


                longitude=
                103.85531,


                speed=
                6.5,


                heading=
                160.0

            )
        )


        if first_cycle:

            output()


            if usv_ok:

                output(
                    "[09] "
                    "USV1 heartbeat published"
                )

                output(
                    f"     seq = "
                    f"{usv_seq}"
                )


            if uav_ok:

                output(
                    "[10] "
                    "UAV1 heartbeat published"
                )

                output(
                    f"     seq = "
                    f"{uav_seq}"
                )


            if usv_ok:

                output(
                    "[11] "
                    "USV1 = STATE_AUTO"
                )


            if uav_ok:

                output(
                    "[12] "
                    "UAV1 = STATE_AUTO"
                )


            first_cycle = False


        stop_event.wait(
            config.HEARTBEAT_PERIOD
        )


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
# LOCAL AUTOMATIC TASK REPORT TEST
#
# LOCAL TEST ONLY.
#
# These values are intentionally simulated.
# ============================================================

def local_task_report_test(
    client
):

    time.sleep(
        1
    )


    output()

    output(
        "===================================================="
    )

    output(
        "LOCAL TASK REPORT TEST"
    )

    output(
        "===================================================="
    )


    # ========================================================
    # [16] PIPELINE SURVEY
    # ========================================================

    success, sequence = (
        publish_pipeline_survey(

            client=
            client,


            vehicle_id=
            config.USV_ID,


            latitude=
            1.28075,


            longitude=
            103.85560

        )
    )


    if success:

        output(
            "[16] "
            "PipelineSurveyReport publish queued"
        )

        output(
            f"     vehicle_id = "
            f"{config.USV_ID}"
        )

        output(
            f"     report_seq = "
            f"{sequence}"
        )


    time.sleep(
        0.75
    )


    # ========================================================
    # [17] RESOURCE DELIVERY
    # ========================================================

    success, sequence = (
        publish_resource_delivery(

            client=
            client,


            vehicle_id=
            config.USV_ID,


            task=
            rx_common_pb2
            .TASK_INFRA_SURVEY_REPAIR,


            resource_color=
            rx_common_pb2
            .COLOR_RED,


            delivery_circle_color=
            rx_common_pb2
            .COLOR_GREEN

        )
    )


    if success:

        output()

        output(
            "[17] "
            "ResourceDeliveryRequest publish queued"
        )

        output(
            f"     vehicle_id = "
            f"{config.USV_ID}"
        )

        output(
            f"     report_seq = "
            f"{sequence}"
        )


    time.sleep(
        0.75
    )


    # ========================================================
    # [18] DOCKING
    # ========================================================

    success, sequence = (
        publish_docking_report(

            client=
            client,


            vehicle_id=
            config.USV_ID,


            bay_id=
            1

        )
    )


    if success:

        output()

        output(
            "[18] "
            "DockingReport publish queued"
        )

        output(
            f"     vehicle_id = "
            f"{config.USV_ID}"
        )

        output(
            "     bay_id = 1"
        )

        output(
            f"     report_seq = "
            f"{sequence}"
        )


    time.sleep(
        0.75
    )


    # ========================================================
    # [19] FIREFIGHTING
    # ========================================================

    success, sequence = (
        publish_firefighting_report(

            client=
            client,


            vehicle_id=
            config.UAV_ID,


            window_id=
            1

        )
    )


    if success:

        output()

        output(
            "[19] "
            "FirefightingReport publish queued"
        )

        output(
            f"     vehicle_id = "
            f"{config.UAV_ID}"
        )

        output(
            "     window_id = 1"
        )

        output(
            f"     report_seq = "
            f"{sequence}"
        )


    output()

    output(
        "DATA = LOCAL SIMULATION"
    )

    output(
        "===================================================="
    )

    output(
        "LOCAL TASK REPORT TEST COMPLETE"
    )

    output(
        "===================================================="
    )


# ============================================================
# START LOCAL TASK TEST
# ============================================================

def start_local_task_test(
    client
):

    global local_task_test_started


    if not (
        config.AUTO_LOCAL_TASK_REPORT_TEST
    ):

        return


    with state_lock:

        if local_task_test_started:

            return


        local_task_test_started = True


    thread = threading.Thread(

        target=
        local_task_report_test,


        args=(
            client,
        ),


        daemon=True,


        name=
        "robotx-local-task-test"

    )


    thread.start()


# ============================================================
# DETERMINE COMMAND TARGET VEHICLE
#
# Used only for local Task 4 response testing.
# ============================================================

def command_target_vehicle(
    command,
    command_type
):

    try:

        body = getattr(
            command,
            command_type
        )


        if hasattr(
            body,
            "vehicle_type"
        ):

            vehicle_type = (
                body.vehicle_type
            )


            if (
                vehicle_type
                ==
                rx_common_pb2.TYPE_UAV
            ):

                return config.UAV_ID


    except Exception:

        pass


    return config.USV_ID


# ============================================================
# LOCAL TASK 4 RESPONSE
#
# This proves the protobuf response path locally.
#
# Real mission logic / vehicle routing is still pending.
# ============================================================

def local_task4_response(
    client,
    command,
    command_type
):

    if not config.AUTO_TASK4_RESPONSES:
        output(f"[COMMAND] response not sent; simulated auto-response is disabled | command_seq={command.seq} run={run_id}", "warning")
        return


    vehicle_id = (
        command_target_vehicle(
            command,
            command_type
        )
    )


    # ReadinessConfirm has its matching readiness report.

    if (
        command_type
        == "readiness_confirm"
    ):

        success, sequence = (
            publish_readiness_report(

                client=
                client,


                vehicle_id=
                vehicle_id,


                command_seq=
                command.seq

            )
        )


        if success:

            output(
                f"[COMMAND] response publish queued | type=ReadinessReport | command_seq={command.seq} | report_seq={sequence} | vehicle={vehicle_id} | run={run_id}"
            )

            output(
                f"        command_seq = "
                f"{command.seq}"
            )

            output(
                f"        report_seq = "
                f"{sequence}"
            )


        else:
            output(f"[COMMAND] response failed | type=ReadinessReport | command_seq={command.seq} | vehicle={vehicle_id} | run={run_id}", "error")
        return


    # Other local Task 4 command tests use IncidentAck.

    success, sequence = (
        publish_incident_ack(

            client=
            client,


            vehicle_id=
            vehicle_id,


            command_seq=
            command.seq

        )
    )


    if success:

        output(
            f"[COMMAND] response publish queued | type=IncidentAck | command_seq={command.seq} | report_seq={sequence} | vehicle={vehicle_id} | run={run_id}"
        )

        output(
            f"        vehicle_id = "
            f"{vehicle_id}"
        )

        output(
            f"        command_seq = "
            f"{command.seq}"
        )

        output(
            f"        report_seq = "
            f"{sequence}"
        )
    else:
        output(f"[COMMAND] response failed | type=IncidentAck | command_seq={command.seq} | vehicle={vehicle_id} | run={run_id}", "error")


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


        start_local_task_test(
            client
        )


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


        local_task4_response(

            client,
            command,
            command_type

        )


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
        message_queue.put_nowait((client, message))
    except Exception:
        # Avoid disk/console I/O in Paho's callback if the bounded queue is overloaded.
        message_queue_dropped += 1


def message_worker():
    while not stop_event.is_set():
        try:
            item = message_queue.get(timeout=0.25)
        except queue.Empty:
            continue
        if item is None:
            message_queue.task_done()
            break
        client, message = item
        try:
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
            "[01] Network ready "
            "(LOCAL TEST - DHCP not verified)"
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
                output(f"[MQTT CURRENT STATE] {current_state} | run={run_id or 'not-started'} | dropped_messages={message_queue_dropped}")
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