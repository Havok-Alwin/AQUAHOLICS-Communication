# ============================================================
# AQUAHOLICS ROBOTX 2026
# OCS CONFIGURATION
# ============================================================

import os


# ============================================================
# TEAM
# ============================================================

TEAM_ID = os.getenv("ROBOTX_TEAM_ID", "RMKE")

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
# NETWORK CHECK
#
# Expected course network. When unset, those checks are skipped with a
# warning (the 169.254.x.x link-local check always runs).
#   ROBOTX_SUBNET          e.g. 192.168.10.0/24
#   ROBOTX_ROBOCOMMAND_IP  e.g. 192.168.10.1
# In real mode a mismatch blocks the MQTT connection; set
# ROBOTX_NETWORK_STRICT=0 to downgrade it to a warning.
# ============================================================

EXPECTED_SUBNET = os.getenv("ROBOTX_SUBNET") or None

EXPECTED_ROBOCOMMAND_IP = os.getenv("ROBOTX_ROBOCOMMAND_IP") or None

NETWORK_STRICT = (
    os.getenv("ROBOTX_NETWORK_STRICT", "1") == "1"
)


# ============================================================
# EXPECTED COURSE
#
# The received RxCourse.course_id must equal this value, otherwise the
# course is rejected. When unset the comparison is skipped with a warning.
# Override with ROBOTX_COURSE_ID or --course-id.
# ============================================================

EXPECTED_COURSE_ID = os.getenv("ROBOTX_COURSE_ID") or None


# ============================================================
# PROTOBUF SCHEMA
#
# SHA-256 of the approved generated protobuf files. In real mode a
# different schema blocks startup. Print the current value with
#     python3 startup_checks.py
# and update this when a new schema release is approved.
# ============================================================

APPROVED_SCHEMA_HASH = os.getenv("ROBOTX_SCHEMA_HASH") or "d3252516e0e760662807a886fff386a49301ed9ef4f4d3ead2f7ce0cf3584fc9"


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
# Default is OFF (real run). Set ROBOTX_LOCAL_TEST=1 to opt in.
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
        "0"
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
# Override with ROBOTX_TASK1_TIER .. ROBOTX_TASK4_TIER.
# Before competition, set these according to the team's
# actual declared task tiers. They are printed at startup.
# ============================================================

TASK1_TIER = os.getenv("ROBOTX_TASK1_TIER", "core").lower()

TASK2_TIER = os.getenv("ROBOTX_TASK2_TIER", "disruptive").lower()

TASK3_TIER = os.getenv("ROBOTX_TASK3_TIER", "disruptive").lower()

TASK4_TIER = os.getenv("ROBOTX_TASK4_TIER", "disruptive").lower()


# ============================================================
# UAV GEOFENCE
#
# JSON file holding a list of [lat, lon] points, first point repeated as
# the last (closed). Set with ROBOTX_UAV_GEOFENCE or --uav-geofence.
# Required in real mode. In local test mode with no file, a geofence is
# derived from the course (shrunk to 50%) for simulator testing only.
# ============================================================

UAV_GEOFENCE_FILE = os.getenv("ROBOTX_UAV_GEOFENCE") or None


# ============================================================
# OCS LOG DIRECTORY
# ============================================================

OCS_LOG_DIR = os.path.join(
    os.path.dirname(
        os.path.abspath(__file__)
    ),
    "logs"
)
