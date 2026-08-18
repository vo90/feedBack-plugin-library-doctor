import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';

import { createFormatters } from '../../src/formatters.js';
import {
  ROOT,
  deferred,
  jsonResponse,
  launchLibraryDoctor,
  waitFor,
} from './helpers/library-doctor-app.mjs';

const auditedStates = JSON.parse(
  await fs.readFile(path.join(ROOT, 'tests', 'fixtures', 'phase1_browser_states.json'), 'utf8'),
);

for (const stateName of ['first_run', 'cached_complete', 'partial', 'stale']) {
  test(`renders the ${stateName} dashboard contract`, async (t) => {
    const fixture = auditedStates[stateName];
    const app = await launchLibraryDoctor({ status: fixture.status });
    t.after(() => app.close());

    const health = app.document.querySelector('#lh-health-workspace');
    assert.equal(health.dataset.viewState, fixture.expected.view);
    assert.equal(app.document.querySelector('#lh-scan-options').open, fixture.expected.scan_options_open);
    assert.equal(app.document.querySelector('#lh-results-section').hidden, !fixture.expected.results_visible);
  });
}

test('first run presents one safe primary action without showing empty result chrome', async (t) => {
  const app = await launchLibraryDoctor({ status: auditedStates.first_run.status });
  t.after(() => app.close());

  assert.equal(app.document.querySelector('#lh-guidance-title').textContent, 'Check your library');
  assert.equal(app.document.querySelector('#lh-scan').textContent, 'Scan my library');
  assert.equal(app.document.querySelector('#lh-results-section').hidden, true);
  assert.match(app.document.querySelector('#lh-status').textContent, /has not scanned/i);
});

test('external scan results expose the same repair controls', async (t) => {
  const target = {
    kind: 'folder',
    label: 'staging',
  };
  const app = await launchLibraryDoctor({
    status: {
      stage: 'idle',
      running: false,
      scan_current: true,
      target,
      summary: { total: 1, errors: 1, warnings: 0, reviews: 0 },
      last_scan: { complete: true, target },
    },
    repairs: {
      schema: 'library_doctor.repair_catalog.v1',
      combined: null,
      items: [{
        rule_code: 'chart.exact-duplicate-note',
        safety: 'safe_automatic',
        title: 'Remove an exact duplicate note',
        change_kind: 'remove_redundant',
        item_name: 'note',
      }],
    },
    results: {
      total: 1,
      items: [{
        package: 'Song.feedpak',
        title: 'External Song',
        artist: 'Test Artist',
        status: 'error',
        counts: { error: 1, warning: 0, info: 0 },
        features: {
          preview_declared: true,
          repair_scan_current: true,
          repair_eligibility: {
            'chart.exact-duplicate-note': { status: 'automatic' },
          },
        },
        findings: [{
          code: 'chart.exact-duplicate-note',
          severity: 'error',
          category: 'validation',
          message: 'An exact duplicate note was found.',
          affected_count: 1,
        }],
      }],
    },
  });
  t.after(() => app.close());

  assert.notEqual(app.document.querySelector('#lh-results .lh-repair-action'), null);
  assert.equal(app.document.querySelector('#lh-scan-warning').hidden, true);
  assert.match(app.document.querySelector('#lh-batch-copy').textContent, /review every deterministic safe repair/i);
  assert.equal(app.document.querySelector('#lh-batch-review').disabled, false);
  assert.equal(app.document.querySelector('#lh-batch-panel').open, false);
  assert.notEqual(
    app.document.querySelector('#lh-batch-section').compareDocumentPosition(
      app.document.querySelector('#lh-results-section'),
    ) & app.window.Node.DOCUMENT_POSITION_FOLLOWING,
    0,
  );
});

