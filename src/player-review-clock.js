export function finiteNumber(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  if (typeof value === 'string' && value.trim() === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function createPlayerReviewClock(window) {
  const clock = {
    mediaTime: 0,
    chartTime: 0,
    duration: null,
    songOffset: 0,
    playbackRate: 1,
    isPlaying: false,
  };

  clock.clamp = (value) => {
    const parsed = Math.max(0, finiteNumber(value) ?? 0);
    return Number.isFinite(clock.duration) ? Math.min(clock.duration, parsed) : parsed;
  };

  clock.update = (detail = {}) => {
    const mediaTime = [
      detail.audioT,
      detail.mediaTime,
      detail.currentTime,
      detail.time,
      detail.landedTime,
      detail.to,
    ].map(finiteNumber).find((value) => value !== null) ?? null;
    const chartTime = finiteNumber(detail.chartT);
    const duration = finiteNumber(detail.duration);
    const playbackRate = finiteNumber(detail.playbackRate);
    if (mediaTime !== null) {
      clock.mediaTime = Math.max(0, mediaTime);
      clock.chartTime = clock.mediaTime + clock.songOffset;
    } else if (chartTime !== null) {
      clock.chartTime = chartTime;
      clock.mediaTime = clock.clamp(chartTime - clock.songOffset);
    }
    if (duration !== null && duration > 0) clock.duration = duration;
    if (playbackRate !== null && playbackRate > 0) clock.playbackRate = playbackRate;
  };

  clock.sync = () => {
    const highway = window.highway;
    const chartTime = typeof highway?.getTime === 'function'
      ? finiteNumber(highway.getTime())
      : null;
    const mediaTime = chartTime === null ? null : chartTime - clock.songOffset;
    clock.update({
      mediaTime,
      audioT: mediaTime,
      chartT: chartTime,
    });
  };

  clock.chartToMedia = (chartTime) => (
    clock.clamp((finiteNumber(chartTime) ?? 0) - clock.songOffset)
  );

  clock.setSongOffset = (value = 0) => {
    clock.songOffset = finiteNumber(value) ?? 0;
  };

  clock.snapshot = () => ({
    mediaTime: clock.mediaTime,
    chartTime: clock.chartTime,
    duration: clock.duration,
    songOffset: clock.songOffset,
    playbackRate: clock.playbackRate,
    isPlaying: clock.isPlaying,
  });

  clock.reset = () => {
    clock.mediaTime = 0;
    clock.chartTime = 0;
    clock.duration = null;
    clock.songOffset = 0;
    clock.playbackRate = 1;
    clock.isPlaying = false;
  };

  clock.stateFrom = (result) => {
    const state = result?.payload?.state;
    return state && typeof state === 'object' ? state : null;
  };

  clock.applyState = (state) => {
    if (!state) return;
    const media = state.media || {};
    clock.update({
      audioT: media.mediaTime ?? media.currentTime,
      chartT: media.chartTime,
      duration: media.duration,
      playbackRate: media.playbackRate,
    });
    if (state.state === 'playing' || state.transport?.isPlaying === true) {
      clock.isPlaying = true;
    } else if (['paused', 'ready', 'ended', 'stopped'].includes(state.state)) {
      clock.isPlaying = false;
    }
  };

  return clock;
}
