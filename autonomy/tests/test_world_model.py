import math

import pytest

from decision.world_model import (
    WorldModel,
    associate_detections,
    detection_to_map_position,
)
from decision.world_model_models import (
    WorldObject,
    WorldObjectStatus,
)
from interfaces.messages import (
    BoatState,
    Detection,
    InvalidMessageError,
    MarkerType,
    ObjectClass,
    LightColor,
    LightState,
)


def make_boat(
    x_m=0.0,
    y_m=0.0,
    heading_deg=0.0,
    timestamp_s=10.0,
    valid=True,
):
    return BoatState(
        timestamp_s=timestamp_s,
        x_m=x_m,
        y_m=y_m,
        heading_deg=heading_deg,
        heading_sigma_deg=1.0,
        speed_mps=1.0,
        position_sigma_m=0.5,
        valid=valid,
    )


def make_detection(
    range_m=10.0,
    bearing_deg=0.0,
    track_id=1,
):
    return Detection(
        timestamp_s=10.0,
        track_id=track_id,
        object_class=ObjectClass.TASK1_BUOY,
        light_color=LightColor.RED,
        light_state=LightState.FLASHING,
        range_m=range_m,
        bearing_deg=bearing_deg,
        range_sigma_m=0.5,
        bearing_sigma_deg=2.0,
        detection_confidence=0.9,
        light_confidence=0.9,
    )


def make_world_object(
    object_id,
    x_m,
    y_m,
    position_sigma_m=0.5,
):
    return WorldObject(
        object_id=object_id,
        marker_type=MarkerType.RED,
        x_m=x_m,
        y_m=y_m,
        position_sigma_m=position_sigma_m,
        existence_confidence=0.9,
        classification_confidence=0.9,
        first_seen_s=1.0,
        last_seen_s=10.0,
        observation_count=3,
        status=WorldObjectStatus.CONFIRMED,
        last_track_id=1,
    )


def assert_position(actual, expected_x, expected_y, tolerance=1e-6):
    assert math.isclose(actual[0], expected_x, abs_tol=tolerance)
    assert math.isclose(actual[1], expected_y, abs_tol=tolerance)


# ---------------------------------------------------------------------------
# Stage 1 - coordinate projection
# ---------------------------------------------------------------------------


def test_heading_zero_detection_ahead_goes_north():
    boat = make_boat(100.0, 200.0, 0.0)
    detection = make_detection(10.0, 0.0)

    result = detection_to_map_position(boat, detection)

    assert_position(result, 100.0, 210.0)


def test_heading_90_detection_ahead_goes_east():
    boat = make_boat(100.0, 200.0, 90.0)
    detection = make_detection(10.0, 0.0)

    result = detection_to_map_position(boat, detection)

    assert_position(result, 110.0, 200.0)


def test_heading_180_detection_ahead_goes_south():
    boat = make_boat(100.0, 200.0, 180.0)
    detection = make_detection(10.0, 0.0)

    result = detection_to_map_position(boat, detection)

    assert_position(result, 100.0, 190.0)


def test_heading_270_detection_ahead_goes_west():
    boat = make_boat(100.0, 200.0, 270.0)
    detection = make_detection(10.0, 0.0)

    result = detection_to_map_position(boat, detection)

    assert_position(result, 90.0, 200.0)


def test_positive_bearing_is_starboard():
    boat = make_boat(0.0, 0.0, 0.0)
    detection = make_detection(10.0, 90.0)

    result = detection_to_map_position(boat, detection)

    assert_position(result, 10.0, 0.0)


def test_negative_bearing_is_port():
    boat = make_boat(0.0, 0.0, 0.0)
    detection = make_detection(10.0, -90.0)

    result = detection_to_map_position(boat, detection)

    assert_position(result, -10.0, 0.0)


def test_projection_includes_boat_position():
    boat = make_boat(50.0, -20.0, 0.0)
    detection = make_detection(5.0, 0.0)

    result = detection_to_map_position(boat, detection)

    assert_position(result, 50.0, -15.0)


def test_invalid_boat_state_is_rejected():
    boat = make_boat(valid=False)
    detection = make_detection()

    with pytest.raises(ValueError):
        detection_to_map_position(boat, detection)


def test_negative_detection_range_is_rejected():
    with pytest.raises(InvalidMessageError):
        make_detection(-1.0, 0.0)


# ---------------------------------------------------------------------------
# Stage 2 - association
# ---------------------------------------------------------------------------


