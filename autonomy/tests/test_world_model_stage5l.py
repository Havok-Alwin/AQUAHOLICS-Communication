from interfaces.messages import (
    BoatState,
    Interpretation,
    InterpretationReason,
    MarkerType,
)

from decision.world_model import WorldModel
from decision.world_model_models import WorldObjectStatus


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


def interpretation(timestamp_s):
    return Interpretation(
        timestamp_s=timestamp_s,
        track_id=1,
        marker_type=MarkerType.RED,
        marker_confidence=0.9,
        object_confidence=0.9,
        reason=InterpretationReason.OK,
        range_m=10.0,
        bearing_deg=0.0,
        range_sigma_m=0.2,
        bearing_sigma_deg=1.0,
    )


def test_stale_object_remains_stale_without_new_observation():
    model = WorldModel()

    for timestamp in (0.0, 1.0, 2.0):
        model.update_interpretations(
            boat(timestamp),
            [interpretation(timestamp)],
        )

    snapshot = model.update_interpretations(
        boat(6.0),
        [],
    )

    assert snapshot.objects[0].status == WorldObjectStatus.STALE

    snapshot = model.update_interpretations(
        boat(7.0),
        [],
    )

    assert snapshot.objects[0].status == WorldObjectStatus.STALE

    snapshot = model.update_interpretations(
        boat(30.0),
        [],
    )

    assert snapshot.objects[0].status == WorldObjectStatus.STALE