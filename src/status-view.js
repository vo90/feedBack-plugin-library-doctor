export function createStatusView({
  actions: actionRegistry,
  duration,
  getElements,
  legacyLayoutQuery,
  localDateTime,
  number,
  pluralSongs,
  setHidden,
  state,
  text,
  window,
}) {
  const el = new Proxy({}, {
    get(_target, key) { return getElements()?.[key]; },
  });

  function announce(node, message) {
    text(node, message);
  }

  function announceScan(key, message) {
    if (!el.scanLive || el.scanLive.dataset.announcementKey === key) return;
    el.scanLive.dataset.announcementKey = key;
    text(el.scanLive, message);
  }

  function scanAnnouncement(status, summary, targetLabel) {
    if (status.batch?.running) {
      const phase = status.batch.phase || 'running';
      return [`batch:${phase}`, status.batch.message || 'Library Doctor batch operation is running.'];
    }
    if (status.repairing) return ['repairing', 'Library Doctor is applying and verifying a safe package repair.'];
    if (status.running) {
      const stage = status.stage || 'scanning';
      if (stage === 'paused') return ['scan:paused', 'Library scan paused while a song is open.'];
      if (stage === 'cancelling') return ['scan:cancelling', 'Cancelling the library scan after the current package.'];
      if (stage === 'discovering') return ['scan:discovering', 'Finding song packages to scan.'];
      const total = Math.max(0, Number(status.total || 0));
      const done = Math.max(0, Number(status.done || 0));
      const interval = total > 0 ? Math.max(1, Math.ceil(total / 4)) : 25;
      const bucket = Math.floor(done / interval);
      const message = total
        ? `Library scan in progress. ${number(done)} of ${number(total)} packages checked.`
        : 'Library scan in progress.';
      return [`scan:running:${total}:${bucket}`, message];
    }
    if (status.stage === 'complete') {
      return [
        `scan:complete:${Number(summary.total || 0)}`,
        `Scan complete for ${targetLabel}. ${number(summary.total)} package${summary.total === 1 ? '' : 's'} checked.`,
      ];
    }
    if (status.stage === 'cancelled') return ['scan:cancelled', 'Scan cancelled. Completed package reports were kept.'];
    if (status.stage === 'incomplete') return ['scan:incomplete', `Scan finished for ${targetLabel}, but some folders could not be read.`];
    if (status.stage === 'error') return [`scan:error:${status.error || ''}`, `The selected scan could not finish. ${status.error || ''}`.trim()];
    if (Number(summary.total || 0) > 0) {
      return [`scan:cached:${Number(summary.total || 0)}`, `${number(summary.total)} cached package reports are available.`];
    }
    return ['scan:idle', 'Library Doctor is ready to scan this library.'];
  }

  function showError(node, error) {
    text(node, error?.message || String(error || 'Unknown error'));
    setHidden(node, false);
  }

  function clear(node) {
    text(node, '');
    setHidden(node, true);
  }

  function legacyLayoutRequested() {
    if (window.__LIBRARY_DOCTOR_LEGACY_LAYOUT__ === true) return true;
    try {
      return new URLSearchParams(window.location.search).get(legacyLayoutQuery) === 'legacy';
    } catch (_) {
      return false;
    }
  }

  function dashboardViewFor(status) {
    const safe = status || {};
    const total = Number(safe.summary?.total || 0);
    if (safe.running || ((safe.repairing || safe.batch?.running) && total === 0)) return 'scanning';
    if (['cancelled', 'incomplete', 'error'].includes(safe.stage)) return 'partial';
    if (total > 0 && safe.scan_current === false) return 'stale';
    if (total > 0) return 'complete';
    return 'first_run';
  }

  function updateScanActionCopy(view) {
    if (state.targetKind === 'folder') {
      text(el.scan, 'Scan selected folder');
      return;
    }
    if (state.targetKind === 'file') {
      text(el.scan, 'Scan package');
      return;
    }
    if (view === 'first_run') text(el.scan, 'Scan my library');
    else if (view === 'partial' || view === 'stale') text(el.scan, 'Scan again');
    else text(el.scan, 'Check for changes');
  }

  function updateDashboardShell(status, forcedView) {
    if (!el?.healthWorkspace) return;
    const safe = status || {};
    const summary = safe.summary || {};
    const hasReports = Number(summary.total || 0) > 0;
    const view = forcedView || dashboardViewFor(safe);
    const viewChanged = view !== state.dashboardView;
    state.dashboardView = view;
    state.legacyLayout = legacyLayoutRequested();
    el.healthWorkspace.dataset.viewState = view;
    el.root.classList.toggle('lh-legacy-layout', state.legacyLayout);

    if (state.legacyLayout) {
      el.scanOptions.open = true;
      el.scanDetails.open = true;
      el.moreFilters.open = true;
    } else if (viewChanged) {
      el.scanOptions.open = view === 'first_run';
      if (view !== 'complete') el.scanDetails.open = false;
      el.moreFilters.open = false;
    }

    const hideResults = state.legacyLayout
      ? false
      : !hasReports || view === 'scanning';
    setHidden(el.overview, hideResults);
    setHidden(el.resultsSection, hideResults);
    setHidden(el.scanDetails, state.legacyLayout ? false : !hasReports || view === 'scanning');

    const target = safe.last_scan?.target?.label
      || (state.targetKind === 'library' ? 'All songs' : state.targetKind === 'folder' ? 'Selected folder' : 'One package');
    const completedAt = localDateTime(safe.last_scan?.completed_at || safe.last_scan?.started_at);
    text(
      el.scanOptionsSummary,
      hasReports
        ? `${target}${completedAt ? ` · completed ${completedAt}` : ''} · Change`
        : `${target} · recommended settings`,
    );

    if (view === 'scanning') {
      text(el.guidanceTitle, safe.repairing || safe.batch?.running ? 'Finishing a safe change' : 'Checking your library');
      text(el.guidanceCopy, safe.repairing || safe.batch?.running
        ? 'Library Doctor is validating the package state before it finishes.'
        : 'You can keep playing. The scan pauses automatically while a song is open.');
    } else if (view === 'partial') {
      text(el.guidanceTitle, 'The scan did not finish');
      text(el.guidanceCopy, 'Completed results are still available, but scan again before relying on the full library picture.');
    } else if (view === 'stale') {
      text(el.guidanceTitle, 'These results need a new scan');
      text(el.guidanceCopy, 'Library Doctor checks changed after this scan. Scan again before reviewing or applying repairs.');
    } else if (view === 'outcome') {
      const receipt = state.latestRepair;
      const uncertain = receipt?.file_state_copy?.includes('could not confirm');
      text(el.guidanceTitle, uncertain ? 'Package state needs verification' : receipt?.outcome === 'failure' ? 'Nothing changed' : 'Change completed');
      text(el.guidanceCopy, uncertain
        ? 'Review Activity and recovery, then scan this package again before making another change.'
        : receipt?.outcome === 'failure'
          ? 'The existing Feedpak remains in place. Review Activity and recovery for the next step.'
          : 'The result and any available Undo action are in Activity and recovery.');
    } else if (view === 'complete') {
      if (Number(summary.errors || 0) > 0) {
        text(el.guidanceTitle, `${pluralSongs(summary.errors)} need fixing`);
        text(el.guidanceCopy, 'Start with Needs fixing. Open a song to understand the problem and review any safe repair.');
      } else if (Number(summary.warnings || 0) > 0) {
        text(el.guidanceTitle, `${pluralSongs(summary.warnings)} may be affected in FeedBack`);
        text(el.guidanceCopy, 'Review the highest-impact compatibility findings first.');
      } else if (Number(summary.reviews || 0) > 0) {
        text(el.guidanceTitle, `${pluralSongs(summary.reviews)} have optional improvements`);
        text(el.guidanceCopy, 'These findings may be intentional and never require an automatic change.');
      } else {
        text(el.guidanceTitle, 'No problems found');
        text(el.guidanceCopy, `${pluralSongs(summary.total)} passed the current checks.`);
      }
    } else {
      text(el.guidanceTitle, 'Check your library');
      text(el.guidanceCopy, 'Start with the recommended scan. It reads song packages but never changes them.');
    }
    updateScanActionCopy(view);

    const running = !!safe.running || !!safe.repairing || !!safe.batch?.running;
    const showForce = hasReports && !running && (state.legacyLayout || el.scanOptions.open);
    setHidden(el.scanAll, !showForce);
  }


  function renderStatus(status) {
    state.status = status;
    const summary = status.summary || {};
    const running = !!status.running;
    const repairing = !!status.repairing;
    const batch = status.batch || null;
    const batchRunning = !!batch?.running;
    const hasReports = Number(summary.total || 0) > 0;
    actionRegistry.renderSummary(summary);

    setHidden(el.scan, running || repairing || batchRunning);
    setHidden(el.cancel, !running);
    setHidden(el.progress, !running);
    setHidden(el.error, status.stage !== 'error');
    setHidden(el.scanWarning, true);
    actionRegistry.updateTargetControls();

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
      const workers = status.worker_policy?.reason === 'not_started'
        ? 0 : Number(status.worker_policy?.selected_workers || 0);
      const workerCopy = workers > 0 ? ` | ${number(workers)} worker${workers === 1 ? '' : 's'}` : '';
      text(el.progressCount, total ? `${number(done)} of ${number(total)}${eta}${deep}${workerCopy}` : deep.slice(3));
      if (Array.isArray(status.discovery_errors) && status.discovery_errors.length) {
        text(el.scanWarning, 'Some folders could not be read. This scan cannot represent the full selected scope.');
        setHidden(el.scanWarning, false);
      }
    } else if (status.stage === 'complete') {
      text(el.status, `Scan complete for ${targetLabel}. ${number(summary.total)} package${summary.total === 1 ? '' : 's'} checked.`);
      const workers = Number(status.worker_policy?.selected_workers || 0);
      const details = [];
      if (status.reused) details.push(`${number(status.reused)} unchanged`);
      if (status.scanned && workers) details.push(`${number(workers)} worker${workers === 1 ? '' : 's'}`);
      text(el.progressCount, details.join(' | '));
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

    const [announcementKey, announcement] = scanAnnouncement(status, summary, targetLabel);
    announceScan(announcementKey, announcement);

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
      const workers = Number(last.worker_policy?.selected_workers || 0);
      const workerCopy = workers ? ` | ${number(workers)} worker${workers === 1 ? '' : 's'}` : '';
      text(el.scanProvenance, `Last scan: ${outcome} | ${scope} | ${profile} | ${coverage}${workerCopy}${when ? ` | ${when}` : ''}`);
      setHidden(el.scanProvenance, false);
      if (!running && !last.complete && status.stage === 'idle') {
        text(el.scanWarning, `The last scan was ${last.outcome || 'interrupted'}. Cached results may be incomplete.`);
        setHidden(el.scanWarning, false);
      } else if (!running && status.scan_current === false) {
        text(el.scanWarning, 'Library Doctor checks were updated after this scan. The saved results remain available for reference, but run this target again before reviewing or applying repairs.');
        setHidden(el.scanWarning, false);
      }
    } else {
      setHidden(el.scanProvenance, true);
    }
    if (!running && status.target?.repairs_available === false) {
      text(
        el.scanWarning,
        'This saved scan is no longer bound to an available folder. Scan that folder or package again before using Library Doctor repairs.',
      );
      setHidden(el.scanWarning, false);
    }
    actionRegistry.renderBatchStatus(batch, status);
    updateDashboardShell(status);
  }








  return {
    announce,
    clear,
    renderStatus,
    showError,
    updateDashboardShell,
  };
}
