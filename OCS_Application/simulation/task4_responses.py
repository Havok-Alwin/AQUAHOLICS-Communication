# ============================================================
# AQUAHOLICS ROBOTX 2026
# SIMULATED TASK 4 AUTO-RESPONSES
#
# Automatically responds to Task 4 commands (AssistanceRequest,
# KeepOutZone, etc.) with IncidentAck or ReadinessReport.
#
# Real mission logic / vehicle routing replaces this.
# ============================================================

import config
from robotx import rx_common_pb2

from task_reports import (
    publish_incident_ack,
    publish_readiness_report,
)


def _target_vehicle(command, command_type):
    """Determine which vehicle should respond to a Task 4 command."""
    try:
        body = getattr(command, command_type)
        explicit_vehicle_id = getattr(body, "vehicle_id", "")
        if explicit_vehicle_id in config.VEHICLE_IDS:
            return explicit_vehicle_id
        if command.vehicle_id in config.VEHICLE_IDS:
            return command.vehicle_id
        if hasattr(body, "vehicle_type"):
            if body.vehicle_type == rx_common_pb2.TYPE_UAV:
                return config.UAV_ID
    except Exception:
        pass
    return config.USV_ID


def respond(client, command, command_type, run_id, output_fn):
    """Send an automatic simulated response to a Task 4 command."""
    if not config.AUTO_TASK4_RESPONSES:
        output_fn(
            f"[COMMAND] response not sent; simulated auto-response is disabled | "
            f"command_seq={command.seq} run={run_id}",
            "warning",
        )
        return

    vehicle_id = _target_vehicle(command, command_type)

    if command_type == "readiness_confirm":
        success, sequence = publish_readiness_report(
            client=client,
            vehicle_id=vehicle_id,
            command_seq=command.seq,
        )
        label = "ReadinessReport"
    else:
        success, sequence = publish_incident_ack(
            client=client,
            vehicle_id=vehicle_id,
            command_seq=command.seq,
        )
        label = "IncidentAck"

    if success:
        output_fn(
            f"[COMMAND] response publish queued | type={label} | "
            f"command_seq={command.seq} | report_seq={sequence} | "
            f"vehicle={vehicle_id} | run={run_id}"
        )
    else:
        output_fn(
            f"[COMMAND] response failed | type={label} | "
            f"command_seq={command.seq} | vehicle={vehicle_id} | run={run_id}",
            "error",
        )
