import assert from 'node:assert/strict';
import test from 'node:test';
import { JSDOM } from 'jsdom';
import { createSourceRecoveryBatchTool } from '../../src/source-recovery-batch-tool.js';

const idle = { phase: 'idle', running: false, message: 'Ready' };
const entry = (i = 0) => ({ package: `Songs/Song ${i}.feedpak`, title: `Song ${i}`, artist: 'Artist',
  source_name: `Original ${i}.psarc`, source_path: `C:/Originals/Original ${i}.psarc`, status: 'eligible',
  change_count: 2, excluded_count: 1, member_count: 1 });
const preview = (packages = [entry()]) => ({ ...idle, phase: 'ready', preview: {
  batch_plan_id: 'reviewed-plan', source_folder: 'C:/Originals', scope_package_count: packages.length,
  eligible_count: packages.filter((p) => p.status === 'eligible').length,
  blocked_count: packages.filter((p) => p.status !== 'eligible').length, unchanged_count: 0,
  change_count: packages.length * 2, packages, source_errors: [],
} });
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { resolve, promise }; };

function harness(onRequest, desktop) {
  const dom = new JSDOM('<main></main>');
  const { document } = dom.window;
  dom.window.feedBackDesktop = desktop;
  const region = document.querySelector('main');
  const calls = [];
  const timers = new Map();
  let nextTimer = 0;
  let current = true;
  const make = (tag, cls = '', value = '') => {
    const node = document.createElement(tag); node.className = cls; node.textContent = value; return node;
  };
  const tool = createSourceRecoveryBatchTool({ document, make, isCurrent: () => current,
    actions: { renderRepairResult() {}, renderRepairFailure() {}, async refreshStatus() {}, async loadResults() {} },
    request: async (path, options) => {
      const call = { path, method: options?.method || 'GET', body: options?.body ? JSON.parse(options.body) : undefined };
      calls.push(call);
      return onRequest?.(call, calls) ?? idle;
    },
    schedule: (fn) => { const id = ++nextTimer; timers.set(id, fn); return id; },
    unschedule: (id) => timers.delete(id),
  });
  tool.mount(region); tool.resume();
  const find = (label) => [...region.querySelectorAll('button')].find((b) => b.textContent === label);
  return { dom, document, region, tool, calls, timers, find,
    setCurrent(value) { current = value; },
    cleanup() { tool.pause(); dom.window.close(); },
    async tick() { const [id, fn] = [...timers][0] || []; if (fn) { timers.delete(id); fn(); await settle(); } },
  };
}

test('folder picking is read-only; 301-song preview is paginated, searchable and includes unavailable sources', async (t) => {
  const packages = Array.from({ length: 301 }, (_, i) => entry(i));
  packages[0] = { ...packages[0], status: 'blocked', code: 'source_match_ambiguous', reason: 'Two originals match' };
  const ready = preview(packages);
  ready.preview.source_errors = [{ path: 'Broken.psarc', message: 'Archive could not be read' }];
  const h = harness((call) => call.path.endsWith('/preview') ? ready : idle, { async pickDirectory() { return 'C:/Originals'; } });
  t.after(h.cleanup);
  await settle();
  h.find('Choose PSARC folder').click(); await settle();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].method, 'GET');
  assert.equal(h.document.querySelector('input').value, 'C:/Originals');
  h.find('Preview source recovery').click(); await settle();
  assert.deepEqual(h.calls[1].body, { source_folder: 'C:/Originals' });
  assert.equal(h.region.querySelectorAll('li').length, 25);
  assert.match(h.region.textContent, /Two originals match/);
  assert.match(h.region.textContent, /Broken.psarc: Archive could not be read/);
  assert.match(h.region.textContent, /Page 1 of 13/);
  h.find('Next songs').click();
  assert.match(h.region.textContent, /Song 25/);
  const search = h.region.querySelector('input[type="search"]');
  search.value = 'Song 300'; search.dispatchEvent(new h.dom.window.Event('input'));
  assert.equal(h.region.querySelectorAll('li').length, 1);
  assert.match(h.region.textContent, /Original 300.psarc/);
  assert.equal(h.calls.filter((call) => call.path.endsWith('/apply')).length, 0);
});

