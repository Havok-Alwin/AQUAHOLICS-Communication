
import math

from interfaces.messages import (
    BoatState,
    Detection,
    Interpretation,
    MarkerType,
)
from decision.world_model_models import (
    WorldObject,
    WorldObjectStatus,
    WorldSnapshot,
)


DEFAULT_MIN_GATE_M = 1.0
DEFAULT_SIGMA_MULTIPLIER = 3.0
DEFAULT_MAX_GATE_M = 5.0

DEFAULT_CONFIRMATION_COUNT = 3
DEFAULT_CONFIRMATION_TIME_SPAN_S = 2.0
DEFAULT_CONFIRMATION_EXISTENCE_CONFIDENCE = 0.70

DEFAULT_STALE_AFTER_S = 3.0
DEFAULT_TENTATIVE_EXPIRY_S = 3.0

DEFAULT_MIN_CLASSIFICATION_CONFIDENCE = 0.50
DEFAULT_CLASSIFICATION_HYSTERESIS = 0.20
DEFAULT_CLASSIFICATION_EVIDENCE_GAIN = 0.20

CLASSIFICATION_EVIDENCE_DECAY = 0.65
CLASSIFICATION_SWITCH_MARGIN = 0.10
CLASSIFICATION_MIN_CHALLENGER_COUNT = 2
CLASSIFICATION_OBSTACLE_CHALLENGER_COUNT = 3


def detection_to_map_position(boat, detection):
    if not boat.valid:
        raise ValueError("BoatState is invalid")
    if not math.isfinite(detection.range_m):
        raise ValueError("Detection range must be finite")
    if detection.range_m < 0.0:
        raise ValueError("Detection range cannot be negative")

    bearing_rad = math.radians(detection.bearing_deg)
    heading_rad = math.radians(boat.heading_deg)
    local_x = detection.range_m * math.sin(bearing_rad)
    local_y = detection.range_m * math.cos(bearing_rad)

    map_x = (
        local_x * math.cos(heading_rad)
        + local_y * math.sin(heading_rad)
        + boat.x_m
    )
    map_y = (
        -local_x * math.sin(heading_rad)
        + local_y * math.cos(heading_rad)
        + boat.y_m
    )
    return map_x, map_y


def interpretation_to_map_position(boat, interpretation):
    if not boat.valid:
        raise ValueError("BoatState is invalid")
    if not math.isfinite(interpretation.range_m):
        raise ValueError("Interpretation range must be finite")
    if interpretation.range_m < 0.0:
        raise ValueError("Interpretation range cannot be negative")

    bearing_rad = math.radians(interpretation.bearing_deg)
    heading_rad = math.radians(boat.heading_deg)
    local_x = interpretation.range_m * math.sin(bearing_rad)
    local_y = interpretation.range_m * math.cos(bearing_rad)

    map_x = (
        local_x * math.cos(heading_rad)
        + local_y * math.sin(heading_rad)
        + boat.x_m
    )
    map_y = (
        -local_x * math.sin(heading_rad)
        + local_y * math.cos(heading_rad)
        + boat.y_m
    )
    return map_x, map_y


def _distance_m(x1, y1, x2, y2):
    return math.hypot(x1 - x2, y1 - y2)


def _association_gate_m(
    object_sigma_m,
    observation_sigma_m,
    min_gate_m=DEFAULT_MIN_GATE_M,
    sigma_multiplier=DEFAULT_SIGMA_MULTIPLIER,
    max_gate_m=DEFAULT_MAX_GATE_M,
):
    combined_sigma = math.hypot(
        object_sigma_m,
        observation_sigma_m,
    )
    return min(
        max_gate_m,
        max(min_gate_m, sigma_multiplier * combined_sigma),
    )


