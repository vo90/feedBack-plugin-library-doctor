import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

import {
  completeStatus,
  firstRunStatus,
  openSyntheticLibraryDoctor,
  repairPlan,
  syntheticReport,
} from './helpers/synthetic-nightly.mjs';

async function expectNoAccessibilityViolations(page) {
  const result = await new AxeBuilder({ page })
    .include('#plugin-library_doctor')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(result.violations, JSON.stringify(result.violations, null, 2)).toEqual([]);
}

const reviewedDefinition = {
  adapter_id: 'review.hopo-techniques',
  title: 'Review hammer-ons and pull-offs',
  description: 'Compare same-string context and choose explicitly.',
  safety: 'review_required',
  trigger_rule_codes: ['review.same-fret-hopo'],
  mutable_fields: ['ho', 'po', 'tp'],
  context_schema: 'library_doctor.reviewed_hopo_context.v1',
  candidate_limit: 2000,
  audio_support: true,
  test_owner: 'review.hopo-techniques',
  decisions: [
    ['set_hammer_on', 'Use hammer-on'],
    ['set_pull_off', 'Use pull-off'],
    ['convert_to_tap', 'Convert to tap'],
    ['remove_hopo', 'Remove HO/PO'],
    ['leave_unchanged', 'Leave unchanged'],
  ].map(([name, label]) => ({
    name, label, description: `${label} description.`, confirmation: `${label}?`,
  })),
};

const reviewedCandidate = {
  candidate_id: `hopo-${'d'.repeat(24)}`,
  member_path: 'arrangements/lead.json',
  location: 'arrangements/lead.json.notes[1]',
  context_kind: 'standalone_note',
  stream: 'Top-level arrangement',
  time: 2,
  string: 0,
  fret: 5,
  techniques: { hammer_on: true, pull_off: false, tap: false },
  reasons: ['same_fret'],
  trigger_codes: ['review.same-fret-hopo'],
  predecessor_state: 'usable',
  next_state: 'usable',
  previous: { time: 1, fret: 5, writable: true },
  next: { time: 3, fret: 8, writable: true },
  previous_gap_seconds: 1,
  next_gap_seconds: 1,
  outgoing_match: true,
  blockers: [],
  decision_names: ['set_hammer_on', 'set_pull_off', 'convert_to_tap', 'remove_hopo', 'leave_unchanged'],
};

const reviewedReport = {
  ...syntheticReport,
  package: 'synthetic/reviewed-hopo.feedpak',
  title: 'Reviewed HOPO Song',
  features: {
    preview_declared: true,
    preview_available: true,
    repair_scan_current: true,
    repair_eligibility: {},
  },
  findings: [{
    code: 'review.same-fret-hopo',
    severity: 'info',
    category: 'authoring_review',
    message: 'One same-fret HO/PO needs an author decision.',
    affected_count: 1,
    rule: {
      title: 'Same-fret HO/PO needs review',
      area: 'Playability',
      confidence: 'medium',
      repairability: 'review_required',
      guidance: 'Open Reviewed repair.',
    },
  }],
};

function reviewedInspection() {
  return {
    schema: 'library_doctor.reviewed_repair_inspection.v1',
    adapter_id: reviewedDefinition.adapter_id,
    package: reviewedReport.package,
    title: reviewedDefinition.title,
    available: true,
    candidate_count: 1,
    candidates: [reviewedCandidate],
    decision_definitions: reviewedDefinition.decisions,
  };
}

const reviewedPlan = {
  plan_id: 'e'.repeat(64),
  available: true,
  safety: 'review_required',
  title: reviewedDefinition.title,
  changing_count: 1,
  skipped_count: 0,
  unresolved_count: 0,
  remaining_review_count: 0,
  decision_counts: { remove_hopo: 1 },
  player_result: 'Only the selected HO/PO fields change.',
  user_value: 'The author chose the intended result.',
  file_handling: { summary: 'A complete candidate is validated and Undo is retained.' },
};


test('first-run shell exposes the safe action and scoped live status', async ({ page }) => {
  const { root, requests } = await openSyntheticLibraryDoctor(page);

  await expect(page.locator('script[data-plugin-id="library_doctor"]')).toHaveAttribute('type', 'module');
  await expect(root.locator('#lh-module-status')).toBeHidden();
  expect(requests.some((request) => /GET .*\/src\/app\.js/.test(request))).toBe(true);
  await expect(root.locator('#lh-health-workspace')).toHaveAttribute('data-view-state', 'first_run');
  await expect(root.locator('#lh-scan-options')).toHaveAttribute('open', '');
  await expect(root.getByRole('button', { name: 'Scan my library' })).toBeVisible();
  await expect(root.locator('#lh-results-section')).toBeHidden();
  await expect(root.locator('#lh-status')).not.toHaveAttribute('aria-live', /.+/);
  await expect(root.locator('#lh-scan-live')).toHaveAttribute('role', 'status');
  await expect(root.locator('#lh-scan-live')).toHaveAttribute('aria-live', 'polite');
  await expect(root.locator('#lh-scan-live')).toHaveAttribute('aria-atomic', 'true');
  expect(requests.some((request) => request.startsWith('POST ') && request.includes('/scan'))).toBe(false);
  await expectNoAccessibilityViolations(page);
});

