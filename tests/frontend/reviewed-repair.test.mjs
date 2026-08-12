import assert from 'node:assert/strict';
import test from 'node:test';

import {
  deferred,
  jsonResponse,
  launchLibraryDoctor,
  waitFor,
} from './helpers/library-doctor-app.mjs';

const status = {
  stage: 'complete',
  running: false,
  repairing: false,
  scan_current: true,
  summary: { total: 1, errors: 0, warnings: 1, reviews: 1 },
  last_scan: { deep_audio: false },
  batch: { phase: 'idle', running: false },
};

const reviewedDefinition = {
  adapter_id: 'review.hopo-techniques',
  title: 'Review hammer-ons and pull-offs',
  description: 'Compare same-string context and choose explicitly.',
  safety: 'review_required',
  trigger_rule_codes: [
    'chart.conflicting-techniques',
    'review.hopo-direction-mismatch',
    'review.same-fret-hopo',
    'review.hopo-without-source',
  ],
  mutable_fields: ['ho', 'po', 'tp'],
  context_schema: 'library_doctor.reviewed_hopo_context.v1',
  candidate_limit: 2000,
  candidate_id_prefix: 'hopo',
  operation_name: 'review_hopo_techniques',
  blocker_codes: ['same_time_string_conflict', 'ambiguous_predecessor', 'malformed_technique_value'],
  postconditions: ['only_declared_fields_change'],
  audio_support: true,
  test_owner: 'review.hopo-techniques',
  decisions: [
    ['set_hammer_on', 'Use hammer-on'],
    ['set_pull_off', 'Use pull-off'],
    ['convert_to_tap', 'Convert to tap'],
    ['remove_hopo', 'Remove HO/PO'],
    ['move_to_next', 'Move to next note'],
    ['leave_unchanged', 'Leave unchanged'],
  ].map(([name, label]) => ({
    name,
    label,
    description: `${label} description.`,
    confirmation: `${label}?`,
  })),
};

const result = {
  package: 'Synthetic/HOPO.feedpak',
  title: 'HOPO Test',
  artist: 'Synthetic',
  features: { repair_scan_current: true, preview_declared: true },
  findings: [
    {
      code: 'review.hopo-direction-mismatch',
      severity: 'info',
      category: 'authoring_review',
      message: 'One wrong-direction marker.',
      affected_count: 1,
      rule: { repairability: 'review_required', title: 'HO/PO direction needs review' },
    },
    {
      code: 'review.same-fret-hopo',
      severity: 'info',
      category: 'authoring_review',
      message: 'One same-fret marker.',
      affected_count: 1,
      rule: { repairability: 'review_required', title: 'Same-fret HO/PO needs review' },
    },
  ],
};

const candidates = [
  {
    candidate_id: `hopo-${'a'.repeat(24)}`,
    member_path: 'arrangements/lead.json',
    location: 'arrangements/lead.json.notes[1]',
    context_kind: 'standalone_note',
    stream: 'Top-level arrangement',
    time: 2,
    string: 0,
    fret: 5,
    techniques: { hammer_on: false, pull_off: true, tap: false },
    reasons: ['direction_mismatch'],
    trigger_codes: ['review.hopo-direction-mismatch'],
    predecessor_state: 'usable',
    next_state: 'usable',
    previous: { time: 1, fret: 3, writable: true },
    next: { time: 3, fret: 7, writable: true },
    previous_gap_seconds: 1,
    next_gap_seconds: 1,
    outgoing_match: false,
    blockers: [],
    decision_names: ['set_hammer_on', 'set_pull_off', 'convert_to_tap', 'remove_hopo', 'leave_unchanged'],
  },
  {
    candidate_id: `hopo-${'b'.repeat(24)}`,
    member_path: 'arrangements/lead.json',
    location: 'arrangements/lead.json.chords[0].notes[0]',
    context_kind: 'chord_member',
    stream: 'Phrase difficulty phrases.0.levels.0',
    time: 4,
    string: 2,
    fret: 7,
    techniques: { hammer_on: true, pull_off: false, tap: false },
    reasons: ['same_fret'],
    trigger_codes: ['review.same-fret-hopo'],
    predecessor_state: 'usable',
    next_state: 'missing',
    previous: { time: 3.5, fret: 7, writable: true },
    next: null,
    previous_gap_seconds: 0.5,
    next_gap_seconds: null,
    outgoing_match: false,
    blockers: [],
    decision_names: ['set_hammer_on', 'set_pull_off', 'convert_to_tap', 'remove_hopo', 'leave_unchanged'],
  },
];

