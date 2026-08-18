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
  difficulty_scoped: true,
  test_owner: 'review.hopo-techniques',
  decisions: [
    ['set_hammer_on', 'Use hammer-on'],
    ['set_pull_off', 'Use pull-off'],
    ['convert_to_tap', 'Convert to tap'],
    ['remove_hopo', 'Remove HO/PO'],
    ['move_to_next', 'Move to next note'],
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
  features: {
    repair_scan_current: true,
    preview_declared: true,
    player_review: { available: true, reason: '', message: 'Available in Player.' },
  },
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
    review_item_id: `hopo-item-${'1'.repeat(24)}`,
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
    decision_names: ['set_hammer_on', 'set_pull_off', 'convert_to_tap', 'remove_hopo'],
    stream_context: {
      kind: 'top_level', difficulty_scope: 'full', is_full_difficulty: true, mastery_fraction: 1,
    },
    runtime_locator: { kind: 'standalone_note', note_index: 1 },
    player: {
      available: true,
      arrangements: [{ index: 0, id: 'lead', name: 'Lead', type: 'lead' }],
      default_arrangement_index: 0,
      mastery_fraction: 1,
    },
  },
  {
    candidate_id: `hopo-${'b'.repeat(24)}`,
    review_item_id: `hopo-item-${'2'.repeat(24)}`,
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
    decision_names: ['set_hammer_on', 'set_pull_off', 'convert_to_tap', 'remove_hopo'],
    stream_context: {
      kind: 'phrase_level',
      phrase_index: 0,
      difficulty_index: 0,
      difficulty_count: 1,
      difficulty_scope: 'lower',
      is_full_difficulty: false,
      mastery_fraction: 0.5,
    },
    runtime_locator: { kind: 'chord_member', chord_index: 0, chord_note_index: 0 },
    player: {
      available: true,
      arrangements: [{ index: 0, id: 'lead', name: 'Lead', type: 'lead' }],
      default_arrangement_index: 0,
      mastery_fraction: 0.5,
    },
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
    difficulty_scope: 'full_only',
    full_candidate_count: candidates.length,
    lower_candidate_count: 0,
    hidden_lower_candidate_count: 0,
  };
}

function playerContext(candidateItems = candidates, pendingRecovery = null) {
  return {
    schema: 'library_doctor.player_review_context.v1',
    package: result.package,
    adapter_id: reviewedDefinition.adapter_id,
    playback_filename: result.package,
    capabilities: {
      normal_player: true,
      live_highway_preview: true,
      full_tab_live_preview: false,
      partial_apply: true,
      single_undo_checkpoint: true,
    },
    pending_recovery: pendingRecovery,
    difficulty_scope: 'full_only',
    inspection: {
      ...inspection(),
      candidates: candidateItems,
      candidate_count: candidateItems.length,
      total_candidate_count: candidateItems.length,
      available: candidateItems.length > 0,
    },
  };
}

