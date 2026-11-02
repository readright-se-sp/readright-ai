from audio.alignment.labels import WordLabeler
from audio.models import AlignedWord, Tag, new_row
from audio.pipeline import AudioPipeline
from audio.tagging.attempts import AttemptJudge
from tests.conftest import spoken

_NONE: frozenset[str] = frozenset()


def test_labeler_rules() -> None:
    lab = WordLabeler()
    assert lab.label("chloroplasts", "chloroplast", _NONE) == ("mispronunciation", "ending")
    assert lab.label("coast", "cost", _NONE).tag == "mispronunciation"
    assert lab.label("bench", "chair", _NONE).tag == "substitution"
    assert lab.label("anansi", "nancy", frozenset({"anansi"})) == ("match", "proper_noun")


def test_lane_a_rows_have_the_planned_shape(lane: AudioPipeline) -> None:
    """(passage_word, spoken_word, start, end, tag) — nullable words for omission/insertion."""
    words = lane.analyze(spoken("the very dog"), "the small dog")["words"]
    assert [(w["passage_word"], w["spoken_word"], w["tag"]) for w in words] == [
        ("the", "the", "match"),
        ("small", "very", "substitution"),
        ("dog", "dog", "match"),
    ]
    words = lane.analyze(spoken("the dog"), "the small dog")["words"]
    assert words[1]["passage_word"] == "small" and words[1]["spoken_word"] is None
    assert words[1]["start"] is None and words[1]["tag"] == "omission"


def test_reversal_is_tagged_in_post_pass(lane: AudioPipeline) -> None:
    words = lane.analyze(spoken("ben saw happy"), "Ben was happy")["words"]
    assert words[1]["tag"] == "reversal" and words[1]["counted"]


def test_self_correction_is_recorded_not_counted(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("at the pork park"), "at the park")
    sc = [w for w in result["words"] if w["tag"] == "self_correction"]
    assert len(sc) == 1 and sc[0]["attempts"] == ["pork"] and not sc[0]["counted"]
    assert result["score"]["error_count"] == 0


def test_several_tries_at_one_word_are_one_self_correction(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("at the pork perk park"), "at the park")
    sc = [w for w in result["words"] if w["tag"] == "self_correction"]
    assert len(sc) == 1 and sc[0]["attempts"] == ["pork", "perk"]
    assert result["score"]["error_count"] == 0


def test_reversed_try_then_right_word_is_self_correction(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("the saw was happy"), "the was happy")
    assert [(w["tag"], w["attempts"]) for w in result["words"] if w["tag"] != "match"] == [("self_correction", ["saw"])]
    assert result["score"]["error_count"] == 0


def test_misread_then_reread_is_not_a_jump_back_with_a_stray_word(lane: AudioPipeline) -> None:
    """Real recording: 'yet' misread as 'yelled', then re-read. Not 'Jackie' standing in for 'Mom'."""
    passage = "Time for school, yelled Mom. And yet Jacky did not get up."
    heard = "time for school, yelled Mom. And yelled Jackie. And yet, Jackie did not get up."
    rows = lane.analyze(spoken(heard), passage, ["Jacky"])["words"]
    assert [(w["tag"], w["passage_word"], w["spoken_word"], w["attempts"]) for w in rows if w["tag"] != "match"] == [
        ("repetition", None, "and jackie", []),
        ("self_correction", "yet", "yet", ["yelled"]),
    ]
    assert next(w for w in rows if w["passage_word"] == "mom")["tag"] == "match"


def test_repeat_with_a_word_between_is_one_insertion_not_a_repetition(lane: AudioPipeline) -> None:
    """Real recording: 'I… and I will' for 'I will'. Not said again straight away, so 'I and' is extra words."""
    result = lane.analyze(spoken("get up now. I and I will."), "get up now. I will!")
    assert [(w["tag"], w["spoken_word"], w["counted"]) for w in result["words"] if w["tag"] != "match"] == [
        ("insertion", "i", True),
        ("insertion", "and", False),
    ]
    assert result["score"]["error_count"] == 1


