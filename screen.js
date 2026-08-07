(function () {
  'use strict';

  const API = '/api/plugins/library_health';
  const PAGE_SIZE = 50;
  const state = {
    active: false,
    filter: 'problems',
    query: '',
    offset: 0,
    status: null,
    results: null,
    pollTimer: 0,
    searchTimer: 0,
    resultRequest: 0,
    targetKind: 'library',
    targetPaths: { folder: '', file: '' },
  };
  let el = null;

  function elements() {
    if (el && el.root && el.root.isConnected) return el;
    const root = document.getElementById('plugin-library_health');
    if (!root) return null;
    el = {
      root,
      targets: root.querySelector('#lh-targets'),
      targetOptions: root.querySelectorAll('input[name="lh-target"]'),
      targetPath: root.querySelector('#lh-target-path'),
      pickerNote: root.querySelector('#lh-picker-note'),
      chooseTarget: root.querySelector('#lh-choose-target'),
      scan: root.querySelector('#lh-scan'),
      scanAll: root.querySelector('#lh-scan-all'),
      cancel: root.querySelector('#lh-cancel'),
      status: root.querySelector('#lh-status'),
      progressCount: root.querySelector('#lh-progress-count'),
      progress: root.querySelector('#lh-progress'),
      error: root.querySelector('#lh-error'),
      search: root.querySelector('#lh-search'),
      filters: root.querySelector('#lh-filters'),
      results: root.querySelector('#lh-results'),
      empty: root.querySelector('#lh-empty'),
      resultsError: root.querySelector('#lh-results-error'),
      resultCount: root.querySelector('#lh-result-count'),
      pagination: root.querySelector('#lh-pagination'),
      prev: root.querySelector('#lh-prev'),
      next: root.querySelector('#lh-next'),
      pageLabel: root.querySelector('#lh-page-label'),
    };
    return el;
  }

  async function request(path, options) {
    const response = await fetch(API + path, options);
    let body = null;
    try { body = await response.json(); } catch (_) { /* handled below */ }
    if (!response.ok) {
      const detail = body && (body.detail || body.error);
      throw new Error(typeof detail === 'string' ? detail : `Request failed (${response.status})`);
    }
    return body;
  }

  function setHidden(node, hidden) { if (node) node.hidden = !!hidden; }
  function text(node, value) { if (node) node.textContent = value == null ? '' : String(value); }
  function number(value) { return Number(value || 0).toLocaleString(); }

  function selectedPath() {
    return state.targetKind === 'library' ? '' : state.targetPaths[state.targetKind];
  }

  function pickerAvailable() {
    const desktop = window.feedBackDesktop;
    if (!desktop) return false;
    return state.targetKind === 'folder'
      ? typeof desktop.pickDirectory === 'function'
      : state.targetKind === 'file' && typeof desktop.pickFile === 'function';
  }

  function updateTargetControls() {
    const running = !!state.status?.running;
    const path = selectedPath();
    el.targetOptions.forEach((option) => {
      option.checked = option.value === state.targetKind;
      option.disabled = running;
    });

    if (state.targetKind === 'library') {
      text(el.targetPath, 'Configured song library');
      setHidden(el.chooseTarget, true);
      setHidden(el.pickerNote, true);
      text(el.scan, 'Scan whole library');
    } else {
      const folder = state.targetKind === 'folder';
      text(el.targetPath, path || (folder ? 'No folder selected' : 'No package selected'));
      text(el.chooseTarget, folder ? (path ? 'Change folder' : 'Choose folder')
        : (path ? 'Change Feedpak' : 'Choose Feedpak'));
      setHidden(el.chooseTarget, running);
      el.chooseTarget.disabled = running || !pickerAvailable();
      text(el.scan, folder ? 'Scan selected folder' : 'Scan Feedpak');
      const unavailable = !pickerAvailable();
      setHidden(el.pickerNote, !unavailable);
      if (unavailable) {
        text(el.pickerNote, 'Folder and file selection requires FeedBack Desktop.');
      }
    }
    el.scan.disabled = running || (state.targetKind !== 'library' && !path);
    el.scanAll.disabled = el.scan.disabled;
  }

  async function chooseTarget() {
    if (state.targetKind === 'library' || !pickerAvailable()) return;
    setHidden(el.error, true);
    try {
      let path = null;
      if (state.targetKind === 'folder') {
        path = await window.feedBackDesktop.pickDirectory();
      } else {
        path = await window.feedBackDesktop.pickFile([
          { name: 'Feedpak packages', extensions: ['feedpak', 'sloppak'] },
        ]);
      }
      if (path) state.targetPaths[state.targetKind] = path;
      updateTargetControls();
    } catch (error) {
      text(el.error, error?.message || 'The system picker could not be opened.');
      setHidden(el.error, false);
    }
  }

  function updateFilterButtons() {
    const nodes = el.root.querySelectorAll('[data-filter]');
    nodes.forEach((node) => {
      if (node.closest('#lh-filters')) {
        node.setAttribute('aria-pressed', String(node.dataset.filter === state.filter));
      }
    });
  }

  function setFilter(next) {
    if (!next || next === state.filter) return;
    state.filter = next;
    state.offset = 0;
    updateFilterButtons();
    loadResults();
  }

  function renderSummary(summary) {
    const safe = summary || {};
    el.root.querySelectorAll('[data-summary]').forEach((node) => {
      text(node, number(safe[node.dataset.summary]));
    });
  }

  function renderStatus(status) {
    state.status = status;
    const summary = status.summary || {};
    const running = !!status.running;
    const hasReports = Number(summary.total || 0) > 0;
    renderSummary(summary);

    setHidden(el.scan, running);
    el.scan.textContent = hasReports ? 'Check for changes' : 'Scan library';
    setHidden(el.scanAll, running || !hasReports);
    setHidden(el.cancel, !running);
    setHidden(el.progress, !running);
    setHidden(el.error, status.stage !== 'error');
    updateTargetControls();

    const targetLabel = status.target?.label || 'the selected target';

    if (running) {
      const total = Number(status.total || 0);
      const done = Number(status.done || 0);
      el.progress.max = Math.max(1, total);
      el.progress.value = Math.min(done, Math.max(1, total));
      const phase = status.stage === 'discovering' ? 'Finding song packages…'
        : status.stage === 'cancelling' ? 'Finishing the current package…'
          : `Scanning ${status.current || 'library'}…`;
      text(el.status, phase);
      text(el.progressCount, total ? `${number(done)} of ${number(total)}` : '');
    } else if (status.stage === 'complete') {
      text(el.status, `Scan complete for ${targetLabel}. ${number(summary.total)} package${summary.total === 1 ? '' : 's'} checked.`);
      text(el.progressCount, status.reused ? `${number(status.reused)} unchanged` : '');
    } else if (status.stage === 'cancelled') {
      text(el.status, 'Scan cancelled. Completed package reports were kept.');
      text(el.progressCount, status.done ? `${number(status.done)} completed` : '');
    } else if (status.stage === 'error') {
      text(el.status, 'The selected scan could not finish.');
      text(el.progressCount, '');
      text(el.error, status.error || 'Unknown scan error');
    } else if (hasReports) {
      text(el.status, `${number(summary.total)} cached package reports are available for ${targetLabel}.`);
      text(el.progressCount, '');
    } else {
      text(el.status, 'Library Health has not scanned this library yet.');
      text(el.progressCount, '');
    }
  }

  function make(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value != null) node.textContent = String(value);
    return node;
  }

  function badge(value, tone) {
    return make('span', `lh-badge${tone ? ` lh-badge-${tone}` : ''}`, value);
  }

  function findingNode(finding) {
    const item = make('li', 'lh-finding');
    item.dataset.severity = finding.severity || 'info';
    item.appendChild(make('p', 'lh-finding-message', finding.message || 'Unspecified finding'));
    const meta = make('div', 'lh-finding-meta');
    meta.appendChild(make('span', 'lh-finding-code', finding.code || 'unknown'));
    if (finding.arrangement_id) meta.appendChild(make('span', '', `Arrangement: ${finding.arrangement_id}`));
    if (finding.time != null) meta.appendChild(make('span', '', `Time: ${Number(finding.time).toFixed(4)}s`));
    if (finding.string != null) meta.appendChild(make('span', '', `String index: ${finding.string} (0 = lowest)`));
    if (finding.location) meta.appendChild(make('span', 'lh-finding-code', finding.location));
    item.appendChild(meta);
    return item;
  }

  function packageNode(report) {
    const details = make('details', 'lh-package');
    const summary = make('summary');
    const heading = make('div', 'lh-package-title');
    const displayTitle = report.title || report.package || 'Unnamed package';
    heading.appendChild(make('strong', '', displayTitle));
    heading.appendChild(make('span', '', report.artist || 'Unknown artist'));
    summary.appendChild(heading);

    const badges = make('div', 'lh-package-badges');
    const counts = report.counts || {};
    if (counts.error) badges.appendChild(badge(`${number(counts.error)} error${counts.error === 1 ? '' : 's'}`, 'error'));
    if (counts.warning) badges.appendChild(badge(`${number(counts.warning)} warning${counts.warning === 1 ? '' : 's'}`, 'warning'));
    if (!counts.error && !counts.warning) badges.appendChild(badge('No problems found', 'good'));
    const features = report.features || {};
    if (!features.lyrics_declared) badges.appendChild(badge('No lyrics'));
    if (!features.preview_declared) badges.appendChild(badge('No preview'));
    summary.appendChild(badges);
    details.appendChild(summary);

    const body = make('div', 'lh-package-body');
    body.appendChild(make('p', 'lh-package-path', report.package || ''));
    const findings = Array.isArray(report.findings) ? report.findings : [];
    if (findings.length) {
      const list = make('ul', 'lh-finding-list');
      findings.forEach((finding) => list.appendChild(findingNode(finding)));
      body.appendChild(list);
    } else {
      body.appendChild(make('p', 'lh-healthy-copy', 'No problems were found by the current checks.'));
    }
    details.appendChild(body);
    return details;
  }

  function emptyMessage(totalReports) {
    if (!totalReports) return 'Run a scan to create package health reports.';
    if (state.query) return 'No packages match this search and filter.';
    if (state.filter === 'problems') return 'No packages need attention. The current checks found no errors or warnings.';
    if (state.filter === 'no_lyrics') return 'Every scanned package declares lyrics.';
    if (state.filter === 'no_preview') return 'Every scanned package declares a preview.';
    return 'No packages match this filter.';
  }

  function renderResults(payload) {
    state.results = payload;
    el.results.replaceChildren();
    (payload.items || []).forEach((report) => el.results.appendChild(packageNode(report)));
    const total = Number(payload.total || 0);
    const libraryTotal = Number(state.status?.summary?.total || 0);
    const targetLabel = state.status?.target?.label;
    text(el.resultCount, `${number(total)} matching package${total === 1 ? '' : 's'}${targetLabel ? ` from ${targetLabel}` : ''}`);
    setHidden(el.empty, total !== 0);
    text(el.empty, emptyMessage(libraryTotal));

    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const page = Math.min(pages, Math.floor(state.offset / PAGE_SIZE) + 1);
    setHidden(el.pagination, total <= PAGE_SIZE);
    text(el.pageLabel, `Page ${page} of ${pages}`);
    el.prev.disabled = state.offset <= 0;
    el.next.disabled = state.offset + PAGE_SIZE >= total;
  }

  async function loadResults() {
    const requestId = ++state.resultRequest;
    setHidden(el.resultsError, true);
    const params = new URLSearchParams({
      filter: state.filter,
      query: state.query,
      limit: String(PAGE_SIZE),
      offset: String(state.offset),
    });
    try {
      const payload = await request(`/results?${params}`);
      if (requestId !== state.resultRequest || !state.active) return;
      renderResults(payload);
    } catch (error) {
      if (requestId !== state.resultRequest || !state.active) return;
      text(el.resultsError, error.message);
      setHidden(el.resultsError, false);
    }
  }

  function schedulePoll(delay) {
    clearTimeout(state.pollTimer);
    if (!state.active) return;
    state.pollTimer = setTimeout(refreshStatus, delay);
  }

  async function refreshStatus() {
    if (!state.active) return;
    const wasRunning = !!state.status?.running;
    try {
      const status = await request('/status');
      if (!state.active) return;
      renderStatus(status);
      if (wasRunning && !status.running) await loadResults();
      if (status.running) schedulePoll(750);
    } catch (error) {
      text(el.error, error.message);
      setHidden(el.error, false);
      schedulePoll(3000);
    }
  }

  async function startScan(force) {
    setHidden(el.error, true);
    if (state.targetKind !== 'library' && !selectedPath()) {
      await chooseTarget();
      if (!selectedPath()) return;
    }
    const target = { scope: state.targetKind };
    if (state.targetKind !== 'library') target.path = selectedPath();
    try {
      const payload = await request(`/scan?force=${force ? 'true' : 'false'}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(target),
      });
      renderStatus(payload.status);
      schedulePoll(250);
    } catch (error) {
      text(el.error, error.message);
      setHidden(el.error, false);
    }
  }

  async function cancelScan() {
    try {
      const payload = await request('/cancel', { method: 'POST' });
      renderStatus(payload.status);
      schedulePoll(250);
    } catch (error) {
      text(el.error, error.message);
      setHidden(el.error, false);
    }
  }

  function bind() {
    if (el.root.dataset.libraryHealthBound === '1') return;
    el.root.dataset.libraryHealthBound = '1';
    el.targets.addEventListener('change', (event) => {
      const option = event.target.closest('input[name="lh-target"]');
      if (!option || !['library', 'folder', 'file'].includes(option.value)) return;
      state.targetKind = option.value;
      updateTargetControls();
    });
    el.chooseTarget.addEventListener('click', chooseTarget);
    el.scan.addEventListener('click', () => startScan(false));
    el.scanAll.addEventListener('click', () => startScan(true));
    el.cancel.addEventListener('click', cancelScan);
    el.filters.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-filter]');
      if (button) setFilter(button.dataset.filter);
    });
    el.root.querySelector('.lh-summary').addEventListener('click', (event) => {
      const button = event.target.closest('button[data-filter]');
      if (button) setFilter(button.dataset.filter);
    });
    el.search.addEventListener('input', () => {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(() => {
        state.query = el.search.value.trim();
        state.offset = 0;
        loadResults();
      }, 250);
    });
    el.prev.addEventListener('click', () => {
      state.offset = Math.max(0, state.offset - PAGE_SIZE);
      loadResults();
    });
    el.next.addEventListener('click', () => {
      state.offset += PAGE_SIZE;
      loadResults();
    });
  }

  async function enter() {
    if (!elements() || state.active) return;
    state.active = true;
    const dropdown = document.getElementById('plugin-dropdown');
    if (dropdown) dropdown.classList.add('hidden');
    bind();
    updateFilterButtons();
    updateTargetControls();
    await refreshStatus();
    if (state.active) loadResults();
  }

  function leave() {
    state.active = false;
    clearTimeout(state.pollTimer);
    clearTimeout(state.searchTimer);
  }

  function onScreenChanged(event) {
    const id = (event && event.detail && event.detail.id) || (event && event.id);
    if (id === 'plugin-library_health') enter();
    else if (state.active) leave();
  }

  function wire() {
    if (window.feedBack && typeof window.feedBack.on === 'function') {
      window.feedBack.on('screen:changed', onScreenChanged);
    } else {
      window.addEventListener('feedBack:capabilities:ready', wire, { once: true });
    }
    const root = document.getElementById('plugin-library_health');
    if (root && root.classList.contains('active')) enter();
  }

  wire();
}());