function optionResponse(candidate, decisionNames = candidate.decision_names, overrides = {}) {
  const definitions = new Map(
    reviewedDefinition.decisions.map((definition) => [definition.name, definition]),
  );
  const names = [...decisionNames];
  return {
    schema: 'library_doctor.reviewed_repair_options.v1',
    package: result.package,
    adapter_id: reviewedDefinition.adapter_id,
    difficulty_scope: 'full_only',
    candidate_id: candidate.candidate_id,
    review_item_id: candidate.review_item_id,
    decision_names: names,
    decision_definitions: names.map((name) => definitions.get(name)).filter(Boolean),
    omitted_decisions: [],
    available: names.length > 0,
    blocked: names.length === 0,
    message: names.length
      ? ''
      : 'No outcome-checked choice resolves this issue. Skip it for now.',
    options_id: `synthetic-${candidate.candidate_id}-${names.join('-') || 'none'}`,
    ...overrides,
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
    reviewedCandidates: candidates,
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

  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review with text only').click();
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
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review with text only').click();
  await waitFor(() => app.document.querySelector('input[value="remove_hopo"]'), 'reviewed choices');
  app.document.querySelector('input[value="remove_hopo"]').click();
  app.document.querySelector('.lh-reviewed-footer .lh-button-primary').click();
  await waitFor(() => app.document.querySelector('.lh-reviewed-preview'), 'exact preview');

  assert.match(app.document.querySelector('.lh-reviewed-preview').textContent, /1 outcome-checked note decision will change/);
  assert.match(app.document.querySelector('.lh-reviewed-preview').textContent, /1 unselected issue remain/);
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

test('text-only exact previews recover their trigger and invalidate on review-state changes', async (t) => {
  const previewRequests = [];
  const app = await launch((request) => {
    if (request.key.endsWith('/reviewed-repair/inspect')) return jsonResponse(inspection());
    if (request.key.endsWith('/reviewed-repair/preview')) {
      const body = JSON.parse(request.body);
      previewRequests.push(body);
      return jsonResponse({
        plan_id: String(previewRequests.length).repeat(64),
        available: true,
        changing_count: body.decisions.length,
        skipped_count: 0,
        unresolved_count: Math.max(0, candidates.length - body.decisions.length),
        remaining_review_count: Math.max(0, candidates.length - body.decisions.length),
        decision_counts: Object.fromEntries(
          body.decisions.map(({ decision }) => [decision, 1]),
        ),
      });
    }
    return null;
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review with text only').click();
  await waitFor(() => app.document.querySelector('input[value="remove_hopo"]'), 'first text-only choice');
  app.document.querySelector('input[value="remove_hopo"]').click();

  let preview = buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Preview selected changes');
  preview.click();
  await waitFor(() => app.document.querySelector('.lh-reviewed-preview'), 'first exact preview');
  assert.equal(preview.disabled, false, 'a successful preview restores its trigger');
  buttonByText(app.document.querySelector('.lh-reviewed-preview'), 'Back to decisions').click();
  assert.equal(app.document.querySelector('.lh-reviewed-preview'), null);
  assert.equal(preview.disabled, false, 'Back restores an immediately reusable Preview button');

  preview.click();
  await waitFor(() => app.document.querySelector('.lh-reviewed-preview'), 'preview before decision change');
  app.document.querySelector('input[value="set_hammer_on"]').click();
  assert.equal(app.document.querySelector('.lh-reviewed-preview'), null);
  assert.equal(
    buttonByText(app.document, 'Confirm these reviewed changes'),
    undefined,
    'a stale frozen group cannot still be confirmed after a selection changes',
  );

  preview = buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Preview selected changes');
  preview.click();
  await waitFor(() => app.document.querySelector('.lh-reviewed-preview'), 'preview before candidate navigation');
  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Next note').click();
  assert.equal(app.document.querySelector('.lh-reviewed-preview'), null);
  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 2 of 2'),
    'second candidate after invalidating preview',
  );

  app.document.querySelector('input[value="remove_hopo"]').click();
  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Preview selected changes').click();
  await waitFor(() => app.document.querySelector('.lh-reviewed-preview'), 'preview before Skip');
  buttonByText(app.document.querySelector('.lh-reviewed-candidate'), 'Skip for now').click();
  assert.equal(app.document.querySelector('.lh-reviewed-preview'), null);
  await waitFor(
    () => buttonByText(app.document.querySelector('.lh-reviewed-candidate'), 'Review this issue again'),
    'skipped candidate remains reviewable',
  );

  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Preview selected changes').click();
  await waitFor(() => app.document.querySelector('.lh-reviewed-preview'), 'preview before review-again');
  assert.deepEqual(previewRequests.at(-1).decisions, [{
    candidate_id: candidates[0].candidate_id,
    decision: 'set_hammer_on',
  }]);
  buttonByText(app.document.querySelector('.lh-reviewed-candidate'), 'Review this issue again').click();
  assert.equal(app.document.querySelector('.lh-reviewed-preview'), null);
});

test('text-only pagination invalidates an exact preview before loading another page', async (t) => {
  const pages = {
    0: {
      ...inspection(),
      candidates: [candidates[0]],
      candidate_count: 1,
      total_candidate_count: 2,
      limit: 1,
      has_next: true,
      next_offset: 1,
    },
    1: {
      ...inspection(),
      candidates: [candidates[1]],
      candidate_count: 1,
      total_candidate_count: 2,
      offset: 1,
      limit: 1,
      has_previous: true,
      previous_offset: 0,
    },
  };
  const app = await launch((request) => {
    if (request.key.endsWith('/reviewed-repair/inspect')) {
      const body = JSON.parse(request.body);
      return jsonResponse(pages[body.offset || 0]);
    }
    if (request.key.endsWith('/reviewed-repair/preview')) {
      return jsonResponse({
        plan_id: 'f'.repeat(64),
        available: true,
        changing_count: 1,
        skipped_count: 0,
        unresolved_count: 1,
        remaining_review_count: 1,
        decision_counts: { remove_hopo: 1 },
      });
    }
    return null;
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review with text only').click();
  await waitFor(() => app.document.querySelector('input[value="remove_hopo"]'), 'first paged choice');
  app.document.querySelector('input[value="remove_hopo"]').click();
  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Preview selected changes').click();
  await waitFor(() => app.document.querySelector('.lh-reviewed-preview'), 'paged exact preview');

  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Next page').click();
  assert.equal(app.document.querySelector('.lh-reviewed-preview'), null);
  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 2 of 2'),
    'second page after preview invalidation',
  );
});

test('a late text-only preview response cannot repaint after candidate navigation', async (t) => {
  const delayedPreview = deferred();
  const app = await launch((request) => {
    if (request.key.endsWith('/reviewed-repair/inspect')) return jsonResponse(inspection());
    if (request.key.endsWith('/reviewed-repair/preview')) return delayedPreview.promise;
    return null;
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review with text only').click();
  await waitFor(() => app.document.querySelector('input[value="remove_hopo"]'), 'choice before delayed preview');
  app.document.querySelector('input[value="remove_hopo"]').click();
  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Preview selected changes').click();
  await waitFor(
    () => buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Building exact preview...'),
    'pending exact preview',
  );

  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Next note').click();
  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 2 of 2'),
    'navigation while preview is pending',
  );
  delayedPreview.resolve(jsonResponse({
    plan_id: '9'.repeat(64),
    available: true,
    changing_count: 1,
    skipped_count: 0,
    unresolved_count: 1,
    remaining_review_count: 1,
    decision_counts: { remove_hopo: 1 },
  }));
  await new Promise((resolve) => setTimeout(resolve, 10));

  assert.equal(app.document.querySelector('.lh-reviewed-preview'), null);
  const restored = buttonByText(
    app.document.querySelector('.lh-reviewed-footer'),
    'Preview selected changes',
  );
  assert.ok(restored);
  assert.equal(restored.disabled, false);
});

test('closing a reviewed session prevents a stale inspection from repainting it', async (t) => {
  const delayed = deferred();
  const app = await launch((request) => {
    if (request.key.endsWith('/reviewed-repair/inspect')) return delayed.promise;
    return null;
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  const trigger = buttonByText(
    app.document.querySelector('.lh-reviewed-action'),
    'Review with text only',
  );
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
    if (request.key.endsWith('/reviewed-repair/options')) {
      const body = JSON.parse(request.body);
      if (body.candidate_id === blockedCandidate.candidate_id) {
        return jsonResponse(optionResponse(blockedCandidate, [], {
          blocked: true,
          message: 'This issue is blocked and has no safe reviewed choice. Skip it for now.',
        }));
      }
    }
    if (!request.key.endsWith('/reviewed-repair/inspect')) return null;
    const body = JSON.parse(request.body);
    return jsonResponse(pages[body.offset || 0]);
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review with text only').click();
  await waitFor(() => app.document.querySelector('input[value="remove_hopo"]'), 'first reviewed page');
  app.document.querySelector('input[value="remove_hopo"]').click();

  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Next page').click();
  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 2 of 2'),
    'second reviewed page',
  );
  await waitFor(
    () => /blocked and has no safe reviewed choice/.test(
      app.document.querySelector('.lh-reviewed-candidate')?.textContent || '',
    ),
    'blocked candidate outcome check',
  );
  assert.equal(app.document.querySelectorAll('.lh-reviewed-choice input').length, 0);
  assert.ok(buttonByText(app.document.querySelector('.lh-reviewed-candidate'), 'Skip for now'));
  assert.match(app.document.querySelector('.lh-reviewed-footer').textContent, /1 selected change/);

  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Previous page').click();
  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 1 of 2'),
    'first reviewed page again',
  );
  assert.equal(app.document.querySelector('input[value="remove_hopo"]').checked, true);
});

test('reviewed choices come only from outcome checks and skipped issues never become preview decisions', async (t) => {
  const previewRequests = [];
  const app = await launch((request) => {
    if (request.key.endsWith('/reviewed-repair/inspect')) return jsonResponse(inspection());
    if (request.key.endsWith('/reviewed-repair/options')) {
      const body = JSON.parse(request.body);
      if (body.candidate_id === candidates[0].candidate_id) {
        return jsonResponse(optionResponse(candidates[0], [], {
          message: 'No outcome-checked choice resolves the first issue. Skip it for now.',
        }));
      }
      if (body.candidate_id === candidates[1].candidate_id) {
        return jsonResponse(optionResponse(candidates[1], ['remove_hopo']));
      }
    }
    if (request.key.endsWith('/reviewed-repair/preview')) {
      previewRequests.push(JSON.parse(request.body));
      return jsonResponse({
        plan_id: 'e'.repeat(64),
        available: true,
        changing_count: 1,
        skipped_count: 0,
        unresolved_count: 1,
        remaining_review_count: 1,
        decision_counts: { remove_hopo: 1 },
        player_result: 'Only the outcome-checked choice changes.',
        user_value: 'Skipped issues remain unresolved.',
        file_handling: { summary: 'No skipped issue is included.' },
      });
    }
    return null;
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review with text only').click();

  await waitFor(() => /No outcome-checked choice resolves the first issue/.test(
    app.document.querySelector('.lh-reviewed-candidate')?.textContent || '',
  ), 'zero-option reviewed issue');
  assert.equal(app.document.querySelectorAll('.lh-reviewed-choice input').length, 0);
  buttonByText(app.document.querySelector('.lh-reviewed-candidate'), 'Skip for now').click();

  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 2 of 2')
      && app.document.querySelector('input[value="remove_hopo"]'),
    'server-filtered second issue',
  );
  assert.deepEqual(
    [...app.document.querySelectorAll('.lh-reviewed-choice input')].map((input) => input.value),
    ['remove_hopo'],
  );
  app.document.querySelector('input[value="remove_hopo"]').click();

  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Review skipped issues').click();
  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 1 of 2'),
    'revisit skipped issue',
  );
  buttonByText(app.document.querySelector('.lh-reviewed-candidate'), 'Skip for now').click();
  await waitFor(
    () => app.document.querySelector('.lh-reviewed-candidate h5')?.textContent.includes('Candidate 2 of 2'),
    'return to selected issue after a second skip',
  );
  buttonByText(app.document.querySelector('.lh-reviewed-footer'), 'Preview selected changes').click();
  await waitFor(() => previewRequests.length === 1, 'preview without skipped decision');

  assert.deepEqual(previewRequests[0].decisions, [{
    candidate_id: candidates[1].candidate_id,
    decision: 'remove_hopo',
  }]);
  assert.equal(
    previewRequests[0].decisions.some(({ candidate_id }) => candidate_id === candidates[0].candidate_id),
    false,
  );
});

test('preserved Player Review choices block Apply until failures are skipped', async (t) => {
  const freshCandidates = candidates.map((item, index) => ({
    ...item,
    candidate_id: `hopo-${(index ? 'd' : 'c').repeat(24)}`,
  }));
  const approvals = new Map(
    freshCandidates.map((item) => [item.candidate_id, deferred()]),
  );
  const previewRequests = [];
  let contextCalls = 0;
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        contextCalls += 1;
        return jsonResponse(playerContext(contextCalls === 1 ? candidates : freshCandidates));
      }
      if (request.key.endsWith('/reviewed-repair/options')) {
        const body = JSON.parse(request.body);
        const original = candidates.find((item) => item.candidate_id === body.candidate_id);
        if (original) return jsonResponse(optionResponse(original, ['remove_hopo']));
        return approvals.get(body.candidate_id)?.promise || null;
      }
      if (request.key.endsWith('/reviewed-repair/preview')) {
        previewRequests.push(JSON.parse(request.body));
        return jsonResponse({
          plan_id: 'f'.repeat(64),
          available: true,
          changing_count: 1,
          skipped_count: 0,
          unresolved_count: 1,
        });
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();

  for (let index = 0; index < candidates.length; index += 1) {
    await waitFor(() => {
      const input = app.document.querySelector(
        '#lh-player-review-overlay input[value="remove_hopo"]',
      );
      return input && !input.disabled;
    }, `original choice ${index + 1}`);
    app.document.querySelector(
      '#lh-player-review-overlay input[value="remove_hopo"]',
    ).click();
    buttonByText(
      app.document.querySelector('#lh-player-review-overlay'),
      'Accept & Next issue',
    ).click();
  }
  await waitFor(() => /2 accepted changes/.test(
    app.document.querySelector('.lh-player-review-summary')?.textContent || '',
  ), 'two accepted original choices');
  buttonByText(
    app.document.querySelector('#lh-player-review-overlay'),
    'Return to Library Doctor',
  ).click();
  await waitFor(
    () => app.document.querySelector('.screen.active')?.id === 'plugin-library_doctor',
    'return before fresh approval checks',
  );
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Resume Player Review').click();
  await waitFor(() => /Rechecking 2 preserved choices/.test(
    app.document.querySelector('.lh-player-review-summary')?.textContent || '',
  ), 'bounded preserved approval start');

  approvals.get(freshCandidates[0].candidate_id).resolve(
    jsonResponse(optionResponse(freshCandidates[0], ['remove_hopo'])),
  );
  await waitFor(() => {
    const summary = app.document.querySelector('.lh-player-review-summary')?.textContent || '';
    return /1 accepted change/.test(summary) && /Rechecking 1 preserved choice/.test(summary);
  }, 'partial preserved approval');
  let apply = buttonByText(
    app.document.querySelector('#lh-player-review-overlay'),
    'Apply accepted changes',
  );
  assert.equal(apply.disabled, true);
  apply.disabled = false;
  apply.click();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(previewRequests.length, 0, 'the execution guard must reject a partial approval group');

  approvals.get(freshCandidates[1].candidate_id).reject(
    new Error('Synthetic preserved approval failure'),
  );
  await waitFor(() => /1 preserved choice check failed/.test(
    app.document.querySelector('.lh-player-review-summary')?.textContent || '',
  ), 'failed approval remains blocking');
  apply = buttonByText(
    app.document.querySelector('#lh-player-review-overlay'),
    'Apply accepted changes',
  );
  assert.equal(apply.disabled, true);
  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Next issue').click();
  await waitFor(() => {
    const overlay = app.document.querySelector('#lh-player-review-overlay');
    const retry = buttonByText(overlay, 'Retry choice check');
    const skipFailed = buttonByText(overlay, 'Skip for now');
    return retry && skipFailed && !skipFailed.disabled;
  }, 'failed preserved choice controls');
  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Skip for now').click();
  await waitFor(() => {
    const overlay = app.document.querySelector('#lh-player-review-overlay');
    const button = buttonByText(overlay, 'Apply accepted changes');
    return button && !button.disabled
      && !/preserved choice/.test(overlay.querySelector('.lh-player-review-summary')?.textContent || '');
  }, 'Skip clears the failed complete-group gate');
  buttonByText(
    app.document.querySelector('#lh-player-review-overlay'),
    'Apply accepted changes',
  ).click();
  await waitFor(() => previewRequests.length === 1, 'approved remainder preview');
  assert.deepEqual(previewRequests[0].decisions, [{
    candidate_id: freshCandidates[0].candidate_id,
    decision: 'remove_hopo',
  }]);
});

test('Player Review opens the normal player and previews an explicit HO/PO choice live', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    chartInput: {
      notes: [{ t: 2, s: 0, f: 5, po: true }],
      allNotes: [{ t: 2, s: 0, f: 5, po: true }],
      chords: [{
        t: 4,
        notes: [
          { s: 0, f: 0, ho: false, po: true, tp: false },
          { s: 2, f: 7, ho: true, po: false, tp: false },
        ],
      }],
      allChords: [{
        t: 4,
        notes: [
          { s: 0, f: 0, ho: false, po: true, tp: false },
          { s: 2, f: 7, ho: true, po: false, tp: false },
        ],
      }],
    },
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext());
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(
    () => app.capabilityRequests.some(
      (request) => request.capability === 'playback' && request.command === 'start',
    ),
    'Player Review playback start',
  );

  const start = app.capabilityRequests.find(
    (request) => request.capability === 'playback' && request.command === 'start',
  );
  assert.equal(start.source, 'library_doctor');
  assert.deepEqual(start.args.target, {
    filename: result.package,
    sourceKind: 'local',
  });
  assert.equal(start.args.arrangement, 0);
  assert.equal('startTime' in start.args, false);
  assert.equal(start.args.authorization, 'user-action');
  assert.equal(app.document.querySelector('.screen.active').id, 'player');
  assert.equal(app.mastery, 1);
  assert.equal(app.playbackTime, 2);
  assert.equal(app.document.querySelector('.lh-player-review-scope'), null);
  assert.ok(app.capabilityRequests.some(
    (request) => request.capability === 'playback' && request.command === 'pause',
  ));
  await waitFor(
    () => /Paused at the current issue/.test(
      app.document.querySelector('.lh-player-review-status')?.textContent || '',
    ),
    'initial issue landing',
  );
  assert.match(app.document.querySelector('.lh-player-review-status').textContent, /Paused at the current issue/);

  const timelineOverlay = app.document.querySelector('#lh-player-review-timeline-overlay');
  assert.ok(timelineOverlay);
  assert.equal(timelineOverlay.parentElement, app.document.body);
  assert.equal(app.document.querySelector('#lh-player-review-overlay').contains(timelineOverlay), false);
  buttonByText(timelineOverlay, '+0.1s').click();
  await waitFor(() => Math.abs(app.playbackTime - 2.1) < 0.001, 'sub-second timeline nudge');
  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Jump to issue').click();
  await waitFor(() => app.playbackTime === 2, 'timeline return to issue');

  await waitFor(() => {
    const button = buttonByText(
      app.document.querySelector('#lh-player-review-overlay'),
      'Play preview (2s before + 2s after)',
    );
    return button && !button.disabled;
  }, 'ready to preview after timeline jump');
  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Play preview (2s before + 2s after)').click();
  await waitFor(
    () => app.capabilityRequests.some(
      (request) => request.capability === 'playback' && request.command === 'resume',
    ),
    'four-second preview playback',
  );
  assert.equal(app.playbackTime, 0);
  app.window.feedBack.emit('song:position-changed', { time: 4.01, chartT: 4.01 });
  await waitFor(() => app.playbackTime === 2
    && /Preview finished/.test(
      app.document.querySelector('.lh-player-review-status')?.textContent || '',
    ), 'preview return to issue');
  assert.match(app.document.querySelector('.lh-player-review-status').textContent, /Preview finished/);

  const seeksBeforeJump = app.capabilityRequests.filter(
    (request) => request.capability === 'playback' && request.command === 'seek',
  ).length;
  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Jump to issue').click();
  await waitFor(
    () => app.capabilityRequests.filter(
      (request) => request.capability === 'playback' && request.command === 'seek',
    ).length > seeksBeforeJump
      && /Paused at the current issue/.test(app.document.querySelector('.lh-player-review-status')?.textContent || ''),
    'explicit jump to issue',
  );

  app.document.querySelector(
    '#lh-player-review-overlay input[value="set_hammer_on"]',
  ).click();
  await waitFor(() => app.lastChartOutput?.notes?.[0]?.ho === true, 'live chart transform');
  assert.equal('po' in app.lastChartOutput.notes[0], false);
  assert.equal(app.lastChartOutput.allNotes[0].ho, true);
  assert.match(
    app.document.querySelector('.lh-player-review-status').textContent,
    /previewed on the Highway/,
  );

  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Accept & Next issue').click();
  await waitFor(
    () => app.document.querySelector('.lh-player-review-count')?.textContent.includes('Issue 2'),
    'next Player Review issue',
  );
  assert.match(app.document.querySelector('.lh-player-review-summary').textContent, /1 accepted change/);

  await waitFor(
    () => {
      const input = app.document.querySelector(
        '#lh-player-review-overlay input[value="convert_to_tap"]',
      );
      return input && !input.disabled;
    },
    'chord-member live choice',
  );
  app.document.querySelector(
    '#lh-player-review-overlay input[value="convert_to_tap"]',
  ).click();
  await waitFor(
    () => app.lastChartOutput?.chords?.[0]?.notes?.[1]?.tp === true,
    'live chord-member chart transform',
  );
  assert.equal(app.lastChartOutput.chords[0].notes[1].ho, undefined);
  assert.equal(app.lastChartOutput.chords[0].notes[1].po, undefined);
  assert.equal(
    app.lastChartOutput.chords[0].notes[0].po,
    true,
    'the valid first-string pull-off must remain authored',
  );
  assert.ok(app.capabilityRequests.some(
    (request, index, requests) => request.capability === 'chart-transform'
      && request.command === 'refresh'
      && requests.slice(0, index).some(
        (earlier) => earlier.capability === 'chart-transform'
          && earlier.command === 'select-provider',
      ),
  ));
  assert.match(
    app.document.querySelector('.lh-player-review-status').textContent,
    /previewed on the Highway/,
  );
});

test('Player Review composes the issue highlight with the incumbent provider and restores it on exit', async (t) => {
  const incumbent = (note, chartTime) => ({
    state: 'incumbent',
    string: note?.s,
    chartTime,
  });
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    noteStateProvider: incumbent,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext());
      }
      if (request.key.endsWith('/reviewed-repair/options')) {
        const body = JSON.parse(request.body);
        if (body.candidate_id === candidates[0].candidate_id) {
          return jsonResponse(optionResponse(candidates[0], ['remove_hopo']));
        }
        if (body.candidate_id === candidates[1].candidate_id) {
          return jsonResponse(optionResponse(candidates[1], [], {
            message: 'No outcome-checked choice resolves the highlighted issue. Skip it for now.',
          }));
        }
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();

  await waitFor(
    () => app.noteStateProvider !== incumbent
      && /Paused at the current issue/.test(
        app.document.querySelector('.lh-player-review-status')?.textContent || '',
      ),
    'installed first issue highlight',
  );
  const composite = app.noteStateProvider;
  const firstHighlight = composite({ s: 0, f: 5 }, 2);
  assert.equal(firstHighlight.state, 'active');
  assert.equal(firstHighlight.color, '#ffd166');
  assert.equal(firstHighlight.live, true);
  assert.deepEqual(
    composite({ s: 1, f: 5 }, 2),
    { state: 'incumbent', string: 1, chartTime: 2 },
  );
  assert.match(
    app.document.querySelector('.lh-player-review-highlight-note')?.textContent || '',
    /Pulsing note = current issue.*string 1, fret 5/,
  );
  await waitFor(
    () => app.document.querySelector('#lh-player-review-overlay input[value="remove_hopo"]'),
    'server-filtered Player Review choice',
  );
  assert.deepEqual(
    [...app.document.querySelectorAll('#lh-player-review-overlay input[type="radio"]')]
      .map((input) => input.value),
    ['remove_hopo'],
  );

  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Skip for now').click();
  await waitFor(
    () => app.document.querySelector('.lh-player-review-count')?.textContent.includes('Issue 2')
      && Math.abs(app.playbackTime - 4) < 0.001
      && /No outcome-checked choice resolves the highlighted issue/.test(
        app.document.querySelector('#lh-player-review-overlay')?.textContent || '',
      ),
    'second issue highlight target',
  );
  assert.equal(
    app.document.querySelectorAll('#lh-player-review-overlay input[type="radio"]').length,
    0,
  );
  assert.equal(app.noteStateProvider, composite);
  assert.deepEqual(
    composite({ s: 0, f: 5 }, 2),
    { state: 'incumbent', string: 0, chartTime: 2 },
  );
  assert.equal(composite({ s: 2, f: 7 }, 4).state, 'active');

  buttonByText(
    app.document.querySelector('#lh-player-review-overlay'),
    'Return to Library Doctor',
  ).click();
  await waitFor(
    () => app.document.querySelector('.screen.active')?.id === 'plugin-library_doctor'
      && app.noteStateProvider === incumbent,
    'incumbent highlight provider restoration',
  );
});

test('affected-song difficulty filter updates cached counters independently of the scan default', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    route(request) {
      if (request.key.includes('/rules?')) {
        const allAuthored = request.key.includes('review_difficulty_scope=all_authored');
        return jsonResponse({
          items: allAuthored ? [{
            code: 'review.lower-only',
            severity: 'info',
            category: 'authoring_review',
            package_count: 1,
            finding_count: 2,
            rule: { title: 'Lower-difficulty review', area: 'Playability' },
          }] : [],
        });
      }
      if (!request.key.startsWith('/api/plugins/library_doctor/status?')) return null;
      const allAuthored = request.key.includes('review_difficulty_scope=all_authored');
      return jsonResponse({
        ...status,
        review_difficulty_scope: allAuthored ? 'all_authored' : 'full_only',
        summary: {
          total: 38,
          errors: 1,
          warnings: 11,
          reviews: allAuthored ? 15 : 7,
          healthy: allAuthored ? 18 : 24,
        },
      });
    },
  });
  t.after(() => app.close());

  assert.ok(app.requests.some(({ key }) => (
    key.includes('/results?') && key.includes('review_difficulty_scope=full_only')
  )));
  assert.ok(app.requests.some(({ key }) => (
    key.includes('/rules?') && key.includes('review_difficulty_scope=full_only')
  )));
  const scanRequestsBefore = app.requests.filter(({ key }) => key.endsWith('/scan')).length;
  const resultRequestsBefore = app.requests.filter(({ key }) => key.includes('/results?')).length;
  const listSelect = app.document.querySelector('#lh-review-list-difficulty-scope');
  const defaultSelect = app.document.querySelector('#lh-review-difficulty-scope');
  assert.equal(listSelect.value, 'full_only');
  assert.equal(defaultSelect.value, 'full_only');
  assert.equal(app.document.querySelector('[data-summary="reviews"]').textContent, '7');
  listSelect.value = 'all_authored';
  listSelect.dispatchEvent(new app.window.Event('change', { bubbles: true }));

  await waitFor(
    () => app.requests.filter(({ key }) => (
      key.includes('/results?') && key.includes('review_difficulty_scope=all_authored')
    )).length > 0
      && app.requests.filter(({ key }) => key.includes('/results?')).length > resultRequestsBefore,
    'cached all-difficulty result request',
  );
  await waitFor(
    () => app.document.querySelector('[data-summary="reviews"]').textContent === '15',
    'all-authored cached counters',
  );
  assert.equal(
    app.requests.filter(({ key }) => key.endsWith('/scan')).length,
    scanRequestsBefore,
  );
  assert.equal(app.window.localStorage.getItem('library_doctor.review.difficulty_scope'), null);

  await waitFor(
    () => app.document.querySelector('button[data-rule="review.lower-only"]'),
    'lower-difficulty rule summary',
  );
  app.document.querySelector('button[data-rule="review.lower-only"]').click();
  await waitFor(() => app.requests.some(({ key }) => (
    key.includes('/results?') && key.includes('rule=review.lower-only')
  )), 'lower-difficulty selected rule');

  const requestsBeforeReturningToFull = app.requests.filter(({ key }) => key.includes('/results?')).length;
  listSelect.value = 'full_only';
  listSelect.dispatchEvent(new app.window.Event('change', { bubbles: true }));
  await waitFor(() => app.requests.filter(({ key }) => key.includes('/results?')).length
    > requestsBeforeReturningToFull, 'return to full-only cached view');
  const resultRequestsAfterListToggle = app.requests.filter(({ key }) => key.includes('/results?')).length;
  const latestFullResultRequest = app.requests.filter(({ key }) => key.includes('/results?')).at(-1).key;
  assert.match(latestFullResultRequest, /review_difficulty_scope=full_only/);
  assert.doesNotMatch(latestFullResultRequest, /rule=review.lower-only/);
  defaultSelect.value = 'all_authored';
  defaultSelect.dispatchEvent(new app.window.Event('change', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(app.window.localStorage.getItem('library_doctor.review.difficulty_scope'), 'all_authored');
  assert.equal(listSelect.value, 'full_only');
  assert.equal(
    app.requests.filter(({ key }) => key.includes('/results?')).length,
    resultRequestsAfterListToggle,
  );
});

test('changing the affected-song scope cannot resume a stale Player Review queue', async (t) => {
  const contextScopes = [];
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    route(request) {
      if (request.key.includes('/results?')) {
        const allAuthored = request.key.includes('review_difficulty_scope=all_authored');
        return jsonResponse({
          total: 1,
          items: [{ ...result, title: allAuthored ? 'HOPO Test · all authored' : result.title }],
        });
      }
      if (!request.key.endsWith('/reviewed-repair/player-context')) return null;
      const body = JSON.parse(request.body);
      const scope = body.difficulty_scope || 'full_only';
      contextScopes.push(scope);
      const context = playerContext(scope === 'all_authored' ? [candidates[1]] : []);
      context.difficulty_scope = scope;
      context.inspection.difficulty_scope = scope;
      return jsonResponse(context);
    },
  });
  t.after(() => app.close());

  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(
    () => /No manual Player Review issues match/.test(
      app.document.querySelector('.lh-reviewed-region')?.textContent || '',
    ),
    'clear empty full-difficulty result',
  );
  assert.equal(app.document.querySelector('.screen.active').id, 'plugin-library_doctor');

  const scope = app.document.querySelector('#lh-review-list-difficulty-scope');
  scope.value = 'all_authored';
  scope.dispatchEvent(new app.window.Event('change', { bubbles: true }));
  await waitFor(() => app.requests.some(({ key }) => (
    key.includes('/results?') && key.includes('review_difficulty_scope=all_authored')
  )), 'all-authored affected-song view');
  await waitFor(
    () => /all authored/.test(app.document.querySelector('.lh-package')?.textContent || ''),
    'refiltered song row',
  );
  app.document.querySelector('.lh-package').open = true;
  const action = app.document.querySelector('.lh-reviewed-action');
  assert.ok(buttonByText(action, 'Review in Player'));
  assert.equal(buttonByText(action, 'Resume Player Review'), undefined);
  buttonByText(action, 'Review in Player').click();

  await waitFor(
    () => app.document.querySelector('.screen.active')?.id === 'player'
      && /Paused at the current issue/.test(
        app.document.querySelector('.lh-player-review-status')?.textContent || '',
      ),
    'fresh all-authored Player Review',
  );
  assert.deepEqual(contextScopes, ['full_only', 'all_authored']);
  assert.match(app.document.querySelector('.lh-player-review-count').textContent, /Issue 1 of 1/);
});

