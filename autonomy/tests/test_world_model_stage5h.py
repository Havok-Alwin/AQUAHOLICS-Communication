from interfaces.messages import (
    BoatState,
    Interpretation,
    InterpretationReason,
    MarkerType,
)

from decision.world_model import WorldModel
from decision.world_model_models import WorldObjectStatus


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
        speed_mps=1.0,
        position_sigma_m=0.5,
        valid=True,
    )


def interpretation(
    timestamp_s,
    marker_type,
    range_m=10.0,
    bearing_deg=0.0,
    track_id=1,
    marker_confidence=0.9,
    object_confidence=0.9,
):
    return Interpretation(
        timestamp_s=timestamp_s,
        track_id=track_id,
        marker_type=marker_type,
        marker_confidence=marker_confidence,
        object_confidence=object_confidence,
        reason=(
            InterpretationReason.OK
            if marker_type != MarkerType.UNKNOWN
            else InterpretationReason.UNDEFINED_PATTERN
        ),
        range_m=range_m,
        bearing_deg=bearing_deg,
        range_sigma_m=0.3,
        bearing_sigma_deg=1.0,
    )


def test_full_confirm_stale_reconfirm_lifecycle():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                MarkerType.RED,
            )
        ],
    )

    assert len(snapshot.objects) == 1

    object_id = snapshot.objects[0].object_id

    assert (
        snapshot.objects[0].status
        == WorldObjectStatus.TENTATIVE
    )

    snapshot = model.update_interpretations(
        boat(1.0),
        [
            interpretation(
                1.0,
                MarkerType.RED,
            )
        ],
    )

    assert len(snapshot.objects) == 1
    assert snapshot.objects[0].object_id == object_id

    assert (
        snapshot.objects[0].status
        == WorldObjectStatus.TENTATIVE
    )

    snapshot = model.update_interpretations(
        boat(2.0),
        [
            interpretation(
                2.0,
                MarkerType.RED,
            )
        ],
    )

    assert len(snapshot.objects) == 1
    assert snapshot.objects[0].object_id == object_id

    assert (
        snapshot.objects[0].status
        == WorldObjectStatus.CONFIRMED
    )

    snapshot = model.update_interpretations(
        boat(5.0),
        [],
    )

    assert len(snapshot.objects) == 1
    assert snapshot.objects[0].object_id == object_id

    assert (
        snapshot.objects[0].status
        == WorldObjectStatus.STALE
    )

    snapshot = model.update_interpretations(
        boat(6.0),
        [
            interpretation(
                6.0,
                MarkerType.RED,
            )
        ],
    )

    assert len(snapshot.objects) == 1
    assert snapshot.objects[0].object_id == object_id

    assert (
        snapshot.objects[0].status
        == WorldObjectStatus.CONFIRMED
    )


def test_query_api_returns_correct_marker_groups():
    model = WorldModel()

    # Keep observing the RED buoy so it remains confirmed.
    for timestamp in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0):
        model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.RED,
                    range_m=10.0,
                    bearing_deg=-20.0,
                    track_id=1,
                )
            ],
        )

    # Create the GREEN buoy while continuing to observe RED.
    for timestamp in (6.0, 7.0, 8.0):
        model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.RED,
                    range_m=10.0,
                    bearing_deg=-20.0,
                    track_id=1,
                ),
                interpretation(
                    timestamp,
                    MarkerType.GREEN,
                    range_m=15.0,
                    bearing_deg=20.0,
                    track_id=2,
                ),
            ],
        )

    confirmed = model.get_confirmed_objects()

    assert len(confirmed) == 2

    red_objects = (
        model.get_confirmed_objects_by_type(
            MarkerType.RED
        )
    )

    green_objects = (
        model.get_confirmed_objects_by_type(
            MarkerType.GREEN
        )
    )

    assert len(red_objects) == 1
    assert len(green_objects) == 1

    assert (
        red_objects[0].marker_type
        == MarkerType.RED
    )

    assert (
        green_objects[0].marker_type
        == MarkerType.GREEN
    )


def test_nearest_object_query_is_deterministic():
    model = WorldModel()

    # RED buoy at 10 m.
    for timestamp in (0.0, 1.0, 2.0):
        model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.RED,
                    range_m=10.0,
                    bearing_deg=0.0,
                    track_id=1,
                )
            ],
        )

    # Continue observing RED while creating GREEN.
    for timestamp in (3.0, 4.0, 5.0):
        model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.RED,
                    range_m=10.0,
                    bearing_deg=0.0,
                    track_id=1,
                ),
                interpretation(
                    timestamp,
                    MarkerType.GREEN,
                    range_m=20.0,
                    bearing_deg=0.0,
                    track_id=2,
                ),
            ],
        )

    nearest = model.get_nearest_object(
        x_m=0.0,
        y_m=0.0,
        confirmed_only=True,
    )

    assert nearest is not None

    assert (
        nearest.marker_type
        == MarkerType.RED
    )


def test_stale_object_is_not_returned_as_confirmed():
    model = WorldModel()

    for timestamp in (0.0, 1.0, 2.0):
        model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.RED,
                )
            ],
        )

    snapshot = model.update_interpretations(
        boat(5.0),
        [],
    )

    assert (
        snapshot.objects[0].status
        == WorldObjectStatus.STALE
    )

    confirmed = model.get_confirmed_objects()

    assert confirmed == ()

    confirmed_red = (
        model.get_confirmed_objects_by_type(
            MarkerType.RED
        )
    )

    assert confirmed_red == ()


def test_world_reset_clears_all_navigation_state():
    model = WorldModel()

    for timestamp in (0.0, 1.0, 2.0):
        model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.RED,
                )
            ],
        )

    assert len(model.get_objects()) == 1

    model.reset()

    assert model.get_objects() == ()
    assert model.get_confirmed_objects() == ()

    assert (
        model.get_confirmed_objects_by_type(
            MarkerType.RED
        )
        == ()
    )

    assert (
        model.get_nearest_object(
            0.0,
            0.0,
        )
        is None
    )


def test_multiple_objects_keep_stable_ids():
    model = WorldModel()

    model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                MarkerType.RED,
                range_m=10.0,
                bearing_deg=-30.0,
                track_id=10,
            )
        ],
    )

    model.update_interpretations(
        boat(1.0),
        [
            interpretation(
                1.0,
                MarkerType.GREEN,
                range_m=10.0,
                bearing_deg=30.0,
                track_id=20,
            )
        ],
    )

    objects = model.get_objects()

    assert len(objects) == 2

    first_ids = {
        obj.marker_type: obj.object_id
        for obj in objects
    }

    model.update_interpretations(
        boat(2.0),
        [
            interpretation(
                2.0,
                MarkerType.RED,
                range_m=10.0,
                bearing_deg=-30.0,
                track_id=100,
            ),
            interpretation(
                2.0,
                MarkerType.GREEN,
                range_m=10.0,
                bearing_deg=30.0,
                track_id=200,
            ),
        ],
    )

    objects = model.get_objects()

    second_ids = {
        obj.marker_type: obj.object_id
        for obj in objects
    }

    assert first_ids == second_ids