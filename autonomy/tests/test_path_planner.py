
import pytest

from decision.mission_manager import (
    MissionDecision,
    MissionObjective,
    MissionState,
)
from decision.path_planner import (
    PathPlanStatus,
    PathPlanner,
    PathPlannerSettings,
)
from decision.world_model_models import (
    WorldObject,
    WorldObjectStatus,
    WorldSnapshot,
)
from interfaces.messages import BoatState, MarkerType


def make_boat(x=0.0, y=0.0, valid=True):
    return BoatState(
        timestamp_s=1.0,
        x_m=x,
        y_m=y,
        heading_deg=0.0,
        heading_sigma_deg=1.0,
        speed_mps=0.0,
        position_sigma_m=0.2,
        valid=valid,
    )


def make_object(object_id, marker_type, x=10.0, y=0.0,
                status=WorldObjectStatus.CONFIRMED):
    return WorldObject(
        object_id=object_id,
        marker_type=marker_type,
        x_m=x,
        y_m=y,
        position_sigma_m=0.2,
        existence_confidence=0.95,
        classification_confidence=0.95,
        first_seen_s=0.0,
        last_seen_s=1.0,
        observation_count=3,
        status=status,
        last_track_id=object_id,
    )


def make_snapshot(objects=(), boat=None):
    return WorldSnapshot(
        timestamp_s=1.0,
        boat=boat or make_boat(),
        objects=tuple(objects),
        revision=1,
    )


def make_decision(objective, target_id=None):
    state_by_objective = {
        MissionObjective.WAIT_FOR_START: MissionState.WAIT_FOR_START,
        MissionObjective.LOCATE_ENTRY: MissionState.FIND_ENTRY,
        MissionObjective.CIRCLE_ENTRY_CLOCKWISE: MissionState.CIRCLE_ENTRY_CW,
        MissionObjective.TRANSIT_FIELD_TO_EXIT: MissionState.TRANSIT_FIELD,
        MissionObjective.CIRCLE_EXIT_COUNTERCLOCKWISE:
            MissionState.CIRCLE_EXIT_CCW,
        MissionObjective.TASK_COMPLETE: MissionState.DONE,
    }
    return MissionDecision(
        state=state_by_objective[objective],
        objective=objective,
        target_object_id=target_id,
        reason="test",
    )


def test_waits_when_mission_has_no_navigation_target():
    planner = PathPlanner()
    result = planner.plan(
        make_snapshot(),
        make_decision(MissionObjective.LOCATE_ENTRY),
    )
    assert result.status is PathPlanStatus.WAITING
    assert result.waypoints == ()


def test_missing_snapshot_is_blocked():
    result = PathPlanner().plan(
        None,
        make_decision(MissionObjective.CIRCLE_ENTRY_CLOCKWISE, 1),
    )
    assert result.status is PathPlanStatus.BLOCKED
    assert result.waypoints == ()


def test_invalid_boat_state_is_blocked():
    result = PathPlanner().plan(
        make_snapshot(boat=make_boat(valid=False)),
        make_decision(MissionObjective.CIRCLE_ENTRY_CLOCKWISE, 1),
    )
    assert result.status is PathPlanStatus.BLOCKED


def test_entry_circle_generates_clockwise_waypoints():
    entry = make_object(1, MarkerType.ENTRY, x=0.0, y=0.0)
    planner = PathPlanner(PathPlannerSettings(2.0, 8))
    result = planner.plan(
        make_snapshot([entry], make_boat(x=3.0, y=0.0)),
        make_decision(MissionObjective.CIRCLE_ENTRY_CLOCKWISE, 1),
    )

    assert result.status is PathPlanStatus.READY
    assert result.target_object_id == 1
    assert len(result.waypoints) == 8

    first = result.waypoints[0]
    # Starting east of the target, clockwise motion initially moves south.
    assert first.x_m == pytest.approx(2.0 * 0.70710678)
    assert first.y_m < 0.0


def test_exit_circle_generates_counterclockwise_waypoints():
    exit_buoy = make_object(2, MarkerType.EXIT, x=0.0, y=0.0)
    planner = PathPlanner(PathPlannerSettings(2.0, 8))
    result = planner.plan(
        make_snapshot([exit_buoy], make_boat(x=3.0, y=0.0)),
        make_decision(MissionObjective.CIRCLE_EXIT_COUNTERCLOCKWISE, 2),
    )

    assert result.status is PathPlanStatus.READY
    assert len(result.waypoints) == 8
    assert result.waypoints[0].y_m > 0.0