function inspection() {
  return {
    schema: 'library_doctor.reviewed_repair_inspection.v1',
    package: result.package,
    adapter_id: reviewedDefinition.adapter_id,
    title: reviewedDefinition.title,
    candidates,
    candidate_count: candidates.length,
    total_candidate_count: candidates.length,
    offset: 0,
    limit: 2000,
    has_previous: false,
    has_next: false,
    decision_definitions: reviewedDefinition.decisions,
    available: true,
  };
}

function buttonByText(root, label) {
  return [...root.querySelectorAll('button')]
    .find((button) => button.textContent.trim() === label);
}

async function launch(route) {
  return launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    route,
  });
}

test('reviewed repair groups related findings and has no default decision', async (t) => {
  const app = await launch((request) => {
    if (request.key.endsWith('/reviewed-repair/inspect')) return jsonResponse(inspection());
    return null;
  });
  t.after(() => app.close());
  const packageNode = app.document.querySelector('.lh-package');
  packageNode.open = true;
  assert.equal(app.document.querySelectorAll('.lh-reviewed-finding-group').length, 1);
  assert.equal(packageNode.querySelector('.lh-all-safe'), null);

  app.document.querySelector('.lh-reviewed-action button').click();
  await waitFor(() => app.document.querySelector('.lh-reviewed-candidate'), 'reviewed candidate');

  assert.equal(app.document.querySelectorAll('.lh-reviewed-choice input:checked').length, 0);
  assert.equal(app.document.querySelector('.lh-reviewed-footer .lh-button-primary').disabled, true);
  assert.match(app.document.querySelector('.lh-reviewed-candidate').textContent, /Previous on string/);
  assert.match(app.document.querySelector('.lh-reviewed-candidate').textContent, /Stored flags: pull-off/);
  assert.equal(app.document.activeElement.tagName, 'H5');

  const remove = app.document.querySelector('input[value="remove_hopo"]');
  remove.click();
  assert.equal(app.document.querySelector('.lh-reviewed-footer .lh-button-primary').disabled, false);
  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Next note').click();
  assert.match(app.document.querySelector('.lh-reviewed-candidate h5').textContent, /Candidate 2 of 2/);
  assert.equal(app.document.querySelectorAll('.lh-reviewed-choice input:checked').length, 0);
  assert.match(app.document.querySelector('.lh-reviewed-footer').textContent, /1 selected change/);
});