def test_detection_associates_with_nearby_world_object():
    detection = make_detection()

    world_object = make_world_object(
        object_id=10,
        x_m=0.5,
        y_m=0.5,
    )

    result = associate_detections(
        [(detection, 0.0, 0.0)],
        (world_object,),
    )

    assert result == [(0, 0)]


def test_detection_does_not_associate_when_outside_gate():
    detection = make_detection()

    world_object = make_world_object(
        object_id=10,
        x_m=10.0,
        y_m=10.0,
    )

    result = associate_detections(
        [(detection, 0.0, 0.0)],
        (world_object,),
    )

    assert result == [(0, None)]


def test_two_detections_cannot_associate_with_same_object():
    detection_1 = make_detection(track_id=1)
    detection_2 = make_detection(track_id=2)

    world_object = make_world_object(
        object_id=10,
        x_m=0.2,
        y_m=0.2,
    )

    result = associate_detections(
        [
            (detection_1, 0.0, 0.0),
            (detection_2, 0.3, 0.3),
        ],
        (world_object,),
    )

    matches = [match for _, match in result if match is not None]

    assert len(matches) == 1


def test_two_world_objects_can_match_two_detections():
    detection_1 = make_detection(track_id=1)
    detection_2 = make_detection(track_id=2)

    object_1 = make_world_object(
        object_id=10,
        x_m=0.2,
        y_m=0.2,
    )

    object_2 = make_world_object(
        object_id=20,
        x_m=10.2,
        y_m=10.2,
    )

    result = associate_detections(
        [
            (detection_1, 0.0, 0.0),
            (detection_2, 10.0, 10.0),
        ],
        (object_1, object_2),
    )

    assert result == [
        (0, 0),
        (1, 1),
    ]


def test_association_prefers_closest_object():
    detection = make_detection()

    object_1 = make_world_object(
        object_id=10,
        x_m=0.5,
        y_m=0.0,
    )

    object_2 = make_world_object(
        object_id=20,
        x_m=2.0,
        y_m=0.0,
    )

    result = associate_detections(
        [(detection, 0.0, 0.0)],
        (object_1, object_2),
    )

    assert result == [(0, 0)]


def test_association_is_deterministic_on_equal_distance():
    detection = make_detection()

    object_1 = make_world_object(
        object_id=20,
        x_m=1.0,
        y_m=0.0,
    )

    object_2 = make_world_object(
        object_id=10,
        x_m=-1.0,
        y_m=0.0,
    )

    result = associate_detections(
        [(detection, 0.0, 0.0)],
        (object_1, object_2),
    )

    assert result == [(0, 1)]


def test_large_uncertainty_expands_gate_but_is_capped():
    detection = make_detection()

    world_object = make_world_object(
        object_id=10,
        x_m=4.0,
        y_m=0.0,
        position_sigma_m=10.0,
    )

    result = associate_detections(
        [(detection, 0.0, 0.0)],
        (world_object,),
    )

    assert result == [(0, 0)]


def test_invalid_gate_configuration_is_rejected():
    detection = make_detection()
    world_object = make_world_object(10, 0.0, 0.0)

    with pytest.raises(ValueError):
        associate_detections(
            [(detection, 0.0, 0.0)],
            (world_object,),
            minimum_gate_m=0.0,
        )

    with pytest.raises(ValueError):
        associate_detections(
            [(detection, 0.0, 0.0)],
            (world_object,),
            sigma_multiplier=0.0,
        )

    with pytest.raises(ValueError):
        associate_detections(
            [(detection, 0.0, 0.0)],
            (world_object,),
            minimum_gate_m=6.0,
            maximum_gate_m=5.0,
        )


def test_nonfinite_projected_detection_is_rejected():
    detection = make_detection()
    world_object = make_world_object(10, 0.0, 0.0)

    with pytest.raises(ValueError):
        associate_detections(
            [(detection, math.nan, 0.0)],
            (world_object,),
        )


# ---------------------------------------------------------------------------
# Stage 3 - persistent WorldModel
# ---------------------------------------------------------------------------