def test_rejects_missing_target():
    result = PathPlanner().plan(
        make_snapshot(),
        make_decision(MissionObjective.CIRCLE_ENTRY_CLOCKWISE, 99),
    )
    assert result.status is PathPlanStatus.BLOCKED


def test_rejects_wrong_marker_type():
    red_buoy = make_object(1, MarkerType.RED)
    result = PathPlanner().plan(
        make_snapshot([red_buoy]),
        make_decision(MissionObjective.CIRCLE_ENTRY_CLOCKWISE, 1),
    )
    assert result.status is PathPlanStatus.BLOCKED


def test_rejects_unconfirmed_target():
    entry = make_object(
        1, MarkerType.ENTRY, status=WorldObjectStatus.TENTATIVE
    )
    result = PathPlanner().plan(
        make_snapshot([entry]),
        make_decision(MissionObjective.CIRCLE_ENTRY_CLOCKWISE, 1),
    )
    assert result.status is PathPlanStatus.BLOCKED



def test_transit_is_blocked_when_exit_target_id_is_missing():
    result = PathPlanner().plan(
        make_snapshot(),
        make_decision(MissionObjective.TRANSIT_FIELD_TO_EXIT),
    )

    assert result.status is PathPlanStatus.BLOCKED
    assert "EXIT target ID" in result.reason 

def test_direct_transit_succeeds_when_route_is_clear():
    exit_buoy = make_object(10, MarkerType.EXIT, x=10.0, y=0.0)

    result = PathPlanner().plan(
        make_snapshot([exit_buoy]),
        make_decision(MissionObjective.TRANSIT_FIELD_TO_EXIT, 10),
    )

    assert result.status is PathPlanStatus.READY
    assert result.target_object_id == 10
    assert len(result.waypoints) == 1
    assert result.waypoints[0].x_m == pytest.approx(10.0)
    assert result.waypoints[0].y_m == pytest.approx(0.0)


def test_direct_transit_blocks_red_buoy_on_port_side():
    exit_buoy = make_object(10, MarkerType.EXIT, x=10.0, y=0.0)
    red_buoy = make_object(1, MarkerType.RED, x=5.0, y=2.0)

    result = PathPlanner().plan(
        make_snapshot([exit_buoy, red_buoy]),
        make_decision(MissionObjective.TRANSIT_FIELD_TO_EXIT, 10),
    )

    assert result.status is PathPlanStatus.BLOCKED
    assert "RED" in result.reason
    assert "starboard" in result.reason


def test_direct_transit_blocks_green_buoy_on_starboard_side():
    exit_buoy = make_object(10, MarkerType.EXIT, x=10.0, y=0.0)
    green_buoy = make_object(2, MarkerType.GREEN, x=5.0, y=-2.0)

    result = PathPlanner().plan(
        make_snapshot([exit_buoy, green_buoy]),
        make_decision(MissionObjective.TRANSIT_FIELD_TO_EXIT, 10),
    )

    assert result.status is PathPlanStatus.BLOCKED
    assert "GREEN" in result.reason
    assert "port" in result.reason


def test_direct_transit_blocks_nearby_inactive_obstacle():
    exit_buoy = make_object(10, MarkerType.EXIT, x=10.0, y=0.0)
    obstacle = make_object(3, MarkerType.OBSTACLE, x=5.0, y=1.0)

    result = PathPlanner().plan(
        make_snapshot([exit_buoy, obstacle]),
        make_decision(MissionObjective.TRANSIT_FIELD_TO_EXIT, 10),
    )

    assert result.status is PathPlanStatus.BLOCKED
    assert "obstacle" in result.reason


def test_direct_transit_blocks_unconfirmed_exit():
    exit_buoy = make_object(
        10,
        MarkerType.EXIT,
        x=10.0,
        y=0.0,
        status=WorldObjectStatus.TENTATIVE,
    )

    result = PathPlanner().plan(
        make_snapshot([exit_buoy]),
        make_decision(MissionObjective.TRANSIT_FIELD_TO_EXIT, 10),
    )

    assert result.status is PathPlanStatus.BLOCKED
    assert "Confirmed EXIT" in result.reason
    
def test_completed_mission_has_no_waypoints():
    result = PathPlanner().plan(
        make_snapshot(),
        make_decision(MissionObjective.TASK_COMPLETE),
    )
    assert result.status is PathPlanStatus.READY
    assert result.waypoints == ()


def test_invalid_circle_settings_are_rejected():
    with pytest.raises(ValueError):
        PathPlannerSettings(circle_radius_m=0.0)

    with pytest.raises(ValueError):
        PathPlannerSettings(circle_waypoint_count=3)