test('per-song details keep the combined safe action first and sort findings by severity', async (t) => {
  const target = { kind: 'folder', label: 'staging' };
  const repairDefinitions = [
    ['review-note', 'Review metadata'],
    ['fix-warning', 'Fix a compatibility warning'],
    ['fix-error', 'Fix a validation error'],
  ].map(([rule_code, title]) => ({
    rule_code,
    title,
    safety: 'safe_automatic',
    change_kind: 'replace_value',
    item_name: 'value',
  }));
  const app = await launchLibraryDoctor({
    status: {
      stage: 'idle',
      running: false,
      scan_current: true,
      target,
      summary: { total: 1, errors: 1, warnings: 1, reviews: 1 },
      last_scan: { complete: true, target },
    },
    repairs: {
      schema: 'library_doctor.repair_catalog.v1',
      combined: { title: 'Fix all safe issues' },
      items: repairDefinitions,
    },
    results: {
      total: 1,
      items: [{
        package: 'Song.feedpak',
        title: 'Mixed Severity Song',
        artist: 'Test Artist',
        features: { preview_declared: true, repair_scan_current: true },
        findings: [
          { code: 'review-note', severity: 'info', category: 'authoring_review', message: 'Review this.' },
          { code: 'fix-warning', severity: 'warning', category: 'feedback_compatibility', message: 'Warning.' },
          { code: 'fix-error', severity: 'error', category: 'validation', message: 'Error.' },
        ],
      }],
    },
  });
  t.after(() => app.close());

  const body = app.document.querySelector('.lh-package-body');
  const combined = body.querySelector('.lh-all-safe');
  const findingList = body.querySelector('.lh-finding-list');
  assert.notEqual(combined, null);
  assert.notEqual(findingList, null);
  assert.notEqual(
    combined.compareDocumentPosition(findingList) & app.window.Node.DOCUMENT_POSITION_FOLLOWING,
    0,
  );
  assert.deepEqual(
    [...findingList.querySelectorAll(':scope > .lh-finding')].map((node) => node.dataset.severity),
    ['error', 'warning', 'info'],
  );
});

test('empty-key change copy describes omission without claiming a musical deletion', () => {
  const formatters = createFormatters({ number: (value) => String(value) });
  const change = {
    change_kind: 'omit_empty',
    change_count: 2,
    item_name: 'phrase-ladder key',
  };

  assert.equal(
    formatters.plannedRepairChange(change),
    'omit 2 empty optional phrase-ladder keys',
  );
  assert.equal(
    formatters.completedRepairChange(change),
    'Omitted 2 empty optional phrase-ladder keys without deleting any musical event',
  );
  assert.doesNotMatch(formatters.plannedRepairChange(change), /remove|musical position/i);
});

test('grouped empty-key findings explain the non-musical omission and expose one safe action', async (t) => {
  const code = 'chart.empty-phrases-key';
  const definition = {
    rule_code: code,
    action_kind: 'omit_empty_phrases_key',
    source_kind: 'arrangement',
    safety: 'safe_automatic',
    title: 'Omit empty phrase ladder keys',
    change_kind: 'omit_empty',
    item_name: 'phrase-ladder key',
  };
  const target = { kind: 'folder', label: 'staging' };
  const app = await launchLibraryDoctor({
    status: {
      stage: 'idle',
      running: false,
      scan_current: true,
      target,
      summary: { total: 1, errors: 0, warnings: 1, reviews: 0 },
      last_scan: { complete: true, target },
    },
    repairs: {
      schema: 'library_doctor.repair_catalog.v1',
      combined: null,
      items: [definition],
    },
    results: {
      total: 1,
      items: [{
        package: 'Song.feedpak',
        title: 'Empty Ladders',
        artist: 'Test Artist',
        counts: { error: 0, warning: 2, info: 0 },
        features: {
          preview_declared: true,
          repair_scan_current: true,
          repair_eligibility: { [code]: { status: 'automatic' } },
        },
        findings: [
          {
            code,
            severity: 'warning',
            category: 'validation',
            message: 'The optional phrase ladder is explicitly empty.',
            affected_count: 1,
            arrangement_id: 'lead',
            location: 'lead.json:phrases',
            rule: { title: definition.title },
          },
          {
            code,
            severity: 'warning',
            category: 'validation',
            message: 'The optional phrase ladder is explicitly empty.',
            affected_count: 1,
            arrangement_id: 'rhythm',
            location: 'rhythm.json:phrases',
            rule: { title: definition.title },
          },
        ],
      }],
    },
  });
  t.after(() => app.close());

  const group = app.document.querySelector('.lh-finding-repair-group');
  assert.notEqual(group, null);
  assert.match(group.textContent, /2 optional phrase-ladder keys store an explicit empty array/i);
  assert.match(group.textContent, /does not delete a musical event or position/i);
  assert.equal(group.querySelectorAll('.lh-repair-action').length, 1);
});

