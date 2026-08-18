import { finiteNumber } from './player-review-clock.js';
import { formatTimelineTime } from './player-review-transport.js';

const HOLD_DELAY_MS = 360;
const HOLD_REPEAT_MS = 70;
const CLICK_SUPPRESSION_MS = 500;
const NUDGE_PRECISION = 1000;

function deferred() {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function activationKey(event) {
  return event.key === 'Enter' || event.key === ' ';
}

function rangeAdjustmentKey(event) {
  return [
    'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowUp',
    'End', 'Home', 'PageDown', 'PageUp',
  ].includes(event.key);
}

export function createPlayerReviewTimeline({
  cancelPreview,
  document,
  getCurrentCandidate,
  getReviewSession,
  layout,
  make,
  pausePlayer,
  playbackClock,
  renderReview,
  resumePlayer,
  seekPlayer,
  setStatus,
  transport,
}) {
  const view = document.defaultView || globalThis;
  let overlay = null;
  let body = null;
  let time = null;
  let refs = null;
  let adjustment = null;
  let adjustmentSequence = 0;
  let candidateId = null;
  let destroyed = false;
  const suppressedClicks = new WeakMap();

  function session() {
    return getReviewSession();
  }

  function ensureOverlay() {
    if (overlay?.isConnected) return overlay;
    overlay = make('aside', 'lh-player-review-timeline-overlay');
    overlay.id = 'lh-player-review-timeline-overlay';
    overlay.hidden = true;
    overlay.setAttribute('role', 'region');
    overlay.setAttribute('aria-label', 'Library Doctor song timeline');
    const header = make('header', 'lh-player-review-timeline-overlay-header');
    const title = make('div');
    title.appendChild(make('span', 'lh-player-review-kicker', 'Library Doctor'));
    title.appendChild(make('strong', '', 'Song timeline'));
    layout.installHandle(title, 'timeline', overlay);
    time = make('output', 'lh-player-review-timeline-time');
    header.appendChild(title);
    header.appendChild(time);
    body = make('div', 'lh-player-review-timeline-body');
    overlay.appendChild(header);
    overlay.appendChild(body);
    document.body.appendChild(overlay);
    layout.position('timeline', overlay);
    return overlay;
  }

  function clearInteractionResources(active) {
    if (!active || active.resourcesCleared) return;
    active.resourcesCleared = true;
    active.cleanup.splice(0).forEach((cleanup) => {
      try { cleanup(); } catch (_error) { /* The interaction is already ending. */ }
    });
    if (active.holdDelay !== null) view.clearTimeout(active.holdDelay);
    if (active.holdRepeat !== null) view.clearInterval(active.holdRepeat);
    active.holdDelay = null;
    active.holdRepeat = null;
    if (
      active.pointerId !== null
      && typeof active.source?.releasePointerCapture === 'function'
    ) {
      try { active.source.releasePointerCapture(active.pointerId); } catch (_error) {
        // Losing capture is normal when a pointer is cancelled outside the window.
      }
    }
  }

  function finishInteraction(active) {
    clearInteractionResources(active);
    if (adjustment !== active) return;
    adjustment = null;
    update();
    renderReview();
  }

  function resolveInteraction(active, kind, reason) {
    if (!active || active.resolution) return false;
    active.resolution = { kind, reason };
    active.showDraft = kind === 'commit';
    clearInteractionResources(active);
    active.gate.resolve(active.resolution);
    update();
    return true;
  }

  function beginAdjustment(source, kind) {
    const reviewSession = session();
    if (
      destroyed
      || adjustment
      || reviewSession?.busy
      || !Number.isFinite(playbackClock.duration)
      || playbackClock.duration <= 0
    ) return null;
    cancelPreview();
    const active = {
      id: ++adjustmentSequence,
      kind,
      source,
      original: playbackClock.clamp(playbackClock.mediaTime),
      draft: playbackClock.clamp(playbackClock.mediaTime),
      changed: false,
      localStepCount: 0,
      wasPlaying: playbackClock.isPlaying === true,
      ownsPause: false,
      resumeEligible: playbackClock.isPlaying === true,
      showDraft: true,
      resolution: null,
      gate: deferred(),
      cleanup: [],
      resourcesCleared: false,
      holdDelay: null,
      holdRepeat: null,
      pointerId: null,
      operation: null,
      operationPromise: null,
    };
    adjustment = active;
    transport.trace?.(null, 'timeline-adjustment-began', {
      adjustment: active.id,
      inputKind: kind,
      original: active.original,
      wasPlaying: active.wasPlaying,
    });
    update();
    renderReview();

    active.operationPromise = transport.run('timeline-adjustment', async (operation) => {
      active.operation = operation;
      const earlyResolution = active.resolution;
      if (earlyResolution?.kind === 'cancel') {
        return { kind: 'cancel', moved: false, restored: false };
      }
      if (active.wasPlaying) {
        await pausePlayer(operation);
        transport.assertCurrent(operation);
        active.ownsPause = true;
      }
      const resolution = active.resolution || await active.gate.promise;
      transport.assertCurrent(operation);
      let moved = false;
      let restored = false;
      try {
        if (resolution.kind === 'commit' && active.changed) {
          await seekPlayer(operation, active.draft, 'timeline');
          moved = true;
        }
      } finally {
        if (active.ownsPause && active.resumeEligible) {
          await resumePlayer(operation, 'Resume after Library Doctor timeline adjustment');
          restored = true;
        }
      }
      return { kind: resolution.kind, moved, restored };
    }).then((result) => {
      if (!result || adjustment !== active) return result;
      transport.trace?.(active.operation, 'timeline-adjustment-settled', {
        adjustment: active.id,
        inputKind: active.kind,
        localStepCount: active.localStepCount,
        draft: active.draft,
        moved: result.moved,
        restored: result.restored,
      });
      if (result.moved) {
        setStatus(`Moved to ${formatTimelineTime(active.draft, true)}.`, 'good');
      }
      return result;
    }).catch((error) => {
      if (adjustment === active && !destroyed) setStatus(error.message, 'error');
      return null;
    }).finally(() => {
      if (!destroyed) finishInteraction(active);
    });
    return active;
  }

  function setDraft(active, value, { countStep = false } = {}) {
    if (!active || adjustment !== active || active.resolution) return;
    const draft = playbackClock.clamp(value);
    active.draft = draft;
    active.changed = active.changed || Math.abs(draft - active.original) > 0.000001;
    if (countStep) active.localStepCount += 1;
    update();
  }

  function nudgeDraft(active, delta) {
    const target = Math.round((active.draft + delta) * NUDGE_PRECISION) / NUDGE_PRECISION;
    setDraft(active, target, { countStep: true });
  }

  function commitAdjustment(active = adjustment, reason = 'committed') {
    if (!resolveInteraction(active, 'commit', reason)) return active?.operationPromise || null;
    return active.operationPromise;
  }

  function cancelAdjustment(reason = 'cancelled') {
    const active = adjustment;
    if (!active) return null;
    active.showDraft = false;
    if (!resolveInteraction(active, 'cancel', reason)) return active.operationPromise;
    transport.trace?.(active.operation, 'timeline-adjustment-cancelled', {
      adjustment: active.id,
      reason,
      restoreEligible: active.ownsPause && active.resumeEligible,
    });
    return active.operationPromise;
  }

  function supersedeAdjustment(reason = 'external-player-action', { notify = true } = {}) {
    const active = adjustment;
    if (!active) return false;
    active.resumeEligible = false;
    active.showDraft = false;
    resolveInteraction(active, 'external', reason);
    transport.trace?.(active.operation, 'timeline-adjustment-ownership-lost', {
      adjustment: active.id,
      reason,
    });
    transport.supersede(`timeline-${reason}`);
    if (adjustment === active) adjustment = null;
    update();
    if (notify) renderReview();
    return true;
  }

  function handlePlaybackEvent(type, _detail = {}, { internal = false } = {}) {
    if (!adjustment || internal || type === 'position') return false;
    return supersedeAdjustment(`external-${type}`);
  }

  function installPointerCompletion(active, event) {
    const pointerId = finiteNumber(event.pointerId);
    active.pointerId = pointerId;
    if (pointerId !== null && typeof active.source?.setPointerCapture === 'function') {
      try { active.source.setPointerCapture(pointerId); } catch (_error) {
        // Window-level completion listeners still safely finish the interaction.
      }
    }
    const matchesPointer = (nextEvent) => {
      const nextPointerId = finiteNumber(nextEvent.pointerId);
      return pointerId === null || nextPointerId === null || nextPointerId === pointerId;
    };
    const pointerUp = (nextEvent) => {
      if (!matchesPointer(nextEvent) || adjustment !== active) return;
      suppressedClicks.set(active.source, Date.now() + CLICK_SUPPRESSION_MS);
      commitAdjustment(active, 'pointer-release');
    };
    const pointerCancel = (nextEvent) => {
      if (!matchesPointer(nextEvent) || adjustment !== active) return;
      cancelAdjustment('pointer-cancelled');
    };
    view.addEventListener('pointerup', pointerUp);
    view.addEventListener('pointercancel', pointerCancel);
    active.cleanup.push(() => view.removeEventListener('pointerup', pointerUp));
    active.cleanup.push(() => view.removeEventListener('pointercancel', pointerCancel));
  }

  function installBlurCancellation(active) {
    const onBlur = () => {
      if (adjustment === active && !active.resolution) cancelAdjustment('window-blurred');
    };
    view.addEventListener('blur', onBlur);
    active.cleanup.push(() => view.removeEventListener('blur', onBlur));
  }

  function startHeldNudge(active, delta) {
    active.holdDelay = view.setTimeout(() => {
      if (adjustment !== active || active.resolution) return;
      nudgeDraft(active, delta);
      active.holdRepeat = view.setInterval(() => {
        if (adjustment === active && !active.resolution) nudgeDraft(active, delta);
      }, HOLD_REPEAT_MS);
    }, HOLD_DELAY_MS);
  }

  function update() {
    const reviewSession = session();
    if (destroyed || !refs || !reviewSession) return;
    const candidate = getCurrentCandidate();
    if (!candidate) return;
    const issueMediaTime = playbackClock.clamp(playbackClock.chartToMedia(candidate.time));
    const duration = finiteNumber(playbackClock.duration);
    const ready = duration !== null && duration > 0;
    const disabled = reviewSession.busy || !ready;
    const active = adjustment;
    refs.range.disabled = disabled || Boolean(active && active.source !== refs.range);
    refs.nudges.forEach((button) => {
      button.disabled = disabled || Boolean(active && active.source !== button);
    });
    if (!ready) {
      refs.range.min = '0';
      refs.range.max = '1';
      refs.range.value = '0';
      refs.range.removeAttribute('aria-valuetext');
      refs.current.textContent = 'Loading song duration…';
      refs.issue.textContent = `Issue ${formatTimelineTime(issueMediaTime, true)}`;
      refs.marker.hidden = true;
      return;
    }
    const displayTime = active?.showDraft
      ? active.draft
      : playbackClock.clamp(playbackClock.mediaTime);
    refs.range.max = String(duration);
    refs.range.value = String(displayTime);
    refs.range.setAttribute(
      'aria-valuetext',
      `${formatTimelineTime(displayTime, true)} of ${formatTimelineTime(duration)}`,
    );
    refs.current.textContent = `${formatTimelineTime(displayTime, true)} / ${formatTimelineTime(duration)}`;
    refs.issue.textContent = `Issue ${formatTimelineTime(issueMediaTime, true)}`;
    refs.marker.hidden = false;
    refs.marker.style.left = `${Math.max(0, Math.min(100, (issueMediaTime / duration) * 100))}%`;
  }

  function createRange(label, describedBy) {
    const track = make('div', 'lh-player-review-timeline-track');
    const markerRail = make('div', 'lh-player-review-timeline-marker-rail');
    markerRail.setAttribute('aria-hidden', 'true');
    const marker = make('span', 'lh-player-review-timeline-marker');
    markerRail.appendChild(marker);
    const input = document.createElement('input');
    input.type = 'range';
    input.className = 'lh-player-review-timeline-range';
    input.min = '0';
    input.max = '1';
    input.step = '0.01';
    input.disabled = true;
    input.setAttribute('aria-label', label);
    input.setAttribute('aria-describedby', describedBy);
    input.addEventListener('pointerdown', (event) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      const active = beginAdjustment(input, 'range');
      if (!active) return;
      installPointerCompletion(active, event);
      installBlurCancellation(active);
    });
    input.addEventListener('input', () => {
      const active = adjustment || beginAdjustment(input, 'range');
      if (active?.source === input) setDraft(active, input.value);
    });
    input.addEventListener('change', () => {
      const active = adjustment || beginAdjustment(input, 'range');
      if (!active || active.source !== input) return;
      setDraft(active, input.value);
      if (!active.keyboardKey) commitAdjustment(active, 'range-change');
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && adjustment?.source === input) {
        event.preventDefault();
        cancelAdjustment('keyboard-cancelled');
        return;
      }
      if (!rangeAdjustmentKey(event)) return;
      const active = adjustment || beginAdjustment(input, 'range');
      if (active?.source === input) active.keyboardKey = event.key;
    });
    input.addEventListener('keyup', (event) => {
      const active = adjustment;
      if (!active || active.source !== input || !rangeAdjustmentKey(event)) return;
      active.keyboardKey = null;
      setDraft(active, input.value);
      commitAdjustment(active, 'keyboard-release');
    });
    input.addEventListener('blur', () => {
      const active = adjustment;
      if (!active || active.source !== input || active.resolution) return;
      cancelAdjustment('range-blurred');
    });
    track.appendChild(markerRail);
    track.appendChild(input);
    return { track, input, marker };
  }

  function createNudgeButton(delta, label, ariaLabel) {
    const button = make('button', 'lh-button', label);
    button.type = 'button';
    button.disabled = session().busy;
    button.setAttribute('aria-label', ariaLabel);
    button.addEventListener('pointerdown', (event) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      event.preventDefault();
      button.focus();
      const active = beginAdjustment(button, 'nudge-hold');
      if (!active) return;
      nudgeDraft(active, delta);
      installPointerCompletion(active, event);
      installBlurCancellation(active);
      startHeldNudge(active, delta);
    });
    button.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && adjustment?.source === button) {
        event.preventDefault();
        cancelAdjustment('keyboard-cancelled');
        return;
      }
      if (!activationKey(event)) return;
      event.preventDefault();
      const active = adjustment || beginAdjustment(button, 'nudge-keyboard');
      if (!active || active.source !== button) return;
      active.keyboardKey = event.key;
      nudgeDraft(active, delta);
      if (!event.repeat) installBlurCancellation(active);
    });
    button.addEventListener('keyup', (event) => {
      const active = adjustment;
      if (!active || active.source !== button || !activationKey(event)) return;
      event.preventDefault();
      active.keyboardKey = null;
      suppressedClicks.set(button, Date.now() + CLICK_SUPPRESSION_MS);
      commitAdjustment(active, 'keyboard-release');
    });
    button.addEventListener('click', (event) => {
      if ((suppressedClicks.get(button) || 0) >= Date.now()) {
        event.preventDefault();
        suppressedClicks.delete(button);
        return;
      }
      const active = beginAdjustment(button, 'nudge-click');
      if (!active) return;
      nudgeDraft(active, delta);
      commitAdjustment(active, 'button-click');
    });
    button.addEventListener('blur', () => {
      const active = adjustment;
      if (!active || active.source !== button || active.resolution) return;
      cancelAdjustment('button-blurred');
    });
    return button;
  }

  function renderControls() {
    const panel = make('section', 'lh-player-review-timeline');
    const labels = make('div', 'lh-player-review-timeline-labels');
    labels.appendChild(make('span', '', 'Whole song'));
    const issue = make('span', '', 'Issue');
    issue.id = 'lh-player-review-timeline-issue';
    labels.appendChild(issue);
    panel.appendChild(labels);
    const range = createRange('Move through the whole song', issue.id);
    panel.appendChild(range.track);

    const nudges = make('div', 'lh-player-review-timeline-nudges');
    const nudgeButtons = [
      [-1, '−1s', 'Move backward 1 second'],
      [-0.1, '−0.1s', 'Move backward 0.1 seconds'],
      [0.1, '+0.1s', 'Move forward 0.1 seconds'],
      [1, '+1s', 'Move forward 1 second'],
    ].map(([delta, label, ariaLabel]) => {
      const button = createNudgeButton(delta, label, ariaLabel);
      nudges.appendChild(button);
      return button;
    });
    panel.appendChild(nudges);
    panel.appendChild(make(
      'small',
      '',
      'Drag the song line for a precise move, or press and hold a button for fine control. Library Doctor pauses once, moves once when you release, then resumes only if it paused playback.',
    ));
    refs = {
      current: time,
      issue,
      range: range.input,
      marker: range.marker,
      nudges: nudgeButtons,
    };
    body.appendChild(panel);
    update();
  }

  function render() {
    if (destroyed) return;
    ensureOverlay();
    const reviewSession = session();
    const candidate = getCurrentCandidate();
    const nextCandidateId = candidate?.review_item_id || null;
    if (adjustment && (!reviewSession || nextCandidateId !== candidateId)) {
      supersedeAdjustment('candidate-changed', { notify: false });
    }
    if (!reviewSession || !candidate) {
      body.replaceChildren();
      refs = null;
      candidateId = null;
      overlay.hidden = true;
      return;
    }
    overlay.hidden = !reviewSession.active;
    if (!refs || candidateId !== nextCandidateId) {
      body.replaceChildren();
      refs = null;
      candidateId = nextCandidateId;
      renderControls();
    } else {
      update();
    }
  }

  return {
    cancelAdjustment,
    destroy() {
      destroyed = true;
      supersedeAdjustment('destroyed', { notify: false });
      refs = null;
      candidateId = null;
      overlay?.remove();
      overlay = null;
      body = null;
      time = null;
    },
    getOverlay: () => overlay,
    handlePlaybackEvent,
    hide() {
      supersedeAdjustment('hidden', { notify: false });
      if (overlay) overlay.hidden = true;
    },
    isScrubbing: () => Boolean(adjustment),
    render,
    settle: () => adjustment?.operationPromise || transport.settle?.() || Promise.resolve(),
    show() {
      if (destroyed) return;
      ensureOverlay().hidden = !getCurrentCandidate();
    },
    supersede: (reason) => supersedeAdjustment(reason),
    update,
  };
}
