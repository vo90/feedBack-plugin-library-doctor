"""Registry and pure adapters for author-decided Library Doctor repairs.

Reviewed repairs are intentionally separate from the automatic repair catalog.
An adapter may classify evidence and translate a named decision into a closed
domain operation, but it cannot execute filesystem writes. RepairService owns
source binding, complete-candidate validation, backup, commit, journal, and
Undo for every adapter.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

try:
    from repair_eligibility import (
        HopoReviewCandidate,
        find_hopo_review_candidates,
        page_hopo_review_candidates,
        select_hopo_review_candidates,
    )
except ModuleNotFoundError:  # Tests and some plugin hosts load files by path.
    _eligibility_name = "_library_doctor_reviewed_repair_eligibility"
    _eligibility = sys.modules.get(_eligibility_name)
    if _eligibility is None:
        _eligibility_spec = importlib.util.spec_from_file_location(
            _eligibility_name,
            Path(__file__).resolve().with_name("repair_eligibility.py"),
        )
        _eligibility = importlib.util.module_from_spec(_eligibility_spec)
        sys.modules[_eligibility_name] = _eligibility
        _eligibility_spec.loader.exec_module(_eligibility)
    HopoReviewCandidate = _eligibility.HopoReviewCandidate
    find_hopo_review_candidates = _eligibility.find_hopo_review_candidates
    page_hopo_review_candidates = _eligibility.page_hopo_review_candidates
    select_hopo_review_candidates = _eligibility.select_hopo_review_candidates


REVIEWED_REPAIR_REGISTRY_VERSION = "reviewed-repairs-2"


@dataclass(frozen=True)
class ReviewedDecisionDefinition:
    name: str
    label: str
    description: str
    confirmation: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "confirmation": self.confirmation,
        }


@dataclass(frozen=True)
class ReviewedMutationSpec:
    """One adapter-derived, field-limited mutation target."""

    target_role: str
    set_fields: tuple[tuple[str, bool], ...]
    remove_fields: tuple[str, ...]

    def set_dict(self) -> dict[str, bool]:
        return dict(self.set_fields)


@dataclass(frozen=True)
class ReviewedRepairDefinition:
    adapter_id: str
    title: str
    description: str
    trigger_rule_codes: tuple[str, ...]
    mutable_fields: tuple[str, ...]
    decisions: tuple[ReviewedDecisionDefinition, ...]
    inspect_document: Callable[..., list[HopoReviewCandidate]]
    inspect_page_document: Callable[..., object]
    select_document: Callable[..., object]
    build_mutations: Callable[[HopoReviewCandidate, str], tuple[ReviewedMutationSpec, ...]]
    context_schema: str
    candidate_limit: int
    candidate_id_prefix: str
    operation_name: str
    blocker_codes: tuple[str, ...]
    postconditions: tuple[str, ...]
    audio_support: bool
    test_owner: str

    def to_dict(self) -> dict:
        return {
            "adapter_id": self.adapter_id,
            "title": self.title,
            "description": self.description,
            "safety": "review_required",
            "trigger_rule_codes": list(self.trigger_rule_codes),
            "mutable_fields": list(self.mutable_fields),
            "decisions": [decision.to_dict() for decision in self.decisions],
            "context_schema": self.context_schema,
            "candidate_limit": self.candidate_limit,
            "candidate_id_prefix": self.candidate_id_prefix,
            "operation_name": self.operation_name,
            "blocker_codes": list(self.blocker_codes),
            "postconditions": list(self.postconditions),
            "audio_support": self.audio_support,
            "test_owner": self.test_owner,
        }


_HOPO_DECISIONS = (
    ReviewedDecisionDefinition(
        name="set_hammer_on",
        label="Use hammer-on",
        description=(
            "Keep this note as the destination and store hammer-on only. "
            "The tap flag, if any, is left unchanged."
        ),
        confirmation="Set this note to hammer-on and remove its pull-off flag?",
    ),
    ReviewedDecisionDefinition(
        name="set_pull_off",
        label="Use pull-off",
        description=(
            "Keep this note as the destination and store pull-off only. "
            "The tap flag, if any, is left unchanged."
        ),
        confirmation="Set this note to pull-off and remove its hammer-on flag?",
    ),
    ReviewedDecisionDefinition(
        name="convert_to_tap",
        label="Convert to tap",
        description=(
            "Remove hammer-on and pull-off from this note and enable its tap flag."
        ),
        confirmation="Convert this note to a tap?",
    ),
    ReviewedDecisionDefinition(
        name="remove_hopo",
        label="Remove HO/PO",
        description=(
            "Remove the hammer-on and pull-off fields from this note without "
            "changing its fret, timing, or other techniques."
        ),
        confirmation="Remove hammer-on and pull-off from this note?",
    ),
    ReviewedDecisionDefinition(
        name="move_to_next",
        label="Move to next note",
        description=(
            "Remove HO/PO here and put the directionally matching flag on the "
            "next explicit same-string note. This is offered only when that "
            "destination is unambiguous."
        ),
        confirmation="Move this HO/PO instruction to the next note?",
    ),
    ReviewedDecisionDefinition(
        name="leave_unchanged",
        label="Leave unchanged",
        description="Record no mutation for this candidate.",
        confirmation="Leave this authored note unchanged?",
    ),
)


def _hopo_mutations(
    candidate: HopoReviewCandidate,
    decision: str,
) -> tuple[ReviewedMutationSpec, ...]:
    if decision == "set_hammer_on":
        return (ReviewedMutationSpec("current", (("ho", True),), ("po",)),)
    if decision == "set_pull_off":
        return (ReviewedMutationSpec("current", (("po", True),), ("ho",)),)
    if decision == "convert_to_tap":
        return (ReviewedMutationSpec("current", (("tp", True),), ("ho", "po")),)
    if decision == "remove_hopo":
        return (ReviewedMutationSpec("current", (), ("ho", "po")),)
    if decision != "move_to_next":
        raise ValueError("unsupported reviewed repair decision")

    next_note = candidate.next
    if (
        next_note is None
        or not next_note.writable
        or next_note.path is None
        or next_note.fret == candidate.fret
        or next_note.malformed_techniques
        or next_note.hammer_on
        or next_note.pull_off
        or next_note.tap
    ):
        raise ValueError("reviewed repair target is no longer eligible")
    next_fields = (("ho", True),) if candidate.fret < next_note.fret else (("po", True),)
    next_remove = ("po",) if candidate.fret < next_note.fret else ("ho",)
    return (
        ReviewedMutationSpec("current", (), ("ho", "po")),
        ReviewedMutationSpec("next", next_fields, next_remove),
    )


_REVIEWED_REPAIRS = (
    ReviewedRepairDefinition(
        adapter_id="review.hopo-techniques",
        title="Review hammer-ons and pull-offs",
        description=(
            "Compare the previous, current, and next note on the same string, "
            "then make an explicit author decision for each questionable HO/PO flag."
        ),
        trigger_rule_codes=(
            "chart.conflicting-techniques",
            "review.hopo-direction-mismatch",
            "review.same-fret-hopo",
            "review.hopo-without-source",
        ),
        mutable_fields=("ho", "po", "tp"),
        decisions=_HOPO_DECISIONS,
        inspect_document=find_hopo_review_candidates,
        inspect_page_document=page_hopo_review_candidates,
        select_document=select_hopo_review_candidates,
        build_mutations=_hopo_mutations,
        context_schema="library_doctor.reviewed_hopo_context.v1",
        candidate_limit=2_000,
        candidate_id_prefix="hopo",
        operation_name="review_hopo_techniques",
        blocker_codes=(
            "same_time_string_conflict",
            "ambiguous_predecessor",
            "malformed_technique_value",
        ),
        postconditions=(
            "only_declared_fields_change",
            "every_touched_object_matches_before_and_after_hashes",
            "move_target_is_explicit_unique_and_technique_free",
            "unselected_json_values_are_preserved",
        ),
        audio_support=True,
        test_owner="review.hopo-techniques",
    ),
)

_BY_ADAPTER_ID = {
    definition.adapter_id: definition for definition in _REVIEWED_REPAIRS
}
_BY_TRIGGER_RULE = {
    rule_code: definition
    for definition in _REVIEWED_REPAIRS
    for rule_code in definition.trigger_rule_codes
}
_BY_OPERATION = {
    definition.operation_name: definition for definition in _REVIEWED_REPAIRS
}


def reviewed_repair_catalog() -> list[dict]:
    return [definition.to_dict() for definition in _REVIEWED_REPAIRS]


def reviewed_repair_definition(adapter_id: str) -> ReviewedRepairDefinition:
    try:
        return _BY_ADAPTER_ID[adapter_id]
    except KeyError as exc:
        raise ValueError("unsupported reviewed repair adapter") from exc


def reviewed_repair_for_rule(
    rule_code: str,
) -> ReviewedRepairDefinition | None:
    return _BY_TRIGGER_RULE.get(rule_code)


def reviewed_repair_for_operation(
    operation_name: str,
) -> ReviewedRepairDefinition | None:
    return _BY_OPERATION.get(operation_name)


def inspect_reviewed_document(
    adapter_id: str,
    document: dict,
    *,
    member_path: str,
) -> list[HopoReviewCandidate]:
    definition = reviewed_repair_definition(adapter_id)
    return definition.inspect_document(document, member_path=member_path)