test('empty-key preview, apply failure, success, and Undo use accurate omission copy', async (t) => {
  const code = 'chart.empty-phrases-key';
  const definition = {
    rule_code: code,
    action_kind: 'omit_empty_phrases_key',
    source_kind: 'arrangement',
    safety: 'safe_automatic',
    title: 'Omit the empty phrase ladder',
    change_kind: 'omit_empty',
    item_name: 'phrase-ladder key',
  };
  const report = {
    package: 'EmptyLadders.feedpak',
    title: 'Empty Ladders',
    artist: 'Test Artist',
    counts: { error: 0, warning: 1, info: 0 },
    features: {
      preview_declared: true,
      repair_scan_current: true,
      repair_eligibility: { [code]: { status: 'automatic' } },
    },
    findings: [{
      code,
      severity: 'warning',
      category: 'validation',
      message: 'The optional phrase ladder is explicitly empty.',
      affected_count: 2,
      arrangement_id: 'lead',
      location: 'lead.json:phrases',
      rule: { title: definition.title },
    }],
  };
  const firstApply = deferred();
  let applyAttempts = 0;
  const target = { kind: 'folder', label: 'staging' };
  const app = await launchLibraryDoctor({
    status: {
      stage: 'idle',
      running: false,
      scan_current: true,
      target,
      summary: { total: 1, errors: 0, warnings: 1, reviews: 0 },
      last_scan: { complete: true, target },
    },
    repairs: {
      schema: 'library_doctor.repair_catalog.v1',
      combined: null,
      items: [definition],
    },
    results: { total: 1, items: [report] },
    route(request) {
      if (request.key === '/api/plugins/library_doctor/repair/preview') {
        return jsonResponse({
          available: true,
          plan_id: 'omit-plan-1',
          title: definition.title,
          description: 'Only exact root keys whose values are empty arrays are omitted.',
          change_kind: 'omit_empty',
          change_count: 2,
          removed_count: 0,
          musical_positions: 0,
          member_count: 2,
          arrays_affected: 2,
          item_name: definition.item_name,
          player_result: 'Both arrangements still have no phrase ladder.',
          user_value: 'The optional data now uses the format-defined omission.',
          file_handling: { summary: 'A validated candidate and Undo recovery protect the change.' },
        });
      }
      if (request.key === '/api/plugins/library_doctor/repair/apply') {
        applyAttempts += 1;
        if (applyAttempts === 1) return firstApply.promise;
        return jsonResponse({
          applied: true,
          outcome: 'success',
          backup_id: '20260815-120000-abcdef123456',
          undo_available: true,
          rule_code: code,
          change_kind: 'omit_empty',
          change_count: 2,
          removed_count: 0,
          musical_positions: 0,
          item_name: definition.item_name,
          player_result: 'Both arrangements still have no phrase ladder.',
          user_value: 'The optional data now uses the format-defined omission.',
          file_handling: { summary: 'The original song data remains available to Undo.' },
          report: { title: report.title, artist: report.artist },
        });
      }
      if (request.key === '/api/plugins/library_doctor/repair/restore') {
        return jsonResponse({
          action: 'restore',
          outcome: 'restored',
          package: report.package,
          title: report.title,
          artist: report.artist,
          backup_id: '20260815-120000-abcdef123456',
          change_kind: 'omit_empty',
          change_count: 2,
          removed_count: 0,
          musical_positions: 0,
          item_name: definition.item_name,
          file_handling: {
            summary: 'The exact original song data was restored.',
            backup_removed: true,
          },
        });
      }
      return null;
    },
  });
  t.after(() => app.close());

  buttonWithText(app.document, 'Review safe fix').click();
  await waitFor(() => app.document.querySelector('.lh-repair-card'), 'empty-key repair preview');
  const preview = app.document.querySelector('.lh-repair-card');
  assert.match(preview.textContent, /Omit 2 empty optional phrase-ladder keys/i);
  assert.match(preview.textContent, /no musical event or position is removed/i);
  assert.doesNotMatch(preview.textContent, /redundant stored|first authored copy/i);

  buttonWithText(preview, 'Apply safe repair').click();
  await waitFor(
    () => buttonWithText(preview, 'Applying and verifying...'),
    'empty-key applying state',
  );
  firstApply.resolve(jsonResponse({
    detail: {
      code: 'source_changed',
      message: 'The source changed before it could be saved.',
      file_state: 'unchanged',
    },
  }, { status: 409 }));
  await waitFor(
    () => app.document.querySelector('#lh-repair-result')?.dataset.outcome === 'failure',
    'empty-key failure result',
  );
  assert.match(app.document.querySelector('#lh-repair-result').textContent, /Nothing changed/i);
  assert.match(app.document.querySelector('#lh-repair-result').textContent, /existing Feedpak was left unchanged/i);

  buttonWithText(preview, 'Apply safe repair').click();
  await waitFor(
    () => app.document.querySelector('#lh-repair-result')?.dataset.outcome === 'success',
    'empty-key success result',
  );
  const success = app.document.querySelector('#lh-repair-result');
  assert.match(success.textContent, /Omitted 2 empty optional phrase-ladder keys/i);
  assert.match(success.textContent, /without deleting any musical event/i);
  assert.doesNotMatch(success.textContent, /Removed 2 redundant/i);

  buttonWithText(success, 'Undo repair').click();
  const undo = app.document.querySelector('.lh-repair-confirm');
  assert.match(undo.textContent, /restore the original song-data files/i);
  buttonWithText(undo, 'Restore original song data').click();
  await waitFor(
    () => app.document.querySelector('#lh-repair-result')?.dataset.outcome === 'restored',
    'empty-key restored result',
  );
  const restored = app.document.querySelector('#lh-repair-result');
  assert.match(restored.textContent, /Restored 2 original empty optional phrase-ladder keys/i);
  assert.match(restored.textContent, /omission finding is expected to return/i);
});

