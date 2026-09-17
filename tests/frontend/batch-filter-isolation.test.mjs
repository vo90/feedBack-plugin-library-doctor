import assert from 'node:assert/strict';
import test from 'node:test';

import { launchLibraryDoctor, waitFor } from './helpers/library-doctor-app.mjs';

function resultRequests(app) {
  return app.requests.filter(({ key }) => key.startsWith('/api/plugins/library_doctor/results?'));
}

test('batch outcome chips and scan filters keep requests, results and pressed states isolated', async (t) => {
  const target = { kind: 'library', label: 'synthetic' };
  const app = await launchLibraryDoctor({
    status: {
      stage: 'idle', running: false, scan_current: true, target,
      summary: { total: 3, warnings: 3 }, last_scan: { complete: true, target },
      batch: {
        phase: 'completed', running: false,
        result: {
          id: 'synthetic-batch', outcome: 'complete', completed_count: 3,
          successful_count: 1, failed_count: 1, skipped_count: 1, remaining_count: 0,
          outcomes: [
            { package: 'Failed.feedpak', title: 'Failed song', outcome: 'failed', message: 'Synthetic failure' },
            { package: 'Skipped.feedpak', title: 'Skipped song', outcome: 'skipped', message: 'Synthetic refusal' },
            { package: 'Done.feedpak', title: 'Repaired song', outcome: 'success', change_count: 1 },
          ],
        },
      },
    },
  });
  t.after(() => app.close());
  const batch = app.document.querySelector('#lh-batch-result');
  const originalRequests = resultRequests(app).length;
  assert.equal(batch.querySelector('[data-filter]'), null, 'outcome chips use their own namespace');
  for (const [filter, expectedCount] of [['failed', 1], ['skipped', 1], ['all', 3]]) {
    const chip = batch.querySelector(`[data-outcome-filter="${filter}"]`);
    chip.click();
    assert.equal(chip.getAttribute('aria-pressed'), 'true');
    assert.equal(batch.querySelectorAll('.lh-batch-list > li').length, expectedCount);
    assert.equal(resultRequests(app).length, originalRequests, 'local outcome filtering must not fetch scan results');
    assert.equal(app.document.querySelector('#lh-filters [data-filter="problems"]').getAttribute('aria-pressed'), 'true');
  }
  const failed = batch.querySelector('[data-outcome-filter="failed"]');
  failed.click();
  for (const selector of [
    '#lh-filters [data-filter="warnings"]',
    '#lh-more-filters [data-filter="no_preview"]',
    '#lh-overview [data-filter="all"]',
  ]) {
    const scanFilter = app.document.querySelector(selector);
    const previousCount = resultRequests(app).length;
    scanFilter.click();
    await waitFor(() => resultRequests(app).length === previousCount + 1, 'scan filter request');
    assert.equal(failed.getAttribute('aria-pressed'), 'true', 'scan filtering cannot change outcome selection');
    assert.equal(batch.querySelector('[data-outcome-filter="all"]').getAttribute('aria-pressed'), 'false');
    assert.equal(batch.querySelectorAll('.lh-batch-list > li').length, 1);
    assert.match(batch.querySelector('.lh-batch-list').textContent, /Failed song/);
    const url = new URL(resultRequests(app).at(-1).key, 'http://localhost');
    assert.equal(url.searchParams.get('filter'), scanFilter.dataset.filter);
  }
  assert.equal(resultRequests(app).some(({ key }) => /filter=(failed|skipped|attention)/.test(key)), false);
});
