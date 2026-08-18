export function createPlayerReviewNavigation({
  cancelPreview,
  getCandidates,
  getCurrentCandidate,
  getSession,
  highlight,
  number,
  openCurrentInPlayer,
  options,
  refreshTransform,
  render,
  setStatus,
  timeline,
}) {
  function unresolvedIndex(start, direction = 1) {
    const session = getSession();
    const candidates = getCandidates();
    for (let step = 1; step <= candidates.length; step += 1) {
      const index = (start + (step * direction) + candidates.length) % candidates.length;
      const itemId = candidates[index].review_item_id;
      if (!session.accepted.has(itemId) && !session.skipped.has(itemId)) return index;
    }
    return Math.max(0, Math.min(candidates.length - 1, start + direction));
  }

  async function moveTo(index) {
    const session = getSession();
    const candidates = getCandidates();
    if (!session || !candidates.length) return;
    cancelPreview();
    timeline.supersede('candidate-changed');
    session.index = Math.max(0, Math.min(candidates.length - 1, index));
    session.tentative = null;
    session.pendingPlan = null;
    highlight.update(getCurrentCandidate());
    render();
    options.load(getCurrentCandidate());
    await openCurrentInPlayer();
  }

  async function acceptAndNext() {
    const session = getSession();
    const candidate = getCurrentCandidate();
    if (!candidate || !session.tentative?.decision) return;
    options.discardPending(candidate.review_item_id);
    session.accepted.set(candidate.review_item_id, {
      candidate,
      decision: session.tentative.decision,
    });
    session.skipped.delete(candidate.review_item_id);
    session.tentative = null;
    session.pendingPlan = null;
    const next = unresolvedIndex(session.index, 1);
    if (next === session.index) {
      setStatus('All current issues have a resolving choice or were skipped for now. You may Apply the accepted changes or return.', 'good');
      render();
      await refreshTransform();
      return;
    }
    await moveTo(next);
  }

  async function skipCurrent() {
    const session = getSession();
    const candidate = getCurrentCandidate();
    if (!candidate || session.busy) return;
    const itemId = candidate.review_item_id;
    options.discardPending(itemId);
    if (session.skipped.has(itemId)) {
      session.skipped.delete(itemId);
      setStatus('This issue is back in the current review pass. Choose a resolving option or skip it again.');
      render();
      options.load(candidate);
      return;
    }
    session.accepted.delete(itemId);
    session.skipped.add(itemId);
    session.tentative = null;
    session.pendingPlan = null;
    await refreshTransform();
    const next = unresolvedIndex(session.index, 1);
    if (next === session.index) {
      setStatus(
        `${number(session.skipped.size)} issue${session.skipped.size === 1 ? '' : 's'} skipped for now. Skipped issues remain unresolved and can be reviewed again from this session.`,
        'review',
      );
      render();
      return;
    }
    await moveTo(next);
  }

  async function reviewSkipped() {
    const session = getSession();
    if (!session.skipped.size || session.busy) return;
    const skippedIds = new Set(session.skipped);
    session.skipped.clear();
    const target = getCandidates().findIndex((item) => skippedIds.has(item.review_item_id));
    await moveTo(target >= 0 ? target : 0);
  }

  return {
    acceptAndNext,
    moveTo,
    reviewSkipped,
    skipCurrent,
    unresolvedIndex,
  };
}
