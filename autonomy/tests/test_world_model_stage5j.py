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
    valid=True,
):
    return BoatState(
        timestamp_s=timestamp_s,
        x_m=x_m,
        y_m=y_m,
        heading_deg=heading_deg,
        heading_sigma_deg=1.0,
        speed_mps=0.0,
        position_sigma_m=0.1,
        valid=valid,
    )


def interpretation(
    timestamp_s,
    range_m=10.0,
    bearing_deg=0.0,
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


def test_first_update_accepts_zero_timestamp():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(0.0),
        [interpretation(0.0)],
    )

    assert snapshot.timestamp_s == 0.0
    assert len(snapshot.objects) == 1


def test_out_of_order_timestamp_is_rejected():
    model = WorldModel()

    model.update_interpretations(
        boat(10.0),
        [interpretation(10.0)],
    )

    with pytest.raises(ValueError):
        model.update_interpretations(
            boat(9.0),
            [interpretation(9.0)],
        )


def test_equal_timestamp_is_allowed():
    model = WorldModel()

    first = model.update_interpretations(
        boat(10.0),
        [interpretation(10.0)],
    )

    second = model.update_interpretations(
        boat(10.0),
        [interpretation(10.0)],
    )

    assert second.timestamp_s == 10.0
    assert second.revision == first.revision + 1
    assert len(second.objects) == 1


def test_timestamp_protection_survives_empty_update():
    model = WorldModel()

    model.update_interpretations(
        boat(10.0),
        [interpretation(10.0)],
    )

    with pytest.raises(ValueError):
        model.update_interpretations(
            boat(9.0),
            [],
        )


def test_invalid_boat_state_is_rejected():
    model = WorldModel()

    with pytest.raises(ValueError):
        model.update_interpretations(
            boat(0.0, valid=False),
            [interpretation(0.0)],
        )


def test_nonfinite_boat_timestamp_is_rejected():
    model = WorldModel()

    with pytest.raises(ValueError):
        model.update_interpretations(
            boat(float("nan")),
            [interpretation(float("nan"))],
        )


def test_nonfinite_boat_position_is_rejected():
    model = WorldModel()

    with pytest.raises(ValueError):
        model.update_interpretations(
            boat(0.0, x_m=float("nan")),
            [interpretation(0.0)],
        )


def test_nonfinite_boat_heading_is_rejected():
    model = WorldModel()

    with pytest.raises(ValueError):
        model.update_interpretations(
            boat(0.0, heading_deg=float("inf")),
            [interpretation(0.0)],
        )


def test_reset_allows_new_timestamp_sequence():
    model = WorldModel()

    model.update_interpretations(
        boat(100.0),
        [interpretation(100.0)],
    )

    model.reset()

    snapshot = model.update_interpretations(
        boat(0.0),
        [interpretation(0.0)],
    )

    assert snapshot.timestamp_s == 0.0
    assert snapshot.revision == 1
    assert len(snapshot.objects) == 1


def test_reset_removes_previous_objects():
    model = WorldModel()

    model.update_interpretations(
        boat(0.0),
        [interpretation(0.0)],
    )

    model.reset()

    snapshot = model.update_interpretations(
        boat(0.0),
        [],
    )

    assert snapshot.objects == ()