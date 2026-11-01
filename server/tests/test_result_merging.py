"""Merging lane results into the AnalysisResponse (criteria 15, 16)."""

import pytest
from app.merge import merge_results, output_key_prefix
from conftest import API_KEY, valid_body
from lane_helpers import REQUEST_ID, audio_result, lane_miscue, video_result

from app.config import Settings
from app.models import AnalysisRequest, AnalysisResponse

SETTINGS = Settings(_env_file=None, api_key=API_KEY)  # type: ignore[call-arg]


def request(**overrides) -> AnalysisRequest:
    return AnalysisRequest(**valid_body(**overrides))


# output_key_prefix -----------------------------------------------------------------------------


def test_default_prefix_comes_from_settings_template():
    """criterion 15"""
    assert output_key_prefix(request(), SETTINGS) == f"analyses/{REQUEST_ID}/miscues/"


@pytest.mark.parametrize("given", ["custom/path/", "custom/path", "custom/path///"])
def test_custom_prefix_has_exactly_one_trailing_slash(given):
    """criterion 15"""
    assert output_key_prefix(request(output_key_prefix=given), SETTINGS) == "custom/path/"


def test_empty_custom_prefix_falls_back_to_default():
    """criterion 15"""
    assert output_key_prefix(request(output_key_prefix=""), SETTINGS) == (
        f"analyses/{REQUEST_ID}/miscues/"
    )


def test_default_template_without_trailing_slash_is_normalized():
    """criterion 15"""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, api_key=API_KEY, default_output_key_prefix="out/{request_id}/m"
    )
    assert output_key_prefix(request(), settings) == f"out/{REQUEST_ID}/m/"


# merge_results ---------------------------------------------------------------------------------


def test_scores_and_flags_come_from_the_right_lane():
    """criterion 16"""
    audio = audio_result(
        reading_score=77.5,
        wpm=88.0,
        is_word_by_word=True,
        is_disregard_punctuation=False,
        is_monotonous=True,
        is_hardly_audible=True,
    )
    video = video_result(is_finger_pointing=True, is_lack_expression=False)
    merged = merge_results(request(), SETTINGS, audio, video)
    assert isinstance(merged, AnalysisResponse)
    assert str(merged.request_id) == REQUEST_ID
    assert merged.reading_score == 77.5
    assert merged.wpm == 88.0
    assert merged.is_word_by_word is True
    assert merged.is_disregard_punctuation is False
    assert merged.is_monotonous is True
    assert merged.is_hardly_audible is True
    assert merged.is_finger_pointing is True
    assert merged.is_lack_expression is False


def test_video_flags_follow_the_video_result():
    """criterion 16"""
    video = video_result(is_finger_pointing=False, is_lack_expression=True)
    merged = merge_results(request(), SETTINGS, audio_result(), video)
    assert merged.is_finger_pointing is False
    assert merged.is_lack_expression is True


def test_missing_video_result_gives_false_video_flags():
    """criterion 16"""
    merged = merge_results(request(), SETTINGS, audio_result(), None)
    assert merged.is_finger_pointing is False
    assert merged.is_lack_expression is False
    assert merged.reading_score == audio_result().reading_score


def test_miscue_fields_are_copied_and_times_renamed():
    """criterion 16"""
    audio = audio_result(
        miscues=[
            lane_miscue(
                actual_phrase="cat",
                spoken_phrase="cap",
                actual_phoneme=["k", "ae", "t"],
                spoken_phoneme=["k", "ae", "p"],
                start_ms=1200,
                end_ms=1700,
                miscue_type="mispronunciation",
            )
        ]
    )
    [miscue] = merge_results(request(), SETTINGS, audio, None).miscues
    assert miscue.actual_phrase == "cat"
    assert miscue.spoken_phrase == "cap"
    assert miscue.actual_phoneme == ["k", "ae", "t"]
    assert miscue.spoken_phoneme == ["k", "ae", "p"]
    assert (miscue.read_start, miscue.read_end) == (1200, 1700)
    assert miscue.miscue_type == "mispronunciation"


def test_miscues_sorted_by_start_then_end_with_numbered_keys():
    """criterion 16"""
    audio = audio_result(
        miscues=[
            lane_miscue(actual_phrase="c", start_ms=3000, end_ms=3200),
            lane_miscue(actual_phrase="b2", start_ms=1000, end_ms=1500),
            lane_miscue(actual_phrase="a", start_ms=500, end_ms=900),
            lane_miscue(actual_phrase="b1", start_ms=1000, end_ms=1100),
        ]
    )
    miscues = merge_results(request(), SETTINGS, audio, None).miscues
    assert [m.actual_phrase for m in miscues] == ["a", "b1", "b2", "c"]
    prefix = f"analyses/{REQUEST_ID}/miscues/"
    assert [m.audio_object_key for m in miscues] == [
        f"{prefix}miscue-0001.wav",
        f"{prefix}miscue-0002.wav",
        f"{prefix}miscue-0003.wav",
        f"{prefix}miscue-0004.wav",
    ]


def test_sort_is_stable_for_identical_times():
    """criterion 16"""
    audio = audio_result(
        miscues=[
            lane_miscue(actual_phrase=name, start_ms=100, end_ms=200)
            for name in ("first", "second", "third")
        ]
    )
    miscues = merge_results(request(), SETTINGS, audio, None).miscues
    assert [m.actual_phrase for m in miscues] == ["first", "second", "third"]


def test_custom_prefix_is_used_in_keys():
    """criterion 16"""
    merged = merge_results(
        request(output_key_prefix="custom/path"), SETTINGS, audio_result(), video_result()
    )
    assert [m.audio_object_key for m in merged.miscues] == ["custom/path/miscue-0001.wav"]


def test_zero_miscues_gives_empty_list():
    """criterion 16"""
    merged = merge_results(request(), SETTINGS, audio_result(miscues=[]), video_result())
    assert merged.miscues == []
