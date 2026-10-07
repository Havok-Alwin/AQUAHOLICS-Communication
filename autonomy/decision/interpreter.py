"""Block 5: Marker Interpretation.

Translates ONE Detection into ONE Interpretation. Nothing else.

Pure function: same input, same output. No clock, no stored state, no I/O.
It does not track buoys over time, change mission stages, plan paths, avoid
obstacles, or generate waypoints.
"""
from __future__ import annotations

from decision.rules import TASK1_CORE_RULES
from decision.settings import DEFAULT_MIN_MARKER_CONFIDENCE
from interfaces.messages import (
    Detection,
    Interpretation,
    InterpretationReason,
    LightColor,
    LightState,
    MarkerType,
    ObjectClass,
    validate_confidence,
)


def _unmatched_reason(d: Detection) -> InterpretationReason:
    """Why a light pattern is not in the rules table (UNKNOWN cases only)."""
    if d.light_color is LightColor.UNKNOWN or d.light_state is LightState.UNKNOWN:
        return InterpretationReason.LIGHT_NOT_DETERMINED
    # "no light" must pair with "off"; any color must pair with an on-state.
    if (d.light_color is LightColor.NONE) != (d.light_state is LightState.OFF):
        return InterpretationReason.INCONSISTENT_LIGHT
    return InterpretationReason.UNDEFINED_PATTERN  # e.g. steady red


def _build(d: Detection, marker: MarkerType, marker_confidence: float,
           reason: InterpretationReason) -> Interpretation:
    return Interpretation(
        timestamp_s=d.timestamp_s,
        track_id=d.track_id,
        marker_type=marker,
        marker_confidence=marker_confidence,
        object_confidence=d.detection_confidence,
        reason=reason,
        range_m=d.range_m,
        bearing_deg=d.bearing_deg,
        range_sigma_m=d.range_sigma_m,
        bearing_sigma_deg=d.bearing_sigma_deg,
    )


def interpret_detection(
        detection: Detection,
        min_marker_confidence: float = DEFAULT_MIN_MARKER_CONFIDENCE,
) -> Interpretation:
    """Translate one Detection into one Interpretation.

    Meaning comes only from TASK1_CORE_RULES. Not in the table -> UNKNOWN.

    Confidence rule:
        TASK1_BUOY        marker_confidence = min(detection_confidence,
                                                  light_confidence)
        UNKNOWN_OBSTACLE  marker_confidence = detection_confidence
                          (its light data is ignored, so it must not count)
        marker_confidence < min_marker_confidence -> UNKNOWN (LOW_CONFIDENCE).
        Exactly equal to the threshold passes.
        The threshold is our own tunable setting (see decision/settings.py),
        not a RobotX requirement. Pass a different value to override it.
    min() because a label is only as trustworthy as its weakest input, and it
    assumes nothing about independence.

    When the marker is UNKNOWN: marker_confidence is 0.0, and
    object_confidence still says how sure we are something is physically
    there, so later modules can keep clear of it.
    """
    threshold = validate_confidence("min_marker_confidence",
                                    min_marker_confidence)

    marker = TASK1_CORE_RULES.get(
        (detection.object_class, detection.light_color, detection.light_state))
    if marker is None:
        return _build(detection, MarkerType.UNKNOWN, 0.0,
                      _unmatched_reason(detection))

    if detection.object_class is ObjectClass.UNKNOWN_OBSTACLE:
        confidence = detection.detection_confidence
    else:
        confidence = min(detection.detection_confidence,
                         detection.light_confidence)

    if confidence < threshold:
        return _build(detection, MarkerType.UNKNOWN, 0.0,
                      InterpretationReason.LOW_CONFIDENCE)

    return _build(detection, marker, confidence, InterpretationReason.OK)