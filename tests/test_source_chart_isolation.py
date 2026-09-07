import copy
import sys
from importlib import util
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).parents[1]


def load():
    name = "doctor_source_chart_isolation_tests"
    if name not in sys.modules:
        spec = util.spec_from_file_location(name, ROOT / "source_chart.py")
        module = util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def song(*, unordered=False):
    points = [(10.6, 0), (10.4, 2)] if unordered else [(10.4, 2)]
    note = NS(time=10.0, string=1, fret=7, sustain=1.0, mask=0,
              bends=[NS(time=t, step=v) for t, v in points], slideTo=-1,
              slideUnpitchTo=-1, bend_time=2, chordId=4294967295)
    return NS(levels=[NS(difficulty=0, notes=[note]),
                      NS(difficulty=1, notes=[copy.deepcopy(note)])],
              phraseIterations=[NS(time=0, endTime=20, phraseId=0)],
              phrases=[NS(maxDifficulty=1)], chordTemplates=[], chordNotes=[],
              metadata=NS(capo=-1, tuning=[0] * 6))


def stored(source):
    result = load().source_document(source, include_bends=False)
    for container in [result, *result["phrases"][0]["levels"]]:
        for note in container["notes"]:
            note["bn"] = 2
        for chord in container["chords"]:
            chord["notes"][0]["bn"] = 1
    return result


def test_topology_projection_never_reads_curve_or_scalar_payload(monkeypatch):
    chart, source = load(), song()
    for level in source.levels:
        del level.notes[0].bends
        del level.notes[0].bend_time
    monkeypatch.setattr(chart, "_normalizer", lambda: pytest.fail("Topology must not interpret bends"))
    result = chart.source_document(source, include_bends=False)
    assert result["notes"] == [{"t": 10.0, "s": 1, "f": 7, "sus": 1.0}]
    assert all("_curve" not in level["notes"][0] for level in result["phrases"][0]["levels"])


@pytest.mark.parametrize("field,value", [("time", float("nan")), ("sustain", float("inf")),
                                         ("sustain", -.0001)])
def test_topology_projection_keeps_structural_timing_guards(field, value):
    source = song()
    setattr(source.levels[0].notes[0], field, value)
    with pytest.raises(ValueError, match="Source event onset and sustain"):
        load().source_document(source, include_bends=False)


@pytest.mark.parametrize("mutate", [
    lambda d: d["notes"][0].update(f=8),
    lambda d: d["notes"][0].update(t=10.001),
    lambda d: d["notes"][0].update(hm=True),
    lambda d: d.update(tuning=[-2] * 6),
    lambda d: d.update(capo=2),
    lambda d: d["phrases"][0].update(end_time=19),
    lambda d: d["phrases"][0].update(max_difficulty=2),
    lambda d: d["phrases"][0]["levels"][0].update(difficulty=2),
    lambda d: d["phrases"][0]["levels"][0]["notes"][0].update(sus=.5),
])
def test_every_exact_mismatch_precedes_unsafe_curve_interpretation(mutate, monkeypatch):
    chart, source = load(), song(unordered=True)
    target = stored(source)
    mutate(target)
    monkeypatch.setattr(chart, "_normalizer", lambda: pytest.fail("Unrelated chart must not interpret bends"))
    with pytest.raises(ValueError, match="match") as error:
        chart.recovery_patch(target, source)
    assert not isinstance(error.value, chart.SourceBendError)


def test_matching_unsafe_curve_has_specific_context_and_preserves_source_order():
    chart, source = load(), song(unordered=True)
    target = stored(source)
    before_target, before_source = copy.deepcopy(target), copy.deepcopy(source)
    with pytest.raises(chart.SourceBendError, match=r"10\.000000s, string 2, fret 7.*chronological") as error:
        chart.recovery_patch(target, source)
    assert isinstance(error.value.__cause__, ValueError)
    assert source == before_source and target == before_target
    with pytest.raises(chart.SourceBendError, match="chronological"):
        chart.source_document(source)  # Default remains the full interpreted document.


def mixed_song():
    source = song()
    source.chordTemplates = [NS(frets=[-1, 7, 9, -1, -1, -1])]
    slots = [NS(count=0, bendValues=[]) for _ in range(6)]
    slots[1] = NS(count=1, bendValues=[NS(time=12.5, step=1), NS(time=0, step=99)])
    source.chordNotes = [NS(mask=[0, 0x2000, 0x40, 0, 0, 0], bends=slots,
                           slideTo=[-1] * 6, slideUnpitchTo=[-1] * 6)]
    chord = NS(time=12.0, chordId=0, chordNoteId=0, sustain=1.0, mask=0)
    source.levels[1].notes.extend([chord, copy.deepcopy(chord)])
    return source


def test_valid_chord_and_difficulty_copies_keep_existing_recovery_behavior():
    chart, source = load(), mixed_song()
    target = stored(source)
    assert len(target["chords"]) == 1  # Same deduplication and active-string selection.
    assert target["chords"][0]["notes"][1] == {"t": 12.0, "s": 2, "f": 9, "sus": 0.0, "pm": True}
    fixed, changes, blocked = chart.recovery_patch(target, source)
    assert len(changes) == 5 and not blocked
    assert fixed["notes"][0]["bnv"] == [{"t": 0.0, "v": 0.0}, {"t": .4, "v": 2.0}]
    assert fixed["chords"][0]["notes"][0]["bnv"] == [{"t": 0.0, "v": 0.0}, {"t": .5, "v": 1.0}]
    assert not chart.recovery_patch(fixed, source)[1]


def test_chord_topology_does_not_read_bend_slots_and_member_mismatch_is_ordinary():
    chart, source = load(), mixed_song()
    target = stored(source)
    del source.chordNotes[0].bends
    assert chart.source_document(source, include_bends=False)["chords"][0]["notes"][0]["f"] == 7
    target["phrases"][0]["levels"][1]["chords"][0]["notes"][1]["pm"] = False
    with pytest.raises(ValueError, match="chord members") as error:
        chart.recovery_patch(target, source)
    assert not isinstance(error.value, chart.SourceBendError)