test('result filtering and repair review/cancel never apply a package', async ({ page }) => {
  const { root, requests } = await openSyntheticLibraryDoctor(page, {
    status: completeStatus,
    results: { total: 1, limit: 50, offset: 0, items: [syntheticReport] },
  });

  await root.getByRole('button', { name: 'May affect FeedBack' }).click();
  await expect(root.getByRole('button', { name: 'May affect FeedBack' })).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => requests.some((request) => request.includes('filter=warnings'))).toBe(true);

  await root.locator('.lh-package > summary').click();
  await root.getByRole('button', { name: 'Review safe fix' }).click();
  await expect(root.getByRole('button', { name: 'Apply safe repair' })).toBeVisible();
  await root.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(root.getByRole('button', { name: 'Review safe fix' })).toBeVisible();
  await expect(root.getByRole('button', { name: 'Apply safe repair' })).toHaveCount(0);
  expect(requests.some((request) => request.includes('/repair/apply'))).toBe(false);
});

test('reviewed HOPO journey can be cancelled without any mutation', async ({ page }) => {
  const { root, requests } = await openSyntheticLibraryDoctor(page, {
    status: { ...completeStatus, summary: { total: 1, errors: 0, warnings: 0, reviews: 1 } },
    results: { total: 1, limit: 50, offset: 0, items: [reviewedReport] },
    pluginRoute: async ({ path, request, route }) => {
      if (path === '/reviewed-repairs') {
        await route.fulfill({ json: { items: [reviewedDefinition] } });
        return true;
      }
      if (path === '/reviewed-repair/inspect' && request.method() === 'POST') {
        await route.fulfill({ json: reviewedInspection() });
        return true;
      }
      return false;
    },
  });

  await root.locator('.lh-package > summary').click();
  await root.getByRole('button', { name: 'Open Reviewed repair' }).click();
  await expect(root.getByRole('heading', { name: /Candidate 1 of 1/ })).toBeFocused();
  await expect(root.locator('.lh-reviewed-choice input:checked')).toHaveCount(0);
  await expect(root.getByRole('button', { name: 'Preview selected changes' })).toBeDisabled();
  await root.getByRole('radio', { name: /Remove HO\/PO/ }).check();
  await root.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(root.getByRole('button', { name: 'Open Reviewed repair' })).toBeFocused();
  expect(requests.some((item) => item.includes('/reviewed-repair/preview'))).toBe(false);
  expect(requests.some((item) => item.includes('/reviewed-repair/apply'))).toBe(false);
});

test('reviewed HOPO journey previews, confirms, applies, and offers Undo', async ({ page }) => {
  const receipt = {
    ...reviewedPlan,
    applied: true,
    outcome: 'success',
    backup_id: 'synthetic-reviewed-backup',
    undo_available: true,
    change_kind: 'reviewed_decisions',
    change_count: 1,
    removed_count: 0,
    musical_positions: 1,
    item_name: 'reviewed HO/PO decision',
    package: reviewedReport.package,
    report: { title: reviewedReport.title, artist: reviewedReport.artist },
    file_handling: {
      summary: 'A complete candidate passed validation and recovery was retained.',
      backup_retained: true,
    },
  };
  const { root, requests } = await openSyntheticLibraryDoctor(page, {
    status: { ...completeStatus, summary: { total: 1, errors: 0, warnings: 0, reviews: 1 } },
    results: { total: 1, limit: 50, offset: 0, items: [reviewedReport] },
    pluginRoute: async ({ path, request, route }) => {
      if (path === '/reviewed-repairs') {
        await route.fulfill({ json: { items: [reviewedDefinition] } });
        return true;
      }
      if (path === '/reviewed-repair/inspect') {
        await route.fulfill({ json: reviewedInspection() });
        return true;
      }
      if (path === '/reviewed-repair/preview') {
        await route.fulfill({ json: reviewedPlan });
        return true;
      }
      if (path === '/reviewed-repair/apply') {
        await route.fulfill({ json: receipt });
        return true;
      }
      return false;
    },
  });

  await root.locator('.lh-package > summary').click();
  await root.getByRole('button', { name: 'Open Reviewed repair' }).click();
  await root.getByRole('radio', { name: /Remove HO\/PO/ }).check();
  await root.getByRole('button', { name: 'Preview selected changes' }).click();
  await expect(root.getByRole('heading', { name: 'Exact reviewed-repair preview' })).toBeFocused();
  await expect(root.locator('.lh-reviewed-preview')).toContainText('0 remain unresolved');
  await root.getByRole('button', { name: 'Confirm these reviewed changes' }).click();
  await expect(root.getByRole('button', { name: 'Apply reviewed changes' })).toBeFocused();
  await root.getByRole('button', { name: 'Apply reviewed changes' }).click();
  await expect(root.locator('#lh-repair-result')).toContainText('Change completed');
  await expect(root.getByRole('button', { name: 'Undo repair' })).toBeVisible();
  expect(requests.some((item) => item.includes('/reviewed-repair/preview'))).toBe(true);
  expect(requests.some((item) => item.includes('/reviewed-repair/apply'))).toBe(true);
});

