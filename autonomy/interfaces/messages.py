"""Shared message types for the Task 1 Core decision layer (Section 2).

Pure data only: no ROS, no clocks, no file or network access.

Units used everywhere (the unit is also in every field name):
    time       -> seconds (float), the moment the sensor data was CAPTURED
    distance   -> meters
    angle      -> degrees
    bearing    -> degrees relative to the boat's bow, positive = starboard
                  (right), allowed range (-180, 180]
    confidence -> float in [0.0, 1.0]

Two kinds of "bad input" are kept separate on purpose:
    * Values that make no sense at all (NaN, negative range, confidence 1.7,
      a string instead of an enum): the message cannot be built and
      InvalidMessageError is raised. Whoever creates the message must catch
      it at the boundary and drop that one message.
    * Values that are valid but unhelpful (light state UNKNOWN, contradictory
      light data): the message exists, and interpretation returns UNKNOWN.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class InvalidMessageError(ValueError):
    """A message field has the wrong type or is outside its allowed range."""


class ObjectClass(Enum):
    TASK1_BUOY = "TASK1_BUOY"              # RoboBuoy with diamond panels + beacon
    UNKNOWN_OBSTACLE = "UNKNOWN_OBSTACLE"  # floating object, not a Task 1 buoy


class LightColor(Enum):
    RED = "RED"
    GREEN = "GREEN"
    BLUE = "BLUE"
    NONE = "NONE"        # buoy seen, no light seen
    UNKNOWN = "UNKNOWN"  # Section 1 could not tell


class LightState(Enum):
    FLASHING = "FLASHING"
    STEADY = "STEADY"
    OFF = "OFF"          # clearly seen with no light for the whole window
    UNKNOWN = "UNKNOWN"  # not enough observation time yet, or unclear


class MarkerType(Enum):
    RED = "RED"
    GREEN = "GREEN"
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    OBSTACLE = "OBSTACLE"
    UNKNOWN = "UNKNOWN"


class InterpretationReason(Enum):
    """Why an Interpretation is what it is. OK means marker_type is known."""
    OK = "OK"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    LIGHT_NOT_DETERMINED = "LIGHT_NOT_DETERMINED"
    INCONSISTENT_LIGHT = "INCONSISTENT_LIGHT"
    UNDEFINED_PATTERN = "UNDEFINED_PATTERN"


# ---------------------------------------------------------------- validation

def _real(name: str, value: object) -> float:
    """Return value as a finite float, or raise InvalidMessageError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidMessageError(
            f"{name} must be a number, got {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError:
        raise InvalidMessageError(f"{name} is too large") from None
    if not math.isfinite(number):
        raise InvalidMessageError(f"{name} must be finite, got {number}")
    return number


def _enum(name: str, value: object, enum_type: type) -> None:
    if not isinstance(value, enum_type):
        raise InvalidMessageError(
            f"{name} must be a {enum_type.__name__}, got {value!r}")


def validate_confidence(name: str, value: object) -> float:
    number = _real(name, value)
    if not 0.0 <= number <= 1.0:
        raise InvalidMessageError(f"{name} must be in [0, 1], got {number}")
    return number


# ------------------------------------------------------------------ messages

@dataclass(frozen=True)
class Detection:
    """One buoy seen by Section 1 (one tracked buoy at one capture time).

    Section 1 decides flashing / steady / off by watching the buoy over time.
    A flashing light that happens to be in its dark second must be reported
    with light_state UNKNOWN, never OFF.
    """
    timestamp_s: float           # camera frame CAPTURE time, seconds
    track_id: int                # Section 1 tracker ID (not the world-model ID)
    object_class: ObjectClass
    light_color: LightColor
    light_state: LightState
    range_m: float               # boat to buoy, meters, > 0
    bearing_deg: float           # from bow, + = starboard, in (-180, 180]
    range_sigma_m: float         # 1-sigma range error, meters, >= 0
    bearing_sigma_deg: float     # 1-sigma bearing error, degrees, >= 0
    detection_confidence: float  # "a buoy is really there", [0, 1]
    light_confidence: float      # "light color and state are right", [0, 1]

    def __post_init__(self) -> None:
        put = object.__setattr__  # frozen dataclass: normalise via object

        # Only "is a finite number" is enforced for time: the interface
        # contract does not limit its sign or origin.
        put(self, "timestamp_s", _real("timestamp_s", self.timestamp_s))

        # Only "is an int" is enforced for track_id: the contract does not
        # limit its sign.
        if isinstance(self.track_id, bool) or not isinstance(self.track_id, int):
            raise InvalidMessageError(
                f"track_id must be an int, got {self.track_id!r}")

        _enum("object_class", self.object_class, ObjectClass)
        _enum("light_color", self.light_color, LightColor)
        _enum("light_state", self.light_state, LightState)

        range_m = _real("range_m", self.range_m)
        if range_m <= 0.0:
            raise InvalidMessageError(f"range_m must be > 0, got {range_m}")
        put(self, "range_m", range_m)

        bearing = _real("bearing_deg", self.bearing_deg)
        if not -180.0 < bearing <= 180.0:
            raise InvalidMessageError(
                f"bearing_deg must be in (-180, 180], got {bearing}")
        put(self, "bearing_deg", bearing)

        for name in ("range_sigma_m", "bearing_sigma_deg"):
            sigma = _real(name, getattr(self, name))
            if sigma < 0.0:
                raise InvalidMessageError(f"{name} must be >= 0, got {sigma}")
            put(self, name, sigma)

        put(self, "detection_confidence",
            validate_confidence("detection_confidence",
                                self.detection_confidence))
        put(self, "light_confidence",
            validate_confidence("light_confidence", self.light_confidence))


@dataclass(frozen=True)
class Interpretation:
    """The meaning of ONE Detection. Position fields are copied unchanged.

    object_confidence : copied from the Detection. "Something physical is
                        there." Stays meaningful even when marker_type is
                        UNKNOWN, so downstream can still keep clear of it.
    marker_confidence : confidence that marker_type is correct. Always 0.0
                        when marker_type is UNKNOWN.
    """
    timestamp_s: float
    track_id: int
    marker_type: MarkerType
    marker_confidence: float
    object_confidence: float
    reason: InterpretationReason
    range_m: float
    bearing_deg: float
    range_sigma_m: float
    bearing_sigma_deg: float