def test_contiguous_insertions_are_one_error(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("i will get up now i think this good"), "i will get up now")
    assert [w["counted"] for w in result["words"] if w["tag"] == "insertion"] == [True, False, False, False]
    assert result["score"]["counts"]["insertion"] == 1 and result["score"]["error_count"] == 1


def test_new_sentence_after_the_reading_is_off_text_and_not_in_wpm(lane: AudioPipeline) -> None:
    """Real recording: '…I will. I think this good.' The child finished, then commented."""
    result = lane.analyze(spoken("get up now. I will. I think this good."), "get up now. I will!")
    assert [(w["tag"], w["counted"]) for w in result["words"] if w["tag"] != "match"] == [("off_text", False)] * 4
    assert result["score"]["error_count"] == 0 and result["score"]["counts"]["off_text"] == 4
    reading_only = lane.analyze(spoken("get up now. I will."), "get up now. I will!")["score"]["wpm"]
    assert result["score"]["wpm"] == reading_only


def test_new_sentence_before_the_reading_is_off_text_and_not_in_wpm(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("Okay, I will read. A mouse and a cat lived here."), "A mouse and a cat lived here.")
    assert [(w["tag"], w["spoken_word"]) for w in result["words"] if w["tag"] != "match"] == [
        ("off_text", "okay"), ("off_text", "i"), ("off_text", "will"), ("off_text", "read"),
    ]
    assert result["score"]["error_count"] == 0
    reading_only = lane.analyze(spoken("A mouse and a cat lived here."), "A mouse and a cat lived here.")["score"]["wpm"]
    assert result["score"]["wpm"] == reading_only


def test_word_running_into_the_first_sentence_is_still_an_insertion(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("The a mouse and a cat lived here."), "A mouse and a cat lived here.")
    assert [w["tag"] for w in result["words"] if w["tag"] != "match"] == ["insertion"]


def test_words_continuing_the_last_sentence_are_still_insertions(lane: AudioPipeline) -> None:
    """Sample 5: '…would not be possible at all.' 'at all' was added to the sentence."""
    result = lane.analyze(spoken("would not be possible at all."), "would not be possible.")
    assert [w["tag"] for w in result["words"] if w["tag"] != "match"] == ["insertion", "insertion"]
    assert result["score"]["error_count"] == 1


def test_contiguous_repeat_is_still_a_repetition(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("they can um they can produce"), "they can produce")
    assert [(w["tag"], w["spoken_word"]) for w in result["words"] if w["tag"] in ("repetition", "insertion")] == [
        ("repetition", "they can"),
    ]


def test_attempt_judge_only_judges_rows_with_attempts() -> None:
    def row(tag: Tag, attempts: list[str]) -> AlignedWord:
        return new_row(tag, passage_indices=[0], passage_words=["park"], spoken=["park" if tag == "match" else "pork"], attempts=attempts)

    judged = AttemptJudge().tag([row("match", ["pork"]), row("substitution", ["park"]), row("match", [])])
    assert [(r["tag"], r["detail"], r["counted"]) for r in judged] == [
        ("self_correction", None, False),
        ("substitution", "miscorrection", True),
        ("match", None, False),
    ]


def test_miscorrection_counts_final_attempt(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("storm signals singles so"), "storm signals so")
    row = next(w for w in result["words"] if w["passage_word"] == "signals")
    assert row["tag"] == "mispronunciation" and row["detail"] == "miscorrection"
    assert result["score"]["error_count"] == 1


def test_mispronunciation_is_derived_from_substitution(lane: AudioPipeline) -> None:
    words = lane.analyze(spoken("she ate two tomato"), "she ate two tomatoes")["words"]
    assert (words[3]["tag"], words[3]["detail"]) == ("mispronunciation", "ending")


def test_swap_is_two_rows_one_error(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("she happy was to pet"), "she was happy to pet")
    swap = [w for w in result["words"] if w["tag"] == "swap"]
    assert [(w["passage_word"], w["spoken_word"], w["counted"]) for w in swap] == [
        ("was", "was", True),
        ("happy", "happy", False),
    ]
    assert swap[0]["move_id"] == swap[1]["move_id"] == 1
    assert result["score"]["counts"]["swap"] == 1 and result["score"]["error_count"] == 1


