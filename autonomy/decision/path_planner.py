
"""Waypoint planning for RobotX 2026 Task 1.

Creates candidate waypoints and checks basic geometric constraints.
This module does not issue motor commands or certify real-world safety.
"""

from dataclasses import dataclass
from enum import Enum
from math import atan2, cos, hypot, isfinite, pi, sin

from decision.mission_manager import MissionDecision, MissionObjective
from decision.world_model_models import WorldObjectStatus, WorldSnapshot
from interfaces.messages import MarkerType


class PathPlanStatus(Enum):
    READY = "READY"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Waypoint:
    """A waypoint in the world map frame: x east, y north."""

    x_m: float
    y_m: float


@dataclass(frozen=True)
class PathPlan:
    status: PathPlanStatus
    waypoints: tuple[Waypoint, ...]
    target_object_id: int | None
    reason: str


@dataclass(frozen=True)
class PathPlannerSettings:
    """Initial simulation settings, not official RobotX specifications."""

    circle_radius_m: float = 5.0
    circle_waypoint_count: int = 12
    transit_clearance_m: float = 3.0
    obstacle_clearance_m: float = 4.0

    def __post_init__(self) -> None:
        positive_values = {
            "circle_radius_m": self.circle_radius_m,
            "transit_clearance_m": self.transit_clearance_m,
            "obstacle_clearance_m": self.obstacle_clearance_m,
        }

        for name, value in positive_values.items():
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and greater than zero")

        if self.circle_waypoint_count < 4:
            raise ValueError("circle_waypoint_count must be at least 4")


