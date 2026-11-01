from conftest import valid_body

from app.models import AnalysisResponse

REQUEST_ID = "3f6c1c7e-9a0d-4a53-8f55-5d2d4f0c8a11"


def analyze(client, auth, **overrides):
    return client.post("/v1/analyses", json=valid_body(**overrides), headers=auth)


def test_stub_response(client, auth):
    response = analyze(client, auth)
    assert response.status_code == 200
    assert response.headers["X-MAI-Stub"] == "true"
    parsed = AnalysisResponse.model_validate(response.json())
    assert str(parsed.request_id) == REQUEST_ID
    assert parsed.reading_score == 85.0
    assert parsed.wpm == 60.0
    assert parsed.is_disregard_punctuation is True
    assert not any(
        [
            parsed.is_word_by_word,
            parsed.is_monotonous,
            parsed.is_lack_expression,
            parsed.is_hardly_audible,
            parsed.is_finger_pointing,
        ]
    )


def test_default_prefix_used(client, auth):
    keys = [m["audio_object_key"] for m in analyze(client, auth).json()["miscues"]]
    assert keys == [
        f"analyses/{REQUEST_ID}/miscues/miscue-0001.wav",
        f"analyses/{REQUEST_ID}/miscues/miscue-0002.wav",
    ]


def test_custom_prefix_with_and_without_trailing_slash(client, auth):
    for prefix in ("custom/path/", "custom/path"):
        keys = [
            m["audio_object_key"]
            for m in analyze(client, auth, output_key_prefix=prefix).json()["miscues"]
        ]
        assert keys == ["custom/path/miscue-0001.wav", "custom/path/miscue-0002.wav"]


def test_miscues_come_from_passage(client, auth):
    first, second = analyze(client, auth).json()["miscues"]
    assert first["miscue_type"] == "substitution"
    assert first["actual_phrase"] == "Pam"
    assert first["spoken_phrase"] == "Pa"
    assert first["actual_phoneme"] == ["P", "a", "m"]
    assert (first["read_start"], first["read_end"]) == (500, 1000)
    assert second["miscue_type"] == "omission"
    assert second["actual_phrase"] == "a"
    assert second["spoken_phrase"] == ""
    assert second["spoken_phoneme"] == []
    assert (second["read_start"], second["read_end"]) == (2000, 2400)


def test_punctuation_stripped_and_inner_apostrophe_kept(client, auth):
    miscues = analyze(client, auth, passage_content="“Don't” stop, now!").json()["miscues"]
    assert [m["actual_phrase"] for m in miscues] == ["Don't", "now"]


def test_one_letter_word_spoken_as_uh(client, auth):
    miscue = analyze(client, auth, passage_content="A").json()["miscues"][0]
    assert miscue["spoken_phrase"] == "uh"


def test_one_word_passage_gives_one_miscue(client, auth):
    assert len(analyze(client, auth, passage_content="Hello.").json()["miscues"]) == 1


def test_punctuation_only_passage_gives_no_miscues(client, auth):
    assert analyze(client, auth, passage_content="...").json()["miscues"] == []


def test_deterministic(client, auth):
    assert analyze(client, auth).json() == analyze(client, auth).json()
