from types import SimpleNamespace

import numpy as np
from landmark_helpers import category, point

from app.landmarks import face_from_result, hands_from_result, pose_from_result


def hand_result(specs):
    """specs: list of (label, score, base); base makes the coordinates distinct per hand."""
    return SimpleNamespace(
        hand_landmarks=[
            [point(b + i, b + i + 0.25, b + i + 0.5) for i in range(21)] for _, _, b in specs
        ],
        hand_world_landmarks=[
            [point(-(b + i), -(b + i) - 0.25, -(b + i) - 0.5) for i in range(21)]
            for _, _, b in specs
        ],
        handedness=[[category(label, score)] for label, score, _ in specs],
    )


def test_face_maps_blendshape_names_to_scores():
    """Criterion 6."""
    result = SimpleNamespace(
        face_blendshapes=[[category("jawOpen", 0.75), category("mouthSmileLeft", 0.125)]]
    )
    sample = face_from_result(result)
    assert sample is not None
    assert sample.blendshapes == {"jawOpen": 0.75, "mouthSmileLeft": 0.125}


def test_face_uses_only_the_first_face():
    """Criterion 6."""
    result = SimpleNamespace(
        face_blendshapes=[[category("jawOpen", 0.5)], [category("jawOpen", 0.9)]]
    )
    sample = face_from_result(result)
    assert sample is not None
    assert sample.blendshapes == {"jawOpen": 0.5}


def test_face_is_none_when_no_face_found():
    """Criterion 6."""
    assert face_from_result(SimpleNamespace(face_blendshapes=[])) is None


def test_hand_world_and_image_landmarks_land_in_the_right_arrays():
    """Criterion 6."""
    (hand,) = hands_from_result(hand_result([("Right", 0.9, 1.0)]))
    assert hand.world.shape == (21, 3)
    assert hand.image.shape == (21, 3)
    assert np.allclose(hand.image[0], [1.0, 1.25, 1.5])
    assert np.allclose(hand.image[20], [21.0, 21.25, 21.5])
    assert np.allclose(hand.world[0], [-1.0, -1.25, -1.5])
    assert np.allclose(hand.world[20], [-21.0, -21.25, -21.5])


def test_hand_keeps_handedness_label_and_score():
    """Criterion 6."""
    (hand,) = hands_from_result(hand_result([("Left", 0.8125, 0.0)]))
    assert hand.handedness == "Left"
    assert hand.score == 0.8125


def test_hands_are_sorted_by_score_descending():
    """Criterion 6."""
    hands = hands_from_result(
        hand_result([("Left", 0.6, 0.0), ("Right", 0.95, 10.0), ("Left", 0.8, 20.0)])
    )
    assert [h.score for h in hands] == [0.95, 0.8, 0.6]
    assert [h.handedness for h in hands] == ["Right", "Left", "Left"]
    # Landmarks travel with their hand when reordering.
    assert np.allclose(hands[0].image[0], [10.0, 10.25, 10.5])


def test_hands_are_empty_when_no_hand_found():
    """Criterion 6."""
    result = SimpleNamespace(hand_landmarks=[], hand_world_landmarks=[], handedness=[])
    assert hands_from_result(result) == []


def test_pose_has_33_rows_with_visibility_in_last_column():
    """Criterion 6."""
    landmarks = [point(i, i + 0.25, i + 0.5, visibility=i / 100) for i in range(33)]
    sample = pose_from_result(SimpleNamespace(pose_landmarks=[landmarks]))
    assert sample is not None
    assert sample.landmarks.shape == (33, 4)
    assert np.allclose(sample.landmarks[5], [5, 5.25, 5.5, 0.05])
    assert np.allclose(sample.landmarks[:, 3], [i / 100 for i in range(33)])


def test_pose_is_none_when_no_pose_found():
    """Criterion 6."""
    assert pose_from_result(SimpleNamespace(pose_landmarks=[])) is None
