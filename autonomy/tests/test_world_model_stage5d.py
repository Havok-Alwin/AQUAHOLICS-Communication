import math

import pytest

from decision.world_model import WorldModel
from interfaces.messages import BoatState


def make_boat(timestamp_s):
    return BoatState(
        timestamp_s=timestamp_s,
        x_m=0.0,
        y_m=0.0,
        heading_deg=0.0,
        heading_sigma_deg=1.0,
        speed_mps=0.0,
        position_sigma_m=0.5,
        valid=True,
    )


def test_first_timestamp_is_accepted():
    model = WorldModel()

    snapshot = model.snapshot(0.0, make_boat(0.0))

    assert snapshot.timestamp_s == 0.0
    assert snapshot.revision == 0


def test_newer_timestamp_is_accepted():
    model = WorldModel()

    model.update(make_boat(1.0), [], timestamp_s=1.0)
    snapshot = model.update(
        make_boat(2.0),
        [],
        timestamp_s=2.0,
    )

    assert snapshot.timestamp_s == 2.0
    assert snapshot.revision == 2


def test_equal_timestamp_is_accepted():
    model = WorldModel()

    model.update(make_boat(1.0), [], timestamp_s=1.0)

    snapshot = model.update(
        make_boat(1.0),
        [],
        timestamp_s=1.0,
    )

    assert snapshot.timestamp_s == 1.0
    assert snapshot.revision == 2


def test_older_timestamp_is_rejected():
    model = WorldModel()

    model.update(make_boat(2.0), [], timestamp_s=2.0)

    with pytest.raises(ValueError, match="chronological"):
        model.update(
            make_boat(1.0),
            [],
            timestamp_s=1.0,
        )


@pytest.mark.parametrize(
    "timestamp_s",
    [
        math.nan,
        math.inf,
        -math.inf,
    ],
)
def test_nonfinite_timestamp_is_rejected(timestamp_s):
    model = WorldModel()

    with pytest.raises(ValueError, match="finite"):
        model.update(
            make_boat(timestamp_s),
            [],
            timestamp_s=timestamp_s,
        )


def test_rejected_old_update_does_not_change_world_model():
    model = WorldModel()

    first = model.update(
        make_boat(10.0),
        [],
        timestamp_s=10.0,
    )

    with pytest.raises(ValueError, match="chronological"):
        model.update(
            make_boat(5.0),
            [],
            timestamp_s=5.0,
        )

    second = model.snapshot(
        10.0,
        make_boat(10.0),
    )

    assert second == first