# ============================================================
# AQUAHOLICS ROBOTX 2026
# SIMULATED AUTOMATIC TASK REPORT TEST
#
# Fires test task reports (pipeline survey, resource delivery,
# docking, firefighting) after RunStart in local test mode.
# ============================================================

import time
import threading

import config
from robotx import rx_common_pb2

from task_reports import (
    publish_pipeline_survey,
    publish_resource_delivery,
    publish_docking_report,
    publish_firefighting_report,
)


_started = False
_lock = threading.Lock()


def _run_test(client, output_fn):

    time.sleep(1)

    output_fn()
    output_fn("====================================================")
    output_fn("LOCAL TASK REPORT TEST")
    output_fn("====================================================")

    # [16] Pipeline Survey
    success, sequence = publish_pipeline_survey(
        client=client,
        vehicle_id=config.USV_ID,
        latitude=1.28075,
        longitude=103.85560,
    )
    if success:
        output_fn(f"[16] PipelineSurveyReport publish queued  vehicle={config.USV_ID} seq={sequence}")

    time.sleep(0.75)

    # [17] Resource Delivery
    success, sequence = publish_resource_delivery(
        client=client,
        vehicle_id=config.USV_ID,
        task=rx_common_pb2.TASK_INFRA_SURVEY_REPAIR,
        resource_color=rx_common_pb2.COLOR_RED,
        delivery_circle_color=rx_common_pb2.COLOR_GREEN,
    )
    if success:
        output_fn(f"[17] ResourceDeliveryRequest publish queued  vehicle={config.USV_ID} seq={sequence}")

    time.sleep(0.75)

    # [18] Docking
    success, sequence = publish_docking_report(
        client=client,
        vehicle_id=config.USV_ID,
        bay_id=1,
    )
    if success:
        output_fn(f"[18] DockingReport publish queued  vehicle={config.USV_ID} bay=1 seq={sequence}")

    time.sleep(0.75)

    # [19] Firefighting
    success, sequence = publish_firefighting_report(
        client=client,
        vehicle_id=config.UAV_ID,
        window_id=1,
    )
    if success:
        output_fn(f"[19] FirefightingReport publish queued  vehicle={config.UAV_ID} window=1 seq={sequence}")

    output_fn()
    output_fn("DATA = LOCAL SIMULATION")
    output_fn("====================================================")
    output_fn("LOCAL TASK REPORT TEST COMPLETE")
    output_fn("====================================================")


def start(client, output_fn):
    """Launch the automatic task report test once (no-op on repeated calls)."""
    global _started

    if not config.AUTO_LOCAL_TASK_REPORT_TEST:
        return

    with _lock:
        if _started:
            return
        _started = True

    threading.Thread(
        target=_run_test,
        args=(client, output_fn),
        daemon=True,
        name="robotx-local-task-test",
    ).start()
