"""Post-pass rule for Lane B: rows Lane B judged by sound keep their tag; the rest are unchanged."""

from audio.alignment.labels import WordLabeler
from audio.models import AlignedWord, PhoneticEvidence, new_row
from audio.tagging.mispronunciation import MispronunciationTagger


def evidence(check) -> PhoneticEvidence:
    return PhoneticEvidence(
        expected_phonemes=["k", "ɔ", "s", "t"],
        heard_phonemes=["k", "ɔ", "s", "t"],
        d_exp=0.1,
        d_hyp=None,
        check=check,
    )


def sub_row(**extra) -> AlignedWord:
    # coast -> cost is close in spelling: the letter-based tagger calls it a mispronunciation
    row = new_row("substitution", passage_indices=[0], passage_words=["coast"], spoken=["cost"])
    row.update(extra)  # type: ignore[typeddict-item]
    return row


def tag(row: AlignedWord) -> AlignedWord:
    (out,) = MispronunciationTagger(WordLabeler()).tag([row], frozenset())
    return out


def test_letter_tagger_alone_turns_a_close_substitution_into_mispronunciation() -> None:
    assert tag(sub_row())["tag"] == "mispronunciation"


def test_a_substitution_lane_b_judged_stays_a_substitution_even_when_spelling_is_close() -> None:
    row = sub_row(phonetic=evidence("done"))
    assert tag(row) is row
    assert tag(row)["tag"] == "substitution"


def test_a_row_lane_b_skipped_still_gets_the_letter_based_tag() -> None:
    assert tag(sub_row(phonetic=evidence("unverifiable")))["tag"] == "mispronunciation"
    assert tag(sub_row(phonetic=evidence("skipped")))["tag"] == "mispronunciation"
