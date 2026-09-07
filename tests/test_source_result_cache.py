import copy
import gc
import hashlib
import weakref
from types import SimpleNamespace as NS

import pytest

from test_source_recovery import load, read_all
from test_source_preview_validation import setup, write_members


def cache(**limits):
    return load("source_result_cache").SourceResultCache(**limits)


def test_cache_has_explicit_encoded_byte_entry_and_item_bounds_and_lru_eviction():
    default = cache()
    assert (default.max_bytes, default.max_entries, default.max_item_bytes) == (16 * 1024 ** 2, 64, 4 * 1024 ** 2)
    bounded = cache(max_bytes=30, max_entries=2, max_item_bytes=20)
    assert bounded.put("a", "aaaaaaaaaa") and bounded.put("b", "bbbbbbbbbb")
    assert bounded.get("a") == "aaaaaaaaaa"
    assert bounded.put("c", "cccccccccc")
    assert bounded.get("b") is None and bounded.get("a") is not None
    assert bounded._bytes <= 30 and len(bounded._items) <= 2
    assert not bounded.put("large", "x" * 21)
    assert bounded.get("large") is None
    assert all(isinstance(value, bytes) for value in bounded._items.values())
    assert bounded.put("a", "z")
    assert bounded._bytes == sum(len(v) for v in bounded._items.values())


def test_cached_values_are_independent_and_do_not_retain_source_objects():
    class Song:
        pass

    result_cache, source = cache(), Song()
    reference = weakref.ref(source)
    chart = NS(recovery_patch=lambda document, song: (copy.deepcopy(document), [], []), SourceBendError=ValueError)
    entry = {"member": "lead.sng", "sha256": "1" * 64, "song": source}
    document = {"notes": [{"t": 1, "s": 0, "f": 2}]}
    first = result_cache.patch(chart, document, "target", "archive", entry)
    first[0]["notes"][0]["f"] = 19
    assert result_cache.patch(chart, document, "target", "archive", entry)[0]["notes"][0]["f"] == 2
    del first, entry, source
    gc.collect()
    assert reference() is None


@pytest.mark.parametrize("changed", ["archive", "member", "chart", "target", "callback"])
def test_patch_identity_changes_never_reuse_another_result(changed):
    result_cache, calls = cache(), []

    def patch(document, song):
        calls.append(True)
        return document, [], []

    chart = NS(recovery_patch=patch, SourceBendError=ValueError)
    entry = {"member": "lead.sng", "sha256": "1" * 64, "song": object()}
    result_cache.patch(chart, {}, "target", "archive", entry)
    result_cache.patch(chart, {}, "target", "archive", entry)
    assert len(calls) == 1
    archive, target = "archive", "target"
    if changed == "archive":
        archive = "different archive"
    elif changed == "target":
        target = "different raw bytes"
    elif changed == "member":
        entry["member"] = "alternate.sng"
    elif changed == "chart":
        entry["sha256"] = "2" * 64
    else:
        chart.recovery_patch = lambda document, song: patch(document, song)
    result_cache.patch(chart, {}, target, archive, entry)
    assert len(calls) == 2


@pytest.mark.parametrize("invalid_bend", [False, True])
def test_cached_errors_preserve_unsafe_source_candidate_classification(invalid_bend):
    class SourceBendError(ValueError):
        pass

    result_cache, calls = cache(), []
    error_type = SourceBendError if invalid_bend else ValueError

    def patch(document, song):
        calls.append(True)
        raise error_type("Unsafe ordered curve" if invalid_bend else "Wrong arrangement")

    chart = NS(recovery_patch=patch, SourceBendError=SourceBendError)
    entry = {"member": "lead.sng", "sha256": "1" * 64, "song": object()}
    for _ in range(2):
        with pytest.raises(error_type) as error:
            result_cache.patch(chart, {}, "target", "archive", entry)
        assert isinstance(error.value, SourceBendError) == invalid_bend
    assert len(calls) == 1


