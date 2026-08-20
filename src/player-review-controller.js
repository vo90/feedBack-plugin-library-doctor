import { createPlayerReviewLayout } from './player-review-layout.js';
import { createPlayerReviewClock, finiteNumber } from './player-review-clock.js';
import { createPlayerReviewChartTransform } from './player-review-chart-transform.js';
import {
  playerReviewChoiceNodes, playerReviewHighlightCopy, playerReviewReasonCopy,
  playerReviewSummaryCopy,
} from './player-review-choice-view.js';
import { createPlayerReviewHighlight } from './player-review-highlight.js';
import { createPlayerReviewNavigation } from './player-review-navigation.js';
import { createPlayerReviewOptions } from './player-review-options.js';
import { createPlayerReviewOverlay } from './player-review-overlay.js';
import { createPlayerReviewPlaybackObserver } from './player-review-playback-events.js';
import { createPlayerReviewTimeline } from './player-review-timeline.js';
import { decisionChangesSource } from './player-review-transform.js';
import {
  createHostSignals, createPlayerReviewTransport, failedCapability,
  handlerTimedOut, nextPaint,
} from './player-review-transport.js';

const PLAYER_REVIEW_SOURCE = 'library_doctor';
const ISSUE_PREVIEW_SECONDS = 2; const HOST_SIGNAL_TIMEOUT_MS = 10000;
const TRANSPORT_POLL_MS = 25; const SEEK_TOLERANCE_SECONDS = 0.075;
const PLAYBACK_TIMEOUTS_MS = Object.freeze({
  inspect: 1000,
  start: 12000,
  pause: 3500,
  resume: 3500,
  seek: 3500,
  stop: 3500,
});
export function createPlayerReviewController({
  actions,
  document,
  layoutStorageKey,
  localStorage,
  make,
  number,
  requestGlobal,
  getReviewDifficultyScope,
  text,
  window,
}) {
  let reviewSession = null;
  let reviewOverlay = null;
  let timeline = null;
  let loadedFilename = '';
  let loadingArrangement = null;
  let playbackBinding = null;
  let autoplayRelease = null;
  let deferredAutoplayCleanup = null;
  let previewRun = null;
  const playbackClock = createPlayerReviewClock(window);
  const hostSignals = createHostSignals(['loaded', 'ready', 'seek', 'pause', 'resume']);
  const highlight = createPlayerReviewHighlight({ window });

  const layout = createPlayerReviewLayout({
    document,
    getOverlay: () => reviewOverlay?.getOverlay() || null,
    getTimelineOverlay: () => timeline?.getOverlay() || null,
    localStorage,
    setStatus: (value, tone) => setStatus(value, tone),
    storageKey: layoutStorageKey,
    window,
  });
  reviewOverlay = createPlayerReviewOverlay({
    document,
    getTimeline: () => timeline,
    layout,
    make,
    onReturn: () => suspendAndReturn(),
    text,
    window,
  });
  const transport = createPlayerReviewTransport({
    isAvailable: () => Boolean(reviewSession?.active),
    window,
  });
  const chartTransform = createPlayerReviewChartTransform({
    dispatch,
    getChoices: () => effectiveChoices(),
    getLoadedFilename: () => loadedFilename,
    getSession: () => reviewSession,
    window,
  });
  timeline = createPlayerReviewTimeline({
    cancelPreview,
    document,
    getCurrentCandidate: () => currentCandidate(),
    getReviewSession: () => reviewSession,
    layout,
    make,
    pausePlayer,
    playbackClock,
    renderReview: () => render(),
    resumePlayer,
    seekPlayer,
    setStatus,
    transport,
  });
  const handlePlaybackCapabilityEvent = createPlayerReviewPlaybackObserver({
    cancelPreview, getBinding: () => playbackBinding, getOperation: transport.current,
    getPreviewRun: () => previewRun, getSession: () => reviewSession, highlight,
    playbackClock, render, requesterId: PLAYER_REVIEW_SOURCE, setStatus, timeline, transport,
  });
  const options = createPlayerReviewOptions({
    getCurrentCandidate: () => currentCandidate(),
    getSession: () => reviewSession,
    getUnresolvedCandidates: () => unresolvedCandidates(),
    render: () => render(),
    request: requestGlobal,
    refreshTransform: () => refreshTransform(),
  });
  const navigation = createPlayerReviewNavigation({
    cancelPreview,
    getCandidates: () => currentCandidates(),
    getCurrentCandidate: () => currentCandidate(),
    getSession: () => reviewSession,
    highlight,
    number,
    openCurrentInPlayer,
    options,
    refreshTransform,
    render,
    setStatus,
    timeline,
  });


  function capabilities() {
    const api = window.feedBack?.capabilities;
    return api?.version === 1 && typeof api.dispatch === 'function' ? api : null;
  }

  async function dispatch(capability, command, args = {}) {
    const api = capabilities();
    if (!api) return null;
    return api.dispatch({
      capability,
      command,
      source: PLAYER_REVIEW_SOURCE,
      args,
      reason: 'Library Doctor Player Review',
      ...(capability === 'playback' && PLAYBACK_TIMEOUTS_MS[command]
        ? { timeoutMs: PLAYBACK_TIMEOUTS_MS[command] }
        : {}),
    });
  }

  async function inspectPlayback() {
    const result = await dispatch('playback', 'inspect');
    if (failedCapability(result)) return null;
    const state = playbackClock.stateFrom(result);
    playbackClock.applyState(state);
    return state;
  }

  function bindingArgs(args = {}) {
    return playbackBinding
      ? {
        ...args,
        sessionId: playbackBinding.sessionId,
        targetId: playbackBinding.targetId,
      }
      : args;
  }

  function stateMatchesBinding(state) {
    return Boolean(
      state
      && playbackBinding
      && state.sessionId === playbackBinding.sessionId
      && state.target?.targetId === playbackBinding.targetId,
    );
  }

  function bindPlaybackState(state) {
    const sessionId = String(state?.sessionId || '');
    const targetId = String(state?.target?.targetId || '');
    if (!sessionId || !targetId) return false;
    playbackBinding = {
      sessionId,
      targetId,
      arrangementRef: String(state?.target?.arrangementRef || ''),
    };
    return true;
  }

  function clearPlaybackBinding() {
    highlight.release();
    playbackBinding = null;
    loadedFilename = '';
    loadingArrangement = null;
    if (reviewSession) reviewSession.currentArrangement = null;
    playbackClock.reset();
  }

  function forgetAutoplayHold() {
    autoplayRelease = null;
  }

  function clearDeferredAutoplayRelease() {
    deferredAutoplayCleanup?.();
    deferredAutoplayCleanup = null;
  }

  function claimAutoplayHold(operation) {
    clearDeferredAutoplayRelease();
    forgetAutoplayHold();
    const release = typeof window.feedBack?.holdAutoplay === 'function'
      ? window.feedBack.holdAutoplay()
      : null;
    if (typeof release !== 'function') return;
    release.settle?.();
    autoplayRelease = release;
    transport.trace(operation, 'autoplay-held');
  }

  function releaseAutoplayHold(operation = null) {
    const release = autoplayRelease;
    if (typeof release !== 'function') return;
    clearDeferredAutoplayRelease();
    autoplayRelease = null;
    try {
      release();
      transport.trace(operation, 'autoplay-released');
    } catch (error) {
      transport.trace(operation, 'autoplay-release-failed', { message: error.message });
    }
  }

  function deferAutoplayReleaseUntilSafe() {
    const release = autoplayRelease;
    if (typeof release !== 'function') return;
    autoplayRelease = null;
    clearDeferredAutoplayRelease();
    if (document.querySelector('.screen.active')?.id !== 'player') {
      try { release(); } catch (_error) { /* A stale hold is already harmless. */ }
      return;
    }
    const unsubscribers = [];
    const cleanup = () => {
      while (unsubscribers.length) {
        try { unsubscribers.pop()(); } catch (_error) { /* Best effort. */ }
      }
      if (deferredAutoplayCleanup === cleanup) deferredAutoplayCleanup = null;
    };
    const releaseSafely = () => {
      cleanup();
      try { release(); } catch (_error) { /* A stale hold is already harmless. */ }
    };
    const invalidate = () => cleanup();
    const onScreenChanged = (event) => {
      const id = event?.detail?.id ?? event?.id;
      if (id !== 'player') releaseSafely();
    };
    [
      ['screen:changed', onScreenChanged],
      ['song:resume', releaseSafely],
      // playSong clears the old hold before emitting loading, so the retained
      // release token is stale and can simply be discarded at that point.
      ['song:loading', invalidate],
    ].forEach(([name, handler]) => {
      const unsubscribe = window.feedBack?.on?.(name, handler);
      unsubscribers.push(typeof unsubscribe === 'function' ? unsubscribe : () => window.feedBack?.off?.(name, handler));
    });
    deferredAutoplayCleanup = cleanup;
  }

  async function waitForTransportConfirmation(operation, {
    signalType,
    after,
    signalPredicate = null,
    statePredicate = null,
    requireSignal = false,
    requireState = false,
    timeoutMessage,
  }) {
    const deadline = Date.now() + HOST_SIGNAL_TIMEOUT_MS;
    while (Date.now() < deadline) {
      transport.assertCurrent(operation);
      const observed = hostSignals.observed(signalType, after, signalPredicate);
      if (observed || !requireSignal) {
        const state = await inspectPlayback();
        transport.assertCurrent(operation);
        const stateMatches = Boolean(statePredicate?.(state));
        if (observed && (!requireState || stateMatches)) return { detail: observed, state };
        if (!requireSignal && stateMatches) return { detail: null, state };
      }
      await transport.delay(operation, observed ? 50 : 100);
    }
    throw new Error(timeoutMessage);
  }

  function setStatus(value, tone = '') {
    reviewOverlay.setStatus(value, tone);
  }

  function currentCandidates() {
    return Array.isArray(reviewSession?.context?.inspection?.candidates)
      ? reviewSession.context.inspection.candidates
      : [];
  }

  function currentCandidate() {
    return currentCandidates()[reviewSession?.index || 0] || null;
  }

  function definitions() {
    return new Map(
      (reviewSession?.context?.inspection?.decision_definitions || [])
        .map((item) => [item.name, item]),
    );
  }

  function unresolvedCandidates() {
    return currentCandidates().filter((candidate) => (
      !reviewSession.accepted.has(candidate.review_item_id)
      && !reviewSession.skipped.has(candidate.review_item_id)
    ));
  }

  function effectiveChoices() {
    const choices = new Map(reviewSession?.accepted || []);
    if (reviewSession?.tentative?.candidate && reviewSession.tentative.decision) {
      choices.set(
        reviewSession.tentative.candidate.review_item_id,
        reviewSession.tentative,
      );
    }
    return [...choices.values()];
  }

  async function activateTransform() {
    if (!capabilities()) return false;
    return chartTransform.activate();
  }

  async function restoreTransform() {
    await chartTransform.restore();
  }

  async function refreshTransform(options = {}) {
    return chartTransform.refresh(options);
  }

  function selectedArrangement(candidate) {
    const options = candidate?.player?.arrangements || [];
    const saved = reviewSession.arrangements.get(candidate.member_path);
    if (options.some((item) => Number(item.index) === Number(saved))) return Number(saved);
    return Number(candidate?.player?.default_arrangement_index ?? options[0]?.index ?? -1);
  }

  function setMastery(candidate) {
    const fraction = candidate?.stream_context?.is_full_difficulty === true
      ? 1
      : Number(candidate?.player?.mastery_fraction ?? 1);
    if (typeof window.highway?.setMastery === 'function' && Number.isFinite(fraction)) {
      window.highway.setMastery(Math.max(0, Math.min(1, fraction)));
    }
  }

  function cancelPreview() {
    if (!previewRun) return;
    previewRun = null;
  }

  async function pausePlayer(operation) {
    transport.assertCurrent(operation);
    const after = hostSignals.sequence('pause');
    operation.expectedSignal = 'pause';
    transport.trace(operation, 'pause-requested', { clock: playbackClock.snapshot() });
    const result = await dispatch('playback', 'pause', bindingArgs({
      priority: 'user',
      reason: 'Pause Library Doctor Player Review',
    }));
    if (
      failedCapability(result)
      && !handlerTimedOut(result)
      && !['paused', 'ready'].includes(result?.status)
    ) {
      throw new Error(result?.reason || 'FeedBack Player could not pause this issue.');
    }
    const directState = playbackClock.stateFrom(result);
    playbackClock.applyState(directState);
    if (result?.status !== 'paused' && directState?.state !== 'paused') {
      await waitForTransportConfirmation(operation, {
        signalType: 'pause',
        after,
        requireState: handlerTimedOut(result),
        statePredicate: (state) => stateMatchesBinding(state) && state.state === 'paused',
        timeoutMessage: 'FeedBack Player did not finish pausing this issue.',
      });
    }
    transport.assertCurrent(operation);
    operation.expectedSignal = null;
    playbackClock.isPlaying = false;
    transport.trace(operation, 'pause-confirmed', { clock: playbackClock.snapshot() });
  }

  async function resumePlayer(operation, label) {
    transport.assertCurrent(operation);
    const after = hostSignals.sequence('resume');
    operation.expectedSignal = 'resume';
    transport.trace(operation, 'resume-requested', {
      label,
      clock: playbackClock.snapshot(),
    });
    const result = await dispatch('playback', 'resume', bindingArgs({
      priority: 'user',
      reason: label,
    }));
    if (failedCapability(result) && !handlerTimedOut(result)) {
      throw new Error(result?.reason || 'FeedBack Player could not resume playback.');
    }
    const directState = playbackClock.stateFrom(result);
    playbackClock.applyState(directState);
    if (result?.status !== 'playing' && directState?.state !== 'playing') {
      await waitForTransportConfirmation(operation, {
        signalType: 'resume',
        after,
        requireState: handlerTimedOut(result),
        statePredicate: (state) => stateMatchesBinding(state) && (
          state.state === 'playing' || state.transport?.isPlaying === true
        ),
        timeoutMessage: 'FeedBack Player did not finish resuming playback.',
      });
    }
    transport.assertCurrent(operation);
    operation.expectedSignal = null;
    playbackClock.isPlaying = true;
    releaseAutoplayHold(operation);
    transport.trace(operation, 'resume-confirmed', { clock: playbackClock.snapshot() });
  }

  async function seekPlayer(operation, time, action) {
    transport.assertCurrent(operation);
    const target = playbackClock.clamp(time);
    const reason = transport.reason(operation, action);
    const after = hostSignals.sequence('seek');
    operation.expectedSignal = 'seek';
    transport.trace(operation, 'seek-requested', {
      action,
      target,
      clock: playbackClock.snapshot(),
    });
    const result = await dispatch('playback', 'seek', bindingArgs({
      time: target,
      priority: 'user',
      reason,
    }));
    if (failedCapability(result) && !handlerTimedOut(result)) {
      throw new Error(result?.reason || 'FeedBack Player could not move to this issue.');
    }
    const landed = finiteNumber(result?.payload?.landedTime);
    const directState = result?.payload?.snapshot?.state || playbackClock.stateFrom(result);
    playbackClock.applyState(directState);
    const confirmation = await waitForTransportConfirmation(operation, {
      signalType: 'seek',
      after,
      requireSignal: true,
      requireState: true,
      signalPredicate: (detail) => transport.isReason(detail?.reason, operation)
        && Math.abs((finiteNumber(detail?.to) ?? Number.POSITIVE_INFINITY) - target)
          <= SEEK_TOLERANCE_SECONDS,
      statePredicate: (state) => stateMatchesBinding(state)
        && state.state !== 'seeking'
        && Math.abs(
          (finiteNumber(state.media?.mediaTime ?? state.media?.currentTime)
            ?? Number.POSITIVE_INFINITY) - target,
        ) <= SEEK_TOLERANCE_SECONDS,
      timeoutMessage: 'FeedBack Player did not finish moving to the requested position.',
    });
    playbackClock.update(confirmation.detail || {});
    playbackClock.applyState(confirmation.state);
    transport.assertCurrent(operation);
    operation.expectedSignal = null;
    timeline.update();
    transport.trace(operation, 'seek-confirmed', {
      action,
      requested: target,
      commandLanded: landed,
      landed: playbackClock.mediaTime,
      reason,
      clock: playbackClock.snapshot(),
    });
  }

  async function pauseAtIssue(operation, candidate, { announce = true } = {}) {
    if (!candidate) return;
    highlight.update(candidate);
    highlight.install();
    setMastery(candidate);
    await refreshTransform();
    transport.assertCurrent(operation);
    await pausePlayer(operation);
    await seekPlayer(
      operation,
      playbackClock.chartToMedia(candidate.time),
      'jump-to-issue',
    );
    await nextPaint(window);
    transport.assertCurrent(operation);
    setMastery(candidate);
    await refreshTransform();
    transport.assertCurrent(operation);
    timeline.update();
    if (announce) {
      setStatus(
        `Paused at the current issue (${Number(candidate.time).toFixed(4)}s). Use Play preview for the four-second context, or use the normal Player controls freely.`,
        'good',
      );
    }
  }

  async function openCurrentInPlayer({ forceStart = false } = {}) {
    const candidate = currentCandidate();
    if (!candidate) return { opened: false, message: 'No current review issue is available.' };
    const arrangement = selectedArrangement(candidate);
    if (!Number.isInteger(arrangement) || arrangement < 0) {
      const message = 'This reviewed chart is not available in FeedBack Player.';
      setStatus(message, 'error');
      return { opened: false, message };
    }
    const issueTime = Math.max(0, Number(candidate.time || 0));
    cancelPreview();
    reviewSession.busy = true;
    render();
    try {
      const opened = await transport.run('open-issue', async (operation) => {
        const alreadyLoaded = !forceStart
          && loadedFilename === reviewSession.context.playback_filename
          && reviewSession.currentArrangement === arrangement
          && document.querySelector('.screen.active')?.id === 'player';
        if (!alreadyLoaded) {
          const priorState = await inspectPlayback();
          transport.assertCurrent(operation);
          const priorSessionId = String(priorState?.sessionId || '');
          clearPlaybackBinding();
          const expectedFilename = reviewSession.context.playback_filename;
          const loadedAfter = hostSignals.sequence('loaded');
          const readyAfter = hostSignals.sequence('ready');
          operation.expectedSignal = 'ready';
          operation.expectedFilename = expectedFilename;
          operation.expectedArrangement = arrangement;
          transport.trace(operation, 'song-start-requested', { arrangement });
          const result = await dispatch('playback', 'start', {
            target: {
              filename: expectedFilename,
              sourceKind: 'local',
            },
            arrangement,
            authorization: 'user-action',
            priority: 'user',
            reason: 'Open a Library Doctor review issue',
          });
          if (failedCapability(result) && !handlerTimedOut(result)) {
            throw new Error(result?.reason || 'FeedBack Player could not open this issue.');
          }
          playbackClock.applyState(playbackClock.stateFrom(result));
          const readyConfirmation = await waitForTransportConfirmation(operation, {
            signalType: 'ready',
            after: readyAfter,
            requireSignal: true,
            requireState: true,
            signalPredicate: (detail) => detail?.filename === expectedFilename
              && Number(detail?.arrangement) === arrangement
              && Number(detail?.loadedSequence) > loadedAfter
              && document.querySelector('.screen.active')?.id === 'player',
            statePredicate: (state) => state
              && ['ready', 'paused', 'playing'].includes(state.state)
              && (!priorSessionId || state.sessionId !== priorSessionId)
              && state.transport?.readiness === 'ready'
              && state.media?.readiness === 'ready'
              && finiteNumber(state.media?.duration) > 0
              && state.target?.arrangementRef === `arrangement-${arrangement}`,
            timeoutMessage: 'FeedBack Player did not finish loading this issue.',
          });
          transport.assertCurrent(operation);
          operation.expectedSignal = null;
          if (!bindPlaybackState(readyConfirmation.state)) {
            throw new Error('FeedBack Player did not provide a stable song-session identity.');
          }
          loadedFilename = expectedFilename;
          reviewSession.currentArrangement = arrangement;
          // Library Doctor Player Review only opens Feedpak/Sloppak sources.
          // The core highway contract fixes their chart/audio offset at zero.
          playbackClock.setSongOffset(0);
          playbackClock.applyState(readyConfirmation.state);
          playbackClock.sync();
          transport.trace(operation, 'song-ready', {
            arrangement,
            candidateTime: issueTime,
            clock: playbackClock.snapshot(),
          });
        }
        reviewSession.active = true;
        reviewSession.suspended = false;
        reviewOverlay.show();
        await pauseAtIssue(operation, candidate, { announce: false });
        setStatus(
          chartTransform.isAvailable()
            ? `Paused at the current issue (${issueTime.toFixed(4)}s). Choose an option to preview it on the Highway; nothing changes on disk until Apply.`
            : 'Player Review is open. Live Highway preview is unavailable in this FeedBack build; decisions still require preview and confirmation before Apply.',
          chartTransform.isAvailable() ? '' : 'review',
        );
        return true;
      });
      if (!opened) return { opened: false, message: 'Player Review opening was interrupted.' };
      return { opened: true };
    } catch (error) {
      const message = error?.message || 'FeedBack Player could not open this review issue.';
      setStatus(message, 'error');
      cancelPreview();
      timeline.supersede('player-review-open-failed');
      highlight.release();
      if (reviewSession) {
        reviewSession.active = false;
        reviewSession.suspended = true;
      }
      reviewOverlay.hide();
      try { await restoreTransform(); } catch (_restoreError) {
        // Opening already failed; keep the original transport error as the
        // actionable message and leave the transform cleanup best-effort.
      }
      window.feedBack?.navigate?.('plugin-library_doctor');
      return { opened: false, message };
    } finally {
      reviewSession.busy = false;
      render();
    }
  }

  async function playIssuePreview() {
    const candidate = currentCandidate();
    if (!candidate || reviewSession.busy) return;
    cancelPreview();
    reviewSession.busy = true;
    render();
    const issueTime = Math.max(0, Number(candidate.time || 0));
    let previewNeedsRecoveryPause = false;
    try {
      await transport.run('issue-preview', async (operation) => {
        await pausePlayer(operation);
        const startChartTime = Math.max(0, issueTime - ISSUE_PREVIEW_SECONDS);
        const endChartTime = issueTime + ISSUE_PREVIEW_SECONDS;
        const startMediaTime = playbackClock.chartToMedia(startChartTime);
        const endMediaTime = playbackClock.chartToMedia(endChartTime);
        transport.trace(operation, 'preview-bounds', {
          candidateId: candidate.review_item_id,
          issueChartTime: issueTime,
          startMediaTime,
          endMediaTime,
          returnMediaTime: playbackClock.chartToMedia(issueTime),
          clock: playbackClock.snapshot(),
        });
        await seekPlayer(operation, startMediaTime, 'preview-start');
        const run = {
          operationId: operation.id,
          candidateId: candidate.review_item_id,
          endMediaTime,
          completing: false,
        };
        previewRun = run;
        render();
        previewNeedsRecoveryPause = true;
        await resumePlayer(operation, 'Play the four-second Library Doctor issue preview');
        let lastProgressAt = Date.now();
        let lastMediaTime = playbackClock.mediaTime;
        setStatus(
          'Playing exactly two chart seconds before through two seconds after this issue. It will return paused to the issue automatically.',
          'good',
        );
        while (playbackClock.mediaTime < endMediaTime - 0.02) {
          transport.assertCurrent(operation);
          if (previewRun !== run) {
            transport.supersede('preview-cancelled');
            transport.assertCurrent(operation);
          }
          playbackClock.sync();
          if (playbackClock.mediaTime > lastMediaTime + 0.002) {
            lastMediaTime = playbackClock.mediaTime;
            lastProgressAt = Date.now();
          } else if (Date.now() - lastProgressAt > 3000) {
            throw new Error('FeedBack Player stopped advancing during the four-second preview.');
          }
          await transport.delay(operation, TRANSPORT_POLL_MS);
        }
        run.completing = true;
        await pausePlayer(operation);
        previewNeedsRecoveryPause = false;
        await seekPlayer(operation, playbackClock.chartToMedia(candidate.time), 'preview-return');
        await nextPaint(window);
        transport.assertCurrent(operation);
        setMastery(candidate);
        await refreshTransform();
        setStatus(
          `Preview finished. Paused again at the current issue (${Number(candidate.time).toFixed(4)}s).`,
          'good',
        );
      });
    } catch (error) {
      if (previewNeedsRecoveryPause && reviewSession?.active && playbackBinding) {
        try {
          await transport.run('preview-recovery-pause', (operation) => pausePlayer(operation));
        } catch (recoveryError) {
          transport.trace(null, 'preview-recovery-pause-failed', {
            message: recoveryError.message,
          });
        }
      }
      setStatus(error.message, 'error');
    } finally {
      cancelPreview();
      reviewSession.busy = false;
      render();
    }
  }

  async function jumpToIssue() {
    const candidate = currentCandidate();
    if (!candidate || reviewSession.busy) return;
    reviewSession.busy = true;
    render();
    try {
      await transport.run('jump-to-issue', (operation) => (
        pauseAtIssue(operation, candidate)
      ));
    } catch (error) {
      setStatus(error.message, 'error');
    } finally {
      reviewSession.busy = false;
      render();
    }
  }

  function changingDecisions() {
    return [...reviewSession.accepted.values()]
      .filter((item) => decisionChangesSource(item.decision))
      .map((item) => ({
        candidate_id: item.candidate.candidate_id,
        decision: item.decision,
      }));
  }

  function allAcceptedDecisions() {
    return [...reviewSession.accepted.values()].map((item) => ({
      candidate_id: item.candidate.candidate_id,
      decision: item.decision,
    }));
  }

  async function stopForMutation() {
    cancelPreview();
    highlight.release();
    timeline.supersede('song-mutation');
    transport.supersede('song-mutation');
    await timeline.settle();
    await transport.settle();
    const stopped = await dispatch('playback', 'stop', bindingArgs({
      priority: 'user',
      reason: 'Temporarily unload the song for a Library Doctor transaction',
    }));
    if (failedCapability(stopped) && stopped?.status !== 'no-target') {
      throw new Error(stopped?.reason || 'The song could not be unloaded safely.');
    }
    await requestGlobal('/playback', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ active: false }),
    });
    loadedFilename = '';
  }

  async function loadFreshContext({ keepAccepted = false } = {}) {
    const oldCandidate = currentCandidate();
    const oldOrder = currentCandidates().map((item) => item.review_item_id);
    const oldSkipped = new Set(reviewSession.skipped || []);
    const context = await requestGlobal('/reviewed-repair/player-context', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package: reviewSession.package,
        adapter_id: reviewSession.adapterId,
        difficulty_scope: reviewSession.difficultyScope,
      }),
    });
    reviewSession.context = context;
    reviewSession.difficultyScope = context.difficulty_scope || reviewSession.difficultyScope;
    reviewSession.pendingRecovery = context.pending_recovery || null;
    if (!keepAccepted) reviewSession.accepted.clear();
    reviewSession.options = new Map();
    reviewSession.tentative = null;
    reviewSession.pendingPlan = null;
    const fresh = currentCandidates();
    const oldPosition = oldOrder.indexOf(oldCandidate?.review_item_id);
    let nextIndex = 0;
    if (oldPosition >= 0) {
      const laterIds = new Set(oldOrder.slice(oldPosition + 1));
      const found = fresh.findIndex((item) => laterIds.has(item.review_item_id));
      if (found >= 0) nextIndex = found;
    }
    reviewSession.index = nextIndex;
    highlight.update(currentCandidate());
    const freshIds = new Set(fresh.map((item) => item.review_item_id));
    reviewSession.skipped = new Set(
      [...oldSkipped].filter((itemId) => freshIds.has(itemId)),
    );
    if (keepAccepted) {
      reviewSession.accepted = new Map(
        [...reviewSession.accepted].filter(([id]) => freshIds.has(id)),
      );
    }
    options.load(currentCandidate());
  }

  async function applyAccepted() {
    if (options.approvalState().blocked || reviewSession.pendingRecovery) return;
    const changing = changingDecisions();
    if (!changing.length) return;
    reviewSession.busy = true;
    render();
    try {
      if (!reviewSession.pendingPlan) {
        const plan = await requestGlobal('/reviewed-repair/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            package: reviewSession.package,
            adapter_id: reviewSession.adapterId,
            difficulty_scope: reviewSession.difficultyScope,
            decisions: allAcceptedDecisions(),
          }),
        });
        reviewSession.pendingPlan = plan;
        setStatus(
          `Review ready: ${number(plan.changing_count)} selected change${Number(plan.changing_count) === 1 ? '' : 's'}. Check the count, then choose Apply to save this group.`,
          'review',
        );
        return;
      }
      const plan = reviewSession.pendingPlan;
      await stopForMutation();
      const requestId = `player-review-${Date.now()}`;
      const result = await requestGlobal('/reviewed-repair/apply', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': requestId,
        },
        body: JSON.stringify({
          package: reviewSession.package,
          adapter_id: reviewSession.adapterId,
          difficulty_scope: reviewSession.difficultyScope,
          decisions: allAcceptedDecisions(),
          plan_id: plan.plan_id,
          request_id: requestId,
        }),
      });
      result.id = `repair-${result.backup_id || Date.now()}`;
      actions.renderRepairResult(result, { reveal: false });
      await loadFreshContext();
      reviewSession.pendingRecovery = {
        backup_id: result.backup_id,
        undo_available: result.undo_available !== false,
        change_count: result.change_count,
      };
      render();
      if (currentCandidate()
        && !(await openCurrentInPlayer({ forceStart: true })).opened) return;
      setStatus(
        'The selected changes were applied safely. You may keep reviewing, but first undo these changes or keep them and remove the Undo copy before applying another group.',
        'good',
      );
    } catch (error) {
      reviewSession.pendingPlan = null;
      setStatus(error.message, 'error');
    } finally {
      reviewSession.busy = false;
      render();
    }
  }

  async function resolveRecovery(action) {
    const recovery = reviewSession.pendingRecovery;
    if (!recovery?.backup_id) return;
    reviewSession.busy = true;
    render();
    try {
      await stopForMutation();
      const requestId = `player-review-${action}-${Date.now()}`;
      const endpoint = action === 'restore'
        ? '/repair/restore'
        : '/repair/recovery/finalize';
      const result = await requestGlobal(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': requestId,
        },
        body: JSON.stringify({
          package: reviewSession.package,
          backup_id: recovery.backup_id,
          request_id: requestId,
        }),
      });
      result.id = `${action}-${recovery.backup_id}-${Date.now()}`;
      actions.renderRepairResult(result, { reveal: false });
      if (action === 'restore') {
        await loadFreshContext();
        reviewSession.skipped.clear();
        reviewSession.index = 0;
        options.load(currentCandidate());
      } else {
        reviewSession.pendingRecovery = null;
      }
      render();
      if (currentCandidate()
        && !(await openCurrentInPlayer({ forceStart: true })).opened) return;
      if (action === 'restore') {
        setStatus('The exact original song data was restored. The review queue has been rebuilt.', 'good');
      } else {
        setStatus('The repaired version was finalized. You can apply another accepted group.', 'good');
      }
    } catch (error) {
      setStatus(error.message, 'error');
    } finally {
      reviewSession.busy = false;
      render();
    }
  }

  function render() {
    const overlayBody = reviewOverlay.getBody();
    timeline.render();
    overlayBody.replaceChildren();
    if (!reviewSession) {
      return;
    }
    reviewOverlay.setFileState(
      reviewSession.pendingRecovery
        ? 'Changes applied — Undo is available'
        : 'Preview only — song files have not changed',
      reviewSession.pendingRecovery ? 'applied' : 'preview',
    );
    const candidates = currentCandidates();
    const candidate = currentCandidate();
    if (!candidate) {
      overlayBody.appendChild(make(
        'p',
        'lh-player-review-complete',
        'No current HO/PO issues remain in this package.',
      ));
      reviewOverlay.appendRecovery(overlayBody, reviewSession, resolveRecovery);
      return;
    }
    const acceptedChanging = changingDecisions().length;
    const skippedTotal = reviewSession.skipped.size;
    const hasTentativeChoice = Boolean(reviewSession.tentative?.decision);
    const currentStep = reviewSession.pendingPlan || (acceptedChanging && !hasTentativeChoice)
      ? 3 : hasTentativeChoice ? 2 : 1;
    const steps = make('ol', 'lh-player-review-steps');
    ['1. Inspect and listen', '2. Choose', '3. Review and apply'].forEach((label, offset) => {
      const step = make('li', '', label);
      step.dataset.current = String(currentStep === offset + 1);
      steps.appendChild(step);
    });
    overlayBody.appendChild(steps);
    const issue = make('section', 'lh-player-review-issue');
    issue.appendChild(make(
      'p',
      'lh-player-review-count',
      `Issue ${number(reviewSession.index + 1)} of ${number(candidates.length)} · ${reviewSession.difficultyScope === 'all_authored' ? 'all authored difficulties' : 'max difficulty only'}`,
    ));
    issue.appendChild(make(
      'h3',
      '',
      `String ${number(Number(candidate.string) + 1)}, fret ${number(candidate.fret)} at ${Number(candidate.time).toFixed(4)}s`,
    ));
    issue.appendChild(make(
      'p',
      'lh-muted',
      `${candidate.context_kind === 'chord_member' ? 'Chord member' : 'Standalone note'} · ${candidate.stream}`,
    ));
    highlight.update(candidate);
    const highlightStatus = highlight.status();
    issue.appendChild(make(
      'p',
      'lh-player-review-note lh-player-review-highlight-note',
      playerReviewHighlightCopy(candidate, highlightStatus, number),
    ));
    (candidate.reasons || []).forEach((reason) => {
      issue.appendChild(make('p', 'lh-player-review-reason', playerReviewReasonCopy(reason)));
    });

    const issueTransport = make('div', 'lh-player-review-issue-transport');
    const jumpButton = make('button', 'lh-button', 'Jump to issue');
    const previewButton = make(
      'button',
      hasTentativeChoice || acceptedChanging ? 'lh-button' : 'lh-button lh-button-primary',
      previewRun?.candidateId === candidate.review_item_id
        ? 'Playing preview…'
        : 'Play preview (2s before + 2s after)',
    );
    jumpButton.type = previewButton.type = 'button';
    jumpButton.disabled = reviewSession.busy || timeline.isScrubbing();
    previewButton.disabled = reviewSession.busy || Boolean(previewRun) || timeline.isScrubbing();
    jumpButton.addEventListener('click', jumpToIssue);
    previewButton.addEventListener('click', playIssuePreview);
    issueTransport.appendChild(jumpButton);
    issueTransport.appendChild(previewButton);
    issueTransport.appendChild(make(
      'small',
      '',
      'The short preview returns paused to this note. Normal Player controls and keybindings remain available.',
    ));
    issue.appendChild(issueTransport);

    const arrangements = candidate.player?.arrangements || [];
    if (arrangements.length > 1) {
      const label = make('label', 'lh-player-review-arrangement');
      label.appendChild(make('span', '', 'Player arrangement'));
      const select = document.createElement('select');
      arrangements.forEach((item) => {
        const option = document.createElement('option');
        option.value = String(item.index);
        option.textContent = item.name || item.id || `Arrangement ${Number(item.index) + 1}`;
        select.appendChild(option);
      });
      select.value = String(selectedArrangement(candidate));
      select.disabled = reviewSession.busy;
      select.addEventListener('change', async () => {
        reviewSession.arrangements.set(candidate.member_path, Number(select.value));
        await openCurrentInPlayer({ forceStart: true });
      });
      label.appendChild(select);
      issue.appendChild(label);
    }

    const currentOptionState = options.state(candidate);
    if (!currentOptionState) options.load(candidate);
    const decisionMap = new Map(definitions());
    (currentOptionState?.response?.decision_definitions || []).forEach((item) => {
      decisionMap.set(item.name, item);
    });
    const selected = reviewSession.tentative?.candidate?.review_item_id === candidate.review_item_id
      ? reviewSession.tentative.decision
      : reviewSession.accepted.get(candidate.review_item_id)?.decision;
    playerReviewChoiceNodes({
      candidate,
      decisionDefinitions: decisionMap,
      document,
      disabled: reviewSession.busy || timeline.isScrubbing(),
      make,
      onRetry: () => {
        options.retry(candidate);
      },
      onSelect: async (name, definition) => {
          reviewSession.skipped.delete(candidate.review_item_id);
          reviewSession.tentative = { candidate, decision: name };
          reviewSession.pendingPlan = null;
          setStatus(`${definition.label} resolves this issue in the safety check. Preparing its Highway preview; the song file is still unchanged.`);
          render();
          const preview = await refreshTransform({
            reason: 'choice-selected',
            requireChange: true,
          });
          setStatus(
            preview?.verified
              ? `${definition.label} resolves this issue and is previewed on the Highway. The song file is still unchanged.`
              : `${definition.label} resolves this issue, but Library Doctor could not confirm the live Highway preview. The song file is still unchanged.`,
            preview?.verified ? '' : 'review',
          );
      },
      optionState: currentOptionState,
      selected,
    }).forEach((node) => issue.appendChild(node));
    if (!chartTransform.isAvailable()) {
      issue.appendChild(make(
        'p',
        'lh-player-review-note',
        'A live Highway preview is not available in this FeedBack build. Your choice is still a preview and will not be saved unless you review and apply it.',
      ));
    }
    if (candidate.blockers?.length) {
      issue.appendChild(make(
        'p',
        'lh-inline-error',
        `This occurrence is display-only: ${candidate.blockers.join(', ')}.`,
      ));
    }
    overlayBody.appendChild(issue);

    const summary = make(
      'p',
      'lh-player-review-summary',
      playerReviewSummaryCopy({
        accepted: acceptedChanging,
        approval: options.approvalState(),
        skipped: skippedTotal,
        total: candidates.length,
        number,
      }),
    );
    overlayBody.appendChild(summary);
    const controls = make('div', 'lh-player-review-buttons');
    const previous = make('button', 'lh-button', 'Previous issue');
    const accept = make(
      'button',
      hasTentativeChoice ? 'lh-button lh-button-primary' : 'lh-button',
      'Keep choice & next',
    );
    const skip = make(
      'button',
      'lh-button',
      reviewSession.skipped.has(candidate.review_item_id)
        ? 'Review this issue again'
        : 'Skip for now',
    );
    const next = make('button', 'lh-button', 'Next issue');
    const apply = make(
      'button',
      currentStep === 3 ? 'lh-button lh-button-primary' : 'lh-button',
      reviewSession.pendingPlan
        ? `Apply ${number(reviewSession.pendingPlan.changing_count)} selected change${Number(reviewSession.pendingPlan.changing_count) === 1 ? '' : 's'}`
        : `Review ${number(acceptedChanging)} selected change${acceptedChanging === 1 ? '' : 's'}`,
    );
    previous.type = accept.type = skip.type = next.type = apply.type = 'button';
    previous.disabled = reviewSession.busy || timeline.isScrubbing() || candidates.length < 2;
    next.disabled = reviewSession.busy || timeline.isScrubbing() || candidates.length < 2;
    accept.disabled = reviewSession.busy || timeline.isScrubbing()
      || !reviewSession.tentative?.decision;
    skip.disabled = reviewSession.busy || timeline.isScrubbing();
    apply.disabled = reviewSession.busy || timeline.isScrubbing()
      || acceptedChanging === 0 || options.approvalState().blocked || Boolean(reviewSession.pendingRecovery);
    previous.addEventListener('click', () => navigation.moveTo(
      navigation.unresolvedIndex(reviewSession.index, -1),
    ));
    next.addEventListener('click', () => navigation.moveTo(
      navigation.unresolvedIndex(reviewSession.index, 1),
    ));
    accept.addEventListener('click', navigation.acceptAndNext);
    skip.addEventListener('click', navigation.skipCurrent);
    apply.addEventListener('click', applyAccepted);
    controls.appendChild(previous);
    controls.appendChild(accept);
    controls.appendChild(skip);
    controls.appendChild(next);
    controls.appendChild(apply);
    overlayBody.appendChild(controls);
    if (skippedTotal) {
      const reviewSkippedButton = make('button', 'lh-button', 'Review skipped issues');
      reviewSkippedButton.type = 'button';
      reviewSkippedButton.disabled = reviewSession.busy || timeline.isScrubbing();
      reviewSkippedButton.addEventListener('click', navigation.reviewSkipped);
      overlayBody.appendChild(reviewSkippedButton);
    }
    reviewOverlay.appendRecovery(overlayBody, reviewSession, resolveRecovery);
  }

  async function open(report, adapterId, difficultyScope = getReviewDifficultyScope()) {
    const requestedScope = difficultyScope === 'all_authored' ? 'all_authored' : 'full_only';
    reviewOverlay.prepare();
    setStatus('Preparing a source-bound Player Review…');
    const matchingSuspendedSession = (
      reviewSession?.suspended
      && reviewSession.package === report.package
      && reviewSession.adapterId === adapterId
    );
    if (
      matchingSuspendedSession
      && reviewSession.difficultyScope !== requestedScope
      && (reviewSession.accepted.size || reviewSession.tentative)
    ) {
      reviewOverlay.hide();
      return {
        opened: false,
        message: 'This song has staged Player Review choices under the other difficulty filter. Switch the Manual Player Review filter back to resume, then apply or leave those choices before changing scope.',
      };
    }
    if (matchingSuspendedSession && reviewSession.difficultyScope === requestedScope) {
      if (!(reviewSession.options instanceof Map)) reviewSession.options = new Map();
      if (!(reviewSession.skipped instanceof Set)) reviewSession.skipped = new Set();
      reviewSession.active = true;
      reviewSession.suspended = false;
      reviewSession.report = report;
      try {
        await loadFreshContext({ keepAccepted: true });
        if (!currentCandidate()) {
          reviewSession.active = false;
          reviewSession.suspended = true;
          reviewOverlay.hide();
          await restoreTransform();
          return {
            opened: false,
            message: 'No manual Player Review issues match the selected difficulty filter. Automatic safe repairs remain available in the song list.',
          };
        }
        await activateTransform();
        render();
        options.load(currentCandidate());
        return await openCurrentInPlayer({ forceStart: true });
      } catch (error) {
        reviewSession.active = false;
        reviewSession.suspended = true;
        reviewOverlay.hide();
        await restoreTransform();
        return { opened: false, message: error.message };
      }
    }
    if (reviewSession) await end({ discard: true, navigate: false });
    try {
      const context = await requestGlobal('/reviewed-repair/player-context', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          package: report.package,
          adapter_id: adapterId,
          difficulty_scope: requestedScope,
        }),
      });
      reviewSession = {
        package: report.package,
        adapterId,
        report,
        context,
        difficultyScope: context.difficulty_scope || requestedScope,
        index: 0,
        accepted: new Map(),
        skipped: new Set(),
        options: new Map(),
        tentative: null,
        pendingPlan: null,
        pendingRecovery: context.pending_recovery || null,
        arrangements: new Map(),
        currentArrangement: null,
        active: true,
        suspended: false,
        busy: false,
        lastTransform: null,
      };
      await activateTransform();
      render();
      if (!currentCandidate()) {
        reviewSession.active = false;
        reviewSession.suspended = true;
        reviewOverlay.hide();
        await restoreTransform();
        return {
          opened: false,
          message: 'No manual Player Review issues match the selected difficulty filter. Automatic safe repairs remain available in the song list.',
        };
      }
      options.load(currentCandidate());
      return await openCurrentInPlayer({ forceStart: true });
    } catch (error) {
      setStatus(error.message, 'error');
      if (!reviewSession) {
        reviewOverlay.getBody().replaceChildren(make('p', 'lh-inline-error', error.message));
      }
      return { opened: false, message: error.message };
    }
  }

  async function suspendAndReturn() {
    if (!reviewSession) return;
    cancelPreview();
    transport.supersede('return-to-library-doctor');
    reviewSession.active = false;
    reviewSession.suspended = true;
    reviewOverlay.hide();
    highlight.release();
    await restoreTransform();
    if (typeof window.feedBack?.navigate === 'function') {
      window.feedBack.navigate('plugin-library_doctor');
    }
    releaseAutoplayHold();
  }

  async function end({ discard = true, navigate = false } = {}) {
    cancelPreview();
    transport.supersede('player-review-ended');
    if (reviewSession) reviewSession.active = false;
    reviewOverlay.hide();
    highlight.release();
    await restoreTransform();
    if (discard) reviewSession = null;
    if (navigate && typeof window.feedBack?.navigate === 'function') {
      window.feedBack.navigate('plugin-library_doctor');
      releaseAutoplayHold();
    } else {
      deferAutoplayReleaseUntilSafe();
    }
  }

  function handleScreenChanged(id) {
    if (!reviewSession) return;
    if (id === 'player' && reviewSession.active) {
      reviewOverlay.show();
      return;
    }
    if (id !== 'player' && reviewSession.active) {
      timeline.supersede('player-screen-left');
      transport.supersede('player-screen-left');
      reviewSession.active = false;
      reviewSession.suspended = true;
      reviewOverlay.hide();
      highlight.release();
      restoreTransform();
      releaseAutoplayHold();
    }
  }

  function handleSongLoading(detail) {
    timeline.supersede('song-loading');
    const nextFilename = String(detail?.filename || '');
    const nextArrangement = finiteNumber(detail?.arrangement);
    loadingArrangement = Number.isInteger(nextArrangement) ? nextArrangement : null;
    if (!reviewSession?.active) {
      playbackClock.reset();
      loadedFilename = nextFilename;
      return;
    }
    const operation = transport.current();
    const expected = operation?.kind === 'open-issue'
      && operation.expectedSignal === 'ready'
      && nextFilename === operation.expectedFilename
      && loadingArrangement === operation.expectedArrangement;
    if (!expected) {
      cancelPreview();
      forgetAutoplayHold();
      transport.supersede('player-loaded-another-source');
      clearPlaybackBinding();
      reviewSession.active = false;
      reviewSession.suspended = true;
      setStatus(
        'Player Review paused because FeedBack loaded another song or arrangement. Return to Library Doctor and resume the review to reopen this issue.',
        'review',
      );
      reviewOverlay.hide();
      return;
    }
    playbackClock.reset();
    loadedFilename = nextFilename;
    claimAutoplayHold(operation);
    if (
      reviewSession?.active
      && loadedFilename === reviewSession.context?.playback_filename
    ) {
      setMastery(currentCandidate());
    }
  }

  function handleSongLoaded(detail = {}) {
    const currentSong = window.feedBack?.currentSong || detail;
    hostSignals.signal('loaded', {
      filename: String(currentSong?.filename || loadedFilename || ''),
      arrangement: Number(
        currentSong?.arrangementIndex
        ?? currentSong?.arrangement
        ?? loadingArrangement,
      ),
    });
  }

  function handleSongReady(detail = {}) {
    const currentSong = window.feedBack?.currentSong || {};
    const readyDetail = {
      ...detail,
      filename: String(currentSong.filename || loadedFilename || ''),
      arrangement: Number(
        currentSong.arrangementIndex
        ?? currentSong.arrangement
        ?? loadingArrangement,
      ),
      loadedSequence: hostSignals.sequence('loaded'),
    };
    hostSignals.signal('ready', readyDetail);
    playbackClock.update(detail);
    playbackClock.sync();
    if (!reviewSession?.active) return;
    setMastery(currentCandidate());
    refreshTransform();
  }

  function handleSongSeek(detail = {}) {
    hostSignals.signal('seek', detail);
    playbackClock.update(detail);
    timeline.update();
    const activeOperation = transport.current();
    const internal = transport.isReason(detail?.reason, activeOperation);
    timeline.handlePlaybackEvent('seek', detail, { internal });
    if (activeOperation?.expectedSignal === 'seek' && !internal) {
      transport.supersede('player-moved-during-review-command');
    }
    if (previewRun && !previewRun.completing && !internal) {
      cancelPreview();
      transport.supersede('preview-moved-with-player-controls');
      setStatus('The short preview stopped because the Player was moved. Use Play preview to start it again.');
      render();
    }
  }

  function handleSongPosition(detail = {}) {
    playbackClock.update(detail);
    timeline.update();
  }

  function handleSongPause(detail = {}) {
    hostSignals.signal('pause', detail);
    playbackClock.update(detail);
    playbackClock.isPlaying = false;
    timeline.update();
  }

  function handleSongResume(detail = {}) {
    hostSignals.signal('resume', detail);
    playbackClock.update(detail);
    playbackClock.isPlaying = true;
    timeline.update();
  }

  function handleSongEnded() {
    playbackClock.isPlaying = false;
    timeline.supersede('song-ended');
    timeline.update();
  }

  function handleSongStop() {
    cancelPreview();
    timeline.supersede('song-stopped');
    deferAutoplayReleaseUntilSafe();
    transport.supersede('player-stopped');
    clearPlaybackBinding();
    timeline.update();
  }

  function canResume(packageName, adapterId, difficultyScope = getReviewDifficultyScope()) {
    const requestedScope = difficultyScope === 'all_authored' ? 'all_authored' : 'full_only';
    return Boolean(
      reviewSession?.suspended
      && reviewSession.package === packageName
      && reviewSession.adapterId === adapterId
      && reviewSession.difficultyScope === requestedScope
      && currentCandidates().length,
    );
  }

  function destroy() {
    cancelPreview();
    transport.supersede('player-review-destroyed');
    highlight.destroy();
    deferAutoplayReleaseUntilSafe();
    layout.destroy();
    end({ discard: true, navigate: false });
    reviewOverlay.destroy();
    timeline.destroy();
  }

  return {
    canResume,
    destroy,
    handleScreenChanged,
    handleSongEnded,
    handlePlaybackCapabilityEvent,
    handleSongLoading,
    handleSongLoaded,
    handleSongPause,
    handleSongPosition,
    handleSongReady,
    handleSongResume,
    handleSongSeek,
    handleSongStop,
    open,
  };
}
