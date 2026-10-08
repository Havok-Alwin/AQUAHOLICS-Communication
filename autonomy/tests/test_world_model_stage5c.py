from decision.world_model import _update_classification
from interfaces.messages import MarkerType


def test_same_class_never_decreases_classification_confidence():
    marker, confidence = _update_classification(
        MarkerType.GREEN,
        0.90,
        MarkerType.GREEN,
        0.60,
    )

    assert marker == MarkerType.GREEN
    assert confidence == 0.90


def test_same_class_strong_evidence_can_increase_confidence():
    marker, confidence = _update_classification(
        MarkerType.GREEN,
        0.50,
        MarkerType.GREEN,
        0.80,
    )

    assert marker == MarkerType.GREEN
    assert confidence > 0.50
    assert confidence <= 1.0


def test_weak_conflicting_classification_does_not_flip():
    marker, confidence = _update_classification(
        MarkerType.GREEN,
        0.80,
        MarkerType.RED,
        0.90,
    )

    assert marker == MarkerType.GREEN
    assert confidence == 0.80


def test_strong_conflicting_classification_can_flip():
    marker, confidence = _update_classification(
        MarkerType.GREEN,
        0.70,
        MarkerType.RED,
        0.95,
    )

    assert marker == MarkerType.RED
    assert confidence == 0.95


def test_unknown_never_erases_semantic_classification():
    marker, confidence = _update_classification(
        MarkerType.GREEN,
        0.85,
        MarkerType.UNKNOWN,
        0.95,
    )

    assert marker == MarkerType.GREEN
    assert confidence == 0.85


def test_obstacle_never_erases_semantic_classification():
    marker, confidence = _update_classification(
        MarkerType.GREEN,
        0.85,
        MarkerType.OBSTACLE,
        0.95,
    )

    assert marker == MarkerType.GREEN
    assert confidence == 0.85


def test_classification_confidence_is_bounded_at_one():
    marker, confidence = _update_classification(
        MarkerType.GREEN,
        0.95,
        MarkerType.GREEN,
        1.0,
    )

    assert marker == MarkerType.GREEN
    assert confidence == 1.0