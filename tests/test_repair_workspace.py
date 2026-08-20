import json

import repair_workspace


def test_candidate_workspace_never_uses_a_song_package_name(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    package.mkdir()

    workspace = repair_workspace.create_candidate_workspace(
        config_dir=tmp_path / "config",
        package_path=package,
    )
    try:
        assert workspace.root.parent == library.resolve()
        assert workspace.root.name.startswith(repair_workspace.WORKSPACE_PREFIX)
        assert workspace.candidate.name == repair_workspace.CANDIDATE_NAME
        assert workspace.candidate.suffix.lower() not in {".feedpak", ".sloppak"}
        marker = json.loads(
            (workspace.root / repair_workspace.MARKER_NAME).read_text(encoding="utf-8")
        )
        assert marker["workspace_id"] == workspace.workspace_id
        receipts = list(
            (tmp_path / "config" / "library_doctor" / "repair_workspaces").glob(
                "*.json"
            )
        )
        assert len(receipts) == 1
    finally:
        workspace.cleanup()

    assert not workspace.root.exists()
    assert not list(
        (tmp_path / "config" / "library_doctor" / "repair_workspaces").glob(
            "*.json"
        )
    )


def test_stale_owned_workspace_is_reconciled_but_recent_one_is_preserved(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    package.mkdir()
    workspace = repair_workspace.create_candidate_workspace(
        config_dir=tmp_path / "config",
        package_path=package,
    )
    workspace.candidate.mkdir()
    created_at = json.loads(
        (workspace.root / repair_workspace.MARKER_NAME).read_text(encoding="utf-8")
    )["created_at"]

    recent = repair_workspace.reconcile_stale_workspaces(
        tmp_path / "config",
        now=created_at + 1,
        stale_after_seconds=10,
    )
    assert recent["pending"] == 1
    assert workspace.root.exists()

    old = repair_workspace.reconcile_stale_workspaces(
        tmp_path / "config",
        now=created_at + repair_workspace.STALE_WORKSPACE_SECONDS + 1,
        stale_after_seconds=repair_workspace.STALE_WORKSPACE_SECONDS,
    )
    assert old["removed"] == 1
    assert not workspace.root.exists()


def test_reconciliation_never_removes_workspace_with_unexpected_content(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    package = library / "Song.feedpak"
    package.mkdir()
    workspace = repair_workspace.create_candidate_workspace(
        config_dir=tmp_path / "config",
        package_path=package,
    )
    (workspace.root / "unexpected-user-file.txt").write_text("keep", encoding="utf-8")

    result = repair_workspace.reconcile_stale_workspaces(
        tmp_path / "config",
        now=repair_workspace.STALE_WORKSPACE_SECONDS + 1,
        stale_after_seconds=repair_workspace.STALE_WORKSPACE_SECONDS,
    )

    assert result["pending"] == 1
    assert workspace.root.exists()
    assert (workspace.root / "unexpected-user-file.txt").read_text(encoding="utf-8") == "keep"


def test_missing_workspace_removes_only_its_registry_receipt(tmp_path):
    registry = tmp_path / "config" / "library_doctor" / "repair_workspaces"
    registry.mkdir(parents=True)
    workspace_id = "missing"
    receipt = registry / f"{workspace_id}.json"
    receipt.write_text(
        json.dumps({
            "schema": repair_workspace.RECEIPT_SCHEMA,
            "workspace_id": workspace_id,
            "workspace": str(tmp_path / f"{repair_workspace.WORKSPACE_PREFIX}{workspace_id}"),
            "created_at": 0,
        }),
        encoding="utf-8",
    )

    result = repair_workspace.reconcile_stale_workspaces(tmp_path / "config", now=100)

    assert result == {"pending": 0, "removed": 0, "unreadable": 0, "capped": False}
    assert not receipt.exists()
