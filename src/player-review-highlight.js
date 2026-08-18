const TARGET_TICKS_PER_SECOND = 10_000;
const HIGHLIGHT_COLOR = '#ffd166';
const PULSE_ALPHA_MIN = 0.62;
const PULSE_PERIOD_MS = 1_100;

function finiteNumber(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  if (typeof value === 'string' && value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function candidateTarget(candidate) {
  if (candidate?.visual_target_ambiguous === true) return null;
  const time = finiteNumber(candidate?.time);
  const string = finiteNumber(candidate?.string);
  const fret = finiteNumber(candidate?.fret);
  if (
    time === null
    || string === null
    || fret === null
    || !Number.isInteger(string)
    || !Number.isInteger(fret)
    || string < 0
    || fret < 0
  ) return null;
  return {
    reviewItemId: String(candidate?.review_item_id || ''),
    time,
    timeTick: Math.round(time * TARGET_TICKS_PER_SECOND),
    string,
    fret,
    contextKind: candidate?.context_kind === 'chord_member'
      ? 'chord_member'
      : 'standalone_note',
  };
}

function supportsNoteStateProvider(highway) {
  return Boolean(
    highway
    && typeof highway.setNoteStateProvider === 'function'
    && typeof highway.getNoteStateProvider === 'function',
  );
}

export function createPlayerReviewHighlight({ window }) {
  let target = null;
  let ownerHighway = null;
  let incumbentProvider = null;
  let state = 'idle';
  let reason = '';
  let destroyed = false;
  let reducedMotion = null;
  const highlightState = {
    state: 'active',
    alpha: 1,
    color: HIGHLIGHT_COLOR,
    live: true,
  };

  function prefersReducedMotion() {
    if (reducedMotion === null) {
      try {
        reducedMotion = typeof window?.matchMedia === 'function'
          ? window.matchMedia('(prefers-reduced-motion: reduce)')
          : false;
      } catch (_error) {
        reducedMotion = false;
      }
    }
    return reducedMotion?.matches === true;
  }

  function pulseAlpha() {
    if (prefersReducedMotion()) return 1;
    let now = 0;
    try {
      now = typeof window?.performance?.now === 'function'
        ? window.performance.now()
        : Date.now();
    } catch (_error) {
      now = Date.now();
    }
    const phase = (now % PULSE_PERIOD_MS) / PULSE_PERIOD_MS;
    const wave = (Math.sin(phase * Math.PI * 2) + 1) / 2;
    return PULSE_ALPHA_MIN + ((1 - PULSE_ALPHA_MIN) * wave);
  }

  function matchesTarget(note, chartTime) {
    if (!target || !note || typeof note !== 'object') return false;
    const time = finiteNumber(chartTime);
    if (time === null) return false;
    return Math.round(time * TARGET_TICKS_PER_SECOND) === target.timeTick
      && Number(note.s) === target.string
      && Number(note.f) === target.fret;
  }

  function compositeProvider(note, chartTime) {
    if (matchesTarget(note, chartTime)) {
      highlightState.alpha = pulseAlpha();
      return highlightState;
    }
    if (typeof incumbentProvider !== 'function') return null;
    try {
      return incumbentProvider(note, chartTime);
    } catch (_error) {
      return null;
    }
  }

  function describe() {
    if (!target) return null;
    return {
      reviewItemId: target.reviewItemId,
      time: target.time,
      string: target.string,
      fret: target.fret,
      contextKind: target.contextKind,
    };
  }

  function refreshOwnershipState() {
    if (state !== 'installed' || !ownerHighway) return;
    if (window?.highway !== ownerHighway) {
      state = 'displaced';
      reason = 'highway-replaced';
      return;
    }
    try {
      if (ownerHighway.getNoteStateProvider() !== compositeProvider) {
        state = 'displaced';
        reason = 'note-state-provider-replaced';
      }
    } catch (_error) {
      state = 'unsupported';
      reason = 'note-state-provider-read-failed';
    }
  }

  function status() {
    refreshOwnershipState();
    const highway = window?.highway;
    return {
      state,
      reason,
      supported: supportsNoteStateProvider(highway),
      installed: state === 'installed',
      displaced: state === 'displaced',
      target: describe(),
    };
  }

  function update(candidate) {
    if (destroyed) return status();
    target = candidateTarget(candidate);
    return status();
  }

  function install() {
    if (destroyed) return status();
    refreshOwnershipState();
    if (state === 'installed') return status();
    const highway = window?.highway;
    if (!supportsNoteStateProvider(highway)) {
      state = 'unsupported';
      reason = 'note-state-provider-unavailable';
      return status();
    }
    let incumbent = null;
    try {
      incumbent = highway.getNoteStateProvider();
    } catch (_error) {
      state = 'unsupported';
      reason = 'note-state-provider-read-failed';
      return status();
    }
    if (incumbent === compositeProvider) {
      ownerHighway = highway;
      state = 'installed';
      reason = '';
      return status();
    }
    incumbentProvider = typeof incumbent === 'function' ? incumbent : null;
    ownerHighway = highway;
    try {
      highway.setNoteStateProvider(compositeProvider);
      if (highway.getNoteStateProvider() !== compositeProvider) {
        state = 'displaced';
        reason = 'note-state-provider-install-replaced';
        return status();
      }
    } catch (_error) {
      ownerHighway = null;
      incumbentProvider = null;
      state = 'unsupported';
      reason = 'note-state-provider-install-failed';
      return status();
    }
    state = 'installed';
    reason = '';
    return status();
  }

  function release() {
    const highway = ownerHighway;
    const incumbent = incumbentProvider;
    ownerHighway = null;
    incumbentProvider = null;
    if (highway && supportsNoteStateProvider(highway)) {
      try {
        if (highway.getNoteStateProvider() === compositeProvider) {
          highway.setNoteStateProvider(incumbent);
        }
      } catch (_error) {
        state = 'unsupported';
        reason = 'note-state-provider-restore-failed';
        return status();
      }
    }
    state = destroyed ? 'destroyed' : 'idle';
    reason = '';
    return status();
  }

  function destroy() {
    if (destroyed) return status();
    release();
    destroyed = true;
    target = null;
    state = 'destroyed';
    reason = '';
    return status();
  }

  return {
    describe,
    destroy,
    install,
    release,
    status,
    update,
  };
}
