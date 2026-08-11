import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from api_contracts import (
    BatchPreviewRequestContract,
    ErrorEnvelopeContract,
    RepairApplyRequestContract,
    RepairCatalogContract,
    RepairPreviewRequestContract,
    ResultsContract,
    ScanRequestContract,
    StatusContract,
)


ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def contracts():
    return json.loads(
        (ROOT / "tests" / "fixtures" / "api_contracts.json").read_text(
            encoding="utf-8"
        )
    )


def test_canonical_response_and_error_fixtures_match_typed_contracts(contracts):
    responses = contracts["responses"]
    assert StatusContract.model_validate(responses["status"]).stage == "complete"
    assert ResultsContract.model_validate(responses["results"]).total == 1
    catalog = RepairCatalogContract.model_validate(responses["repair_catalog"])
    assert catalog.items[0].rule_code == "chart.duplicate-note"

    structured = ErrorEnvelopeContract.model_validate(
        contracts["errors"]["structured"]
    )
    assert structured.detail.code == "stale_preview"
    assert structured.detail.file_state == "unchanged"
    assert structured.detail.retryable is True
    assert structured.detail.next_action == "review_repair"


def test_canonical_mutation_requests_match_strict_contracts(contracts):
    requests = contracts["requests"]
    assert ScanRequestContract.model_validate(requests["scan"]).scope == "folder"
    assert RepairPreviewRequestContract.model_validate(
        requests["repair_preview"]
    ).rule_code == "chart.duplicate-note"
    assert RepairApplyRequestContract.model_validate(
        requests["repair_apply"]
    ).plan_id == "synthetic-plan-0001"
    assert BatchPreviewRequestContract.model_validate(
        requests["batch_preview"]
    ).include_preview_repairs is False


def test_mutation_contracts_reject_unknown_or_unsafe_shapes(contracts):
    invalid = {**contracts["requests"]["scan"], "worker_count": 2}
    with pytest.raises(ValidationError):
        ScanRequestContract.model_validate(invalid)
    with pytest.raises(ValidationError):
        ScanRequestContract.model_validate({"scope": "file", "max_workers": 0})