def associate_detections(
    detections: list[
        tuple[Detection | Interpretation, float, float]
    ],
    world_objects: tuple[WorldObject, ...],
    min_gate_m=DEFAULT_MIN_GATE_M,
    sigma_multiplier=DEFAULT_SIGMA_MULTIPLIER,
    max_gate_m=DEFAULT_MAX_GATE_M,
    *,
    minimum_gate_m=None,
    maximum_gate_m=None,
    observation_sigmas_m=None,
):
    """Associate projected observations with existing world objects.

    observation_sigmas_m, when supplied, contains the full position
    uncertainty for each projected observation. Omitting it preserves
    compatibility with callers using range uncertainty alone.
    """
    if minimum_gate_m is not None:
        min_gate_m = minimum_gate_m
    if maximum_gate_m is not None:
        max_gate_m = maximum_gate_m

    if not math.isfinite(min_gate_m) or min_gate_m <= 0.0:
        raise ValueError("minimum_gate_m must be finite and positive")
    if (
        not math.isfinite(sigma_multiplier)
        or sigma_multiplier <= 0.0
    ):
        raise ValueError("sigma_multiplier must be finite and positive")
    if not math.isfinite(max_gate_m) or max_gate_m <= 0.0:
        raise ValueError("maximum_gate_m must be finite and positive")
    if min_gate_m > max_gate_m:
        raise ValueError(
            "minimum_gate_m cannot exceed maximum_gate_m"
        )

    if observation_sigmas_m is not None:
        if len(observation_sigmas_m) != len(detections):
            raise ValueError(
                "observation_sigmas_m must match detections length"
            )

    candidates = []

    for detection_index, (observation, x_m, y_m) in enumerate(detections):
        if not math.isfinite(x_m) or not math.isfinite(y_m):
            raise ValueError(
                "Projected observation position must be finite"
            )

        if observation_sigmas_m is None:
            observation_sigma = observation.range_sigma_m
        else:
            observation_sigma = observation_sigmas_m[detection_index]

        if (
            not math.isfinite(observation_sigma)
            or observation_sigma < 0.0
        ):
            raise ValueError(
                "Observation sigma must be finite and non-negative"
            )

        for object_index, obj in enumerate(world_objects):
            distance = _distance_m(x_m, y_m, obj.x_m, obj.y_m)
            gate = _association_gate_m(
                obj.position_sigma_m,
                observation_sigma,
                min_gate_m,
                sigma_multiplier,
                max_gate_m,
            )

            if distance <= gate:
                candidates.append(
                    (
                        distance,
                        obj.object_id,
                        detection_index,
                        object_index,
                    )
                )

    # Deterministic greedy one-to-one association.
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    matched_detection_indices = set()
    matched_object_indices = set()
    matched_by_detection = {}

    for _, _, detection_index, object_index in candidates:
        if detection_index in matched_detection_indices:
            continue
        if object_index in matched_object_indices:
            continue

        matched_detection_indices.add(detection_index)
        matched_object_indices.add(object_index)
        matched_by_detection[detection_index] = object_index

    return [
        (index, matched_by_detection.get(index))
        for index in range(len(detections))
    ]


