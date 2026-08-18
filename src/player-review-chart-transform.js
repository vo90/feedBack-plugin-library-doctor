import { rewriteEvidence } from './player-review-transform.js';
import { failedCapability } from './player-review-transport.js';

const PROVIDER_ID = 'library_doctor.player-review';
const TRACE_SCHEMA = 'library_doctor.player_review_chart_transform.v1';

export function createPlayerReviewChartTransform({
  dispatch,
  getChoices,
  getLoadedFilename,
  getSession,
  window,
}) {
  let previousProviderId = null;
  let providerRegistered = false;
  let providerSelected = false;
  let available = false;
  let sequence = 0;
  let lastEvaluation = null;

  function trace(event, facts = {}) {
    sequence += 1;
    const payload = {
      schema: TRACE_SCHEMA,
      sequence,
      event,
      ...facts,
    };
    try {
      window.console?.info?.(
        `[Library Doctor Player Review chart transform] ${JSON.stringify(payload)}`,
      );
    } catch (_error) {
      // Diagnostics must never affect Player Review.
    }
  }

  function transformChart(input) {
    const session = getSession();
    if (
      !session?.active
      || getLoadedFilename() !== session.context?.playback_filename
      || !input || typeof input !== 'object'
    ) return null;
    let changed = 0;
    let ambiguous = 0;
    const choices = getChoices();
    choices.forEach(({ candidate, decision }) => {
      const result = rewriteEvidence(input, candidate, decision);
      changed += result.changed;
      ambiguous += result.ambiguous;
    });
    lastEvaluation = {
      choices: Math.max(lastEvaluation?.choices || 0, choices.length),
      changed: Math.max(lastEvaluation?.changed || 0, changed),
      ambiguous: Math.max(lastEvaluation?.ambiguous || 0, ambiguous),
    };
    return input;
  }

  async function activate() {
    available = false;
    const inspected = await dispatch('chart-transform', 'inspect');
    if (failedCapability(inspected)) {
      trace('activation-failed', { phase: 'inspect' });
      return false;
    }
    previousProviderId = inspected?.payload?.active || null;
    const registered = await dispatch('chart-transform', 'register-provider', {
      providerId: PROVIDER_ID,
      label: 'Library Doctor Player Review',
      transform: transformChart,
    });
    if (failedCapability(registered)) {
      trace('activation-failed', { phase: 'register' });
      return false;
    }
    providerRegistered = true;
    const selected = await dispatch('chart-transform', 'select-provider', {
      providerId: PROVIDER_ID,
    });
    if (failedCapability(selected) || selected?.payload?.active !== PROVIDER_ID) {
      trace('activation-failed', { phase: 'select' });
      return false;
    }
    providerSelected = true;
    available = true;
    trace('activated', {
      installed: selected?.payload?.installed === true,
      surfaces: Number(selected?.payload?.surfaces || 0),
    });
    return true;
  }

  async function bindCurrentSurface(reason) {
    if (!available || !providerRegistered || !providerSelected) return null;
    const inspected = await dispatch('chart-transform', 'inspect');
    if (failedCapability(inspected) || inspected?.payload?.active !== PROVIDER_ID) {
      available = false;
      trace('binding-lost', {
        reason,
        active: inspected?.payload?.active || null,
      });
      return null;
    }
    // Selecting the already-active provider is intentional. Core installs the
    // transform on the current primary Highway and any live secondary surfaces,
    // closing the song-reload race where the provider was bound to an old surface.
    const rebound = await dispatch('chart-transform', 'select-provider', {
      providerId: PROVIDER_ID,
    });
    if (
      failedCapability(rebound)
      || rebound?.payload?.active !== PROVIDER_ID
      || rebound?.payload?.installed !== true
    ) {
      trace('binding-failed', {
        reason,
        active: rebound?.payload?.active || null,
        installed: rebound?.payload?.installed === true,
        surfaces: Number(rebound?.payload?.surfaces || 0),
      });
      return null;
    }
    return rebound.payload;
  }

  async function refresh({ reason = 'refresh', requireChange = false } = {}) {
    if (!available) return { available: false, verified: false };
    lastEvaluation = null;
    const binding = await bindCurrentSurface(reason);
    if (!binding) return { available, verified: false };
    const refreshed = await dispatch('chart-transform', 'refresh');
    const refreshSucceeded = !failedCapability(refreshed)
      && refreshed?.payload?.active === PROVIDER_ID
      && refreshed?.payload?.installed === true
      && refreshed?.payload?.refreshed === true;
    const evaluation = lastEvaluation || { choices: 0, changed: 0, ambiguous: 0 };
    const verified = refreshSucceeded
      && evaluation.ambiguous === 0
      && (!requireChange || evaluation.changed > 0);
    trace('refresh', {
      reason,
      outcome: verified ? 'verified' : 'unverified',
      active: refreshed?.payload?.active || null,
      installed: refreshed?.payload?.installed === true,
      surfaces: Number(refreshed?.payload?.surfaces || binding.surfaces || 0),
      refreshed: refreshed?.payload?.refreshed === true,
      choices: evaluation.choices,
      changed: evaluation.changed,
      ambiguous: evaluation.ambiguous,
    });
    return {
      available,
      verified,
      refreshSucceeded,
      ...evaluation,
    };
  }

  async function restore() {
    if (!providerRegistered) return;
    const inspected = await dispatch('chart-transform', 'inspect');
    const active = inspected?.payload?.active;
    if (providerSelected && active === PROVIDER_ID) {
      if (previousProviderId && previousProviderId !== PROVIDER_ID) {
        await dispatch('chart-transform', 'select-provider', {
          providerId: previousProviderId,
        });
      } else {
        await dispatch('chart-transform', 'clear-provider');
      }
    }
    await dispatch('chart-transform', 'unregister-provider', {
      providerId: PROVIDER_ID,
    });
    providerRegistered = false;
    providerSelected = false;
    available = false;
    previousProviderId = null;
    lastEvaluation = null;
    trace('restored');
  }

  return {
    activate,
    isAvailable: () => available,
    refresh,
    restore,
  };
}
