export function createPlaybackController({ document, requestGlobal }) {
  let playbackDesired = false;
  let playbackApplied = null;
  let playbackSyncing = false;
  let playerScreenActive = false;
  let playbackNotice = null;
  let playbackStatus = null;
  let playbackPollTimer = 0;
  let playbackSyncRetryTimer = 0;
  let playbackSyncRetryDelay = 500;

  function getPlaybackNotice() {
    if (playbackNotice?.isConnected) return playbackNotice;
    playbackNotice = document.createElement('div');
    playbackNotice.id = 'lh-playback-notice';
    playbackNotice.className = 'lh-playback-notice';
    playbackNotice.setAttribute('role', 'status');
    playbackNotice.setAttribute('aria-live', 'polite');
    playbackNotice.setAttribute('aria-atomic', 'true');
    playbackNotice.hidden = true;
    document.body.appendChild(playbackNotice);
    return playbackNotice;
  }

  function renderPlaybackNotice(status) {
    playbackStatus = status || null;
    const notice = getPlaybackNotice();
    const batch = status?.batch;
    const batchRunning = !!batch?.running;
    const running = !!status?.running || batchRunning;
    const show = playerScreenActive && playbackDesired && running;
    notice.hidden = !show;
    if (!show) return;
    const paused = batchRunning
      ? batch.phase === 'paused'
      : !!status.playback_paused || status.stage === 'paused';
    notice.dataset.stage = paused ? 'paused' : 'pausing';
    notice.textContent = paused
      ? 'Library Doctor scan paused · resumes when you exit'
      : 'Library Doctor scan pausing to prioritize playback…';
    if (batchRunning) {
      notice.textContent = paused
        ? 'Library Doctor batch paused - resumes when you exit'
        : 'Library Doctor batch finishing the current Feedpak before pausing...';
    }
  }

  function schedulePlaybackStatusPoll(delay) {
    clearTimeout(playbackPollTimer);
    if (!playerScreenActive || !playbackDesired) return;
    playbackPollTimer = setTimeout(refreshPlaybackStatus, delay);
  }

  async function refreshPlaybackStatus() {
    if (!playerScreenActive || !playbackDesired) return;
    try {
      const status = await requestGlobal('/status');
      renderPlaybackNotice(status);
      if (
        (status.running && !status.playback_paused)
        || (status.batch?.running && status.batch.phase !== 'paused')
      ) schedulePlaybackStatusPoll(250);
    } catch (error) {
      console.warn('[Library Doctor] Could not confirm paused scan status:', error);
      schedulePlaybackStatusPoll(1000);
    }
  }

  function schedulePlaybackSyncRetry() {
    clearTimeout(playbackSyncRetryTimer);
    if (playbackApplied === playbackDesired) return;
    const delay = playbackSyncRetryDelay;
    playbackSyncRetryDelay = Math.min(5000, playbackSyncRetryDelay * 2);
    playbackSyncRetryTimer = setTimeout(syncPlaybackPriority, delay);
  }

  async function syncPlaybackPriority() {
    if (playbackSyncing) return;
    playbackSyncing = true;
    try {
      while (playbackApplied !== playbackDesired) {
        const active = playbackDesired;
        const payload = await requestGlobal('/playback', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ active }),
        });
        playbackApplied = active;
        playbackSyncRetryDelay = 500;
        if (active) {
          renderPlaybackNotice(payload?.status);
          if (
            (payload?.status?.running && !payload.status.playback_paused)
            || (payload?.status?.batch?.running && payload.status.batch.phase !== 'paused')
          ) schedulePlaybackStatusPoll(250);
        } else {
          clearTimeout(playbackPollTimer);
          renderPlaybackNotice(null);
        }
      }
    } catch (error) {
      console.warn('[Library Doctor] Could not update playback priority:', error);
    } finally {
      playbackSyncing = false;
      if (playbackApplied !== playbackDesired) schedulePlaybackSyncRetry();
    }
  }

  function setPlaybackPriority(active) {
    playbackDesired = !!active;
    clearTimeout(playbackSyncRetryTimer);
    if (!playbackDesired) {
      clearTimeout(playbackPollTimer);
      renderPlaybackNotice(null);
    }
    syncPlaybackPriority();
  }

  function handleScreenChanged(id, from) {
    playerScreenActive = id === 'player';
    if (playerScreenActive) {
      setPlaybackPriority(true);
      renderPlaybackNotice(playbackStatus);
      schedulePlaybackStatusPoll(0);
    } else if (from === 'player') {
      setPlaybackPriority(false);
    }
  }

  function initialize() {
    playerScreenActive = document.querySelector('.screen.active')?.id === 'player';
    setPlaybackPriority(playerScreenActive);
  }

  function destroy() {
    clearTimeout(playbackPollTimer);
    clearTimeout(playbackSyncRetryTimer);
    playbackNotice?.remove();
    playbackNotice = null;
  }

  return { destroy, handleScreenChanged, initialize, setPlaybackPriority };
}
