export function subscribePlayerReviewPlaybackEvents(capabilityApi, onEvent) {
  if (typeof capabilityApi?.subscribe !== 'function') return [];
  return ['paused', 'resumed'].map((eventName) => capabilityApi.subscribe(
    `playback:${eventName}`,
    (detail) => onEvent(eventName, detail?.payload || {}),
  )).filter((unsubscribe) => typeof unsubscribe === 'function');
}

export function createPlayerReviewPlaybackObserver({
  cancelPreview,
  getBinding,
  getOperation,
  getPreviewRun,
  getSession,
  highlight,
  playbackClock,
  render,
  requesterId,
  setStatus,
  timeline,
  transport,
}) {
  return function handlePlaybackCapabilityEvent(type, detail = {}) {
    const binding = getBinding();
    if (!getSession()?.active || !binding) return;
    if (
      detail?.sessionId !== binding.sessionId
      || detail?.target?.targetId !== binding.targetId
    ) return;
    const expectedSignal = type === 'paused' ? 'pause' : 'resume';
    const internal = detail?.requesterId === requesterId
      && getOperation()?.expectedSignal === expectedSignal;
    playbackClock.update(detail?.media || {});
    playbackClock.isPlaying = type === 'resumed';
    timeline.handlePlaybackEvent(expectedSignal, detail, { internal });
    timeline.update();
    if (internal) return;
    const previewRun = getPreviewRun();
    if (previewRun && !previewRun.completing) {
      cancelPreview();
      transport.supersede(`preview-${expectedSignal}-with-player-controls`);
      setStatus(type === 'paused'
        ? 'The short preview was paused with the normal Player controls. Use Jump to issue or Play preview when ready.'
        : 'The short preview stopped because playback was resumed with the normal Player controls. Use Play preview to start it again.');
    }
    if (type === 'resumed') highlight.install();
    render();
  };
}