test('Player Review and timeline windows move independently and reset safely', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'movable review windows');

  const review = app.document.querySelector('#lh-player-review-overlay');
  const timeline = app.document.querySelector('#lh-player-review-timeline-overlay');
  const reviewStart = Number.parseFloat(review.style.left);
  const timelineStart = Number.parseFloat(timeline.style.left);
  const handle = review.querySelector('.lh-player-review-drag-handle');
  handle.dispatchEvent(new app.window.MouseEvent('pointerdown', {
    bubbles: true, button: 0, clientX: 200, clientY: 100,
  }));
  app.window.dispatchEvent(new app.window.MouseEvent('pointermove', {
    bubbles: true, clientX: 100, clientY: 140,
  }));
  app.window.dispatchEvent(new app.window.MouseEvent('pointerup', { bubbles: true }));

  assert.ok(Number.parseFloat(review.style.left) < reviewStart);
  assert.equal(Number.parseFloat(timeline.style.left), timelineStart);
  const stored = JSON.parse(
    app.window.localStorage.getItem('library_doctor.player_review.layout.v1'),
  );
  assert.equal(stored.version, 1);
  assert.ok(stored.positions.review);
  assert.ok(stored.positions.timeline);

  buttonByText(review, 'Reset layout').click();
  assert.equal(Number.parseFloat(review.style.left), reviewStart);
  assert.equal(Number.parseFloat(timeline.style.left), timelineStart);
  assert.match(app.document.querySelector('.lh-player-review-status').textContent, /default positions/);
});