test('unavailable structural repairs show stable blocker copy and no repair action', async (t) => {
  const toneCode = 'tones.changes-out-of-order';
  const tempoCode = 'timeline.tempos-out-of-order';
  const definitions = [
    {
      rule_code: toneCode,
      action_kind: 'reorder_tone_changes',
      source_kind: 'arrangement',
      safety: 'safe_automatic',
      title: 'Put tone changes in chronological order',
      change_kind: 'reorder',
      item_name: 'tone change',
    },
    {
      rule_code: tempoCode,
      action_kind: 'reorder_tempo_events',
      source_kind: 'timeline',
      safety: 'safe_automatic',
      title: 'Put tempo events in chronological order',
      change_kind: 'reorder',
      item_name: 'tempo event',
    },
  ];
  const target = { kind: 'folder', label: 'staging' };
  const app = await launchLibraryDoctor({
    status: {
      stage: 'idle',
      running: false,
      scan_current: true,
      target,
      summary: { total: 1, errors: 0, warnings: 3, reviews: 0 },
      last_scan: { complete: true, target },
    },
    repairs: {
      schema: 'library_doctor.repair_catalog.v1',
      combined: { title: 'Fix all safe issues' },
      items: definitions,
    },
    results: {
      total: 1,
      items: [{
        package: 'BlockedSources.feedpak',
        title: 'Blocked Sources',
        artist: 'Test Artist',
        counts: { error: 0, warning: 3, info: 0 },
        features: {
          preview_declared: true,
          repair_scan_current: true,
          repair_eligibility: {
            [toneCode]: {
              status: 'unavailable',
              reason_code: 'manifest_tones_require_manual_edit',
            },
            [tempoCode]: {
              status: 'unavailable',
              reason_code: 'jsonc_requires_lossless_writer',
            },
          },
        },
        findings: [
          {
            code: toneCode,
            severity: 'warning',
            category: 'validation',
            message: 'Tone changes are not chronological.',
            arrangement_id: 'lead',
            location: 'manifest.yaml:arrangements[0].tones.changes',
            rule: { title: definitions[0].title },
          },
          {
            code: toneCode,
            severity: 'warning',
            category: 'validation',
            message: 'Tone changes are not chronological.',
            arrangement_id: 'rhythm',
            location: 'manifest.yaml:arrangements[1].tones.changes',
            rule: { title: definitions[0].title },
          },
          {
            code: tempoCode,
            severity: 'warning',
            category: 'validation',
            message: 'Tempo events are not chronological.',
            location: 'timeline.jsonc:tempos[1]',
            rule: { title: definitions[1].title },
          },
        ],
      }],
    },
  });
  t.after(() => app.close());

  assert.match(app.document.querySelector('#lh-results').textContent, /stored in the manifest and require a manual edit/i);
  assert.match(app.document.querySelector('#lh-results').textContent, /JSONC comments.*comment-preserving writer/i);
  assert.equal(app.document.querySelectorAll('#lh-results .lh-repair-action').length, 0);
  assert.equal(app.document.querySelectorAll('#lh-results .lh-all-safe').length, 0);
});