def test_first_detection_creates_one_tentative_object():
    model = WorldModel()

    boat = make_boat(
        x_m=0.0,
        y_m=0.0,
        heading_deg=0.0,
        timestamp_s=10.0,
    )

    detection = make_detection(
        range_m=10.0,
        bearing_deg=0.0,
    )

    snapshot = model.update(
        boat,
        [detection],
    )

    assert len(snapshot.objects) == 1

    obj = snapshot.objects[0]

    assert obj.object_id == 1
    assert obj.marker_type == MarkerType.UNKNOWN
    assert obj.status == WorldObjectStatus.TENTATIVE
    assert obj.observation_count == 1
    assert obj.first_seen_s == 10.0
    assert obj.last_seen_s == 10.0
    assert obj.last_track_id == 1

    assert_position(
        (obj.x_m, obj.y_m),
        0.0,
        10.0,
    )


def test_second_detection_of_same_buoy_updates_existing_object():
    model = WorldModel()

    boat_1 = make_boat(
        x_m=0.0,
        y_m=0.0,
        heading_deg=0.0,
        timestamp_s=10.0,
    )

    detection_1 = make_detection(
        range_m=10.0,
        bearing_deg=0.0,
        track_id=1,
    )

    first = model.update(
        boat_1,
        [detection_1],
    )

    first_id = first.objects[0].object_id

    boat_2 = make_boat(
        x_m=0.5,
        y_m=0.0,
        heading_deg=0.0,
        timestamp_s=11.0,
    )

    detection_2 = make_detection(
        range_m=9.5,
        bearing_deg=0.0,
        track_id=99,
    )

    second = model.update(
        boat_2,
        [detection_2],
    )

    assert len(second.objects) == 1
    assert second.objects[0].object_id == first_id
    assert second.objects[0].observation_count == 2
    assert second.objects[0].last_seen_s == 11.0
    assert second.objects[0].last_track_id == 99


def test_two_far_apart_detections_create_two_objects():
    model = WorldModel()

    boat = make_boat()

    detection_1 = make_detection(
        range_m=10.0,
        bearing_deg=0.0,
        track_id=1,
    )

    detection_2 = make_detection(
        range_m=10.0,
        bearing_deg=90.0,
        track_id=2,
    )

    snapshot = model.update(
        boat,
        [detection_1, detection_2],
    )

    assert len(snapshot.objects) == 2
    assert [obj.object_id for obj in snapshot.objects] == [1, 2]


def test_unmatched_detection_creates_new_object_without_replacing_old():
    model = WorldModel()

    boat = make_boat()

    first_detection = make_detection(
        range_m=10.0,
        bearing_deg=0.0,
        track_id=1,
    )

    model.update(
        boat,
        [first_detection],
    )

    second_detection = make_detection(
        range_m=10.0,
        bearing_deg=90.0,
        track_id=2,
    )

    snapshot = model.update(
        boat,
        [second_detection],
    )

    assert len(snapshot.objects) == 2

    assert snapshot.objects[0].object_id == 1
    assert snapshot.objects[1].object_id == 2


def test_object_position_is_fused_after_repeated_observation():
    model = WorldModel()

    boat = make_boat(
        x_m=0.0,
        y_m=0.0,
        heading_deg=0.0,
        timestamp_s=10.0,
    )

    first_detection = make_detection(
        range_m=10.0,
        bearing_deg=0.0,
    )

    first_snapshot = model.update(
        boat,
        [first_detection],
    )

    first_position = (
        first_snapshot.objects[0].x_m,
        first_snapshot.objects[0].y_m,
    )

    second_detection = make_detection(
        range_m=10.0,
        bearing_deg=5.0,
    )

    second_position = detection_to_map_position(
        boat,
        second_detection,
    )

    snapshot = model.update(
        boat,
        [second_detection],
    )

    obj = snapshot.objects[0]

    # The fused position must lie between the two actual observations
    # independently on both axes.
    assert min(first_position[0], second_position[0]) <= obj.x_m <= max(
        first_position[0],
        second_position[0],
    )

    assert min(first_position[1], second_position[1]) <= obj.y_m <= max(
        first_position[1],
        second_position[1],
    )


def test_position_uncertainty_does_not_increase_after_consistent_update():
    model = WorldModel()

    boat = make_boat()

    detection = make_detection(
        range_m=10.0,
        bearing_deg=0.0,
    )

    first = model.update(
        boat,
        [detection],
    )

    first_sigma = first.objects[0].position_sigma_m

    second = model.update(
        boat,
        [detection],
    )

    second_sigma = second.objects[0].position_sigma_m

    assert second_sigma < first_sigma


def test_existence_confidence_increases_with_observation():
    model = WorldModel()

    boat = make_boat()

    detection = make_detection()

    first = model.update(
        boat,
        [detection],
    )

    second = model.update(
        boat,
        [detection],
    )

    assert (
        second.objects[0].existence_confidence
        > first.objects[0].existence_confidence
    )


