"""Source-bound transfer of plain coincident sustains into explicit chord members."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

RULE_CODE = "chart.coincident-chord-sustain"
OPERATION = "consolidate_coincident_chord_sustains"


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class ConsolidateChordSustains:
    original_sha256: str
    changes: tuple[tuple[tuple[str | int, ...], int, int, int, float], ...]

    @property
    def remove_indices(self):
        return tuple(ni for _path, ni, _ci, _mi, _sustain in self.changes)

    @property
    def change_count(self):
        return len(self.changes)

    def to_dict(self):
        return {"operation": OPERATION, "original_sha256": self.original_sha256,
                "changes": [{"container_path": list(path), "note_index": ni,
                             "chord_index": ci, "member_index": mi, "sustain": sustain}
                            for path, ni, ci, mi, sustain in self.changes]}


def plan_operation(document, *, assess, error_type):
    assessment = assess(document)
    if assessment["status"] == "no_defect":
        return None
    if assessment["status"] != "eligible":
        raise error_type(assessment["blocker_code"], assessment["message"])
    return ConsolidateChordSustains(_digest(document), tuple(
        (tuple(c["container_path"]), c["note_index"], c["chord_index"], c["member_index"], c["sustain"])
        for c in assessment["changes"]
    ))


def apply_operation(document, operation, *, assess, error_type):
    expected = plan_operation(document, assess=assess, error_type=error_type)
    if expected is None or expected.to_dict() != operation:
        raise error_type("source_changed", "The coincident notes or chord members changed. Preview the repair again.")
    # Rebuild and verify all ownership before mutation. Transfer every duration
    # first, then remove from each note array in descending index order.
    removals = {}
    for path, ni, ci, mi, sustain in expected.changes:
        container = document
        for part in path:
            container = container[part]
        container["chords"][ci]["notes"][mi]["sus"] = sustain
        removals.setdefault(path, (container["notes"], []))[1].append(ni)
    for notes, indexes in removals.values():
        for index in sorted(indexes, reverse=True):
            del notes[index]
