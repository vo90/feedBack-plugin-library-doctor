import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { JSDOM } from 'jsdom';

import { bootLibraryDoctor } from '../../../src/app.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, '..', '..', '..');

export function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

export function jsonResponse(body, { status = 200 } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return structuredClone(body); },
  };
}

export async function waitFor(predicate, message = 'condition', timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      if (await predicate()) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  if (lastError) throw lastError;
  throw new Error(`Timed out waiting for ${message}`);
}

function routeKey(url) {
  const parsed = new URL(String(url), 'http://127.0.0.1:18000');
  return `${parsed.pathname}${parsed.search}`;
}

async function resolveWithAbort(value, signal) {
  if (!signal) return value;
  if (signal.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
  let removeAbortListener = () => {};
  const aborted = new Promise((_, reject) => {
    const onAbort = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
    signal.addEventListener('abort', onAbort, { once: true });
    removeAbortListener = () => signal.removeEventListener('abort', onAbort);
  });
  try {
    return await Promise.race([Promise.resolve(value), aborted]);
  } finally {
    removeAbortListener();
  }
}

export async function launchLibraryDoctor({
  status,
  results = { total: 0, items: [] },
  rules = { items: [] },
  repairs = { schema: 'library_doctor.repair_catalog.v1', items: [], combined: null },
  reviewedRepairs = { schema: 'library_doctor.reviewed_repair_catalog.v1', items: [] },
  reviewedCandidates = [],
  history = { items: [] },
  songs = { total: 0, songs: [] },
  chartInput = null,
  capabilityDispatch,
  playbackChartOffset = 0,
  playbackDuration = 120,
  playbackArrangement = 'Lead',
  playbackRate = 1,
  simulateFreshSongAutoplay = false,
  noteStateProvider = null,
  route,
} = {}) {
  const screen = await fs.readFile(path.join(ROOT, 'screen.html'), 'utf8');
  const dom = new JSDOM(
    `<!doctype html><body><section id="plugin-library_doctor" class="screen active">${screen}</section><section id="player" class="screen"><canvas id="highway"></canvas></section><div id="plugin-dropdown"></div></body>`,
    {
      url: 'http://127.0.0.1:18000/#/plugins',
      runScripts: 'outside-only',
      pretendToBeVisual: true,
    },
  );
  const { window } = dom;
  const requests = [];
  const capabilityRequests = [];
  const coreEvents = [];
  const navigations = [];
  const subscriptions = new Map();
  const subscriptionListeners = new Map();
  const capabilitySubscriptions = new Map();
  const providers = new Map();
  let activeProvider = null;
  let activeNoteStateProvider = typeof noteStateProvider === 'function'
    ? noteStateProvider
    : null;
  let lastChartOutput = null;
  let mastery = null;
  let playbackTime = 0;
  let playbackChartTime = Number(playbackChartOffset || 0);
  let playbackState = 'idle';
  let playbackPlaying = false;
  let playbackSessionId = null;
  let playbackTargetId = null;
  let playbackArrangementIndex = 0;
  let playbackSessionCounter = 0;
  let activePlaybackCapabilityDispatches = 0;
  let autoplayGeneration = 0;
  let autoplayToken = 0;
  let autoplayHeld = false;
  let autoplayPending = false;
  let autoplayDeferredStart = null;
  const autoplayActions = [];
  function finitePlaybackNumber(value) {
    if (value === null || value === undefined || typeof value === 'boolean') return null;
    if (typeof value === 'string' && value.trim() === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  function setPlaybackTime(value) {
    const parsed = Number(value);
    playbackTime = parsed;
    playbackChartTime = parsed + Number(playbackChartOffset || 0);
  }
  function setPlaybackClocks({ audioTime, chartTime } = {}) {
    const nextAudioTime = finitePlaybackNumber(audioTime);
    const nextChartTime = finitePlaybackNumber(chartTime);
    if (nextAudioTime !== null) playbackTime = nextAudioTime;
    if (nextChartTime !== null) playbackChartTime = nextChartTime;
  }
  function preparePlaybackStart(request) {
    autoplayGeneration += 1;
    autoplayHeld = false;
    autoplayPending = Boolean(simulateFreshSongAutoplay);
    autoplayDeferredStart = null;
    playbackSessionCounter += 1;
    playbackSessionId = `synthetic-session-${playbackSessionCounter}`;
    playbackTargetId = `synthetic-target-${playbackSessionCounter}`;
    playbackArrangementIndex = Number(request.args.arrangement ?? 0);
    window.feedBack.currentSong = {
      filename: request.args.target.filename,
      arrangement: playbackArrangement,
      arrangementIndex: playbackArrangementIndex,
      duration: playbackDuration,
    };
  }
  function startFreshSongAutoplay() {
    autoplayActions.push({ event: 'start', coreEventCount: coreEvents.length });
    playbackState = 'playing';
    playbackPlaying = true;
    const detail = {
      time: playbackTime,
      audioT: playbackTime,
      chartT: playbackChartTime,
    };
    deliverCoreEvent('song:play', detail);
    deliverCoreEvent('song:resume', detail);
  }
  function consumeFreshSongAutoplay() {
    if (!autoplayPending) return;
    autoplayPending = false;
    if (playbackPlaying) return;
    if (autoplayHeld) {
      autoplayDeferredStart = startFreshSongAutoplay;
    } else {
      startFreshSongAutoplay();
    }
  }
  function holdAutoplay() {
    const generation = autoplayGeneration;
    const token = ++autoplayToken;
    let released = false;
    autoplayHeld = true;
    autoplayActions.push({ event: 'hold', coreEventCount: coreEvents.length });
    function release() {
      if (released || generation !== autoplayGeneration || token !== autoplayToken) return;
      released = true;
      autoplayHeld = false;
      autoplayActions.push({ event: 'release', coreEventCount: coreEvents.length });
      const start = autoplayDeferredStart;
      autoplayDeferredStart = null;
      start?.();
    }
    release.settle = () => {
      if (generation !== autoplayGeneration || token !== autoplayToken) return;
      autoplayActions.push({ event: 'settle', coreEventCount: coreEvents.length });
    };
    return release;
  }
  function playbackDetail(extra = {}) {
    return {
      time: playbackTime,
      audioT: playbackTime,
      chartT: playbackChartTime,
      duration: playbackDuration,
      playbackRate,
      ...extra,
    };
  }
  function emitCapability(event, payload = {}) {
    const detail = {
      capability: 'playback',
      event: event.replace(/^playback:/, ''),
      payload: structuredClone(payload),
      timestamp: Date.now(),
    };
    [...(capabilitySubscriptions.get(event) || [])].forEach((listener) => listener(detail));
    [...(capabilitySubscriptions.get('*') || [])].forEach((listener) => listener(detail));
  }
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.HTMLMediaElement.prototype.load = () => {};
  window.URL.createObjectURL = () => 'blob:synthetic-library-doctor';
  window.URL.revokeObjectURL = () => {};
  function deliverCoreEvent(name, detail = {}) {
    coreEvents.push({ name, detail: structuredClone(detail) });
    if (name === 'song:loading') playbackState = 'loading';
    if (name === 'song:ready') playbackState = playbackPlaying ? 'playing' : 'ready';
    if (name === 'song:pause') {
      playbackState = 'paused';
      playbackPlaying = false;
    }
    if (name === 'song:play' || name === 'song:resume') {
      playbackState = 'playing';
      playbackPlaying = true;
    }
    if (name === 'song:stop') {
      playbackState = 'stopped';
      playbackPlaying = false;
    }
    subscriptions.get(name)?.({ detail });
    const playbackEvent = name === 'song:pause'
      ? 'playback:paused'
      : (name === 'song:resume' ? 'playback:resumed' : null);
    if (playbackEvent && activePlaybackCapabilityDispatches === 0) {
      emitCapability(playbackEvent, {
        requesterId: detail.requesterId || 'legacy-event-bus',
        sessionId: playbackSessionId,
        state: name === 'song:pause' ? 'paused' : 'playing',
        target: playbackTargetId ? { targetId: playbackTargetId } : null,
        media: playbackDetail(),
      });
    }
    if (name === 'song:ready') consumeFreshSongAutoplay();
  }
  function emit(name, detail = {}) {
    const nextTime = finitePlaybackNumber(detail.audioT ?? detail.time ?? detail.to);
    const nextChartTime = finitePlaybackNumber(detail.chartT);
    if (nextTime !== null) playbackTime = nextTime;
    if (nextChartTime !== null) {
      playbackChartTime = nextChartTime;
    } else if (nextTime !== null) {
      // Convenience events in the synthetic tests historically moved both
      // clocks. Tests for real core races use deliverCoreEvent after setting
      // the audio and highway clocks independently.
      playbackChartTime = nextTime + Number(playbackChartOffset || 0);
    }
    deliverCoreEvent(name, detail);
  }
  function emitPlaybackLoaded() {
    deliverCoreEvent('song:loaded', window.feedBack.currentSong || {});
  }
  function navigate(id) {
    const from = window.document.querySelector('.screen.active')?.id || null;
    window.document.querySelectorAll('.screen').forEach((node) => node.classList.remove('active'));
    window.document.getElementById(id)?.classList.add('active');
    navigations.push({ id, from });
    emit('screen:changed', { id, from });
  }
  window.highway = {
    getAudioElement() {
      return {
        currentTime: playbackTime,
        duration: playbackDuration,
        playbackRate,
      };
    },
    getTime() { return playbackChartTime; },
    setMastery(value) { mastery = value; },
    getNoteStateProvider() { return activeNoteStateProvider; },
    setNoteStateProvider(provider) {
      activeNoteStateProvider = typeof provider === 'function' ? provider : null;
    },
  };
  window.feedBack = {
    on(name, callback) {
      let listeners = subscriptionListeners.get(name);
      if (!listeners) {
        listeners = new Set();
        subscriptionListeners.set(name, listeners);
        subscriptions.set(name, (event) => {
          [...(subscriptionListeners.get(name) || [])].forEach((listener) => listener(event));
        });
      }
      listeners.add(callback);
      // FeedBack core's event bus returns void. Consumers must pair on/off.
      return undefined;
    },
    off(name, callback) {
      const listeners = subscriptionListeners.get(name);
      if (!listeners) return;
      listeners.delete(callback);
      if (listeners.size > 0) return;
      subscriptionListeners.delete(name);
      subscriptions.delete(name);
    },
    emit,
    holdAutoplay,
    navigate,
    capabilities: {
      version: 1,
      subscribe(event, callback) {
        const listeners = capabilitySubscriptions.get(event) || new Set();
        listeners.add(callback);
        capabilitySubscriptions.set(event, listeners);
        return () => {
          listeners.delete(callback);
          if (!listeners.size) capabilitySubscriptions.delete(event);
        };
      },
      async dispatch(request) {
        capabilityRequests.push(request);
        if (request.capability === 'playback' && request.command === 'start') {
          preparePlaybackStart(request);
        }
        let custom = null;
        if (capabilityDispatch) {
          const playbackCommand = request.capability === 'playback';
          if (playbackCommand) activePlaybackCapabilityDispatches += 1;
          try {
            custom = await capabilityDispatch(request, {
              emit,
              emitCoreEvent: deliverCoreEvent,
              emitPlaybackLoaded,
              navigate,
              providers,
              getPlaybackClocks() {
                return { audioTime: playbackTime, chartTime: playbackChartTime };
              },
              setPlaybackClocks,
              setPlaybackTime,
            });
          } finally {
            if (playbackCommand) activePlaybackCapabilityDispatches -= 1;
          }
        }
        if (custom) {
          const playbackEvent = request.capability === 'playback'
            && request.command === 'pause'
            ? 'playback:paused'
            : (request.capability === 'playback' && request.command === 'resume'
              ? 'playback:resumed'
              : null);
          if (playbackEvent) {
            emitCapability(playbackEvent, {
              requesterId: request.source,
              sessionId: playbackSessionId,
              state: request.command === 'pause' ? 'paused' : 'playing',
              target: playbackTargetId ? { targetId: playbackTargetId } : null,
              media: playbackDetail(),
            });
          }
          return custom;
        }
        if (request.capability === 'chart-transform') {
          if (request.command === 'inspect') {
            return {
              status: 'handled',
              outcome: 'handled',
              payload: {
                active: activeProvider,
                available: true,
                installed: Boolean(activeProvider),
                surfaces: activeProvider ? 1 : 0,
              },
            };
          }
          if (request.command === 'register-provider') {
            providers.set(request.args.providerId, request.args.transform);
          } else if (request.command === 'unregister-provider') {
            providers.delete(request.args.providerId);
            if (activeProvider === request.args.providerId) activeProvider = null;
          } else if (request.command === 'select-provider') {
            activeProvider = request.args.providerId;
            if (chartInput && activeProvider) {
              lastChartOutput = providers.get(activeProvider)?.(structuredClone(chartInput)) || null;
            }
          } else if (request.command === 'clear-provider') {
            activeProvider = null;
          } else if (request.command === 'refresh' && chartInput && activeProvider) {
            lastChartOutput = providers.get(activeProvider)?.(structuredClone(chartInput)) || null;
          }
          return {
            status: 'handled',
            outcome: 'handled',
            payload: {
              active: activeProvider,
              available: true,
              installed: Boolean(activeProvider),
              surfaces: activeProvider ? 1 : 0,
              ...(request.command === 'refresh'
                ? { refreshed: Boolean(chartInput && activeProvider) }
                : {}),
            },
          };
        }
        if (request.capability === 'playback') {
          if (request.command === 'inspect') {
            return {
              status: 'handled',
              outcome: 'handled',
              payload: {
                state: {
                  sessionId: playbackSessionId,
                  state: playbackState,
                  target: playbackTargetId ? {
                    targetId: playbackTargetId,
                    arrangementRef: `arrangement-${playbackArrangementIndex}`,
                  } : null,
                  transport: {
                    isPlaying: playbackPlaying,
                    readiness: playbackState === 'loading' ? 'loading' : 'ready',
                  },
                  media: playbackDetail({
                    currentTime: playbackTime,
                    mediaTime: playbackTime,
                    readiness: playbackState === 'loading' ? 'loading' : 'ready',
                  }),
                },
              },
            };
          }
          if (request.command === 'start') {
            setPlaybackTime(Number(request.args.startTime || 0));
            emit('song:loading', {
              filename: request.args.target.filename,
              arrangement: playbackArrangementIndex,
            });
            navigate('player');
            // This mirrors the core highway contract: identity and arrangement
            // live on currentSong, while song:ready only announces readiness.
            emitPlaybackLoaded();
            deliverCoreEvent('song:ready', { hasPhraseData: true });
          } else if (request.command === 'pause') {
            emit('song:pause', playbackDetail({ requesterId: request.source }));
          } else if (request.command === 'resume') {
            emit('song:play', playbackDetail({ requesterId: request.source }));
            emit('song:resume', playbackDetail({ requesterId: request.source }));
          } else if (request.command === 'seek') {
            const from = playbackTime;
            setPlaybackTime(Number(request.args.time || 0));
            emit('song:seek', { from, to: playbackTime, reason: request.args.reason });
            emit('song:position-changed', playbackDetail());
          } else if (request.command === 'stop') {
            emit('song:stop', {});
          }
          return { status: 'handled', outcome: 'handled', payload: {} };
        }
        return { status: 'no-owner', outcome: 'no-owner', reason: 'Synthetic capability unavailable' };
      },
    },
  };
  window.fetch = async (url, options = {}) => {
    const key = routeKey(url);
    const request = {
      key,
      method: String(options.method || 'GET').toUpperCase(),
      body: options.body,
      signal: options.signal,
    };
    requests.push(request);
    if (route) {
      let custom = await resolveWithAbort(route(request), options.signal);
      if (!custom && key.startsWith('/api/plugins/library_doctor/status?')) {
        custom = await resolveWithAbort(route({
          ...request,
          key: '/api/plugins/library_doctor/status',
        }), options.signal);
      }
      if (custom) return custom;
    }
    if (key === '/api/plugins/library_doctor/playback') {
      return jsonResponse({ changed: false, status: status || {} });
    }
    if (key === '/api/plugins/library_doctor/status'
        || key.startsWith('/api/plugins/library_doctor/status?')) return jsonResponse(status || {});
    if (key === '/api/plugins/library_doctor/rules'
        || key.startsWith('/api/plugins/library_doctor/rules?')) return jsonResponse(rules);
    if (key === '/api/plugins/library_doctor/repairs') return jsonResponse(repairs);
    if (key === '/api/plugins/library_doctor/reviewed-repairs') return jsonResponse(reviewedRepairs);
    if (key === '/api/plugins/library_doctor/reviewed-repair/options') {
      const body = JSON.parse(options.body || '{}');
      const candidate = reviewedCandidates.find(
        (item) => item.candidate_id === body.candidate_id,
      );
      if (!candidate) {
        return jsonResponse({ detail: `Unknown reviewed candidate: ${body.candidate_id}` }, { status: 404 });
      }
      const adapter = reviewedRepairs.items.find(
        (item) => item.adapter_id === body.adapter_id,
      );
      const definitionMap = new Map(
        (adapter?.decisions || []).map((item) => [item.name, item]),
      );
      const blocked = Boolean(candidate.blockers?.length);
      const decisionNames = blocked
        ? []
        : (candidate.decision_names || []).filter(
          (name) => name !== 'leave_unchanged' && definitionMap.has(name),
        );
      return jsonResponse({
        schema: 'library_doctor.reviewed_repair_options.v1',
        package: body.package,
        adapter_id: body.adapter_id,
        difficulty_scope: body.difficulty_scope,
        candidate_id: candidate.candidate_id,
        review_item_id: candidate.review_item_id,
        decision_names: decisionNames,
        decision_definitions: decisionNames.map((name) => definitionMap.get(name)),
        omitted_decisions: [],
        available: decisionNames.length > 0,
        blocked,
        message: blocked
          ? 'This issue is blocked and has no safe reviewed choice. Skip it for now.'
          : '',
        options_id: `synthetic-${candidate.candidate_id}`,
      });
    }
    if (key.startsWith('/api/plugins/library_doctor/repair/history?')) return jsonResponse(history);
    if (key.startsWith('/api/plugins/library_doctor/results?')) return jsonResponse(results);
    if (key.startsWith('/api/library?')) return jsonResponse(songs);
    return jsonResponse({ detail: `Unhandled synthetic route: ${key}` }, { status: 501 });
  };
  const controller = bootLibraryDoctor(window);

  await waitFor(
    () => requests.some(({ key }) => key.startsWith('/api/plugins/library_doctor/results?')),
    'initial Library Doctor results request',
  );
  await waitFor(
    () => window.document.querySelector('#lh-health-workspace')?.dataset.viewState,
    'initial dashboard view',
  );

  return {
    dom,
    window,
    document: window.document,
    requests,
    capabilityRequests,
    coreEvents,
    navigations,
    subscriptions,
    get listenerCount() {
      return [...subscriptionListeners.values()]
        .reduce((count, listeners) => count + listeners.size, 0);
    },
    controller,
    get lastChartOutput() { return lastChartOutput; },
    get noteStateProvider() { return activeNoteStateProvider; },
    get mastery() { return mastery; },
    get playbackTime() { return playbackTime; },
    get playbackChartTime() { return playbackChartTime; },
    get playbackState() { return playbackState; },
    get autoplayState() {
      return {
        actions: structuredClone(autoplayActions),
        held: autoplayHeld,
        pending: autoplayPending,
      };
    },
    close() {
      controller.destroy();
      dom.window.close();
    },
  };
}