def test_revision_increases_after_each_update():
    model = WorldModel()

    boat = make_boat()

    first = model.update(
        boat,
        [],
    )

    second = model.update(
        boat,
        [],
    )

    assert first.revision == 1
    assert second.revision == 2


def test_empty_detection_batch_preserves_existing_objects():
    model = WorldModel()

    boat = make_boat()

    detection = make_detection()

    first = model.update(
        boat,
        [detection],
    )

    second = model.update(
        boat,
        [],
    )

    assert len(first.objects) == 1
    assert len(second.objects) == 1

    assert (
        second.objects[0].object_id
        == first.objects[0].object_id
    )


def test_reset_clears_objects_and_restarts_object_ids():
    model = WorldModel()

    boat = make_boat()

    detection = make_detection()

    first = model.update(
        boat,
        [detection],
    )

    assert first.objects[0].object_id == 1

    model.reset()

    second = model.update(
        boat,
        [detection],
    )

    assert len(second.objects) == 1
    assert second.objects[0].object_id == 1
    assert second.revision == 1


def test_out_of_order_update_is_rejected():
    model = WorldModel()

    first_boat = make_boat(timestamp_s=20.0)

    model.update(
        first_boat,
        [],
    )

    older_boat = make_boat(timestamp_s=19.0)

    with pytest.raises(ValueError):
        model.update(
            older_boat,
            [],
        )


def test_snapshot_objects_are_immutable():
    model = WorldModel()

    boat = make_boat()

    snapshot = model.update(
        boat,
        [make_detection()],
    )

    with pytest.raises(Exception):
        snapshot.objects[0].x_m = 999.0

# ---------------------------------------------------------------------------
# Stage 4 - object lifecycle
# ---------------------------------------------------------------------------


def test_new_object_starts_tentative():
    model = WorldModel()
    boat = make_boat(timestamp_s=10.0)

    snapshot = model.update(
        boat,
        [make_detection()],
    )

    assert snapshot.objects[0].status == WorldObjectStatus.TENTATIVE


def test_object_becomes_confirmed_after_required_evidence():
    model = WorldModel()

    model.update(
        make_boat(timestamp_s=10.0),
        [make_detection()],
    )

    model.update(
        make_boat(timestamp_s=11.0),
        [make_detection()],
    )

    snapshot = model.update(
        make_boat(timestamp_s=12.0),
        [make_detection()],
    )

    obj = snapshot.objects[0]

    assert obj.status == WorldObjectStatus.CONFIRMED
    assert obj.observation_count == 3
    assert obj.last_seen_s == 12.0
    assert obj.existence_confidence >= 0.70
    assert obj.last_seen_s - obj.first_seen_s >= 2.0


def test_object_stays_tentative_with_too_few_observations():
    model = WorldModel()

    model.update(
        make_boat(timestamp_s=10.0),
        [make_detection()],
    )

    snapshot = model.update(
        make_boat(timestamp_s=11.0),
        [make_detection()],
    )

    assert snapshot.objects[0].status == WorldObjectStatus.TENTATIVE
    assert snapshot.objects[0].observation_count == 2


def test_object_stays_tentative_when_observations_do_not_span_enough_time():
    model = WorldModel()

    model.update(
        make_boat(timestamp_s=10.0),
        [make_detection()],
    )

    model.update(
        make_boat(timestamp_s=10.5),
        [make_detection()],
    )

    snapshot = model.update(
        make_boat(timestamp_s=11.0),
        [make_detection()],
    )

    obj = snapshot.objects[0]

    assert obj.status == WorldObjectStatus.TENTATIVE
    assert obj.observation_count == 3
    assert obj.last_seen_s - obj.first_seen_s < 2.0