def test_swap_with_a_misread_half_is_swap_plus_mispronunciation(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("she happy wos to pet"), "she was happy to pet")
    rows = [(w["tag"], w["passage_word"], w["spoken_word"], w["counted"]) for w in result["words"] if w["move_id"]]
    assert rows == [("mispronunciation", "was", "wos", True), ("swap", "happy", "happy", True)]
    counts = result["score"]["counts"]
    assert counts["swap"] == 1 and counts["mispronunciation"] == 1 and result["score"]["error_count"] == 2


def test_swap_with_a_reversed_half_is_swap_plus_reversal(lane: AudioPipeline) -> None:
    """Decided: a misread swap half is its own miscue, whatever the misreading ('saw' read backwards)."""
    result = lane.analyze(spoken("she happy saw to pet"), "she was happy to pet")
    counts = result["score"]["counts"]
    assert (counts["swap"], counts["reversal"], result["score"]["error_count"]) == (1, 1, 2)


def test_two_misread_halves_are_two_misreadings_not_a_swap(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("she hapy wos to pet"), "she was happy to pet")
    assert result["score"]["counts"]["swap"] == 0


def test_exchange_across_one_word_is_one_swap(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("ripe fish fresh mangoes"), "fresh fish ripe mangoes")
    assert [(w["tag"], w["passage_word"], w["spoken_word"]) for w in result["words"]] == [
        ("swap", "fresh", "fresh"),
        ("match", "fish", "fish"),
        ("swap", "ripe", "ripe"),
        ("match", "mangoes", "mangoes"),
    ]
    assert result["score"]["counts"]["swap"] == 1 and result["score"]["error_count"] == 1


def test_exchange_with_words_between_is_one_swap(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("the mat sat on the cat"), "the cat sat on the mat")
    swap = [(w["passage_word"], w["spoken_word"]) for w in result["words"] if w["tag"] == "swap"]
    assert swap == [("cat", "cat"), ("mat", "mat")]
    assert result["score"]["error_count"] == 1


def test_swap_said_twice_is_repetition_plus_swap_with_correction(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("she happy wos happy was to pet"), "she was happy to pet")
    assert [(w["tag"], w["spoken_word"], w["attempts"]) for w in result["words"] if w["tag"] != "match"] == [
        ("repetition", "happy", []),
        ("swap", "was", ["wos"]),
        ("swap", "happy", []),
    ]
    assert result["score"]["error_count"] == 2


def test_swap_read_twice_the_same_is_repetition_plus_swap(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("she happy was happy was to pet"), "she was happy to pet")
    assert [(w["tag"], w["spoken_word"]) for w in result["words"] if w["tag"] != "match"] == [
        ("repetition", "happy was"),
        ("swap", "was"),
        ("swap", "happy"),
    ]
    assert result["score"]["error_count"] == 2


def test_swap_right_then_misread_on_repeat_is_three_errors(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("she happy was happy wos to pet"), "she was happy to pet")
    row = next(w for w in result["words"] if w["passage_word"] == "was")
    assert (row["tag"], row["spoken_word"], row["attempts"], row["detail"]) == (
        "mispronunciation", "wos", ["was"], "miscorrection",
    )
    counts = result["score"]["counts"]
    assert (counts["repetition"], counts["swap"], result["score"]["error_count"]) == (1, 1, 3)


def test_swap_far_apart_is_one_swap_but_short_words_need_an_anchor(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("the dog sat on a mat by the cat"), "the cat sat on a mat by the dog")
    assert result["score"]["counts"]["swap"] == 1 and result["score"]["error_count"] == 1
    passage = "a big dog sat on the mat by the cat"  # 'a' and 'the' crossed 7 words apart: chance
    result = lane.analyze(spoken("the big dog sat on the mat by a cat"), passage)
    assert result["score"]["counts"]["swap"] == 0


