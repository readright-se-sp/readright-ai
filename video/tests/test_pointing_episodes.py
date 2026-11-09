from pointing_helpers import timestamps
from video_helpers import make_settings

from app.pointing import episodes

INTERVAL = 200.0


def run(evidence: list[bool], **overrides):
    return episodes(evidence, timestamps(len(evidence)), INTERVAL, make_settings(**overrides))


def test_runs_with_gap_at_most_merge_gap_are_merged():
    """Criterion 4."""
    # Two missing frames = 400 ms, equal to the default merge gap.
    evidence = [True] * 4 + [False] * 2 + [True] * 4
    assert run(evidence) == [(0, 9)]


def test_runs_with_gap_over_merge_gap_stay_separate():
    """Criterion 4."""
    # Three missing frames = 600 ms, over the default 400 ms.
    evidence = [True] * 4 + [False] * 3 + [True] * 4
    assert run(evidence) == [(0, 3), (7, 10)]


def test_merge_gap_follows_the_setting():
    """Criterion 4."""
    evidence = [True] * 4 + [False] * 3 + [True] * 4
    assert run(evidence, pointing_merge_gap_ms=600) == [(0, 10)]
    assert run([True] * 4 + [False] + [True] * 4, pointing_merge_gap_ms=100) == [(0, 3), (5, 8)]


def test_episode_shorter_than_minimum_is_dropped():
    """Criterion 4."""
    # Two frames last 400 ms, under the default 600 ms.
    assert run([False] * 3 + [True] * 2 + [False] * 5) == []


def test_episode_exactly_at_minimum_is_kept():
    """Criterion 4."""
    # Three frames last 600 ms.
    assert run([False] * 3 + [True] * 3 + [False] * 5) == [(3, 5)]


def test_spans_are_inclusive_first_and_last_evidence_indices():
    """Criterion 4."""
    evidence = [False] * 5 + [True] * 5 + [False] * 10 + [True] * 4 + [False] * 2
    assert run(evidence) == [(5, 9), (20, 23)]


def test_short_runs_are_dropped_only_after_merging():
    """Criterion 4."""
    # Two 2-frame runs (400 ms each) merge across a 200 ms gap into 1000 ms.
    evidence = [True, True, False, True, True]
    assert run(evidence) == [(0, 4)]


def test_gap_is_measured_from_timestamps_not_indices():
    """Criterion 4."""
    evidence = [True, True, True, False, True, True, True]
    # Only one frame index apart, but 600 ms of missing time between 400 and 1200.
    stamps = [0, 200, 400, 600, 1200, 1400, 1600]
    assert episodes(evidence, stamps, INTERVAL, make_settings()) == [(0, 2), (4, 6)]


def test_no_evidence_gives_no_episodes():
    """Criterion 4."""
    assert run([False] * 10) == []
    assert run([]) == []
