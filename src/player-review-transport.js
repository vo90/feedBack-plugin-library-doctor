class SupersededTransportOperation extends Error {
  constructor() {
    super('A newer Player Review transport action replaced this one.');
    this.name = 'SupersededTransportOperation';
  }
}

export function failedCapability(result) {
  return !result || [
    'error', 'failed', 'no-owner', 'no-handler', 'unsupported-command',
    'unavailable', 'blocked', 'user-action-required', 'incompatible-version',
    'stale', 'cancelled', 'no-target',
  ].includes(result.status) || [
    'failed', 'denied', 'unavailable', 'stale', 'cancelled', 'no-target',
  ].includes(result.outcome);
}

export function handlerTimedOut(result) {
  return result?.outcome === 'failed'
    && /Handler\s+core\.playback\s+timed out after\s+\d+\s*ms/i.test(String(result?.reason || ''));
}

export function formatTimelineTime(value, precise = false) {
  const seconds = Math.max(0, Number.isFinite(Number(value)) ? Number(value) : 0);
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds - (minutes * 60);
  return `${minutes}:${remainder.toFixed(precise ? 2 : 1).padStart(precise ? 5 : 4, '0')}`;
}

export function nextPaint(window) {
  return new Promise((resolve) => {
    if (typeof window.requestAnimationFrame === 'function') {
      window.requestAnimationFrame(() => resolve());
    } else {
      window.setTimeout(resolve, 0);
    }
  });
}

export function createHostSignals(types) {
  const signals = new Map(types.map((type) => [type, { sequence: 0, events: [] }]));
  return {
    observed(type, after, predicate = null) {
      return signals.get(type)?.events.find((event) => (
        event.sequence > after && (!predicate || predicate(event.detail))
      ))?.detail || null;
    },
    sequence(type) {
      return signals.get(type)?.sequence || 0;
    },
    signal(type, detail = {}) {
      const state = signals.get(type);
      if (!state) return;
      state.sequence += 1;
      state.events.push({ sequence: state.sequence, detail });
      if (state.events.length > 24) state.events.shift();
    },
  };
}

export function createPlayerReviewTransport({ isAvailable, window }) {
  let generation = 0;
  let traceSequence = 0;
  let chain = Promise.resolve();
  let active = null;

  function trace(operation, event, detail = {}) {
    const entry = {
      schema: 'library_doctor.player_review_transport.v1',
      sequence: ++traceSequence,
      operation: operation?.id ?? null,
      kind: operation?.kind || 'none',
      event,
      elapsedMs: operation?.startedAt ? Date.now() - operation.startedAt : 0,
      ...detail,
    };
    let encoded = '';
    try {
      encoded = JSON.stringify(entry);
    } catch (_error) {
      encoded = JSON.stringify({
        operation: entry.operation,
        kind: entry.kind,
        event: entry.event,
        loggingError: 'trace-not-serializable',
      });
    }
    console.info(`[Library Doctor Player Review transport] ${encoded}`);
  }

  function isCurrent(operation) {
    return Boolean(
      operation
      && operation.id === generation
      && !operation.cancelled
      && isAvailable(),
    );
  }

  function assertCurrent(operation) {
    if (!isCurrent(operation)) throw new SupersededTransportOperation();
  }

  function supersede(reason = 'superseded') {
    generation += 1;
    if (active) {
      active.cancelled = true;
      trace(active, 'superseded', { reason });
    }
  }

  function run(kind, task) {
    const operation = {
      id: generation + 1,
      kind,
      step: 0,
      startedAt: 0,
      cancelled: false,
      expectedSignal: null,
    };
    generation = operation.id;
    if (active) active.cancelled = true;
    const scheduled = chain.catch(() => {}).then(async () => {
      try {
        assertCurrent(operation);
        active = operation;
        operation.startedAt = Date.now();
        trace(operation, 'started');
        const value = await task(operation);
        assertCurrent(operation);
        trace(operation, 'completed');
        return value;
      } catch (error) {
        if (error instanceof SupersededTransportOperation) {
          trace(operation, 'cancelled');
          return null;
        }
        trace(operation, 'failed', { message: error.message });
        throw error;
      } finally {
        if (active === operation) active = null;
      }
    });
    chain = scheduled.catch(() => {});
    return scheduled;
  }

  async function delay(operation, milliseconds) {
    assertCurrent(operation);
    await new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    assertCurrent(operation);
  }

  function reason(operation, action) {
    operation.step += 1;
    return `library-doctor:${operation.id}:${operation.step}:${action}`;
  }

  function isReason(value, operation = null) {
    const valueString = String(value || '');
    if (!valueString.startsWith('library-doctor:')) return false;
    return !operation || valueString.startsWith(`library-doctor:${operation.id}:`);
  }

  return {
    assertCurrent,
    current: () => active,
    delay,
    isReason,
    reason,
    run,
    settle: () => chain.catch(() => {}),
    supersede,
    trace,
  };
}
