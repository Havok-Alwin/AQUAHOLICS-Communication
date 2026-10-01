# ============================================================
# AQUAHOLICS ROBOTX 2026
# OCS CONFIGURATION
# ============================================================

import os


# ============================================================
# TEAM
# ============================================================

TEAM_ID = "RMKE"

USV_ID = "USV1"
UAV_ID = "UAV1"

VEHICLE_IDS = [
    USV_ID,
    UAV_ID,
]


# ============================================================
# MQTT
#
# Set ROBOTX_BROKER to override automatic discovery. When unset, the OCS uses
# the DHCP-provided default gateway (RoboCommand laptop in the two-laptop setup).
# ============================================================

MQTT_BROKER = os.getenv("ROBOTX_BROKER")
MQTT_BROKER_EXPLICIT = bool(MQTT_BROKER)

MQTT_PORT = int(
    os.getenv(
        "ROBOTX_PORT",
        "1883"
    )
)

MQTT_KEEPALIVE = 60

MQTT_QOS = 1


# ============================================================
# RECONNECT
# ============================================================

RECONNECT_MIN_DELAY = 1
RECONNECT_MAX_DELAY = 10


# ============================================================
# MQTT TOPICS
# ============================================================

COURSE_TOPIC = (
    "robocommand/robotx/course"
)

COMMAND_TOPIC = (
    f"robocommand/robotx/"
    f"{TEAM_ID}/command"
)

REQUEST_TOPIC = (
    f"robocommand/robotx/"
    f"{TEAM_ID}/request"
)


def report_topic(vehicle_id):

    return (
        f"robocommand/robotx/"
        f"{TEAM_ID}/"
        f"{vehicle_id}/report"
    )


# ============================================================
# HEARTBEAT
# ============================================================

HEARTBEAT_HZ = 2.0

HEARTBEAT_PERIOD = (
    1.0 / HEARTBEAT_HZ
)


# ============================================================
# LOCAL TEST MODE
#
# IMPORTANT:
#
# True:
#     Uses SIMULATED vehicle telemetry.
#     Automatically sends local test task reports.
#
# False:
#     Fake telemetry / automatic fake reports are disabled.
#
# DO NOT leave simulated data enabled when connecting to
# actual RobotX RoboCommand.
# ============================================================

LOCAL_TEST_MODE = (
    os.getenv(
        "ROBOTX_LOCAL_TEST",
        "1"
    )
    == "1"
)


AUTO_LOCAL_TASK_REPORT_TEST = (
    LOCAL_TEST_MODE
    and
    os.getenv(
        "ROBOTX_AUTO_REPORT_TEST",
        "1"
    )
    == "1"
)


AUTO_TASK4_RESPONSES = (
    LOCAL_TEST_MODE
    and
    os.getenv(
        "ROBOTX_AUTO_TASK4_RESPONSES",
        "1"
    )
    == "1"
)


# ============================================================
# LOCAL TEST RUN DECLARATION TIERS
#
# These are LOCAL TEST settings.
#
# Before competition, set these according to the team's
# actual declared task tiers.
# ============================================================

TASK1_TIER = "core"

TASK2_TIER = "disruptive"

TASK3_TIER = "disruptive"

TASK4_TIER = "disruptive"


# ============================================================
# LOCAL UAV GEOFENCE
#
# LOCAL TEST DATA ONLY
# ============================================================

LOCAL_UAV_GEOFENCE = [

    (1.2801, 103.8552),

    (1.2801, 103.85625),

    (1.2811, 103.85625),

    (1.2811, 103.8552),

    (1.2801, 103.8552),
]


# ============================================================
# OCS LOG DIRECTORY
# ============================================================

OCS_LOG_DIR = os.path.join(
    os.path.dirname(
        os.path.abspath(__file__)
    ),
    "logs"
)