test('keyboard workspace activation and batch confirmation remain reversible', async ({ page }) => {
  const { root, requests } = await openSyntheticLibraryDoctor(page, {
    status: completeStatus,
    results: { total: 1, limit: 50, offset: 0, items: [syntheticReport] },
    songs: {
      total: 1,
      songs: [{
        filename: 'synthetic/tool-song.feedpak',
        title: 'Tool Song',
        artist: 'Synthetic Artist',
      }],
    },
    batchReady: true,
  });

  const tools = root.getByRole('button', { name: 'Song tools', exact: true });
  await tools.focus();
  await page.keyboard.press('Enter');
  await expect(tools).toBeFocused();
  await expect(tools).toHaveAttribute('aria-pressed', 'true');
  await expect(root.getByText('Tool Song', { exact: true })).toBeVisible();

  await root.getByRole('button', { name: 'Library check', exact: true }).click();
  await root.getByRole('button', { name: 'Continue to confirmation' }).click();
  await expect(root.getByRole('button', { name: 'Apply batch repair' })).toBeFocused();
  await root.getByRole('button', { name: 'Go back' }).click();
  await expect(root.getByRole('button', { name: 'Continue to confirmation' })).toBeFocused();
  await expect(root.getByRole('button', { name: 'Apply batch repair' })).toHaveCount(0);
  expect(requests.some((request) => request.includes('/repair/batch/apply'))).toBe(false);
});

test('synthetic scan, repair, and Undo complete one browser journey', async ({ page }) => {
  let phase = 'first_run';
  let scanPolling = false;
  const healthyReport = {
    ...syntheticReport,
    findings: [],
    features: {
      ...syntheticReport.features,
      repair_eligibility: {},
    },
  };
  const healthyStatus = {
    ...completeStatus,
    summary: { total: 1, errors: 0, warnings: 0, reviews: 0 },
  };
  const runningStatus = {
    ...firstRunStatus,
    stage: 'scanning',
    running: true,
    total: 1,
    done: 0,
    target: { label: 'Synthetic library' },
  };
  const appliedReceipt = {
    ...repairPlan,
    id: 'synthetic-repair-receipt',
    package: syntheticReport.package,
    title: syntheticReport.title,
    artist: syntheticReport.artist,
    applied: true,
    outcome: 'success',
    backup_id: 'synthetic-backup-0001',
    undo_available: true,
    receipt_saved: true,
    cache_updated: true,
    report: healthyReport,
    file_handling: {
      ...repairPlan.file_handling,
      backup_created: true,
      backup_retained: true,
      backup_size_bytes: 1024,
    },
  };
  const restoredReceipt = {
    ...appliedReceipt,
    id: 'synthetic-restore-receipt',
    action: 'restore',
    outcome: 'restored',
    restored: true,
    undo_available: false,
    report: syntheticReport,
    file_handling: {
      summary: 'The saved original song data was restored at the same package path.',
      backup_retained: false,
      backup_removed: true,
    },
  };

  const statusForPhase = () => (
    phase === 'applied' ? healthyStatus : phase === 'first_run' ? firstRunStatus : completeStatus
  );
  const resultsForPhase = () => {
    if (phase === 'first_run') return { total: 0, limit: 50, offset: 0, items: [] };
    if (phase === 'applied') return { total: 0, limit: 50, offset: 0, items: [] };
    return { total: 1, limit: 50, offset: 0, items: [syntheticReport] };
  };

  const { root, requests } = await openSyntheticLibraryDoctor(page, {
    pluginRoute: async ({ path, request, route }) => {
      if (path === '/playback') {
        await route.fulfill({ json: { changed: false, status: statusForPhase() } });
        return true;
      }
      if (path === '/scan' && request.method() === 'POST') {
        scanPolling = true;
        await route.fulfill({ json: { started: true, status: runningStatus } });
        return true;
      }
      if (path === '/status') {
        if (scanPolling) {
          scanPolling = false;
          phase = 'scanned';
        }
        await route.fulfill({ json: statusForPhase() });
        return true;
      }
      if (path === '/results') {
        await route.fulfill({ json: resultsForPhase() });
        return true;
      }
      if (path === '/rules') {
        await route.fulfill({
          json: {
            items: phase === 'applied' ? [] : [{
              code: 'chart.duplicate-note',
              severity: 'error',
              category: 'validation',
              package_count: phase === 'first_run' ? 0 : 1,
              finding_count: phase === 'first_run' ? 0 : 1,
              rule: syntheticReport.findings[0].rule,
            }],
          },
        });
        return true;
      }
      if (path === '/repair/history') {
        const latest = phase === 'restored'
          ? restoredReceipt : phase === 'applied' ? appliedReceipt : null;
        await route.fulfill({ json: { items: latest ? [latest] : [] } });
        return true;
      }
      if (path === '/repair/preview' && request.method() === 'POST') {
        await route.fulfill({ json: repairPlan });
        return true;
      }
      if (path === '/repair/apply' && request.method() === 'POST') {
        phase = 'applied';
        await route.fulfill({ json: appliedReceipt });
        return true;
      }
      if (path === '/repair/restore' && request.method() === 'POST') {
        phase = 'restored';
        await route.fulfill({ json: restoredReceipt });
        return true;
      }
      return false;
    },
  });

  await root.getByRole('button', { name: 'Scan my library' }).click();
  await expect(root.getByText('Contract Song', { exact: true })).toBeVisible();
  await root.locator('.lh-package > summary').click();
  await root.getByRole('button', { name: 'Review safe fix' }).click();
  await root.getByRole('button', { name: 'Apply safe repair' }).click();
  await expect(root.locator('#lh-repair-result')).toContainText('Change completed');

  await root.getByRole('button', { name: 'Undo repair' }).click();
  await expect(root.getByRole('button', { name: 'Restore original song data' })).toBeFocused();
  await root.getByRole('button', { name: 'Restore original song data' }).click();
  await expect(root.locator('#lh-repair-result')).toContainText('Original restored');
  await expect(root.getByText('Contract Song', { exact: true })).toBeVisible();

  expect(requests.some((request) => request.startsWith('POST ') && request.includes('/scan'))).toBe(true);
  expect(requests.some((request) => request.includes('/repair/preview'))).toBe(true);
  expect(requests.some((request) => request.includes('/repair/apply'))).toBe(true);
  expect(requests.some((request) => request.includes('/repair/restore'))).toBe(true);
});

