import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';

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