class PathPlanner:
    """Creates mission waypoints; does not control the boat."""

    def __init__(
        self,
        settings: PathPlannerSettings | None = None,
    ) -> None:
        self.settings = settings or PathPlannerSettings()

    def plan(
        self,
        snapshot: WorldSnapshot | None,
        decision: MissionDecision,
    ) -> PathPlan:
        if snapshot is None:
            return self._blocked("No world snapshot is available.")

        if not snapshot.boat.valid:
            return self._blocked("Boat state is invalid.")

        if decision.objective in (
            MissionObjective.WAIT_FOR_START,
            MissionObjective.LOCATE_ENTRY,
        ):
            return PathPlan(
                status=PathPlanStatus.WAITING,
                waypoints=(),
                target_object_id=None,
                reason="Waiting for a mission target before planning a path.",
            )

        if decision.objective is MissionObjective.CIRCLE_ENTRY_CLOCKWISE:
            return self._circle_target(
                snapshot,
                decision.target_object_id,
                MarkerType.ENTRY,
                clockwise=True,
            )

        if (
            decision.objective
            is MissionObjective.CIRCLE_EXIT_COUNTERCLOCKWISE
        ):
            return self._circle_target(
                snapshot,
                decision.target_object_id,
                MarkerType.EXIT,
                clockwise=False,
            )

        if decision.objective is MissionObjective.TRANSIT_FIELD_TO_EXIT:
            return self._plan_direct_transit(
                snapshot,
                decision.target_object_id,
            )

        if decision.objective is MissionObjective.TASK_COMPLETE:
            return PathPlan(
                status=PathPlanStatus.READY,
                waypoints=(),
                target_object_id=None,
                reason="Mission complete; no further waypoints required.",
            )

        return self._blocked("Unsupported mission objective.")

    def _circle_target(
        self,
        snapshot: WorldSnapshot,
        target_object_id: int | None,
        required_type: MarkerType,
        *,
        clockwise: bool,
    ) -> PathPlan:
        if target_object_id is None:
            return self._blocked(
                f"No target ID supplied for {required_type.value} circling."
            )

        target = next(
            (
                obj
                for obj in snapshot.objects
                if obj.object_id == target_object_id
                and obj.marker_type is required_type
                and obj.status is WorldObjectStatus.CONFIRMED
            ),
            None,
        )

        if target is None:
            return self._blocked(
                f"Confirmed {required_type.value} target "
                f"{target_object_id} is unavailable."
            )

        boat = snapshot.boat
        dx = boat.x_m - target.x_m
        dy = boat.y_m - target.y_m

        if hypot(dx, dy) < 1e-6:
            return self._blocked(
                "Boat position coincides with the target; "
                "cannot determine the circle's starting direction."
            )

        start_angle = atan2(dy, dx)
        direction = -1.0 if clockwise else 1.0
        radius = self.settings.circle_radius_m
        count = self.settings.circle_waypoint_count

        waypoints = tuple(
            Waypoint(
                x_m=target.x_m
                + radius
                * cos(start_angle + direction * 2.0 * pi * i / count),
                y_m=target.y_m
                + radius
                * sin(start_angle + direction * 2.0 * pi * i / count),
            )
            for i in range(1, count + 1)
        )

        direction_name = (
            "clockwise" if clockwise else "counterclockwise"
        )

        return PathPlan(
            status=PathPlanStatus.READY,
            waypoints=waypoints,
            target_object_id=target.object_id,
            reason=(
                f"Generated {direction_name} circle waypoints around "
                f"{required_type.value}. Radius is a simulation setting; "
                "clearance and collision safety are not yet verified."
            ),
        )

    def _plan_direct_transit(
        self,
        snapshot: WorldSnapshot,
        target_object_id: int | None,
    ) -> PathPlan:
        """Validate a direct boat-to-EXIT segment.

        This first transit version does not search for detours. If the direct
        segment violates a passing-side or clearance constraint, it blocks.
        """

        if target_object_id is None:
            return self._blocked("No EXIT target ID supplied for transit.")

        target = next(
            (
                obj
                for obj in snapshot.objects
                if obj.object_id == target_object_id
                and obj.marker_type is MarkerType.EXIT
                and obj.status is WorldObjectStatus.CONFIRMED
            ),
            None,
        )

        if target is None:
            return self._blocked(
                f"Confirmed EXIT target {target_object_id} is unavailable."
            )

        boat = snapshot.boat
        start_x, start_y = boat.x_m, boat.y_m
        end_x, end_y = target.x_m, target.y_m
        dx, dy = end_x - start_x, end_y - start_y
        segment_length = hypot(dx, dy)

        if segment_length < 1e-6:
            return self._blocked(
                "Boat is already at the EXIT position; "
                "direct transit cannot be planned."
            )

        # Check every confirmed object except the EXIT target itself.
        for obj in snapshot.objects:
            if (
                obj.object_id == target.object_id
                or obj.status is not WorldObjectStatus.CONFIRMED
            ):
                continue

            # Signed cross product relative to the direction of travel:
            # positive = port/left; negative = starboard/right.
            rel_x = obj.x_m - start_x
            rel_y = obj.y_m - start_y
            cross = dx * rel_y - dy * rel_x

            # Distance from the object to the finite boat-to-EXIT segment.
            t = (rel_x * dx + rel_y * dy) / (segment_length ** 2)
            t_clamped = max(0.0, min(1.0, t))
            closest_x = start_x + t_clamped * dx
            closest_y = start_y + t_clamped * dy
            distance = hypot(obj.x_m - closest_x, obj.y_m - closest_y)

            if obj.marker_type is MarkerType.RED and cross >= 0:
                return self._blocked(
                    f"Direct route violates RED buoy {obj.object_id} "
                    "starboard-side passing constraint."
                )

            if obj.marker_type is MarkerType.GREEN and cross <= 0:
                return self._blocked(
                    f"Direct route violates GREEN buoy {obj.object_id} "
                    "port-side passing constraint."
                )

            if obj.marker_type is MarkerType.OBSTACLE:
                if distance < self.settings.obstacle_clearance_m:
                    return self._blocked(
                        f"Direct route is too close to inactive obstacle "
                        f"{obj.object_id}."
                    )
            elif obj.marker_type in (
                MarkerType.RED,
                MarkerType.GREEN,
                MarkerType.ENTRY,
            ):
                if distance < self.settings.transit_clearance_m:
                    return self._blocked(
                        f"Direct route is too close to buoy {obj.object_id}; "
                        "required clearance is not met."
                    )

        return PathPlan(
            status=PathPlanStatus.READY,
            waypoints=(Waypoint(end_x, end_y),),
            target_object_id=target.object_id,
            reason=(
                "Direct route passed the implemented geometric checks. "
                "This is not a complete field route or a real-world "
                "safety certification."
            ),
        )

    @staticmethod
    def _blocked(reason: str) -> PathPlan:
        return PathPlan(
            status=PathPlanStatus.BLOCKED,
            waypoints=(),
            target_object_id=None,
            reason=reason,
        )