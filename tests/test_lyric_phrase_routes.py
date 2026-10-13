"""Exercise the real lyric repair routes using only disposable song packages."""
import json
import zipfile

import pytest
import yaml

from test_routes import _client, _wait_for_scan


RULE = "lyrics.too-few-line-breaks"
PACKAGE = "Synthetic/Phrases.feedpak"
PRIMARY = "lyrics/main.json"
ADDITIONAL = "lyrics/additional.json"
AUTHORED = "lyrics/authored.json"


def _cues(count, *, start=1.0):
    cues = [
        {"t": start + index * 0.6, "d": 0.2, "w": f"word{index:03}",
         "future": {"source_index": index, "preserve": [True, None]}}
        for index in range(count)
    ]
    # A proposed phrase boundary must never divide this authored syllable chain.
    cues[7]["w"] = "sylla-"
    cues[8]["w"] = "ble"
    return cues


def _members():
    manifest = {
        "feedpak_version": "1.19.0", "title": "Synthetic lyric phrases",
        "artist": "Test Artist", "duration": 120.0,
        "arrangements": [{"id": "lead", "file": "arrangements/lead.json"}],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
        "lyrics": PRIMARY,
        "lyric_tracks": [
            {"id": "primary", "file": PRIMARY},  # Duplicate pointer, one repair.
            {"id": "additional", "file": ADDITIONAL},
            {"id": "authored", "file": AUTHORED},
        ],
    }
    authored = _cues(16)
    authored[7]["w"] = "ending+"
    authored[-1]["w"] += "+"
    def encode(value):
        return (json.dumps(value, ensure_ascii=False, indent=3) + "\n").replace("\n", "\r\n").encode("utf-8")
    return {
        "manifest.yaml": yaml.safe_dump(manifest, sort_keys=False).encode("utf-8"),
        "arrangements/lead.json": b'{"notes": [], "chords": [], "keep": "exact"}\n',
        "stems/full.ogg": b"opaque-audio-kept-exactly",
        "extras/retain.bin": bytes(range(64)),
        PRIMARY: encode(_cues(96)),
        ADDITIONAL: encode(_cues(84, start=2.0)),
        AUTHORED: encode(authored),
    }


def _write_package(library, archive, members):
    package = library / PACKAGE
    package.parent.mkdir(parents=True, exist_ok=True)
    if archive:
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as target:
            target.comment = b"lyric route fixture: preserve archive comment"
            for name, raw in members.items():
                target.writestr(name, raw)
    else:
        for name, raw in members.items():
            member = package / name
            member.parent.mkdir(parents=True, exist_ok=True)
            member.write_bytes(raw)
    return package


def _read_members(package):
    if package.is_dir():
        return {member.relative_to(package).as_posix(): member.read_bytes()
                for member in package.rglob("*") if member.is_file()}
    with zipfile.ZipFile(package) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert archive.comment == b"lyric route fixture: preserve archive comment"
        return {name: archive.read(name) for name in archive.namelist()}


def _scan(client):
    response = client.post("/api/plugins/library_doctor/scan")
    assert response.status_code == 202, response.text
    assert _wait_for_scan(client)["stage"] == "complete"
    reports = client.get("/api/plugins/library_doctor/results").json()["items"]
    return next(report for report in reports if report["package"] == PACKAGE)


def _preview(client):
    response = client.post("/api/plugins/library_doctor/repair/preview",
                           json={"package": PACKAGE, "rule_code": RULE})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["available"] is True
    assert plan["change_kind"] == "add_line_breaks"
    assert plan["change_count"] > 0
    assert plan["removed_count"] == 0
    return plan


