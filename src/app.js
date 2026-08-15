import { createApiClient, isAbortError } from './api.js';
import { createBatchController } from './batch-controller.js';
import {
  API_ROOT as API,
  LEGACY_LAYOUT_QUERY,
  PAGE_SIZE,
  SONG_TOOL_PAGE_SIZE,
  WORKER_LIMIT_KEY,
  WORKER_MODE_KEY,
} from './constants.js';
import { createDomPrimitives } from './dom.js';
import { createFindingView } from './finding-view.js';
import { createFormatters } from './formatters.js';
import { createPlaybackController } from './playback-controller.js';
import { createPreviewController } from './preview-controller.js';
import { createRepairController } from './repair-controller.js';
import { createReviewedRepairController } from './reviewed-repair-controller.js';
import { createResultsController } from './results-controller.js';
import { createScanController } from './scan-controller.js';
import { createSongToolsController } from './song-tools-controller.js';
import { createStatusView } from './status-view.js';
import { createLibraryDoctorStore } from './store.js';

export function bootLibraryDoctor(hostWindow = window) {
  const window = hostWindow;
  const document = window.document;
  const fetch = window.fetch.bind(window);
  const localStorage = window.localStorage;

  const activation = createLibraryDoctorStore({ AbortController: window.AbortController });
  const { state } = activation;
  const { request, requestGlobal, coreRequest } = createApiClient({
    fetch,
    activation,
    apiRoot: API,
  });
  const {
    badge,
    createConfirmation,
    focus,
    make,
    number,
    setHidden,
    text,
  } = createDomPrimitives(document);
  const {
    completedRepairChange,
    duration,
    fileSize,
    localDateTime,
    plannedRepairChange,
    pluralSongs,
    repairChangeCount,
  } = createFormatters({ number });
  let el = null;
  const actions = {};
  const unsubscribers = [];
  let wired = false;
  let waitingForCapabilities = false;
  const statusView = createStatusView({
    actions,
    duration,
    getElements: elements,
    legacyLayoutQuery: LEGACY_LAYOUT_QUERY,
    localDateTime,
    number,
    pluralSongs,
    setHidden,
    state,
    text,
    window,
  });
  const batchController = createBatchController({
    actions,
    badge,
    completedRepairChange,
    createConfirmation,
    duration,
    fileSize,
    getElements: elements,
    make,
    number,
    pluralSongs,
    repairChangeCount,
    request,
    setHidden,
    state,
    text,
  });
  const findingView = createFindingView({ actions, document, make, number, state });
  const playback = createPlaybackController({ document, requestGlobal });
  const repairController = createRepairController({
    actions,
    apiRoot: API,
    badge,
    completedRepairChange,
    createConfirmation,
    document,
    duration,
    fileSize,
    getElements: elements,
    isAbortError,
    make,
    number,
    plannedRepairChange,
    repairChangeCount,
    request,
    setHidden,
    state,
    text,
  });
  const reviewedRepairController = createReviewedRepairController({
    actions,
    apiRoot: API,
    createConfirmation,
    document,
    focus,
    make,
    number,
    request,
    state,
    text,
  });
  const previewController = createPreviewController({
    actions,
    apiRoot: API,
    document,
    duration,
    make,
    number,
    repairChangeCount,
    request,
    state,
    text,
  });
  const songToolsController = createSongToolsController({
    actions,
    apiRoot: API,
    badge,
    coreRequest,
    document,
    duration,
    focus,
    getElements: elements,
    make,
    number,
    request,
    setHidden,
    songToolPageSize: SONG_TOOL_PAGE_SIZE,
    state,
    text,
  });
  const resultsController = createResultsController({
    actions,
    apiRoot: API,
    badge,
    document,
    getElements: elements,
    isAbortError,
    make,
    number,
    pageSize: PAGE_SIZE,
    request,
    setHidden,
    state,
    text,
  });
  const scanController = createScanController({
    actions,
    getElements: elements,
    isAbortError,
    localStorage,
    number,
    request,
    setHidden,
    state,
    text,
    window,
    workerLimitKey: WORKER_LIMIT_KEY,
    workerModeKey: WORKER_MODE_KEY,
  });

  function elements() {
    if (el && el.root && el.root.isConnected) return el;
    const root = document.getElementById('plugin-library_doctor');
    if (!root) return null;
    el = {
      root,
      moduleStatus: root.querySelector('#lh-module-status'),
      workspaceTabs: root.querySelector('#lh-workspace-tabs'),
      healthWorkspace: root.querySelector('#lh-health-workspace'),
      guidance: root.querySelector('#lh-guidance'),
      guidanceTitle: root.querySelector('#lh-guidance-title'),
      guidanceCopy: root.querySelector('#lh-guidance-copy'),
      scanOptions: root.querySelector('#lh-scan-options'),
      scanOptionsSummary: root.querySelector('#lh-scan-options-summary'),
      overview: root.querySelector('#lh-overview'),
      resultsSection: root.querySelector('#lh-results-section'),
      moreFilters: root.querySelector('#lh-more-filters'),
      scanDetails: root.querySelector('#lh-scan-details'),
      activitySection: root.querySelector('#lh-activity-section'),
      activityStatus: root.querySelector('#lh-activity-status'),
      songToolsWorkspace: root.querySelector('#lh-song-tools-workspace'),
      songToolSearch: root.querySelector('#lh-song-tool-search'),
      songToolCount: root.querySelector('#lh-song-tool-count'),
      songToolResults: root.querySelector('#lh-song-tool-results'),
      songToolError: root.querySelector('#lh-song-tool-error'),
      songToolPagination: root.querySelector('#lh-song-tool-pagination'),
      songToolPrev: root.querySelector('#lh-song-tool-prev'),
      songToolNext: root.querySelector('#lh-song-tool-next'),
      songToolPage: root.querySelector('#lh-song-tool-page'),
      songToolSelection: root.querySelector('#lh-song-tool-selection'),
      targets: root.querySelector('#lh-targets'),
      targetOptions: root.querySelectorAll('input[name="lh-target"]'),
      deepAudio: root.querySelector('#lh-deep-audio'),
      workerMode: root.querySelector('#lh-worker-mode'),
      workerLimit: root.querySelector('#lh-worker-limit'),
      workerLimitWrap: root.querySelector('#lh-worker-limit-wrap'),
      workerSummary: root.querySelector('#lh-worker-summary'),
      targetPath: root.querySelector('#lh-target-path'),
      pickerNote: root.querySelector('#lh-picker-note'),
      chooseTarget: root.querySelector('#lh-choose-target'),
      scan: root.querySelector('#lh-scan'),
      scanAll: root.querySelector('#lh-scan-all'),
      cancel: root.querySelector('#lh-cancel'),
      status: root.querySelector('#lh-status'),
      scanLive: root.querySelector('#lh-scan-live'),
      progressCount: root.querySelector('#lh-progress-count'),
      progress: root.querySelector('#lh-progress'),
      scanWarning: root.querySelector('#lh-scan-warning'),
      repairResult: root.querySelector('#lh-repair-result'),
      batchSection: root.querySelector('#lh-batch-section'),
      batchCopy: root.querySelector('#lh-batch-copy'),
      batchPreviewMedia: root.querySelector('#lh-batch-preview-media'),
      batchReview: root.querySelector('#lh-batch-review'),
      batchCancel: root.querySelector('#lh-batch-cancel'),
      batchProgress: root.querySelector('#lh-batch-progress'),
      batchStatus: root.querySelector('#lh-batch-status'),
      batchCount: root.querySelector('#lh-batch-count'),
      batchLiveCounts: root.querySelector('#lh-batch-live-counts'),
      batchProgressBar: root.querySelector('#lh-batch-progress-bar'),
      batchPreview: root.querySelector('#lh-batch-preview'),
      batchResult: root.querySelector('#lh-batch-result'),
      scanProvenance: root.querySelector('#lh-scan-provenance'),
      error: root.querySelector('#lh-error'),
      search: root.querySelector('#lh-search'),
      filters: root.querySelector('#lh-filters'),
      results: root.querySelector('#lh-results'),
      empty: root.querySelector('#lh-empty'),
      resultsError: root.querySelector('#lh-results-error'),
      resultCount: root.querySelector('#lh-result-count'),
      ruleSummary: root.querySelector('#lh-rule-summary'),
      ruleEmpty: root.querySelector('#lh-rule-empty'),
      ruleError: root.querySelector('#lh-rule-error'),
      ruleNote: root.querySelector('#lh-rule-note'),
      exportJson: root.querySelector('#lh-export-json'),
      exportCsv: root.querySelector('#lh-export-csv'),
      pagination: root.querySelector('#lh-pagination'),
      prev: root.querySelector('#lh-prev'),
      next: root.querySelector('#lh-next'),
      pageLabel: root.querySelector('#lh-page-label'),
    };
    return el;
  }

  function bind() {
    if (el.root.dataset.libraryDoctorBound === '1') return;
    el.root.dataset.libraryDoctorBound = '1';
    scanController.loadWorkerSettings();
    el.workspaceTabs.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-workspace]');
      if (button) songToolsController.setWorkspace(button.dataset.workspace);
    });
    el.songToolSearch.addEventListener('input', () => {
      clearTimeout(state.songTools.searchTimer);
      state.songTools.searchTimer = setTimeout(() => {
        songToolsController.closeSongToolSelection({ render: false });
        state.songTools.query = el.songToolSearch.value.trim();
        state.songTools.page = 0;
        songToolsController.loadSongTools();
      }, 250);
    });
    el.songToolPrev.addEventListener('click', () => {
      songToolsController.closeSongToolSelection({ render: false });
      state.songTools.page = Math.max(0, state.songTools.page - 1);
      songToolsController.loadSongTools();
    });
    el.songToolNext.addEventListener('click', () => {
      songToolsController.closeSongToolSelection({ render: false });
      state.songTools.page += 1;
      songToolsController.loadSongTools();
    });
    el.targets.addEventListener('change', (event) => {
      const option = event.target.closest('input[name="lh-target"]');
      if (!option || !['library', 'folder', 'file'].includes(option.value)) return;
      state.targetKind = option.value;
      scanController.updateTargetControls();
    });
    el.chooseTarget.addEventListener('click', scanController.chooseTarget);
    el.scanOptions.addEventListener('toggle', () => statusView.updateDashboardShell(state.status || {}));
    el.workerMode.addEventListener('change', () => {
      state.workerMode = el.workerMode.value === 'custom' ? 'custom' : 'automatic';
      scanController.saveWorkerSettings();
      scanController.updateWorkerControls();
    });
    el.workerLimit.addEventListener('change', () => {
      const maximum = Number(el.workerLimit.value);
      if (Number.isInteger(maximum) && maximum > 0) state.workerLimit = maximum;
      scanController.saveWorkerSettings();
      scanController.updateWorkerControls();
    });
    el.scan.addEventListener('click', () => scanController.startScan(false));
    el.scanAll.addEventListener('click', () => scanController.startScan(true));
    el.cancel.addEventListener('click', scanController.cancelScan);
    el.batchPreviewMedia.addEventListener('change', () => {
      if (state.batch?.phase !== 'ready') return;
      batchController.startBatchPreview();
    });
    el.batchReview.addEventListener('click', batchController.startBatchPreview);
    el.batchCancel.addEventListener('click', batchController.cancelBatchOperation);
    el.resultsSection.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-filter]');
      if (button) resultsController.setFilter(button.dataset.filter);
    });
    el.ruleSummary.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-rule]');
      if (button) resultsController.setRule(button.dataset.rule);
    });
    el.exportJson.addEventListener('click', () => resultsController.exportResults('json'));
    el.exportCsv.addEventListener('click', () => resultsController.exportResults('csv'));
    el.search.addEventListener('input', () => {
      clearTimeout(state.searchTimer);
      state.searchTimer = setTimeout(() => {
        state.query = el.search.value.trim();
        state.offset = 0;
        resultsController.loadResults();
      }, 250);
    });
    el.prev.addEventListener('click', () => {
      state.offset = Math.max(0, state.offset - PAGE_SIZE);
      resultsController.loadResults();
    });
    el.next.addEventListener('click', () => {
      state.offset += PAGE_SIZE;
      resultsController.loadResults();
    });
  }

  async function enter() {
    if (!elements() || state.active) return;
    const visit = activation.activate();
    const dropdown = document.getElementById('plugin-dropdown');
    if (dropdown) dropdown.classList.add('hidden');
    bind();
    songToolsController.setWorkspace(state.workspace);
    resultsController.updateFilterButtons();
    scanController.updateTargetControls();
    await scanController.refreshStatus();
    if (activation.isCurrent(visit)) {
      await Promise.all([
        resultsController.loadRules(),
        resultsController.loadRepairCatalog(),
        repairController.loadRepairHistory(),
      ]);
      if (activation.isCurrent(visit)) await resultsController.loadResults();
    }
  }

  function leave() {
    activation.deactivate();
    clearTimeout(state.pollTimer);
    clearTimeout(state.searchTimer);
    clearTimeout(state.songTools.searchTimer);
  }

  function onScreenChanged(event) {
    const id = (event && event.detail && event.detail.id) || (event && event.id);
    const from = event && event.detail && event.detail.from;
    playback.handleScreenChanged(id, from);
    if (id === 'plugin-library_doctor') enter();
    else if (state.active) leave();
  }

  function wire() {
    if (wired) return;
    const moduleStatus = document.getElementById('lh-module-status');
    if (moduleStatus) moduleStatus.hidden = true;
    if (window.feedBack && typeof window.feedBack.on === 'function') {
      wired = true;
      waitingForCapabilities = false;
      window.removeEventListener('feedBack:capabilities:ready', wire);
      [
        ['screen:changed', onScreenChanged],
        ['song:loading', () => playback.setPlaybackPriority(true)],
        ['song:stop', () => playback.setPlaybackPriority(false)],
      ].forEach(([name, handler]) => {
        const unsubscribe = window.feedBack.on(name, handler);
        if (typeof unsubscribe === 'function') unsubscribers.push(unsubscribe);
      });
    } else {
      if (!waitingForCapabilities) {
        waitingForCapabilities = true;
        window.addEventListener('feedBack:capabilities:ready', wire, { once: true });
      }
      return;
    }
    playback.initialize();
    const root = document.getElementById('plugin-library_doctor');
    if (root && root.classList.contains('active')) enter();
  }

  function destroy() {
    leave();
    window.removeEventListener('feedBack:capabilities:ready', wire);
    waitingForCapabilities = false;
    while (unsubscribers.length) {
      try { unsubscribers.pop()(); } catch (_) { /* Host cleanup is best effort. */ }
    }
    wired = false;
    playback.destroy();
  }

  Object.assign(actions, {
    appendRepairPreviewAnswers: repairController.appendRepairPreviewAnswers,
    confirmAutomaticPreviewRepair: previewController.confirmAutomaticPreviewRepair,
    confirmFinalizeRecovery: repairController.confirmFinalizeRecovery,
    allSafeRepairControls: repairController.allSafeRepairControls,
    renderBatchStatus: batchController.renderBatchStatus,
    renderSummary: resultsController.renderSummary,
    displayFindingNodes: findingView.displayFindingNodes,
    loadResults: resultsController.loadResults,
    loadRules: resultsController.loadRules,
    refreshSelectedSongTool: songToolsController.refreshSelectedSongTool,
    refreshStatus: scanController.refreshStatus,
    previewRepair: previewController.previewRepair,
    repairControls: repairController.repairControls,
    reviewedRepairControls: reviewedRepairController.reviewedRepairControls,
    renderRepairFailure: repairController.renderRepairFailure,
    renderRepairResult: repairController.renderRepairResult,
    renderStatus: statusView.renderStatus,
    updateDashboardShell: statusView.updateDashboardShell,
    showStatusError: statusView.showError,
    updateTargetControls: scanController.updateTargetControls,
    schedulePoll: scanController.schedulePoll,
  });
  wire();
  return { destroy, enter, leave, onScreenChanged, state, activation };
}
