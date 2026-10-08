from decision.world_model import WorldModel
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


def make_detection(
    timestamp_s=0.0,
    track_id=1,
    range_m=10.0,
    bearing_deg=0.0,
):
    return Detection(
        timestamp_s=timestamp_s,
        track_id=track_id,
        object_class=ObjectClass.TASK1_BUOY,
        light_color=LightColor.RED,
        light_state=LightState.FLASHING,
        range_m=range_m,
        bearing_deg=bearing_deg,
        range_sigma_m=0.2,
        bearing_sigma_deg=1.0,
        detection_confidence=0.9,
        light_confidence=0.9,
    )


def test_repeated_detection_updates_existing_object():
    model = WorldModel()

    first = model.update(
        make_boat(0.0),
        [make_detection(0.0)],
        timestamp_s=0.0,
    )

    second = model.update(
        make_boat(1.0),
        [make_detection(1.0, range_m=10.1)],
        timestamp_s=1.0,
    )

    assert len(first.objects) == 1
    assert len(second.objects) == 1
    assert second.objects[0].object_id == first.objects[0].object_id
    assert second.objects[0].observation_count == 2


def test_distant_detections_create_separate_objects():
    model = WorldModel()

    snapshot = model.update(
        make_boat(0.0),
        [
            make_detection(0.0, track_id=1, range_m=10.0),
            make_detection(0.0, track_id=2, range_m=20.0),
        ],
        timestamp_s=0.0,
    )

    assert len(snapshot.objects) == 2
    assert snapshot.objects[0].object_id != snapshot.objects[1].object_id


def test_association_is_independent_of_track_id():
    model = WorldModel()

    first = model.update(
        make_boat(0.0),
        [make_detection(0.0, track_id=100, range_m=10.0)],
        timestamp_s=0.0,
    )

    second = model.update(
        make_boat(1.0),
        [make_detection(1.0, track_id=999, range_m=10.1)],
        timestamp_s=1.0,
    )

    assert len(second.objects) == 1
    assert second.objects[0].object_id == first.objects[0].object_id
    assert second.objects[0].last_track_id == 999


def test_multiple_distinct_objects_remain_distinct():
    model = WorldModel()

    first = model.update(
        make_boat(0.0),
        [
            make_detection(0.0, track_id=1, range_m=10.0),
            make_detection(0.0, track_id=2, range_m=15.0),
        ],
        timestamp_s=0.0,
    )

    second = model.update(
        make_boat(1.0),
        [
            make_detection(1.0, track_id=20, range_m=10.1),
            make_detection(1.0, track_id=30, range_m=15.1),
        ],
        timestamp_s=1.0,
    )

    assert len(first.objects) == 2
    assert len(second.objects) == 2

    assert {
        obj.object_id for obj in first.objects
    } == {
        obj.object_id for obj in second.objects
    }


def test_object_ids_are_created_deterministically():
    model = WorldModel()

    snapshot = model.update(
        make_boat(0.0),
        [
            make_detection(0.0, track_id=1, range_m=10.0),
            make_detection(0.0, track_id=2, range_m=20.0),
        ],
        timestamp_s=0.0,
    )

    assert [obj.object_id for obj in snapshot.objects] == [1, 2]


def test_repeated_updates_preserve_deterministic_object_identity():
    model = WorldModel()

    first = model.update(
        make_boat(0.0),
        [
            make_detection(0.0, track_id=10, range_m=10.0),
            make_detection(0.0, track_id=20, range_m=20.0),
        ],
        timestamp_s=0.0,
    )

    second = model.update(
        make_boat(1.0),
        [
            make_detection(1.0, track_id=200, range_m=20.1),
            make_detection(1.0, track_id=100, range_m=10.1),
        ],
        timestamp_s=1.0,
    )

    first_by_range = {
        round(obj.x_m**2 + obj.y_m**2, 1): obj.object_id
        for obj in first.objects
    }

    second_by_range = {
        round(obj.x_m**2 + obj.y_m**2, 1): obj.object_id
        for obj in second.objects
    }

    assert len(second.objects) == 2
    assert set(first_by_range.values()) == set(second_by_range.values())