@pytest.mark.parametrize("archive", [False, True], ids=["directory", "zip"])
def test_phrase_routes_preview_apply_validate_and_exact_byte_undo(tmp_path, archive):
    # Use the existing real validator through the fixture's thread pool, avoiding
    # process-start overhead while retaining normal route/service/validation code.
    client, library = _client(tmp_path, validator_hook=lambda _module: None)
    try:
        originals = _members()
        package = _write_package(library, archive, originals)
        original_archive = package.read_bytes() if archive else None
        before = _scan(client)
        warnings = [finding for finding in before["findings"] if finding["code"] == RULE]
        assert len(warnings) == 2  # Primary alias must not duplicate an operation.
        assert all(finding["rule"]["repairability"] == "safe_candidate" for finding in warnings)
        assert all(finding["rule"]["title"] == "Lyrics may be missing line breaks" for finding in warnings)
        assert before["features"]["repair_eligibility"][RULE]["status"] == "automatic"

        plan = _preview(client)
        assert plan["title"] == "Add readable lyric phrase endings"
        assert plan["member_count"] == 2
        assert _read_members(package) == originals
        if archive:
            assert package.read_bytes() == original_archive
        assert not (tmp_path / "config/library_doctor/repair_backups").exists()

        applied = client.post("/api/plugins/library_doctor/repair/apply", json={
            "package": PACKAGE, "rule_code": RULE, "plan_id": plan["plan_id"],
        })
        assert applied.status_code == 200, applied.text
        result = applied.json()
        assert result["change_kind"] == "add_line_breaks"
        assert result["change_count"] == plan["change_count"]
        assert result["removed_count"] == 0
        assert RULE not in {finding["code"] for finding in result["report"]["findings"]}

        repaired_members = _read_members(package)
        assert set(repaired_members) == set(originals)
        added = 0
        for name, original_raw in originals.items():
            if name not in {PRIMARY, ADDITIONAL}:
                assert repaired_members[name] == original_raw
                continue
            original = json.loads(original_raw)
            repaired = json.loads(repaired_members[name])
            assert len(repaired) == len(original)
            for old, new in zip(original, repaired):
                assert {key: value for key, value in new.items() if key != "w"} == {
                    key: value for key, value in old.items() if key != "w"
                }
                assert new["w"] in {old["w"], old["w"] + "+"}
                if new["w"] != old["w"]:
                    added += 1
                    assert not old["w"].endswith(("-", "+"))
            assert repaired[7]["w"] == "sylla-"
        assert added == plan["change_count"]
        after = client.get("/api/plugins/library_doctor/results").json()["items"]
        assert not any(f["code"] == RULE for report in after for f in report["findings"])

        repeated = client.post("/api/plugins/library_doctor/repair/preview",
                               json={"package": PACKAGE, "rule_code": RULE})
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["available"] is False
        assert _read_members(package) == repaired_members

        restored = client.post("/api/plugins/library_doctor/repair/restore", json={
            "package": PACKAGE, "backup_id": result["backup_id"],
        })
        assert restored.status_code == 200, restored.text
        assert restored.json()["change_kind"] == "add_line_breaks"
        assert restored.json()["change_count"] == added
        # Undo restores exact member bytes (including original CRLF/indentation),
        # not merely equivalent JSON. ZIP container recompression is permitted.
        assert _read_members(package) == originals
        assert RULE in {finding["code"] for finding in restored.json()["report"]["findings"]}
    finally:
        client.close()


@pytest.mark.parametrize("archive", [False, True], ids=["directory", "zip"])
def test_phrase_routes_refuse_stale_preview_without_touching_song(tmp_path, archive):
    client, library = _client(tmp_path, validator_hook=lambda _module: None)
    try:
        originals = _members()
        package = _write_package(library, archive, originals)
        plan = _preview(client)
        edited = dict(originals)
        cues = json.loads(edited[ADDITIONAL])
        cues[0]["future"]["author_edit"] = "must remain"
        edited[ADDITIONAL] = json.dumps(cues).encode("utf-8")
        _write_package(library, archive, edited)
        edited_archive = package.read_bytes() if archive else None

        response = client.post("/api/plugins/library_doctor/repair/apply", json={
            "package": PACKAGE, "rule_code": RULE, "plan_id": plan["plan_id"],
        })
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["file_state"] == "unchanged"
        assert _read_members(package) == edited
        if archive:
            assert package.read_bytes() == edited_archive
        assert not (tmp_path / "config/library_doctor/repair_backups").exists()
    finally:
        client.close()


