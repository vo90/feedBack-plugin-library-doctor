"""Source-bound replacement of proven stale hand-shape references only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

RULE_CODE = "chart.stale-handshape-reference"
OPERATION = "repair_stale_handshape_references"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


@dataclass(frozen=True)
class RepairHandshapeReferences:
    original_sha256: str
    changes: tuple[tuple[tuple[str | int, ...], int, int, int], ...]

    @property
    def change_count(self):
        return len(self.changes)

    @property
    def remove_indices(self):
        return ()

    def to_dict(self):
        return {"operation": OPERATION, "original_sha256": self.original_sha256,
                "changes": [{"container_path": list(path), "handshape_index": index,
                             "original_chord_id": old, "target_chord_id": target}
                            for path, index, old, target in self.changes]}


def plan_operation(document, *, assess, error_type):
    assessment = assess(document)
    if not assessment["changes"]:
        return None
    return RepairHandshapeReferences(hashlib.sha256(_canonical(document)).hexdigest(), tuple(
        (tuple(c["container_path"]), c["handshape_index"], c["original_chord_id"], c["target_chord_id"])
        for c in assessment["changes"]
    ))


def apply_operation(document, operation, *, assess, error_type):
    expected = plan_operation(document, assess=assess, error_type=error_type)
    try:
        valid = expected is not None and _canonical(expected.to_dict()) == _canonical(operation)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise error_type("source_changed", "The hand-shape reference evidence changed. Preview the repair again.")
    # Re-derive and compare the complete operation before changing anything.
    # Canonical bytes distinguish booleans from integers and reject extra data.
    for path, index, _old, target in expected.changes:
        container = document
        for part in path:
            container = container[part]
        container["handshapes"][index]["chord_id"] = target
