const MAX_PRESERVED_APPROVALS = 2;

export function createPlayerReviewOptions({
  getCurrentCandidate,
  getSession,
  getUnresolvedCandidates,
  render,
  request,
  refreshTransform,
}) {
  const validationBySession = new WeakMap();

  function validation(owner) {
    let value = validationBySession.get(owner);
    if (!value) {
      value = {
        options: null,
        pending: new Map(),
        failed: new Map(),
        queue: [],
        active: 0,
        activeItems: new Set(),
      };
      validationBySession.set(owner, value);
    }
    return value;
  }

  function freshCandidates(owner) {
    const candidates = owner?.context?.inspection?.candidates;
    return Array.isArray(candidates) ? candidates : [];
  }

  function stagePreservedChoices(owner) {
    const state = validation(owner);
    if (state.options === owner.options) return false;
    state.options = owner.options;
    state.pending.clear();
    state.failed.clear();
    state.queue = [];
    if (!(owner.accepted instanceof Map) || !owner.accepted.size) return false;

    const candidates = new Map(
      freshCandidates(owner).map((candidate) => [candidate.review_item_id, candidate]),
    );
    let changed = false;
    [...owner.accepted].forEach(([reviewItemId, accepted]) => {
      const candidate = candidates.get(reviewItemId);
      const decision = accepted?.decision;
      owner.accepted.delete(reviewItemId);
      changed = true;
      if (!candidate || typeof decision !== 'string' || !decision) return;
      state.pending.set(reviewItemId, { candidate, decision });
      state.queue.push(reviewItemId);
    });
    return changed;
  }

  function approvalState() {
    const owner = getSession();
    if (!owner) {
      return {
        pending: 0, failed: 0, active: 0, queued: 0, blocked: false,
      };
    }
    const state = validation(owner);
    const queued = state.queue.filter((reviewItemId) => (
      state.pending.has(reviewItemId) && !state.activeItems.has(reviewItemId)
    )).length;
    return {
      pending: state.pending.size,
      failed: state.failed.size,
      active: state.active,
      queued,
      blocked: Boolean(state.pending.size || state.failed.size),
    };
  }

  function prioritize(owner, reviewItemId) {
    const state = validation(owner);
    state.queue = state.queue.filter((itemId) => itemId !== reviewItemId);
    state.queue.unshift(reviewItemId);
  }

  function pump(owner) {
    const state = validation(owner);
    while (getSession() === owner && state.active < MAX_PRESERVED_APPROVALS) {
      const index = state.queue.findIndex((reviewItemId) => (
        state.pending.has(reviewItemId) && !state.activeItems.has(reviewItemId)
      ));
      if (index < 0) return;
      const [reviewItemId] = state.queue.splice(index, 1);
      const item = state.pending.get(reviewItemId);
      if (!item) continue;
      state.active += 1;
      state.activeItems.add(reviewItemId);
      Promise.resolve(load(item.candidate, { prefetch: true, approval: true }))
        .catch(() => null)
        .finally(() => {
          state.active = Math.max(0, state.active - 1);
          state.activeItems.delete(reviewItemId);
          pump(owner);
        });
    }
  }

  function retryFailed(owner, candidate) {
    const state = validation(owner);
    const failed = state.failed.get(candidate?.review_item_id);
    if (!failed || failed.candidate.candidate_id !== candidate.candidate_id) return false;
    state.failed.delete(candidate.review_item_id);
    state.pending.set(candidate.review_item_id, { ...failed, candidate });
    prioritize(owner, candidate.review_item_id);
    owner.pendingPlan = null;
    return true;
  }

  function retry(candidate = getCurrentCandidate()) {
    const owner = getSession();
    if (!owner || !candidate) return null;
    owner.options.delete(candidate.candidate_id);
    if (!retryFailed(owner, candidate)) return load(candidate);
    render();
    pump(owner);
    return owner.options.get(candidate.candidate_id)?.promise || null;
  }

  function discardPending(reviewItemId) {
    const owner = getSession();
    if (!owner || typeof reviewItemId !== 'string') return false;
    const state = validation(owner);
    const removed = state.pending.delete(reviewItemId) || state.failed.delete(reviewItemId);
    state.queue = state.queue.filter((itemId) => itemId !== reviewItemId);
    if (removed) {
      owner.pendingPlan = null;
      pump(owner);
    }
    return removed;
  }

  function settlePreserved(owner, candidate, response, error = null) {
    const state = validation(owner);
    const pending = state.pending.get(candidate.review_item_id);
    if (!pending || pending.candidate.candidate_id !== candidate.candidate_id) return false;
    state.pending.delete(candidate.review_item_id);
    state.failed.delete(candidate.review_item_id);
    if (error) {
      state.failed.set(candidate.review_item_id, { ...pending, error });
    } else if (new Set(response?.decision_names || []).has(pending.decision)) {
      owner.accepted.set(candidate.review_item_id, {
        candidate,
        decision: pending.decision,
      });
    }
    owner.pendingPlan = null;
    refreshTransform();
    render();
    return true;
  }

  function prefetchNext(owner, candidate) {
    const approval = validation(owner);
    const nextCandidate = getUnresolvedCandidates().find(
      (item) => item.candidate_id !== candidate.candidate_id
        && !owner.options.has(item.candidate_id)
        && !approval.pending.has(item.review_item_id)
        && !approval.failed.has(item.review_item_id),
    );
    if (nextCandidate) load(nextCandidate, { prefetch: true });
  }

  function state(candidate = getCurrentCandidate()) {
    const session = getSession();
    return candidate && session?.options instanceof Map
      ? session.options.get(candidate.candidate_id) || null
      : null;
  }

  async function load(candidate, {
    prefetch = false,
    approval = false,
  } = {}) {
    const owner = getSession();
    if (!owner) return null;
    const staged = !prefetch && !approval && stagePreservedChoices(owner);
    if (staged) {
      owner.pendingPlan = null;
      refreshTransform();
      render();
    }
    pump(owner);
    const approvalData = validation(owner);
    if (!candidate) return null;
    if (approvalData.failed.has(candidate.review_item_id) && !approval) {
      return owner.options.get(candidate.candidate_id)?.promise || null;
    }
    if (approvalData.pending.has(candidate.review_item_id) && !approval) {
      prioritize(owner, candidate.review_item_id);
      pump(owner);
      return owner.options.get(candidate.candidate_id)?.promise || null;
    }
    const existing = owner.options.get(candidate.candidate_id);
    if (existing?.status === 'ready') {
      if (!prefetch) prefetchNext(owner, candidate);
      return existing.response;
    }
    if (existing?.status === 'loading') return existing.promise;
    const optionState = {
      status: 'loading', response: null, error: null, promise: null,
    };
    owner.options.set(candidate.candidate_id, optionState);
    if (!prefetch) render();
    optionState.promise = request('/reviewed-repair/options', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        package: owner.package,
        adapter_id: owner.adapterId,
        difficulty_scope: owner.difficultyScope,
        candidate_id: candidate.candidate_id,
      }),
    }).then((response) => {
      if (
        getSession() !== owner
        || owner.options.get(candidate.candidate_id) !== optionState
      ) return null;
      if (
        response?.candidate_id !== candidate.candidate_id
        || response?.review_item_id !== candidate.review_item_id
      ) {
        const error = new Error('The choice check returned stale source data. Retry this issue.');
        optionState.status = 'error';
        optionState.error = error;
        optionState.promise = null;
        settlePreserved(owner, candidate, null, error);
        if (getCurrentCandidate()?.candidate_id === candidate.candidate_id) render();
        return null;
      }
      optionState.status = 'ready';
      optionState.response = response;
      optionState.promise = null;
      const allowed = new Set(response.decision_names || []);
      settlePreserved(owner, candidate, response);
      const accepted = owner.accepted.get(candidate.review_item_id);
      if (accepted && !allowed.has(accepted.decision)) {
        owner.accepted.delete(candidate.review_item_id);
        owner.pendingPlan = null;
        refreshTransform();
      }
      if (!prefetch || getCurrentCandidate()?.candidate_id === candidate.candidate_id) render();
      if (!prefetch) prefetchNext(owner, candidate);
      return response;
    }).catch((error) => {
      if (
        getSession() !== owner
        || owner.options.get(candidate.candidate_id) !== optionState
      ) return null;
      optionState.status = 'error';
      optionState.error = error;
      optionState.promise = null;
      settlePreserved(owner, candidate, null, error);
      if (!prefetch || getCurrentCandidate()?.candidate_id === candidate.candidate_id) render();
      return null;
    });
    return optionState.promise;
  }

  return {
    approvalState, discardPending, load, retry, state,
  };
}
