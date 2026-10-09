
import pytest

from decision.mission_manager import MissionManager, MissionObjective, MissionState
from decision.world_model_models import (
    WorldObject,
    WorldObjectStatus,
    WorldSnapshot,
)
from interfaces.messages import BoatState, MarkerType


def make_boat(valid=True):
    return BoatState(
        timestamp_s=10.0,
        x_m=0.0,
        y_m=0.0,
        heading_deg=0.0,
        heading_sigma_deg=1.0,
        speed_mps=0.0,
        position_sigma_m=0.2,
        valid=valid,
    )


def make_object(
    object_id,
    marker_type,
    *,
    x_m=5.0,
    y_m=0.0,
    status=WorldObjectStatus.CONFIRMED,
    classification_confidence=0.9,
    existence_confidence=0.9,
):
    return WorldObject(
        object_id=object_id,
        marker_type=marker_type,
        x_m=x_m,
        y_m=y_m,
        position_sigma_m=0.2,
        existence_confidence=existence_confidence,
        classification_confidence=classification_confidence,
        first_seen_s=1.0,
        last_seen_s=10.0,
        observation_count=3,
        status=status,
        last_track_id=None,
    )


def make_snapshot(*objects, boat=None):
    return WorldSnapshot(
        timestamp_s=10.0,
        boat=boat or make_boat(),
        objects=tuple(objects),
        revision=1,
    )


def test_manager_starts_in_wait_state():
    manager = MissionManager()

    assert manager.state is MissionState.WAIT_FOR_START


def test_waits_until_start_is_enabled():
    manager = MissionManager()
    snapshot = make_snapshot(make_object(1, MarkerType.ENTRY))

    result = manager.update(snapshot, start_enabled=False)

    assert result.state is MissionState.WAIT_FOR_START
    assert result.objective is MissionObjective.WAIT_FOR_START


def test_start_enabled_moves_to_find_entry():
    manager = MissionManager()

    result = manager.update(make_snapshot(), start_enabled=True)

    assert result.state is MissionState.FIND_ENTRY
    assert result.objective is MissionObjective.LOCATE_ENTRY


def test_invalid_boat_prevents_start():
    manager = MissionManager()
    snapshot = make_snapshot(boat=make_boat(valid=False))

    result = manager.update(snapshot, start_enabled=True)

    assert result.state is MissionState.WAIT_FOR_START


def test_confirmed_entry_selects_clockwise_circle():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)

    result = manager.update(
        make_snapshot(make_object(11, MarkerType.ENTRY))
    )

    assert result.state is MissionState.CIRCLE_ENTRY_CW
    assert result.objective is MissionObjective.CIRCLE_ENTRY_CLOCKWISE
    assert result.target_object_id == 11


def test_unconfirmed_entry_is_not_selected():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)

    snapshot = make_snapshot(
        make_object(
            11,
            MarkerType.ENTRY,
            status=WorldObjectStatus.TENTATIVE,
        )
    )

    result = manager.update(snapshot)

    assert result.state is MissionState.FIND_ENTRY
    assert result.target_object_id is None


def test_entry_circle_requires_completion_signal():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)
    manager.update(make_snapshot(make_object(11, MarkerType.ENTRY)))

    result = manager.update(make_snapshot())

    assert result.state is MissionState.CIRCLE_ENTRY_CW


def test_entry_circle_completion_starts_transit():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)
    manager.update(make_snapshot(make_object(11, MarkerType.ENTRY)))

    result = manager.update(
        make_snapshot(),
        entry_circle_complete=True,
    )

    assert result.state is MissionState.TRANSIT_FIELD
    assert result.objective is MissionObjective.TRANSIT_FIELD_TO_EXIT


def test_transit_requires_completion_signal():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)
    manager.update(make_snapshot(make_object(11, MarkerType.ENTRY)))
    manager.update(make_snapshot(), entry_circle_complete=True)

    result = manager.update(make_snapshot())

    assert result.state is MissionState.TRANSIT_FIELD


def test_transit_completion_moves_to_exit_circle():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)
    manager.update(make_snapshot(make_object(11, MarkerType.ENTRY)))
    manager.update(make_snapshot(), entry_circle_complete=True)

    result = manager.update(
        make_snapshot(make_object(22, MarkerType.EXIT)),
        transit_complete=True,
    )

    assert result.state is MissionState.CIRCLE_EXIT_CCW
    assert result.objective is MissionObjective.CIRCLE_EXIT_COUNTERCLOCKWISE
    assert result.target_object_id == 22


def test_exit_circle_requires_completion_signal():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)
    manager.update(make_snapshot(make_object(11, MarkerType.ENTRY)))
    manager.update(make_snapshot(), entry_circle_complete=True)
    manager.update(make_snapshot(), transit_complete=True)

    result = manager.update(make_snapshot())

    assert result.state is MissionState.CIRCLE_EXIT_CCW


def test_exit_circle_completion_finishes_task():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)
    manager.update(make_snapshot(make_object(11, MarkerType.ENTRY)))
    manager.update(make_snapshot(), entry_circle_complete=True)
    manager.update(make_snapshot(), transit_complete=True)

    result = manager.update(
        make_snapshot(),
        exit_circle_complete=True,
    )

    assert result.state is MissionState.DONE
    assert result.objective is MissionObjective.TASK_COMPLETE


def test_done_state_remains_done():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)
    manager.update(make_snapshot(make_object(11, MarkerType.ENTRY)))
    manager.update(make_snapshot(), entry_circle_complete=True)
    manager.update(make_snapshot(), transit_complete=True)
    manager.update(make_snapshot(), exit_circle_complete=True)

    result = manager.update(make_snapshot())

    assert result.state is MissionState.DONE


def test_missing_snapshot_does_not_advance_state():
    manager = MissionManager()
    manager.update(make_snapshot(), start_enabled=True)

    result = manager.update(None, entry_circle_complete=True)

    assert result.state is MissionState.FIND_ENTRY


def test_invalid_confidence_threshold_is_rejected():
    with pytest.raises(ValueError):
        MissionManager(min_marker_confidence=1.5)