test('reviewed repair previews exact counts, confirms, applies, and exposes Undo', async (t) => {
  const requests = [];
  const app = await launch((request) => {
    if (request.key.endsWith('/reviewed-repair/inspect')) return jsonResponse(inspection());
    if (request.key.endsWith('/reviewed-repair/preview')) {
      requests.push(JSON.parse(request.body));
      return jsonResponse({
        plan_id: 'c'.repeat(64),
        available: true,
        changing_count: 1,
        skipped_count: 0,
        unresolved_count: 1,
        remaining_review_count: 1,
        decision_counts: { remove_hopo: 1 },
        player_result: 'Only selected technique fields change.',
        user_value: 'Explicit author decision.',
        file_handling: { summary: 'Full validation, backup, and Undo.' },
      });
    }
    if (request.key.endsWith('/reviewed-repair/apply')) {
      requests.push(JSON.parse(request.body));
      return jsonResponse({
        applied: true,
        outcome: 'success',
        backup_id: '20260812-120000-abcdef123456',
        undo_available: true,
        change_kind: 'reviewed_decisions',
        change_count: 1,
        removed_count: 0,
        musical_positions: 1,
        item_name: 'reviewed HO/PO decision',
        player_result: 'Only selected technique fields changed.',
        user_value: 'Explicit author decision.',
        file_handling: { summary: 'Full validation and recovery backup.', backup_retained: true },
        report: { title: result.title, artist: result.artist },
      });
    }
    return null;
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  app.document.querySelector('.lh-reviewed-action button').click();
  await waitFor(() => app.document.querySelector('input[value="remove_hopo"]'), 'reviewed choices');
  app.document.querySelector('input[value="remove_hopo"]').click();
  app.document.querySelector('.lh-reviewed-footer .lh-button-primary').click();
  await waitFor(() => app.document.querySelector('.lh-reviewed-preview'), 'exact preview');

  assert.match(app.document.querySelector('.lh-reviewed-preview').textContent, /1 note decision will change/);
  assert.match(app.document.querySelector('.lh-reviewed-preview').textContent, /1 remain unresolved/);
  assert.deepEqual(requests[0].decisions, [{
    candidate_id: candidates[0].candidate_id,
    decision: 'remove_hopo',
  }]);

  app.document.querySelector('.lh-reviewed-preview .lh-button-primary').click();
  await waitFor(() => app.document.querySelector('.lh-repair-confirm'), 'reviewed confirmation');
  assert.match(app.document.querySelector('.lh-repair-confirm').textContent, /recovery backup/);
  app.document.querySelector('.lh-repair-confirm .lh-button-primary').click();
  await waitFor(() => app.document.querySelector('#lh-repair-result').textContent.includes('Change completed'), 'reviewed receipt');
  assert.match(app.document.querySelector('#lh-repair-result').textContent, /Undo repair/);
  assert.equal(requests[1].plan_id, 'c'.repeat(64));
  assert.match(requests[1].request_id, /^reviewed-/);
});

test('closing a reviewed session prevents a stale inspection from repainting it', async (t) => {
  const delayed = deferred();
  const app = await launch((request) => {
    if (request.key.endsWith('/reviewed-repair/inspect')) return delayed.promise;
    return null;
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  const trigger = app.document.querySelector('.lh-reviewed-action button');
  trigger.click();
  // A pending inspection has no Close button yet. Leaving the plugin aborts
  // its activation and is the stronger stale-response boundary.
  app.controller.leave();
  delayed.resolve(jsonResponse(inspection()));
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.equal(app.document.querySelector('.lh-reviewed-shell'), null);
});

test('reviewed pages retain decisions and expose blocked candidates without guessing', async (t) => {
  const firstCandidate = { ...candidates[0] };
  const blockedCandidate = {
    ...candidates[1],
    blockers: ['same_time_string_conflict'],
  };
  const pages = {
    0: {
      ...inspection(),
      candidates: [firstCandidate],
      candidate_count: 1,
      total_candidate_count: 2,
      offset: 0,
      limit: 1,
      has_previous: false,
      has_next: true,
      previous_offset: null,
      next_offset: 1,
    },
    1: {
      ...inspection(),
      candidates: [blockedCandidate],
      candidate_count: 1,
      total_candidate_count: 2,
      offset: 1,
      limit: 1,
      has_previous: true,
      has_next: false,
      previous_offset: 0,
      next_offset: null,
    },
  };
  const app = await launch((request) => {
    if (!request.key.endsWith('/reviewed-repair/inspect')) return null;
    const body = JSON.parse(request.body);
    return jsonResponse(pages[body.offset || 0]);
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  app.document.querySelector('.lh-reviewed-action button').click();
  await waitFor(() => app.document.querySelector('input[value="remove_hopo"]'), 'first reviewed page');
  app.document.querySelector('input[value="remove_hopo"]').click();

  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Next page').click();
  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 2 of 2'),
    'second reviewed page',
  );
  const blockedChoices = [...app.document.querySelectorAll('.lh-reviewed-choice input')]
    .filter((input) => input.value !== 'leave_unchanged');
  assert.ok(blockedChoices.length > 0);
  assert.ok(blockedChoices.every((input) => input.disabled));
  assert.match(app.document.querySelector('.lh-reviewed-footer').textContent, /1 selected change/);

  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Previous page').click();
  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 1 of 2'),
    'first reviewed page again',
  );
  assert.equal(app.document.querySelector('input[value="remove_hopo"]').checked, true);
});