@pytest.mark.parametrize("archive", [False, True], ids=["directory", "zip"])
@pytest.mark.parametrize("declarations", [
    {"lyric_tracks": {"file": ADDITIONAL}},
    {"lyric_tracks": [None]},
    {"lyric_tracks": [{"id": "other", "file": 42}]},
    {"lyric_tracks": [{"id": "other", "file": "../other.json"}]},
    {"lyrics": 42},
])
def test_malformed_lyric_declarations_block_single_and_combined_phrase_repair(
    tmp_path, archive, declarations,
):
    client, library = _client(tmp_path, validator_hook=lambda _module: None)
    try:
        originals = _members()
        manifest = yaml.safe_load(originals["manifest.yaml"])
        manifest.update(declarations)
        originals["manifest.yaml"] = yaml.safe_dump(manifest, sort_keys=False).encode("utf-8")
        # Two independent safe findings expose the ordinary combined-fix path.
        note = {"t": 1.0, "s": 0, "f": 3, "sus": 0.5}
        anchor = {"time": 1.0, "fret": 3, "width": 4}
        chart = {"notes": [note, dict(note)], "chords": [],
                 "anchors": [anchor, dict(anchor)], "keep": "exact"}
        originals["arrangements/lead.json"] = json.dumps(chart).encode("utf-8")
        package = _write_package(library, archive, originals)
        archive_before = package.read_bytes() if archive else None
        before = _scan(client)
        assert before["features"]["repair_eligibility"][RULE]["status"] == "unavailable"

        response = client.post("/api/plugins/library_doctor/repair/preview",
                               json={"package": PACKAGE, "rule_code": RULE})
        assert response.status_code == 200, response.text
        rejected = response.json()
        assert rejected["available"] is False
        assert rejected["change_count"] == 0
        assert rejected["blockers"][0]["code"] == "lyric_source_unavailable"

        response = client.post("/api/plugins/library_doctor/repair/all/preview", json={"package": PACKAGE})
        assert response.status_code == 200, response.text
        combined = response.json()
        assert combined["available"] is True
        assert set(combined["rule_codes"]) == {"chart.duplicate-note", "chart.duplicate-anchor"}
        assert RULE not in {item["rule_code"] for item in combined["repair_summaries"]}
        assert _read_members(package) == originals
        if archive:
            assert package.read_bytes() == archive_before

        # Independent repairs may still commit, leaving ALL lyric/source bytes
        # unchanged, including the malformed declaration and every cue timing.
        response = client.post("/api/plugins/library_doctor/repair/all/apply",
                               json={"package": PACKAGE, "plan_id": combined["plan_id"]})
        assert response.status_code == 200, response.text
        repaired = _read_members(package)
        for path, raw in originals.items():
            if path != "arrangements/lead.json":
                assert repaired[path] == raw
        assert json.loads(repaired["arrangements/lead.json"]) == dict(
            chart, notes=[note], anchors=[anchor],
        )
    finally:
        client.close()


def test_new_phrase_declaration_guard_does_not_change_existing_lyric_order_repair(tmp_path):
    client, library = _client(tmp_path, validator_hook=lambda _module: None)
    try:
        originals = _members()
        manifest = yaml.safe_load(originals["manifest.yaml"])
        manifest["lyric_tracks"] = {"file": ADDITIONAL}
        originals["manifest.yaml"] = yaml.safe_dump(manifest, sort_keys=False).encode("utf-8")
        cues = json.loads(originals[PRIMARY])
        cues[0], cues[1] = cues[1], cues[0]
        originals[PRIMARY] = json.dumps(cues).encode("utf-8")
        package = _write_package(library, False, originals)
        response = client.post("/api/plugins/library_doctor/repair/preview",
                               json={"package": PACKAGE, "rule_code": "lyrics.out-of-order"})
        assert response.status_code == 200, response.text
        assert response.json()["available"] is True
        assert response.json()["change_kind"] == "reorder"
        assert _read_members(package) == originals
    finally:
        client.close()
