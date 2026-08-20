function normalized(value) {
  return value === 'all_authored' ? 'all_authored' : 'full_only';
}

export function createReviewDifficultyState({
  document,
  localStorage,
  state,
  storageKey,
  refresh,
}) {
  try { state.reviewDifficultyDefaultScope = normalized(localStorage.getItem(storageKey)); }
  catch (_) { state.reviewDifficultyDefaultScope = 'full_only'; }
  state.reviewDifficultyScope = state.reviewDifficultyDefaultScope;

  function getReviewDifficultyScope() {
    return state.reviewDifficultyScope;
  }

  function setReviewDifficultyDefaultScope(value) {
    state.reviewDifficultyDefaultScope = normalized(value);
    try { localStorage.setItem(storageKey, state.reviewDifficultyDefaultScope); } catch (_) { /* best effort */ }
    return state.reviewDifficultyDefaultScope;
  }

  function setReviewDifficultyScope(value, { refresh: shouldRefresh = true } = {}) {
    state.reviewDifficultyScope = normalized(value);
    const control = document.getElementById('lh-review-list-difficulty-scope');
    if (control && control.value !== state.reviewDifficultyScope) control.value = state.reviewDifficultyScope;
    if (shouldRefresh && state.active) refresh();
    return state.reviewDifficultyScope;
  }

  function resetReviewDifficultyScope({ refresh: shouldRefresh = true } = {}) {
    return setReviewDifficultyScope(state.reviewDifficultyDefaultScope, { refresh: shouldRefresh });
  }

  return {
    getReviewDifficultyScope,
    resetReviewDifficultyScope,
    setReviewDifficultyDefaultScope,
    setReviewDifficultyScope,
  };
}