test('per-song details show exact before/after changes and their difficulty copies in pages', async (t) => {
  const changes = Array.from({ length: 51 }, (_, i) => ({ member_path: 'lead.json', path: ['phrases', i], time: i,
    string: 2, fret: 9, before: { bn: 2 }, after: { bnv: [{ t: 0, v: 0 }, { t: .2, v: 2 }] }, adjustments: ['interpolated_start'] }));
  const h = harness((call) => call.path.includes('/details?') ? { change_count: 51, excluded_count: 1, changes,
    matches: [{ member_path: 'lead.json', source_member: 'original_lead.sng' }], excluded: [{ member_path: 'lead.json', message: 'An edited curve stays unchanged' }],
  } : preview());
  t.after(h.cleanup); await settle();
  h.find('Review song details').click(); await settle();
  assert.match(h.calls[1].path, /package=Songs%2FSong%200.feedpak/);
  assert.equal(h.region.querySelectorAll('.lh-source-change').length, 50);
  assert.match(h.region.textContent, /Before:.*After:/s);
  assert.match(h.region.textContent, /lead.json matches original_lead.sng/);
  assert.match(h.region.textContent, /difficulty copy/);
  assert.match(h.region.textContent, /An edited curve stays unchanged/);
  h.find('Next changes').click();
  assert.equal(h.region.querySelectorAll('.lh-source-change').length, 1);
});

test('Apply uses only the reviewed batch id, and changing folder invalidates the visible Apply action', async (t) => {
  const h = harness((call) => call.path.endsWith('/apply') ? { ...idle, phase: 'applying', running: true } : preview());
  t.after(h.cleanup); await settle();
  const input = h.document.querySelector('input');
  input.value = 'C:/Different'; input.dispatchEvent(new h.dom.window.Event('input'));
  assert.equal(h.find('Apply reviewed recovery to 1 song'), undefined);
  assert.match(h.region.textContent, /Preview again before applying/);
  input.value = 'C:/Originals'; input.dispatchEvent(new h.dom.window.Event('input'));
  h.find('Apply reviewed recovery to 1 song').click(); await settle();
  assert.equal(h.calls[1].path, '/source-recovery/batch/apply');
  assert.deepEqual(h.calls[1].body, { batch_plan_id: 'reviewed-plan' });
  assert.ok(h.find('Stop after current song'));
});

test('a successful preview adopts the server-normalized folder without requiring a second preview', async (t) => {
  const ready = preview(); ready.preview.source_folder = 'C:\\Originals';
  const h = harness((call) => call.path.endsWith('/preview') ? ready : idle);
  t.after(h.cleanup); await settle();
  const input = h.document.querySelector('input');
  input.value = 'C:/Originals/'; input.dispatchEvent(new h.dom.window.Event('input'));
  h.find('Preview source recovery').click(); await settle();
  assert.deepEqual(h.calls.at(-1).body, { source_folder: 'C:/Originals/' });
  assert.equal(input.value, 'C:\\Originals');
  assert.ok(h.find('Apply reviewed recovery to 1 song'));
  assert.doesNotMatch(h.region.textContent, /Preview again before applying/);
});

test('a polled preview normalizes its submitted folder once and never overwrites later folder edits', async (t) => {
  const ready = preview(); ready.preview.source_folder = 'C:\\Originals';
  let remote = idle;
  const h = harness((call) => call.path.endsWith('/preview')
    ? { ...idle, mode: 'preview', phase: 'indexing', running: true } : remote);
  t.after(h.cleanup); await settle();
  const input = h.document.querySelector('input');
  input.value = 'C:/Originals/'; input.dispatchEvent(new h.dom.window.Event('input'));
  h.find('Preview source recovery').click(); await settle();
  assert.equal(h.find('Apply reviewed recovery to 1 song'), undefined);
  remote = ready; await h.tick();
  assert.equal(input.value, 'C:\\Originals');
  assert.ok(h.find('Apply reviewed recovery to 1 song'));
  input.value = 'C:/Other originals'; input.dispatchEvent(new h.dom.window.Event('input'));
  h.find('Refresh batch status').click(); await settle();
  assert.equal(input.value, 'C:/Other originals');
  assert.equal(h.find('Apply reviewed recovery to 1 song'), undefined);
});

