"""
Shared message types for the Task 1 Core decision layer (Section 2).

Pure data only: no ROS, no clocks, no file or network access.

Units used everywhere:
    time       -> seconds (float), the moment the sensor data was CAPTURED
    distance   -> meters
    angle      -> degrees
    bearing    -> degrees relative to the boat's bow, positive = starboard
                  (right), allowed range (-180, 180]
    confidence -> float in [0.0, 1.0]

Two kinds of "bad input" are kept separate on purpose:
    * Values that make no sense at all (NaN, negative range, confidence 1.7,
      a string instead of an enum): the message cannot be built and
      InvalidMessageError is raised.
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
    TASK1_BUOY = "TASK1_BUOY"
    UNKNOWN_OBSTACLE = "UNKNOWN_OBSTACLE"


class LightColor(Enum):
    RED = "RED"
    GREEN = "GREEN"
    BLUE = "BLUE"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class LightState(Enum):
    FLASHING = "FLASHING"
    STEADY = "STEADY"
    OFF = "OFF"
    UNKNOWN = "UNKNOWN"


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


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _real(name: str, value: object) -> float:
    """Return value as a finite float, or raise InvalidMessageError."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidMessageError(
            f"{name} must be a number, got {type(value).__name__}"
        )

    try:
        number = float(value)
    except OverflowError:
        raise InvalidMessageError(f"{name} is too large") from None

    if not math.isfinite(number):
        raise InvalidMessageError(
            f"{name} must be finite, got {number}"
        )

    return number


def _enum(name: str, value: object, enum_type: type) -> None:
    if not isinstance(value, enum_type):
        raise InvalidMessageError(
            f"{name} must be a {enum_type.__name__}, got {value!r}"
        )


def validate_confidence(name: str, value: object) -> float:
    number = _real(name, value)

    if not 0.0 <= number <= 1.0:
        raise InvalidMessageError(
            f"{name} must be in [0, 1], got {number}"
        )

    return number


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Detection:
    """One buoy seen by Section 1.

    Section 1 decides flashing / steady / off by watching the buoy over time.

    A flashing light that happens to be in its dark second must be reported
    with light_state UNKNOWN, never OFF.
    """

    timestamp_s: float
    track_id: int

    object_class: ObjectClass

    light_color: LightColor
    light_state: LightState

    range_m: float
    bearing_deg: float

    range_sigma_m: float
    bearing_sigma_deg: float

    detection_confidence: float
    light_confidence: float

    def __post_init__(self) -> None:
        put = object.__setattr__

        put(
            self,
            "timestamp_s",
            _real("timestamp_s", self.timestamp_s),
        )

        if isinstance(self.track_id, bool) or not isinstance(
            self.track_id, int
        ):
            raise InvalidMessageError(
                f"track_id must be an int, got {self.track_id!r}"
            )

        _enum(
            "object_class",
            self.object_class,
            ObjectClass,
        )

        _enum(
            "light_color",
            self.light_color,
            LightColor,
        )

        _enum(
            "light_state",
            self.light_state,
            LightState,
        )

        range_m = _real("range_m", self.range_m)

        if range_m <= 0.0:
            raise InvalidMessageError(
                f"range_m must be > 0, got {range_m}"
            )

        put(self, "range_m", range_m)

        bearing = _real(
            "bearing_deg",
            self.bearing_deg,
        )

        if not -180.0 < bearing <= 180.0:
            raise InvalidMessageError(
                f"bearing_deg must be in (-180, 180], got {bearing}"
            )

        put(self, "bearing_deg", bearing)

        for name in (
            "range_sigma_m",
            "bearing_sigma_deg",
        ):
            sigma = _real(
                name,
                getattr(self, name),
            )

            if sigma < 0.0:
                raise InvalidMessageError(
                    f"{name} must be >= 0, got {sigma}"
                )

            put(self, name, sigma)

        put(
            self,
            "detection_confidence",
            validate_confidence(
                "detection_confidence",
                self.detection_confidence,
            ),
        )

        put(
            self,
            "light_confidence",
            validate_confidence(
                "light_confidence",
                self.light_confidence,
            ),
        )


# ---------------------------------------------------------------------------
# Interpretation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Interpretation:
    """The meaning of one Detection.

    object_confidence:
        Confidence that something physical is there.

    marker_confidence:
        Confidence that marker_type is correct.

    marker_confidence is always 0.0 when marker_type is UNKNOWN.
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


# ---------------------------------------------------------------------------
# BoatState
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BoatState:
    """Boat pose/state supplied to Section 2.

    Position uses the World Model map frame:

        x_m = East
        y_m = North

    Heading convention:

        0 deg   = North
        90 deg  = East
        180 deg = South
        270 deg = West

    Heading increases clockwise.

    timestamp_s must correspond to the pose time associated with the
    detection being processed.
    """

    timestamp_s: float

    x_m: float
    y_m: float

    heading_deg: float
    heading_sigma_deg: float

    speed_mps: float

    position_sigma_m: float

    valid: bool

    def __post_init__(self) -> None:
        put = object.__setattr__

        # Timestamp
        put(
            self,
            "timestamp_s",
            _real("timestamp_s", self.timestamp_s),
        )

        # Position
        put(
            self,
            "x_m",
            _real("x_m", self.x_m),
        )

        put(
            self,
            "y_m",
            _real("y_m", self.y_m),
        )

        # Heading
        heading = _real(
            "heading_deg",
            self.heading_deg,
        )

        # Normalize to [0, 360)
        heading = heading % 360.0

        put(
            self,
            "heading_deg",
            heading,
        )

        # Heading uncertainty
        heading_sigma = _real(
            "heading_sigma_deg",
            self.heading_sigma_deg,
        )

        if heading_sigma < 0.0:
            raise InvalidMessageError(
                f"heading_sigma_deg must be >= 0, got {heading_sigma}"
            )

        put(
            self,
            "heading_sigma_deg",
            heading_sigma,
        )

        # Speed
        speed = _real(
            "speed_mps",
            self.speed_mps,
        )

        if speed < 0.0:
            raise InvalidMessageError(
                f"speed_mps must be >= 0, got {speed}"
            )

        put(
            self,
            "speed_mps",
            speed,
        )

        # Position uncertainty
        position_sigma = _real(
            "position_sigma_m",
            self.position_sigma_m,
        )

        if position_sigma < 0.0:
            raise InvalidMessageError(
                f"position_sigma_m must be >= 0, got {position_sigma}"
            )

        put(
            self,
            "position_sigma_m",
            position_sigma,
        )

        # Validity flag
        if not isinstance(self.valid, bool):
            raise InvalidMessageError(
                f"valid must be a bool, got {self.valid!r}"
            )