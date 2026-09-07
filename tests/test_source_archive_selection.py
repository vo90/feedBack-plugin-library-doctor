import gc
import hashlib
import struct
import sys
import weakref
from importlib import util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).parents[1]


def load():
    name = "doctor_archive_selection_tests"
    if name not in sys.modules:
        spec = util.spec_from_file_location(name, ROOT / "source_archive.py")
        module = util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def archive(tmp_path, monkeypatch, count=3, payload=b"source"):
    """Exercise reader selection/streaming; block/decrypt bounds have separate tests."""
    module = load()
    path = tmp_path / "compilation.psarc"
    header = bytearray(32)
    struct.pack_into(">I", header, 12, 32)
    struct.pack_into(">I", header, 20, count + 1)
    path.write_bytes(header)
    names = [f"songs/bin/generic/chart-{i}.sng" for i in range(count)]
    entries = [NS(length=1, index=i) for i in range(count + 1)]
    table = NS(bom=NS(entries=entries, zlength=[]))
    refs, parsed = [], []

    class Song:
        levels = [1]

    def parse(data):
        song = Song()
        refs.append(weakref.ref(song))
        parsed.append(data)
        return song

    def entry(stream, value, lengths, size, limit):
        return "\n".join(names).encode() if value.index == 0 else payload

    monkeypatch.setattr(module, "_reader", lambda: (
        NS(parse_stream=lambda stream: table), lambda raw, key: raw, None, None, NS(parse=parse)))
    monkeypatch.setattr(module, "_read_entry", entry)
    return module, path, names, parsed, refs


def test_streams_compilation_above_legacy_limit_without_retaining_songs(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch, count=586)
    visited = []
    def visit(entry):
        assert sum(ref() is not None for ref in refs) == 1
        visited.append(entry["member"])
    result = module.inspect_source_charts(path, visit)
    gc.collect()
    assert result["complete"] and not result["cancelled"]
    assert result["chart_count"] == 586 and result["expanded_bytes"] == 586 * len(b"source")
    assert visited == names and all(ref() is None for ref in refs)
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_selected_members_only_decode_exact_requested_charts(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch, count=586)
    result = module.read_source_charts(path, members=[names[500], names[10]])
    assert [row["member"] for row in result["charts"]] == [names[10], names[500]]
    assert len(parsed) == 2 and result["expanded_bytes"] == 12
    assert result["complete"] and result["chart_count"] == 2


def test_default_reader_keeps_legacy_bound_and_actionable_batch_guidance(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch, count=129)
    with pytest.raises(ValueError, match="folder batch"):
        module.read_source_charts(path)
    assert parsed == []


def test_stream_member_count_is_bounded_before_decode(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch, count=1025)
    with pytest.raises(ValueError, match="1,024"):
        module.inspect_source_charts(path, lambda entry: None)
    assert parsed == []


@pytest.mark.parametrize("members", [[], "lead.sng", ["x"] * 2, [None], ["x"] * 129,
                                     ["songs/bin/generic/chart-999.sng"]])
def test_invalid_or_missing_selection_never_silently_drops_members(tmp_path, monkeypatch, members):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        module.read_source_charts(path, members=members)
    assert parsed == []


def test_expanded_aggregate_counts_actual_decrypted_bytes_before_parse(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch, payload=b"1234")
    monkeypatch.setattr(module, "MAX_EXPANDED_TOTAL", 7)
    with pytest.raises(ValueError, match="aggregate expansion"):
        module.inspect_source_charts(path, lambda entry: None)
    assert len(parsed) == 1  # The second expanded chart is rejected before allocating its Song.


def test_stream_visitor_cancellation_is_explicitly_incomplete(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch)
    result = module.inspect_source_charts(path, lambda entry: False)
    assert result["cancelled"] and not result["complete"] and result["chart_count"] == 1
    assert len(parsed) == 1


def test_changed_archive_during_stream_rejects_all_collected_keys(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch)
    def visit(entry):
        path.write_bytes(b"changed archive" + bytes(32))
    with pytest.raises(ValueError, match="changed during"):
        module.inspect_source_charts(path, visit)


def test_visitor_failure_is_not_reported_as_complete(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch)
    def visit(entry):
        raise ValueError("malformed source topology")
    with pytest.raises(ValueError, match="topology"):
        module.inspect_source_charts(path, visit)
    assert len(parsed) == 1


def test_declared_and_decoded_empty_placeholder_is_reported_without_topology(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch, count=1, payload=b"")
    reader = module._reader()
    header = reader[0].parse_stream(None)
    header.bom.entries[1].length = 0
    result = module.inspect_source_charts(path, lambda entry: pytest.fail("Empty placeholder is not a chart"))
    assert result["complete"] and result["empty_members"] == names
    assert result["expanded_bytes"] == 0 and result["chart_count"] == 0 and not parsed


def test_nonempty_member_with_invalid_empty_decoding_remains_blocking(tmp_path, monkeypatch):
    module, path, names, parsed, refs = archive(tmp_path, monkeypatch, count=1, payload=b"")
    reader = module._reader()
    def invalid(data):
        raise ValueError("missing source fields")
    monkeypatch.setattr(module, "_reader", lambda: (*reader[:-1], NS(parse=invalid)))
    with pytest.raises(ValueError, match="could not be decoded"):
        module.inspect_source_charts(path, lambda entry: None)
