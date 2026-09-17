import assert from 'node:assert/strict';
import test from 'node:test';

import { jsonResponse, launchLibraryDoctor, waitFor } from './helpers/library-doctor-app.mjs';

const terminal = 'timeline.terminal-duplicate-beats';
const otherRules = ['chart.exact-duplicate-note', 'chart.empty-anchors'];
const definitions = [terminal, ...otherRules].map((rule_code) => ({
  rule_code,
  title: rule_code === terminal ? 'Remove a verified duplicate beat tail' : rule_code,
  safety: 'safe_automatic',
  change_kind: 'remove_duplicates',
  item_name: rule_code === terminal ? 'terminal beat copy' : 'item',
}));

function reportFor(eligibility, extraCodes = [], { current = true, grouped = false } = {}) {
  const findings = (grouped ? [terminal, terminal] : [terminal]).concat(extraCodes);
  return {
    package: 'Synthetic.feedpak',
    title: 'Synthetic repeated ending',
    artist: 'Test Artist',
    status: 'warning',
    features: {
      preview_declared: true,
      repair_scan_current: current,
      repair_eligibility: eligibility === undefined ? {} : { [terminal]: eligibility },
    },
    findings: findings.map((code, index) => ({
      code,
      severity: 'warning',
      category: 'feedback_compatibility',
      arrangement_id: `arrangement-${index}`,
      affected_count: 1,
      message: 'A repeated ending marker points backwards.',
      rule: {
        title: definitions.find((definition) => definition.rule_code === code).title,
        guidance: 'Review a safe fix.',
      },
    })),
  };
}

async function launchReport(report, route) {
  const target = { kind: 'folder', label: 'synthetic' };
  return launchLibraryDoctor({
    status: {
      stage: 'idle', running: false, scan_current: true, target,
      summary: { total: 1, errors: 0, warnings: 1, reviews: 0 },
      last_scan: { complete: true, target },
    },
    repairs: {
      schema: 'library_doctor.repair_catalog.v1',
      combined: { title: 'Fix all safe issues' },
      items: definitions,
    },
    results: { total: 1, items: [report] },
    route,
  });
}

function safeButtons(root) {
  return [...root.querySelectorAll('button')].filter((button) => /Review.*safe fix/.test(button.textContent));
}

for (const [name, eligibility] of [
  ['missing', undefined], ['null', null], ['empty object', {}],
  ['string', 'automatic'], ['array', [{ status: 'automatic' }]],
  ['unknown status', { status: 'probably_automatic' }],
]) {
  test(`terminal ${name} eligibility never offers a safe fix or inflates combined fixes`, async (t) => {
    const app = await launchReport(reportFor(eligibility, [otherRules[0]]));
    t.after(() => app.close());
    const findings = app.document.querySelectorAll('#lh-results .lh-finding');
    assert.equal(safeButtons(findings[0]).length, 0);
    assert.match(findings[0].textContent, /Safety not verified.*Rescan/i);
    assert.equal(safeButtons(findings[1]).length, 1, 'unrelated legacy safe repair remains available');
    assert.equal(app.document.querySelector('.lh-all-safe'), null);
    assert.equal(app.requests.some(({ key }) => key.includes('/repair/preview')), false);
    assert.equal(app.requests.some(({ key }) => key.includes('/reviewed-repair/')), false);
  });
}

test('terminal author review explains the blocker without offering HO/PO Player Review', async (t) => {
  const app = await launchReport(reportFor({
    status: 'author_review',
    message: 'No existing clean grid exactly corroborates this tail.',
  }, [], { grouped: true }));
  t.after(() => app.close());
  const group = app.document.querySelector('.lh-finding-repair-group');
  assert.match(group.textContent, /2 repeated beat markers.*end of 2 arrangements/i);
  assert.match(group.textContent, /Needs review.*No existing clean grid/i);
  assert.match(group.textContent, /Automatic removal has not been approved/i);
  assert.equal(group.querySelector('button'), null);
  assert.doesNotMatch(group.textContent, /Open Player Review|HO\/PO|Review a safe fix/);
});

test('terminal unavailable assessment preserves its reason and leaves other safe operations available', async (t) => {
  const app = await launchReport(reportFor({
    status: 'unavailable', message: 'A declared chart could not be read safely.',
  }, otherRules));
  t.after(() => app.close());
  assert.match(app.document.querySelector('.lh-finding').textContent, /Needs review.*could not be read safely/i);
  assert.match(app.document.querySelector('.lh-all-safe').textContent, /2 kinds of safe fix/);
  assert.equal(safeButtons(app.document.querySelector('#lh-results')).length, 3);
});

test('a stale scan cannot offer terminal repair even with old automatic approval', async (t) => {
  const app = await launchReport(reportFor({ status: 'automatic' }, [], { current: false }));
  t.after(() => app.close());
  assert.equal(safeButtons(app.document.querySelector('#lh-results')).length, 0);
  assert.match(app.document.querySelector('.lh-finding').textContent, /Rescan this song/i);
});

