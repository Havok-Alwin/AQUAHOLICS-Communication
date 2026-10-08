import pytest

from decision.world_model import WorldModel
from decision.world_model_models import WorldObjectStatus
from interfaces.messages import (
    BoatState,
    Detection,
    LightColor,
    LightState,
    ObjectClass,
)


def make_boat(timestamp_s=0.0):
    return BoatState(
        timestamp_s=timestamp_s,
        x_m=0.0,
        y_m=0.0,
        heading_deg=0.0,
        heading_sigma_deg=1.0,
        speed_mps=0.0,
        position_sigma_m=0.2,
        valid=True,
    )


def make_detection(timestamp_s=0.0, range_m=10.0):
    return Detection(
        timestamp_s=timestamp_s,
        track_id=1,
        object_class=ObjectClass.TASK1_BUOY,
        light_color=LightColor.RED,
        light_state=LightState.FLASHING,
        range_m=range_m,
        bearing_deg=0.0,
        range_sigma_m=0.2,
        bearing_sigma_deg=1.0,
        detection_confidence=0.9,
        light_confidence=0.9,
    )


def test_new_object_starts_tentative():
    model = WorldModel()

    snapshot = model.update(
        make_boat(0.0),
        [make_detection(0.0)],
        timestamp_s=0.0,
    )

    assert len(snapshot.objects) == 1
    assert snapshot.objects[0].status == WorldObjectStatus.TENTATIVE


def test_object_becomes_confirmed_after_consistent_observations():
    model = WorldModel()

    snapshot_1 = model.update(
        make_boat(0.0),
        [make_detection(0.0)],
        timestamp_s=0.0,
    )

    snapshot_2 = model.update(
        make_boat(1.0),
        [make_detection(1.0, range_m=10.1)],
        timestamp_s=1.0,
    )

    snapshot_3 = model.update(
        make_boat(2.0),
        [make_detection(2.0, range_m=10.2)],
        timestamp_s=2.0,
    )

    assert snapshot_1.objects[0].status == WorldObjectStatus.TENTATIVE
    assert snapshot_2.objects[0].status == WorldObjectStatus.TENTATIVE
    assert snapshot_3.objects[0].status == WorldObjectStatus.CONFIRMED


def test_confirmed_object_becomes_stale_when_not_seen():
    model = WorldModel()

    model.update(
        make_boat(0.0),
        [make_detection(0.0)],
        timestamp_s=0.0,
    )

    model.update(
        make_boat(1.0),
        [make_detection(1.0)],
        timestamp_s=1.0,
    )

    confirmed = model.update(
        make_boat(2.0),
        [make_detection(2.0)],
        timestamp_s=2.0,
    )

    assert confirmed.objects[0].status == WorldObjectStatus.CONFIRMED

    stale = model.update(
        make_boat(6.0),
        [],
        timestamp_s=6.0,
    )

    assert len(stale.objects) == 1
    assert stale.objects[0].object_id == confirmed.objects[0].object_id
    assert stale.objects[0].status == WorldObjectStatus.STALE


def test_stale_object_becomes_confirmed_when_seen_again():
    model = WorldModel()

    model.update(
        make_boat(0.0),
        [make_detection(0.0)],
        timestamp_s=0.0,
    )

    model.update(
        make_boat(1.0),
        [make_detection(1.0)],
        timestamp_s=1.0,
    )

    model.update(
        make_boat(2.0),
        [make_detection(2.0)],
        timestamp_s=2.0,
    )

    stale = model.update(
        make_boat(6.0),
        [],
        timestamp_s=6.0,
    )

    assert stale.objects[0].status == WorldObjectStatus.STALE

    recovered = model.update(
        make_boat(7.0),
        [make_detection(7.0, range_m=10.1)],
        timestamp_s=7.0,
    )

    assert len(recovered.objects) == 1
    assert recovered.objects[0].object_id == stale.objects[0].object_id
    assert recovered.objects[0].status == WorldObjectStatus.CONFIRMED


def test_confirmed_object_is_not_deleted_when_temporarily_unseen():
    model = WorldModel()

    model.update(
        make_boat(0.0),
        [make_detection(0.0)],
        timestamp_s=0.0,
    )

    model.update(
        make_boat(1.0),
        [make_detection(1.0)],
        timestamp_s=1.0,
    )

    confirmed = model.update(
        make_boat(2.0),
        [make_detection(2.0)],
        timestamp_s=2.0,
    )

    missing = model.update(
        make_boat(3.0),
        [],
        timestamp_s=3.0,
    )

    assert len(missing.objects) == 1
    assert missing.objects[0].object_id == confirmed.objects[0].object_id
    assert missing.objects[0].status == WorldObjectStatus.CONFIRMED


def test_tentative_object_can_expire_without_becoming_confirmed():
    model = WorldModel()

    first = model.update(
        make_boat(0.0),
        [make_detection(0.0)],
        timestamp_s=0.0,
    )

    assert first.objects[0].status == WorldObjectStatus.TENTATIVE

    later = model.update(
        make_boat(10.0),
        [],
        timestamp_s=10.0,
    )

    assert len(later.objects) == 0