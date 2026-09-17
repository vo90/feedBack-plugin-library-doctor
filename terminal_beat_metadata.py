"""Closed metadata proof for exact redundant terminal-beat suffixes.

This does not prove the beat/event edit by itself. The caller also validates all
chart/lyric data, strict parsing, inventory, bounds and candidate preservation.
Only recognized, typed non-beat-indexed metadata is admitted here. Schema
extension points and opaque plugin state are not evidence of safe references.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from jsonschema import Draft202012Validator


_RIG_SCHEMA_TEXT = (Path(__file__).with_name("schemas") / "rigs.schema.json").read_text(encoding="utf-8")
_MANIFEST_SCHEMA_TEXT = (Path(__file__).with_name("schemas") / "manifest.schema.json").read_text(encoding="utf-8")


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _text(value):
    return isinstance(value, str)


def _id(value):
    return _text(value) and bool(value)


def _natural(value):
    return type(value) is int and value >= 0


def _scalar(value):
    return type(value) in (str, bool) or _finite(value)


def _list(value, predicate):
    return isinstance(value, list) and all(predicate(item) for item in value)


def _object(value, fields, required=()):
    return (isinstance(value, dict) and not set(value) - set(fields)
            and set(required) <= set(value)
            and all(fields[key](item) for key, item in value.items()))


def _need(valid, message):
    if not valid:
        raise ValueError(message)


def _knobs(value):
    return (isinstance(value, dict)
            and all(_id(key) and _scalar(item) for key, item in value.items()))


def _params(value):
    def parameter(item):
        return _scalar(item) or _object(item, {"value": _scalar, "unit": _text}, ("value",))
    return (isinstance(value, dict)
            and all(_id(key) and parameter(item) for key, item in value.items()))


def _pedal(value):
    return _object(value, {
        "Type": lambda v: v in ("Racks", "Pedals", "Amps", "Cabinets"),
        "Key": _id, "PedalKey": _id, "Category": _text, "KnobValues": _knobs,
    }, ("Type", "Key", "KnobValues"))


def _volume(value):
    if _finite(value):
        return True
    if not _text(value):
        return False
    try:
        return math.isfinite(float(value))
    except (ValueError, OverflowError):
        return False


def _slot(value):
    return _text(value) and bool(re.fullmatch(r"Amp|Cabinet|(?:Rack|PrePedal|PostPedal)[1-9]", value))


def _tone(value):
    return _object(value, {
        "GearList": lambda v: isinstance(v, dict) and all(
            _slot(key) and _pedal(pedal) for key, pedal in v.items()),
        "ToneDescriptors": lambda v: _list(v, _text),
        "NameSeparator": _text, "IsCustom": lambda v: type(v) is bool,
        "Volume": _volume, "Key": _id, "Name": _text,
        "SortOrder": lambda v: _finite(v) and v >= 0,
    }, ("GearList", "Key"))


def _source_ext(value, fields, required):
    return _object(value, {"source": lambda v: _object(v, fields, required)})


def _arrangement_source(value):
    return _source_ext(value, {
        "format": lambda v: v == "psarc-manifest2014",
        "arrangement_properties": lambda v: _object(v, {
            key: lambda flag: type(flag) is int and flag in (0, 1)
            for key in ("pathLead", "pathRhythm", "pathBass", "bonusArr", "represent")
        }),
    }, ("format",))


def _tone_bindings(value, boundary, rig_ids):
    def change(v):
        return (_object(v, {
            "t": lambda t: _finite(t) and 0 <= t < boundary,
            "name": _id, "rig": lambda r: _id(r) and r in rig_ids,
        }, ("t",)) and bool(set(v) & {"name", "rig"}))
    return _object(value, {
        "base": _id, "base_rig": lambda v: _id(v) and v in rig_ids,
        "changes": lambda v: _list(v, change),
        "definitions": lambda v: _list(v, _tone),
    })


def _minus_mix(value):
    def digest(v):
        return _text(v) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", v))
    def reuse(v):
        return _object(v, {
            "policy": _id, "plan_key": digest,
            "excluded_stems": lambda names: _list(names, _id),
            "fresh_package_sha256": digest, "donor_package_sha256": digest,
            "full_sha256": digest,
            "preview_sha256": lambda h: h is None or digest(h),
        }, ("policy", "plan_key", "excluded_stems", "fresh_package_sha256",
            "donor_package_sha256", "full_sha256"))
    return _object(value, {
        "excluded_stems": lambda v: _list(v, _id), "source_title": _text,
        "generator": lambda v: v == "minus_mix", "audio_reuse": reuse,
    }, ("excluded_stems", "source_title", "generator"))


def _manifest(value, boundary, rig_ids):
    def arrangement(v):
        return _object(v, {
            "id": _id, "name": _text, "file": _id, "type": _text,
            "tuning": lambda t: _list(t, lambda n: type(n) is int),
            "capo": _natural, "centOffset": _finite,
            "event_count": _natural, "note_count": _natural,
            "tones": lambda t: _tone_bindings(t, boundary, rig_ids),
        }, ("id", "file"))
    def stem(v):
        return _object(v, {"id": _id, "file": _id, "codec": _text,
                           "default": lambda b: type(b) is bool}, ("id", "file"))
    def lyrics(v):
        return _object(v, {key: _text for key in (
            "id", "file", "language", "kind", "lyrics_source", "name", "stem"
        )}, ("id", "file", "language", "kind"))
    fields = {key: _text for key in (
        "feedpak_version", "title", "artist", "album", "lyrics", "lyrics_source",
        "language", "cover", "preview", "rigs", "song_timeline"
    )}
    fields.update({"year": lambda v: type(v) is int,
                   "duration": lambda v: _finite(v) and v > 0,
                   "arrangements": lambda v: _list(v, arrangement),
                   "stems": lambda v: _list(v, stem),
                   "lyric_tracks": lambda v: _list(v, lyrics), "minus_mix": _minus_mix})
    return (_object(value, fields, ("title", "artist", "duration", "arrangements", "stems"))
            and Draft202012Validator(json.loads(_MANIFEST_SCHEMA_TEXT)).is_valid(value))


def _intent(value):
    return _object(value, {
        "kind": _text, "family": _text, "model": _text,
        "tags": lambda v: _list(v, _text),
        "gm": lambda v: _object(v, {
            "program": lambda n: _natural(n) and n <= 127,
            "percussion": lambda b: type(b) is bool, "kit": _natural,
        }),
    })


def _realization(value):
    # Opaque state may embed an unrecognized reference; it is deliberately absent.
    return _object(value, {
        "engine": lambda v: v in ("nam", "ir", "plugin", "builtin", "soundfont"),
        **{key: _text for key in ("ref", "sha256", "format", "id", "name", "vendor", "preset")},
        "channels": lambda v: _natural(v) and v > 0,
        "bank": _natural, "program": lambda v: _natural(v) and v <= 127,
        "params": _params,
    }, ("engine",))


def _automation(value, boundary):
    def point(v):
        return _object(v, {"t": lambda t: _finite(t) and 0 <= t < boundary,
                           "v": _scalar}, ("t", "v"))
    return (_object(value, {"param": _id, "points": lambda v: _list(v, point)},
                    ("param", "points"))
            and all(a["t"] < b["t"] for a, b in zip(value["points"], value["points"][1:])))


def _block(value, boundary):
    return _object(value, {
        "id": _id, "role": _text, "name": _text,
        "bypassed": lambda v: type(v) is bool,
        "mix": lambda v: _finite(v) and 0 <= v <= 1,
        "intent": _intent, "params": _params,
        "realizations": lambda v: _list(v, _realization),
        "automation": lambda v: _list(v, lambda lane: _automation(lane, boundary)),
        "ext": lambda v: _source_ext(v, {"slot": _slot, "pedal": _pedal}, ("slot", "pedal")),
    })


def _graph(value, blocks):
    ids = [block.get("id") for block in blocks]
    if not all(_id(v) for v in ids) or len(ids) != len(set(ids)) or set(ids) & {"input", "output"}:
        return False
    if not _object(value, {"nodes": lambda v: _list(v, _id),
                           "edges": lambda v: _list(v, lambda e: _list(e, _id) and len(e) == 2)},
                   ("nodes", "edges")):
        return False
    nodes = value["nodes"]
    allowed = set(ids) | {"input", "output"}
    return (len(nodes) == len(set(nodes)) and set(nodes) == allowed
            and all(a in allowed and b in allowed and a != b for a, b in value["edges"]))


def _rigs(value, boundary):
    def rig(v):
        return (_object(v, {
            "id": _id, "name": _text, "instrument": _text,
            "channels": lambda n: _natural(n) and n > 0,
            "blocks": lambda blocks: _list(blocks, lambda block: _block(block, boundary)),
            "graph": lambda graph: _graph(graph, v.get("blocks", [])),
            "ext": lambda ext: _source_ext(ext, {
                "format": lambda f: f == "psarc-tone2014", "tone_id": _natural,
                "definition": _tone,
            }, ("format", "tone_id", "definition")),
        }, ("id", "blocks")) and len([b["id"] for b in v["blocks"] if "id" in b])
                == len({b["id"] for b in v["blocks"] if "id" in b}))
    return (_object(value, {"version": lambda v: type(v) is int and v == 1,
                           "rigs": lambda v: _list(v, rig)}, ("version", "rigs"))
            and len(value["rigs"]) == len({r["id"] for r in value["rigs"]})
            and Draft202012Validator(json.loads(_RIG_SCHEMA_TEXT)).is_valid(value))


def assess_metadata(manifest, documents, boundary):
    """Return None only for complete, typed and closed known metadata shapes."""
    try:
        _need(isinstance(manifest, dict) and isinstance(documents, dict)
              and _finite(boundary) and boundary >= 0, "Metadata inputs or terminal boundary are invalid.")
        rig_ids = set()
        if "rigs" in manifest:
            path = manifest["rigs"]
            _need(_id(path) and path in documents, "Declared rig metadata is missing or malformed.")
            rigs = documents[path]
            _need(_rigs(rigs, boundary), "Rig metadata has unsupported structures, references or automation timing.")
            rig_ids = {rig["id"] for rig in rigs["rigs"]}
        _need(_manifest(manifest, boundary, rig_ids), "Manifest metadata has unsupported fields or value shapes.")
        for declaration in manifest["arrangements"]:
            data = documents.get(declaration["file"])
            _need(isinstance(data, dict), "Declared arrangement metadata is missing or malformed.")
            if "stats" in data:
                _need(_object(data["stats"], {"events": _natural, "notes": _natural}),
                      "Arrangement statistics have unsupported fields or value shapes.")
            if "ext" in data:
                _need(_arrangement_source(data["ext"]), "Arrangement source metadata is not a recognized closed format.")
            if "tones" in data:
                _need(_tone_bindings(data["tones"], boundary, rig_ids),
                      "Tone metadata has unsupported controls, references or timing.")
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return str(exc) if isinstance(exc, ValueError) else "Metadata structures could not be verified safely."
    return None
