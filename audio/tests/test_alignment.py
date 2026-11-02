from audio.alignment.cleanup import TranscriptCleaner
from audio.alignment.needleman_wunsch import NeedlemanWunschAligner
from audio.alignment.repeats import RepeatFolder
from audio.alignment.swaps import SwapDetector
from audio.alignment.text import damerau, levenshtein, normalize, tokenize_passage
from audio.models import Column, Repeat, Swap, Token
from audio.pipeline import AudioPipeline
from tests.conftest import spoken


def _tokens(text: str) -> list[Token]:
    return [Token(i, w, w, None, None) for i, w in enumerate(text.split())]


def _words(text: str) -> list[str]:
    return text.split()


def _ops(passage: str, heard: str) -> list[str]:
    cols = NeedlemanWunschAligner().align(passage.split(), _tokens(heard))
    return [c.op for c in cols]


def test_damerau_counts_a_neighbour_letter_swap_as_one_edit() -> None:
    assert (levenshtein("from", "form"), damerau("from", "form")) == (2, 1)
    assert (levenshtein("girls", "gril"), damerau("girls", "gril")) == (3, 2)


def test_normalize_strips_punctuation_apostrophes_and_spells_numbers() -> None:
    assert normalize("Mia's") == ["mias"]
    assert normalize("the...") == ["the"]
    assert normalize("by-product") == ["by", "product"]
    assert normalize("21") == ["twenty", "one"]
    assert tokenize_passage("About 20 typhoons!") == ["about", "twenty", "typhoons"]


def test_nw_keeps_struggling_reader_in_order() -> None:
    """The SequenceMatcher failure case: NW pairs words 1-to-1 instead of mass omit/insert."""
    ops = _ops("the boy saw the dog and the dog saw the boy", "a boy see a dog an a dog see the boy")
    assert "del" not in ops and "ins" not in ops


def test_nw_skipped_phrase_is_one_contiguous_run() -> None:
    ops = _ops("known as chlorophyll chlorophyll absorbs energy the plant", "known as chlorophyll the plant")
    assert ops == ["match", "match", "match", "del", "del", "del", "match", "match"]


def test_nw_rejects_gap_extend_that_lets_gaps_beat_mismatches() -> None:
    try:
        NeedlemanWunschAligner(gap_open=0.9, gap_extend=0.5)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_nw_rereads_a_repeated_phrase_instead_of_inserting_it() -> None:
    assert _ops("they can produce", "they can they can produce") == ["match", "match", "rep", "rep", "match"]


def test_nw_leaves_doubling_that_is_in_the_passage() -> None:
    assert _ops("he had had enough", "he had had enough") == ["match"] * 4


def test_nw_rejects_repeat_or_swap_cost_that_ties_or_beats_gaps() -> None:
    for name in ("repeat_open", "swap_cost"):
        for bad in (0.0, 0.9):
            try:
                NeedlemanWunschAligner(**{name: bad})
            except ValueError:
                continue
            raise AssertionError(f"expected ValueError for {name}={bad}")


def test_nw_transposes_neighbours_and_exchanges_across_one() -> None:
    assert _ops("she was happy", "she happy was") == ["match", "swp", "swp"]
    assert _ops("fresh fish ripe", "ripe fish fresh") == ["swp", "mid", "swp"]
    assert _ops("the cat sat on the mat", "the mat sat on the cat") == ["match", "swp", "mid", "mid", "mid", "swp"]


def test_nw_needs_exact_words_for_a_distant_exchange() -> None:
    """Close by, 'happy wos' is a swap; four words apart, cat/cot read as cot/cut are two misreadings."""
    assert "swp" not in _ops("the cat sat on the cot", "the cot sat on the cut")


def test_repeat_fold_keeps_last_copy() -> None:
    passage = _words("they can produce")
    items = RepeatFolder().fold(NeedlemanWunschAligner().align(passage, _tokens("they can they can produce")), passage)
    repeat = items[0]
    assert isinstance(repeat, Repeat)
    assert [t.id for t in repeat.heard] == [0, 1]
    assert [c.h.id for c in repeat.columns if c.h is not None] == [2, 3]


