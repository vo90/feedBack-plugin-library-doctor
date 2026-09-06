"""Source-bound repair of demonstrably absolute retained bend timestamps."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

RULE_CODE = "chart.bend-time-coordinates"
OPERATION = "normalize_bend_time_coordinates"


@dataclass(frozen=True)
class NormalizeBendTimes:
    original_sha256: str
    changes: tuple

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
    digest = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return NormalizeBendTimes(digest, tuple((tuple(c["path"]), c["expected"], c["replacement"])
                                          for c in assessment["changes"]))


def apply_operation(document, operation, *, assess, error_type):
    expected = plan_operation(document, assess=assess, error_type=error_type)
    if expected is None or expected.to_dict() != operation:
        raise error_type("source_changed", "The retained bend curve changed. Preview again.")
    for path, _before, after in expected.changes:
        parent = document
        for part in path[:-1]:
            parent = parent[part]
        parent[path[-1]] = after
