"""Acceptance test on a NEW isolated copy, never on the supplied source Feedpak."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import shutil
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RULE = "timeline.terminal-duplicate-beats"


def load(name):
    spec = importlib.util.spec_from_file_location("acceptance_" + name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def archive_hashes(path):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        return {member.filename: hashlib.sha256(archive.read(member)).hexdigest()
                for member in archive.infolist() if not member.is_dir()}


def verify(source, output, expected_removed=None, expected_retained=None):
    source = source.resolve(strict=True)
    output = output.resolve()
    if output.exists() or source.is_relative_to(output):
        raise ValueError("Choose a new, nonexistent output directory outside the source path.")
    if not source.is_file() or source.suffix.lower() != ".feedpak":
        raise ValueError("Source must be an existing ZIP Feedpak.")
    original_hash = digest(source)
    output.mkdir(parents=True)
    library = output / "library"
    library.mkdir()
    package = library / "Acceptance.feedpak"
    shutil.copy2(source, package)
    repair, validator = load("repair"), load("validator")
    service = repair.RepairService(
        config_dir=output / "profile", get_dlc_dir=lambda: library,
        validate_feedpak=validator.validate_feedpak,
        validator_version=validator.VALIDATOR_VERSION,
        log=logging.getLogger("terminal-copy-acceptance"),
    )
    report = {"source_sha256_before": original_hash, "playback_checked": False}
    try:
        before = archive_hashes(package)
        scan = validator.validate_feedpak(package)
        proof = scan["features"]["repair_eligibility"][RULE]
        assert proof["status"] == "automatic", proof
        preview = service.preview(package.name, RULE)
        assert preview["available"], preview["blockers"]
        assert preview["terminal_assessment"] == proof
        if expected_removed is not None:
            assert preview["removed_count"] == expected_removed
        if expected_retained is not None:
            assert all(m["retained_count"] == expected_retained for m in proof["members"])
        result = service.apply(package.name, RULE, preview["plan_id"])
        assert result["applied"] and result["undo_available"], result
        after = archive_hashes(package)
        affected = {m["member_path"]: m for m in proof["members"]}
        assert set(before) == set(after)
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(package) as candidate:
            for name in before:
                if name not in affected:
                    assert after[name] == before[name], name
                    continue
                expected = json.loads(original.read(name))
                retained = expected["beats"][:affected[name]["suffix_start"]]
                expected["beats"] = retained
                assert json.loads(candidate.read(name)) == expected, name
                assert all(a["time"] < b["time"] for a, b in zip(retained, retained[1:]))
        rescanned = validator.validate_feedpak(package)
        assert not rescanned["findings"], rescanned["findings"]
        assert not service.preview(package.name, RULE)["available"]
        restored = service.restore(package.name, result["backup_id"])
        assert restored["outcome"] == "restored", restored
        assert archive_hashes(package) == before
        report.update(passed=True, assessment=proof, removed_count=preview["removed_count"],
                      member_count=len(affected), after_status=rescanned["status"],
                      after_findings=len(rescanned["findings"]), other_members_unchanged=True,
                      all_audio_byte_identical=True, repeat_repair_available=False,
                      undo_restored_every_member=True, undo_archive_byte_identical=digest(package) == original_hash,
                      copy=str(package))
        return report
    finally:
        report["source_sha256_after"] = digest(source)
        report["source_unchanged"] = report["source_sha256_after"] == original_hash
        (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        assert report["source_unchanged"], "The source changed during acceptance testing."


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-removed", type=int)
    parser.add_argument("--expected-retained", type=int)
    args = parser.parse_args()
    print(json.dumps(verify(args.source, args.output, args.expected_removed,
                            args.expected_retained), indent=2))
