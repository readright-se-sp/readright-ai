from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import valid_body
from jsonschema import Draft202012Validator

from app import __version__
from app.models import (
    AnalysisRequest,
    AnalysisResponse,
    ErrorCode,
    Miscue,
    MiscueType,
)

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "be-mai-openapi.yaml"


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def schema(contract: dict[str, Any], name: str) -> dict[str, Any]:
    return contract["components"]["schemas"][name]


def validator(contract: dict[str, Any], name: str) -> Draft202012Validator:
    """Validator for a component schema; `#/components/...` refs resolve against the document."""

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(contract_lookup(node["$ref"]))
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    def contract_lookup(ref: str) -> Any:
        target: Any = contract
        for part in ref.removeprefix("#/").split("/"):
            target = target[part]
        return target

    return Draft202012Validator(resolve(schema(contract, name)))


def test_miscue_type_values(contract):
    assert {m.value for m in MiscueType} == set(schema(contract, "MiscueType")["enum"])


def test_error_code_values(contract):
    expected = set(schema(contract, "ErrorBody")["properties"]["code"]["enum"])
    assert {c.value for c in ErrorCode} == expected


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("AnalysisRequest", AnalysisRequest),
        ("AnalysisResponse", AnalysisResponse),
        ("Miscue", Miscue),
    ],
)
def test_model_fields_match_contract(contract, name, model):
    spec = schema(contract, name)
    assert set(model.model_fields) == set(spec["properties"])
    required = {f for f, info in model.model_fields.items() if info.is_required()}
    assert required == set(spec["required"])


def test_analysis_response_validates_against_contract(contract, client, auth):
    response = client.post("/v1/analyses", json=valid_body(), headers=auth)
    validator(contract, "AnalysisResponse").validate(response.json())


def test_400_validates_against_error_response(contract, client, auth):
    response = client.post("/v1/analyses", json={}, headers=auth)
    assert response.status_code == 400
    validator(contract, "ErrorResponse").validate(response.json())


def test_version_matches_contract(contract):
    assert __version__ == contract["info"]["version"]
