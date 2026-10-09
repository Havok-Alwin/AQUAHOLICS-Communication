
from dataclasses import dataclass
from enum import Enum
from math import hypot

from decision.settings import DEFAULT_MIN_MARKER_CONFIDENCE
from decision.world_model_models import (
    WorldObject,
    WorldObjectStatus,
    WorldSnapshot,
)
from interfaces.messages import MarkerType


class MissionState(Enum):
    WAIT_FOR_START = "WAIT_FOR_START"
    FIND_ENTRY = "FIND_ENTRY"
    CIRCLE_ENTRY_CW = "CIRCLE_ENTRY_CW"
    TRANSIT_FIELD = "TRANSIT_FIELD"
    CIRCLE_EXIT_CCW = "CIRCLE_EXIT_CCW"
    DONE = "DONE"


class MissionObjective(Enum):
    WAIT_FOR_START = "WAIT_FOR_START"
    LOCATE_ENTRY = "LOCATE_ENTRY"
    CIRCLE_ENTRY_CLOCKWISE = "CIRCLE_ENTRY_CLOCKWISE"
    TRANSIT_FIELD_TO_EXIT = "TRANSIT_FIELD_TO_EXIT"
    CIRCLE_EXIT_COUNTERCLOCKWISE = "CIRCLE_EXIT_COUNTERCLOCKWISE"
    TASK_COMPLETE = "TASK_COMPLETE"


@dataclass(frozen=True)
class MissionDecision:
    state: MissionState
    objective: MissionObjective
    target_object_id: int | None
    reason: str


class MissionManager:
    """Tracks Task 1 mission phases; it does not control motors or plan paths.

    Circle and transit completion must come from other verified system
    components. Seeing a buoy alone never counts as completing a maneuver.
    """

    def __init__(
        self,
        min_marker_confidence: float = DEFAULT_MIN_MARKER_CONFIDENCE,
    ) -> None:
        if not 0.0 <= min_marker_confidence <= 1.0:
            raise ValueError("min_marker_confidence must be between 0 and 1")

        self.min_marker_confidence = min_marker_confidence
        self.state = MissionState.WAIT_FOR_START

    def reset(self) -> None:
        """Return the manager to its initial state."""
        self.state = MissionState.WAIT_FOR_START

    def update(
        self,
        snapshot: WorldSnapshot | None,
        *,
        start_enabled: bool = False,
        entry_circle_complete: bool = False,
        transit_complete: bool = False,
        exit_circle_complete: bool = False,
    ) -> MissionDecision:
        """Update one mission cycle using the latest world snapshot.

        start_enabled is an integration input, not a defined RobotX message.
        Completion flags must be supplied by the relevant navigation behavior.
        """

        if snapshot is None:
            return self._decision(
                None,
                "No world snapshot available; holding mission state.",
            )

        if self.state is MissionState.DONE:
            return self._decision(
                snapshot,
                "Task is already complete.",
            )

        if not snapshot.boat.valid:
            return self._decision(
                None,
                "Boat state is invalid; holding mission state.",
            )

        if self.state is MissionState.WAIT_FOR_START:
            if not start_enabled:
                return self._decision(
                    snapshot,
                    "Waiting for the system start/enable input.",
                )

            self.state = MissionState.FIND_ENTRY
            return self._decision(
                snapshot,
                "Start enabled; searching for the ENTRY marker.",
            )

        if self.state is MissionState.FIND_ENTRY:
            entry = self._find_target(snapshot, MarkerType.ENTRY)

            if entry is None:
                return self._decision(
                    snapshot,
                    "Searching for a confirmed ENTRY marker.",
                )

            self.state = MissionState.CIRCLE_ENTRY_CW
            return self._decision(
                snapshot,
                "ENTRY identified; clockwise circle must now be completed.",
            )

        if self.state is MissionState.CIRCLE_ENTRY_CW:
            if not entry_circle_complete:
                return self._decision(
                    snapshot,
                    "Waiting for verified clockwise ENTRY-circle completion.",
                )

            self.state = MissionState.TRANSIT_FIELD
            return self._decision(
                snapshot,
                "ENTRY circle completed; begin transit through the buoy field.",
            )

        if self.state is MissionState.TRANSIT_FIELD:
            if not transit_complete:
                return self._decision(
                    snapshot,
                    "Transit is in progress; waiting for verified completion.",
                )

            self.state = MissionState.CIRCLE_EXIT_CCW
            return self._decision(
                snapshot,
                "Transit completed; locate and circle EXIT counterclockwise.",
            )

        if self.state is MissionState.CIRCLE_EXIT_CCW:
            if not exit_circle_complete:
                return self._decision(
                    snapshot,
                    "Waiting for verified counterclockwise EXIT-circle completion.",
                )

            self.state = MissionState.DONE
            return self._decision(
                snapshot,
                "EXIT circle completed; Task 1 mission is complete.",
            )

        return self._decision(snapshot, "No mission transition performed.")

    def _find_target(
        self,
        snapshot: WorldSnapshot,
        marker_type: MarkerType,
    ) -> WorldObject | None:
        candidates = [
            obj
            for obj in snapshot.objects
            if obj.marker_type is marker_type
            and obj.status is WorldObjectStatus.CONFIRMED
            and obj.classification_confidence
            >= self.min_marker_confidence
        ]

        if not candidates:
            return None

        # Prefer stronger classification, then stronger existence confidence,
        # then the nearer object, with object ID as a deterministic tie-break.
        return min(
            candidates,
            key=lambda obj: (
                -obj.classification_confidence,
                -obj.existence_confidence,
                hypot(
                    obj.x_m - snapshot.boat.x_m,
                    obj.y_m - snapshot.boat.y_m,
                ),
                obj.object_id,
            ),
        )

    def _decision(
        self,
        snapshot: WorldSnapshot | None,
        reason: str,
    ) -> MissionDecision:
        objective_by_state = {
            MissionState.WAIT_FOR_START: MissionObjective.WAIT_FOR_START,
            MissionState.FIND_ENTRY: MissionObjective.LOCATE_ENTRY,
            MissionState.CIRCLE_ENTRY_CW:
                MissionObjective.CIRCLE_ENTRY_CLOCKWISE,
            MissionState.TRANSIT_FIELD:
                MissionObjective.TRANSIT_FIELD_TO_EXIT,
            MissionState.CIRCLE_EXIT_CCW:
                MissionObjective.CIRCLE_EXIT_COUNTERCLOCKWISE,
            MissionState.DONE: MissionObjective.TASK_COMPLETE,
        }

        target_id = None

        if snapshot is not None and snapshot.boat.valid:
            if self.state in (
                MissionState.FIND_ENTRY,
                MissionState.CIRCLE_ENTRY_CW,
            ):
                target = self._find_target(snapshot, MarkerType.ENTRY)
                target_id = target.object_id if target else None

            elif self.state in (
                MissionState.TRANSIT_FIELD,
                MissionState.CIRCLE_EXIT_CCW,
            ):
                target = self._find_target(snapshot, MarkerType.EXIT)
                target_id = target.object_id if target else None

        return MissionDecision(
            state=self.state,
            objective=objective_by_state[self.state],
            target_object_id=target_id,
            reason=reason,
        )