def test_move_that_would_add_errors_is_not_made(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("the cot sat on the cut"), "the cat sat on the cot")
    assert [w["tag"] for w in result["words"] if w["tag"] != "match"] == ["mispronunciation", "mispronunciation"]
    assert result["score"]["error_count"] == 2


def test_unrelated_word_is_not_half_of_a_swap(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("she happy dog to pet"), "she was happy to pet")
    assert result["score"]["counts"]["swap"] == 0


def test_moved_word_is_two_halves_one_error(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("with see mang ador father"), "with his father Mang Ador")
    moved = [w for w in result["words"] if w["tag"] == "moved"]
    assert [(w["passage_word"], w["spoken_word"]) for w in moved] == [("father", None), (None, "father")]
    assert moved[0]["move_id"] == moved[1]["move_id"] and moved[0]["start"] is None
    assert result["score"]["error_count"] == 2  # moved father + his->see


def test_moved_phrase_shares_one_move_id(lane: AudioPipeline) -> None:
    passage = "every day the old man walks to the river and sits by the water"
    result = lane.analyze(spoken("every day walks to the river the old man and sits by the water"), passage)
    moved = [w for w in result["words"] if w["tag"] == "moved"]
    assert {w["move_id"] for w in moved} == {1} and len(moved) == 6
    assert result["score"]["counts"]["moved"] == 1 and result["score"]["error_count"] == 1


def test_misread_word_in_moved_phrase_is_moved_plus_mispronunciation(lane: AudioPipeline) -> None:
    passage = "every day the old man walks to the river and sits"
    result = lane.analyze(spoken("every day walks to the river the old men and sits"), passage)
    misread = [w for w in result["words"] if w["tag"] == "mispronunciation"]
    assert [(w["passage_word"], w["spoken_word"], w["move_id"]) for w in misread] == [("man", "men", 1)]
    counts = result["score"]["counts"]
    assert counts["moved"] == 1 and counts["mispronunciation"] == 1 and result["score"]["error_count"] == 2


_FAR = "the farmer went to the big market in town and he sold his fresh vegetables to many happy people there"


def test_far_move_of_a_long_word_is_one_error(lane: AudioPipeline) -> None:
    heard = "the went to the big market in town and he sold his farmer fresh vegetables to many happy people there"
    result = lane.analyze(spoken(heard), _FAR)
    assert result["score"]["counts"]["moved"] == 1 and result["score"]["error_count"] == 1


def test_far_move_of_a_short_word_is_not_trusted(lane: AudioPipeline) -> None:
    heard = "the farmer went to the market in town and he sold his fresh vegetables to many happy people big there"
    assert lane.analyze(spoken(heard), _FAR)["score"]["counts"]["moved"] == 0


def test_flipped_or_mirrored_word_is_reversal_other_letter_order_is_transposition(lane: AudioPipeline) -> None:
    def tag(passage_word: str, heard: str) -> str:
        return lane.analyze(spoken(f"the {heard} is here"), f"the {passage_word} is here")["words"][1]["tag"]

    assert [tag(p, h) for p, h in (("was", "saw"), ("pot", "top"), ("on", "no"))] == ["reversal"] * 3
    assert tag("bad", "dad") == "mispronunciation"  # one letter changed, not read backwards
    assert [tag(p, h) for p, h in (("from", "form"), ("animal", "aminal"), ("girls", "gril"))] == [
        "letter_transposition"
    ] * 3


def test_every_passage_word_in_exactly_one_row(lane: AudioPipeline) -> None:
    passage = "the cat sat on the mat and the dog ran"
    result = lane.analyze(spoken("the the cat um sat mat on and dog the ran fast"), passage)
    covered = sorted(i for w in result["words"] for i in w["passage_indices"])
    assert covered == list(range(len(passage.split())))


def test_side_rows_appear_before_the_word_they_precede(lane: AudioPipeline) -> None:
    result = lane.analyze(spoken("the plant the plant combines"), "the plant combines")
    assert [w["tag"] for w in result["words"]] == ["repetition", "match", "match", "match"]
