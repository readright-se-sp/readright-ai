from pathlib import Path

import yaml

from audio.types import WordLabel

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "be-mai-openapi.yaml"


def test_word_labels_match_contract_miscue_types():
    spec = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract_types = set(spec["components"]["schemas"]["MiscueType"]["enum"])
    assert {label.value for label in WordLabel} - {"correct"} == contract_types