test('batch work opens its panel without hiding results or overriding a user collapse', async (t) => {
  const target = { kind: 'folder', label: 'staging' };
  const app = await launchLibraryDoctor({
    status: {
      stage: 'idle',
      running: false,
      repairing: true,
      scan_current: true,
      target,
      summary: { total: 1, errors: 1, warnings: 0, reviews: 0 },
      last_scan: { complete: true, target },
      batch: {
        phase: 'applying',
        running: true,
        mode: 'apply',
        message: 'Repairing safe issues...',
        total: 1,
        done: 0,
      },
    },
    results: {
      total: 1,
      items: [{
        package: 'Song.feedpak',
        title: 'Visible During Repair',
        artist: 'Test Artist',
        features: { preview_declared: true },
        findings: [{ code: 'broken', severity: 'error', category: 'validation', message: 'Broken.' }],
      }],
    },
  });
  t.after(() => app.close());

  const panel = app.document.querySelector('#lh-batch-panel');
  assert.equal(panel.open, true);
  assert.equal(app.document.querySelector('#lh-results-section').hidden, false);
  assert.equal(app.document.querySelector('#lh-health-workspace').dataset.viewState, 'complete');

  panel.open = false;
  await waitFor(
    () => app.requests.filter(({ key }) => (
      key.startsWith('/api/plugins/library_doctor/status')
    )).length >= 2,
    'batch status poll',
  );
  assert.equal(panel.open, false);
});

test('a new batch preview cannot hide unresolved Undo and Finalize choices', async (t) => {
  const target = { kind: 'folder', label: 'staging' };
  const lastResult = {
    id: 'previous-batch',
    outcome: 'complete',
    completed_count: 1,
    successful_count: 1,
    failed_count: 0,
    skipped_count: 0,
    currently_repaired_count: 1,
    restored_count: 0,
    finalized_count: 0,
    undoable_count: 1,
    preview_cleanup_required_count: 0,
    recovery_summary: 'One Feedpak retains an Undo recovery copy.',
    outcomes: [{
      package: 'Song.feedpak',
      title: 'Song',
      artist: 'Artist',
      outcome: 'success',
      backup_id: 'backup-1',
      undo_available: true,
      change_count: 1,
      removed_count: 1,
      file_state: 'repaired',
    }],
  };
  const app = await launchLibraryDoctor({
    status: {
      stage: 'idle',
      running: false,
      repairing: false,
      scan_current: true,
      target,
      summary: { total: 1, errors: 0, warnings: 0, reviews: 0 },
      last_scan: { complete: true, target },
      batch: {
        phase: 'ready',
        running: false,
        preview: { batch_plan_id: 'new-preview', eligible_count: 0 },
        last_result: lastResult,
      },
    },
  });
  t.after(() => app.close());

  assert.match(app.document.querySelector('#lh-batch-copy').textContent, /resolve the previous batch first/i);
  assert.equal(app.document.querySelector('#lh-batch-review').disabled, true);
  assert.equal(app.document.querySelector('#lh-batch-review').textContent, 'Resolve previous repairs first');
  assert.notEqual(buttonWithText(app.document, 'Review Undo all remaining repairs'), undefined);
  assert.notEqual(buttonWithText(app.document, 'Review Finalize all remaining repairs'), undefined);
});