test('Player Review claims and settles the fresh-song autoplay gate before ready', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    simulateFreshSongAutoplay: true,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'autoplay-gated issue landing');

  const loadingIndex = app.coreEvents.findIndex(({ name }) => name === 'song:loading');
  const loadedIndex = app.coreEvents.findIndex(({ name }) => name === 'song:loaded');
  const readyIndex = app.coreEvents.findIndex(({ name }) => name === 'song:ready');
  assert.ok(loadingIndex >= 0 && loadingIndex < loadedIndex && loadedIndex < readyIndex);
  assert.deepEqual(
    app.autoplayState.actions.map(({ event }) => event),
    ['hold', 'settle'],
    'the fresh-song autostart must remain deferred until Player Review explicitly releases it',
  );
  assert.equal(app.autoplayState.held, true);
  assert.ok(app.autoplayState.actions.every(
    ({ coreEventCount }) => coreEventCount === loadingIndex + 1 && coreEventCount <= loadedIndex,
  ), 'holdAutoplay and release.settle must run synchronously inside expected song:loading');
  assert.equal(
    app.coreEvents.some(({ name }) => name === 'song:play' || name === 'song:resume'),
    false,
    'fresh-song autoplay must not race the initial pause and seek',
  );
  assert.equal(
    app.capabilityRequests.some(
      (request) => request.capability === 'playback' && request.command === 'resume',
    ),
    false,
    'only an explicit preview or user Play may resume the review song',
  );
});

