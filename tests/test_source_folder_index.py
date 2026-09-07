import copy
import hashlib
import os
import sys
import threading
from importlib import util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).parents[1]


def load(name):
    key = "doctor_folder_index_tests_" + name
    if key not in sys.modules:
        spec = util.spec_from_file_location(key, ROOT / (name + ".py"))
        module = util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


def document(fret=7):
    return {"notes": [{"t": 10, "s": 1, "f": fret, "sus": 1, "bn": 2}], "chords": []}


class Archive:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    def read_source_charts(self, value):
        path = Path(value)
        self.calls.append(path.name)
        data = self.documents[path.name]
        if isinstance(data, Exception):
            raise data
        return {"path": path, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "charts": [{"member": f"songs/bin/generic/chart-{i}.sng", "song": copy.deepcopy(d)}
                           for i, d in enumerate(data)]}


def create(tmp_path, records):
    for name, data in records.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def index(adapter, chart=None):
    return load("source_folder_index").SourceFolderIndex(
        archive=adapter, chart=chart or NS(source_document=lambda source: source))


def test_recursive_content_matching_ignores_names_and_media(tmp_path):
    create(tmp_path, {"artist/unrelated-name.PSARC": b"source", "song.psarc.bak": b"ignored",
                      "actual-title.psarc": b"other"})
    adapter = Archive({"unrelated-name.PSARC": [document()], "actual-title.psarc": [document(8)]})
    lookup = index(adapter)
    summary = lookup.build(tmp_path)
    assert summary["complete"] and summary["archive_count"] == 2
    rows = lookup.candidates([document()])
    assert len(rows) == 1 and rows[0]["relative_path"] == "artist/unrelated-name.PSARC"
    assert set(adapter.calls) == {"unrelated-name.PSARC", "actual-title.psarc"}


def test_candidate_must_contain_every_arrangement_key(tmp_path):
    create(tmp_path, {"all.psarc": b"all", "lead-only.psarc": b"lead"})
    lookup = index(Archive({"all.psarc": [document(), document(8)], "lead-only.psarc": [document()]}))
    lookup.build(tmp_path)
    assert [r["relative_path"] for r in lookup.candidates([document(), document(8)])] == ["all.psarc"]
    assert lookup.candidates([document(9)]) == []
    assert lookup.candidates([]) == []


def test_different_archives_remain_candidates_and_exact_duplicates_are_exposed(tmp_path):
    create(tmp_path, {"a.psarc": b"same", "copy.psarc": b"same", "b.psarc": b"different"})
    lookup = index(Archive({name: [document()] for name in ("a.psarc", "copy.psarc", "b.psarc")}))
    summary = lookup.build(tmp_path)
    rows = lookup.candidates([document()])
    assert len(rows) == 2
    assert rows[0]["duplicate_paths"] == [str(tmp_path / "copy.psarc")]
    assert summary["indexed_archive_count"] == 3 and summary["unique_archive_count"] == 2
    assert summary["duplicate_archive_count"] == 1 and summary["chart_count"] == 2
    rows[0]["duplicate_paths"].clear()
    assert lookup.candidates([document()])[0]["duplicate_paths"]


def test_decoder_failure_is_reported_and_prevents_uniqueness_claim(tmp_path):
    class DecoderError(Exception):
        pass
    create(tmp_path, {"good.psarc": b"good", "broken.psarc": b"bad"})
    lookup = index(Archive({"good.psarc": [document()], "broken.psarc": DecoderError("truncated table")}))
    summary = lookup.build(tmp_path)
    assert not summary["complete"] and summary["error_count"] == 1
    assert summary["errors"] == [{"relative_path": "broken.psarc", "message": "truncated table"}]
    assert len(lookup.candidates([document()])) == 1  # Coarse candidate, not a unique-source approval.


def test_failed_chart_does_not_leave_partially_indexed_archive(tmp_path):
    create(tmp_path, {"bad.psarc": b"bad"})
    lookup = index(Archive({"bad.psarc": [document(), {"notes": "broken"}]}))
    summary = lookup.build(tmp_path)
    assert not summary["complete"] and summary["indexed_archive_count"] == 0
    assert lookup.candidates([document()]) == []


def test_progress_runs_before_decode_and_can_cancel(tmp_path):
    create(tmp_path, {"one.psarc": b"one"})
    adapter = Archive({"one.psarc": [document()]})
    lookup, seen = index(adapter), []
    def progress(state):
        seen.append(state)
        if state["phase"] == "indexing":
            assert adapter.calls == []
            return False
    summary = lookup.build(tmp_path, on_progress=progress)
    assert summary["cancelled"] and not summary["complete"] and adapter.calls == []
    assert seen[-1]["current"] == "one.psarc" and seen[-1]["total"] == 1


