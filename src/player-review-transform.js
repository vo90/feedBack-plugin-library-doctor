export function decisionChangesSource(decision) {
  return Boolean(decision);
}

function techniqueState(value) {
  return {
    hammer_on: value?.ho === true,
    pull_off: value?.po === true,
    tap: value?.tp === true,
  };
}

function sameTechniqueState(value, expected) {
  const actual = techniqueState(value);
  return actual.hammer_on === Boolean(expected?.hammer_on)
    && actual.pull_off === Boolean(expected?.pull_off)
    && actual.tap === Boolean(expected?.tap);
}

function sameNumber(left, right) {
  return Number.isFinite(Number(left))
    && Number.isFinite(Number(right))
    && Math.abs(Number(left) - Number(right)) < 0.00005;
}

function applyTechniqueDecision(target, decision, candidate, role = 'current') {
  if (!target || typeof target !== 'object') return;
  if (decision === 'set_hammer_on') {
    target.ho = true;
    delete target.po;
  } else if (decision === 'set_pull_off') {
    target.po = true;
    delete target.ho;
  } else if (decision === 'convert_to_tap') {
    target.tp = true;
    delete target.ho;
    delete target.po;
  } else if (decision === 'remove_hopo' || (decision === 'move_to_next' && role === 'current')) {
    delete target.ho;
    delete target.po;
  } else if (decision === 'move_to_next' && role === 'next') {
    if (Number(candidate.fret) < Number(candidate.next?.fret)) {
      target.ho = true;
      delete target.po;
    } else {
      target.po = true;
      delete target.ho;
    }
  }
}

function matchingObjects(items, evidence, contextKind, chordMode = false) {
  if (!Array.isArray(items)) return [];
  const matches = [];
  if (!chordMode) {
    items.forEach((item) => {
      if (
        item && typeof item === 'object'
        && sameNumber(item.t, evidence.time)
        && Number(item.s) === Number(evidence.string)
        && Number(item.f) === Number(evidence.fret)
        && sameTechniqueState(item, evidence.techniques)
      ) matches.push(item);
    });
    return matches;
  }
  items.forEach((chord) => {
    if (!chord || typeof chord !== 'object' || !sameNumber(chord.t, evidence.time)) return;
    if (!Array.isArray(chord.notes)) return;
    chord.notes.forEach((note) => {
      if (
        note && typeof note === 'object'
        && Number(note.s) === Number(evidence.string)
        && Number(note.f) === Number(evidence.fret)
        && sameTechniqueState(note, evidence.techniques)
      ) matches.push(note);
    });
  });
  return contextKind === 'chord_member' ? matches : [];
}

export function rewriteEvidence(input, candidate, decision) {
  if (!decisionChangesSource(decision)) return { changed: 0, ambiguous: 0 };
  const phraseLevel = candidate.stream_context?.kind === 'phrase_level';
  const noteKeys = phraseLevel ? ['notes'] : ['notes', 'allNotes'];
  const chordKeys = phraseLevel ? ['chords'] : ['chords', 'allChords'];
  let changed = 0;
  let ambiguous = 0;

  function rewriteRole(evidence, contextKind, role) {
    if (!evidence) return;
    const keys = contextKind === 'chord_member' ? chordKeys : noteKeys;
    const seenArrays = new Set();
    keys.forEach((key) => {
      const items = input[key];
      if (!Array.isArray(items) || seenArrays.has(items)) return;
      seenArrays.add(items);
      const matches = matchingObjects(items, evidence, contextKind, contextKind === 'chord_member');
      if (matches.length === 1) {
        applyTechniqueDecision(matches[0], decision, candidate, role);
        changed += 1;
      } else if (matches.length > 1) {
        ambiguous += 1;
      }
    });
  }

  rewriteRole(candidate, candidate.context_kind, 'current');
  if (decision === 'move_to_next') {
    rewriteRole({
      ...candidate.next,
      string: candidate.string,
      techniques: candidate.next?.techniques,
    }, candidate.next?.context_kind, 'next');
  }
  return { changed, ambiguous };
}
