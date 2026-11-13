import re
from pathlib import Path

import yaml

from audio.worker.mapping import TAG_TO_MISCUE_TYPE
from audio.worker.schemas import AudioWorkerResult, ErrorCode, WorkerMiscue

CONTRACTS = Path(__file__).resolve().parents[3] / "contracts"


def audio_example() -> dict:
    """The JSON example under '### Audio response (200)' in worker-api.md."""
    text = (CONTRACTS / "worker-api.md").read_text(encoding="utf-8")
    section = text.split("### Audio response (200)", 1)[1]
    block = re.search(r"```json\n(.*?)```", section, re.S)
    assert block is not None
    import json

    return json.loads(block.group(1))


def test_response_keys_match_worker_api_example() -> None:
    example = audio_example()
    assert set(AudioWorkerResult.model_fields) == set(example)
    assert set(WorkerMiscue.model_fields) == set(example["miscues"][0])


def test_miscue_types_are_be_miscue_types() -> None:
    spec = yaml.safe_load((CONTRACTS / "be-mai-openapi.yaml").read_text(encoding="utf-8"))
    allowed = set(spec["components"]["schemas"]["MiscueType"]["enum"])
    assert set(TAG_TO_MISCUE_TYPE.values()) <= allowed


def test_error_codes_match_worker_api() -> None:
    text = (CONTRACTS / "worker-api.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| \d{3} \| `([A-Z_]+)` \|", text, re.M))
    assert {c.value for c in ErrorCode} == documented
