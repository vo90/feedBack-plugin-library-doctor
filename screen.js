(function () {
  'use strict';

  const API = '/api/plugins/library_health';
  const PAGE_SIZE = 50;
  const state = {
    active: false,
    filter: 'problems',
    ruleCode: '',
    query: '',
    offset: 0,
    status: null,
    results: null,
    pollTimer: 0,
    searchTimer: 0,
    resultRequest: 0,
    repairRules: {},
    allSafeRepair: null,
    batch: null,
    batchRenderKey: '',
    latestRepair: null,
    dismissedRepairId: null,
    targetKind: 'library',
    targetPaths: { folder: '', file: '' },
  };
  let el = null;
  let playbackDesired = false;
  let playbackApplied = null;
  let playbackSyncing = false;
  let playerScreenActive = false;
  let playbackNotice = null;
  let playbackStatus = null;
  let playbackPollTimer = 0;
  let playbackSyncRetryTimer = 0;
  let playbackSyncRetryDelay = 500;

  function elements() {
    if (el && el.root && el.root.isConnected) return el;
    const root = document.getElementById('plugin-library_health');
    if (!root) return null;
    el = {
      root,
      targets: root.querySelector('#lh-targets'),
      targetOptions: root.querySelectorAll('input[name="lh-target"]'),
      deepAudio: root.querySelector('#lh-deep-audio'),
      targetPath: root.querySelector('#lh-target-path'),
      pickerNote: root.querySelector('#lh-picker-note'),
      chooseTarget: root.querySelector('#lh-choose-target'),
      scan: root.querySelector('#lh-scan'),
      scanAll: root.querySelector('#lh-scan-all'),
      cancel: root.querySelector('#lh-cancel'),
      status: root.querySelector('#lh-status'),
      progressCount: root.querySelector('#lh-progress-count'),
      progress: root.querySelector('#lh-progress'),
      scanWarning: root.querySelector('#lh-scan-warning'),
      repairResult: root.querySelector('#lh-repair-result'),
      batchSection: root.querySelector('#lh-batch-section'),
      batchCopy: root.querySelector('#lh-batch-copy'),
      batchReview: root.querySelector('#lh-batch-review'),
      batchCancel: root.querySelector('#lh-batch-cancel'),
      batchProgress: root.querySelector('#lh-batch-progress'),
      batchStatus: root.querySelector('#lh-batch-status'),
      batchCount: root.querySelector('#lh-batch-count'),
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

  async function request(path, options) {
    const response = await fetch(API + path, options);
    let body = null;
    try { body = await response.json(); } catch (_) { /* handled below */ }
    if (!response.ok) {
      const detail = body && (body.detail || body.error);
      const message = typeof detail === 'string'
        ? detail
        : detail && typeof detail.message === 'string'
          ? detail.message
          : `Request failed (${response.status})`;
      const error = new Error(message);
      error.code = detail && typeof detail === 'object' ? detail.code : null;
      error.fileState = detail && typeof detail === 'object' ? detail.file_state : null;
      error.status = response.status;
      throw error;
    }
    return body;
  }

  function getPlaybackNotice() {
    if (playbackNotice && playbackNotice.isConnected) return playbackNotice;
    playbackNotice = document.createElement('div');
    playbackNotice.id = 'lh-playback-notice';
    playbackNotice.className = 'lh-playback-notice';
    playbackNotice.setAttribute('role', 'status');
    playbackNotice.setAttribute('aria-live', 'polite');
    playbackNotice.hidden = true;
    document.body.appendChild(playbackNotice);
    return playbackNotice;
  }

  function renderPlaybackNotice(status) {
    playbackStatus = status || null;
    const notice = getPlaybackNotice();
    const batch = status?.batch;
    const batchRunning = !!batch?.running;
    const running = !!status?.running || batchRunning;
    const show = playerScreenActive && playbackDesired && running;
    notice.hidden = !show;
    if (!show) return;
    const paused = batchRunning
      ? batch.phase === 'paused'
      : !!status.playback_paused || status.stage === 'paused';
    notice.dataset.stage = paused ? 'paused' : 'pausing';
    notice.textContent = paused
      ? 'Library Doctor scan paused · resumes when you exit'
      : 'Library Doctor scan pausing to prioritize playback…';
    if (batchRunning) {
      notice.textContent = paused
        ? 'Library Doctor batch paused - resumes when you exit'
        : 'Library Doctor batch finishing the current Feedpak before pausing...';
    }
  }

  function schedulePlaybackStatusPoll(delay) {
    clearTimeout(playbackPollTimer);
    if (!playerScreenActive || !playbackDesired) return;
    playbackPollTimer = setTimeout(refreshPlaybackStatus, delay);
  }

  async function refreshPlaybackStatus() {
    if (!playerScreenActive || !playbackDesired) return;
    try {
      const status = await request('/status');
      renderPlaybackNotice(status);
      if (
        (status.running && !status.playback_paused)
        || (status.batch?.running && status.batch.phase !== 'paused')
      ) {
        schedulePlaybackStatusPoll(250);
      }
    } catch (error) {
      console.warn('[Library Doctor] Could not confirm paused scan status:', error);
      schedulePlaybackStatusPoll(1000);
    }
  }

  function schedulePlaybackSyncRetry() {
    clearTimeout(playbackSyncRetryTimer);
    if (playbackApplied === playbackDesired) return;
    const delay = playbackSyncRetryDelay;
    playbackSyncRetryDelay = Math.min(5000, playbackSyncRetryDelay * 2);
    playbackSyncRetryTimer = setTimeout(syncPlaybackPriority, delay);
  }

  async function syncPlaybackPriority() {
    if (playbackSyncing) return;
    playbackSyncing = true;
    try {
      while (playbackApplied !== playbackDesired) {
        const active = playbackDesired;
        const payload = await request('/playback', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ active }),
        });
        playbackApplied = active;
        playbackSyncRetryDelay = 500;
        if (active) {
          renderPlaybackNotice(payload?.status);
          if (
            (payload?.status?.running && !payload.status.playback_paused)
            || (
              payload?.status?.batch?.running
              && payload.status.batch.phase !== 'paused'
            )
          ) {
            schedulePlaybackStatusPoll(250);
          }
        } else {
          clearTimeout(playbackPollTimer);
          renderPlaybackNotice(null);
        }
      }
    } catch (error) {
      console.warn('[Library Doctor] Could not update playback priority:', error);
    } finally {
      playbackSyncing = false;
      if (playbackApplied !== playbackDesired) schedulePlaybackSyncRetry();
    }
  }

  function setPlaybackPriority(active) {
    playbackDesired = !!active;
    clearTimeout(playbackSyncRetryTimer);
    if (!playbackDesired) {
      clearTimeout(playbackPollTimer);
      renderPlaybackNotice(null);
    }
    syncPlaybackPriority();
  }

  function setHidden(node, hidden) { if (node) node.hidden = !!hidden; }
  function text(node, value) { if (node) node.textContent = value == null ? '' : String(value); }
  function number(value) { return Number(value || 0).toLocaleString(); }

  function duration(value) {
    let seconds = Math.max(0, Math.round(Number(value || 0)));
    const hours = Math.floor(seconds / 3600);
    seconds -= hours * 3600;
    const minutes = Math.floor(seconds / 60);
    seconds -= minutes * 60;
    if (hours) return `${hours}h ${minutes}m`;
    if (minutes) return `${minutes}m ${seconds}s`;
    return `${seconds}s`;
  }

  function localDateTime(value) {
    const timestamp = Number(value);
    if (!Number.isFinite(timestamp) || timestamp <= 0) return '';
    return new Date(timestamp * 1000).toLocaleString();
  }

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
    const running = !!state.status?.running || !!state.status?.repairing;
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
    el.deepAudio.disabled = running;
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
    const repairing = !!status.repairing;
    const batch = status.batch || null;
    const batchRunning = !!batch?.running;
    const hasReports = Number(summary.total || 0) > 0;
    renderSummary(summary);

    setHidden(el.scan, running || repairing);
    el.scan.textContent = hasReports ? 'Check for changes' : 'Scan library';
    setHidden(el.scanAll, running || repairing || !hasReports);
    setHidden(el.cancel, !running);
    setHidden(el.progress, !running);
    setHidden(el.error, status.stage !== 'error');
    setHidden(el.scanWarning, true);
    updateTargetControls();

    const targetLabel = status.target?.label || 'the selected target';

    if (batchRunning) {
      text(el.status, batch.message || 'Library Doctor batch operation is running...');
      text(el.progressCount, 'Scanning is temporarily unavailable');
    } else if (repairing) {
      text(el.status, 'Applying and verifying a safe package repair...');
      text(el.progressCount, 'Scanning is temporarily unavailable');
    } else if (running) {
      const total = Number(status.total || 0);
      const done = Number(status.done || 0);
      el.progress.max = Math.max(1, total);
      el.progress.value = Math.min(done, Math.max(1, total));
      el.progress.setAttribute(
        'aria-valuetext',
        total ? `${number(done)} of ${number(total)} packages checked` : 'Finding song packages',
      );
      const phase = status.stage === 'paused'
        ? 'Paused while a song is open — scanning will resume automatically.'
        : status.playback_active
          ? 'Pausing to prioritize song playback…'
          : status.stage === 'discovering' ? 'Finding song packages…'
            : status.stage === 'cancelling' ? 'Finishing the current package…'
              : `Scanning ${status.current || 'library'}…`;
      text(el.status, phase);
      const eta = status.playback_active || status.eta_seconds == null
        ? ''
        : ` | about ${duration(status.eta_seconds)} left`;
      const deep = status.deep_audio ? ' | deep audio' : '';
      text(el.progressCount, total ? `${number(done)} of ${number(total)}${eta}${deep}` : deep.slice(3));
      if (Array.isArray(status.discovery_errors) && status.discovery_errors.length) {
        text(el.scanWarning, 'Some folders could not be read. This scan cannot represent the full selected scope.');
        setHidden(el.scanWarning, false);
      }
    } else if (status.stage === 'complete') {
      text(el.status, `Scan complete for ${targetLabel}. ${number(summary.total)} package${summary.total === 1 ? '' : 's'} checked.`);
      text(el.progressCount, status.reused ? `${number(status.reused)} unchanged` : '');
    } else if (status.stage === 'cancelled') {
      text(el.status, 'Scan cancelled. Completed package reports were kept.');
      text(el.progressCount, status.done ? `${number(status.done)} completed` : '');
      text(el.scanWarning, 'These results are incomplete because the scan was cancelled.');
      setHidden(el.scanWarning, false);
    } else if (status.stage === 'incomplete') {
      text(el.status, `Scan finished for ${targetLabel}, but some folders could not be read.`);
      text(el.progressCount, status.done ? `${number(status.done)} completed` : '');
      text(el.scanWarning, 'These results do not represent the full selected scope. Check folder access and scan again.');
      setHidden(el.scanWarning, false);
    } else if (status.stage === 'error') {
      text(el.status, 'The selected scan could not finish.');
      text(el.progressCount, '');
      text(el.error, status.error || 'Unknown scan error');
    } else if (hasReports) {
      text(el.status, `${number(summary.total)} cached package reports are available for ${targetLabel}.`);
      text(el.progressCount, '');
    } else {
      text(el.status, 'Library Doctor has not scanned this library yet.');
      text(el.progressCount, '');
    }

    const last = status.last_scan;
    if (last && typeof last === 'object') {
      const when = localDateTime(last.completed_at || last.started_at);
      const scope = last.target?.label || 'selected scope';
      const expected = last.expected == null || !Number.isFinite(Number(last.expected))
        ? null
        : Number(last.expected);
      const completed = Number(last.completed || 0);
      const coverage = expected == null ? `${number(completed)} packages` : `${number(completed)} of ${number(expected)} packages`;
      const profile = last.deep_audio ? 'deep audio' : 'normal checks';
      const outcome = last.complete ? 'complete' : `${last.outcome || 'incomplete'}`;
      text(el.scanProvenance, `Last scan: ${outcome} | ${scope} | ${profile} | ${coverage}${when ? ` | ${when}` : ''}`);
      setHidden(el.scanProvenance, false);
      if (!running && !last.complete && status.stage === 'idle') {
        text(el.scanWarning, `The last scan was ${last.outcome || 'interrupted'}. Cached results may be incomplete.`);
        setHidden(el.scanWarning, false);
      }
    } else {
      setHidden(el.scanProvenance, true);
    }
    renderBatchStatus(batch, status);
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

  function batchSummaryGrid(items) {
    const grid = make('div', 'lh-batch-summary');
    items.forEach(([value, label]) => {
      const item = make('div');
      item.appendChild(make('strong', '', number(value)));
      item.appendChild(make('span', '', label));
      grid.appendChild(item);
    });
    return grid;
  }

  function renderBatchStatus(batch, scannerStatus) {
    if (!el.batchSection) return;
    const summary = scannerStatus?.summary || {};
    const lastScan = scannerStatus?.last_scan;
    const hasReports = Number(summary.total || 0) > 0;
    const completeScope = !!lastScan?.complete;
    const phase = batch?.phase || 'idle';
    const running = !!batch?.running;
    state.batch = batch || null;
    setHidden(el.batchSection, !hasReports);
    if (!hasReports) return;

    const target = batch?.target?.label || scannerStatus?.target?.label || 'current scan scope';
    text(
      el.batchCopy,
      completeScope
        ? `Review every deterministic safe repair in ${target}. The preview is read-only and must finish before anything can be applied.`
        : 'Complete this scan scope before reviewing a batch repair. Incomplete results are never used for mass changes.',
    );
    setHidden(el.batchReview, running);
    setHidden(el.batchCancel, !running);
    el.batchReview.disabled = !completeScope || !!scannerStatus?.running || (!!scannerStatus?.repairing && !running);
    text(
      el.batchReview,
      phase === 'ready' ? 'Refresh batch preview' : 'Review batch repair',
    );
    text(
      el.batchCancel,
      ['apply', 'undo-apply'].includes(batch?.mode)
        ? 'Stop after current Feedpak' : 'Stop preview',
    );

    setHidden(el.batchProgress, !running);
    if (running) {
      const total = Number(batch.total || 0);
      const done = Number(batch.done || 0);
      text(el.batchStatus, batch.message || 'Working...');
      const eta = batch.eta_seconds == null || phase === 'paused'
        ? ''
        : ` | about ${duration(batch.eta_seconds)} left`;
      text(el.batchCount, `${number(done)} of ${number(total)}${eta}`);
      el.batchProgressBar.max = Math.max(1, total);
      el.batchProgressBar.value = Math.min(done, Math.max(1, total));
      el.batchProgressBar.setAttribute(
        'aria-valuetext',
        `${number(done)} of ${number(total)} Feedpaks processed`,
      );
    }

    const undoPhase = phase.startsWith('undo')
      || (['paused', 'cancelling'].includes(phase) && String(batch?.mode || '').startsWith('undo'));
    const activeResult = batch?.result || (
      phase === 'idle' || phase === 'stale' || undoPhase
        || (phase === 'error' && String(batch?.mode || '').startsWith('undo'))
        ? batch?.last_result : null
    );
    const restoredCount = Array.isArray(activeResult?.outcomes)
      ? activeResult.outcomes.filter((item) => item.outcome === 'restored').length
      : 0;
    const renderKey = [
      phase,
      batch?.preview?.batch_plan_id || '',
      activeResult?.id || '',
      restoredCount,
      activeResult?.currently_repaired_count ?? '',
      batch?.undo_preview?.undo_plan_id || '',
      batch?.undo_result?.id || '',
      batch?.message || '',
    ].join(':');
    if (state.batchRenderKey === renderKey) return;
    state.batchRenderKey = renderKey;
    el.batchPreview.replaceChildren();
    el.batchResult.replaceChildren();

    if (phase === 'ready' && batch.preview) renderBatchPreview(batch.preview);
    if (phase === 'undo_ready' && batch.undo_preview) {
      renderBatchUndoPreview(batch.undo_preview);
    }
    if (
      batch?.undo_result
      && ['undo_completed', 'undo_cancelled'].includes(phase)
    ) {
      renderBatchUndoResult(batch.undo_result);
    }
    if (activeResult) renderBatchResult(activeResult, !batch?.result, batch);
    if (
      !activeResult
      && ['stale', 'error', 'cancelled', 'undo_cancelled'].includes(phase)
    ) {
      const card = make('div', 'lh-batch-card');
      card.appendChild(make('h4', '', phase === 'error' ? 'Batch operation stopped' : 'Batch preview is not active'));
      card.appendChild(make('p', '', batch?.message || 'Review the current scan scope again when you are ready.'));
      el.batchResult.appendChild(card);
    }
  }

  function renderBatchPreview(preview) {
    const card = make('div', 'lh-batch-card');
    card.appendChild(make('h4', '', 'Batch preview ready - no Feedpaks changed'));
    card.appendChild(make(
      'p',
      '',
      `${number(preview.eligible_count)} of ${number(preview.scope_package_count)} scanned Feedpaks have a safe repair that can be applied now.`,
    ));
    card.appendChild(batchSummaryGrid([
      [preview.eligible_count, 'Eligible Feedpaks'],
      [preview.removed_count, 'Redundant entries'],
      [preview.blocked_count, 'Blocked and excluded'],
      [preview.no_longer_needed_count, 'No longer need repair'],
    ]));

    const rules = make('details', 'lh-batch-details');
    rules.open = true;
    rules.appendChild(make('summary', '', `Changes by repair type (${number(preview.rule_summaries?.length || 0)})`));
    const ruleList = make('ul', 'lh-batch-list');
    (preview.rule_summaries || []).forEach((rule) => {
      const item = make('li');
      item.appendChild(make('strong', '', rule.title || rule.rule_code));
      item.appendChild(make(
        'span',
        '',
        `${number(rule.removed_count)} redundant ${rule.item_name || 'item'} ${Number(rule.removed_count) === 1 ? 'copy' : 'copies'} across ${number(rule.package_count)} Feedpak${Number(rule.package_count) === 1 ? '' : 's'}.`,
      ));
      ruleList.appendChild(item);
    });
    rules.appendChild(ruleList);
    card.appendChild(rules);

    const packages = make('details', 'lh-batch-details');
    packages.appendChild(make('summary', '', `Eligible Feedpaks (${number(preview.packages?.length || 0)})`));
    const packageList = make('ul', 'lh-batch-list');
    const packageLimit = 250;
    (preview.packages || []).slice(0, packageLimit).forEach((item) => {
      const row = make('li');
      row.appendChild(make('strong', '', `${item.title || item.package}${item.artist ? ` - ${item.artist}` : ''}`));
      row.appendChild(make(
        'span',
        '',
        `${number(item.removed_count)} redundant entries | ${number(item.rule_count)} repair ${Number(item.rule_count) === 1 ? 'type' : 'types'} | ${item.package}`,
      ));
      packageList.appendChild(row);
    });
    if ((preview.packages || []).length > packageLimit) {
      packageList.appendChild(make(
        'li',
        '',
        `${number(preview.packages.length - packageLimit)} additional eligible Feedpaks are included in the totals above. Use a smaller folder scan for a complete on-screen package list.`,
      ));
    }
    packages.appendChild(packageList);
    card.appendChild(packages);

    if (Number(preview.blocked_count || 0) > 0) {
      const blocked = make('details', 'lh-batch-details');
      blocked.open = true;
      blocked.appendChild(make('summary', '', `Blocked Feedpaks (${number(preview.blocked_count)})`));
      const blockedList = make('ul', 'lh-batch-list');
      (preview.blocked || []).slice(0, 250).forEach((item) => {
        const row = make('li');
        row.appendChild(make('strong', '', item.title || item.package));
        const additional = Math.max(0, Number(item.blocker_count || 1) - 1);
        row.appendChild(make(
          'span',
          '',
          `${item.message}${additional ? ` ${number(additional)} additional safety ${additional === 1 ? 'blocker was' : 'blockers were'} found.` : ''} (${item.package})`,
        ));
        blockedList.appendChild(row);
      });
      blocked.appendChild(blockedList);
      card.appendChild(blocked);
      card.appendChild(make(
        'p',
        'lh-batch-warning',
        'Blocked Feedpaks are excluded from this batch and will not be changed. Eligible Feedpaks can still be repaired.',
      ));
    }

    card.appendChild(make('p', '', preview.file_handling));
    card.appendChild(make(
      'p',
      'lh-muted',
      `${preview.deep_audio ? 'This batch will repeat Deep Audio validation because the current scan used it. ' : ''}Gameplay pauses the batch between Feedpaks. Stopping also takes effect after the current Feedpak finishes safely.`,
    ));
    if (Number(preview.eligible_count || 0) > 0) {
      const actions = make('div', 'lh-repair-buttons');
      const continueButton = make('button', 'lh-button lh-button-primary', 'Continue to confirmation');
      continueButton.type = 'button';
      continueButton.addEventListener('click', () => showBatchConfirmation(preview, continueButton, card));
      actions.appendChild(continueButton);
      card.appendChild(actions);
    }
    el.batchPreview.appendChild(card);
  }

  function showBatchConfirmation(preview, trigger, card) {
    trigger.disabled = true;
    const confirmation = make('div', 'lh-batch-confirm');
    confirmation.appendChild(make(
      'p',
      '',
      `Apply safe repairs to ${number(preview.eligible_count)} Feedpak${Number(preview.eligible_count) === 1 ? '' : 's'}? Each package is validated, backed up, and saved separately. If a later package fails, earlier successful repairs remain in place with their own Undo backup.`,
    ));
    const apply = make('button', 'lh-button lh-button-primary', 'Apply batch repair');
    const cancel = make('button', 'lh-button', 'Go back');
    apply.type = 'button';
    cancel.type = 'button';
    apply.addEventListener('click', () => applyBatchRepairs(preview, apply, cancel));
    cancel.addEventListener('click', () => {
      confirmation.remove();
      trigger.disabled = false;
    });
    confirmation.appendChild(apply);
    confirmation.appendChild(cancel);
    card.appendChild(confirmation);
  }

  async function startBatchPreview() {
    el.batchReview.disabled = true;
    setHidden(el.batchPreview, false);
    el.batchPreview.replaceChildren(make('p', 'lh-muted', 'Preparing a read-only batch preview...'));
    try {
      const batch = await request('/repair/batch/preview', { method: 'POST' });
      const status = { ...(state.status || {}), repairing: true, batch };
      renderStatus(status);
      schedulePoll(200);
    } catch (error) {
      el.batchReview.disabled = false;
      el.batchPreview.replaceChildren(make('p', 'lh-inline-error', error.message));
    }
  }

  async function applyBatchRepairs(preview, apply, cancel) {
    apply.disabled = true;
    cancel.disabled = true;
    text(apply, 'Starting batch...');
    try {
      const batch = await request('/repair/batch/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_plan_id: preview.batch_plan_id }),
      });
      renderStatus({ ...(state.status || {}), repairing: true, batch });
      schedulePoll(200);
    } catch (error) {
      apply.disabled = false;
      cancel.disabled = false;
      text(apply, 'Apply batch repair');
      apply.parentNode.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  async function cancelBatchOperation() {
    el.batchCancel.disabled = true;
    try {
      const payload = await request('/repair/batch/cancel', { method: 'POST' });
      renderStatus({ ...(state.status || {}), repairing: true, batch: payload.status });
      schedulePoll(200);
    } catch (error) {
      el.batchCancel.disabled = false;
      el.batchResult.replaceChildren(make('p', 'lh-inline-error', error.message));
    }
  }

  async function startBatchUndoPreview(trigger) {
    trigger.disabled = true;
    text(trigger, 'Checking recovery backups...');
    try {
      const batch = await request('/repair/batch/undo/preview', { method: 'POST' });
      renderStatus({ ...(state.status || {}), repairing: true, batch });
      schedulePoll(200);
    } catch (error) {
      trigger.disabled = false;
      text(trigger, 'Review Undo all remaining repairs');
      trigger.parentNode.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  function renderBatchUndoPreview(preview) {
    const card = make('div', 'lh-batch-card lh-batch-undo-card');
    card.appendChild(make('h4', '', 'Undo preview ready - no Feedpaks changed'));
    card.appendChild(make(
      'p',
      '',
      `${number(preview.eligible_count)} remaining batch repair${Number(preview.eligible_count) === 1 ? '' : 's'} can be safely undone now.`,
    ));
    card.appendChild(batchSummaryGrid([
      [preview.eligible_count, 'Ready to restore'],
      [preview.already_restored_count, 'Already restored'],
      [preview.blocked_count, 'Blocked and excluded'],
      [preview.entries_to_restore, 'Entries that will return'],
    ]));

    const packages = make('details', 'lh-batch-details');
    packages.open = true;
    packages.appendChild(make('summary', '', `Feedpaks ready to restore (${number(preview.eligible_count)})`));
    const packageList = make('ul', 'lh-batch-list');
    (preview.packages || []).slice(0, 250).forEach((item) => {
      const row = make('li');
      row.appendChild(make('strong', '', `${item.title || item.package}${item.artist ? ` - ${item.artist}` : ''}`));
      row.appendChild(make(
        'span',
        '',
        `${number(item.removed_count)} original chart entries will return across ${number(item.member_count)} chart ${Number(item.member_count) === 1 ? 'file' : 'files'}. ${item.package}`,
      ));
      packageList.appendChild(row);
    });
    if ((preview.packages || []).length > 250) {
      packageList.appendChild(make(
        'li',
        '',
        `${number(preview.packages.length - 250)} additional Feedpaks are included in the totals above.`,
      ));
    }
    packages.appendChild(packageList);
    card.appendChild(packages);

    if (Number(preview.blocked_count || 0) > 0) {
      const blocked = make('details', 'lh-batch-details');
      blocked.open = true;
      blocked.appendChild(make('summary', '', `Blocked Feedpaks (${number(preview.blocked_count)})`));
      const blockedList = make('ul', 'lh-batch-list');
      (preview.blocked || []).slice(0, 250).forEach((item) => {
        const row = make('li');
        row.appendChild(make('strong', '', item.title || item.package));
        row.appendChild(make('span', '', `${item.message} (${item.package})`));
        blockedList.appendChild(row);
      });
      blocked.appendChild(blockedList);
      card.appendChild(blocked);
      card.appendChild(make(
        'p',
        'lh-batch-warning',
        'Blocked Feedpaks will remain unchanged. Undo will continue only with packages whose repaired chart files and retained backups still pass every safety check.',
      ));
    }

    card.appendChild(make('p', '', preview.file_handling));
    card.appendChild(make(
      'p',
      'lh-muted',
      'The safe repair findings are expected to return after Undo. Gameplay pauses restoration between Feedpaks, and stopping takes effect after the current Feedpak finishes.',
    ));
    if (Number(preview.eligible_count || 0) > 0) {
      const actions = make('div', 'lh-repair-buttons');
      const continueButton = make('button', 'lh-button lh-button-danger', 'Continue to Undo confirmation');
      continueButton.type = 'button';
      continueButton.addEventListener('click', () => showBatchUndoConfirmation(preview, continueButton, card));
      actions.appendChild(continueButton);
      card.appendChild(actions);
    }
    el.batchPreview.appendChild(card);
  }

  function showBatchUndoConfirmation(preview, trigger, card) {
    trigger.disabled = true;
    const confirmation = make('div', 'lh-batch-confirm lh-batch-undo-confirm');
    confirmation.appendChild(make(
      'p',
      '',
      `Restore the saved original chart data for ${number(preview.eligible_count)} Feedpak${Number(preview.eligible_count) === 1 ? '' : 's'}? Safe repair findings and ${number(preview.entries_to_restore)} redundant chart entries are expected to return. Other package files are preserved.`,
    ));
    const apply = make('button', 'lh-button lh-button-danger', `Undo repairs for ${number(preview.eligible_count)} Feedpaks`);
    const cancel = make('button', 'lh-button', 'Keep repaired versions');
    apply.type = 'button';
    cancel.type = 'button';
    apply.addEventListener('click', () => applyBatchUndo(preview, apply, cancel));
    cancel.addEventListener('click', () => {
      confirmation.remove();
      trigger.disabled = false;
    });
    confirmation.appendChild(apply);
    confirmation.appendChild(cancel);
    card.appendChild(confirmation);
  }

  async function applyBatchUndo(preview, apply, cancel) {
    apply.disabled = true;
    cancel.disabled = true;
    text(apply, 'Starting Undo...');
    try {
      const batch = await request('/repair/batch/undo/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ undo_plan_id: preview.undo_plan_id }),
      });
      renderStatus({ ...(state.status || {}), repairing: true, batch });
      schedulePoll(200);
    } catch (error) {
      apply.disabled = false;
      cancel.disabled = false;
      text(apply, `Undo repairs for ${number(preview.eligible_count)} Feedpaks`);
      apply.parentNode.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  function renderBatchUndoResult(result) {
    const card = make('div', 'lh-batch-card lh-batch-undo-card');
    card.appendChild(make(
      'h4',
      '',
      result.outcome === 'complete' ? 'Batch Undo complete' : 'Batch Undo stopped',
    ));
    card.appendChild(make(
      'p',
      '',
      result.outcome === 'complete'
        ? `${number(result.completed_count)} planned restore transactions finished.`
        : `${number(result.completed_count)} restores finished; ${number(result.remaining_count)} were not started.`,
    ));
    card.appendChild(batchSummaryGrid([
      [result.restored_count, 'Originals restored'],
      [result.skipped_count, 'Skipped safely'],
      [result.failed_count, 'Failed'],
      [result.restored_entry_count, 'Chart entries returned'],
    ]));
    if (Number(result.cache_refresh_failed_count || 0) > 0) {
      card.appendChild(make(
        'p',
        'lh-batch-warning',
        `${number(result.cache_refresh_failed_count)} restored package report${Number(result.cache_refresh_failed_count) === 1 ? '' : 's'} need a manual rescan to refresh their displayed status.`,
      ));
    }
    const details = make('details', 'lh-batch-details');
    details.open = Number(result.failed_count || 0) > 0 || Number(result.skipped_count || 0) > 0;
    const outcomes = Array.isArray(result.outcomes) ? result.outcomes : [];
    details.appendChild(make('summary', '', `Restore outcomes (${number(outcomes.length)})`));
    const list = make('ul', 'lh-batch-list');
    const pager = make('div', 'lh-batch-pager');
    const pageSize = 25;
    let page = 0;
    function drawPage() {
      list.replaceChildren();
      const start = page * pageSize;
      outcomes.slice(start, start + pageSize).forEach((outcome) => {
        const row = make('li');
        const heading = make('div', 'lh-batch-outcome-heading');
        heading.appendChild(make('strong', '', `${outcome.title || outcome.package}${outcome.artist ? ` - ${outcome.artist}` : ''}`));
        const label = outcome.outcome === 'restored' ? 'Original restored'
          : outcome.outcome === 'failed' ? 'Failed' : 'Skipped';
        const tone = outcome.outcome === 'restored' ? 'review'
          : outcome.outcome === 'failed' ? 'error' : 'warning';
        heading.appendChild(badge(label, tone));
        row.appendChild(heading);
        row.appendChild(make(
          'span',
          '',
          outcome.outcome === 'restored'
            ? `${number(outcome.restored_count)} original chart entries returned. ${outcome.cache_updated === false ? 'Displayed scan result needs a manual refresh. ' : ''}${outcome.package}`
            : `${outcome.message || 'No additional details.'} ${outcome.package}`,
        ));
        list.appendChild(row);
      });
      const pages = Math.max(1, Math.ceil(outcomes.length / pageSize));
      pager.replaceChildren();
      const previous = make('button', 'lh-button', 'Previous');
      const next = make('button', 'lh-button', 'Next');
      previous.type = 'button';
      next.type = 'button';
      previous.disabled = page === 0;
      next.disabled = page + 1 >= pages;
      previous.addEventListener('click', () => { page -= 1; drawPage(); });
      next.addEventListener('click', () => { page += 1; drawPage(); });
      pager.appendChild(previous);
      pager.appendChild(make('span', '', `Page ${number(page + 1)} of ${number(pages)}`));
      pager.appendChild(next);
    }
    drawPage();
    details.appendChild(list);
    if (outcomes.length > pageSize) details.appendChild(pager);
    card.appendChild(details);
    el.batchPreview.appendChild(card);
  }

  function renderBatchResult(result, previousSession, batchState) {
    const card = make('div', 'lh-batch-card');
    const completed = result.outcome === 'complete';
    const hasOutcomeDetails = Array.isArray(result.outcomes);
    const outcomes = hasOutcomeDetails ? result.outcomes : [];
    const outcomeCount = hasOutcomeDetails
      ? outcomes.length
      : Number(result.completed_count || 0);
    const restoredCount = Number(
      result.restored_count ?? outcomes.filter((item) => item.outcome === 'restored').length,
    );
    const currentlyRepaired = Number(
      result.currently_repaired_count
        ?? outcomes.filter((item) => item.outcome === 'success').length,
    );
    const currentRemoved = Number(
      result.current_removed_count
        ?? outcomes
          .filter((item) => item.outcome === 'success')
          .reduce((total, item) => total + Number(item.removed_count || 0), 0),
    );
    card.appendChild(make(
      'h4',
      '',
      previousSession
        ? 'Most recent batch result'
        : completed ? 'Batch repair complete' : 'Batch repair stopped',
    ));
    card.appendChild(make(
      'p',
      '',
      completed
        ? `${number(result.completed_count)} planned Feedpak transactions finished. ${number(result.successful_count)} originally completed successfully.`
        : `${number(result.completed_count)} Feedpaks finished before the batch stopped; ${number(result.remaining_count)} were not started.`,
    ));
    card.appendChild(batchSummaryGrid([
      [currentlyRepaired, 'Currently repaired'],
      [restoredCount, 'Originals restored'],
      [result.failed_count, 'Repair failures'],
      [currentRemoved, 'Entries currently removed'],
    ]));
    card.appendChild(make('p', '', result.recovery_summary));
    if (Number(result.cache_refresh_failed_count || 0) > 0) {
      card.appendChild(make(
        'p',
        'lh-batch-warning',
        `${number(result.cache_refresh_failed_count)} repaired Feedpak report${Number(result.cache_refresh_failed_count) === 1 ? '' : 's'} could not refresh automatically. Scan those packages again before relying on their displayed status.`,
      ));
    }

    const details = make('details', 'lh-batch-details');
    details.open = Number(result.failed_count || 0) > 0 || Number(result.skipped_count || 0) > 0;
    details.appendChild(make('summary', '', `Package outcomes (${number(outcomeCount)})`));
    if (!hasOutcomeDetails) {
      details.appendChild(make(
        'p',
        'lh-muted',
        'Package outcome details will return when the current operation finishes.',
      ));
    }
    const list = make('ul', 'lh-batch-list');
    const pager = make('div', 'lh-batch-pager');
    const pageSize = 25;
    let page = 0;

    function drawPage() {
      list.replaceChildren();
      const start = page * pageSize;
      outcomes.slice(start, start + pageSize).forEach((outcome) => {
        const row = make('li');
        const heading = make('div', 'lh-batch-outcome-heading');
        heading.appendChild(make('strong', '', `${outcome.title || outcome.package}${outcome.artist ? ` - ${outcome.artist}` : ''}`));
        const tone = outcome.outcome === 'success' ? 'good'
          : outcome.outcome === 'failed' ? 'error'
            : outcome.outcome === 'restored' ? 'review' : 'warning';
        const label = outcome.outcome === 'success' ? 'Repaired'
          : outcome.outcome === 'restored' ? 'Original restored'
            : outcome.outcome === 'failed' ? 'Failed' : 'Skipped';
        heading.appendChild(badge(label, tone));
        row.appendChild(heading);
        row.appendChild(make(
          'span',
          '',
          outcome.outcome === 'success'
            ? `${number(outcome.removed_count)} redundant entries removed. ${outcome.cache_updated === false ? 'Displayed scan result needs a manual refresh. ' : ''}${outcome.package}`
            : `${outcome.message || 'No additional details.'} ${outcome.package}`,
        ));
        if (outcome.outcome === 'success' && outcome.backup_id) {
          const actions = make('div', 'lh-batch-outcome-actions');
          const undo = make('button', 'lh-button', 'Review Undo');
          undo.type = 'button';
          undo.addEventListener('click', () => reviewBatchUndo(outcome, undo, row));
          actions.appendChild(undo);
          row.appendChild(actions);
        }
        list.appendChild(row);
      });
      const pages = Math.max(1, Math.ceil(outcomes.length / pageSize));
      pager.replaceChildren();
      const previous = make('button', 'lh-button', 'Previous');
      const next = make('button', 'lh-button', 'Next');
      previous.type = 'button';
      next.type = 'button';
      previous.disabled = page === 0;
      next.disabled = page + 1 >= pages;
      previous.addEventListener('click', () => { page -= 1; drawPage(); });
      next.addEventListener('click', () => { page += 1; drawPage(); });
      pager.appendChild(previous);
      pager.appendChild(make('span', '', `Page ${number(page + 1)} of ${number(pages)}`));
      pager.appendChild(next);
    }
    drawPage();
    details.appendChild(list);
    if (outcomes.length > pageSize) details.appendChild(pager);
    card.appendChild(details);
    const phase = batchState?.phase || 'idle';
    const undoActive = ['undo_previewing', 'undo_ready', 'undoing'].includes(phase)
      || (batchState?.running && ['paused', 'cancelling'].includes(phase)
        && String(batchState?.mode || '').startsWith('undo'));
    if (currentlyRepaired > 0 && !batchState?.running && !undoActive) {
      const actions = make('div', 'lh-repair-buttons');
      const reviewUndo = make('button', 'lh-button lh-button-danger', 'Review Undo all remaining repairs');
      reviewUndo.type = 'button';
      reviewUndo.addEventListener('click', () => startBatchUndoPreview(reviewUndo));
      actions.appendChild(reviewUndo);
      card.appendChild(actions);
      card.appendChild(make(
        'p',
        'lh-muted',
        'Undo all still performs an independent safety check and restore for each Feedpak. Packages changed since repair will be excluded rather than overwritten.',
      ));
    }
    el.batchResult.appendChild(card);
  }

  function reviewBatchUndo(outcome, trigger, row) {
    trigger.disabled = true;
    const confirmation = make('div', 'lh-batch-confirm');
    confirmation.appendChild(make(
      'p',
      '',
      'Undo restores the original chart files saved for this Feedpak before its batch repair. Other successfully repaired Feedpaks are not affected.',
    ));
    const restore = make('button', 'lh-button lh-button-danger', 'Restore original chart data');
    const keep = make('button', 'lh-button', 'Keep repaired version');
    restore.type = 'button';
    keep.type = 'button';
    restore.addEventListener('click', () => undoBatchOutcome(outcome, restore, keep, confirmation));
    keep.addEventListener('click', () => {
      confirmation.remove();
      trigger.disabled = false;
    });
    confirmation.appendChild(restore);
    confirmation.appendChild(keep);
    row.appendChild(confirmation);
  }

  async function undoBatchOutcome(outcome, restore, keep, confirmation) {
    restore.disabled = true;
    keep.disabled = true;
    text(restore, 'Restoring and validating...');
    try {
      const result = await request('/repair/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package: outcome.package, backup_id: outcome.backup_id }),
      });
      result.id = `batch-restore-${outcome.backup_id}-${Date.now()}`;
      renderRepairResult(result);
      await refreshStatus();
      await Promise.all([loadRules(), loadResults()]);
    } catch (error) {
      restore.disabled = false;
      keep.disabled = false;
      text(restore, 'Restore original chart data');
      confirmation.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  function relatedFindingDetails(findings) {
    const technical = make('details', 'lh-finding-technical');
    technical.appendChild(make('summary', '', `Related checks (${number(findings.length)})`));
    const list = make('ul', 'lh-related-findings');
    findings.forEach((finding) => {
      const rule = finding.rule || {};
      const affected = Number(finding.affected_count || 1);
      list.appendChild(make(
        'li',
        '',
        `${rule.title || finding.code || 'Validation issue'}: ${number(affected)} affected item${affected === 1 ? '' : 's'} (${finding.code || 'unknown'})`,
      ));
    });
    technical.appendChild(list);
    return technical;
  }

  function appendFindingExplanation(item, problem, playerImpact, fixBenefit, guidance) {
    const explanation = make('div', 'lh-finding-explanation');
    [
      ['What Library Doctor found', problem, 'problem'],
      ['What you may notice in game', playerImpact, 'impact'],
      ['Why fixing it matters', fixBenefit, 'benefit'],
    ].forEach(([label, value, tone]) => {
      const block = make('div', `lh-finding-answer lh-finding-answer-${tone}`);
      block.appendChild(make('span', 'lh-finding-answer-label', label));
      block.appendChild(make('p', '', value || 'No additional explanation is available.'));
      explanation.appendChild(block);
    });
    item.appendChild(explanation);
    if (guidance) {
      const next = make('p', 'lh-finding-guidance');
      next.appendChild(make('strong', '', 'Suggested next step: '));
      next.appendChild(document.createTextNode(guidance));
      item.appendChild(next);
    }
  }

  function groupedFindingNode(title, message, playerImpact, fixBenefit, findings) {
    const item = make('li', 'lh-finding lh-finding-group');
    const severity = findings.some((finding) => finding.severity === 'error') ? 'error' : 'warning';
    item.dataset.severity = severity;
    item.dataset.category = 'validation';
    item.appendChild(make('strong', 'lh-finding-title', title));
    const affected = findings.reduce(
      (total, finding) => total + Math.max(1, Number(finding.affected_count || 1)),
      0,
    );
    appendFindingExplanation(
      item,
      message,
      playerImpact,
      fixBenefit,
      `${number(affected)} affected items are grouped here. Review the related checks and correct the shared source problem first.`,
    );
    item.appendChild(relatedFindingDetails(findings));
    return item;
  }

  function repairableFindingGroupNode(findings, report) {
    const representative = findings[0];
    const rule = representative.rule || {};
    const definition = state.repairRules[representative.code] || {};
    const itemName = definition.item_name || 'item';
    const pluralItem = itemName === 'drum hit' ? 'drum hits' : `${itemName}s`;
    const affected = findings.reduce(
      (total, finding) => total + Math.max(1, Number(finding.affected_count || 1)),
      0,
    );
    const arrangements = new Set(
      findings.map((finding) => finding.arrangement_id).filter(Boolean),
    );
    const sourceFiles = new Set(
      findings.map((finding) => String(finding.location || '').split(':')[0]).filter(Boolean),
    );
    const scope = arrangements.size
      ? `${number(arrangements.size)} arrangement${arrangements.size === 1 ? '' : 's'}`
      : `${number(sourceFiles.size || findings.length)} source ${sourceFiles.size === 1 ? 'file' : 'files'}`;

    const item = make('li', 'lh-finding lh-finding-repair-group');
    item.dataset.severity = representative.severity || 'warning';
    item.dataset.category = representative.category || 'validation';
    item.appendChild(make('strong', 'lh-finding-title', rule.title || definition.title || 'Safe repair available'));
    appendFindingExplanation(
      item,
      `${number(affected)} musical ${affected === 1 ? 'position contains' : 'positions contain'} redundant ${pluralItem} with identical stored values across ${scope}. These arrangement-level findings share one package-wide repair.`,
      rule.player_impact,
      rule.fix_benefit,
      'Review the single package-wide fix below. Its preview recalculates every declared source file and shows the complete change before anything is saved.',
    );
    const repair = repairControls(report, representative);
    if (repair) item.appendChild(repair);

    const technical = make('details', 'lh-finding-technical lh-repair-group-technical');
    technical.appendChild(make(
      'summary',
      '',
      `Affected ${arrangements.size ? 'arrangements' : 'source findings'} (${number(findings.length)})`,
    ));
    const list = make('ul', 'lh-repair-group-evidence');
    findings.forEach((finding) => {
      const evidence = make('li');
      evidence.appendChild(make(
        'strong',
        '',
        finding.arrangement_id || String(finding.location || '').split(':')[0] || 'Package source',
      ));
      evidence.appendChild(make('p', '', finding.message || 'Duplicate entries were found.'));
      const meta = [];
      if (finding.time != null) meta.push(`First example: ${Number(finding.time).toFixed(4)}s`);
      if (finding.string != null) meta.push(`String ${Number(finding.string) + 1}`);
      if (finding.location) meta.push(finding.location);
      if (meta.length) evidence.appendChild(make('span', 'lh-finding-code', meta.join(' | ')));
      list.appendChild(evidence);
    });
    technical.appendChild(list);
    item.appendChild(technical);
    return item;
  }

  function displayFindingNodes(report) {
    const findings = Array.isArray(report.findings) ? report.findings : [];
    const consumed = new Set();
    const nodes = [];
    const durationFindings = findings.filter((finding) => (
      String(finding.code || '').includes('after-duration')
      || finding.code === 'media.audio-longer-than-manifest'
    ));
    if (durationFindings.length >= 2) {
      durationFindings.forEach((finding) => consumed.add(finding));
      const audio = durationFindings.find(
        (finding) => finding.code === 'media.audio-longer-than-manifest',
      );
      const message = audio
        ? `${audio.message} Review the declared song duration first; correcting it may resolve the related timeline findings.`
        : 'Several kinds of song content continue beyond the declared duration. Review the manifest duration first because one correction may resolve these related findings.';
      nodes.push(groupedFindingNode(
        'Content extends beyond the declared song duration',
        message,
        'The highway, lyrics, tone changes, or audio may be cut off because FeedBack believes the song has already ended.',
        'Correct duration data lets the complete intended ending remain visible and playable, and may clear several related findings at once.',
        durationFindings,
      ));
    }

    const negative = findings.find((finding) => finding.code === 'chart.negative-fret');
    const invisible = findings.find((finding) => finding.code === 'chart.invisible-chord');
    const sameMutedPositions = negative && invisible
      && negative.arrangement_id === invisible.arrangement_id
      && Math.abs(Number(negative.time) - Number(invisible.time)) < 0.0001
      && Number(negative.affected_count || 1) === Number(invisible.affected_count || 1);
    if (sameMutedPositions && !consumed.has(negative) && !consumed.has(invisible)) {
      consumed.add(negative);
      consumed.add(invisible);
      nodes.push(groupedFindingNode(
        'Muted events have no playable fret or visible chord shape',
        'Two checks describe the same imported positions. FeedBack cannot place these events reliably on the highway, but Library Doctor cannot infer the intended fret or chord shape.',
        'The affected events may be absent from the highway or shown without a useful instruction for the player.',
        'Correct playable fret or chord data makes the intended events visible and usable during the song.',
        [negative, invisible],
      ));
    }

    const repairGroups = new Map();
    findings.forEach((finding) => {
      if (consumed.has(finding)) return;
      const definition = state.repairRules[finding.code];
      if (!definition || definition.safety !== 'safe_automatic') return;
      if (!repairGroups.has(finding.code)) repairGroups.set(finding.code, []);
      repairGroups.get(finding.code).push(finding);
    });

    findings.forEach((finding) => {
      if (consumed.has(finding)) return;
      const group = repairGroups.get(finding.code);
      if (group && group.length > 1) {
        group.forEach((member) => consumed.add(member));
        nodes.push(repairableFindingGroupNode(group, report));
      } else {
        consumed.add(finding);
        nodes.push(findingNode(finding, report));
      }
    });
    return nodes;
  }

  function repairChangeSummary(receipt) {
    const count = Number(receipt.removed_count || 0);
    const positions = Number(receipt.musical_positions || 0);
    const itemName = receipt.item_name || 'item';
    if (receipt.legacy_receipt) {
      return 'The package still matches an earlier successful Library Doctor repair. That older version did not store the exact item count in its result receipt.';
    }
    if (!count) return 'The saved original chart data was restored.';
    const summaries = Array.isArray(receipt.repair_summaries)
      ? receipt.repair_summaries.filter((item) => Number(item.removed_count || 0) > 0)
      : [];
    if (summaries.length > 1) {
      const changes = summaries.map((item) => {
        const itemCount = Number(item.removed_count || 0);
        return `${number(itemCount)} ${item.item_name || 'item'} ${itemCount === 1 ? 'copy' : 'copies'}`;
      });
      return `Applied ${number(summaries.length)} safe repair types in one transaction and removed ${number(count)} redundant stored entries: ${changes.join(', ')}. The first identical authored entries were kept.`;
    }
    return `Removed ${number(count)} redundant ${itemName} ${count === 1 ? 'copy' : 'copies'}${positions ? ` at ${number(positions)} musical ${positions === 1 ? 'position' : 'positions'}` : ''}. The first identical authored entry was kept.`;
  }

  function showRepairedPackage(receipt) {
    state.filter = 'all';
    state.ruleCode = '';
    state.offset = 0;
    state.query = String(receipt.title || receipt.package || '').trim();
    el.search.value = state.query;
    updateFilterButtons();
    loadResults().then(() => {
      el.root.querySelector('#lh-results-title')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  }

  function renderRepairFailure(report, error) {
    const stateCopy = error.fileState === 'recovery_required'
      ? 'Automatic rollback could not be confirmed. Do not play or repair this package again until its recovery backup has been restored.'
      : error.fileState === 'verify_required'
        ? 'Library Doctor could not confirm the final file state. Scan this package again before trying another repair.'
        : 'The existing Feedpak was left unchanged. No repaired copy was added to the library.';
    renderRepairResult({
      id: `failure-${Date.now()}`,
      action: 'repair',
      outcome: 'failure',
      package: report.package,
      title: report.title || report.package,
      artist: report.artist || '',
      message: error.message,
      file_state_copy: stateCopy,
    });
  }

  function renderRepairResult(receipt) {
    const receiptKey = receipt && (receipt.backup_id || receipt.id);
    if (!receipt || !el.repairResult || receiptKey === state.dismissedRepairId) return;
    state.latestRepair = receipt;
    const failed = receipt.outcome === 'failure';
    const restored = receipt.outcome === 'restored' || receipt.action === 'restore';
    const panel = el.repairResult;
    panel.replaceChildren();
    panel.dataset.outcome = failed ? 'failure' : restored ? 'restored' : 'success';
    panel.setAttribute('role', failed ? 'alert' : 'status');

    const heading = make('div', 'lh-repair-result-heading');
    const copy = make('div');
    copy.appendChild(badge(failed ? 'Not applied' : restored ? 'Original restored' : 'Repair successful', failed ? 'error' : 'good'));
    copy.appendChild(make(
      'h3',
      '',
      failed ? 'The repair was not completed' : restored ? 'The original chart data was restored' : 'The repair completed successfully',
    ));
    copy.appendChild(make(
      'p',
      'lh-muted',
      `${receipt.title || receipt.package || 'Selected package'}${receipt.artist ? ` — ${receipt.artist}` : ''}`,
    ));
    const dismiss = make('button', 'lh-repair-dismiss', 'Dismiss');
    dismiss.type = 'button';
    dismiss.setAttribute('aria-label', 'Dismiss repair result');
    dismiss.addEventListener('click', () => {
      state.dismissedRepairId = receiptKey;
      setHidden(panel, true);
    });
    heading.appendChild(copy);
    heading.appendChild(dismiss);
    panel.appendChild(heading);

    const answers = make('div', 'lh-repair-result-answers');
    const blocks = failed ? [
      ['What happened', receipt.message || 'The repair could not be completed.'],
      ['What changed in the Feedpak', receipt.file_state_copy],
      ['What to do next', receipt.file_state_copy?.includes('Scan')
        ? 'Run a scan of this package and review its result before trying again.'
        : 'You can review the finding and try again; the existing package remains the version FeedBack will load.'],
    ] : restored ? [
      ['What happened', repairChangeSummary(receipt)],
      ['What to expect in game', receipt.player_result || 'The original entries are present again, so the repaired finding may return when the package is scanned.'],
      ['Why this is useful', receipt.user_value || 'This returns the chart to the state saved immediately before the repair.'],
      ['What happened to the Feedpak', receipt.file_handling?.summary || 'The original chart data was restored at the same package path. No duplicate song was added.'],
    ] : [
      ['What changed', repairChangeSummary(receipt)],
      ['What to expect in game', receipt.player_result || 'FeedBack will load the repaired chart the next time the song is opened.'],
      ['Why the fix matters', receipt.user_value || 'The repaired data is now unambiguous and passed the current validation checks.'],
      ['What happened to the Feedpak', receipt.file_handling?.summary || 'The validated candidate replaced the package at the same path. No duplicate song was added, and original changed chart files were backed up.'],
    ];
    blocks.forEach(([label, value]) => {
      const block = make('div', 'lh-repair-result-answer');
      block.appendChild(make('strong', '', label));
      block.appendChild(make('p', '', value));
      answers.appendChild(block);
    });
    panel.appendChild(answers);

    if (!failed) {
      panel.appendChild(make(
        'p',
        'lh-repair-verification',
        restored
          ? `Recovery was validated before it was saved.${receipt.cache_updated === false ? ' Scan this package again to refresh the displayed result.' : ''}${receipt.receipt_saved === false ? ' The recovery succeeded, but this result could not be saved to repair history.' : ''}`
          : `The complete repaired candidate passed validation before it replaced the existing package.${receipt.cache_updated === false ? ' The repair succeeded, but you should scan this package again to refresh its displayed result.' : ''}${receipt.receipt_saved === false ? ' The repair succeeded, but this result could not be saved to repair history; the recovery backup still exists.' : ''}`,
      ));
    }

    const actions = make('div', 'lh-repair-buttons');
    if (!failed) {
      const show = make('button', 'lh-button', 'Show this package');
      show.type = 'button';
      show.addEventListener('click', () => showRepairedPackage(receipt));
      actions.appendChild(show);
    }
    if (!failed && !restored && receipt.backup_id) {
      const undo = make('button', 'lh-button', 'Undo this repair');
      undo.type = 'button';
      undo.addEventListener('click', () => confirmRestore(receipt, undo, actions));
      actions.appendChild(undo);
    }
    panel.appendChild(actions);
    setHidden(panel, false);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function confirmRestore(receipt, trigger, actions) {
    trigger.disabled = true;
    const confirmation = make('div', 'lh-repair-confirm');
    confirmation.appendChild(make(
      'p',
      '',
      receipt.rule_code === 'package.all-safe'
        ? 'Undo will restore all original chart files saved before this combined repair. The repaired safe findings are expected to return. Other package files are preserved.'
        : 'Undo will restore the original chart files saved before this repair. The repaired duplicate finding is expected to return. Other package files are preserved.',
    ));
    const confirm = make('button', 'lh-button lh-button-danger', 'Restore original chart data');
    const cancel = make('button', 'lh-button', 'Keep repaired version');
    confirm.type = 'button';
    cancel.type = 'button';
    confirm.addEventListener('click', () => restoreRepair(receipt, confirm, cancel));
    cancel.addEventListener('click', () => {
      confirmation.remove();
      trigger.disabled = false;
    });
    confirmation.appendChild(confirm);
    confirmation.appendChild(cancel);
    actions.parentNode.insertBefore(confirmation, actions.nextSibling);
  }

  async function restoreRepair(receipt, confirm, cancel) {
    confirm.disabled = true;
    cancel.disabled = true;
    text(confirm, 'Restoring and validating...');
    try {
      const result = await request('/repair/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package: receipt.package, backup_id: receipt.backup_id }),
      });
      result.id = `restore-${receipt.backup_id}-${Date.now()}`;
      renderRepairResult(result);
      await refreshStatus();
      await Promise.all([loadRules(), loadResults()]);
    } catch (error) {
      renderRepairFailure(receipt, error);
    }
  }

  async function loadRepairHistory() {
    try {
      const payload = await request('/repair/history?limit=1');
      if (!state.active) return;
      const latest = Array.isArray(payload?.items) ? payload.items[0] : null;
      if (latest) renderRepairResult(latest);
    } catch (error) {
      console.warn('[Library Doctor] Could not load repair history:', error);
    }
  }

  function repairControls(report, finding) {
    const definition = state.repairRules[finding.code];
    if (!definition || definition.safety !== 'safe_automatic') return null;
    const wrapper = make('div', 'lh-repair-action');
    const button = make('button', 'lh-button lh-button-safe', 'Review safe fix');
    button.type = 'button';
    const region = make('div', 'lh-repair-preview');
    region.setAttribute('aria-live', 'polite');
    button.addEventListener('click', () => previewRepair(report, finding, button, region));
    wrapper.appendChild(button);
    wrapper.appendChild(region);
    return wrapper;
  }

  function appendRepairPreviewAnswers(card, plan) {
    const previewAnswers = make('div', 'lh-repair-preview-answers');
    [
      ['Expected result in game', plan.player_result],
      ['Why this is useful', plan.user_value],
      [
        'What happens to the Feedpak',
        plan.file_handling?.summary || (
          'A complete repaired candidate is validated first. It then replaces the existing Feedpak at the same path, while the original changed chart files are kept in private recovery storage. No duplicate song is added to the library.'
        ),
      ],
    ].forEach(([label, value]) => {
      const answer = make('div', 'lh-repair-preview-answer');
      answer.appendChild(make('strong', '', label));
      answer.appendChild(make('p', '', value));
      previewAnswers.appendChild(answer);
    });
    card.appendChild(previewAnswers);
    card.appendChild(make(
      'p',
      'lh-muted',
      'If candidate creation, backup, integrity checking, or validation fails, the existing Feedpak is not replaced. After a successful repair, Undo can restore the saved original chart data.',
    ));
  }

  function safeRepairCodes(report) {
    return new Set(
      (Array.isArray(report.findings) ? report.findings : [])
        .map((finding) => finding.code)
        .filter((code) => state.repairRules[code]?.safety === 'safe_automatic'),
    );
  }

  function allSafeRepairControls(report) {
    const ruleCodes = safeRepairCodes(report);
    if (ruleCodes.size <= 1 || !state.allSafeRepair) return null;

    const panel = make('section', 'lh-all-safe');
    const heading = make('div', 'lh-all-safe-heading');
    const copy = make('div');
    copy.appendChild(make('strong', '', state.allSafeRepair.title || 'Fix all safe issues'));
    copy.appendChild(make(
      'p',
      '',
      `${number(ruleCodes.size)} safe repair types are available for this Feedpak. Review and apply them together with one validation, one backup, and one Undo.`,
    ));
    heading.appendChild(copy);
    heading.appendChild(badge(`${number(ruleCodes.size)} safe fixes`, 'good'));
    panel.appendChild(heading);

    const action = make('div', 'lh-repair-action');
    const button = make('button', 'lh-button lh-button-safe', 'Review all safe fixes');
    button.type = 'button';
    const region = make('div', 'lh-repair-preview');
    region.setAttribute('aria-live', 'polite');
    button.addEventListener('click', () => previewAllSafeRepairs(report, button, region));
    action.appendChild(button);
    action.appendChild(region);
    panel.appendChild(action);
    return panel;
  }

  async function previewAllSafeRepairs(report, trigger, region) {
    trigger.disabled = true;
    text(trigger, 'Preparing combined preview...');
    region.replaceChildren();
    try {
      const plan = await request('/repair/all/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package: report.package }),
      });
      trigger.hidden = true;
      const card = make('div', 'lh-repair-card lh-all-safe-card');
      card.appendChild(make('strong', '', plan.title || 'Fix all safe issues'));
      if (plan.available) {
        card.appendChild(make(
          'p',
          '',
          `This will apply ${number(plan.rule_count)} safe repair ${plan.rule_count === 1 ? 'type' : 'types'} and remove ${number(plan.removed_count)} redundant stored ${plan.removed_count === 1 ? 'entry' : 'entries'} across ${number(plan.member_count)} chart ${plan.member_count === 1 ? 'file' : 'files'}.`,
        ));
        const list = make('ul', 'lh-all-safe-list');
        (plan.repair_summaries || []).forEach((summary) => {
          const count = Number(summary.removed_count || 0);
          list.appendChild(make(
            'li',
            '',
            `${summary.title}: remove ${number(count)} redundant ${summary.item_name || 'item'} ${count === 1 ? 'copy' : 'copies'} from ${number(summary.member_count)} chart ${summary.member_count === 1 ? 'file' : 'files'}.`,
          ));
        });
        card.appendChild(list);
        appendRepairPreviewAnswers(card, plan);
        const actions = make('div', 'lh-repair-buttons');
        const apply = make('button', 'lh-button lh-button-primary', 'Apply all safe fixes');
        const cancel = make('button', 'lh-button', 'Cancel');
        apply.type = 'button';
        cancel.type = 'button';
        apply.addEventListener('click', () => applyAllSafeRepairs(report, plan, apply, cancel, region));
        cancel.addEventListener('click', () => {
          region.replaceChildren();
          trigger.hidden = false;
          trigger.disabled = false;
          text(trigger, 'Review all safe fixes');
        });
        actions.appendChild(apply);
        actions.appendChild(cancel);
        card.appendChild(actions);
      } else {
        const blockers = Array.isArray(plan.blockers) ? plan.blockers : [];
        card.appendChild(make(
          'p',
          '',
          blockers.length
            ? 'Library Doctor cannot safely apply the combined repair because at least one referenced chart file could not be prepared. Nothing will be changed.'
            : 'No supported safe repairs are currently available in this package.',
        ));
        blockers.forEach((blocker) => {
          card.appendChild(make(
            'p',
            'lh-repair-warning',
            `${blocker.member_path || 'Referenced chart file'}: ${blocker.message}`,
          ));
        });
        const close = make('button', 'lh-button', 'Close');
        close.type = 'button';
        close.addEventListener('click', () => {
          region.replaceChildren();
          trigger.hidden = false;
          trigger.disabled = false;
          text(trigger, 'Review all safe fixes');
        });
        card.appendChild(close);
      }
      region.appendChild(card);
    } catch (error) {
      trigger.disabled = false;
      text(trigger, 'Review all safe fixes');
      region.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  async function applyAllSafeRepairs(report, plan, apply, cancel, region) {
    apply.disabled = true;
    cancel.disabled = true;
    text(apply, 'Applying and verifying all fixes...');
    try {
      const result = await request('/repair/all/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package: report.package, plan_id: plan.plan_id }),
      });
      result.id = `repair-${result.backup_id || Date.now()}`;
      result.title = result.report?.title || report.title || report.package;
      result.artist = result.report?.artist || report.artist || '';
      renderRepairResult(result);
      await refreshStatus();
      await Promise.all([loadRules(), loadResults()]);
    } catch (error) {
      apply.disabled = false;
      cancel.disabled = false;
      text(apply, 'Apply all safe fixes');
      region.appendChild(make('p', 'lh-inline-error', error.message));
      renderRepairFailure(report, error);
    }
  }

  async function previewRepair(report, finding, trigger, region) {
    trigger.disabled = true;
    text(trigger, 'Preparing preview...');
    region.replaceChildren();
    try {
      const plan = await request('/repair/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package: report.package, rule_code: finding.code }),
      });
      trigger.hidden = true;
      const card = make('div', 'lh-repair-card');
      card.appendChild(make('strong', '', plan.title || 'Safe repair'));
      if (plan.available) {
        const itemName = plan.item_name || 'item';
        card.appendChild(make(
          'p',
          '',
          `Remove ${number(plan.removed_count)} redundant stored ${itemName} ${plan.removed_count === 1 ? 'copy' : 'copies'} at ${number(plan.musical_positions)} musical ${plan.musical_positions === 1 ? 'position' : 'positions'}, across ${number(plan.arrays_affected)} ${itemName} ${plan.arrays_affected === 1 ? 'list' : 'lists'}. The first authored copy is kept.`,
        ));
        card.appendChild(make(
          'p',
          '',
          plan.description || 'Only exact redundant copies will be removed.',
        ));
        appendRepairPreviewAnswers(card, plan);
        if (Array.isArray(plan.blockers) && plan.blockers.length) {
          card.appendChild(make(
            'p',
            'lh-repair-warning',
            `${number(plan.blockers.length)} arrangement file${plan.blockers.length === 1 ? '' : 's'} cannot be changed safely and will be left untouched.`,
          ));
        }
        const actions = make('div', 'lh-repair-buttons');
        const apply = make('button', 'lh-button lh-button-primary', 'Apply safe repair');
        const cancel = make('button', 'lh-button', 'Cancel');
        apply.type = 'button';
        cancel.type = 'button';
        apply.addEventListener('click', () => applyRepair(report, finding, plan, apply, cancel, region));
        cancel.addEventListener('click', () => {
          region.replaceChildren();
          trigger.hidden = false;
          trigger.disabled = false;
          text(trigger, 'Review safe fix');
        });
        actions.appendChild(apply);
        actions.appendChild(cancel);
        card.appendChild(actions);
      } else {
        card.appendChild(make(
          'p',
          '',
          'No supported exact duplicates are currently available to repair in this package.',
        ));
        if (Array.isArray(plan.blockers) && plan.blockers.length) {
          card.appendChild(make('p', 'lh-repair-warning', plan.blockers[0].message));
        }
        const close = make('button', 'lh-button', 'Close');
        close.type = 'button';
        close.addEventListener('click', () => {
          region.replaceChildren();
          trigger.hidden = false;
          trigger.disabled = false;
          text(trigger, 'Review safe fix');
        });
        card.appendChild(close);
      }
      region.appendChild(card);
    } catch (error) {
      trigger.disabled = false;
      text(trigger, 'Review safe fix');
      region.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  async function applyRepair(report, finding, plan, apply, cancel, region) {
    apply.disabled = true;
    cancel.disabled = true;
    text(apply, 'Applying and verifying...');
    try {
      const result = await request('/repair/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          package: report.package,
          rule_code: finding.code,
          plan_id: plan.plan_id,
        }),
      });
      result.id = `repair-${result.backup_id || Date.now()}`;
      result.title = result.report?.title || report.title || report.package;
      result.artist = result.report?.artist || report.artist || '';
      renderRepairResult(result);
      await refreshStatus();
      await Promise.all([loadRules(), loadResults()]);
    } catch (error) {
      apply.disabled = false;
      cancel.disabled = false;
      text(apply, 'Apply safe repair');
      region.appendChild(make('p', 'lh-inline-error', error.message));
      renderRepairFailure(report, error);
    }
  }

  function findingNode(finding, report) {
    const item = make('li', 'lh-finding');
    item.dataset.severity = finding.severity || 'info';
    item.dataset.category = finding.category || 'validation';
    const rule = finding.rule || {};
    item.appendChild(make('strong', 'lh-finding-title', rule.title || 'Validation issue'));
    appendFindingExplanation(
      item,
      finding.message || 'No additional description is available.',
      rule.player_impact,
      rule.fix_benefit,
      rule.guidance,
    );
    const technical = make('details', 'lh-finding-technical');
    technical.appendChild(make('summary', '', 'Technical details'));
    const meta = make('div', 'lh-finding-meta');
    if (rule.area) meta.appendChild(make('span', '', `Area: ${rule.area}`));
    if (finding.category === 'authoring_review') meta.appendChild(make('span', 'lh-review-label', 'Authoring review'));
    if (finding.category === 'feedback_compatibility') meta.appendChild(make('span', 'lh-compatibility-label', 'FeedBack compatibility'));
    meta.appendChild(make('span', 'lh-finding-code', `Rule: ${finding.code || 'unknown'}`));
    if (finding.affected_count > 1) meta.appendChild(make('span', '', `Affected: ${number(finding.affected_count)}`));
    if (rule.confidence) meta.appendChild(make('span', '', `Confidence: ${rule.confidence}`));
    if (finding.arrangement_id) meta.appendChild(make('span', '', `Arrangement: ${finding.arrangement_id}`));
    if (finding.time != null) meta.appendChild(make('span', '', `Time: ${Number(finding.time).toFixed(4)}s`));
    if (finding.string != null) meta.appendChild(make('span', '', `String: ${Number(finding.string) + 1} (stored index ${finding.string})`));
    if (finding.location) meta.appendChild(make('span', 'lh-finding-code', finding.location));
    technical.appendChild(meta);
    const repair = repairControls(report, finding);
    if (repair) item.appendChild(repair);
    item.appendChild(technical);
    return item;
  }

  function packageNode(report) {
    const details = make('details', 'lh-package');
    const summary = make('summary');
    const heading = make('div', 'lh-package-title');
    const displayTitle = report.title || report.package || 'Unnamed package';
    const findings = Array.isArray(report.findings) ? report.findings : [];
    const findingNodes = findings.length ? displayFindingNodes(report) : [];
    const counts = { error: 0, warning: 0, info: 0 };
    findingNodes.forEach((node) => {
      const severity = node.dataset.severity;
      if (Object.hasOwn(counts, severity)) counts[severity] += 1;
    });
    heading.appendChild(make('strong', '', displayTitle));
    heading.appendChild(make('span', '', report.artist || 'Unknown artist'));
    summary.appendChild(heading);

    const badges = make('div', 'lh-package-badges');
    if (counts.error) badges.appendChild(badge(`${number(counts.error)} error${counts.error === 1 ? '' : 's'}`, 'error'));
    if (counts.warning) badges.appendChild(badge(`${number(counts.warning)} warning${counts.warning === 1 ? '' : 's'}`, 'warning'));
    if (counts.info) badges.appendChild(badge(`${number(counts.info)} review suggestion${counts.info === 1 ? '' : 's'}`, 'review'));
    if (!counts.error && !counts.warning && !counts.info) badges.appendChild(badge('No issues found by current checks', 'good'));
    const features = report.features || {};
    if (!features.lyrics_declared) badges.appendChild(badge('No lyrics'));
    if (!features.preview_declared) badges.appendChild(badge('No preview'));
    const unsupportedAudio = Number(features.deep_audio_unsupported || 0);
    const skippedAudio = Number(features.deep_audio_skipped || 0);
    if (unsupportedAudio) {
      badges.appendChild(badge(`${number(unsupportedAudio)} audio file${unsupportedAudio === 1 ? '' : 's'} not deep-checked`));
    }
    if (skippedAudio) {
      badges.appendChild(badge(`${number(skippedAudio)} oversized audio file${skippedAudio === 1 ? '' : 's'} skipped`));
    }
    summary.appendChild(badges);
    details.appendChild(summary);

    const body = make('div', 'lh-package-body');
    body.appendChild(make('p', 'lh-package-path', report.package || ''));
    const allSafe = allSafeRepairControls(report);
    if (allSafe) body.appendChild(allSafe);
    if (findings.length) {
      const list = make('ul', 'lh-finding-list');
      findingNodes.forEach((node) => list.appendChild(node));
      body.appendChild(list);
    } else {
      const partial = unsupportedAudio + skippedAudio;
      body.appendChild(make(
        'p',
        'lh-healthy-copy',
        partial
          ? 'No issues were found by the checks that completed. Deep audio verification was partial for this package.'
          : 'No issues were found by the current checks.',
      ));
    }
    details.appendChild(body);
    return details;
  }

  function emptyMessage(totalReports) {
    if (!totalReports) return 'Run a scan to create package health reports.';
    if (state.query) return 'No packages match this search and filter.';
    if (state.filter === 'problems') return 'No packages need attention. The current checks found no errors, warnings, or review suggestions.';
    if (state.filter === 'review') return 'No packages have authoring review suggestions.';
    if (state.filter === 'no_lyrics') return 'Every scanned package declares lyrics.';
    if (state.filter === 'no_preview') return 'Every scanned package declares a preview.';
    if (state.filter === 'deep_audio_partial') return 'Every package received complete Deep Audio coverage.';
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

  function renderRules(payload) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    el.ruleSummary.replaceChildren();
    items.forEach((item) => {
      const button = make('button', 'lh-rule-row');
      button.type = 'button';
      button.dataset.rule = item.code || '';
      button.dataset.severity = item.severity || 'info';
      button.setAttribute('aria-pressed', String(item.code === state.ruleCode));
      const copy = make('span', 'lh-rule-copy');
      copy.appendChild(make('strong', '', item.rule?.title || 'Validation issue'));
      const ruleArea = item.category === 'feedback_compatibility'
        ? `${item.rule?.area || 'Tab'} | FeedBack compatibility`
        : item.category === 'authoring_review'
          ? `${item.rule?.area || 'Tab'} | Authoring review`
          : item.rule?.area;
      if (ruleArea) copy.appendChild(make('span', 'lh-rule-area', ruleArea));
      copy.appendChild(make('code', '', item.code || 'unknown'));
      button.appendChild(copy);
      button.appendChild(make(
        'span',
        'lh-rule-count',
        `${number(item.package_count)} package${item.package_count === 1 ? '' : 's'} | ${number(item.finding_count)} affected item${item.finding_count === 1 ? '' : 's'}`,
      ));
      el.ruleSummary.appendChild(button);
    });
    setHidden(el.ruleEmpty, items.length !== 0);
    text(
      el.ruleNote,
      state.ruleCode
        ? `Filtering packages by ${state.ruleCode}. Select it again to clear the rule filter.`
        : 'Select a rule to show only affected packages.',
    );
  }

  async function loadRules() {
    setHidden(el.ruleError, true);
    try {
      const payload = await request('/rules');
      if (!state.active) return;
      const available = (payload.items || []).some((item) => item.code === state.ruleCode);
      if (state.ruleCode && !available) state.ruleCode = '';
      renderRules(payload);
    } catch (error) {
      if (!state.active) return;
      text(el.ruleError, error.message);
      setHidden(el.ruleError, false);
    }
  }

  async function loadRepairCatalog() {
    try {
      const payload = await request('/repairs');
      if (!state.active) return;
      state.repairRules = {};
      state.allSafeRepair = payload.combined || null;
      (payload.items || []).forEach((definition) => {
        if (definition && definition.rule_code) {
          state.repairRules[definition.rule_code] = definition;
        }
      });
    } catch (error) {
      if (!state.active) return;
      state.repairRules = {};
      state.allSafeRepair = null;
      console.warn('[Library Doctor] Could not load safe repair catalog:', error);
    }
  }

  function setRule(code) {
    state.ruleCode = code === state.ruleCode ? '' : code;
    state.offset = 0;
    el.ruleSummary.querySelectorAll('[data-rule]').forEach((node) => {
      node.setAttribute('aria-pressed', String(node.dataset.rule === state.ruleCode));
    });
    text(
      el.ruleNote,
      state.ruleCode
        ? `Filtering packages by ${state.ruleCode}. Select it again to clear the rule filter.`
        : 'Select a rule to show only affected packages.',
    );
    loadResults();
  }

  function exportResults(format) {
    const params = new URLSearchParams({
      format,
      filter: state.filter,
      query: state.query,
      rule: state.ruleCode,
    });
    const link = document.createElement('a');
    link.href = `${API}/export?${params}`;
    link.download = `library-doctor-report.${format}`;
    link.hidden = true;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  async function loadResults() {
    const requestId = ++state.resultRequest;
    setHidden(el.resultsError, true);
    const params = new URLSearchParams({
      filter: state.filter,
      query: state.query,
      rule: state.ruleCode,
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
    const wasBatchRunning = !!state.status?.batch?.running;
    try {
      const status = await request('/status');
      if (!state.active) return;
      renderStatus(status);
      if ((wasRunning && !status.running) || (wasBatchRunning && !status.batch?.running)) {
        await Promise.all([loadResults(), loadRules()]);
      }
      if (status.running || status.batch?.running) {
        schedulePoll(status.batch?.running ? 400 : 750);
      }
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
    const target = {
      scope: state.targetKind,
      deep_audio: !!el.deepAudio.checked,
    };
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
    if (el.root.dataset.libraryDoctorBound === '1') return;
    el.root.dataset.libraryDoctorBound = '1';
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
    el.batchReview.addEventListener('click', startBatchPreview);
    el.batchCancel.addEventListener('click', cancelBatchOperation);
    el.filters.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-filter]');
      if (button) setFilter(button.dataset.filter);
    });
    el.ruleSummary.addEventListener('click', (event) => {
      const button = event.target.closest('button[data-rule]');
      if (button) setRule(button.dataset.rule);
    });
    el.exportJson.addEventListener('click', () => exportResults('json'));
    el.exportCsv.addEventListener('click', () => exportResults('csv'));
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
    if (state.active) {
      await Promise.all([loadRules(), loadRepairCatalog(), loadRepairHistory()]);
      if (state.active) await loadResults();
    }
  }

  function leave() {
    state.active = false;
    clearTimeout(state.pollTimer);
    clearTimeout(state.searchTimer);
  }

  function onScreenChanged(event) {
    const id = (event && event.detail && event.detail.id) || (event && event.id);
    const from = event && event.detail && event.detail.from;
    playerScreenActive = id === 'player';
    if (playerScreenActive) {
      setPlaybackPriority(true);
      renderPlaybackNotice(playbackStatus);
      schedulePlaybackStatusPoll(0);
    }
    else if (from === 'player') setPlaybackPriority(false);
    if (id === 'plugin-library_health') enter();
    else if (state.active) leave();
  }

  function wire() {
    if (window.feedBack && typeof window.feedBack.on === 'function') {
      window.feedBack.on('screen:changed', onScreenChanged);
      window.feedBack.on('song:loading', () => setPlaybackPriority(true));
      window.feedBack.on('song:stop', () => setPlaybackPriority(false));
    } else {
      window.addEventListener('feedBack:capabilities:ready', wire, { once: true });
    }
    playerScreenActive = document.querySelector('.screen.active')?.id === 'player';
    setPlaybackPriority(playerScreenActive);
    const root = document.getElementById('plugin-library_health');
    if (root && root.classList.contains('active')) enter();
  }

  wire();
}());