test('destroy defers a settled autoplay hold until safe exit and removes temporary listeners', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    simulateFreshSongAutoplay: true,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.dom.window.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'settled hold before destroy');

  assert.deepEqual(app.autoplayState.actions.map(({ event }) => event), ['hold', 'settle']);
  assert.equal(app.listenerCount, 10, 'only the normal app lifecycle listeners exist initially');
  app.controller.destroy();
  assert.deepEqual(
    app.autoplayState.actions.map(({ event }) => event),
    ['hold', 'settle'],
    'destroy on the Player screen must defer release until a safe lifecycle boundary',
  );
  assert.equal(app.listenerCount, 3, 'only the temporary safe-release listeners remain');

  app.window.feedBack.emit('screen:changed', {
    id: 'plugin-library_doctor',
    from: 'player',
  });
  assert.deepEqual(
    app.autoplayState.actions.map(({ event }) => event),
    ['hold', 'settle', 'release', 'start'],
  );
  assert.equal(app.autoplayState.held, false);
  assert.equal(app.listenerCount, 0, 'safe release must restore the pre-boot listener baseline');

  app.window.feedBack.emit('screen:changed', { id: 'plugins', from: 'plugin-library_doctor' });
  app.window.feedBack.emit('song:resume', { time: 2, audioT: 2, chartT: 2 });
  assert.equal(
    app.autoplayState.actions.filter(({ event }) => event === 'release').length,
    1,
    'later lifecycle events cannot release the settled hold twice',
  );
});

test('a generic stale song resume cannot release a freshly claimed autoplay hold', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    simulateFreshSongAutoplay: true,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'fresh hold before stale resume');

  app.window.feedBack.emit('song:resume', {
    time: app.playbackTime,
    audioT: app.playbackTime,
    chartT: app.playbackChartTime,
    reason: 'stale-core-resume-from-prior-load',
  });
  assert.deepEqual(
    app.autoplayState.actions.map(({ event }) => event),
    ['hold', 'settle'],
    'only the successful bound resume command may release the fresh hold',
  );
  assert.equal(app.autoplayState.held, true);
});

test('a resume that starts playback then fails receives a same-binding recovery pause', async (t) => {
  const resumeFailure = 'Synthetic resume capability failed after starting playback';
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    capabilityDispatch(request, tools) {
      if (request.capability !== 'playback' || request.command !== 'resume') return null;
      const clocks = tools.getPlaybackClocks();
      const detail = {
        time: clocks.audioTime,
        audioT: clocks.audioTime,
        chartT: clocks.chartTime,
        duration: 120,
      };
      tools.emit('song:play', detail);
      tools.emit('song:resume', detail);
      return { status: 'failed', outcome: 'failed', reason: resumeFailure };
    },
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'resume-failure recovery startup');

  const playbackRequestsBefore = app.capabilityRequests.length;
  buttonByText(
    app.document.querySelector('#lh-player-review-overlay'),
    'Play preview (2s before + 2s after)',
  ).click();
  await waitFor(() => new RegExp(resumeFailure).test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'resume-failure recovery pause');

  const previewRequests = app.capabilityRequests.slice(playbackRequestsBefore)
    .filter((request) => request.capability === 'playback');
  const resumeIndex = previewRequests.findIndex(({ command }) => command === 'resume');
  const recoveryPause = previewRequests.slice(resumeIndex + 1)
    .find(({ command }) => command === 'pause');
  assert.ok(resumeIndex >= 0);
  assert.ok(recoveryPause, 'playback that actually started must be stopped after wrapper failure');
  assert.deepEqual(
    {
      sessionId: recoveryPause.args.sessionId,
      targetId: recoveryPause.args.targetId,
    },
    {
      sessionId: 'synthetic-session-1',
      targetId: 'synthetic-target-1',
    },
  );
  assert.equal(app.playbackState, 'paused');
});

