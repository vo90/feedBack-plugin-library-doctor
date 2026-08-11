"""Crash-only helper for real Library Doctor transaction recovery tests."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

import repair  # noqa: E402
import batch_repair  # noqa: E402


CRASH_EXIT_CODE = 86


class _BatchScanner:
    def __init__(self):
        self.reserved = False

    def begin_batch_operation(self):
        if self.reserved:
            return False, "busy"
        self.reserved = True
        return True, ""

    def finish_repair(self):
        self.reserved = False

    @staticmethod
    def playback_active():
        return False

    @staticmethod
    def wait_for_playback(cancel_event):
        return not cancel_event.is_set()

    @staticmethod
    def record_repair_result(_package, _report, *, deep_audio=False):
        del deep_audio


def validate_package(path: Path, package_name: str, *, deep_audio: bool = False):
    def read(member: str) -> bytes:
        if path.is_dir():
            return path.joinpath(*Path(member).parts).read_bytes()
        with zipfile.ZipFile(path) as archive:
            return archive.read(member)

    duplicate = False
    for member in ("arrangements/lead.json", "arrangements/rhythm.json"):
        document = json.loads(read(member))
        duplicate = duplicate or len(document.get("anchors", [])) > 1
    findings = (
        [{"code": "chart.duplicate-anchor", "severity": "warning"}]
        if duplicate else []
    )
    return {
        "schema": "library_doctor.package.v1",
        "validator_version": "rules-test",
        "package": package_name,
        "title": "Transaction Song",
        "artist": "Synthetic",
        "status": "warning" if findings else "healthy",
        "counts": {"error": 0, "warning": len(findings), "info": 0},
        "features": {"deep_audio_checked": bool(deep_audio)},
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("apply", "restore", "batch"))
    parser.add_argument("library")
    parser.add_argument("config")
    parser.add_argument("barrier")
    parser.add_argument("--member-index", type=int)
    parser.add_argument("--backup-id")
    parser.add_argument("--crash-package")
    options = parser.parse_args()

    def crash_barrier(name, context):
        if name != options.barrier:
            return
        if (
            options.crash_package is not None
            and context.get("package") != options.crash_package
        ):
            return
        if (
            options.member_index is not None
            and context.get("member_index") != options.member_index
        ):
            return
        os._exit(CRASH_EXIT_CODE)

    service = repair.RepairService(
        config_dir=Path(options.config),
        get_dlc_dir=lambda: Path(options.library),
        validate_feedpak=validate_package,
        validator_version="rules-test",
        log=logging.getLogger("library-doctor-real-crash-helper"),
        transaction_barrier=crash_barrier,
    )
    if options.operation == "apply":
        plan = service.preview_all("Song.feedpak")
        service.apply_all("Song.feedpak", plan["plan_id"])
        return
    if options.operation == "batch":
        batch_repair.CHECKPOINT_PACKAGE_INTERVAL = 1
        manager = batch_repair.BatchRepairManager(
            config_dir=Path(options.config),
            scanner=_BatchScanner(),
            repair_service=service,
            repair_error_type=repair.RepairPlanningError,
            log=logging.getLogger("library-doctor-real-batch-crash-helper"),
        )
        packages = ("One.feedpak", "Two.feedpak", "Three.feedpak")
        snapshot = {
            "schema": "library_doctor.repair_scope.v1",
            "target": {"kind": "library", "label": "Synthetic library"},
            "deep_audio": False,
            "validator_version": "rules-test",
            "scope_package_count": len(packages),
            "candidates": [
                {
                    "package": package,
                    "title": package,
                    "artist": "Synthetic",
                    "rule_codes": ["chart.duplicate-anchor"],
                }
                for package in packages
            ],
        }
        manager.start_preview(snapshot)
        manager.join(10)
        manager.start_apply(manager.status()["preview"]["batch_plan_id"])
        manager.join(30)
        return
    if not options.backup_id:
        raise SystemExit("restore requires --backup-id")
    service.restore("Song.feedpak", options.backup_id)


if __name__ == "__main__":
    main()
