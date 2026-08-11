import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { JSDOM } from 'jsdom';

import { bootLibraryDoctor } from '../../../src/app.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(HERE, '..', '..', '..');

export function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

export function jsonResponse(body, { status = 200 } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return structuredClone(body); },
  };
}

export async function waitFor(predicate, message = 'condition', timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      if (await predicate()) return;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  if (lastError) throw lastError;
  throw new Error(`Timed out waiting for ${message}`);
}

function routeKey(url) {
  const parsed = new URL(String(url), 'http://127.0.0.1:18000');
  return `${parsed.pathname}${parsed.search}`;
}

async function resolveWithAbort(value, signal) {
  if (!signal) return value;
  if (signal.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
  let removeAbortListener = () => {};
  const aborted = new Promise((_, reject) => {
    const onAbort = () => reject(new DOMException('The operation was aborted.', 'AbortError'));
    signal.addEventListener('abort', onAbort, { once: true });
    removeAbortListener = () => signal.removeEventListener('abort', onAbort);
  });
  try {
    return await Promise.race([Promise.resolve(value), aborted]);
  } finally {
    removeAbortListener();
  }
}

export async function launchLibraryDoctor({
  status,
  results = { total: 0, items: [] },
  rules = { items: [] },
  repairs = { schema: 'library_doctor.repair_catalog.v1', items: [], combined: null },
  history = { items: [] },
  songs = { total: 0, songs: [] },
  route,
} = {}) {
  const screen = await fs.readFile(path.join(ROOT, 'screen.html'), 'utf8');
  const dom = new JSDOM(
    `<!doctype html><body><section id="plugin-library_doctor" class="screen active">${screen}</section><div id="plugin-dropdown"></div></body>`,
    {
      url: 'http://127.0.0.1:18000/#/plugins',
      runScripts: 'outside-only',
      pretendToBeVisual: true,
    },
  );
  const { window } = dom;
  const requests = [];
  const subscriptions = new Map();
  window.HTMLElement.prototype.scrollIntoView = () => {};
  window.HTMLMediaElement.prototype.load = () => {};
  window.URL.createObjectURL = () => 'blob:synthetic-library-doctor';
  window.URL.revokeObjectURL = () => {};
  window.feedBack = {
    on(name, callback) {
      subscriptions.set(name, callback);
      return () => subscriptions.delete(name);
    },
  };
  window.fetch = async (url, options = {}) => {
    const key = routeKey(url);
    const request = {
      key,
      method: String(options.method || 'GET').toUpperCase(),
      body: options.body,
      signal: options.signal,
    };
    requests.push(request);
    if (route) {
      const custom = await resolveWithAbort(route(request), options.signal);
      if (custom) return custom;
    }
    if (key === '/api/plugins/library_doctor/playback') {
      return jsonResponse({ changed: false, status: status || {} });
    }
    if (key === '/api/plugins/library_doctor/status') return jsonResponse(status || {});
    if (key === '/api/plugins/library_doctor/rules') return jsonResponse(rules);
    if (key === '/api/plugins/library_doctor/repairs') return jsonResponse(repairs);
    if (key.startsWith('/api/plugins/library_doctor/repair/history?')) return jsonResponse(history);
    if (key.startsWith('/api/plugins/library_doctor/results?')) return jsonResponse(results);
    if (key.startsWith('/api/library?')) return jsonResponse(songs);
    return jsonResponse({ detail: `Unhandled synthetic route: ${key}` }, { status: 501 });
  };
  const controller = bootLibraryDoctor(window);

  await waitFor(
    () => requests.some(({ key }) => key.startsWith('/api/plugins/library_doctor/results?')),
    'initial Library Doctor results request',
  );
  await waitFor(
    () => window.document.querySelector('#lh-health-workspace')?.dataset.viewState,
    'initial dashboard view',
  );

  return {
    dom,
    window,
    document: window.document,
    requests,
    subscriptions,
    controller,
    close() {
      controller.destroy();
      dom.window.close();
    },
  };
}
