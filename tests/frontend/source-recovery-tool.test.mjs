import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { createSourceRecoveryTool } from '../../src/source-recovery-tool.js';

function harness(plan) {
  const dom = new JSDOM('<main></main>');
  const { document } = dom.window;
  const region = document.querySelector('main');
  const calls = [];
  const receipts = [];
  function make(tag, className = '', text = '') {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = text;
    return element;
  }
  const tool = createSourceRecoveryTool({ document, make, isCurrent: () => true,
    actions: { renderRepairResult: (r) => receipts.push(r), renderRepairFailure() {}, async refreshStatus() {}, async loadResults() {} },
    request: async (path, options) => {
      calls.push({ path, body: JSON.parse(options.body) });
      assert.equal(options.headers['Content-Type'], 'application/json');
      return path.endsWith('/preview') ? plan : { applied: true, backup_id: 'saved-original' };
    },
  });
  tool.open(region, { package: 'Song.feedpak', title: 'Song' });
  return { dom, document, region, calls, receipts };
}

const plan = { available: true, chart_validated: true, validation_scope: 'arrangements', plan_id: 'a'.repeat(64),
  change_count: 1, member_count: 1, source_name: 'Original.psarc', blockers: [],
  changes: [{ member_path: 'lead.json', path: ['notes', 0], time: 10, string: 1, fret: 7,
    before: { bn: 2 }, after: { bn: 2, bnv: [{ t: 0, v: 0 }, { t: .4, v: 2 }] }, adjustments: [] }] };

test('source recovery presents exact changes and applies only after explicit reviewed action', async () => {
  const h = harness(plan);
  h.document.querySelector('input').value = 'C:/Original.psarc';
  h.document.querySelector('button').click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(h.calls.length, 1);
  assert.match(h.region.textContent, /Before:.*After:/s);
  assert.match(h.region.textContent, /changed chart documents passed validation/);
  assert.match(h.region.textContent, /Apply rechecks the inputs and validates the full package before saving/);
  assert.doesNotMatch(h.region.textContent, /complete candidate passed validation/);
  const apply = [...h.document.querySelectorAll('button')].find((b) => b.textContent === 'Apply reviewed source recovery');
  apply.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(h.calls[1].path, '/source-recovery/apply');
  assert.equal(h.calls[1].body.plan_id, plan.plan_id);
  assert.equal(h.receipts.length, 1);
  assert.match(h.region.textContent, /Undo is available/);
  h.dom.window.close();
});

test('changing source clears its preview and blocked matches cannot expose Apply', async () => {
  const h = harness({ ...plan, available: false, blockers: [{ member_path: 'lead.json', message: 'Ambiguous source' }] });
  const input = h.document.querySelector('input');
  input.value = 'C:/Original.psarc';
  h.document.querySelector('button').click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(h.region.textContent, /Recovery is blocked/);
  assert.ok(!h.region.textContent.includes('Apply reviewed source recovery'));
  input.dispatchEvent(new h.dom.window.Event('input'));
  assert.ok(!h.region.textContent.includes('Ambiguous source'));
  h.dom.window.close();
});
