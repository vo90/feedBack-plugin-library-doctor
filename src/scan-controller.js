export function createScanController({
  actions: actionRegistry,
  getElements,
  isAbortError,
  localStorage,
  number,
  request,
  setHidden,
  state,
  text,
  window,
  workerLimitKey,
  workerModeKey,
}) {
  let statusRequest = 0;
  const el = new Proxy({}, {
    get(_target, key) { return getElements()?.[key]; },
  });

  function selectedPath() {
    return state.targetKind === 'library' ? '' : state.targetPaths[state.targetKind];
  }

  function pickerAvailable() {
    const desktop = window.feedBackDesktop;
    if (!desktop) return false;
    return state.targetKind === 'folder'
      ? typeof desktop.pickDirectory === 'function'
      : state.targetKind === 'file' && typeof desktop.pickFile === 'function';
  }

  function loadWorkerSettings() {
    try {
      const savedMode = localStorage.getItem(workerModeKey);
      state.workerMode = savedMode === 'custom' ? 'custom' : 'automatic';
      const savedLimit = Number(localStorage.getItem(workerLimitKey));
      state.workerLimit = Number.isInteger(savedLimit) && savedLimit > 0 ? savedLimit : 1;
    } catch (_) {
      state.workerMode = 'automatic';
      state.workerLimit = 1;
    }
    el.workerMode.value = state.workerMode;
    el.workerLimit.value = String(state.workerLimit);
  }

  function saveWorkerSettings() {
    try {
      localStorage.setItem(workerModeKey, state.workerMode);
      localStorage.setItem(workerLimitKey, String(state.workerLimit));
    } catch (_) { /* Scanning still works when browser storage is unavailable. */ }
  }

  function updateWorkerControls() {
    const running = !!state.status?.running || !!state.status?.repairing || !!state.status?.batch?.running;
    const custom = state.workerMode === 'custom';
    el.workerMode.value = state.workerMode;
    el.workerMode.disabled = running;
    el.workerLimit.value = String(state.workerLimit);
    el.workerLimit.disabled = running;
    setHidden(el.workerLimitWrap, !custom);

    const policy = state.status?.worker_policy;
    const selected = Number(policy?.selected_workers || 0);
    if (policy && policy.reason !== 'not_started' && selected > 0) {
      text(
        el.workerSummary,
        `${selected} worker${selected === 1 ? '' : 's'} (${policy.mode === 'custom' ? 'custom maximum' : 'automatic'})`,
      );
    } else {
      text(el.workerSummary, custom ? `Custom maximum: ${number(state.workerLimit)}` : 'Automatic');
    }
  }

  function updateTargetControls() {
    const running = !!state.status?.running || !!state.status?.repairing || !!state.status?.batch?.running;
    const path = selectedPath();
    el.targetOptions.forEach((option) => {
      option.checked = option.value === state.targetKind;
      option.disabled = running;
    });

    if (state.targetKind === 'library') {
      text(el.targetPath, 'Configured song library');
      setHidden(el.chooseTarget, true);
      setHidden(el.pickerNote, true);
    } else {
      const folder = state.targetKind === 'folder';
      text(el.targetPath, path || (folder ? 'No folder selected' : 'No package selected'));
      text(el.chooseTarget, folder ? (path ? 'Change folder' : 'Choose folder')
        : (path ? 'Change Feedpak' : 'Choose Feedpak'));
      setHidden(el.chooseTarget, running);
      el.chooseTarget.disabled = running || !pickerAvailable();
      const unavailable = !pickerAvailable();
      setHidden(el.pickerNote, !unavailable);
      if (unavailable) {
        text(el.pickerNote, 'Folder and file selection requires FeedBack Desktop.');
      }
    }
    el.scan.disabled = running || (state.targetKind !== 'library' && !path);
    el.scanAll.disabled = el.scan.disabled;
    el.deepAudio.disabled = running;
    setHidden(el.playerReviewScopeNote, state.targetKind === 'library');
    updateWorkerControls();
    actionRegistry.updateDashboardShell(state.status || {});
  }

  async function chooseTarget() {
    if (state.targetKind === 'library' || !pickerAvailable()) return;
    setHidden(el.error, true);
    try {
      let path = null;
      if (state.targetKind === 'folder') {
        path = await window.feedBackDesktop.pickDirectory();
      } else {
        path = await window.feedBackDesktop.pickFile([
          { name: 'Feedpak packages', extensions: ['feedpak', 'sloppak'] },
        ]);
      }
      if (path) state.targetPaths[state.targetKind] = path;
      updateTargetControls();
    } catch (error) {
      text(el.error, error?.message || 'The system picker could not be opened.');
      setHidden(el.error, false);
    }
  }

  function schedulePoll(delay) {
    clearTimeout(state.pollTimer);
    if (!state.active) return;
    state.pollTimer = setTimeout(refreshStatus, delay);
  }

  async function refreshStatus({ refreshCompletedResults = false } = {}) {
    if (!state.active) return;
    const requestId = ++statusRequest;
    const wasRunning = !!state.status?.running;
    const wasBatchRunning = !!state.status?.batch?.running;
    try {
      const params = new URLSearchParams({
        review_difficulty_scope: state.reviewDifficultyScope,
      });
      const status = await request(`/status?${params}`);
      if (!state.active || requestId !== statusRequest) return;
      actionRegistry.renderStatus(status);
      const completed = (wasRunning && !status.running)
        || (wasBatchRunning && !status.batch?.running);
      if (completed || (refreshCompletedResults && !status.running && !status.batch?.running)) {
        await Promise.all([actionRegistry.loadResults(), actionRegistry.loadRules()]);
      }
      if (status.running || status.batch?.running) {
        schedulePoll(status.batch?.running ? 400 : 750);
      }
    } catch (error) {
      if (isAbortError(error) || !state.active || requestId !== statusRequest) return;
      actionRegistry.showStatusError(el.error, error);
      schedulePoll(3000);
    }
  }

  async function startScan(force) {
    setHidden(el.error, true);
    if (state.targetKind !== 'library' && !selectedPath()) {
      await chooseTarget();
      if (!selectedPath()) return;
    }
    const target = {
      scope: state.targetKind,
      deep_audio: !!el.deepAudio.checked,
    };
    if (state.workerMode === 'custom') {
      const maximum = Number(el.workerLimit.value);
      if (!Number.isInteger(maximum) || maximum < 1) {
        text(el.error, 'Maximum validation workers must be a positive whole number.');
        setHidden(el.error, false);
        return;
      }
      state.workerLimit = maximum;
      target.max_workers = maximum;
      saveWorkerSettings();
    }
    if (state.targetKind !== 'library') target.path = selectedPath();
    try {
      actionRegistry.resetReviewDifficultyScope({ refresh: false });
      const started = await request(`/scan?force=${force ? 'true' : 'false'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(target),
      });
      if (started?.status && typeof started.status === 'object') {
        actionRegistry.renderStatus(started.status);
      }
      // A small synthetic library can finish between POST /scan and the first
      // status read. In that case there is no observable running -> complete
      // transition, so explicitly refresh the completed result set once.
      await refreshStatus({ refreshCompletedResults: true });
    } catch (error) {
      text(el.error, error.message);
      setHidden(el.error, false);
    }
  }

  async function cancelScan() {
    try {
      await request('/cancel', { method: 'POST' });
      await refreshStatus();
    } catch (error) {
      text(el.error, error.message);
      setHidden(el.error, false);
    }
  }


  return {
    cancelScan,
    chooseTarget,
    loadWorkerSettings,
    refreshStatus,
    saveWorkerSettings,
    schedulePoll,
    startScan,
    updateTargetControls,
    updateWorkerControls,
  };
}