def test_repeat_said_three_times_is_one_error(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("they can they can they can produce"), "they can produce")
    reps = [w for w in result["words"] if w["tag"] == "repetition"]
    assert [w["spoken_word"] for w in reps] == ["they can they can"]
    assert result["score"]["error_count"] == 1


def test_long_repeated_phrase_is_one_error(lane: AudioPipeline) -> None:
    passage = "the kite flies high above the houses"
    result = lane.analyze(spoken("the kite flies high above the the kite flies high above the houses"), passage)
    assert [w["tag"] for w in result["words"]] == ["repetition"] + ["match"] * 7
    assert result["score"]["error_count"] == 1


def test_overlapping_restarts_are_one_repetition(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("ben ben runs ben runs fast"), "ben runs fast")
    reps = [w for w in result["words"] if w["tag"] == "repetition"]
    assert [w["spoken_word"] for w in reps] == ["ben ben runs"]
    assert result["score"]["error_count"] == 1


def test_near_copy_repeat_is_repetition_plus_self_correction(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("ben rans ben runs fast"), "ben runs fast")
    assert [(w["tag"], w["spoken_word"], w["attempts"]) for w in result["words"]] == [
        ("repetition", "ben", []),
        ("match", "ben", []),
        ("self_correction", "runs", ["rans"]),
        ("match", "fast", []),
    ]
    assert result["score"]["error_count"] == 1


def test_swap_detected_from_both_nw_shapes() -> None:
    passage = _words("their own food this")
    cols = NeedlemanWunschAligner().align(passage, _tokens("their food own this"))
    items = SwapDetector().detect(cols, passage)
    assert any(isinstance(i, Swap) and i.p == 1 for i in items)

    passage = _words("as we know it would")
    cols = NeedlemanWunschAligner().align(passage, _tokens("as we it know would"))
    items = SwapDetector().detect(cols, passage)
    assert any(isinstance(i, Swap) and i.p == 2 for i in items)


def test_swap_next_to_skipped_word_leaves_clean_omission() -> None:
    """'fresh fish ripe' read 'fish fresh': NW gives del/match/sub(ripe->fresh)."""
    passage = _words("of fresh fish ripe mangoes")
    cols = NeedlemanWunschAligner().align(passage, _tokens("of fish fresh mangoes"))
    items = SwapDetector().detect(cols, passage)
    assert any(isinstance(i, Swap) and i.p == 1 for i in items)
    assert [i.p for i in items if isinstance(i, Column) and i.op == "del"] == [3]


def test_cleanup_drops_fillers_and_false_starts_but_never_passage_words() -> None:
    passage = ["releases", "oxygen", "and", "pandesal", "a"]
    result = TranscriptCleaner().clean(spoken("um releases ox- oxygen uh and pan de sal a"), passage)
    assert [t.norm for t in result.tokens] == ["releases", "oxygen", "and", "pandesal", "a"]
    assert sorted(s.row["tag"] for s in result.side_events) == ["false_start", "filler", "filler"]


def test_crisperwhisper_markers_and_joined_names(lane: AudioPipeline) -> None:
    """Verbatim CrisperWhisper text: [UM] tags, 'Th-' cut-offs, 'Mangador' for 'Mang Ador'."""
    passage = "Every Saturday, Manuel goes with his father, Mang Ador. They always pass by Aling Juaning's stall."
    heard = "[UM] Every Saturday Saturday Manuel goes with his father, Mangador. Th- they always pass by Aling Juaning's stall. [LAUGH]"
    words = lane.analyze(spoken(heard), passage, ["mang", "ador", "aling", "juanings"])["words"]
    assert [(w["tag"], w["spoken_word"]) for w in words if w["tag"] != "match"] == [
        ("filler", "um"),
        ("repetition", "saturday"),
        ("false_start", "th"),
        ("filler", "laugh"),
    ]
    assert [w["spoken_word"] for w in words if w["passage_word"] in ("mang", "ador")] == ["mang", "ador"]
