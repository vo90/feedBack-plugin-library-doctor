import { expect } from '@playwright/test';


export const firstRunStatus = {
  stage: 'idle',
  running: false,
  repairing: false,
  scan_current: true,
  summary: { total: 0, errors: 0, warnings: 0, reviews: 0 },
  batch: { phase: 'idle', running: false },
};

export const completeStatus = {
  stage: 'complete',
  running: false,
  repairing: false,
  scan_current: true,
  target: { label: 'Synthetic library' },
  summary: { total: 1, errors: 1, warnings: 0, reviews: 0 },
  last_scan: {
    complete: true,
    outcome: 'complete',
    completed: 1,
    expected: 1,
    target: { label: 'Synthetic library' },
  },
  batch: { phase: 'idle', running: false },
};

export const repairDefinition = {
  rule_code: 'chart.duplicate-note',
  action_kind: 'remove_exact_duplicate_notes',
  source_kind: 'arrangement',
  item_name: 'note',
  safety: 'safe_automatic',
  title: 'Remove exact duplicate notes',
  description: 'Keep the first exact note and remove only exact copies.',
  player_result: 'One stored instruction remains at the repaired position.',
  user_value: 'The highway receives one unambiguous note.',
  change_kind: 'remove_duplicates',
};

export const syntheticReport = {
  package: 'synthetic/contract-song.feedpak',
  title: 'Contract Song',
  artist: 'Synthetic Artist',
  features: {
    preview_declared: true,
    preview_available: true,
    repair_scan_current: true,
    repair_eligibility: {
      'chart.duplicate-note': { status: 'automatic' },
    },
  },
  findings: [{
    code: 'chart.duplicate-note',
    severity: 'error',
    category: 'validation',
    message: 'Two stored notes are exact duplicates.',
    affected_count: 1,
    rule: {
      title: 'Exact duplicate note',
      area: 'Chart',
      confidence: 'high',
      player_impact: 'The same instruction is loaded twice.',
      fix_benefit: 'One unambiguous instruction remains.',
    },
  }],
};

export const repairPlan = {
  available: true,
  plan_id: 'synthetic-plan-0001',
  rule_code: 'chart.duplicate-note',
  title: 'Remove exact duplicate notes',
  item_name: 'note',
  change_kind: 'remove_duplicates',
  change_count: 1,
  removed_count: 1,
  musical_positions: 1,
  arrays_affected: 1,
  description: 'Only the later exact copy is removed.',
  player_result: 'One stored instruction remains at the repaired position.',
  user_value: 'The highway receives one unambiguous note.',
  file_handling: {
    summary: 'A complete candidate is validated before replacement and Undo is retained.',
  },
};

function batchReadyStatus() {
  return {
    ...completeStatus,
    batch: {
      phase: 'ready',
      running: false,
      preview: {
        batch_plan_id: 'synthetic-batch-0001',
        eligible_count: 1,
        scope_package_count: 1,
        safe_repair_package_count: 1,
        preview_repair_count: 0,
        blocked_count: 0,
        rule_summaries: [{
          rule_code: 'chart.duplicate-note',
          title: 'Remove exact duplicate notes',
          package_count: 1,
          reported_affected_count: 1,
        }],
        packages: [{
          package: syntheticReport.package,
          title: syntheticReport.title,
          artist: syntheticReport.artist,
          safe_rule_count: 1,
        }],
        file_handling: 'Each synthetic package is recalculated, validated, and saved separately.',
        deep_audio: false,
      },
    },
  };
}

export async function openSyntheticLibraryDoctor(page, {
  status = firstRunStatus,
  results = { total: 0, limit: 50, offset: 0, items: [] },
  songs = { total: 0, songs: [] },
  batchReady = false,
  pluginRoute = null,
} = {}) {
  const requests = [];
  const dialogs = [];
  const stableStatus = batchReady ? batchReadyStatus() : status;
  page.on('dialog', async (dialog) => {
    dialogs.push(dialog.message());
    await dialog.dismiss();
  });
  await page.route('**/api/library?**', (route) => route.fulfill({ json: songs }));
  await page.route('**/api/plugins/library_doctor/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname}${url.search}`;
    requests.push(key);
    const path = url.pathname.replace('/api/plugins/library_doctor', '');

    if (pluginRoute && await pluginRoute({ path, request, requests, route })) {
      return;
    }

    if (path === '/playback') {
      return route.fulfill({ json: { changed: false, status: stableStatus } });
    }
    if (path === '/status') return route.fulfill({ json: stableStatus });
    if (path === '/rules') {
      return route.fulfill({
        json: {
          items: [{
            code: 'chart.duplicate-note',
            severity: 'error',
            category: 'validation',
            package_count: results.total,
            finding_count: results.total,
            rule: syntheticReport.findings[0].rule,
          }],
        },
      });
    }
    if (path === '/repairs') {
      return route.fulfill({
        json: {
          schema: 'library_doctor.repair_catalog.v1',
          catalog_version: 'synthetic-contract-v1',
          items: [repairDefinition],
          combined: { rule_code: 'package.all-safe', safety: 'safe_automatic' },
        },
      });
    }
    if (path === '/reviewed-repairs') {
      return route.fulfill({
        json: {
          schema: 'library_doctor.reviewed_repair_catalog.v1',
          catalog_version: 'synthetic-contract-v1',
          registry_version: 'reviewed-repairs-5',
          items: [],
        },
      });
    }
    if (path === '/repair/history') return route.fulfill({ json: { items: [] } });
    if (path === '/results') return route.fulfill({ json: results });
    if (path === '/repair/preview') return route.fulfill({ json: repairPlan });
    if (request.method() === 'GET') return route.continue();
    return route.fulfill({
      status: 501,
      json: { detail: `Unhandled synthetic Playwright route: ${path}` },
    });
  });

  await page.goto('/');
  await expect(page.getByRole('link', { name: 'Plugins', exact: true })).toBeVisible();
  // A freshly restarted development host can show its unrelated first-run
  // profile overlay even when the persisted backend profile already exists.
  // This fixture exercises only Library Doctor and never completes or mutates
  // host onboarding, so remove the transient blocker in this isolated page.
  await page.evaluate(() => document.getElementById('v3-onboarding')?.remove());
  await page.getByRole('link', { name: 'Plugins', exact: true }).click();
  const card = page.getByRole('button', { name: /^Library Doctor .* open$/ });
  await expect(card).toBeVisible();
  await card.click();
  const root = page.locator('#plugin-library_doctor');
  const heading = root.locator('#lh-title');
  await expect(heading, `Host dialogs: ${dialogs.join(' | ')}`).toBeAttached({ timeout: 12_000 });
  // The startup-status refetch may replace a newly injected screen until the
  // module script's load event records this exact version as hydrated. Open
  // only that final element, otherwise the test can activate a root that the
  // host legitimately replaces a moment later.
  await page.waitForFunction(() => {
    const pluginRoot = document.getElementById('plugin-library_doctor');
    const loaded = window.feedBack?._loadedPluginScripts;
    return pluginRoot
      && loaded instanceof Map
      && loaded.get('library_doctor') === pluginRoot.dataset.pluginVersion;
  }, null, { timeout: 12_000 });
  await expect(root.locator('#lh-module-status')).toBeHidden({ timeout: 12_000 });
  if (!(await root.evaluate((node) => node.classList.contains('active')))) {
    await expect(card).toBeVisible();
    await card.click();
  }
  await expect(root).toHaveClass(/active/, { timeout: 12_000 });
  await expect(heading).toBeVisible();
  return { root, requests };
}
