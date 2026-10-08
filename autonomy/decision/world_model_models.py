from dataclasses import dataclass
from enum import Enum

from interfaces.messages import BoatState, MarkerType


class WorldObjectStatus(Enum):
    TENTATIVE = "TENTATIVE"
    CONFIRMED = "CONFIRMED"
    STALE = "STALE"


@dataclass(frozen=True)
class WorldObject:
    object_id: int
    marker_type: MarkerType

    x_m: float
    y_m: float
    position_sigma_m: float

    existence_confidence: float
    classification_confidence: float

    first_seen_s: float
    last_seen_s: float

    observation_count: int

    status: WorldObjectStatus

    last_track_id: int | None


@dataclass(frozen=True)
class WorldSnapshot:
    timestamp_s: float
    boat: BoatState
    objects: tuple[WorldObject, ...]
    revision: int