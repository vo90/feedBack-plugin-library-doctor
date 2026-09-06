"""Pure source topology matching and field-limited bend recovery candidates."""
import copy
import importlib.util
import json
import math
import sys
from pathlib import Path


def _normalizer():
    name = "_library_doctor_source_bend_curves"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("source_bend_curves.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].normalize_sng_bend_curve


FLAGS = {"fhm": 0x08, "tr": 0x10, "hm": 0x20, "pm": 0x40, "slp": 0x80,
         "plk": 0x100, "ho": 0x200, "po": 0x400, "tp": 0x4000, "hp": 0x8000,
         "vb": 0x10000, "mt": 0x20000, "ig": 0x40000, "ac": 0x4000000, "ln": 0x8000000}


def _note(source, string, fret, sustain, mask, points, slide=-1, unpitched=-1, peak=0):
    n = {"t": round(float(source.time), 6), "s": int(string), "f": int(fret),
         "sus": round(float(sustain), 3)}
    n.update({flag: True for flag, bit in FLAGS.items() if mask & bit})
    if n["f"] == 127 and n.get("mt"):
        n["f"] = 0
    if slide >= 0:
        n["sl"] = int(slide)
    if unpitched >= 0:
        n["slu"] = int(unpitched)
    absolute = [(float(p.time), float(p.step)) for p in points]
    n["_absolute"] = [{"t": round(t, 6), "v": round(v, 6)} for t, v in absolute]
    nonzero = [v for _, v in absolute if v]
    n["_declared_peak"] = round(max(nonzero, key=abs) if nonzero else float(peak), 6)
    n["_curve"], n["_adjustments"] = _normalizer()(float(source.time), float(sustain), absolute)
    return n


def source_document(song):
    levels = {}
    for level in sorted(song.levels, key=lambda x: x.difficulty):
        payload = {"notes": [], "chords": []}
        seen = set()
        for note in sorted(level.notes, key=lambda x: x.time):
            cid = int(note.chordId)
            if 0 <= cid < len(song.chordTemplates):
                key = (round(float(note.time), 5), cid)
                if key in seen:
                    continue
                seen.add(key)
                members = []
                cn = song.chordNotes[note.chordNoteId] if 0 <= note.chordNoteId < len(song.chordNotes) else None
                for string, fret in enumerate(song.chordTemplates[cid].frets):
                    if fret < 0:
                        continue
                    mask = int(note.mask) | (int(cn.mask[string]) if cn else 0)
                    sustain = float(note.sustain) if cn is None or int(cn.mask[string]) & 0x2000 else 0
                    points = cn.bends[string].bendValues[:cn.bends[string].count] if cn else []
                    member = _note(note, string, fret, sustain, mask, points,
                                   cn.slideTo[string] if cn else -1, cn.slideUnpitchTo[string] if cn else -1)
                    members.append(member)
                payload["chords"].append({"t": round(float(note.time), 6), "id": cid, "notes": members})
            else:
                payload["notes"].append(_note(note, note.string, note.fret, note.sustain, int(note.mask),
                    note.bends, note.slideTo, note.slideUnpitchTo, note.bend_time))
        levels[int(level.difficulty)] = payload
    phrases = []
    for iteration in song.phraseIterations:
        if not 0 <= iteration.phraseId < len(song.phrases):
            raise ValueError("The source has an invalid phrase reference.")
        start, end = round(float(iteration.time), 6), round(float(iteration.endTime), 6)
        if end <= start:
            end = start + .001
        maximum = int(song.phrases[iteration.phraseId].maxDifficulty)
        diffs = [d for d in levels if d <= maximum] or list(levels)
        phrases.append({"start_time": start, "end_time": end, "max_difficulty": maximum,
            "levels": [{"difficulty": diff, **{k: [n for n in levels[diff][k] if start <= n["t"] < end]
                        for k in ("notes", "chords")}} for diff in diffs]})
    fullest = max(levels.values(), key=lambda p: len(p["notes"]) + len(p["chords"]))
    flat = {"notes": [], "chords": []}
    if len(levels) == 1 or not phrases:
        flat = fullest
    else:
        for phrase in phrases:
            level = phrase["levels"][-1]
            for field in flat:
                flat[field].extend(level[field])
        if not flat["notes"] and not flat["chords"]:
            flat = fullest
    return {**flat, "phrases": phrases, "capo": max(0, int(song.metadata.capo)),
            "tuning": list(song.metadata.tuning)}


def _identity(note, onset):
    if not isinstance(note, dict) or type(note.get("s")) is not int or type(note.get("f")) is not int:
        raise ValueError("An event has an ambiguous string or fret.")
    fret = 0 if note["f"] == 127 and note.get("mt") is True else note["f"]
    values = (onset, note.get("sus", 0))
    if not all(type(v) in (int, float) and math.isfinite(v) for v in values):
        raise ValueError("An event has invalid timing.")
    return (round(onset, 6), note["s"], fret, round(note.get("sus", 0), 3),
            tuple(flag for flag in FLAGS if note.get(flag) is True), note.get("sl", -1), note.get("slu", -1))


def _pairs(target, source, prefix=()):
    result = []
    for field in ("notes", "chords"):
        left, right = target.get(field, []), source.get(field, [])
        if not isinstance(left, list) or len(left) != len(right):
            raise ValueError("Source event counts do not match this arrangement/version.")
        for i, (a, b) in enumerate(zip(left, right)):
            path = prefix + (field, i)
            if field == "notes":
                if _identity(a, a.get("t")) != _identity(b, b["t"]):
                    raise ValueError("Source event topology or techniques do not match this arrangement/version.")
                result.append((path, a, b))
            else:
                if a.get("t") != b["t"] or a.get("id") != b["id"] or len(a.get("notes", [])) != len(b["notes"]):
                    raise ValueError("Source chord topology does not match this arrangement/version.")
                for j, (an, bn) in enumerate(zip(a["notes"], b["notes"])):
                    if _identity(an, a["t"]) != _identity(bn, b["t"]):
                        raise ValueError("Source chord members do not match this arrangement/version.")
                    result.append((path + ("notes", j), an, bn))
    return result


def recovery_patch(document, song):
    source = source_document(song)
    if document.get("capo", 0) != source["capo"] or document.get("tuning", [0] * 6) != source["tuning"]:
        raise ValueError("Source tuning or capo does not match this arrangement/version.")
    pairs = _pairs(document, source)
    # A flattened-only document can match the complete source. If a ladder is
    # present, every authored copy must match; no selected copy is skipped.
    if document.get("phrases"):
        if len(document["phrases"]) != len(source["phrases"]):
            raise ValueError("Source phrase topology does not match this arrangement/version.")
        for pi, (target_phrase, source_phrase) in enumerate(zip(document["phrases"], source["phrases"])):
            for key in ("start_time", "end_time", "max_difficulty"):
                if target_phrase.get(key) != source_phrase[key]:
                    raise ValueError("Source phrase boundaries do not match this arrangement/version.")
            if len(target_phrase.get("levels", [])) != len(source_phrase["levels"]):
                raise ValueError("Source difficulty copies do not match this arrangement/version.")
            for li, (target_level, source_level) in enumerate(zip(target_phrase["levels"], source_phrase["levels"])):
                if target_level.get("difficulty") != source_level["difficulty"]:
                    raise ValueError("Source difficulty identity does not match this arrangement/version.")
                pairs.extend(_pairs(target_level, source_level, ("phrases", pi, "levels", li)))
    changes, blocked, excluded_identities = [], [], set()
    for path, target, original in pairs:
        identity = (original["t"], target["s"], target["f"])
        curve = original["_curve"]
        if not original["_absolute"]:
            continue  # A scalar alone never establishes a trajectory.
        if len(original["_absolute"]) == 1 and original["_absolute"][0]["v"] == 0 and original["_declared_peak"]:
            excluded_identities.add(identity)
            blocked.append({"path": list(path), "reason": "A lone zero target does not establish the initial bend trajectory."})
            continue
        peak = max((p["v"] for p in curve), default=0, key=abs)
        after = {"bnv": curve, "bn": peak} if peak else {}
        current = {k: target[k] for k in ("bnv", "bn") if k in target}
        if current == after:
            continue
        retained = target.get("bnv")
        if retained and retained != original["_absolute"]:
            excluded_identities.add(identity)
            blocked.append({"path": list(path), "reason": "An existing curve differs from the original; preserve authored edits."})
            continue
        if not retained and target.get("bn", 0) != original["_declared_peak"]:
            excluded_identities.add(identity)
            blocked.append({"path": list(path), "reason": "The stored bend peak does not match the selected source."})
            continue
        changes.append({"path": list(path), "before": current, "after": after,
                        "time": original["t"], "string": target["s"], "fret": target["f"],
                        "adjustments": sorted(original["_adjustments"])})
    eligible = []
    for change in changes:
        if (change["time"], change["string"], change["fret"]) in excluded_identities:
            blocked.append({"path": change["path"], "reason": "Another copy of this note has ambiguous or edited bend data; all copies remain unchanged."})
        else:
            eligible.append(change)
    changes = eligible
    repaired = copy.deepcopy(document)
    for change in changes:
        note = repaired
        for part in change["path"]:
            note = note[part]
        for field in ("bn", "bnv"):
            note.pop(field, None)
        note.update(copy.deepcopy(change["after"]))
    # Keep the result serializable before it reaches a transaction candidate.
    json.dumps(repaired, allow_nan=False)
    return repaired, changes, blocked
