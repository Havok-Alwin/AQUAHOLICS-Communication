import dataclasses
import itertools
import math

import pytest

from decision.interpreter import interpret_detection
from decision.rules import TASK1_CORE_RULES
from decision.settings import DEFAULT_MIN_MARKER_CONFIDENCE
from interfaces.messages import (
    Detection,
    InterpretationReason as R,
    InvalidMessageError,
    LightColor as C,
    LightState as S,
    MarkerType as M,
    ObjectClass as O,
)

# The handbook rules, written out independently of rules.py on purpose.
EXPECTED_TASK1 = {
    (C.RED, S.FLASHING): M.RED,
    (C.GREEN, S.FLASHING): M.GREEN,
    (C.BLUE, S.FLASHING): M.ENTRY,
    (C.BLUE, S.STEADY): M.EXIT,
    (C.NONE, S.OFF): M.OBSTACLE,
}


def det(**overrides) -> Detection:
    fields = dict(
        timestamp_s=100.0, track_id=7, object_class=O.TASK1_BUOY,
        light_color=C.RED, light_state=S.FLASHING,
        range_m=12.5, bearing_deg=-15.0,
        range_sigma_m=0.5, bearing_sigma_deg=2.0,
        detection_confidence=0.9, light_confidence=0.8,
    )
    fields.update(overrides)
    return Detection(**fields)


# ------------------------------------------------------------ rules (normal)

@pytest.mark.parametrize("key,marker", EXPECTED_TASK1.items())
def test_task1_core_meanings(key, marker):
    color, state = key
    out = interpret_detection(det(light_color=color, light_state=state))
    assert out.marker_type is marker
    assert out.reason is R.OK


@pytest.mark.parametrize("color,state", list(itertools.product(C, S)))
def test_unknown_obstacle_is_always_obstacle(color, state):
    out = interpret_detection(
        det(object_class=O.UNKNOWN_OBSTACLE, light_color=color,
            light_state=state))
    assert out.marker_type is M.OBSTACLE
    assert out.reason is R.OK


# ------------------------------------------------- rules (edge / UNKNOWN)

def test_steady_red_and_green_are_undefined_for_core():
    for color in (C.RED, C.GREEN):
        out = interpret_detection(det(light_color=color, light_state=S.STEADY))
        assert out.marker_type is M.UNKNOWN
        assert out.reason is R.UNDEFINED_PATTERN


def test_unknown_light_state_is_unknown_not_obstacle():
    # A flashing light in its dark second must never become OBSTACLE.
    for color in (C.RED, C.GREEN, C.BLUE, C.NONE):
        out = interpret_detection(det(light_color=color, light_state=S.UNKNOWN))
        assert out.marker_type is M.UNKNOWN
        assert out.reason is R.LIGHT_NOT_DETERMINED


def test_unknown_light_color_is_unknown():
    for state in (S.FLASHING, S.STEADY, S.OFF):
        out = interpret_detection(det(light_color=C.UNKNOWN, light_state=state))
        assert out.marker_type is M.UNKNOWN
        assert out.reason is R.LIGHT_NOT_DETERMINED


@pytest.mark.parametrize("color,state", [
    (C.NONE, S.FLASHING), (C.NONE, S.STEADY),
    (C.RED, S.OFF), (C.GREEN, S.OFF), (C.BLUE, S.OFF),
])
def test_contradictory_light_data_is_unknown(color, state):
    out = interpret_detection(det(light_color=color, light_state=state))
    assert out.marker_type is M.UNKNOWN
    assert out.reason is R.INCONSISTENT_LIGHT


# --------------------------------------------------------------- confidence

def test_task1_confidence_is_min_of_detection_and_light():
    out = interpret_detection(
        det(detection_confidence=0.9, light_confidence=0.7))
    assert out.marker_confidence == 0.7
    out = interpret_detection(
        det(detection_confidence=0.6, light_confidence=0.95))
    assert out.marker_confidence == 0.6


def test_unknown_obstacle_confidence_ignores_light_confidence():
    out = interpret_detection(
        det(object_class=O.UNKNOWN_OBSTACLE, detection_confidence=0.8,
            light_confidence=0.0))
    assert out.marker_type is M.OBSTACLE
    assert out.marker_confidence == 0.8


def test_threshold_boundary_equal_passes_below_fails():
    at = interpret_detection(
        det(detection_confidence=0.5, light_confidence=0.5),
        min_marker_confidence=0.5)
    assert at.marker_type is M.RED
    below = interpret_detection(
        det(detection_confidence=0.9, light_confidence=0.4999),
        min_marker_confidence=0.5)
    assert below.marker_type is M.UNKNOWN
    assert below.reason is R.LOW_CONFIDENCE


def test_low_confidence_off_light_is_unknown():
    # "OFF" with a shaky light reading could be a flashing light in its dark
    # phase, so it must not be trusted as an obstacle label.
    out = interpret_detection(
        det(light_color=C.NONE, light_state=S.OFF, light_confidence=0.3))
    assert out.marker_type is M.UNKNOWN
    assert out.reason is R.LOW_CONFIDENCE


def test_custom_threshold():
    d = det(detection_confidence=0.7, light_confidence=0.7)
    assert interpret_detection(d, 0.7).marker_type is M.RED
    assert interpret_detection(d, 0.71).marker_type is M.UNKNOWN


@pytest.mark.parametrize("bad", [-0.1, 1.1, math.nan, math.inf, "0.5", True])
def test_invalid_threshold_raises(bad):
    with pytest.raises(InvalidMessageError):
        interpret_detection(det(), min_marker_confidence=bad)


def test_default_threshold_is_used():
    d = det(detection_confidence=DEFAULT_MIN_MARKER_CONFIDENCE - 0.01,
            light_confidence=1.0)
    assert interpret_detection(d).reason is R.LOW_CONFIDENCE


