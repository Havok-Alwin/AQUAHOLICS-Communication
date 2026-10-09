import math

import pytest

from decision.world_model import DEFAULT_MIN_GATE_M, WorldModel
from interfaces.messages import (
    BoatState,
    Interpretation,
    InterpretationReason,
    MarkerType,
)


def boat(t, heading_sigma_deg=1.0, position_sigma_m=0.5):
    return BoatState(
        timestamp_s=t, x_m=0.0, y_m=0.0, heading_deg=0.0,
        heading_sigma_deg=heading_sigma_deg, speed_mps=0.0,
        position_sigma_m=position_sigma_m, valid=True,
    )


def interp(t, range_m, bearing_deg, range_sigma_m=0.2,
           bearing_sigma_deg=2.0, track_id=1):
    return Interpretation(
        timestamp_s=t, track_id=track_id, marker_type=MarkerType.RED,
        marker_confidence=0.9, object_confidence=0.9,
        reason=InterpretationReason.OK, range_m=range_m,
        bearing_deg=bearing_deg, range_sigma_m=range_sigma_m,
        bearing_sigma_deg=bearing_sigma_deg,
    )


# ---------------------------------------------------------------- F1 ----
# One term of the true per-observation uncertainty is made dominant in each
# case, so each omitted term (bearing, heading, pose) is tested on its own.
F1_CASES = {
    "bearing": dict(heading_sigma_deg=0.1, position_sigma_m=0.1,
                    bearing_sigma_deg=2.0, probe=(30.0, 3.0)),
    "heading": dict(heading_sigma_deg=3.0, position_sigma_m=0.1,
                    bearing_sigma_deg=0.1, probe=(30.0, 3.0)),
    "pose":    dict(heading_sigma_deg=0.1, position_sigma_m=1.5,
                    bearing_sigma_deg=0.1, probe=(31.5, 0.0)),
}


def _established_model(case):
    model = WorldModel()
    for i in range(30):
        t = float(i)
        model.update_interpretations(
            boat(t, case["heading_sigma_deg"], case["position_sigma_m"]),
            [interp(t, 30.0, 0.0, bearing_sigma_deg=case["bearing_sigma_deg"])],
        )
    return model


@pytest.mark.parametrize("name", sorted(F1_CASES))
def test_f1_probe_within_about_1p5_sigma_associates(name):
    case = F1_CASES[name]
    model = _established_model(case)

    # Preconditions that make the test meaningful.
    obj = model.get_objects()[0]
    assert obj.position_sigma_m < 0.35                 # sigma has collapsed
    r, b = case["probe"]
    dx = r * math.sin(math.radians(b))
    dy = r * math.cos(math.radians(b)) - 30.0
    true_sigma = math.sqrt(
        0.2**2
        + (r * math.radians(case["bearing_sigma_deg"]))**2
        + (r * math.radians(case["heading_sigma_deg"]))**2
        + case["position_sigma_m"]**2
    )
    assert math.hypot(dx, dy) <= 1.6 * true_sigma      # a 1.6-sigma error
    assert math.hypot(dx, dy) > DEFAULT_MIN_GATE_M     # not hidden by the gate floor

    snap = model.update_interpretations(
        boat(30.0, case["heading_sigma_deg"], case["position_sigma_m"]),
        [interp(30.0, r, b, bearing_sigma_deg=case["bearing_sigma_deg"])],
    )

    assert len(snap.objects) == 1
    assert snap.objects[0].observation_count == 31


def test_f1_guard_clearly_different_buoy_stays_separate():
    case = F1_CASES["bearing"]
    model = _established_model(case)
    snap = model.update_interpretations(
        boat(30.0, case["heading_sigma_deg"], case["position_sigma_m"]),
        [interp(30.0, 30.0, 15.0, bearing_sigma_deg=case["bearing_sigma_deg"])],
    )                                                  # about 7.8 m away
    assert len(snap.objects) == 2


# ---------------------------------------------------------------- F2 ----
# Two boxes for one buoy: 10 m range, bearings 0.0 and 0.5 deg = 0.09 m apart.
def _pair(t):
    return [interp(t, 10.0, 0.0, track_id=1),
            interp(t, 10.0, 0.5, track_id=2)]


def test_f2_duplicate_pair_in_empty_world_creates_one_object():
    model = WorldModel()
    snap = model.update_interpretations(boat(0.0), _pair(0.0))
    assert len(snap.objects) == 1


def test_f2_duplicate_pair_next_to_existing_object_spawns_nothing():
    model = WorldModel()
    for t in (0.0, 1.0, 2.0):
        model.update_interpretations(boat(t), [interp(t, 10.0, 0.0)])
    assert len(model.get_objects()) == 1
    snap = model.update_interpretations(boat(3.0), _pair(3.0))
    assert len(snap.objects) == 1


def test_f2_persistent_duplicate_pair_never_becomes_second_confirmed_object():
    model = WorldModel()
    for t in (0.0, 1.0, 2.0, 3.0, 4.0):
        model.update_interpretations(boat(t), _pair(t))
    assert len(model.get_objects()) == 1
    assert len(model.get_confirmed_objects()) == 1


def test_f2_guard_two_buoys_five_metres_apart_stay_two_objects():
    model = WorldModel()
    snap = model.update_interpretations(
        boat(0.0),
        [interp(0.0, 10.0, 0.0, track_id=1), interp(0.0, 10.0, 30.0, track_id=2)],
    )                                                  # about 5.2 m apart
    assert len(snap.objects) == 2