test('source index issues name unreadable files and skipped links while bounding displayed details', async (t) => {
  const ready = preview();
  ready.preview.source_errors = Array.from({ length: 1000 }, (_, i) => ({ relative_path: `Broken ${i}.psarc`, message: 'Unreadable archive' }));
  ready.preview.source_index = { complete: false, error_count: 1003, errors_truncated: true,
    skipped_links: ['Artist junction'], skipped_link_count: 1 };
  const h = harness(() => ready); t.after(h.cleanup); await settle();
  assert.match(h.region.textContent, /Broken 0.psarc: Unreadable archive/);
  assert.match(h.region.textContent, /Broken 24.psarc: Unreadable archive/);
  assert.doesNotMatch(h.region.textContent, /Broken 25.psarc/);
  assert.ok(h.region.textContent.includes(`Showing 25 of ${(1003).toLocaleString()} read errors`));
  assert.match(h.region.textContent, /Skipped linked path: Artist junction/);
});

test('a skipped source link remains actionable when the index has no read errors', async (t) => {
  const ready = preview();
  ready.preview.source_index = { complete: false, error_count: 0, skipped_links: ['Originals junction'], skipped_link_count: 1 };
  const h = harness(() => ready); t.after(h.cleanup); await settle();
  assert.match(h.region.textContent, /Source folder issues: 0 read errors; 1 skipped links/);
  assert.match(h.region.textContent, /Skipped linked path: Originals junction/);
  assert.match(h.region.textContent, /Resolve these paths and preview again/);
});

test('polling stops on leave, resumes on return, and cancel never exposes a cancelled preview for Apply', async (t) => {
  let remote = { ...idle, phase: 'previewing', running: true, done: 3, total: 10, current: 'Song.feedpak' };
  const h = harness((call) => call.path.endsWith('/cancel') ? { ...idle, phase: 'cancelled', preview: null } : remote);
  t.after(h.cleanup); await settle();
  assert.equal(h.timers.size, 1);
  h.tool.pause(); h.setCurrent(false);
  assert.equal(h.timers.size, 0);
  const before = h.calls.length; await h.tick(); assert.equal(h.calls.length, before);
  h.setCurrent(true); h.tool.resume(); await settle();
  assert.equal(h.calls.length, before + 1);
  assert.match(h.region.textContent, /Progress: 3 of 10/);
  h.find('Stop after current song').click(); await settle();
  assert.equal(h.calls.at(-1).path, '/source-recovery/batch/cancel');
  assert.equal(h.timers.size, 0);
  assert.equal(h.find('Apply reviewed recovery to 1 song'), undefined);
});

test('Undo requires its own preview and sends only its reviewed undo id', async (t) => {
  const outcome = { ...entry(), outcome: 'success', backup_id: 'original-backup' };
  const result = { mode: 'apply', outcomes: [outcome, { ...entry(1), outcome: 'blocked', message: 'Package changed after preview' }] };
  const h = harness((call) => call.path.endsWith('/undo/preview') ? { ...idle, phase: 'undo_ready', result,
    undo_preview: { undo_plan_id: 'undo-checked', eligible_count: 1, blocked_count: 1,
      packages: [{ ...outcome, status: 'eligible' }, { ...entry(1), status: 'blocked', reason: 'Backup is unavailable' }] },
  } : call.path.endsWith('/undo/apply') ? { ...idle, phase: 'undoing', running: true }
    : { ...idle, phase: 'completed', result });
  t.after(h.cleanup); await settle();
  assert.match(h.region.textContent, /Package changed after preview/);
  assert.equal(h.find('Restore originals for 1 song'), undefined);
  h.find('Preview batch Undo').click(); await settle();
  assert.match(h.region.textContent, /Backup is unavailable/);
  h.find('Restore originals for 1 song').click(); await settle();
  assert.deepEqual(h.calls.at(-1).body, { undo_plan_id: 'undo-checked' });
});

