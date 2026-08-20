import { createBatchResultsView } from './batch-results-view.js';

export function createBatchController({
  actions,
  badge,
  completedRepairChange,
  createConfirmation,
  duration,
  fileSize,
  getElements,
  make,
  number,
  pluralSongs,
  repairChangeCount,
  request,
  setHidden,
  state,
  text,
}) {
  const el = new Proxy({}, {
    get(_target, key) { return getElements()?.[key]; },
  });
  const resultView = createBatchResultsView({
    badge,
    completedRepairChange,
    fileSize,
    getElements,
    make,
    number,
    onReviewUndo: (...args) => reviewBatchUndo(...args),
    onStartFinalizePreview: (...args) => startBatchFinalizePreview(...args),
    onStartUndoPreview: (...args) => startBatchUndoPreview(...args),
    repairActions: actions,
    repairChangeCount,
    summaryGrid: batchSummaryGrid,
    text,
  });

  function batchSummaryGrid(items) {
    const grid = make('div', 'lh-batch-summary');
    items.forEach(([value, label]) => {
      const item = make('div');
      item.appendChild(make('strong', '', typeof value === 'string' ? value : number(value)));
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
    const repairsAvailable = scannerStatus?.target?.repairs_available !== false;
    const phase = batch?.phase || 'idle';
    const running = !!batch?.running;
    const recoveryResult = batch?.result || batch?.last_result;
    const pendingRecoveryCount = Number(recoveryResult?.undoable_count || 0)
      + Number(recoveryResult?.preview_cleanup_required_count || 0);
    const hasPendingRecovery = pendingRecoveryCount > 0;
    state.batch = batch || null;
    setHidden(el.batchSection, !hasReports);
    if (!hasReports) {
      state.batchAttentionKey = '';
      return;
    }

    const attentionPhases = new Set([
      'ready', 'completed', 'cancelled', 'error',
      'undo_ready', 'undo_completed', 'undo_cancelled',
      'finalize_ready', 'finalize_completed', 'finalize_cancelled',
    ]);
    const attentionKey = running
      ? `running:${phase}:${batch?.mode || ''}`
      : hasPendingRecovery
        ? `recovery:${recoveryResult?.id || phase}`
        : attentionPhases.has(phase)
          ? [
            phase,
            batch?.preview?.batch_plan_id || '',
            batch?.result?.id || batch?.last_result?.id || '',
            batch?.undo_preview?.undo_plan_id || batch?.undo_result?.id || '',
            batch?.finalize_preview?.finalize_plan_id || batch?.finalize_result?.id || '',
          ].join(':')
          : '';
    if (attentionKey && state.batchAttentionKey !== attentionKey && el.batchPanel) {
      el.batchPanel.open = true;
    }
    state.batchAttentionKey = attentionKey;

    text(
      el.batchSummary,
      running
        ? batch?.message || 'A safe-repair operation is in progress.'
        : hasPendingRecovery
          ? `${pluralSongs(pendingRecoveryCount)} ${pendingRecoveryCount === 1 ? 'still needs' : 'still need'} Undo or Finalize.`
          : phase === 'ready' && batch?.preview
            ? `${pluralSongs(batch.preview.eligible_count)} are ready for review.`
            : completeScope
              ? 'Preview safe fixes for several songs before changing anything.'
              : 'Finish the current scan before previewing multi-song fixes.',
    );

    const target = batch?.target?.label || scannerStatus?.target?.label || 'current scan scope';
    text(
      el.batchCopy,
      hasPendingRecovery
        ? 'Resolve the previous batch first. Undo restores its saved originals; Finalize keeps its repairs and removes the recovery copies.'
        : !repairsAvailable
        ? 'This saved scan is no longer bound to an available folder. Scan that folder or package again before using repairs.'
        : completeScope
        ? `See which safe fixes are available in ${target}. Flagged audio previews are included only if you choose that option.`
        : 'Finish this scan before previewing fixes for several songs. Incomplete results are never used for changes.',
    );
    setHidden(el.batchReview, running);
    setHidden(el.batchCancel, !running);
    el.batchReview.disabled = hasPendingRecovery || !repairsAvailable || !completeScope || !!scannerStatus?.running || (!!scannerStatus?.repairing && !running);
    el.batchPreviewMedia.disabled = hasPendingRecovery || !repairsAvailable || running || !completeScope || !!scannerStatus?.running;
    text(
      el.batchReview,
      hasPendingRecovery
        ? 'Resolve previous repairs first'
        : phase === 'ready' && batch?.preview
        ? `Review fixes for ${pluralSongs(batch.preview.eligible_count)}`
        : 'Preview multi-song fixes',
    );
    text(
      el.batchCancel,
      ['apply', 'undo-apply', 'finalize-apply'].includes(batch?.mode)
        ? 'Stop after current song' : 'Stop preview',
    );

    setHidden(el.batchProgress, !running);
    setHidden(el.batchLiveCounts, true);
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
      const live = batch?.live_outcomes;
      if (batch?.mode === 'apply' && live && Number(live.completed || 0) > 0) {
        text(
          el.batchLiveCounts,
          `${number(live.repaired)} repaired | ${number(live.partial)} partial | ${number(live.skipped)} skipped safely | ${number(live.failed)} failed${Number(live.previews_repaired || 0) ? ` | ${number(live.previews_repaired)} previews created` : ''}`,
        );
        setHidden(el.batchLiveCounts, false);
      }
    }

    const undoPhase = phase.startsWith('undo')
      || (['paused', 'cancelling'].includes(phase) && String(batch?.mode || '').startsWith('undo'));
    const finalizePhase = phase.startsWith('finalize')
      || (['paused', 'cancelling'].includes(phase) && String(batch?.mode || '').startsWith('finalize'));
    const activeResult = batch?.result || (hasPendingRecovery
      ? batch?.last_result
      : (
      phase === 'idle' || phase === 'stale' || undoPhase || finalizePhase
        || (phase === 'error' && ['undo', 'finalize'].some((mode) => String(batch?.mode || '').startsWith(mode)))
        ? batch?.last_result : null
      ));
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
      batch?.finalize_preview?.finalize_plan_id || '',
      batch?.finalize_result?.id || '',
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
    if (phase === 'finalize_ready' && batch.finalize_preview) {
      renderBatchFinalizePreview(batch.finalize_preview);
    }
    if (
      batch?.undo_result
      && ['undo_completed', 'undo_cancelled'].includes(phase)
    ) {
      resultView.renderUndo(batch.undo_result);
    }
    if (
      batch?.finalize_result
      && ['finalize_completed', 'finalize_cancelled'].includes(phase)
    ) {
      resultView.renderFinalize(batch.finalize_result);
    }
    if (activeResult) resultView.renderBatch(activeResult, !batch?.result, batch);
    if (
      !activeResult
      && ['stale', 'error', 'cancelled', 'undo_cancelled', 'finalize_cancelled'].includes(phase)
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
      `${number(preview.eligible_count)} of ${number(preview.scope_package_count)} scanned Feedpaks still match the completed scan and have a repair selected for this batch.`,
    ));
    card.appendChild(batchSummaryGrid([
      [preview.eligible_count, 'Eligible Feedpaks'],
      [preview.safe_repair_package_count, 'With safe song-data fixes'],
      [preview.preview_repair_count, 'With preview repairs'],
      [preview.blocked_count, 'Blocked and excluded'],
    ]));

    const rules = make('details', 'lh-batch-details');
    rules.open = true;
    rules.appendChild(make('summary', '', `Repair types found (${number(preview.rule_summaries?.length || 0)})`));
    const ruleList = make('ul', 'lh-batch-list');
    (preview.rule_summaries || []).forEach((rule) => {
      const item = make('li');
      item.appendChild(make('strong', '', rule.title || rule.rule_code));
      item.appendChild(make(
        'span',
        '',
        `${number(rule.reported_affected_count)} affected ${Number(rule.reported_affected_count) === 1 ? 'location was' : 'locations were'} reported by the completed scan across ${number(rule.package_count)} Feedpak${Number(rule.package_count) === 1 ? '' : 's'}.`,
      ));
      ruleList.appendChild(item);
    });
    rules.appendChild(ruleList);
    card.appendChild(rules);
    card.appendChild(make(
      'p',
      'lh-muted',
      'These are scan findings, not promised edit totals. Library Doctor recalculates the selected repairs from the current song data and runs all safety checks when each Feedpak is reached. The completed result shows the exact changes made.',
    ));

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
        `${number(item.safe_rule_count || 0)} safe song-data repair ${Number(item.safe_rule_count || 0) === 1 ? 'type' : 'types'}${item.preview_repair ? ' | automatic audio preview' : ''} | ${item.package}`,
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
    if (Number(preview.preview_repair_count || 0) > 0) {
      card.appendChild(make(
        'p',
        'lh-batch-warning',
        `${number(preview.preview_repair_count)} flagged preview${Number(preview.preview_repair_count) === 1 ? '' : 's'} will be selected and created automatically. This review does not generate or play those excerpts. Successful preview repairs are finalized after validation and are not included in Undo; you can replace any result later in Song Tools.`,
      ));
    }
    card.appendChild(make(
      'p',
      'lh-muted',
      `${preview.deep_audio ? 'For archived Feedpaks, song-data-only repairs reuse signature-bound Deep Audio findings after a full integrity check of unchanged audio. Unpacked packages and preview repairs still validate their audio deeply. ' : ''}Audio encoding can make preview repairs slower. Gameplay pauses the batch between Feedpaks. Stopping also takes effect after the current Feedpak finishes safely.`,
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
    const { region } = createConfirmation({
      className: 'lh-batch-confirm',
      message: `Apply the selected repairs to ${number(preview.eligible_count)} Feedpak${Number(preview.eligible_count) === 1 ? '' : 's'}? Each package is recalculated, safety-checked, validated, and saved separately. A package that no longer qualifies is skipped without being changed. Safe song-data repairs retain Undo backups.${Number(preview.preview_repair_count || 0) > 0 ? ' Automatic preview repairs are finalized after successful validation and do not retain Undo copies.' : ''} If a later package fails, earlier successful repairs remain in place.`,
      confirmLabel: 'Apply batch repair',
      cancelLabel: 'Go back',
      trigger,
      onConfirm: (apply, cancel) => applyBatchRepairs(preview, apply, cancel),
    });
    card.appendChild(region);
  }

  async function startBatchPreview() {
    el.batchReview.disabled = true;
    setHidden(el.batchPreview, false);
    el.batchPreview.replaceChildren(make('p', 'lh-muted', 'Preparing a read-only batch preview...'));
    try {
      const batch = await request('/repair/batch/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          include_preview_repairs: !!el.batchPreviewMedia.checked,
        }),
      });
      const status = { ...(state.status || {}), repairing: true, batch };
      actions.renderStatus(status);
      actions.schedulePoll(200);
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
      actions.renderStatus({ ...(state.status || {}), repairing: true, batch });
      actions.schedulePoll(200);
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
      actions.renderStatus({ ...(state.status || {}), repairing: true, batch: payload.status });
      actions.schedulePoll(200);
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
      actions.renderStatus({ ...(state.status || {}), repairing: true, batch });
      actions.schedulePoll(200);
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
      [preview.changes_to_restore ?? preview.entries_to_restore, 'Safe changes to restore'],
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
        `${number(repairChangeCount(item))} safe song-data ${repairChangeCount(item) === 1 ? 'change will' : 'changes will'} return to the saved original state across ${number(item.member_count)} ${Number(item.member_count) === 1 ? 'file' : 'files'}.${item.preview_repaired ? ' The finalized generated preview will remain.' : ''} ${item.package}`,
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
        'Blocked Feedpaks will remain unchanged. Undo will continue only with packages whose repaired song-data files and retained backups still pass every safety check.',
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
    const { region } = createConfirmation({
      className: 'lh-batch-confirm lh-batch-undo-confirm',
      message: `Restore the saved original song data for ${number(preview.eligible_count)} Feedpak${Number(preview.eligible_count) === 1 ? '' : 's'}? ${number(preview.changes_to_restore ?? preview.entries_to_restore)} safe song-data ${Number(preview.changes_to_restore ?? preview.entries_to_restore) === 1 ? 'change will' : 'changes will'} return to the saved state, and the related findings are expected to return. Finalized generated previews and other package files are preserved.`,
      confirmLabel: `Undo repairs for ${number(preview.eligible_count)} Feedpaks`,
      confirmClass: 'lh-button lh-button-danger',
      trigger,
      onConfirm: (apply, cancel) => applyBatchUndo(preview, apply, cancel),
    });
    card.appendChild(region);
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
      actions.renderStatus({ ...(state.status || {}), repairing: true, batch });
      actions.schedulePoll(200);
    } catch (error) {
      apply.disabled = false;
      cancel.disabled = false;
      text(apply, `Undo repairs for ${number(preview.eligible_count)} Feedpaks`);
      apply.parentNode.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  async function startBatchFinalizePreview(trigger) {
    trigger.disabled = true;
    text(trigger, 'Checking recovery copies...');
    try {
      const batch = await request('/repair/batch/finalize/preview', { method: 'POST' });
      actions.renderStatus({ ...(state.status || {}), repairing: true, batch });
      actions.schedulePoll(200);
    } catch (error) {
      trigger.disabled = false;
      text(trigger, 'Review Finalize all remaining repairs');
      trigger.parentNode.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }


  function renderBatchFinalizePreview(preview) {
    const card = make('div', 'lh-batch-card');
    card.appendChild(make('h4', '', 'Finalization review ready - nothing removed yet'));
    card.appendChild(make(
      'p',
      '',
      `${number(preview.eligible_count)} remaining recovery ${Number(preview.eligible_count) === 1 ? 'copy is' : 'copies are'} verified and ready to remove. Finalization keeps the current Feedpaks and permanently removes Library Doctor Undo for those repairs.`,
    ));
    card.appendChild(batchSummaryGrid([
      [preview.eligible_count, 'Ready to finalize'],
      [preview.blocked_count, 'Kept for safety'],
      [preview.already_finalized_count, 'Already finalized'],
      [fileSize(preview.recovery_bytes_to_free), 'Recovery storage to free'],
    ]));

    const packages = make('details', 'lh-batch-details');
    packages.open = true;
    packages.appendChild(make('summary', '', `Recovery copies ready to remove (${number(preview.eligible_count)})`));
    const packageList = make('ul', 'lh-batch-list');
    (preview.packages || []).slice(0, 250).forEach((item) => {
      const row = make('li');
      row.appendChild(make('strong', '', `${item.title || item.package}${item.artist ? ` - ${item.artist}` : ''}`));
      row.appendChild(make(
        'span',
        '',
        `${fileSize(item.recovery_bytes)} ${item.recovery_kind === 'preview' ? 'temporary preview' : 'song-data'} recovery copy | ${number(item.member_count)} verified ${Number(item.member_count) === 1 ? 'file' : 'files'} | ${item.package}`,
      ));
      packageList.appendChild(row);
    });
    if ((preview.packages || []).length > 250) {
      packageList.appendChild(make(
        'li',
        '',
        `${number(preview.packages.length - 250)} additional verified recovery copies are included in the totals above.`,
      ));
    }
    packages.appendChild(packageList);
    card.appendChild(packages);

    if (Number(preview.blocked_count || 0) > 0) {
      const blocked = make('details', 'lh-batch-details');
      blocked.open = true;
      blocked.appendChild(make('summary', '', `Recovery copies kept for safety (${number(preview.blocked_count)})`));
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
        'These copies will not be removed. A changed, missing, or uncertain Feedpak is excluded rather than losing its saved original data.',
      ));
    }

    card.appendChild(make('p', '', preview.file_handling));
    card.appendChild(make(
      'p',
      'lh-batch-warning',
      'Finalization is permanent. It does not change the playable Feedpaks, but Library Doctor cannot restore their pre-repair song data after the recovery copies are removed.',
    ));
    if (Number(preview.eligible_count || 0) > 0) {
      const actions = make('div', 'lh-repair-buttons');
      const continueButton = make('button', 'lh-button lh-button-danger', 'Continue to finalization confirmation');
      continueButton.type = 'button';
      continueButton.addEventListener('click', () => showBatchFinalizeConfirmation(preview, continueButton, card));
      actions.appendChild(continueButton);
      card.appendChild(actions);
    }
    el.batchPreview.appendChild(card);
  }

  function showBatchFinalizeConfirmation(preview, trigger, card) {
    trigger.disabled = true;
    const { region } = createConfirmation({
      className: 'lh-batch-confirm lh-batch-undo-confirm',
      message: `Keep ${number(preview.eligible_count)} current repaired Feedpak${Number(preview.eligible_count) === 1 ? '' : 's'} and permanently remove their recovery ${Number(preview.eligible_count) === 1 ? 'copy' : 'copies'}? This should free ${fileSize(preview.recovery_bytes_to_free)}. Every Feedpak is reverified immediately before its copy is removed; anything changed or uncertain is skipped. This cannot be undone.`,
      confirmLabel: `Finalize ${number(preview.eligible_count)} repairs`,
      confirmClass: 'lh-button lh-button-danger',
      trigger,
      onConfirm: (apply, cancel) => applyBatchFinalization(preview, apply, cancel),
    });
    card.appendChild(region);
  }

  async function applyBatchFinalization(preview, apply, cancel) {
    apply.disabled = true;
    cancel.disabled = true;
    text(apply, 'Starting finalization...');
    try {
      const batch = await request('/repair/batch/finalize/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ finalize_plan_id: preview.finalize_plan_id }),
      });
      actions.renderStatus({ ...(state.status || {}), repairing: true, batch });
      actions.schedulePoll(200);
    } catch (error) {
      apply.disabled = false;
      cancel.disabled = false;
      text(apply, `Finalize ${number(preview.eligible_count)} repairs`);
      apply.parentNode.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  function reviewBatchUndo(outcome, trigger, row) {
    trigger.disabled = true;
    const confirmation = make('div', 'lh-batch-confirm');
    confirmation.appendChild(make(
      'p',
      '',
      `Undo restores the original song-data files saved for this Feedpak before its batch repair. Other successfully repaired Feedpaks are not affected.${outcome.preview_repaired ? ' Its finalized generated preview remains in place.' : ''}`,
    ));
    const restore = make('button', 'lh-button lh-button-danger', 'Restore original song data');
    const keep = make('button', 'lh-button', 'Cancel');
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
      actions.renderRepairResult(result);
      await actions.refreshStatus();
      await Promise.all([
        actions.loadRules(), actions.loadResults(), actions.refreshSelectedSongTool(outcome.package),
      ]);
    } catch (error) {
      restore.disabled = false;
      keep.disabled = false;
      text(restore, 'Restore original song data');
      confirmation.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  return { cancelBatchOperation, renderBatchStatus, startBatchPreview };
}
