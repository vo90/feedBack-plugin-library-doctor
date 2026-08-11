"""Typed API boundary contracts for Library Doctor.

Response models deliberately allow additive fields. Mutation request models are
strict so misspelled safety inputs fail before a service can run.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _ResponseContract(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class _RequestContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScanSummaryContract(_ResponseContract):
    total: int = Field(ge=0)
    errors: int = Field(default=0, ge=0)
    warnings: int = Field(default=0, ge=0)
    reviews: int = Field(default=0, ge=0)


class StatusContract(_ResponseContract):
    stage: str
    running: bool
    summary: ScanSummaryContract
    scan_current: bool | None = None
    repairing: bool = False
    batch: dict[str, Any] | None = None
    last_scan: dict[str, Any] | None = None


class FindingContract(_ResponseContract):
    code: str
    severity: str
    message: str
    category: str | None = None
    affected_count: int = Field(default=1, ge=0)


class PackageResultContract(_ResponseContract):
    package: str
    title: str
    artist: str = ""
    findings: list[FindingContract]
    features: dict[str, Any]


class ResultsContract(_ResponseContract):
    total: int = Field(ge=0)
    items: list[PackageResultContract]
    limit: int | None = Field(default=None, ge=1)
    offset: int | None = Field(default=None, ge=0)


class RepairDefinitionContract(_ResponseContract):
    rule_code: str
    action_kind: str
    source_kind: str
    item_name: str
    safety: Literal["safe_automatic", "review_required"]
    title: str
    description: str
    player_result: str
    user_value: str
    change_kind: str


class RepairCatalogContract(_ResponseContract):
    schema_: Literal["library_doctor.repair_catalog.v1"] = Field(alias="schema")
    catalog_version: str
    items: list[RepairDefinitionContract]
    combined: dict[str, Any]


class StructuredErrorDetailContract(_ResponseContract):
    code: str
    message: str
    file_state: str | None = None
    retryable: bool = False
    next_action: str | None = None


class ErrorEnvelopeContract(_ResponseContract):
    detail: StructuredErrorDetailContract


RequestId = Annotated[
    str,
    Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


class PlaybackStateRequestContract(_RequestContract):
    active: bool


class ScanRequestContract(_RequestContract):
    scope: Literal["library", "folder", "file"] = "library"
    path: str | None = None
    deep_audio: bool = False
    max_workers: int | None = Field(default=None, ge=1)


class RepairPreviewRequestContract(_RequestContract):
    package: str
    rule_code: str
    start_seconds: float | None = Field(default=None, ge=0)


class RepairApplyRequestContract(_RequestContract):
    package: str
    rule_code: str
    plan_id: str
    request_id: RequestId | None = None


class AutomaticPreviewRequestContract(_RequestContract):
    package: str
    rule_code: str
    request_id: RequestId | None = None


class AllSafePreviewRequestContract(_RequestContract):
    package: str


class AllSafeApplyRequestContract(_RequestContract):
    package: str
    plan_id: str
    request_id: RequestId | None = None


class BatchPreviewRequestContract(_RequestContract):
    include_preview_repairs: bool = False


class BatchApplyRequestContract(_RequestContract):
    batch_plan_id: str


class BatchUndoApplyRequestContract(_RequestContract):
    undo_plan_id: str


class BatchFinalizeApplyRequestContract(_RequestContract):
    finalize_plan_id: str


class RecoveryMutationRequestContract(_RequestContract):
    package: str
    backup_id: str
    request_id: RequestId | None = None


class MutationReceiptContract(_ResponseContract):
    request_id: RequestId | None = None
    idempotent_replay: bool = False
    outcome: str | None = None
    backup_id: str | None = None


class MutationReceiptLookupContract(_ResponseContract):
    schema_: Literal["library_doctor.mutation_receipt.v1"] = Field(alias="schema")
    request_id: RequestId
    operation: str
    state: Literal["pending", "complete"]
    receipt: dict[str, Any] | None = None


def error_detail(
    code: str,
    message: str,
    *,
    file_state: str | None = None,
    retryable: bool = False,
    next_action: str | None = None,
) -> dict[str, Any]:
    """Build the only error shape exposed by plugin routes."""
    return StructuredErrorDetailContract(
        code=code,
        message=message,
        file_state=file_state,
        retryable=retryable,
        next_action=next_action,
    ).model_dump()
