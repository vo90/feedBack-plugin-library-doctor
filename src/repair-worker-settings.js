const DEFAULT_LIMIT = 2;
const HARD_LIMIT = 4;

export function createRepairWorkerSettings({
  document,
  localStorage,
  modeKey,
  limitKey,
  number,
  state,
}) {
  let controls = null;

  function elements() {
    if (controls?.mode?.isConnected) return controls;
    const root = document.getElementById('plugin-library_doctor');
    controls = root ? {
      mode: root.querySelector('#lh-repair-worker-mode'),
      limit: root.querySelector('#lh-repair-worker-limit'),
      limitWrap: root.querySelector('#lh-repair-worker-limit-wrap'),
      summary: root.querySelector('#lh-repair-worker-summary'),
    } : null;
    return controls;
  }

  function readLimit(value, fallback = DEFAULT_LIMIT) {
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed >= 1
      ? Math.min(HARD_LIMIT, parsed)
      : fallback;
  }

  function save() {
    try {
      localStorage.setItem(modeKey, state.repairWorkerMode);
      localStorage.setItem(limitKey, String(state.repairWorkerLimit));
    } catch (_error) {
      // Storage can be unavailable in restricted browser profiles.
    }
  }

  function render(batch = state.batch) {
    const el = elements();
    if (!el) return;
    const running = !!batch?.running;
    const custom = state.repairWorkerMode === 'custom';
    const policy = batch?.worker_policy;
    const selected = Number(policy?.selected_workers || 0);
    const safeMaximum = Number(policy?.manual_max_workers || 0);
    el.mode.value = custom ? 'custom' : 'automatic';
    el.mode.disabled = running;
    el.limit.value = String(state.repairWorkerLimit);
    el.limit.disabled = running;
    el.limitWrap.hidden = !custom;
    if (selected > 0) {
      const maximumCopy = safeMaximum > 0 ? `, safe maximum ${number(safeMaximum)}` : '';
      el.summary.textContent = `${number(selected)} worker${selected === 1 ? '' : 's'}${maximumCopy}`;
    } else {
      el.summary.textContent = custom
        ? `Custom maximum: ${number(state.repairWorkerLimit)}`
        : 'Automatic';
    }
  }

  function bind() {
    const el = elements();
    if (!el) return;
    try {
      state.repairWorkerMode = localStorage.getItem(modeKey) === 'custom'
        ? 'custom'
        : 'automatic';
      state.repairWorkerLimit = readLimit(localStorage.getItem(limitKey));
    } catch (_error) {
      state.repairWorkerMode = 'automatic';
      state.repairWorkerLimit = DEFAULT_LIMIT;
    }
    el.mode.addEventListener('change', () => {
      state.repairWorkerMode = el.mode.value === 'custom' ? 'custom' : 'automatic';
      save();
      render();
    });
    el.limit.addEventListener('change', () => {
      state.repairWorkerLimit = readLimit(el.limit.value, state.repairWorkerLimit);
      save();
      render();
    });
    render();
  }

  function applyRequest(batchPlanId) {
    const payload = { batch_plan_id: batchPlanId };
    if (state.repairWorkerMode === 'custom') {
      payload.max_workers = readLimit(state.repairWorkerLimit);
    }
    return payload;
  }

  function progressCopy(batch) {
    if (batch?.mode !== 'apply') return '';
    const selected = Number(batch?.worker_policy?.selected_workers || 0);
    const preparing = Number(batch?.prepare_in_flight || 0);
    const saving = Number(batch?.commit_in_flight || 0);
    if (!selected) return '';
    const preparation = preparing ? `, ${number(preparing)} preparing` : '';
    const commit = saving ? ', saving 1' : '';
    const activity = preparation || commit ? `${preparation}${commit}` : '';
    return ` | ${number(selected)} worker${selected === 1 ? '' : 's'}${activity}`;
  }

  return { applyRequest, bind, progressCopy, render };
}