def test_oversized_patch_is_used_but_never_retained():
    result_cache, calls = cache(max_item_bytes=20), []

    def patch(document, song):
        calls.append(True)
        return {"large": "x" * 100}, [], []

    chart = NS(recovery_patch=patch, SourceBendError=ValueError)
    entry = {"member": "lead.sng", "sha256": "1" * 64, "song": object()}
    for _ in range(2):
        assert result_cache.patch(chart, {}, "target", "archive", entry)[0]["large"] == "x" * 100
    assert len(calls) == 2 and not result_cache._items


@pytest.mark.parametrize("changed", ["raw", "member", "context", "version", "callback"])
def test_validation_cache_uses_complete_document_context_and_validator_identity(changed):
    result_cache, calls = cache(), []
    def callback(*a, **k):
        return None
    service = NS(_validator_version="v1", _validate_reviewed_arrangement=callback,
                 _reviewed_validation_contexts=lambda manifest, member: manifest)

    def reports(document, manifest, member):
        calls.append((document, manifest, member))
        return [{"findings": []}]

    service._reviewed_validation_reports = reports
    module = load("repair")
    raw, manifest, member = b'{"notes":[]}', [{"duration": 20, "entry": {"capo": 0}}], "lead.json"
    result_cache.reports(service, module, raw, manifest, member)
    result_cache.reports(service, module, raw, manifest, member)
    assert len(calls) == 1
    if changed == "raw":
        raw += b" "
    elif changed == "member":
        member = "rhythm.json"
    elif changed == "context":
        manifest[0]["entry"]["capo"] = 2
    elif changed == "version":
        service._validator_version = "v2"
    else:
        service._validate_reviewed_arrangement = lambda *a, **k: None
    result_cache.reports(service, module, raw, manifest, member)
    assert len(calls) == 2


def test_audio_variants_reuse_chart_work_but_refresh_hashes_and_keep_distinct_plans(tmp_path):
    _, service, engine, package, original, members, _ = setup(tmp_path)
    variant = package.with_name("Song (No Guitar).feedpak")
    variant_members = {**members, "manifest.yaml": b"title: Song (No Guitar)\n" + members["manifest.yaml"],
                       "audio.ogg": b"a different stem with identical chart documents"}
    write_members(variant, variant_members)
    patch_calls, validation_calls, hashes = [], [], []
    patch, validate, source_hash = engine.chart.recovery_patch, service._validate_reviewed_arrangement, engine.archive.source_hash

    def matching(document, source):
        patch_calls.append(True)
        return patch(document, source)

    def validation(document, **context):
        validation_calls.append(True)
        return validate(document, **context)

    def hashed(path):
        hashes.append(str(path))
        return source_hash(path)

    engine.chart = NS(recovery_patch=matching, SourceBendError=engine.chart.SourceBendError)
    service._validate_reviewed_arrangement = validation
    engine.archive.source_hash = hashed
    ordinary = engine.preview(package.name, original)
    assert len(patch_calls) == 1 and len(validation_calls) == 4
    first_hash_count = len(hashes)
    no_guitar = engine.preview(variant.name, original)
    assert len(patch_calls) == 1 and len(validation_calls) == 4
    assert len(hashes) >= first_hash_count + 2  # Current source read and final evidence guard.
    assert ordinary["plan_id"] != no_guitar["plan_id"] and ordinary["change_count"] == no_guitar["change_count"] == 6
    assert read_all(package) == members and read_all(variant) == variant_members
    variant_members["lead.json"] += b"\n"
    write_members(variant, variant_members)
    changed = engine.preview(variant.name, original)
    assert len(patch_calls) == 2 and changed["plan_id"] != no_guitar["plan_id"]
    assert changed["evidence"]["lead.json"] == hashlib.sha256(variant_members["lead.json"]).hexdigest()
