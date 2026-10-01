# ============================================================
# AQUAHOLICS ROBOTX 2026
# TASK REPORT MANAGER
#
# RxReport:
# OCS -> RoboCommand
# ============================================================

import paho.mqtt.client as mqtt
import logging

from google.protobuf.timestamp_pb2 import Timestamp

import config
import common_pb2

from robotx import rx_reports_pb2
from robotx import rx_common_pb2

from sequence_manager import (
    next_report_sequence
)
from logger import get_logger, safe_log

logger = get_logger()


# ============================================================
# TIMESTAMP
# ============================================================

def current_timestamp():

    timestamp = Timestamp()

    timestamp.GetCurrentTime()

    return timestamp


# ============================================================
# VALIDATE VEHICLE
# ============================================================

def validate_vehicle(vehicle_id):

    if vehicle_id not in config.VEHICLE_IDS:

        raise ValueError(
            f"Invalid vehicle_id: "
            f"{vehicle_id}"
        )


# ============================================================
# GENERIC RxReport PUBLISH
# ============================================================

def publish_report(
    client,
    vehicle_id,
    report
):

    validate_vehicle(
        vehicle_id
    )


    topic = config.report_topic(
        vehicle_id
    )


    # Guard against a topic/envelope identity mismatch before putting bytes on MQTT.
    if report.team_id != config.TEAM_ID or report.vehicle_id != vehicle_id:
        safe_log(logger, logging.ERROR,
                 "Report rejected identity mismatch | topic=%s | topic_team=%s | message_team=%s | topic_vehicle=%s | message_vehicle=%s",
                 topic, config.TEAM_ID, report.team_id, vehicle_id, report.vehicle_id)
        return False

    try:
        result = client.publish(topic=topic, payload=report.SerializeToString(), qos=config.MQTT_QOS)
        success = result.rc == mqtt.MQTT_ERR_SUCCESS
        result_code = result.rc
    except Exception as error:
        success = False
        result_code = f"exception:{error}"
    safe_log(logger, logging.INFO if success else logging.ERROR,
             "MQTT TX %s | topic=%s | team=%s | vehicle=%s | seq=%s | rc=%s",
             "queued" if success else "failed", topic, report.team_id, report.vehicle_id, report.seq, result_code)
    return success


# ============================================================
# TASK 2
# PIPELINE SURVEY
# ============================================================

def publish_pipeline_survey(
    client,
    vehicle_id,
    latitude,
    longitude,
    segments=None
):

    validate_vehicle(
        vehicle_id
    )


    sequence = (
        next_report_sequence(
            vehicle_id
        )
    )


    if segments is None:

        segments = [

            rx_common_pb2
            .PIPELINE_SEGMENT_INTACT,

            rx_common_pb2
            .PIPELINE_SEGMENT_DAMAGED,

            rx_common_pb2
            .PIPELINE_SEGMENT_INTACT,

        ]


    body = (
        rx_reports_pb2
        .PipelineSurveyReport(

            active_buoy_position=
            common_pb2.LatLng(

                latitude=latitude,

                longitude=longitude

            ),

            segments=segments

        )
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

        pipeline_survey=
        body

    )


    success = publish_report(

        client,
        vehicle_id,
        report

    )


    return success, sequence


# ============================================================
# RESOURCE DELIVERY
# ============================================================

def publish_resource_delivery(
    client,
    vehicle_id,
    task,
    resource_color,
    delivery_circle_color
):

    validate_vehicle(
        vehicle_id
    )


    sequence = (
        next_report_sequence(
            vehicle_id
        )
    )


    body = (
        rx_reports_pb2
        .ResourceDeliveryRequest(

            task=
            task,

            resource_color=
            resource_color,

            delivery_circle_color=
            delivery_circle_color

        )
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

        resource_delivery=
        body

    )


    success = publish_report(

        client,
        vehicle_id,
        report

    )


    return success, sequence


# ============================================================
# TASK 3
# DOCKING
# ============================================================

def publish_docking_report(
    client,
    vehicle_id,
    bay_id
):

    validate_vehicle(
        vehicle_id
    )


    sequence = (
        next_report_sequence(
            vehicle_id
        )
    )


    body = (
        rx_reports_pb2
        .DockingReport(

            bay_id=
            bay_id

        )
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

        docking=
        body

    )


    success = publish_report(

        client,
        vehicle_id,
        report

    )


    return success, sequence


# ============================================================
# TASK 3
# FIREFIGHTING
# ============================================================

def publish_firefighting_report(
    client,
    vehicle_id,
    window_id
):

    validate_vehicle(
        vehicle_id
    )


    sequence = (
        next_report_sequence(
            vehicle_id
        )
    )


    body = (
        rx_reports_pb2
        .FirefightingReport(

            window_id=
            window_id

        )
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

        firefighting=
        body

    )


    success = publish_report(

        client,
        vehicle_id,
        report

    )


    return success, sequence


# ============================================================
# TASK 4
# INCIDENT ACKNOWLEDGEMENT
# ============================================================

def publish_incident_ack(
    client,
    vehicle_id,
    command_seq
):

    validate_vehicle(
        vehicle_id
    )


    sequence = (
        next_report_sequence(
            vehicle_id
        )
    )


    body = (
        rx_reports_pb2
        .IncidentAck(

            command_seq=
            command_seq

        )
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

        incident_ack=
        body

    )


    success = publish_report(

        client,
        vehicle_id,
        report

    )


    return success, sequence


# ============================================================
# TASK 4
# READINESS REPORT
# ============================================================

def publish_readiness_report(
    client,
    vehicle_id,
    command_seq
):

    validate_vehicle(
        vehicle_id
    )


    sequence = (
        next_report_sequence(
            vehicle_id
        )
    )


    body = (
        rx_reports_pb2
        .ReadinessReport(

            command_seq=
            command_seq

        )
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

        readiness=
        body

    )


    success = publish_report(

        client,
        vehicle_id,
        report

    )


    return success, sequence