test('accessibility: populated state passes axe', async ({ page }) => {
  test.setTimeout(45_000);
  const { root } = await openSyntheticLibraryDoctor(page, {
    status: completeStatus,
    results: { total: 1, limit: 50, offset: 0, items: [syntheticReport] },
  });
  await root.locator('.lh-package > summary').click();
  await expectNoAccessibilityViolations(page);

  await root.evaluate((node) => {
    const colors = {
      '--fb-surface': '255 255 255',
      '--fb-card': '248 250 252',
      '--fb-border': '100 116 139',
      '--fb-text': '15 23 42',
      '--fb-text-dim': '71 85 105',
      '--fb-accent': '37 99 235',
      '--fb-good': '21 128 61',
      '--fb-warn': '146 64 14',
      '--fb-bad': '185 28 28',
      '--fb-review': '29 78 216',
      '--fb-on-accent': '255 255 255',
    };
    Object.entries(colors).forEach(([name, value]) => node.style.setProperty(name, value));
  });
  await expectNoAccessibilityViolations(page);
});

test('accessibility: keyboard focus, forced colors, and 400-percent-equivalent reflow remain usable', async ({ page }) => {
  const { root } = await openSyntheticLibraryDoctor(page, {
    status: completeStatus,
    results: { total: 1, limit: 50, offset: 0, items: [syntheticReport] },
    songs: {
      total: 1,
      songs: [{
        filename: 'synthetic/tool-song.feedpak',
        title: 'Tool Song',
        artist: 'Synthetic Artist',
      }],
    },
  });

  await page.emulateMedia({ forcedColors: 'active', reducedMotion: 'reduce' });
  const tools = root.getByRole('button', { name: 'Song tools', exact: true });
  await tools.focus();
  await page.keyboard.press('Shift+Tab');
  await page.keyboard.press('Tab');
  await expect(tools).toBeFocused();
  await expect(tools).toHaveCSS('outline-style', 'solid');
  await page.keyboard.press('Enter');
  const song = root.getByRole('button', { name: /Tool Song/ });
  await song.focus();
  await page.keyboard.press('Enter');
  await expect(root.locator('#lh-song-tool-selection')).toBeFocused();

  await page.emulateMedia({ forcedColors: 'none', reducedMotion: 'no-preference' });
  await page.setViewportSize({ width: 320, height: 720 });
  const overflow = await root.evaluate((node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 2);
});