test('ordinary song loading outside Player Review does not claim the autoplay gate', async (t) => {
  const app = await launch(() => null);
  t.after(() => app.close());

  app.window.feedBack.emit('song:loading', {
    filename: 'Ordinary/Ordinary Song.feedpak',
    arrangement: 0,
  });

  assert.deepEqual(
    app.autoplayState.actions,
    [],
    'Library Doctor must leave ordinary core playback autoplay ownership untouched',
  );
  assert.equal(app.autoplayState.held, false);
});

test('Player Review binds transport commands and suspends on another arrangement', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'bound issue landing');

  const boundRequests = app.capabilityRequests.filter((request) => (
    request.capability === 'playback'
      && (request.command === 'pause' || request.command === 'seek')
  ));
  assert.ok(boundRequests.length >= 2);
  assert.ok(boundRequests.every(({ args }) => (
    args.sessionId === 'synthetic-session-1'
      && args.targetId === 'synthetic-target-1'
  )), 'every post-load pause and seek must target the exact bound playback session');

  const seekCount = app.capabilityRequests.filter((request) => (
    request.capability === 'playback' && request.command === 'seek'
  )).length;
  app.window.feedBack.emit('song:loading', {
    filename: result.package,
    arrangement: 1,
  });
  await waitFor(() => /loaded another song or arrangement/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'different-arrangement suspension');

  assert.equal(
    app.capabilityRequests.filter((request) => (
      request.capability === 'playback' && request.command === 'seek'
    )).length,
    seekCount,
    'an unexpected arrangement must never receive the review issue seek',
  );
  assert.equal(app.document.querySelector('#lh-player-review-overlay').hidden, true);
  assert.equal(app.document.querySelector('#lh-player-review-timeline-overlay').hidden, true);
});

test('a superseded Player Review open restores Resume without a late seek or false success', async (t) => {
  const startGate = deferred();
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    capabilityDispatch(request) {
      if (request.capability === 'playback' && request.command === 'start') {
        return startGate.promise;
      }
      return null;
    },
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  const action = app.document.querySelector('.lh-reviewed-action');
  const openButton = buttonByText(action, 'Review in Player');
  openButton.click();
  await waitFor(() => app.capabilityRequests.some((request) => (
    request.capability === 'playback' && request.command === 'start'
  )), 'in-flight Player Review start');
  assert.equal(openButton.disabled, true);
  assert.equal(openButton.textContent, 'Opening FeedBack Player…');

  app.window.feedBack.emit('song:loading', {
    filename: result.package,
    arrangement: 1,
  });
  startGate.resolve({ status: 'handled', outcome: 'handled', payload: {} });

  await waitFor(() => openButton.disabled === false, 'restored Player Review action');
  assert.equal(openButton.textContent, 'Resume Player Review');
  assert.match(
    action.querySelector('.lh-reviewed-region')?.textContent || '',
    /Player Review opening was interrupted/,
  );
  assert.doesNotMatch(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
    /Paused at the current issue/,
  );
  assert.equal(
    app.capabilityRequests.some((request) => (
      request.capability === 'playback' && request.command === 'seek'
    )),
    false,
    'a superseded start must not land a delayed issue seek',
  );
  assert.equal(app.document.querySelector('#lh-player-review-overlay').hidden, true);
  assert.equal(app.document.querySelector('#lh-player-review-timeline-overlay').hidden, true);
});

test('Player Review recovery pauses playback after a post-resume preview failure', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'preview recovery startup');

  const playbackRequestsBefore = app.capabilityRequests.length;
  app.window.highway.getTime = () => {
    throw new Error('Synthetic highway clock failed after preview resume');
  };
  buttonByText(
    app.document.querySelector('#lh-player-review-overlay'),
    'Play preview (2s before + 2s after)',
  ).click();

  await waitFor(() => /Synthetic highway clock failed/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'post-resume preview failure recovery');
  const previewRequests = app.capabilityRequests.slice(playbackRequestsBefore)
    .filter((request) => request.capability === 'playback');
  const resumeIndex = previewRequests.findIndex(({ command }) => command === 'resume');
  assert.ok(resumeIndex >= 0, 'the injected failure must happen after playback resumed');
  assert.ok(
    previewRequests.slice(resumeIndex + 1).some(({ command, args }) => (
      command === 'pause'
        && args.sessionId === 'synthetic-session-1'
        && args.targetId === 'synthetic-target-1'
    )),
    'a post-resume failure must issue a same-binding recovery pause',
  );
  assert.equal(app.playbackState, 'paused');
});

test('an external seek cancels Player Review preview without recovery pause or return seek', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'external-seek preview startup');

  const playbackRequestsBefore = app.capabilityRequests.length;
  buttonByText(
    app.document.querySelector('#lh-player-review-overlay'),
    'Play preview (2s before + 2s after)',
  ).click();
  await waitFor(() => /Playing exactly two chart seconds/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'actively playing preview');

  const externalTarget = 9.25;
  app.window.feedBack.emit('song:seek', {
    from: app.playbackTime,
    to: externalTarget,
    reason: 'player-ui:timeline-scrub',
  });
  await waitFor(() => {
    const button = buttonByText(
      app.document.querySelector('#lh-player-review-overlay'),
      'Play preview (2s before + 2s after)',
    );
    return button && !button.disabled;
  }, 'external seek cancellation settlement');

  const previewRequests = app.capabilityRequests.slice(playbackRequestsBefore)
    .filter((request) => request.capability === 'playback');
  assert.match(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
    /preview stopped because the Player was moved/,
  );
  const resumeIndex = previewRequests.findIndex(({ command }) => command === 'resume');
  assert.ok(resumeIndex >= 0);
  assert.deepEqual(
    previewRequests.slice(resumeIndex + 1)
      .filter(({ command }) => command !== 'inspect')
      .map(({ command }) => command),
    [],
    'supersession may inspect state but must not mutate playback after the user action',
  );
  assert.equal(
    previewRequests.some(({ command, args }) => (
      command === 'seek' && /:preview-return$/.test(args.reason || '')
    )),
    false,
  );
  assert.equal(app.playbackState, 'playing');
  assert.ok(Math.abs(app.playbackTime - externalTarget) < 0.001);
});