def test_unknown_keeps_object_confidence():
    out = interpret_detection(
        det(light_state=S.UNKNOWN, detection_confidence=0.93))
    assert out.marker_type is M.UNKNOWN
    assert out.marker_confidence == 0.0
    assert out.object_confidence == 0.93


# ------------------------------------------------- pass-through and purity

def test_fields_are_copied_unchanged():
    d = det(timestamp_s=-3.5, track_id=-2, range_m=33.3, bearing_deg=179.5,
            range_sigma_m=1.25, bearing_sigma_deg=4.5)
    out = interpret_detection(d)
    assert (out.timestamp_s, out.track_id) == (-3.5, -2)
    assert (out.range_m, out.bearing_deg) == (33.3, 179.5)
    assert (out.range_sigma_m, out.bearing_sigma_deg) == (1.25, 4.5)
    assert out.object_confidence == d.detection_confidence


def test_deterministic_and_input_unchanged():
    d = det()
    before = dataclasses.replace(d)
    assert interpret_detection(d) == interpret_detection(d)
    assert d == before


def test_messages_are_frozen():
    out = interpret_detection(det())
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.marker_type = M.EXIT
    with pytest.raises(dataclasses.FrozenInstanceError):
        det().range_m = 1.0


# -------------------------------------------------------------- rules table

def test_rules_table_is_exactly_the_spec():
    task1 = {(c, s): m for (o, c, s), m in TASK1_CORE_RULES.items()
             if o is O.TASK1_BUOY}
    assert task1 == EXPECTED_TASK1
    obstacle_rows = {k: m for k, m in TASK1_CORE_RULES.items()
                     if k[0] is O.UNKNOWN_OBSTACLE}
    assert len(obstacle_rows) == len(C) * len(S)
    assert set(obstacle_rows.values()) == {M.OBSTACLE}
    assert len(TASK1_CORE_RULES) == len(EXPECTED_TASK1) + len(C) * len(S)


def test_rules_table_is_read_only():
    key = (O.TASK1_BUOY, C.RED, S.STEADY)
    with pytest.raises(TypeError):
        TASK1_CORE_RULES[key] = M.RED
    with pytest.raises(TypeError):
        del TASK1_CORE_RULES[(O.TASK1_BUOY, C.RED, S.FLASHING)]


# ------------------------------------------------ exhaustive invariant check

CONFS = (0.0, 0.2, 0.49, 0.5, 0.51, 0.9, 1.0)


def test_exhaustive_all_combinations_never_raise_and_hold_invariants():
    threshold = DEFAULT_MIN_MARKER_CONFIDENCE
    for obj, color, state, dc, lc in itertools.product(O, C, S, CONFS, CONFS):
        out = interpret_detection(det(
            object_class=obj, light_color=color, light_state=state,
            detection_confidence=dc, light_confidence=lc))

        assert out.object_confidence == dc
        if out.marker_type is M.UNKNOWN:
            assert out.marker_confidence == 0.0
            assert out.reason is not R.OK
        else:
            assert out.reason is R.OK
            assert out.marker_confidence >= threshold

        if obj is O.UNKNOWN_OBSTACLE:
            expected = M.OBSTACLE if dc >= threshold else M.UNKNOWN
        elif (color, state) in EXPECTED_TASK1:
            ok = min(dc, lc) >= threshold
            expected = EXPECTED_TASK1[(color, state)] if ok else M.UNKNOWN
        else:
            expected = M.UNKNOWN  # not in the table: never guess
        assert out.marker_type is expected


# ------------------------------------------------ Detection input validation

@pytest.mark.parametrize("field,value", [
    ("timestamp_s", math.nan), ("timestamp_s", math.inf),
    ("timestamp_s", "100"), ("timestamp_s", None), ("timestamp_s", True),
    ("track_id", 1.5), ("track_id", "7"), ("track_id", True),
    ("object_class", "TASK1_BUOY"), ("light_color", "RED"),
    ("light_state", "FLASHING"), ("object_class", None),
    ("range_m", 0.0), ("range_m", -1.0), ("range_m", math.nan),
    ("range_m", math.inf),
    ("bearing_deg", -180.0), ("bearing_deg", 180.01),
    ("bearing_deg", math.nan),
    ("range_sigma_m", -0.1), ("bearing_sigma_deg", -0.1),
    ("range_sigma_m", math.nan), ("bearing_sigma_deg", math.inf),
    ("detection_confidence", 1.01), ("detection_confidence", -0.01),
    ("detection_confidence", math.nan),
    ("light_confidence", 1.01), ("light_confidence", -0.01),
    ("light_confidence", math.inf),
])
def test_invalid_detection_values_are_rejected(field, value):
    with pytest.raises(InvalidMessageError):
        det(**{field: value})


@pytest.mark.parametrize("field,value", [
    ("timestamp_s", -5.0), ("timestamp_s", 0), ("track_id", -1),
    ("track_id", 0), ("bearing_deg", 180.0), ("bearing_deg", -179.999),
    ("range_sigma_m", 0.0), ("bearing_sigma_deg", 0.0),
    ("detection_confidence", 0.0), ("detection_confidence", 1.0),
    ("range_m", 1e-6),
])
def test_boundary_detection_values_are_accepted(field, value):
    det(**{field: value})


def test_integers_are_accepted_and_stored_as_float():
    d = det(timestamp_s=5, range_m=10, bearing_deg=0,
            range_sigma_m=1, bearing_sigma_deg=1,
            detection_confidence=1, light_confidence=0)
    assert isinstance(d.timestamp_s, float)
    assert isinstance(d.range_m, float)
    assert isinstance(d.detection_confidence, float)