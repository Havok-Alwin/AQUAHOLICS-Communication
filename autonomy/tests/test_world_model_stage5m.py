from interfaces.messages import (
    BoatState,
    Interpretation,
    InterpretationReason,
    MarkerType,
)

from decision.world_model import WorldModel


def boat(timestamp_s):
    return BoatState(
        timestamp_s=timestamp_s,
        x_m=0.0,
        y_m=0.0,
        heading_deg=0.0,
        heading_sigma_deg=1.0,
        speed_mps=0.0,
        position_sigma_m=0.1,
        valid=True,
    )


def interpretation(
    timestamp_s,
    marker_type,
    confidence=0.9,
    track_id=1,
):
    return Interpretation(
        timestamp_s=timestamp_s,
        track_id=track_id,
        marker_type=marker_type,
        marker_confidence=confidence,
        object_confidence=0.9,
        reason=InterpretationReason.OK,
        range_m=10.0,
        bearing_deg=0.0,
        range_sigma_m=0.2,
        bearing_sigma_deg=1.0,
    )


def test_repeated_wrong_labels_do_not_lock_classification_forever():
    model = WorldModel()

    snapshot = None

    # Establish RED with high confidence.
    for timestamp in (0.0, 1.0, 2.0):
        snapshot = model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.RED,
                    confidence=0.9,
                )
            ],
        )

    # Repeated GREEN observations should provide enough evidence
    # to challenge an incorrect early RED classification.
    for timestamp in (3.0, 4.0, 5.0):
        snapshot = model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.GREEN,
                    confidence=0.9,
                )
            ],
        )

    obj = snapshot.objects[0]

    assert obj.marker_type == MarkerType.GREEN


def test_single_challenger_does_not_flip_a_stable_classification():
    model = WorldModel()

    snapshot = None

    for timestamp in (0.0, 1.0, 2.0, 3.0):
        snapshot = model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.RED,
                    confidence=0.9,
                )
            ],
        )

    snapshot = model.update_interpretations(
        boat(4.0),
        [
            interpretation(
                4.0,
                MarkerType.GREEN,
                confidence=0.9,
            )
        ],
    )

    obj = snapshot.objects[0]

    assert obj.marker_type == MarkerType.RED


def test_obstacle_does_not_become_colored_from_one_observation():
    model = WorldModel()

    snapshot = None

    for timestamp in (0.0, 1.0, 2.0):
        snapshot = model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.OBSTACLE,
                    confidence=0.9,
                )
            ],
        )

    snapshot = model.update_interpretations(
        boat(3.0),
        [
            interpretation(
                3.0,
                MarkerType.RED,
                confidence=0.9,
            )
        ],
    )

    obj = snapshot.objects[0]

    assert obj.marker_type == MarkerType.OBSTACLE


def test_unknown_observation_does_not_erase_classification():
    model = WorldModel()

    snapshot = None

    for timestamp in (0.0, 1.0, 2.0):
        snapshot = model.update_interpretations(
            boat(timestamp),
            [
                interpretation(
                    timestamp,
                    MarkerType.RED,
                    confidence=0.9,
                )
            ],
        )

    snapshot = model.update_interpretations(
        boat(3.0),
        [
            interpretation(
                3.0,
                MarkerType.UNKNOWN,
                confidence=0.9,
            )
        ],
    )

    obj = snapshot.objects[0]

    assert obj.marker_type == MarkerType.RED