test('scan live region announces milestones without repeating package-name polling', async (t) => {
  const idle = auditedStates.first_run.status;
  const running = {
    stage: 'scanning',
    running: true,
    repairing: false,
    total: 100,
    done: 1,
    current: 'Artist/First.feedpak',
    summary: { total: 0, errors: 0, warnings: 0, reviews: 0 },
    batch: { phase: 'idle', running: false },
  };
  let currentStatus = idle;
  const app = await launchLibraryDoctor({
    status: idle,
    route(request) {
      if (request.key.startsWith('/api/plugins/library_doctor/scan?')) {
        currentStatus = running;
        return jsonResponse({ status: currentStatus });
      }
      if (request.key === '/api/plugins/library_doctor/status') {
        return jsonResponse(currentStatus);
      }
      return null;
    },
  });
  t.after(() => app.close());
  const live = app.document.querySelector('#lh-scan-live');
  let mutations = 0;
  const observer = new app.window.MutationObserver((records) => { mutations += records.length; });
  observer.observe(live, { childList: true, characterData: true, subtree: true });
  t.after(() => observer.disconnect());

  app.document.querySelector('#lh-scan').click();
  await waitFor(() => live.dataset.announcementKey === 'scan:running:100:0', 'scan milestone announcement');
  assert.match(live.textContent, /1 of 100 packages checked/);
  const milestoneMutations = mutations;

  currentStatus = { ...running, current: 'Artist/Second.feedpak', done: 2 };
  await waitFor(
    () => app.document.querySelector('#lh-status').textContent.includes('Second.feedpak'),
    'next visible package status',
  );
  assert.equal(live.dataset.announcementKey, 'scan:running:100:0');
  assert.equal(live.textContent.includes('Second.feedpak'), false);
  assert.equal(mutations, milestoneMutations);
});

test('a complete result filter updates pressed state and sends the semantic API filter', async (t) => {
  const app = await launchLibraryDoctor({
    status: auditedStates.cached_complete.status,
    results: {
      total: 1,
      items: [{
        package: 'synthetic/contract-song.feedpak',
        title: 'Contract Song',
        artist: 'Synthetic Artist',
        findings: [],
        features: {},
      }],
    },
  });
  t.after(() => app.close());

  const warningFilter = app.document.querySelector('button[data-filter="warnings"]');
  warningFilter.click();
  await waitFor(
    () => app.requests.some(({ key }) => key.includes('/results?') && key.includes('filter=warnings')),
    'warnings result request',
  );

  assert.equal(warningFilter.getAttribute('aria-pressed'), 'true');
  assert.equal(app.document.querySelector('button[data-filter="problems"]').getAttribute('aria-pressed'), 'false');
});

test('stale result responses cannot overwrite a newer filter response', async (t) => {
  const first = deferred();
  const second = deferred();
  let filteredRequests = 0;
  const app = await launchLibraryDoctor({
    status: auditedStates.cached_complete.status,
    route(request) {
      if (!request.key.includes('/results?')) return null;
      if (request.key.includes('filter=warnings')) {
        filteredRequests += 1;
        return first.promise;
      }
      if (request.key.includes('filter=review')) {
        filteredRequests += 1;
        return second.promise;
      }
      return null;
    },
  });
  t.after(() => app.close());

  app.document.querySelector('button[data-filter="warnings"]').click();
  app.document.querySelector('button[data-filter="review"]').click();
  await waitFor(() => filteredRequests === 2, 'two filtered result requests');

  second.resolve(jsonResponse({ total: 1, items: [{
    package: 'synthetic/newer.feedpak', title: 'Newer result', artist: 'Synthetic', findings: [], features: {},
  }] }));
  await waitFor(() => app.document.querySelector('#lh-results').textContent.includes('Newer result'), 'newer result');
  first.resolve(jsonResponse({ total: 1, items: [{
    package: 'synthetic/stale.feedpak', title: 'Stale result', artist: 'Synthetic', findings: [], features: {},
  }] }));
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.match(app.document.querySelector('#lh-results').textContent, /Newer result/);
  assert.doesNotMatch(app.document.querySelector('#lh-results').textContent, /Stale result/);
});

