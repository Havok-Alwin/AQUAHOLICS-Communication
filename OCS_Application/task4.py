# ============================================================
# AQUAHOLICS ROBOTX 2026
# TASK 4 RESPONSES (real mode and local test mode)
#
# Response chains (handbook "Heartbeats and task reports" and the Task 4
# description in Mission_details.pdf):
#
#   AssistanceRequest -> IncidentAck -> ReadinessReport -> ReadinessConfirm
#   KeepOutZone       -> IncidentAck -> AllClear        -> IncidentAck
#   MovingObjectAlert    (no acknowledgment)
#
# - IncidentAck: sent automatically the moment the OCS accepts the command
#   ("must immediately acknowledge"), attributed to our vehicle of the
#   command's domain (vehicle_type), command_seq = the command's seq.
# - ReadinessReport: only when the operator reports that the vehicle has
#   reached the assistance point and is loitering (report_ready, called from
#   the operator display through link C). command_seq = the AssistanceRequest.
# - ReadinessConfirm from RoboCommand references our ReadinessReport's
#   RxReport.seq: it is the clearance to resume normal tasking.
#
# Every outcome is written to Task4State (shown on the display) and to the
# OCS console ([COMMAND] lines, also shown on the display).
# ============================================================

import threading


class Task4Responder:
    """publish_ack(client, vehicle_id, command_seq) and publish_ready(...) are
    task_reports.publish_incident_ack / publish_readiness_report: they return
    (success, report_seq)."""

    def __init__(self, state, vehicle_for_type, publish_ack, publish_ready, output):
        self.state = state                      # link_c.Task4State
        self.vehicle_for_type = vehicle_for_type  # 'USV' -> 'USV1', None if we have none
        self.publish_ack = publish_ack
        self.publish_ready = publish_ready
        self.output = output
        self._lock = threading.Lock()

    def target(self, command, command_type):
        """Our vehicle of the command's domain, or None (e.g. a UUV command: we have no UUV)."""
        body = getattr(command, command_type)
        if command.vehicle_id:
            return command.vehicle_id
        return self.vehicle_for_type(self.state.type_name(body.vehicle_type))

    def on_command(self, client, command, command_type):
        """Call after the command was accepted and recorded in Task4State."""
        if command_type not in ("assistance_request", "keep_out_zone", "all_clear"):
            if command_type == "moving_object_alert":
                self.output("[COMMAND] MovingObjectAlert needs no acknowledgment (handbook); avoid the object by 10 m")
            elif command_type == "readiness_confirm":
                self.output(f"[COMMAND] ReadinessConfirm for report_seq={command.readiness_confirm.report_seq}: "
                            "cleared to resume normal tasking")
            return
        vehicle = self.target(command, command_type)
        if vehicle is None:
            body = getattr(command, command_type)
            self.output(f"[COMMAND] no IncidentAck for {command_type} seq={command.seq}: no vehicle of type "
                        f"{self.state.type_name(body.vehicle_type)} in this run", "warning")
            self.state.set_ack(command.seq, None, None, False, "no vehicle of that type")
            return
        try:
            ok, report_seq = self.publish_ack(client=client, vehicle_id=vehicle, command_seq=command.seq)
        except Exception as error:  # never let a publish problem stop command processing
            ok, report_seq = False, None
            self.output(f"[ERROR] IncidentAck for seq={command.seq} failed: {error}", "error")
        self.state.set_ack(command.seq, vehicle, report_seq, ok, "" if ok else "publish failed")
        self.output(f"[COMMAND] IncidentAck {'queued' if ok else 'FAILED'} | {command_type} command_seq={command.seq} "
                    f"| vehicle={vehicle} report_seq={report_seq}", "info" if ok else "error")

    def report_ready(self, client, command_seq, by="operator"):
        """The operator says the vehicle is at the assistance point. Returns (ok, detail, report_seq).
        `by` names who decided, for the log (local test mode reports automatically)."""
        with self._lock:
            a = self.state.assistance_request
            if a is None or a["seq"] != command_seq:
                return False, f"no AssistanceRequest with seq {command_seq} in this run", None
            if not (a.get("ack") or {}).get("ok"):
                return False, "the AssistanceRequest was not acknowledged", None
            if (a.get("readiness") or {}).get("ok"):
                return False, f"readiness already reported (report_seq {a['readiness']['report_seq']})", None
            vehicle = a["ack"]["vehicle"]
            if client is None or not client.is_connected():
                return False, "not connected to RoboCommand", None
            try:
                ok, report_seq = self.publish_ready(client=client, vehicle_id=vehicle, command_seq=command_seq)
            except Exception as error:
                ok, report_seq = False, None
                self.output(f"[ERROR] ReadinessReport for seq={command_seq} failed: {error}", "error")
            self.state.set_readiness(command_seq, report_seq, ok)
        self.output(f"[COMMAND] ReadinessReport {'queued' if ok else 'FAILED'} ({by}) | command_seq={command_seq} "
                    f"| vehicle={vehicle} report_seq={report_seq}", "info" if ok else "error")
        return ok, "queued" if ok else "publish failed", report_seq
