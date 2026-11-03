"""Lane B at the shipped defaults against the hand labels (plans/phase-B6b.md).

The replay needs the run cache (student audio derivatives, not in git); without it that test is
skipped. The labels file is in git and is always checked.
"""

import pytest

from audio.config import AudioSettings
from devtools.replay import CACHE_DIR, Replayer, load_all
from devtools.score_labels import LABELS_CSV, load_labels, score_config

# Recorded in plans/phase-B6b.md "Results" (Lane A alone: selection 17 / 2, held-out 4 / 1).
MAX_FP_FN = {"selection": (10, 3), "heldout": (1, 2)}
MIN_EXACT = {"selection": 472, "heldout": 244}


def test_labels_file_is_complete():
    labels = load_labels(LABELS_CSV)
    assert len(labels) == 735
    assert {lab.split for lab in labels.values()} == {"selection", "heldout"}
    assert sum(lab.unsure for lab in labels.values()) == 8


@pytest.mark.espeak
def test_lane_b_defaults_do_not_regress_against_the_labels(espeak_g2p):
    caches = load_all(CACHE_DIR)
    if not caches:
        pytest.skip("no run cache in audio/devtools/out/cache (student audio is not in git)")
    settings = AudioSettings(_env_file=None, PHONETIC_ENABLED=True)
    total, _ = score_config(Replayer(settings), caches, load_labels(), {})
    for split, (max_fp, max_fn) in MAX_FP_FN.items():
        n = total[(split, False)]
        assert n.fp <= max_fp, split
        assert n.fn <= max_fn, split
        assert n.exact >= MIN_EXACT[split], split