def test_object_stays_tentative_when_existence_confidence_is_too_low():
    model = WorldModel()

    low_confidence_detection = make_detection()
    low_confidence_detection = Detection(
        timestamp_s=low_confidence_detection.timestamp_s,
        track_id=low_confidence_detection.track_id,
        object_class=low_confidence_detection.object_class,
        light_color=low_confidence_detection.light_color,
        light_state=low_confidence_detection.light_state,
        range_m=low_confidence_detection.range_m,
        bearing_deg=low_confidence_detection.bearing_deg,
        range_sigma_m=low_confidence_detection.range_sigma_m,
        bearing_sigma_deg=low_confidence_detection.bearing_sigma_deg,
        detection_confidence=0.1,
        light_confidence=low_confidence_detection.light_confidence,
    )

    model.update(
        make_boat(timestamp_s=10.0),
        [low_confidence_detection],
    )

    model.update(
        make_boat(timestamp_s=11.0),
        [low_confidence_detection],
    )

    snapshot = model.update(
        make_boat(timestamp_s=12.0),
        [low_confidence_detection],
    )

    obj = snapshot.objects[0]

    assert obj.status == WorldObjectStatus.TENTATIVE
    assert obj.observation_count == 3
    assert obj.existence_confidence < 0.70


def test_confirmed_object_becomes_stale_after_stale_timeout():
    model = WorldModel()

    model.update(
        make_boat(timestamp_s=10.0),
        [make_detection()],
    )

    model.update(
        make_boat(timestamp_s=11.0),
        [make_detection()],
    )

    confirmed = model.update(
        make_boat(timestamp_s=12.0),
        [make_detection()],
    )

    assert confirmed.objects[0].status == WorldObjectStatus.CONFIRMED

    stale = model.update(
        make_boat(timestamp_s=15.0),
        [],
    )

    assert stale.objects[0].status == WorldObjectStatus.STALE
    assert stale.objects[0].object_id == confirmed.objects[0].object_id


def test_stale_object_becomes_confirmed_when_seen_again():
    model = WorldModel()

    model.update(
        make_boat(timestamp_s=10.0),
        [make_detection()],
    )

    model.update(
        make_boat(timestamp_s=11.0),
        [make_detection()],
    )

    model.update(
        make_boat(timestamp_s=12.0),
        [make_detection()],
    )

    stale = model.update(
        make_boat(timestamp_s=15.0),
        [],
    )

    assert stale.objects[0].status == WorldObjectStatus.STALE

    recovered = model.update(
        make_boat(timestamp_s=16.0),
        [make_detection()],
    )

    assert recovered.objects[0].status == WorldObjectStatus.CONFIRMED
    assert recovered.objects[0].object_id == stale.objects[0].object_id
    assert recovered.objects[0].observation_count == 4


# ---------------------------------------------------------------------------
# Stage 5B - Interpretation integration and classification safeguards
# ---------------------------------------------------------------------------

from interfaces.messages import Interpretation, InterpretationReason


def make_interpretation(
    marker_type=MarkerType.RED,
    marker_confidence=0.9,
    object_confidence=0.9,
    range_m=10.0,
    bearing_deg=0.0,
    track_id=1,
):
    return Interpretation(
        timestamp_s=10.0,
        track_id=track_id,
        marker_type=marker_type,
        marker_confidence=marker_confidence,
        object_confidence=object_confidence,
        reason=InterpretationReason.OK,
        range_m=range_m,
        bearing_deg=bearing_deg,
        range_sigma_m=0.5,
        bearing_sigma_deg=2.0,
    )


def test_interpretation_creates_classified_object():
    model = WorldModel()
    boat = make_boat(timestamp_s=10.0)

    snapshot = model.update_interpretations(
        boat,
        [make_interpretation(marker_type=MarkerType.RED)],
    )

    assert len(snapshot.objects) == 1
    assert snapshot.objects[0].marker_type == MarkerType.RED
    assert snapshot.objects[0].classification_confidence == 0.9


def test_obstacle_cannot_erase_strong_semantic_classification():
    model = WorldModel()
    boat = make_boat(timestamp_s=10.0)

    model.update_interpretations(
        boat,
        [make_interpretation(marker_type=MarkerType.RED)],
    )

    snapshot = model.update_interpretations(
        make_boat(timestamp_s=11.0),
        [make_interpretation(marker_type=MarkerType.OBSTACLE)],
    )

    assert snapshot.objects[0].marker_type == MarkerType.RED
    assert snapshot.objects[0].classification_confidence == 0.9


def test_unknown_cannot_erase_strong_semantic_classification():
    model = WorldModel()
    boat = make_boat(timestamp_s=10.0)

    model.update_interpretations(
        boat,
        [make_interpretation(marker_type=MarkerType.RED)],
    )

    snapshot = model.update_interpretations(
        make_boat(timestamp_s=11.0),
        [make_interpretation(marker_type=MarkerType.UNKNOWN)],
    )

    assert snapshot.objects[0].marker_type == MarkerType.RED
    assert snapshot.objects[0].classification_confidence == 0.9
