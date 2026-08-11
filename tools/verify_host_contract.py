"""Verify that a FeedBack checkout satisfies Library Doctor's host contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = PLUGIN_ROOT / "host-contract.json"
DEFAULT_MANIFEST = PLUGIN_ROOT / "plugin.json"
CONTRACT_SCHEMA = "library_doctor.host_contract.v1"


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _evidence_matches(host_root: Path, evidence: dict) -> tuple[bool, str]:
    relative = evidence.get("path")
    patterns = evidence.get("contains")
    if not isinstance(relative, str) or not relative:
        return False, "contract evidence has no path"
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list) or not patterns or not all(
        isinstance(pattern, str) and pattern for pattern in patterns
    ):
        return False, f"{relative}: contract evidence has no text patterns"

    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return False, f"{relative}: unsafe evidence path"
    root = host_root.resolve(strict=True)
    candidate = root.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        source = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return False, f"{relative}: missing, unreadable, or outside the host checkout"

    missing = [pattern for pattern in patterns if pattern not in source]
    if missing:
        return False, f"{relative}: required capability marker is absent"
    return True, relative


def verify_host_contract(
    host_root: Path,
    *,
    contract_path: Path = DEFAULT_CONTRACT,
    manifest_path: Path = DEFAULT_MANIFEST,
) -> dict:
    """Return a stable report without modifying the host checkout."""
    errors: list[str] = []
    capabilities: list[dict] = []
    try:
        contract = _read_json(contract_path)
        manifest = _read_json(manifest_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "schema": "library_doctor.host_contract_result.v1",
            "compatible": False,
            "host_version": "unknown",
            "capabilities": [],
            "errors": [f"contract_input_invalid:{type(exc).__name__}"],
        }

    if contract.get("schema") != CONTRACT_SCHEMA:
        errors.append("contract_schema_invalid")
    if manifest.get("minHost") != contract.get("declaredMinHost"):
        errors.append("manifest_min_host_mismatch")

    try:
        host_version = (host_root / "VERSION").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        host_version = "unknown"
        errors.append("host_version_unavailable")

    declared_capabilities = contract.get("capabilities")
    if not isinstance(declared_capabilities, list) or not declared_capabilities:
        errors.append("contract_capabilities_invalid")
        declared_capabilities = []

    for capability in declared_capabilities:
        capability_id = capability.get("id") if isinstance(capability, dict) else None
        if not isinstance(capability_id, str) or not capability_id:
            errors.append("contract_capability_id_invalid")
            continue
        evidence = capability.get("evidence", [])
        alternatives = capability.get("evidenceAny", [])
        passed = True
        notes: list[str] = []
        if not isinstance(evidence, list) or not evidence:
            passed = False
            notes.append("required evidence is missing from the contract")
        else:
            for item in evidence:
                matched, note = _evidence_matches(host_root, item)
                passed = passed and matched
                notes.append(note)
        if alternatives:
            if not isinstance(alternatives, list):
                passed = False
                notes.append("alternative evidence is invalid")
            else:
                results = [_evidence_matches(host_root, item) for item in alternatives]
                passed = passed and any(matched for matched, _note in results)
                notes.extend(note for _matched, note in results)
        capabilities.append({"id": capability_id, "passed": passed, "evidence": notes})
        if not passed:
            errors.append(f"capability_missing:{capability_id}")

    minimum = contract.get("minimumCompatibleBuild")
    minimum_commit = minimum.get("commit") if isinstance(minimum, dict) else None
    return {
        "schema": "library_doctor.host_contract_result.v1",
        "compatible": not errors,
        "host_version": host_version or "unknown",
        "minimum_compatible_commit": minimum_commit,
        "capabilities": capabilities,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host_root", type=Path, help="Path to a FeedBack source checkout")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    report = verify_host_contract(args.host_root)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        outcome = "compatible" if report["compatible"] else "incompatible"
        print(f"FeedBack host contract: {outcome} ({report['host_version']})")
        for capability in report["capabilities"]:
            state = "pass" if capability["passed"] else "FAIL"
            print(f"- {state}: {capability['id']}")
        for error in report["errors"]:
            print(f"- error: {error}")
    return 0 if report["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