test('Sister Mercurial transport stays pinned to its issue across core clock transients', async (t) => {
  const issueTime = 30.618;
  const duration = 235.071;
  const sisterPackage = 'The Night Flight Orchestra/The Night Flight Orchestra - Sister Mercurial.feedpak';
  const sisterResult = {
    ...result,
    package: sisterPackage,
    title: 'Sister Mercurial',
    artist: 'The Night Flight Orchestra',
  };
  const sisterCandidate = {
    ...candidates[0],
    time: issueTime,
    previous: { ...candidates[0].previous, time: 30.387 },
    next: { ...candidates[0].next, time: 30.849 },
  };
  const context = playerContext([sisterCandidate]);
  context.package = sisterPackage;
  context.playback_filename = sisterPackage;
  context.inspection.package = sisterPackage;

  const seekCalls = [];
  const previewEnds = [];
  let seekResultCount = 0;
  let playbackTools = null;

  function stateSnapshot(state, media) {
    return {
      state,
      transport: { isPlaying: state === 'playing' },
      media,
    };
  }

  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [sisterResult] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    playbackDuration: duration,
    playbackArrangement: 'Lead',
    capabilityDispatch(request, tools) {
      playbackTools = tools;
      if (request.capability !== 'playback') return null;
      if (request.command === 'seek') {
        const target = Number(request.args.time);
        const before = tools.getPlaybackClocks();
        const snapshotKind = seekResultCount % 2 === 0
          ? 'landed-chart-is-media'
          : 'null-partial-snapshot';
        seekResultCount += 1;
        seekCalls.push({
          action: String(request.args.reason || '').split(':').at(-1),
          target,
          snapshotKind,
        });

        // The media clock lands first. The real highway clock can still expose
        // its previous frame briefly, while song:seek itself only has from/to.
        tools.setPlaybackClocks({ audioTime: target, chartTime: before.chartTime });
        tools.emitCoreEvent('song:seek', {
          from: before.audioTime,
          to: target,
          reason: request.args.reason,
        });

        const media = snapshotKind === 'null-partial-snapshot'
          ? {
            currentTime: null,
            mediaTime: null,
            chartTime: null,
            duration: null,
            playbackRate: null,
          }
          : {
            currentTime: target,
            mediaTime: target,
            // Core playback currently writes the landed media time here; it is
            // not an authoritative new chart-to-media offset observation.
            chartTime: target,
            duration,
            playbackRate: 1,
          };
        return {
          status: 'completed',
          outcome: 'handled',
          payload: {
            landedTime: target,
            snapshot: { state: stateSnapshot('paused', media) },
          },
        };
      }
      if (request.command === 'resume') {
        const clocks = tools.getPlaybackClocks();
        const eventDetail = {
          time: clocks.audioTime,
          audioT: clocks.audioTime,
          chartT: clocks.chartTime,
        };
        tools.emitCoreEvent('song:play', eventDetail);
        tools.emitCoreEvent('song:resume', eventDetail);
        if (/four-second Library Doctor issue preview/.test(request.args.reason || '')) {
          const end = clocks.audioTime + 4.01;
          previewEnds.push(end);
          tools.setPlaybackClocks({ audioTime: end, chartTime: end });
          tools.emitCoreEvent('song:position-changed', {
            time: end,
            audioT: end,
            chartT: end,
            duration,
            playbackRate: 1,
          });
        }
        return {
          status: 'playing',
          outcome: 'handled',
          payload: {
            state: stateSnapshot('playing', {
              currentTime: clocks.audioTime,
              mediaTime: clocks.audioTime,
              chartTime: clocks.chartTime,
              duration,
              playbackRate: 1,
            }),
          },
        };
      }
      return null;
    },
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(context);
      }
      return null;
    },
  });
  t.after(() => app.close());

  function reviewOverlay() {
    return app.document.querySelector('#lh-player-review-overlay');
  }

  function timelineState() {
    const timeline = app.document.querySelector('#lh-player-review-timeline-overlay');
    return {
      timeline,
      range: timeline.querySelector('.lh-player-review-timeline-range'),
      current: timeline.querySelector('.lh-player-review-timeline-time'),
      issue: timeline.querySelector('.lh-player-review-timeline-labels span:last-child'),
      marker: timeline.querySelector('.lh-player-review-timeline-track .lh-player-review-timeline-marker'),
    };
  }

  async function waitForTransportStatus(pattern, label) {
    await waitFor(() => pattern.test(
      app.document.querySelector('.lh-player-review-status')?.textContent || '',
    ), label);
  }

  async function playPreview(label) {
    const completedBefore = seekCalls.filter(({ action }) => action === 'preview-return').length;
    await waitFor(() => {
      const button = buttonByText(reviewOverlay(), 'Play preview (2s before + 2s after)');
      return button && !button.disabled;
    }, `${label} preview button`);
    buttonByText(reviewOverlay(), 'Play preview (2s before + 2s after)').click();
    await waitFor(
      () => seekCalls.filter(({ action }) => action === 'preview-return').length > completedBefore
        && /Preview finished/.test(
          app.document.querySelector('.lh-player-review-status')?.textContent || '',
        ),
      `${label} preview completion`,
    );
  }

  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitForTransportStatus(/Paused at the current issue/, 'Sister Mercurial issue landing');

  const ready = app.coreEvents.find(({ name }) => name === 'song:ready');
  const loaded = app.coreEvents.find(({ name }) => name === 'song:loaded');
  assert.equal(loaded.detail.filename, sisterPackage);
  assert.equal(loaded.detail.arrangement, 'Lead');
  assert.ok(
    app.coreEvents.indexOf(loaded) < app.coreEvents.indexOf(ready),
    'song:loaded identity must precede the identity-free song:ready signal',
  );
  assert.deepEqual(ready.detail, { hasPhraseData: true });
  assert.equal('filename' in ready.detail, false);
  assert.equal('time' in ready.detail, false);
  assert.deepEqual(
    {
      filename: app.window.feedBack.currentSong.filename,
      arrangement: app.window.feedBack.currentSong.arrangement,
      arrangementIndex: app.window.feedBack.currentSong.arrangementIndex,
    },
    { filename: sisterPackage, arrangement: 'Lead', arrangementIndex: 0 },
  );

  // Preview once before touching Jump, then press Jump twice quickly enough
  // to overlap the deliberately transient highway-clock lag.
  await playPreview('before Jump');
  for (let index = 0; index < 2; index += 1) {
    const jumpsBefore = seekCalls.filter(({ action }) => action === 'jump-to-issue').length;
    buttonByText(reviewOverlay(), 'Jump to issue').click();
    await waitFor(
      () => seekCalls.filter(({ action }) => action === 'jump-to-issue').length > jumpsBefore
        && /Paused at the current issue/.test(
          app.document.querySelector('.lh-player-review-status')?.textContent || '',
        ),
      `idempotent Jump ${index + 1}`,
    );
  }
  await playPreview('after repeated Jump');

  // Let the synthetic highway catch up without emitting another position
  // event. Player Review must not have derived or retained an offset from the
  // temporary disagreement.
  playbackTools.setPlaybackClocks({ chartTime: app.playbackTime });
  assert.ok(Math.abs(app.playbackChartTime - app.playbackTime) < 0.001);

  const jumpTargets = seekCalls
    .filter(({ action }) => action === 'jump-to-issue')
    .map(({ target }) => target);
  assert.deepEqual(jumpTargets, [issueTime, issueTime, issueTime]);

  const previewTargets = seekCalls
    .filter(({ action }) => action === 'preview-start' || action === 'preview-return')
    .map(({ action, target }) => [action, target]);
  assert.deepEqual(previewTargets, [
    ['preview-start', issueTime - 2],
    ['preview-return', issueTime],
    ['preview-start', issueTime - 2],
    ['preview-return', issueTime],
  ]);
  assert.deepEqual(previewEnds.map((value) => Number(value.toFixed(3))), [
    Number((issueTime + 2.01).toFixed(3)),
    Number((issueTime + 2.01).toFixed(3)),
  ]);
  assert.ok(seekCalls.some(({ snapshotKind }) => snapshotKind === 'landed-chart-is-media'));
  assert.ok(seekCalls.some(({ snapshotKind }) => snapshotKind === 'null-partial-snapshot'));
  assert.ok(Math.abs(app.playbackTime - issueTime) < 0.001);

  const timeline = timelineState();
  assert.equal(timeline.timeline.querySelectorAll('.lh-player-review-timeline-range').length, 1);
  assert.equal(Number(timeline.range.max), duration);
  assert.deepEqual({
    wholeSongValue: Number(timeline.range.value),
    currentLabel: timeline.current.textContent,
    issueLabel: timeline.issue.textContent,
    issueMarkerPercent: Number(Number.parseFloat(timeline.marker.style.left).toFixed(6)),
  }, {
    wholeSongValue: issueTime,
    currentLabel: '0:30.62 / 3:55.1',
    issueLabel: 'Issue 0:30.62',
    issueMarkerPercent: Number(((issueTime / duration) * 100).toFixed(6)),
  }, 'the timeline playhead, labels, and issue marker must all remain pinned to 30.618s');
});

test('Player Review waits for continuing timed-out playback handlers in strict order', async (t) => {
  const completed = [];
  const timeout = {
    status: 'failed',
    outcome: 'failed',
    reason: 'Handler core.playback timed out after 250 ms',
  };
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    capabilityDispatch(request, {
      emit, emitCoreEvent, emitPlaybackLoaded, navigate, setPlaybackTime,
    }) {
      if (request.capability !== 'playback' || request.command === 'inspect') return null;
      if (request.command === 'start') {
        assert.equal('startTime' in request.args, false);
        emit('song:loading', {
          filename: request.args.target.filename,
          arrangement: request.args.arrangement,
        });
        navigate('player');
        setTimeout(() => {
          completed.push('start');
          emitPlaybackLoaded();
          emitCoreEvent('song:ready', { hasPhraseData: true });
        }, 35);
        return timeout;
      }
      if (request.command === 'pause') {
        setTimeout(() => {
          completed.push('pause');
          emit('song:pause', { time: 0, audioT: 0, chartT: 0, duration: 120 });
        }, 35);
        return timeout;
      }
      if (request.command === 'seek') {
        const target = Number(request.args.time);
        setTimeout(() => {
          completed.push('seek');
          setPlaybackTime(target);
          emit('song:seek', {
            from: 0,
            to: target,
            reason: request.args.reason,
          });
          emit('song:position-changed', {
            time: target,
            audioT: target,
            chartT: target,
            duration: 120,
          });
        }, 35);
        return timeout;
      }
      return null;
    },
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();

  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'serialized timeout landing');
  assert.deepEqual(completed, ['start', 'pause', 'seek']);
  assert.equal(app.playbackTime, 2);
  assert.equal(app.playbackState, 'paused');
  assert.doesNotMatch(app.document.querySelector('.lh-player-review-status').textContent, /timed out/i);
});

