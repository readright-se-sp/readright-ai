from app.pointing import count_sweeps
from pointing_helpers import sawtooth_xs, timestamps
from video_helpers import make_settings


def sweeps(xs, **overrides):
    return count_sweeps(xs, timestamps(len(xs)), make_settings(**overrides))


def test_three_slow_left_to_right_sweeps_with_fast_returns_count_three():
    """Criterion 7."""
    assert sweeps(sawtooth_xs(3)) == 3


def test_three_mirrored_sweeps_count_three():
    """Criterion 7."""
    assert sweeps(sawtooth_xs(3, mirrored=True)) == 3


def test_single_sweep_counts_one():
    """Criterion 7."""
    assert sweeps(sawtooth_xs(1)) == 1


def test_jitter_around_a_fixed_point_is_not_a_sweep():
    """Criterion 7."""
    xs = [0.5 + (0.005 if i % 2 else -0.005) for i in range(40)]
    assert sweeps(xs) == 0


def test_one_slow_drift_without_return_is_not_a_sweep():
    """Criterion 7."""
    xs = [0.1 + 0.02 * i for i in range(40)]
    assert sweeps(xs) == 0


def test_sweep_interrupted_by_missing_hand_is_not_counted_across_the_gap():
    """Criterion 7."""
    whole = sawtooth_xs(1)
    assert sweeps(whole) == 1
    interrupted = list(whole)
    interrupted[5] = None
    assert sweeps(interrupted) == 0


def test_sweep_too_short_in_distance_is_not_counted():
    """Criterion 7."""
    xs = [0.5 + 0.005 * k for k in range(11)] + [0.5] * 3
    assert sweeps(xs) == 0


def test_sweep_too_fast_is_not_counted():
    """Criterion 7."""
    xs = [0.2, 0.4, 0.6, 0.2, 0.2, 0.2]
    assert sweeps(xs) == 0


def test_sweep_thresholds_follow_the_settings():
    """Criterion 7."""
    xs = sawtooth_xs(1)
    assert sweeps(xs, sweep_min_dx=0.5) == 0
    assert sweeps(xs, sweep_min_ms=5000) == 0


def test_no_return_within_the_return_window_is_not_a_sweep():
    """Criterion 7."""
    ramp = [0.2 + 0.04 * k for k in range(11)]
    slow_return = ramp + [0.595] * 10 + [0.2] * 3  # stays at the extreme for 2 s
    assert sweeps(slow_return, sweep_return_max_ms=1000) == 0


def test_empty_and_all_missing_tracks_have_no_sweeps():
    """Criterion 7."""
    assert sweeps([]) == 0
    assert sweeps([None] * 10) == 0
