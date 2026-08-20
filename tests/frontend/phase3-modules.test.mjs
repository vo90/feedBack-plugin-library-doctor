import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

import { createApiClient } from '../../src/api.js';
import { createDomPrimitives } from '../../src/dom.js';
import { createLibraryDoctorStore } from '../../src/store.js';
import {
  ROOT,
  deferred,
  jsonResponse,
  launchLibraryDoctor,
  waitFor,
} from './helpers/library-doctor-app.mjs';

async function sourceModules() {
  return (await fs.readdir(path.join(ROOT, 'src')))
    .filter((filename) => filename.endsWith('.js'))
    .sort();
}

test('the manifest and thin entry use the FeedBack native-module contract', async () => {
  const manifest = JSON.parse(await fs.readFile(path.join(ROOT, 'plugin.json'), 'utf8'));
  const entry = await fs.readFile(path.join(ROOT, 'screen.js'), 'utf8');
  const screen = await fs.readFile(path.join(ROOT, 'screen.html'), 'utf8');

  assert.equal(manifest.scriptType, 'module');
  assert.equal(manifest.minHost, '0.3.0-alpha.1');
  assert.equal(
    entry.replace(/\r\n/g, '\n').trim(),
    "import { bootLibraryDoctor } from './src/app.js';\n\nbootLibraryDoctor(window);",
  );
  assert.match(screen, /If this message remains, update FeedBack/);
});

test('every Phase 3 module imports without browser globals at module load time', async () => {
  for (const filename of await sourceModules()) {
    const url = pathToFileURL(path.join(ROOT, 'src', filename));
    await assert.doesNotReject(import(url.href));
  }
});