test('timeline drag keeps its local position and commits one precise seek', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => /Paused at the current issue/.test(
    app.document.querySelector('.lh-player-review-status')?.textContent || '',
  ), 'timeline drag startup');

  const timeline = app.document.querySelector('#lh-player-review-timeline-overlay');
  const full = timeline.querySelector('.lh-player-review-timeline-range');
  assert.equal(timeline.querySelectorAll('.lh-player-review-timeline-range').length, 1);
  app.window.feedBack.emit('song:resume', {
    time: 2,
    audioT: 2,
    chartT: 2,
    duration: 120,
  });
  const seeksBefore = app.capabilityRequests.filter(
    (request) => request.capability === 'playback' && request.command === 'seek',
  ).length;
  full.dispatchEvent(new app.window.MouseEvent('pointerdown', { bubbles: true }));
  full.value = '50.25';
  full.dispatchEvent(new app.window.Event('input', { bubbles: true }));
  app.window.feedBack.emit('song:position-changed', {
    time: 8,
    audioT: 8,
    chartT: 8,
    duration: 120,
  });
  assert.equal(timeline.querySelector('.lh-player-review-timeline-range'), full);
  assert.equal(Number(full.value), 50.25);
  full.dispatchEvent(new app.window.Event('change', { bubbles: true }));

  await waitFor(() => Math.abs(app.playbackTime - 50.25) < 0.001, 'whole-song timeline commit');
  const timelineSeeks = app.capabilityRequests.filter(
    (request) => request.capability === 'playback'
      && request.command === 'seek'
      && /:timeline$/.test(request.args.reason || ''),
  );
  assert.equal(timelineSeeks.length, 1);
  assert.equal(timelineSeeks[0].args.time, 50.25);
  assert.equal(timeline.querySelector('.lh-player-review-timeline-range'), full);
  assert.equal(app.playbackState, 'playing');

  buttonByText(timeline, '+0.1s').click();
  await waitFor(() => Math.abs(app.playbackTime - 50.35) < 0.001, 'sub-second nudge commit');
  const committedTimelineSeeks = app.capabilityRequests.filter(
    (request) => request.capability === 'playback'
      && request.command === 'seek'
      && /:timeline$/.test(request.args.reason || ''),
  );
  assert.deepEqual(committedTimelineSeeks.map((request) => request.args.time), [50.25, 50.35]);
  assert.equal(app.playbackState, 'playing');
});

test('Player Review treats the core 250ms start timeout as loading and waits for song ready', async (t) => {
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    capabilityDispatch(request, {
      emit, emitCoreEvent, emitPlaybackLoaded, navigate,
    }) {
      if (request.capability !== 'playback' || request.command !== 'start') return null;
      emit('song:loading', {
        filename: request.args.target.filename,
        arrangement: request.args.arrangement,
      });
      navigate('player');
      setTimeout(() => {
        emitPlaybackLoaded();
        emitCoreEvent('song:ready', { hasPhraseData: true });
      }, 5);
      return {
        status: 'failed',
        outcome: 'failed',
        reason: 'Handler core.playback timed out after 250 ms',
      };
    },
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        return jsonResponse(playerContext([candidates[0]]));
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(
    () => /Paused at the current issue/.test(app.document.querySelector('.lh-player-review-status')?.textContent || ''),
    'ready event after timed-out capability wrapper',
  );
  assert.doesNotMatch(app.document.querySelector('.lh-player-review-status').textContent, /timed out/i);
  assert.equal(app.document.querySelector('.screen.active').id, 'player');
});

test('Player Review applies a partial group and pins Undo or Finalize before another Apply', async (t) => {
  let contextCalls = 0;
  let restored = false;
  const appliedResult = {
    applied: true,
    action: 'repair',
    outcome: 'success',
    package: result.package,
    title: result.title,
    artist: result.artist,
    backup_id: '20260816-120000-abcdef123456',
    undo_available: true,
    change_kind: 'reviewed_decisions',
    change_count: 1,
    removed_count: 0,
    musical_positions: 1,
    item_name: 'reviewed HO/PO decision',
    player_result: 'The Highway now uses the reviewed technique.',
    user_value: 'The author selected the technique.',
    file_handling: { summary: 'Validated with one retained recovery backup.' },
    report: { title: result.title, artist: result.artist, findings: [] },
  };
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [result] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
    route(request) {
      if (request.key.endsWith('/reviewed-repair/player-context')) {
        contextCalls += 1;
        return jsonResponse(playerContext(
          contextCalls === 1 || restored ? [candidates[0]] : [],
        ));
      }
      if (request.key.endsWith('/reviewed-repair/preview')) {
        return jsonResponse({
          plan_id: 'd'.repeat(64),
          available: true,
          changing_count: 1,
          skipped_count: 0,
          unresolved_count: 0,
        });
      }
      if (request.key.endsWith('/reviewed-repair/apply')) {
        return jsonResponse(appliedResult);
      }
      if (request.key.endsWith('/repair/restore')) {
        restored = true;
        return jsonResponse({
          action: 'restore',
          outcome: 'restored',
          package: result.package,
          title: result.title,
          artist: result.artist,
          backup_id: appliedResult.backup_id,
          change_kind: 'reviewed_decisions',
          change_count: 1,
          removed_count: 0,
          musical_positions: 1,
          item_name: 'reviewed HO/PO decision',
          file_handling: { summary: 'The exact original song data was restored.' },
        });
      }
      return null;
    },
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  buttonByText(app.document.querySelector('.lh-reviewed-action'), 'Review in Player').click();
  await waitFor(() => {
    const input = app.document.querySelector('#lh-player-review-overlay input[value="remove_hopo"]');
    return input && !input.disabled;
  }, 'interactive Player Review issue');
  app.document.querySelector('#lh-player-review-overlay input[value="remove_hopo"]').click();
  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Accept & Next issue').click();
  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Apply accepted changes').click();
  await waitFor(
    () => buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Confirm Apply'),
    'exact partial preview',
  );
  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Confirm Apply').click();
  await waitFor(
    () => buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Undo applied group'),
    'pinned recovery controls',
  );

  const mutationRequests = app.requests.map((request) => request.key);
  const playbackRelease = mutationRequests.lastIndexOf('/api/plugins/library_doctor/playback');
  const applyIndex = mutationRequests.indexOf('/api/plugins/library_doctor/reviewed-repair/apply');
  assert.ok(playbackRelease >= 0 && playbackRelease < applyIndex);
  assert.ok(app.capabilityRequests.some(
    (request) => request.capability === 'playback' && request.command === 'stop',
  ));
  assert.match(app.document.querySelector('.lh-player-review-recovery').textContent, /checkpoint/);
  assert.ok(buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Finalize applied group'));

  await waitFor(() => {
    const undo = buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Undo applied group');
    return undo && !undo.disabled;
  }, 'completed partial apply before Undo');
  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Undo applied group').click();
  await waitFor(
    () => app.document.querySelector('#lh-player-review-overlay input[value="remove_hopo"]')
      && /exact original song data was restored/i.test(
        app.document.querySelector('.lh-player-review-status')?.textContent || '',
      ),
    'review queue rebuilt after Undo',
  );
  assert.equal(app.document.querySelector('.lh-player-review-recovery'), null);
  assert.match(app.document.querySelector('.lh-player-review-status').textContent, /exact original song data was restored/i);

  buttonByText(app.document.querySelector('#lh-player-review-overlay'), 'Return to Library Doctor').click();
  await waitFor(
    () => app.document.querySelector('.screen.active')?.id === 'plugin-library_doctor',
    'Return to Library Doctor',
  );
  assert.equal(app.document.querySelector('#lh-player-review-overlay').hidden, true);
});

test('outside-library reviewed findings show a clear unavailable notice without controls', async (t) => {
  const externalResult = {
    ...result,
    features: {
      ...result.features,
      player_review: {
        available: false,
        reason: 'outside_configured_library',
        message: 'Manual Player Review is unavailable because this song is outside the configured song library. Automatic and standard repairs remain available.',
      },
    },
  };
  const app = await launchLibraryDoctor({
    status,
    results: { total: 1, items: [externalResult] },
    reviewedRepairs: {
      schema: 'library_doctor.reviewed_repair_catalog.v1',
      items: [reviewedDefinition],
    },
    reviewedCandidates: candidates,
  });
  t.after(() => app.close());
  app.document.querySelector('.lh-package').open = true;
  const action = app.document.querySelector('.lh-reviewed-action');
  assert.match(action.textContent, /outside the configured song library/);
  assert.equal(action.querySelector('button'), null);
});
