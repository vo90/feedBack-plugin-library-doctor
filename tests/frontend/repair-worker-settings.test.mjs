import assert from 'node:assert/strict';
import test from 'node:test';

import { createRepairWorkerSettings } from '../../src/repair-worker-settings.js';


function fixture() {
  function element(value = '') {
    const listeners = new Map();
    return {
      value, disabled: false, hidden: false, textContent: '', isConnected: true,
      addEventListener(name, listener) { listeners.set(name, listener); },
      dispatch(name) { listeners.get(name)?.(); },
    };
  }
  const nodes = {
    '#lh-repair-worker-mode': element('automatic'),
    '#lh-repair-worker-limit': element('2'),
    '#lh-repair-worker-limit-wrap': element(),
    '#lh-repair-worker-summary': element(),
  };
  const root = { querySelector: (selector) => nodes[selector] };
  const document = { getElementById: () => root };
  const saved = new Map();
  const localStorage = {
    getItem: (key) => saved.get(key) ?? null,
    setItem: (key, value) => saved.set(key, value),
  };
  const state = {
    batch: null,
    repairWorkerMode: 'automatic',
    repairWorkerLimit: 2,
  };
  const settings = createRepairWorkerSettings({
    document,
    localStorage,
    modeKey: 'repair-mode',
    limitKey: 'repair-limit',
    number: (value) => String(value),
    state,
  });
  return { nodes, saved, settings, state };
}


test('automatic mode omits a custom ceiling and custom mode persists one', () => {
  const { nodes, saved, settings, state } = fixture();
  settings.bind();
  assert.deepEqual(settings.applyRequest('plan-1'), { batch_plan_id: 'plan-1' });

  const mode = nodes['#lh-repair-worker-mode'];
  const limit = nodes['#lh-repair-worker-limit'];
  mode.value = 'custom';
  mode.dispatch('change');
  limit.value = '4';
  limit.dispatch('change');

  assert.equal(state.repairWorkerMode, 'custom');
  assert.equal(state.repairWorkerLimit, 4);
  assert.deepEqual(settings.applyRequest('plan-2'), {
    batch_plan_id: 'plan-2',
    max_workers: 4,
  });
  assert.equal(saved.get('repair-mode'), 'custom');
  assert.equal(saved.get('repair-limit'), '4');
});


test('live policy reports the safe selection and disables changes', () => {
  const { nodes, settings } = fixture();
  settings.bind();
  const batch = {
    mode: 'apply',
    running: true,
    prepare_in_flight: 2,
    prepared_ready: 1,
    commit_in_flight: 1,
    worker_policy: { selected_workers: 2, manual_max_workers: 4 },
  };
  settings.render(batch);

  assert.equal(
    nodes['#lh-repair-worker-summary'].textContent,
    '2 workers, safe maximum 4',
  );
  assert.equal(nodes['#lh-repair-worker-mode'].disabled, true);
  assert.equal(settings.progressCopy(batch), ' | 2 workers, 2 preparing, saving 1');
});
