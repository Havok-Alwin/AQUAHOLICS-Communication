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
    range_sigma_m=0.2,
    bearing_sigma_deg=1.0,
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
        range_sigma_m=range_sigma_m,
        bearing_sigma_deg=bearing_sigma_deg,
    )


def test_noisy_repeated_observations_converge_to_one_object():
    model = WorldModel()

    observations = [
        (0.0, 10.0, 0.0),
        (1.0, 10.2, 1.0),
        (2.0, 9.8, -1.0),
        (3.0, 10.1, 0.5),
        (4.0, 9.9, -0.5),
    ]

    for timestamp, range_m, bearing_deg in observations:
        snapshot = model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    range_m=range_m,
                    bearing_deg=bearing_deg,
                    track_id=100 + int(timestamp),
                )
            ],
        )

    assert len(snapshot.objects) == 1

    obj = snapshot.objects[0]

    assert obj.observation_count == 5
    assert obj.x_m == pytest.approx(0.0, abs=0.5)
    assert obj.y_m == pytest.approx(10.0, abs=0.5)


def test_track_id_change_does_not_create_new_object():
    model = WorldModel()

    model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=0.0,
                track_id=10,
            )
        ],
    )

    snapshot = model.update_interpretations(
        boat(1.0),
        [
            interpretation(
                1.0,
                range_m=10.1,
                bearing_deg=0.5,
                track_id=999,
            )
        ],
    )

    assert len(snapshot.objects) == 1
    assert snapshot.objects[0].object_id == 1
    assert snapshot.objects[0].last_track_id == 999


def test_distant_objects_do_not_merge():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=-20.0,
                track_id=1,
            ),
            interpretation(
                0.0,
                range_m=20.0,
                bearing_deg=20.0,
                track_id=2,
            ),
        ],
    )

    assert len(snapshot.objects) == 2
    assert snapshot.objects[0].object_id != snapshot.objects[1].object_id


def test_multiple_detections_match_one_to_one():
    model = WorldModel()

    first = model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=-20.0,
                track_id=1,
            ),
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=20.0,
                track_id=2,
            ),
        ],
    )

    assert len(first.objects) == 2

    second = model.update_interpretations(
        boat(1.0),
        [
            interpretation(
                1.0,
                range_m=10.1,
                bearing_deg=-20.5,
                track_id=101,
            ),
            interpretation(
                1.0,
                range_m=9.9,
                bearing_deg=19.5,
                track_id=202,
            ),
        ],
    )

    assert len(second.objects) == 2
    assert second.objects[0].observation_count == 2
    assert second.objects[1].observation_count == 2


def test_close_objects_remain_deterministically_associated():
    model = WorldModel()

    first = model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=-5.0,
                track_id=10,
            ),
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=5.0,
                track_id=20,
            ),
        ],
    )

    first_positions = {
        obj.object_id: (obj.x_m, obj.y_m)
        for obj in first.objects
    }

    second = model.update_interpretations(
        boat(1.0),
        [
            interpretation(
                1.0,
                range_m=10.05,
                bearing_deg=4.8,
                track_id=200,
            ),
            interpretation(
                1.0,
                range_m=9.95,
                bearing_deg=-4.8,
                track_id=100,
            ),
        ],
    )

    assert len(second.objects) == 2

    second_positions = {
        obj.object_id: (obj.x_m, obj.y_m)
        for obj in second.objects
    }

    assert set(first_positions) == set(second_positions)


def test_association_does_not_depend_on_marker_track_id():
    model = WorldModel()

    model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=-15.0,
                track_id=1,
            ),
            interpretation(
                0.0,
                range_m=15.0,
                bearing_deg=15.0,
                track_id=2,
            ),
        ],
    )

    snapshot = model.update_interpretations(
        boat(1.0),
        [
            interpretation(
                1.0,
                range_m=10.1,
                bearing_deg=-15.2,
                track_id=500,
            ),
            interpretation(
                1.0,
                range_m=14.9,
                bearing_deg=14.8,
                track_id=600,
            ),
        ],
    )

    assert len(snapshot.objects) == 2
    assert all(obj.observation_count == 2 for obj in snapshot.objects)


def test_new_distant_observation_creates_new_object():
    model = WorldModel()

    model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=0.0,
                track_id=1,
            )
        ],
    )

    snapshot = model.update_interpretations(
        boat(1.0),
        [
            interpretation(
                1.0,
                range_m=10.0,
                bearing_deg=0.0,
                track_id=1,
            ),
            interpretation(
                1.0,
                range_m=30.0,
                bearing_deg=0.0,
                track_id=2,
            ),
        ],
    )

    assert len(snapshot.objects) == 2

    counts = sorted(obj.observation_count for obj in snapshot.objects)

    assert counts == [1, 2]


def test_uncertainty_is_reflected_in_position_sigma():
    model = WorldModel()

    snapshot = model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=0.0,
                range_sigma_m=2.0,
                bearing_sigma_deg=5.0,
            )
        ],
    )

    obj = snapshot.objects[0]

    assert obj.position_sigma_m > 0.0
    assert obj.position_sigma_m >= 0.1


def test_repeated_measurements_reduce_position_uncertainty():
    model = WorldModel()

    first = model.update_interpretations(
        boat(0.0),
        [
            interpretation(
                0.0,
                range_m=10.0,
                bearing_deg=0.0,
                range_sigma_m=1.0,
                bearing_sigma_deg=2.0,
            )
        ],
    )

    first_sigma = first.objects[0].position_sigma_m

    second = model.update_interpretations(
        boat(1.0),
        [
            interpretation(
                1.0,
                range_m=10.0,
                bearing_deg=0.0,
                range_sigma_m=1.0,
                bearing_sigma_deg=2.0,
            )
        ],
    )

    second_sigma = second.objects[0].position_sigma_m

    assert second_sigma < first_sigma


def test_association_is_deterministic_across_repeated_runs():
    def run_once():
        model = WorldModel()

        for timestamp in (0.0, 1.0, 2.0):
            snapshot = model.update_interpretations(
                boat(timestamp),
                [
                    interpretation(
                        timestamp,
                        range_m=10.0,
                        bearing_deg=-10.0,
                        track_id=100 + int(timestamp),
                    ),
                    interpretation(
                        timestamp,
                        range_m=10.0,
                        bearing_deg=10.0,
                        track_id=200 + int(timestamp),
                    ),
                ],
            )

        return tuple(
            (
                obj.object_id,
                obj.marker_type,
                round(obj.x_m, 6),
                round(obj.y_m, 6),
                obj.observation_count,
            )
            for obj in snapshot.objects
        )

    assert run_once() == run_once()