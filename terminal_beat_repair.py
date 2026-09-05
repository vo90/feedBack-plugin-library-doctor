"""Read-only, deliberately narrow assessment of redundant terminal beat tails.

This describes Feedpak records, not the meaning of flags in an upstream SNG.
Package corroboration is mandatory; a local shape match alone is not permission
to mutate a song. No function in this module writes files.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import zipfile

from jsonschema import Draft202012Validator

RULE_CODE = "timeline.terminal-duplicate-beats"


def finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def identity(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def tail_start(beats):
    """Return the unique removable suffix start, or None (never guess)."""
    if not isinstance(beats, list) or len(beats) < 4:
        return None
    if not all(
        isinstance(b, dict) and finite(b.get("time"))
        and type(b.get("measure")) is int for b in beats
    ):
        return None
    cut = next((i for i in range(1, len(beats))
                if beats[i]["time"] <= beats[i - 1]["time"]), None)
    if cut is None or cut < 3 or not 1 <= len(beats) - cut <= 3:
        return None
    try:
        if all(identity(b) == identity(beats[cut - 2 + i % 2])
               for i, b in enumerate(beats[cut:])):
            return cut
    except (ValueError, TypeError):
        pass
    return None


def non_strict(beats):
    if not isinstance(beats, list):
        return False
    times = [b.get("time") for b in beats if isinstance(b, dict)]
    return any(finite(a) and finite(b) and b <= a for a, b in zip(times, times[1:]))


def input_paths(manifest):
    """All declarations whose contents can influence this closed repair."""
    result = []
    for entry in manifest.get("arrangements", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise ValueError("Every arrangement must declare a readable JSON file.")
        result.append(entry["file"])
    for key in ("song_timeline", "lyrics", "rigs"):
        if key in manifest:
            result.append(manifest[key])
    for track in manifest.get("lyric_tracks", []):
        if not isinstance(track, dict):
            raise ValueError("A lyric declaration is malformed.")
        result.append(track.get("file"))
    if not all(isinstance(p, str) and p.endswith(".json") for p in result):
        raise ValueError("Terminal beat repair requires ordinary declared JSON files.")
    return list(dict.fromkeys(result))


def package_inventory(package):
    """Bounded inventory, without following directory links or reading media."""
    if package.is_file():
        with zipfile.ZipFile(package) as archive:
            names = [i.filename for i in archive.infolist() if not i.is_dir()]
    else:
        names = []
        for parent, dirs, files in os.walk(package, followlinks=False):
            for name in dirs + files:
                if (Path(parent) / name).is_symlink() or (Path(parent) / name).is_junction():
                    raise ValueError("Linked package members require manual review.")
            names.extend((Path(parent) / n).relative_to(package).as_posix() for n in files)
            if len(names) > 10000:
                raise ValueError("Package inventory exceeds the terminal repair limit.")
    if len(names) > 10000 or len(names) != len(set(names)):
        raise ValueError("An oversized or ambiguous member inventory requires manual review.")
    return sorted(names)


def check_inventory(manifest, names):
    known = set(input_paths(manifest)) | {"manifest.yaml"}
    for key in ("cover", "preview"):
        if key in manifest:
            known.add(manifest[key])
    for stem in manifest.get("stems", []):
        if not isinstance(stem, dict) or set(stem) - {"id", "file", "codec", "default"}:
            raise ValueError("Unknown stem metadata requires manual review.")
        known.add(stem.get("file"))
    if set(names) - known:
        raise ValueError("Undeclared package members may contain references; manual review is required.")


def _closed(value, schema, root):
    """The published schema permits extensions; this repair does not."""
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].split("/")[-1]]
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if set(value) - set(properties):
            return False
        return all(_closed(v, properties[k], root) for k, v in value.items())
    if isinstance(value, list):
        return all(_closed(v, schema.get("items", {}), root) for v in value)
    return not isinstance(value, float) or math.isfinite(value)


def _known_arrangement(document, schema):
    data = dict(document)
    # These legacy FeedForge fields are explicit, non-indexed metadata.
    ext = data.pop("ext", {})
    source = ext.get("source", {}) if isinstance(ext, dict) else None
    if (not isinstance(ext, dict) or set(ext) - {"source"}
            or not isinstance(source, dict)
            or set(source) - {"format", "arrangement_properties"}):
        return False
    props = source.get("arrangement_properties", {})
    if (not isinstance(props, dict) or set(props) - {
            "pathLead", "pathRhythm", "pathBass", "bonusArr", "represent"}
            or any(type(v) is not int for v in props.values())
            or ("format" in source and not isinstance(source["format"], str))):
        return False
    stats = data.pop("stats", {})
    if not isinstance(stats, dict) or set(stats) - {"events", "notes"}:
        return False
    # Tone gear dictionaries have vendor-defined knob names, not beat positions.
    # Unknown tone control properties still fail closed.
    tones = data.pop("tones", {})
    if not isinstance(tones, dict) or set(tones) - {
            "base", "base_rig", "changes", "definitions"}:
        return False
    for change in tones.get("changes", []):
        if not isinstance(change, dict) or set(change) - {"t", "name", "rig"}:
            return False
    return Draft202012Validator(schema).is_valid(data) and _closed(data, schema, schema)


def arrangement_schema():
    schema = json.loads((Path(__file__).with_name("schemas") / "arrangement.schema.json").read_text())
    defs = schema["$defs"]
    techniques = {k: {"type": "boolean"} for k in (
        "ac", "fhm", "mt", "ho", "po", "pm", "ln", "slp", "tap", "harm",
        "ph", "tr", "ignore"
    )}
    for kind in ("note", "chordNote", "chord"):
        defs[kind]["properties"].update(techniques)
    defs["note"]["properties"]["v"] = {"type": "number"}
    defs["chordNote"]["properties"].update(defs["note"]["properties"])
    defs["phrase"]["properties"]["levels"]["items"] = {
        "type": "object", "properties": {
            "difficulty": {"type": "integer"},
            **{k: schema["properties"][k] for k in ("notes", "chords", "anchors", "handshapes")},
        },
    }
    return schema


def has_beat_reference(value, *, root=True):
    if isinstance(value, dict):
        return any(
            ("beat" in k.lower() and not (root and k == "beats"))
            or has_beat_reference(v, root=False) for k, v in value.items()
        )
    if isinstance(value, list):
        return any(has_beat_reference(v, root=False) for v in value)
    return False


def _event_ends(value, *, parent_time=None):
    if isinstance(value, dict):
        t = value.get("t", parent_time)
        for key in ("t", "sus", "d", "time", "start_time", "end_time"):
            if key in value and not finite(value[key]):
                raise ValueError("Event times and durations must be finite numbers.")
        if t is not None:
            if not finite(t):
                raise ValueError("An event has no valid time.")
            for key in ("sus", "d"):
                if value.get(key, 0) < 0:
                    raise ValueError("An event has a negative duration.")
            yield t + max(value.get("sus", 0), value.get("d", 0))
        for key, child in value.items():
            if key in {"time", "start_time", "end_time"}:
                yield child
            if key not in {"beats", "ext", "stats", "templates", "definitions"}:
                if key == "bnv" and t is not None:
                    for point in child:
                        if not finite(point.get("t")) or point["t"] < 0:
                            raise ValueError("A bend point has invalid relative timing.")
                        yield t + point["t"]
                else:
                    yield from _event_ends(child, parent_time=t)
    elif isinstance(value, list):
        for child in value:
            yield from _event_ends(child, parent_time=parent_time)


def assess_package(manifest, documents):
    """Return a blocker message or None. Caller binds every input to its plan."""
    allowed_manifest = {
        "feedpak_version", "title", "artist", "duration", "arrangements", "stems",
        "album", "year", "lyrics", "lyrics_source", "language", "lyric_tracks",
        "cover", "preview", "rigs", "minus_mix", "song_timeline",
    }
    try:
        if set(manifest) - allowed_manifest:
            raise ValueError("Unknown package metadata or sidecars require manual review.")
        paths = input_paths(manifest)
        if any(p not in documents for p in paths):
            raise ValueError("Every declared repair input must be readable.")
        arrangement_paths = []
        for entry in manifest["arrangements"]:
            if set(entry) - {"id", "name", "file", "tuning", "capo", "centOffset",
                             "type", "event_count", "note_count"}:
                raise ValueError("Unknown arrangement declarations require manual review.")
            arrangement_paths.append(entry["file"])
        grid_paths = list(dict.fromkeys(arrangement_paths + (
            [manifest["song_timeline"]] if "song_timeline" in manifest else [])))
        grids = []
        for path in grid_paths:
            d = documents[path]
            if not isinstance(d, dict):
                raise ValueError("A declared beat document is malformed.")
            b = d.get("beats", [])
            if not isinstance(b, list):
                raise ValueError("A declared beat grid is malformed.")
            if b:
                if not all(isinstance(x, dict) and set(x) == {"time", "measure"}
                           and finite(x["time"]) and x["time"] >= 0
                           and type(x["measure"]) is int for x in b):
                    raise ValueError("Unknown beat properties or invalid beats require manual review.")
                grids.append((b, tail_start(b)))
        affected = [(b, cut) for b, cut in grids if cut is not None]
        if not affected:
            return None
        clean = [b for b, cut in grids if cut is None and not non_strict(b)]
        duration = manifest.get("duration")
        if not finite(duration) or duration <= 0:
            raise ValueError("A valid song duration is required.")
        for b, cut in grids:
            if cut is None and non_strict(b):
                raise ValueError("Another grid contains an unrelated timing defect.")
        for b, cut in affected:
            if not any(identity(b[:cut]) == identity(c) for c in clean):
                raise ValueError("No existing clean grid exactly corroborates this tail.")
            if not b[cut - 2]["time"] <= duration <= b[cut - 1]["time"]:
                raise ValueError("The repeated tail does not bracket the song end.")
        boundary = min(b[cut]["time"] for b, cut in affected)
        schema_dir = Path(__file__).with_name("schemas")
        schema = arrangement_schema()
        if has_beat_reference(manifest) or any(has_beat_reference(d) for d in documents.values()):
            raise ValueError("Stored beat references require manual review.")
        for path in arrangement_paths:
            d = documents[path]
            if not _known_arrangement(d, schema):
                raise ValueError("Unknown arrangement properties or references require manual review.")
            coverage_end = min(b[cut - 1]["time"] for b, cut in affected)
            if any(not 0 <= p["start_time"] <= p["end_time"] <= coverage_end
                   for p in d.get("phrases", [])):
                raise ValueError("A phrase window falls outside the retained beat coverage.")
            # Silent phrase windows may extend to song duration; playable content may not.
            content = dict(d)
            content["phrases"] = [
                {k: v for k, v in phrase.items() if k not in {"start_time", "end_time"}}
                for phrase in d.get("phrases", [])
            ]
            if any(t >= boundary or t < 0 for t in _event_ends(content)):
                raise ValueError("An event or sustain reaches the repeated terminal region.")
        for path in paths:
            if path in grid_paths or path == manifest.get("rigs"):
                continue
            cues = documents[path]
            if not isinstance(cues, list) or any(
                not isinstance(c, dict) or set(c) != {"t", "d", "w"}
                or not isinstance(c["w"], str) for c in cues
            ):
                raise ValueError("Unknown lyric properties require manual review.")
            if any(t >= boundary or t < 0 for t in _event_ends(cues)):
                raise ValueError("A lyric reaches the repeated terminal region.")
        if "song_timeline" in manifest:
            d = documents[manifest["song_timeline"]]
            s = json.loads((schema_dir / "song-timeline.schema.json").read_text())
            if not Draft202012Validator(s).is_valid(d) or not _closed(d, s, s):
                raise ValueError("Unknown timeline properties require manual review.")
            if any(t >= boundary or t < 0 for t in _event_ends(d)):
                raise ValueError("A timeline event reaches the repeated terminal region.")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return str(exc) or "Unsupported data requires manual review."
    return None
