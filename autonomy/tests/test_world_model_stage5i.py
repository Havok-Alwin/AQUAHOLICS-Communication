import math

import pytest

from interfaces.messages import (
    BoatState,
    Interpretation,
    InterpretationReason,
    MarkerType,
)

from decision.world_model import WorldModel


def boat(
    timestamp_s=0.0,
    x_m=0.0,
    y_m=0.0,
    heading_deg=0.0,
):
    return BoatState(
        timestamp_s=timestamp_s,
        x_m=x_m,
        y_m=y_m,
        heading_deg=heading_deg,
        heading_sigma_deg=1.0,
        speed_mps=0.0,
        position_sigma_m=0.1,
        valid=True,
    )


def interpretation(
    timestamp_s,
    range_m,
    bearing_deg,
    marker_type=MarkerType.RED,
    track_id=1,
):
    return Interpretation(
        timestamp_s=timestamp_s,
        track_id=track_id,
        marker_type=marker_type,
        marker_confidence=0.9,
        object_confidence=0.9,
        reason=InterpretationReason.OK,
        range_m=range_m,
        bearing_deg=bearing_deg,
        range_sigma_m=0.1,
        bearing_sigma_deg=0.1,
    )


def test_heading_zero_points_forward_to_north():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(heading_deg=0.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=0.0)],
    )

    obj = snapshot.objects[0]

    assert obj.x_m == pytest.approx(0.0, abs=1e-6)
    assert obj.y_m == pytest.approx(10.0, abs=1e-6)


def test_heading_ninety_points_forward_to_east():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(heading_deg=90.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=0.0)],
    )

    obj = snapshot.objects[0]

    assert obj.x_m == pytest.approx(10.0, abs=1e-6)
    assert obj.y_m == pytest.approx(0.0, abs=1e-6)


def test_heading_one_eighty_points_forward_to_south():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(heading_deg=180.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=0.0)],
    )

    obj = snapshot.objects[0]

    assert obj.x_m == pytest.approx(0.0, abs=1e-6)
    assert obj.y_m == pytest.approx(-10.0, abs=1e-6)


def test_heading_two_seventy_points_forward_to_west():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(heading_deg=270.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=0.0)],
    )

    obj = snapshot.objects[0]

    assert obj.x_m == pytest.approx(-10.0, abs=1e-6)
    assert obj.y_m == pytest.approx(0.0, abs=1e-6)


def test_starboard_bearing_is_positive_rotation():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(heading_deg=0.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=90.0)],
    )

    obj = snapshot.objects[0]

    # Heading 0 = North.
    # Positive bearing = starboard/right = East.
    assert obj.x_m == pytest.approx(10.0, abs=1e-6)
    assert obj.y_m == pytest.approx(0.0, abs=1e-6)


def test_port_bearing_is_negative_rotation():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(heading_deg=0.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=-90.0)],
    )

    obj = snapshot.objects[0]

    # Heading 0 = North.
    # Negative bearing = port/left = West.
    assert obj.x_m == pytest.approx(-10.0, abs=1e-6)
    assert obj.y_m == pytest.approx(0.0, abs=1e-6)


def test_heading_wraparound_360_matches_zero():
    model_zero = WorldModel()
    model_360 = WorldModel()

    snapshot_zero = model_zero.update_interpretations(
        boat(heading_deg=0.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=25.0)],
    )

    snapshot_360 = model_360.update_interpretations(
        boat(heading_deg=360.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=25.0)],
    )

    obj_zero = snapshot_zero.objects[0]
    obj_360 = snapshot_360.objects[0]

    assert obj_360.x_m == pytest.approx(obj_zero.x_m, abs=1e-6)
    assert obj_360.y_m == pytest.approx(obj_zero.y_m, abs=1e-6)


def test_heading_wraparound_negative_angle_matches_equivalent_heading():
    model_negative = WorldModel()
    model_equivalent = WorldModel()

    snapshot_negative = model_negative.update_interpretations(
        boat(heading_deg=-90.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=0.0)],
    )

    snapshot_equivalent = model_equivalent.update_interpretations(
        boat(heading_deg=270.0),
        [interpretation(0.0, range_m=10.0, bearing_deg=0.0)],
    )

    obj_negative = snapshot_negative.objects[0]
    obj_equivalent = snapshot_equivalent.objects[0]

    assert obj_negative.x_m == pytest.approx(obj_equivalent.x_m, abs=1e-6)
    assert obj_negative.y_m == pytest.approx(obj_equivalent.y_m, abs=1e-6)


def test_diagonal_projection_matches_expected_position():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(
            x_m=100.0,
            y_m=200.0,
            heading_deg=45.0,
        ),
        [
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=45.0,
            )
        ],
    )

    obj = snapshot.objects[0]

    # Heading 45° + bearing 45° = 90° absolute bearing.
    assert obj.x_m == pytest.approx(110.0, abs=1e-6)
    assert obj.y_m == pytest.approx(200.0, abs=1e-6)


def test_projection_preserves_distance_from_boat():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(
            x_m=100.0,
            y_m=-50.0,
            heading_deg=123.0,
        ),
        [
            interpretation(
                0.0,
                range_m=17.5,
                bearing_deg=-37.0,
            )
        ],
    )

    obj = snapshot.objects[0]

    distance = math.hypot(
        obj.x_m - 100.0,
        obj.y_m + 50.0,
    )

    assert distance == pytest.approx(17.5, abs=1e-6)