def test_cancel_event_rechecked_after_progress_and_reader(tmp_path):
    create(tmp_path, {"one.psarc": b"one", "two.psarc": b"two"})
    adapter, stop = Archive({"one.psarc": [document()], "two.psarc": [document()]}), threading.Event()
    lookup = index(adapter)
    def progress(state):
        if state["phase"] == "indexing":
            stop.set()
    assert lookup.build(tmp_path, stop, progress)["cancelled"] and not adapter.calls
    stop.clear()
    original = adapter.read_source_charts
    def read(value):
        result = original(value)
        stop.set()
        return result
    adapter.read_source_charts = read
    summary = lookup.build(tmp_path, stop)
    assert summary["cancelled"] and len(adapter.calls) == 1
    assert lookup.candidates([document()]) == []


def test_archive_limit_aborts_before_unusable_partial_decode(tmp_path, monkeypatch):
    create(tmp_path, {f"{i}.psarc": str(i).encode() for i in range(3)})
    monkeypatch.setattr(load("source_folder_index"), "MAX_ARCHIVES", 2)
    adapter = Archive({})
    summary = index(adapter).build(tmp_path)
    assert summary["limit_reached"] and not summary["complete"]
    assert summary["archive_count"] == 2 and adapter.calls == []


def test_entry_limit_bounds_folders_without_archives(tmp_path, monkeypatch):
    create(tmp_path, {f"{i}.txt": b"ignored" for i in range(3)})
    monkeypatch.setattr(load("source_folder_index"), "MAX_ENTRIES", 2)
    summary = index(Archive({})).build(tmp_path)
    assert summary["limit_reached"] and not summary["complete"]


def test_directory_read_failure_is_incomplete(tmp_path, monkeypatch):
    (tmp_path / "denied").mkdir()
    module = load("source_folder_index")
    scan = module.os.scandir
    def scandir(path):
        if Path(path).name == "denied":
            raise PermissionError("no access")
        return scan(path)
    monkeypatch.setattr(module.os, "scandir", scandir)
    summary = index(Archive({})).build(tmp_path)
    assert not summary["complete"] and summary["errors"][0]["relative_path"] == "denied"


def test_linked_file_and_folder_are_not_followed(tmp_path):
    selected, outside = tmp_path / "selected", tmp_path / "outside"
    selected.mkdir()
    outside.mkdir()
    create(outside, {"source.psarc": b"outside"})
    try:
        (selected / "linked.psarc").symlink_to(outside / "source.psarc")
        (selected / "linked-folder").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation is unavailable on this host.")
    adapter = Archive({})
    summary = index(adapter).build(selected)
    assert not summary["complete"] and summary["skipped_link_count"] == 2
    assert summary["archive_count"] == 0 and not adapter.calls


def test_windows_junction_attribute_is_treated_as_link(tmp_path, monkeypatch):
    module = load("source_folder_index")
    class ReparsePath:
        def lstat(self):
            return NS(st_mode=0, st_file_attributes=0x400)
    assert module._linked(ReparsePath())


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_actual_windows_junction_does_not_escape_selected_folder(tmp_path):
    import subprocess
    selected, outside = tmp_path / "selected", tmp_path / "outside"
    selected.mkdir()
    outside.mkdir()
    create(outside, {"source.psarc": b"outside"})
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(selected / "junction"), str(outside)],
                            capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    adapter = Archive({})
    summary = index(adapter).build(selected)
    assert not summary["complete"] and summary["skipped_links"] == ["junction"]
    assert summary["archive_count"] == 0 and not adapter.calls


def test_actual_bounded_reader_failure_is_contained(tmp_path):
    import struct
    pytest.importorskip("construct")
    pytest.importorskip("cryptography")
    data = bytearray(32)
    struct.pack_into(">I", data, 12, 9 * 1024 * 1024)
    struct.pack_into(">I", data, 20, 1)
    create(tmp_path, {"oversized-table.psarc": data})
    summary = index(load("source_archive")).build(tmp_path)
    assert not summary["complete"] and summary["error_count"] == 1
    assert "table exceeds" in summary["errors"][0]["message"]


def test_missing_or_nonfolder_root_is_rejected(tmp_path):
    create(tmp_path, {"file.psarc": b"file"})
    for value in (tmp_path / "missing", tmp_path / "file.psarc"):
        with pytest.raises(ValueError, match="ordinary folder"):
            index(Archive({})).build(value)


def test_scope_changed_to_link_after_discovery_is_rejected(tmp_path, monkeypatch):
    create(tmp_path, {"one.psarc": b"one"})
    module, adapter = load("source_folder_index"), Archive({})
    linked = module._linked
    switched = False
    def progress(state):
        nonlocal switched
        if state["phase"] == "indexing":
            switched = True
    monkeypatch.setattr(module, "_linked", lambda p: switched and Path(p).name == "one.psarc" or linked(p))
    summary = index(adapter).build(tmp_path, on_progress=progress)
    assert not summary["complete"] and summary["error_count"] == 1 and not adapter.calls