def _observation_sigma_m(boat, observation):
    """Estimate projected position uncertainty in metres."""
    values = (
        observation.range_m,
        observation.range_sigma_m,
        observation.bearing_sigma_deg,
        boat.heading_sigma_deg,
        boat.position_sigma_m,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Observation and boat uncertainties must be finite")
    if (
        observation.range_m < 0.0
        or observation.range_sigma_m < 0.0
        or observation.bearing_sigma_deg < 0.0
        or boat.heading_sigma_deg < 0.0
        or boat.position_sigma_m < 0.0
    ):
        raise ValueError("Observation and boat uncertainties cannot be negative")

    bearing_sigma_rad = math.radians(observation.bearing_sigma_deg)
    heading_sigma_rad = math.radians(boat.heading_sigma_deg)

    radial_sigma = observation.range_sigma_m
    bearing_sigma = observation.range_m * bearing_sigma_rad
    heading_sigma = observation.range_m * heading_sigma_rad
    position_sigma = boat.position_sigma_m

    return max(
        0.01,
        math.sqrt(
            radial_sigma**2
            + bearing_sigma**2
            + heading_sigma**2
            + position_sigma**2
        ),
    )


def _weighted_position_update(
    old_x,
    old_y,
    old_sigma,
    new_x,
    new_y,
    new_sigma,
):
    old_variance = max(old_sigma**2, 0.0001)
    new_variance = max(new_sigma**2, 0.0001)

    old_weight = 1.0 / old_variance
    new_weight = 1.0 / new_variance
    total_weight = old_weight + new_weight

    x = (old_x * old_weight + new_x * new_weight) / total_weight
    y = (old_y * old_weight + new_y * new_weight) / total_weight
    sigma = math.sqrt(1.0 / total_weight)
    return x, y, sigma


def _determine_object_status(old_object, timestamp_s, observed=False):
    if old_object.status == WorldObjectStatus.CONFIRMED:
        if (
            not observed
            and timestamp_s - old_object.last_seen_s
            >= DEFAULT_STALE_AFTER_S
        ):
            return WorldObjectStatus.STALE
        return WorldObjectStatus.CONFIRMED

    if old_object.status == WorldObjectStatus.STALE:
        if observed:
            return WorldObjectStatus.CONFIRMED
        return WorldObjectStatus.STALE

    if (
        old_object.observation_count >= DEFAULT_CONFIRMATION_COUNT
        and old_object.last_seen_s - old_object.first_seen_s
        >= DEFAULT_CONFIRMATION_TIME_SPAN_S
        and old_object.existence_confidence
        >= DEFAULT_CONFIRMATION_EXISTENCE_CONFIDENCE
    ):
        return WorldObjectStatus.CONFIRMED

    return WorldObjectStatus.TENTATIVE


def _pack_classification_evidence(evidence):
    """Convert evidence into a deterministic immutable tuple."""
    return tuple(
        (marker, float(values[0]), int(values[1]))
        for marker, values in sorted(
            evidence.items(),
            key=lambda item: item[0].value,
        )
    )


def _update_classification(
    old_marker,
    old_confidence,
    new_marker,
    new_confidence,
    old_evidence=None,
):
    """Update classification while retaining compatibility with legacy calls."""
    track_evidence = old_evidence is not None
    evidence = {
        marker: [float(score), int(count)]
        for marker, score, count in (old_evidence or ())
    }

    def result(marker, confidence):
        if track_evidence:
            return marker, confidence, _pack_classification_evidence(evidence)
        return marker, confidence

    # Legacy four-argument behaviour.
    if not track_evidence:
        if (
            new_marker == MarkerType.UNKNOWN
            or new_confidence < DEFAULT_MIN_CLASSIFICATION_CONFIDENCE
        ):
            return old_marker, old_confidence

        if old_marker == MarkerType.UNKNOWN:
            return new_marker, new_confidence

        if (
            old_marker != MarkerType.OBSTACLE
            and new_marker == MarkerType.OBSTACLE
        ):
            return old_marker, old_confidence

        if old_marker == MarkerType.OBSTACLE:
            return new_marker, new_confidence

        if new_marker == old_marker:
            if new_confidence <= old_confidence:
                return old_marker, old_confidence
            return (
                old_marker,
                min(
                    1.0,
                    old_confidence
                    + DEFAULT_CLASSIFICATION_EVIDENCE_GAIN * new_confidence,
                ),
            )

        if (
            new_confidence
            >= old_confidence + DEFAULT_CLASSIFICATION_HYSTERESIS
        ):
            return new_marker, new_confidence

        return old_marker, old_confidence

    # Seed evidence for existing objects without evidence history.
    if old_marker != MarkerType.UNKNOWN and old_marker not in evidence:
        initial_count = (
            CLASSIFICATION_OBSTACLE_CHALLENGER_COUNT
            if old_marker == MarkerType.OBSTACLE
            else CLASSIFICATION_MIN_CHALLENGER_COUNT
        )
        evidence[old_marker] = [max(float(old_confidence), 0.01), initial_count]

    if (
        new_marker == MarkerType.UNKNOWN
        or new_confidence < DEFAULT_MIN_CLASSIFICATION_CONFIDENCE
    ):
        return result(old_marker, old_confidence)

    if (
        old_marker not in (MarkerType.UNKNOWN, MarkerType.OBSTACLE)
        and new_marker == MarkerType.OBSTACLE
    ):
        return result(old_marker, old_confidence)

    if old_marker == MarkerType.UNKNOWN:
        previous_score, previous_count = evidence.get(
            new_marker, [0.0, 0]
        )
        evidence[new_marker] = [
            previous_score + new_confidence,
            previous_count + 1,
        ]
        return result(new_marker, new_confidence)

    for marker in list(evidence):
        if marker != new_marker:
            evidence[marker][0] *= CLASSIFICATION_EVIDENCE_DECAY
            evidence[marker][1] = max(0, evidence[marker][1] - 1)

    if new_marker not in evidence:
        evidence[new_marker] = [0.0, 0]
    evidence[new_marker][0] += new_confidence
    evidence[new_marker][1] += 1

    current_score = evidence.get(old_marker, [0.0, 0])[0]
    challenger_score, challenger_count = evidence[new_marker]
    winner = old_marker

    if new_marker != old_marker:
        obstacle_involved = (
            old_marker == MarkerType.OBSTACLE
            or new_marker == MarkerType.OBSTACLE
        )
        required_count = (
            CLASSIFICATION_OBSTACLE_CHALLENGER_COUNT
            if obstacle_involved
            else CLASSIFICATION_MIN_CHALLENGER_COUNT
        )
        enough_observations = challenger_count >= required_count
        enough_evidence = (
            challenger_score >= current_score + CLASSIFICATION_SWITCH_MARGIN
        )
        if enough_observations and enough_evidence:
            winner = new_marker

    if winner == old_marker:
        if new_marker == old_marker and new_confidence > old_confidence:
            confidence = min(
                1.0,
                old_confidence
                + DEFAULT_CLASSIFICATION_EVIDENCE_GAIN * new_confidence,
            )
        else:
            confidence = old_confidence
    else:
        winning_score = evidence.get(winner, [0.0, 0])[0]
        total_score = sum(values[0] for values in evidence.values())
        confidence = min(1.0, winning_score / max(total_score, 1.0))

    return result(winner, confidence)


def _observation_is_duplicate(
    x_m,
    y_m,
    observation_sigma_m,
    reference_objects,
):
    """Return True if an observation is too close to a same-frame reference."""
    for obj in reference_objects:
        distance = _distance_m(x_m, y_m, obj.x_m, obj.y_m)
        gate = _association_gate_m(
            obj.position_sigma_m,
            observation_sigma_m,
        )
        if distance <= gate:
            return True
    return False


class WorldModel:
    def __init__(self):
        self._objects = []
        self._next_object_id = 1
        self._revision = 0
        self._last_timestamp_s = -math.inf

    def reset(self):
        self._objects.clear()
        self._next_object_id = 1
        self._revision = 0
        self._last_timestamp_s = -math.inf

    def update(self, boat, detections, timestamp_s=None):
        if timestamp_s is None:
            timestamp_s = boat.timestamp_s
        self._validate_update_time(timestamp_s)

        projected = []
        for detection in detections:
            x_m, y_m = detection_to_map_position(boat, detection)
            projected.append((detection, x_m, y_m))

        self._apply_projected_observations(boat, projected, timestamp_s)
        return self.snapshot(timestamp_s, boat)

    def update_interpretations(
        self,
        boat,
        interpretations,
        timestamp_s=None,
    ):
        if timestamp_s is None:
            timestamp_s = boat.timestamp_s
        self._validate_update_time(timestamp_s)

        projected = []
        for interpretation in interpretations:
            x_m, y_m = interpretation_to_map_position(boat, interpretation)
            projected.append((interpretation, x_m, y_m))

        self._apply_projected_interpretations(boat, projected, timestamp_s)
        return self.snapshot(timestamp_s, boat)

    def _validate_update_time(self, timestamp_s):
        if not math.isfinite(timestamp_s):
            raise ValueError("WorldModel timestamp must be finite")
        if timestamp_s < self._last_timestamp_s:
            raise ValueError("WorldModel updates must be chronological")

    def _apply_projected_observations(self, boat, projected, timestamp_s):
        observation_sigmas = tuple(
            _observation_sigma_m(boat, item[0]) for item in projected
        )
        matches = associate_detections(
            tuple(projected),
            tuple(self._objects),
            observation_sigmas_m=observation_sigmas,
        )

        matched_detections = {
            di for di, oi in matches if oi is not None
        }
        matched_objects = {
            oi for _, oi in matches if oi is not None
        }

        for detection_index, object_index in matches:
            if object_index is None:
                continue
            detection, x_m, y_m = projected[detection_index]
            self._objects[object_index] = self._update_object(
                self._objects[object_index],
                boat,
                detection,
                x_m,
                y_m,
                timestamp_s,
            )

        # Only compare unmatched observations against objects observed
        # in this frame, plus objects created earlier in this same frame.
        frame_objects = [
            self._objects[index]
            for index in sorted(matched_objects)
        ]

        for detection_index, (detection, x_m, y_m) in enumerate(projected):
            if detection_index in matched_detections:
                continue

            sigma = observation_sigmas[detection_index]
            if _observation_is_duplicate(
                x_m, y_m, sigma, frame_objects
            ):
                continue

            obj = self._create_object_from_detection(
                boat, detection, x_m, y_m, timestamp_s
            )
            self._objects.append(obj)
            frame_objects.append(obj)

        self._age_unmatched_objects(matched_objects, timestamp_s)
        self._finish_update(timestamp_s)

    def _apply_projected_interpretations(
        self,
        boat,
        projected,
        timestamp_s,
    ):
        observation_sigmas = tuple(
            _observation_sigma_m(boat, item[0]) for item in projected
        )
        matches = associate_detections(
            tuple(projected),
            tuple(self._objects),
            observation_sigmas_m=observation_sigmas,
        )

        matched_interpretations = {
            index for index, oi in matches if oi is not None
        }
        matched_objects = {
            oi for _, oi in matches if oi is not None
        }

        for interpretation_index, object_index in matches:
            if object_index is None:
                continue
            interpretation, x_m, y_m = projected[interpretation_index]
            self._objects[object_index] = (
                self._update_object_from_interpretation(
                    self._objects[object_index],
                    boat,
                    interpretation,
                    x_m,
                    y_m,
                    timestamp_s,
                )
            )

        frame_objects = [
            self._objects[index]
            for index in sorted(matched_objects)
        ]

        for interpretation_index, (
            interpretation, x_m, y_m
        ) in enumerate(projected):
            if interpretation_index in matched_interpretations:
                continue

            sigma = observation_sigmas[interpretation_index]
            if _observation_is_duplicate(
                x_m, y_m, sigma, frame_objects
            ):
                continue

            obj = self._create_object_from_interpretation(
                boat,
                interpretation,
                x_m,
                y_m,
                timestamp_s,
            )
            self._objects.append(obj)
            frame_objects.append(obj)

        self._age_unmatched_objects(matched_objects, timestamp_s)
        self._finish_update(timestamp_s)

    def _age_unmatched_objects(self, matched_objects, timestamp_s):
        remaining_objects = []

        for object_index, obj in enumerate(self._objects):
            if object_index in matched_objects:
                remaining_objects.append(obj)
                continue

            if obj.status == WorldObjectStatus.TENTATIVE:
                if (
                    timestamp_s - obj.last_seen_s
                    >= DEFAULT_TENTATIVE_EXPIRY_S
                ):
                    continue

            status = _determine_object_status(
                obj, timestamp_s, observed=False
            )
            if status != obj.status:
                obj = WorldObject(
                    object_id=obj.object_id,
                    marker_type=obj.marker_type,
                    x_m=obj.x_m,
                    y_m=obj.y_m,
                    position_sigma_m=obj.position_sigma_m,
                    existence_confidence=obj.existence_confidence,
                    classification_confidence=obj.classification_confidence,
                    first_seen_s=obj.first_seen_s,
                    last_seen_s=obj.last_seen_s,
                    observation_count=obj.observation_count,
                    status=status,
                    last_track_id=obj.last_track_id,
                    classification_evidence=obj.classification_evidence,
                )
            remaining_objects.append(obj)

        self._objects = remaining_objects

    def _finish_update(self, timestamp_s):
        self._objects.sort(key=lambda obj: obj.object_id)
        self._revision += 1
        self._last_timestamp_s = timestamp_s

    def snapshot(self, timestamp_s, boat):
        return WorldSnapshot(
            timestamp_s=timestamp_s,
            boat=boat,
            objects=tuple(self._objects),
            revision=self._revision,
        )

    # World Query API

    def get_objects(self):
        return tuple(self._objects)

    def get_confirmed_objects(self):
        return tuple(
            obj for obj in self._objects
            if obj.status == WorldObjectStatus.CONFIRMED
        )

    def get_objects_by_type(self, marker_type):
        return tuple(
            obj for obj in self._objects
            if obj.marker_type == marker_type
        )

    def get_confirmed_objects_by_type(self, marker_type):
        return tuple(
            obj for obj in self._objects
            if (
                obj.status == WorldObjectStatus.CONFIRMED
                and obj.marker_type == marker_type
            )
        )

    def get_nearest_object(
        self,
        x_m,
        y_m,
        marker_type=None,
        confirmed_only=False,
    ):
        if not math.isfinite(x_m):
            raise ValueError("x_m must be finite")
        if not math.isfinite(y_m):
            raise ValueError("y_m must be finite")

        candidates = self._objects
        if confirmed_only:
            candidates = [
                obj for obj in candidates
                if obj.status == WorldObjectStatus.CONFIRMED
            ]
        if marker_type is not None:
            candidates = [
                obj for obj in candidates
                if obj.marker_type == marker_type
            ]
        if not candidates:
            return None

        return min(
            candidates,
            key=lambda obj: (
                _distance_m(obj.x_m, obj.y_m, x_m, y_m),
                obj.object_id,
            ),
        )

    def _create_object_from_detection(
        self, boat, detection, x_m, y_m, timestamp_s
    ):
        obj = WorldObject(
            object_id=self._next_object_id,
            marker_type=MarkerType.UNKNOWN,
            x_m=x_m,
            y_m=y_m,
            position_sigma_m=_observation_sigma_m(boat, detection),
            existence_confidence=0.5,
            classification_confidence=0.0,
            first_seen_s=timestamp_s,
            last_seen_s=timestamp_s,
            observation_count=1,
            status=WorldObjectStatus.TENTATIVE,
            last_track_id=detection.track_id,
            classification_evidence=(),
        )
        self._next_object_id += 1
        return obj

    def _create_object_from_interpretation(
        self, boat, interpretation, x_m, y_m, timestamp_s
    ):
        (
            marker_type,
            classification_confidence,
            classification_evidence,
        ) = _update_classification(
            MarkerType.UNKNOWN,
            0.0,
            interpretation.marker_type,
            interpretation.marker_confidence,
            (),
        )

        obj = WorldObject(
            object_id=self._next_object_id,
            marker_type=marker_type,
            x_m=x_m,
            y_m=y_m,
            position_sigma_m=_observation_sigma_m(boat, interpretation),
            existence_confidence=0.5,
            classification_confidence=classification_confidence,
            first_seen_s=timestamp_s,
            last_seen_s=timestamp_s,
            observation_count=1,
            status=WorldObjectStatus.TENTATIVE,
            last_track_id=interpretation.track_id,
            classification_evidence=classification_evidence,
        )
        self._next_object_id += 1
        return obj

    def _update_object(
        self, old_object, boat, detection, x_m, y_m, timestamp_s
    ):
        new_x, new_y, new_sigma = _weighted_position_update(
            old_object.x_m,
            old_object.y_m,
            old_object.position_sigma_m,
            x_m,
            y_m,
            _observation_sigma_m(boat, detection),
        )

        candidate = WorldObject(
            object_id=old_object.object_id,
            marker_type=old_object.marker_type,
            x_m=new_x,
            y_m=new_y,
            position_sigma_m=new_sigma,
            existence_confidence=min(
                1.0,
                old_object.existence_confidence
                + 0.15 * detection.detection_confidence,
            ),
            classification_confidence=old_object.classification_confidence,
            first_seen_s=old_object.first_seen_s,
            last_seen_s=timestamp_s,
            observation_count=old_object.observation_count + 1,
            status=old_object.status,
            last_track_id=detection.track_id,
            classification_evidence=old_object.classification_evidence,
        )
        status = _determine_object_status(
            candidate, timestamp_s, observed=True
        )
        return WorldObject(
            object_id=candidate.object_id,
            marker_type=candidate.marker_type,
            x_m=candidate.x_m,
            y_m=candidate.y_m,
            position_sigma_m=candidate.position_sigma_m,
            existence_confidence=candidate.existence_confidence,
            classification_confidence=candidate.classification_confidence,
            first_seen_s=candidate.first_seen_s,
            last_seen_s=candidate.last_seen_s,
            observation_count=candidate.observation_count,
            status=status,
            last_track_id=candidate.last_track_id,
            classification_evidence=candidate.classification_evidence,
        )

    def _update_object_from_interpretation(
        self, old_object, boat, interpretation, x_m, y_m, timestamp_s
    ):
        new_x, new_y, new_sigma = _weighted_position_update(
            old_object.x_m,
            old_object.y_m,
            old_object.position_sigma_m,
            x_m,
            y_m,
            _observation_sigma_m(boat, interpretation),
        )

        (
            marker_type,
            classification_confidence,
            classification_evidence,
        ) = _update_classification(
            old_object.marker_type,
            old_object.classification_confidence,
            interpretation.marker_type,
            interpretation.marker_confidence,
            old_object.classification_evidence,
        )

        candidate = WorldObject(
            object_id=old_object.object_id,
            marker_type=marker_type,
            x_m=new_x,
            y_m=new_y,
            position_sigma_m=new_sigma,
            existence_confidence=min(
                1.0,
                old_object.existence_confidence
                + 0.15 * interpretation.object_confidence,
            ),
            classification_confidence=classification_confidence,
            first_seen_s=old_object.first_seen_s,
            last_seen_s=timestamp_s,
            observation_count=old_object.observation_count + 1,
            status=old_object.status,
            last_track_id=interpretation.track_id,
            classification_evidence=classification_evidence,
        )
        status = _determine_object_status(
            candidate, timestamp_s, observed=True
        )
        return WorldObject(
            object_id=candidate.object_id,
            marker_type=candidate.marker_type,
            x_m=candidate.x_m,
            y_m=candidate.y_m,
            position_sigma_m=candidate.position_sigma_m,
            existence_confidence=candidate.existence_confidence,
            classification_confidence=candidate.classification_confidence,
            first_seen_s=candidate.first_seen_s,
            last_seen_s=candidate.last_seen_s,
            observation_count=candidate.observation_count,
            status=status,
            last_track_id=candidate.last_track_id,
            classification_evidence=candidate.classification_evidence,
        )