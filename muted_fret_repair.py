"""Source-bound normalization of corroborated imported muted-fret sentinels."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

RULE_CODE = "chart.muted-fret-sentinel"
OPERATION = "normalize_muted_fret_sentinels"


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class NormalizeMutedSentinels:
    original_sha256: str
    changes: tuple[tuple[tuple[str | int, ...], int, int], ...]

    @property
    def remove_indices(self):
        return ()

    @property
    def change_count(self):
        return len(self.changes)

    def to_dict(self):
        return {"operation": OPERATION, "original_sha256": self.original_sha256,
                "changes": [{"path": list(path), "expected": before, "replacement": after}
                            for path, before, after in self.changes]}


def plan_operation(document, *, assess, error_type):
    assessment = assess(document)
    if assessment["status"] == "no_defect":
        return None
    if assessment["status"] != "eligible":
        raise error_type(assessment["blocker_code"], assessment["message"])
    return NormalizeMutedSentinels(_digest(document), tuple(
        (tuple(change["path"]), 127, 0) for change in assessment["changes"]
    ))


def apply_operation(document, operation, *, assess, error_type):
    expected = plan_operation(document, assess=assess, error_type=error_type)
    if expected is None or expected.to_dict() != operation:
        raise error_type("source_changed", "The muted notes or their shared template uses changed. Preview the repair again.")
    # Resolve every path before changing any value; the caller also applies to
    # an isolated document/candidate and verifies the complete package.
    targets = []
    for path, before, after in expected.changes:
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        if type(parent[path[-1]]) is not int or parent[path[-1]] != before:
            raise error_type("source_changed", "A muted-fret value changed after preview.")
        targets.append((parent, path[-1], after))
    for parent, key, after in targets:
        parent[key] = copy.deepcopy(after)