test('workspace switching preserves semantic pressed state and isolates Song Tools', async (t) => {
  const app = await launchLibraryDoctor({
    status: auditedStates.cached_complete.status,
    songs: {
      total: 1,
      songs: [{ filename: 'synthetic/tool-song.feedpak', title: 'Tool Song', artist: 'Synthetic Artist' }],
    },
  });
  t.after(() => app.close());

  const tools = app.document.querySelector('button[data-workspace="tools"]');
  tools.click();
  await waitFor(() => app.document.querySelector('#lh-song-tool-results').textContent.includes('Tool Song'), 'Song Tools library');

  assert.equal(tools.getAttribute('aria-pressed'), 'true');
  assert.equal(app.document.querySelector('button[data-workspace="health"]').getAttribute('aria-pressed'), 'false');
  assert.equal(app.document.querySelector('#lh-health-workspace').hidden, true);
  assert.equal(app.document.querySelector('#lh-song-tools-workspace').hidden, false);
});

test('Song Tools keeps its detail panel outside the result list and manages focus', async (t) => {
  const app = await launchLibraryDoctor({
    status: auditedStates.cached_complete.status,
    songs: {
      total: 1,
      songs: [{ filename: 'synthetic/tool-song.feedpak', title: 'Tool Song', artist: 'Synthetic Artist' }],
    },
  });
  t.after(() => app.close());

  app.document.querySelector('button[data-workspace="tools"]').click();
  await waitFor(() => app.document.querySelector('.lh-song-tool-item'), 'Song Tools item');
  app.document.querySelector('.lh-song-tool-item').click();
  const selection = app.document.querySelector('#lh-song-tool-selection');
  await waitFor(() => app.document.activeElement === selection, 'selected song panel focus');

  assert.equal(selection.hidden, false);
  assert.equal(selection.parentElement.id, 'lh-song-tools-workspace');
  assert.equal(app.document.querySelector('#lh-song-tool-results').contains(selection), false);
  assert.equal(selection.getAttribute('aria-labelledby'), 'lh-song-tool-selection-title');

  app.document.querySelector('.lh-song-tool-item').click();
  await waitFor(() => app.document.activeElement?.classList.contains('lh-song-tool-item'), 'song trigger focus restore');
  assert.equal(selection.hidden, true);
});

function previewToolRoute(request, { mode, startSeconds }) {
  if (request.key.startsWith('/api/plugins/library_doctor/repair/media/tool/status?')) {
    return jsonResponse({
      available: true,
      rule_code: 'media.preview-regenerate',
      current_preview_available: true,
      preview_declared: true,
      title: 'Tool Song',
      artist: 'Synthetic Artist',
    });
  }
  if (request.key === '/api/plugins/library_doctor/repair/preview') {
    return jsonResponse({
      available: true,
      plan_id: 'preview-plan-1',
      title: 'Create a standard audio preview',
      change_kind: 'replace_media',
      item_name: 'audio preview',
      media: {
        creates_preview: false,
        original_duration_seconds: 30,
        candidate_duration_seconds: 30,
        start_seconds: startSeconds,
        max_start_seconds: 200,
        candidate_size: '320 KB',
        original_size: '300 KB',
        selection_reason: mode === 'automatic' ? 'representative section' : 'user-selected position',
      },
    });
  }
  const applyPath = mode === 'automatic'
    ? '/api/plugins/library_doctor/repair/media/automatic'
    : '/api/plugins/library_doctor/repair/apply';
  if (request.key === applyPath) {
    return jsonResponse({
      id: `${mode}-preview-receipt`,
      outcome: 'success',
      package: 'synthetic/tool-song.feedpak',
      title: 'Tool Song',
      artist: 'Synthetic Artist',
      change_kind: 'replace_media',
      change_count: 1,
      item_name: 'audio preview',
      media: {
        creates_preview: false,
        original_duration_seconds: 30,
        candidate_duration_seconds: 30,
        start_seconds: startSeconds,
        selection_reason: mode === 'automatic' ? 'representative section' : 'user-selected position',
      },
      file_handling: { backup_removed: true, backup_cleanup_required: false },
      undo_available: false,
    });
  }
  return null;
}