def test_build_resets_previous_candidates_and_summary_is_not_mutable(tmp_path):
    create(tmp_path, {"a.psarc": b"a"})
    lookup = index(Archive({"a.psarc": [document()]}))
    lookup.build(tmp_path)
    lookup.summary["complete"] = False
    assert lookup.summary["complete"]
    stop = threading.Event()
    stop.set()
    assert lookup.build(tmp_path, stop)["cancelled"]
    assert lookup.candidates([document()]) == []


def test_index_holds_no_source_objects(tmp_path):
    import gc
    import weakref
    class Song:
        pass
    references = []
    def read(value):
        song = Song()
        references.append(weakref.ref(song))
        return {"path": Path(value), "sha256": "1" * 64,
                "charts": [{"member": "songs/bin/generic/lead.sng", "song": song}]}
    create(tmp_path, {"a.psarc": b"a"})
    lookup = index(NS(read_source_charts=read), NS(source_document=lambda song: document()))
    lookup.build(tmp_path)
    gc.collect()
    assert references[0]() is None


def test_key_preserves_exact_match_eligibility_across_bend_and_ladder_variants():
    chart, module = load("source_chart"), load("source_folder_index")
    note = NS(time=10.0, string=1, fret=127, sustain=1.0, mask=0x20000,
              bends=[NS(time=10.4, step=2)], slideTo=-1, slideUnpitchTo=-1,
              bend_time=2, chordId=4294967295)
    source = NS(levels=[NS(difficulty=0, notes=[note])], phraseIterations=[], phrases=[],
                chordTemplates=[], chordNotes=[], metadata=NS(capo=-1, tuning=[0] * 6))
    source_doc = chart.source_document(source)
    stored = {"notes": [{"t": 10, "s": 1, "f": 127, "sus": 1, "mt": True, "bn": 2}]}
    assert chart.recovery_patch(stored, source)[1]
    assert module.topology_key(stored) == module.topology_key(source_doc)
    stored["notes"][0].update(bn=.5, bnv=[{"t": 9, "v": .5}], bt=3)
    assert module.topology_key(stored) == module.topology_key(source_doc)


def test_key_keeps_chord_membership_and_rejects_ambiguous_string_ids():
    module = load("source_folder_index")
    left = {"chords": [{"notes": [{"s": 1, "f": 7}, {"s": 2, "f": 9}]}]}
    right = {"chords": [{"notes": [{"s": 1, "f": 7}]}, {"notes": [{"s": 2, "f": 9}]}]}
    assert module.topology_key(left) != module.topology_key(right)
    with pytest.raises(ValueError, match="ambiguous"):
        module.topology_key({"notes": [{"s": True, "f": 7}]})


def test_streaming_index_retains_all_coarse_matching_members_for_exact_selection(tmp_path):
    create(tmp_path, {"compilation.psarc": b"compilation"})
    def inspect(value, visitor):
        for i in range(586):
            doc = document(7 if i in (100, 200) else 8)
            assert visitor({"member": f"chart-{i}.sng", "song": doc}) is not False
        return {"path": Path(value), "sha256": "1" * 64, "complete": True, "cancelled": False}
    lookup = index(NS(inspect_source_charts=inspect,
                      read_source_charts=lambda value: pytest.fail("Must stream compilations")))
    summary = lookup.build(tmp_path)
    assert summary["complete"] and summary["chart_count"] == 586
    assert lookup.candidates([document()])[0]["members"] == ["chart-100.sng", "chart-200.sng"]
    assert len(lookup.candidates([document(), document(8)])[0]["members"]) == 586


def test_incomplete_stream_does_not_publish_partial_topology(tmp_path):
    create(tmp_path, {"compilation.psarc": b"compilation"})
    def inspect(value, visitor):
        visitor({"member": "lead.sng", "song": document()})
        return {"path": Path(value), "sha256": "1" * 64, "complete": False, "cancelled": True}
    lookup = index(NS(inspect_source_charts=inspect))
    summary = lookup.build(tmp_path)
    assert summary["cancelled"] and not summary["complete"]
    assert lookup.candidates([document()]) == []


def test_known_empty_placeholders_are_exposed_without_marking_scope_incomplete(tmp_path):
    create(tmp_path, {"compilation.psarc": b"compilation"})
    def inspect(value, visitor):
        visitor({"member": "lead.sng", "song": document()})
        return {"path": Path(value), "sha256": "1" * 64, "complete": True,
                "empty_members": ["empty-placeholder.sng"]}
    lookup = index(NS(inspect_source_charts=inspect))
    summary = lookup.build(tmp_path)
    assert summary["complete"] and summary["empty_member_count"] == 1
    assert summary["empty_members"] == [{"relative_path": "compilation.psarc", "member": "empty-placeholder.sng"}]