test('stopped Undo attempts retain the action for successful repairs even when the latest attempt has no outcomes', async (t) => {
  for (const phase of ['undo_cancelled', 'failed', 'interrupted']) {
    await t.test(phase, async (child) => {
      const h = harness(() => ({ ...idle, phase, mode: 'undo',
        result: { mode: 'undo', outcomes: [], message: 'Undo stopped before restoring a song' },
        last_result: { mode: 'apply', outcomes: [{ ...entry(), outcome: 'success', backup_id: 'original-backup' }] },
      }));
      child.after(h.cleanup); await settle();
      assert.ok(h.find('Preview batch Undo'));
      assert.match(h.region.textContent, /Undo stopped before restoring a song/);
      const card = [...h.region.querySelectorAll('.lh-batch-card')].find((node) => node.textContent.includes('Source repairs remaining to undo'));
      assert.ok(card);
      assert.match(card.textContent, /Song 0/);
      assert.equal(h.calls.filter((call) => call.method === 'POST').length, 0);
    });
  }
});

test('partial Undo lists only remaining repairs for another preview and fully restored results offer no Undo', async (t) => {
  const restored = { ...entry(), outcome: 'restored', backup_id: 'backup-0' };
  const remaining = { ...entry(1), outcome: 'success', backup_id: 'backup-1' };
  let remote = { ...idle, phase: 'undo_cancelled', mode: 'undo',
    result: { mode: 'undo', outcomes: [restored] },
    last_result: { mode: 'apply', outcomes: [restored, remaining] },
  };
  const h = harness(() => remote); t.after(h.cleanup); await settle();
  const card = [...h.region.querySelectorAll('.lh-batch-card')].find((node) => node.textContent.includes('Source repairs remaining to undo'));
  assert.equal(card.querySelectorAll('li').length, 1);
  assert.match(card.textContent, /Song 1/);
  assert.doesNotMatch(card.textContent, /Song 0/);
  assert.equal([...h.region.querySelectorAll('button')].filter((button) => button.textContent === 'Preview batch Undo').length, 1);
  remote = { ...remote, phase: 'undo_completed',
    result: { mode: 'undo', outcomes: [restored, { ...remaining, outcome: 'restored' }] },
    last_result: { mode: 'apply', outcomes: [restored, { ...remaining, outcome: 'restored' }] },
  };
  h.find('Refresh batch status').click(); await settle();
  assert.equal(h.find('Preview batch Undo'), undefined);
  assert.match(h.region.textContent, /Batch Undo results/);
});

test('a late initial status response cannot overwrite a newer explicit preview', async (t) => {
  const initial = deferred();
  const h = harness((call, calls) => calls.length === 1 ? initial.promise : preview());
  t.after(h.cleanup);
  const input = h.document.querySelector('input'); input.value = 'C:/Originals'; input.dispatchEvent(new h.dom.window.Event('input'));
  h.find('Preview source recovery').click(); await settle();
  assert.ok(h.find('Apply reviewed recovery to 1 song'));
  initial.resolve(idle); await settle();
  assert.ok(h.find('Apply reviewed recovery to 1 song'));
});

test('leaving during a started request discards its response and return resynchronizes after it settles', async (t) => {
  const started = deferred();
  let remote = idle;
  const h = harness((call) => call.path.endsWith('/preview') ? started.promise : remote);
  t.after(h.cleanup); await settle();
  const input = h.document.querySelector('input'); input.value = 'C:/Originals'; input.dispatchEvent(new h.dom.window.Event('input'));
  h.find('Preview source recovery').click();
  h.tool.pause(); h.setCurrent(false); h.setCurrent(true); h.tool.resume();
  remote = preview(); started.resolve({ ...idle, phase: 'previewing', running: true }); await settle(); await settle();
  assert.ok(h.find('Apply reviewed recovery to 1 song'));
  assert.equal(h.timers.size, 0);
});

test('missing-source rows retain an individual original picker, while late details never reappear after leave', async (t) => {
  const response = deferred();
  const h = harness((call) => call.path.includes('/details?') ? response.promise
    : preview([{ ...entry(), status: 'blocked', code: 'source_match_missing', reason: 'No verified original matched' }]));
  t.after(h.cleanup); await settle();
  assert.ok(h.find('Choose an original for this song'));
  h.find('Choose an original for this song').click();
  assert.match(h.region.textContent, /Original PSARC path/);
  h.find('Review song details').click();
  h.tool.pause(); h.setCurrent(false);
  response.resolve({ changes: [], change_count: 0, source_name: 'Late.psarc' }); await settle();
  assert.ok(!h.region.textContent.includes('Late.psarc'));
});