test('the complete source graph is resolvable, acyclic, and points away from app', async () => {
  const modules = await sourceModules();
  const moduleSet = new Set(modules);
  const graph = new Map();
  for (const filename of modules) {
    const source = await fs.readFile(path.join(ROOT, 'src', filename), 'utf8');
    const imports = [...source.matchAll(/from\s+['"]\.\/(.+?\.js)['"]/g)].map((match) => match[1]);
    imports.forEach((target) => assert.ok(moduleSet.has(target), `${filename} resolves ${target}`));
    if (filename !== 'app.js') {
      assert.equal(imports.includes('app.js'), false, `${filename} must not depend on app.js`);
    }
    graph.set(filename, imports);
  }

  const visited = new Set();
  const active = new Set();
  function visit(filename) {
    if (active.has(filename)) throw new Error(`Import cycle reaches ${filename}`);
    if (visited.has(filename)) return;
    active.add(filename);
    graph.get(filename).forEach(visit);
    active.delete(filename);
    visited.add(filename);
  }
  modules.forEach(visit);
});

test('the composition root stays small and every source module respects the size boundary', async () => {
  const modules = await sourceModules();
  const focusedBoundaries = new Map([
    ['batch-controller.js', 700],
    ['batch-results-view.js', 500],
  ]);
  for (const filename of modules) {
    const source = await fs.readFile(path.join(ROOT, 'src', filename), 'utf8');
    const lines = source.split(/\r?\n/).length;
    assert.ok(lines <= 1500, `${filename} has ${lines} lines (maximum 1500)`);
    if (filename === 'app.js') assert.ok(lines <= 500, `app.js has ${lines} lines (maximum 500)`);
    if (focusedBoundaries.has(filename)) {
      const maximum = focusedBoundaries.get(filename);
      assert.ok(lines <= maximum, `${filename} has ${lines} lines (maximum ${maximum})`);
    }
  }
});

test('destroy unsubscribes host lifecycle listeners', async () => {
  const app = await launchLibraryDoctor({
    status: { running: false, stage: 'idle', summary: { total: 0 } },
  });
  assert.deepEqual(
    [...app.subscriptions.keys()].sort(),
    [
      'screen:changed', 'song:ended', 'song:loaded', 'song:loading', 'song:pause',
      'song:position-changed', 'song:ready', 'song:resume', 'song:seek', 'song:stop',
    ],
  );
  app.controller.destroy();
  assert.equal(app.subscriptions.size, 0);
  app.dom.window.close();
});

test('activation generations abort the old visit and reject stale identity', () => {
  const store = createLibraryDoctorStore();
  const first = store.activate();
  assert.equal(store.isCurrent(first), true);
  store.deactivate();
  assert.equal(first.signal.aborted, true);
  const second = store.activate();
  assert.equal(store.isCurrent(first), false);
  assert.equal(store.isCurrent(second), true);
  assert.ok(second.generation > first.generation);
});

test('API normalization preserves structured error facts', async () => {
  const activation = createLibraryDoctorStore();
  activation.activate();
  const api = createApiClient({
    activation,
    fetch: async () => jsonResponse({
      detail: {
        code: 'repair.source-changed',
        message: 'The package changed.',
        file_state: 'changed',
        retryable: true,
        next_action: 'scan_again',
      },
    }, { status: 409 }),
  });
  await assert.rejects(api.request('/repair/apply'), (error) => {
    assert.equal(error.message, 'The package changed.');
    assert.equal(error.code, 'repair.source-changed');
    assert.equal(error.fileState, 'changed');
    assert.equal(error.retryable, true);
    assert.equal(error.nextAction, 'scan_again');
    assert.equal(error.status, 409);
    return true;
  });
});

test('leaving and re-entering aborts the old status request before it can repaint', async (t) => {
  const oldVisit = deferred();
  let statusRequests = 0;
  const complete = {
    running: false,
    stage: 'idle',
    scan_current: true,
    summary: { total: 2, problems: 0, warnings: 0, review: 0 },
  };
  const partial = {
    running: false,
    stage: 'cancelled',
    scan_current: true,
    summary: { total: 1, problems: 1, warnings: 0, review: 0 },
    last_scan: { complete: false, outcome: 'cancelled', scope: 'library' },
  };
  const app = await launchLibraryDoctor({
    status: complete,
    route(request) {
      if (request.key !== '/api/plugins/library_doctor/status') return null;
      statusRequests += 1;
      if (statusRequests === 1) return jsonResponse(complete);
      if (statusRequests === 2) return oldVisit.promise;
      return jsonResponse(partial);
    },
  });
  t.after(() => app.close());

  const changed = app.subscriptions.get('screen:changed');
  changed({ detail: { id: 'plugins' } });
  changed({ detail: { id: 'plugin-library_doctor' } });
  await waitFor(() => statusRequests === 2, 'old visit status request');
  const oldRequest = app.requests.filter(({ key }) => (
    key.startsWith('/api/plugins/library_doctor/status')
  )).at(-1);

  changed({ detail: { id: 'plugins' } });
  assert.equal(oldRequest.signal.aborted, true);
  changed({ detail: { id: 'plugin-library_doctor' } });
  await waitFor(
    () => app.document.querySelector('#lh-health-workspace').dataset.viewState === 'partial',
    'new visit partial state',
  );

  oldVisit.resolve(jsonResponse({ ...complete, summary: { total: 0 } }));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(app.document.querySelector('#lh-health-workspace').dataset.viewState, 'partial');
  assert.doesNotMatch(app.document.querySelector('#lh-error').textContent, /abort/i);
});

test('shared confirmation primitive focuses entry and restores its trigger on cancel', async (t) => {
  const app = await launchLibraryDoctor({
    status: { running: false, stage: 'idle', summary: { total: 0 } },
  });
  t.after(() => app.close());
  const trigger = app.document.createElement('button');
  trigger.textContent = 'Review';
  app.document.body.appendChild(trigger);
  const { createConfirmation } = createDomPrimitives(app.document);
  const confirmation = createConfirmation({
    className: 'lh-repair-confirm',
    message: 'Apply the reviewed change?',
    confirmLabel: 'Apply',
    trigger,
    onConfirm() {},
  });
  trigger.disabled = true;
  app.document.body.appendChild(confirmation.region);
  await waitFor(() => app.document.activeElement === confirmation.confirm, 'confirmation focus');
  confirmation.cancel.click();
  await waitFor(() => app.document.activeElement === trigger, 'restored trigger focus');
});