async function openSyntheticPreviewTool(app) {
  app.document.querySelector('button[data-workspace="tools"]').click();
  await waitFor(() => app.document.querySelector('.lh-song-tool-item'), 'Song Tools item');
  app.document.querySelector('.lh-song-tool-item').click();
  app.document.querySelector('.lh-song-tool-choice').click();
  await waitFor(
    () => [...app.document.querySelectorAll('button')]
      .some((button) => button.textContent === 'Listen and choose a replacement preview'),
    'Preview Creator actions',
  );
}

function buttonWithText(document, label) {
  return [...document.querySelectorAll('button')]
    .find((button) => button.textContent === label);
}

test('Song Tools confirms that a listened-to and chosen preview is now set', async (t) => {
  const app = await launchLibraryDoctor({
    status: auditedStates.cached_complete.status,
    songs: {
      total: 1,
      songs: [{ filename: 'synthetic/tool-song.feedpak', title: 'Tool Song', artist: 'Synthetic Artist' }],
    },
    repairs: {
      items: [{ rule_code: 'media.preview-regenerate', change_kind: 'replace_media' }],
      combined: null,
    },
    route(request) {
      return previewToolRoute(request, { mode: 'chosen', startSeconds: 100 });
    },
  });
  t.after(() => app.close());
  await openSyntheticPreviewTool(app);

  buttonWithText(app.document, 'Listen and choose a replacement preview').click();
  await waitFor(() => buttonWithText(app.document, 'Keep this preview'), 'manual preview candidate');
  buttonWithText(app.document, 'Keep this preview').click();
  buttonWithText(app.document, 'Confirm replacement and finish').click();

  await waitFor(() => app.document.querySelector('.lh-preview-set-confirmation'), 'chosen preview confirmation');
  const confirmation = app.document.querySelector('.lh-preview-set-confirmation');
  assert.match(confirmation.textContent, /Your chosen preview is set/);
  assert.match(confirmation.textContent, /starting at 1m 40s/);
  assert.match(confirmation.textContent, /now the preview FeedBack uses/);
  await waitFor(() => app.document.activeElement === confirmation, 'chosen preview confirmation focus');
});

test('Song Tools confirms that an automatically created preview is now set', async (t) => {
  const app = await launchLibraryDoctor({
    status: auditedStates.cached_complete.status,
    songs: {
      total: 1,
      songs: [{ filename: 'synthetic/tool-song.feedpak', title: 'Tool Song', artist: 'Synthetic Artist' }],
    },
    repairs: {
      items: [{ rule_code: 'media.preview-regenerate', change_kind: 'replace_media' }],
      combined: null,
    },
    route(request) {
      return previewToolRoute(request, { mode: 'automatic', startSeconds: 47 });
    },
  });
  t.after(() => app.close());
  await openSyntheticPreviewTool(app);

  buttonWithText(app.document, 'Create automatically and finish').click();
  buttonWithText(app.document, 'Create preview and finish').click();

  await waitFor(() => app.document.querySelector('.lh-preview-set-confirmation'), 'automatic preview confirmation');
  const confirmation = app.document.querySelector('.lh-preview-set-confirmation');
  assert.match(confirmation.textContent, /Automatic preview created and set/);
  assert.match(confirmation.textContent, /starting at 47s/);
  assert.match(confirmation.textContent, /now the preview FeedBack uses/);
  await waitFor(() => app.document.activeElement === confirmation, 'automatic preview confirmation focus');
});

test('historical repair activity stays collapsed until the user chooses to inspect it', async (t) => {
  const receipt = {
    id: 'synthetic-repair-1',
    outcome: 'success',
    title: 'Contract Song',
    artist: 'Synthetic Artist',
    package: 'synthetic/contract-song.feedpak',
    change_count: 1,
    item_name: 'note',
    change_kind: 'remove_duplicate',
  };
  const app = await launchLibraryDoctor({
    status: auditedStates.repair_receipt.status,
    history: { items: [receipt] },
  });
  t.after(() => app.close());
  await waitFor(() => app.document.querySelector('#lh-repair-result').textContent.includes('Contract Song'), 'repair activity receipt');

  assert.equal(app.document.querySelector('#lh-health-workspace').dataset.viewState, 'complete');
  assert.equal(app.document.querySelector('#lh-activity-section').open, false);
  assert.equal(app.document.querySelector('#lh-activity-status').textContent, '');
});