test('batch preview explains excluded terminal operations without hiding independent safe fixes', async (t) => {
  const target = { kind: 'folder', label: 'synthetic' };
  const app = await launchLibraryDoctor({
    status: {
      stage: 'idle', running: false, scan_current: true, target,
      summary: { total: 2, warnings: 2 }, last_scan: { complete: true, target },
      batch: {
        phase: 'ready', running: false,
        preview: {
          batch_plan_id: 'synthetic-ready', scope_package_count: 2, eligible_count: 1,
          safe_repair_package_count: 1, blocked_count: 0,
          excluded_operation_count: 2,
          excluded_operations: [
            { title: 'Only terminal issue', rule_code: terminal, message: 'No clean grid corroborates this tail.' },
            { title: 'Also has a safe note repair', rule_code: terminal, message: 'A declared chart could not be read safely.' },
          ],
          packages: [{ package: 'Mixed.feedpak', title: 'Also has a safe note repair', safe_rule_count: 1 }],
        },
      },
    },
  });
  t.after(() => app.close());
  const preview = app.document.querySelector('#lh-batch-preview');
  const excluded = preview.querySelector('.lh-excluded-operations');
  assert.match(excluded.textContent, /Repairs excluded from this batch \(2\)/);
  assert.match(excluded.textContent, /No clean grid corroborates this tail/);
  assert.match(excluded.textContent, /declared chart could not be read safely/);
  assert.match(excluded.textContent, /Other independently safe fixes.*may still be included/);
  assert.match(preview.textContent, /1 of 2 scanned Feedpaks/);
  assert.ok([...preview.querySelectorAll('button')].some((button) => button.textContent === 'Continue to confirmation'));
});

test('approved terminal repair counts as one operation and retains its proof in combined preview', async (t) => {
  const app = await launchReport(reportFor({ status: 'automatic' }, otherRules), ({ key }) => {
    if (key !== '/api/plugins/library_doctor/repair/all/preview') return null;
    return jsonResponse({
      available: true, plan_id: 'synthetic-combined', title: 'Fix all safe issues',
      rule_count: 3, change_count: 5, member_count: 2, repair_summaries: [],
      terminal_assessment: {
        status: 'automatic', evidence_mode: 'clean_reference_v1', removed_count: 3,
        members: [{ original_count: 20, retained_count: 17, removed_count: 3 }],
      },
    });
  });
  t.after(() => app.close());
  const combined = app.document.querySelector('.lh-all-safe');
  assert.match(combined.textContent, /3 kinds of safe fix/);
  safeButtons(combined)[0].click();
  await waitFor(() => combined.querySelector('.lh-terminal-repair-proof'), 'combined terminal proof');
  assert.match(combined.textContent, /Remove 3 repeated beat markers at the end of 1 song-data file/);
  assert.match(combined.textContent, /Keep 17 markers in each/);
});

for (const evidenceMode of ['clean_reference_v1', 'shared_prefix_exact_redundancy_v1']) {
  test(`explicit terminal approval offers a preview with exact counts and ${evidenceMode} proof`, async (t) => {
    const app = await launchReport(reportFor({ status: 'automatic' }), ({ key }) => {
      if (key !== '/api/plugins/library_doctor/repair/preview') return null;
      return jsonResponse({
        available: true, plan_id: 'synthetic-terminal-plan', rule_code: terminal,
        title: definitions[0].title, change_kind: 'remove_duplicates',
        item_name: 'terminal beat copy', removed_count: 8, member_count: 4,
        arrays_affected: 4, musical_positions: 8,
        terminal_assessment: {
          status: 'automatic', evidence_mode: evidenceMode, removed_count: 8,
          members: [3, 1, 1, 3].map((removed_count, index) => ({
            member_path: `chart-${index}.json`, removed_count,
            original_count: 470 + removed_count, retained_count: 470,
          })),
        },
      });
    });
    t.after(() => app.close());
    const buttons = safeButtons(app.document.querySelector('#lh-results'));
    assert.equal(buttons.length, 1);
    buttons[0].click();
    await waitFor(() => app.document.querySelector('.lh-terminal-repair-proof'), 'terminal proof preview');
    const proof = app.document.querySelector('.lh-terminal-repair-proof');
    assert.match(proof.textContent, /Remove 8 repeated beat markers at the end of 4 song-data files/);
    assert.match(proof.textContent, /Keep 470 markers in each/);
    assert.match(proof.textContent, /Audio, notes, lyrics and their timing will not change/);
    assert.match(proof.textContent, evidenceMode === 'clean_reference_v1'
      ? /existing clean grid corroborates/ : /exact redundant copies.*retained timelines agree/);
    assert.equal(app.requests.filter(({ key }) => key.endsWith('/repair/preview')).length, 1);
  });
}
