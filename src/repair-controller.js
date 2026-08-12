export function createRepairController({
  actions: actionRegistry,
  apiRoot,
  badge,
  completedRepairChange,
  createConfirmation,
  document,
  duration,
  fileSize,
  getElements,
  isAbortError,
  make,
  number,
  plannedRepairChange,
  repairChangeCount,
  request,
  setHidden,
  state,
  text,
}) {
  const el = new Proxy({}, {
    get(_target, key) { return getElements()?.[key]; },
  });

  function currentPreviewUrl(packageName) {
    return `${apiRoot}/repair/media/current?package=${encodeURIComponent(packageName)}&v=${Date.now()}`;
  }

  function mediaReviewLabel(finding) {
    return finding?.code === 'media.preview-regenerate'
      ? 'Listen and choose a different preview'
      : 'Listen and choose a preview';
  }

  function repairChangeSummary(receipt) {
    const count = repairChangeCount(receipt);
    const positions = Number(receipt.musical_positions || 0);
    const itemName = receipt.item_name || 'item';
    const restored = receipt.outcome === 'restored' || receipt.action === 'restore';
    if (receipt.legacy_receipt) {
      return 'The package still matches an earlier successful Library Doctor repair. That older version did not store the exact item count in its result receipt.';
    }
    if (!count) return 'The saved original song data was restored.';
    const summaries = Array.isArray(receipt.repair_summaries)
      ? receipt.repair_summaries.filter((item) => repairChangeCount(item) > 0)
      : [];
    if (restored) {
      const restoredChange = summaries.length === 1 ? summaries[0] : receipt;
      if (restoredChange.change_kind === 'reviewed_decisions') {
        return `Restored the exact original HO/PO/tap fields for ${number(repairChangeCount(restoredChange))} reviewed ${repairChangeCount(restoredChange) === 1 ? 'decision' : 'decisions'}. The related review findings may return.`;
      }
      if (restoredChange.change_kind === 'replace_media') {
        if (restoredChange.media?.creates_preview) {
          return 'Removed the generated preview and restored the exact original manifest from recovery storage. The package has no embedded preview again.';
        }
        return 'Restored the exact original preview state from recovery storage. The repaired preview recommendation is expected to return.';
      }
      if (restoredChange.change_kind === 'reorder') {
        const restoredItem = restoredChange.item_name || 'timeline';
        return `Restored the saved original order for ${number(repairChangeCount(restoredChange))} ${restoredItem}${repairChangeCount(restoredChange) === 1 ? '' : 's'}. The repaired ordering finding is expected to return.`;
      }
      if (restoredChange.change_kind === 'normalize') {
        return `Restored ${number(repairChangeCount(restoredChange))} original negative muted-note fret ${repairChangeCount(restoredChange) === 1 ? 'value' : 'values'}. The repaired negative-mute finding is expected to return.`;
      }
      return `Restored the saved original song data for ${number(count)} safe ${count === 1 ? 'change' : 'changes'}. The related repaired findings are expected to return.`;
    }
    if (summaries.length > 1) {
      const changes = summaries.map((item) => plannedRepairChange(item));
      return `Applied ${number(summaries.length)} safe repair types in one transaction with ${number(count)} safe stored ${count === 1 ? 'change' : 'changes'}: ${changes.join('; ')}.`;
    }
    if (receipt.change_kind === 'combined' && summaries.length === 1) {
      return `${completedRepairChange(summaries[0])}.`;
    }
    if (receipt.change_kind === 'reviewed_decisions') {
      return `Applied ${number(count)} explicit reviewed HO/PO ${count === 1 ? 'decision' : 'decisions'}. Note timing, string, fret, and every technique outside HO/PO/tap were preserved.`;
    }
    if (receipt.change_kind === 'reorder') {
      return `${completedRepairChange(receipt)}${positions ? ` at ${number(positions)} musical ${positions === 1 ? 'position' : 'positions'}` : ''}. Every authored entry and property was preserved.`;
    }
    if (receipt.change_kind === 'replace_media') {
      const media = receipt.media || {};
      if (media.creates_preview) {
        return `Created a ${duration(media.candidate_duration_seconds || 30)} preview from the full song mix. Gameplay audio and all existing song assets were preserved.`;
      }
      return `Replaced the ${duration(media.original_duration_seconds)} preview with a new ${duration(media.candidate_duration_seconds || 30)} excerpt selected from the full song mix. The full song mix and all other Feedpak files were preserved.`;
    }
    if (receipt.change_kind === 'normalize') {
      return `${completedRepairChange(receipt)}${positions ? ` at ${number(positions)} musical ${positions === 1 ? 'position' : 'positions'}` : ''}.`;
    }
    if (receipt.change_kind === 'remove_redundant') {
      return `${completedRepairChange(receipt)}${positions ? ` at ${number(positions)} musical ${positions === 1 ? 'position' : 'positions'}` : ''}.`;
    }
    return `Removed ${number(count)} redundant ${itemName} ${count === 1 ? 'copy' : 'copies'}${positions ? ` at ${number(positions)} musical ${positions === 1 ? 'position' : 'positions'}` : ''}. The first identical authored entry was kept.`;
  }

  function showRepairedPackage(receipt) {
    state.filter = 'all';
    state.ruleCode = '';
    state.offset = 0;
    state.query = String(receipt.title || receipt.package || '').trim();
    el.search.value = state.query;
    actionRegistry.updateFilterButtons();
    actionRegistry.loadResults().then(() => {
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

  function renderRepairResult(receipt, { reveal = true } = {}) {
    const receiptKey = receipt && (receipt.backup_id || receipt.id);
    if (!receipt || !el.repairResult || receiptKey === state.dismissedRepairId) return;
    state.latestRepair = receipt;
    const failed = receipt.outcome === 'failure';
    const restored = receipt.outcome === 'restored' || receipt.action === 'restore';
    const finalized = receipt.outcome === 'finalized' || receipt.action === 'finalize';
    const cleanupRequired = Boolean(receipt.file_handling?.backup_cleanup_required);
    const panel = el.repairResult;
    setHidden(el.activitySection, false);
    el.activitySection.open = reveal;
    panel.replaceChildren();
    panel.dataset.outcome = failed ? 'failure' : restored ? 'restored' : finalized ? 'finalized' : 'success';
    if (failed) panel.setAttribute('role', 'alert');
    else panel.removeAttribute('role');

    const heading = make('div', 'lh-repair-result-heading');
    const copy = make('div');
    copy.appendChild(badge(
      failed ? 'Nothing changed' : restored ? 'Original restored' : finalized ? 'Undo backup deleted' : cleanupRequired ? 'Change completed · cleanup needed' : 'Change completed',
      failed ? 'error' : finalized || cleanupRequired ? 'review' : 'good',
    ));
    copy.appendChild(make(
      'h3',
      '',
      failed
        ? 'The repair was not completed'
        : restored
          ? receipt.change_kind === 'replace_media'
            ? receipt.media?.creates_preview
              ? 'The generated preview was removed'
              : 'The original preview was restored'
            : 'The original song data was restored'
          : finalized
            ? 'The repaired version was finalized'
          : cleanupRequired
            ? 'The preview is repaired, but cleanup still needs attention'
            : 'The repair completed successfully',
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
      state.latestRepair = null;
      setHidden(panel, true);
      setHidden(el.activitySection, true);
      actionRegistry.updateDashboardShell(state.status || {});
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
      ['What to expect in game', receipt.player_result || 'The original song data is present again, so the repaired finding may return when the package is scanned.'],
      ['Why this is useful', receipt.user_value || 'This returns the song data to the state saved immediately before the repair.'],
      ['What happened to the Feedpak', receipt.file_handling?.summary || 'The original song data was restored at the same package path. No duplicate song was added.'],
    ] : finalized ? [
      ['What happened', `The recovery copy was removed and ${fileSize(receipt.file_handling?.recovery_bytes_freed)} was released.`],
      ['What to expect in game', receipt.player_result || 'FeedBack continues using the repaired Feedpak exactly as before.'],
      ['Undo availability', 'This repair can no longer be undone from Library Doctor because its saved original data was removed.'],
      ['What happened to the Feedpak', receipt.file_handling?.summary || 'The Feedpak itself was not changed. Only its private recovery copy was removed.'],
    ] : [
      ['What changed', repairChangeSummary(receipt)],
      ['What to expect in game', receipt.player_result || 'FeedBack will load the repaired song data the next time the song is opened.'],
      ['Why the fix matters', receipt.user_value || 'The repaired data is now unambiguous and passed the current validation checks.'],
      ['What happened to the Feedpak', receipt.file_handling?.summary || 'The validated candidate replaced the package at the same path. No duplicate song was added, and original changed song-data files were backed up.'],
    ];
    blocks.forEach(([label, value]) => {
      const block = make('div', 'lh-repair-result-answer');
      block.appendChild(make('strong', '', label));
      block.appendChild(make('p', '', value));
      answers.appendChild(block);
    });
    panel.appendChild(answers);

    const completedPreview = (
      !failed && !restored && !finalized && receipt.change_kind === 'replace_media'
    );
    if (completedPreview) {
      const preview = make('section', 'lh-media-review lh-completed-preview');
      preview.appendChild(make('strong', '', 'Your finished preview'));
      preview.appendChild(make(
        'p',
        'lh-muted',
        'This is the preview now stored in the Feedpak and used while browsing the song library.',
      ));
      const audio = document.createElement('audio');
      audio.className = 'lh-media-preview-player';
      audio.controls = true;
      audio.preload = 'metadata';
      audio.src = currentPreviewUrl(receipt.package);
      audio.setAttribute('aria-label', `Finished preview for ${receipt.title || receipt.package}`);
      preview.appendChild(audio);
      panel.appendChild(preview);
    }

    if (!failed) {
      panel.appendChild(make(
        'p',
        'lh-repair-verification',
        restored
          ? `The exact original state was restored and validated.${receipt.file_handling?.backup_removed ? ' The now-redundant recovery copy was removed.' : ' The recovery copy could not be removed automatically, but it is no longer needed for Undo.'}${receipt.cache_updated === false ? ' Scan this package again to refresh the displayed result.' : ''}${receipt.receipt_saved === false ? ' The recovery succeeded, but this result could not be saved to repair history.' : ''}`
          : finalized
            ? `The current package members were checked against the recovery record before it was removed.${receipt.receipt_saved === false ? ' The recovery copy was removed, but this result could not be saved to repair history.' : ''}`
          : cleanupRequired
            ? `The complete repaired candidate passed validation before it replaced the existing package. Its temporary recovery copy could not be removed automatically; use the cleanup option below to finish.${receipt.cache_updated === false ? ' Scan this package again to refresh its displayed result.' : ''}`
            : `The complete repaired candidate passed validation before it replaced the existing package.${completedPreview ? ' Its temporary recovery copy was removed automatically, so this preview repair is fully complete.' : ''}${receipt.cache_updated === false ? ' The repair succeeded, but you should scan this package again to refresh its displayed result.' : ''}${receipt.receipt_saved === false ? completedPreview ? ' The repair succeeded and left no recovery copy, but this result could not be saved to repair history.' : ' The repair succeeded, but this result could not be saved to repair history; the recovery backup still exists.' : ''}`,
      ));
    }

    const performance = receipt.performance;
    const elapsedSeconds = Number(performance?.elapsed_seconds);
    if (
      !failed && !restored && !finalized
      && Number.isFinite(elapsedSeconds) && elapsedSeconds >= 0
    ) {
      const validationCopy = performance.deep_audio_reused
        ? 'Reused the completed Deep Audio scan for unchanged audio; changed song data and archive integrity were still validated.'
        : performance.verified_scan_report_reused
          ? 'Reused the completed Deep Audio scan for the original Feedpak, then deeply checked the newly generated preview.'
          : performance.deep_audio_requested
            ? 'Ran fresh Deep Audio validation.'
            : 'Ran the normal package validation path.';
      panel.appendChild(make(
        'p',
        'lh-repair-verification',
        `Repair checks: ${duration(elapsedSeconds)}. ${validationCopy}`,
      ));
    }

    const actions = make('div', 'lh-repair-buttons');
    if (!failed) {
      const show = make('button', 'lh-button', 'Show this package');
      show.type = 'button';
      show.addEventListener('click', () => showRepairedPackage(receipt));
      actions.appendChild(show);
    }
    const previewRegion = make('div', 'lh-repair-preview');
    if (completedPreview) {
      const replace = make('button', 'lh-button', 'Create a different preview');
      replace.type = 'button';
      replace.addEventListener('click', () => actionRegistry.previewRepair(
        receipt,
        { code: 'media.preview-regenerate' },
        replace,
        previewRegion,
      ));
      actions.appendChild(replace);
    }
    if (!failed && !restored && !finalized && receipt.backup_id && receipt.undo_available !== false) {
      const undo = make('button', 'lh-button', 'Undo repair');
      undo.type = 'button';
      undo.addEventListener('click', () => confirmRestore(receipt, undo, actions));
      actions.appendChild(undo);
      const finalize = make(
        'button',
        'lh-button',
        completedPreview ? 'Delete temporary recovery backup…' : 'Delete Undo backup…',
      );
      finalize.type = 'button';
      finalize.addEventListener('click', () => confirmFinalizeRecovery(receipt, finalize, actions));
      actions.appendChild(finalize);
    } else if (restored && receipt.file_handling?.backup_retained && receipt.backup_id) {
      const cleanup = make('button', 'lh-button', 'Remove redundant recovery copy');
      cleanup.type = 'button';
      cleanup.addEventListener('click', () => confirmFinalizeRecovery(receipt, cleanup, actions));
      actions.appendChild(cleanup);
    }
    panel.appendChild(actions);
    if (completedPreview) panel.appendChild(previewRegion);
    setHidden(panel, false);
    if (reveal) {
      text(
        el.activityStatus,
        failed ? 'Nothing changed. Repair details are available.' : 'Change completed. Activity and recovery details are available.',
      );
      actionRegistry.updateDashboardShell(state.status || {}, 'outcome');
      panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } else {
      text(el.activityStatus, '');
      actionRegistry.updateDashboardShell(state.status || {});
    }
  }

  function confirmRestore(receipt, trigger, actions) {
    trigger.disabled = true;
    const message = receipt.change_kind === 'replace_media'
        ? receipt.media?.creates_preview
          ? 'Undo will restore the exact original manifest and remove the generated preview. The missing-preview recommendation is expected to return. Every other Feedpak file is preserved.'
          : 'Undo will restore the exact original preview saved before conversion. The repaired preview recommendation is expected to return. Every other Feedpak file is preserved.'
        : receipt.rule_code === 'package.all-safe'
        ? 'Undo will restore all original song-data files saved before this combined repair. The repaired safe findings are expected to return. Other package files are preserved.'
        : 'Undo will restore the original song-data files saved before this repair. The repaired finding is expected to return. Other package files are preserved.';
    const { region } = createConfirmation({
      className: 'lh-repair-confirm',
      message,
      confirmLabel: receipt.change_kind === 'replace_media'
        ? 'Restore original preview'
        : 'Restore original song data',
      confirmClass: 'lh-button lh-button-danger',
      trigger,
      onConfirm: (confirm, cancel) => restoreRepair(receipt, confirm, cancel),
    });
    actions.parentNode.insertBefore(region, actions.nextSibling);
  }

  function confirmFinalizeRecovery(receipt, trigger, actions) {
    trigger.disabled = true;
    const retainedBytes = Number(receipt.file_handling?.backup_size_bytes || 0);
    const message = receipt.preview_cleanup
        ? `Remove the temporary preview recovery copy${retainedBytes ? ` and release ${fileSize(retainedBytes)}` : ''}? The repaired preview is already active and the Feedpak will not change.`
        : receipt.outcome === 'restored' || receipt.action === 'restore'
        ? `Remove the redundant recovery copy${retainedBytes ? ` and release ${fileSize(retainedBytes)}` : ''}? The original data is already restored, so the Feedpak will not change.`
        : `Keep the repaired Feedpak and remove its recovery copy${retainedBytes ? ` to release ${fileSize(retainedBytes)}` : ''}? The Feedpak will not change, but this repair can no longer be undone from Library Doctor.`;
    const { region } = createConfirmation({
      className: 'lh-repair-confirm',
      message,
      confirmLabel: 'Remove recovery copy',
      confirmClass: 'lh-button lh-button-danger',
      trigger,
      onConfirm: (confirm, cancel) => finalizeRecovery(receipt, confirm, cancel),
    });
    actions.parentNode.insertBefore(region, actions.nextSibling);
  }

  async function finalizeRecovery(receipt, confirm, cancel) {
    confirm.disabled = true;
    cancel.disabled = true;
    text(confirm, 'Checking and removing...');
    try {
      const result = await request('/repair/recovery/finalize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package: receipt.package, backup_id: receipt.backup_id }),
      });
      result.id = `finalize-${receipt.backup_id}-${Date.now()}`;
      renderRepairResult(result);
      await actionRegistry.refreshStatus();
    } catch (error) {
      confirm.disabled = false;
      cancel.disabled = false;
      text(confirm, 'Remove recovery copy');
      confirm.parentNode.appendChild(make('p', 'lh-inline-error', error.message));
    }
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
      await actionRegistry.refreshStatus();
      await Promise.all([actionRegistry.loadRules(), actionRegistry.loadResults()]);
    } catch (error) {
      renderRepairFailure(receipt, error);
    }
  }

  async function loadRepairHistory() {
    try {
      const payload = await request('/repair/history?limit=1');
      if (!state.active) return;
      const latest = Array.isArray(payload?.items) ? payload.items[0] : null;
      if (latest) renderRepairResult(latest, { reveal: false });
    } catch (error) {
      if (isAbortError(error)) return;
      console.warn('[Library Doctor] Could not load repair history:', error);
    }
  }

  function repairControls(report, finding) {
    if (report.features?.repair_scan_current === false) return null;
    const definition = state.repairRules[finding.code];
    if (!definition || !['safe_automatic', 'review_required'].includes(definition.safety)) return null;
    const eligibility = report.features?.repair_eligibility?.[finding.code];
    if (eligibility && eligibility.status !== 'automatic') return null;
    const wrapper = make('div', 'lh-repair-action');
    const mediaRepair = definition.change_kind === 'replace_media';
    const button = make(
      'button',
      mediaRepair ? 'lh-button lh-button-primary' : 'lh-button lh-button-safe',
      mediaRepair ? mediaReviewLabel(finding) : 'Review safe fix',
    );
    button.type = 'button';
    const region = make('div', 'lh-repair-preview');
    button.addEventListener('click', () => actionRegistry.previewRepair(report, finding, button, region));
    wrapper.appendChild(button);
    if (mediaRepair) {
      const automatic = make('button', 'lh-button', 'Create automatically and finish');
      automatic.type = 'button';
      automatic.addEventListener('click', () => actionRegistry.confirmAutomaticPreviewRepair(
        report, finding, automatic, button, region,
      ));
      wrapper.appendChild(automatic);
    }
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
          'A complete repaired candidate is validated first. It then replaces the existing Feedpak at the same path, while the original changed song-data files are kept in private recovery storage. No duplicate song is added to the library.'
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
      plan.change_kind === 'replace_media'
        ? 'If audio generation, temporary recovery, integrity checking, or validation fails, the existing Feedpak is not replaced. After a successful repair, the temporary recovery copy is removed automatically and the new preview is ready to use.'
        : 'If candidate creation, backup, integrity checking, or validation fails, the existing Feedpak is not replaced. After a successful repair, Undo can restore the saved original song data.',
    ));
  }

  function safeRepairCodes(report) {
    if (report.features?.repair_scan_current === false) return new Set();
    return new Set(
      (Array.isArray(report.findings) ? report.findings : [])
        .map((finding) => finding.code)
        .filter((code) => (
          state.repairRules[code]?.safety === 'safe_automatic'
          && (!report.features?.repair_eligibility?.[code]
            || report.features.repair_eligibility[code].status === 'automatic')
        )),
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
          `This will apply ${number(plan.rule_count)} safe repair ${plan.rule_count === 1 ? 'type' : 'types'} and make ${number(repairChangeCount(plan))} safe stored ${repairChangeCount(plan) === 1 ? 'change' : 'changes'} across ${number(plan.member_count)} song-data ${plan.member_count === 1 ? 'file' : 'files'}.`,
        ));
        const list = make('ul', 'lh-all-safe-list');
        (plan.repair_summaries || []).forEach((summary) => {
          list.appendChild(make(
            'li',
            '',
            `${summary.title}: ${plannedRepairChange(summary)} across ${number(summary.member_count)} song-data ${summary.member_count === 1 ? 'file' : 'files'}.`,
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
            ? 'Library Doctor cannot safely apply the combined repair because at least one referenced song-data file could not be prepared. Nothing will be changed.'
            : 'No supported safe repairs are currently available in this package.',
        ));
        blockers.forEach((blocker) => {
          card.appendChild(make(
            'p',
            'lh-repair-warning',
            `${blocker.member_path || 'Referenced song-data file'}: ${blocker.message}`,
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
      await actionRegistry.refreshStatus();
      await Promise.all([
        actionRegistry.loadRules(), actionRegistry.loadResults(), actionRegistry.refreshSelectedSongTool(report.package),
      ]);
    } catch (error) {
      apply.disabled = false;
      cancel.disabled = false;
      text(apply, 'Apply all safe fixes');
      region.appendChild(make('p', 'lh-inline-error', error.message));
      renderRepairFailure(report, error);
    }
  }


  return {
    allSafeRepairControls,
    appendRepairPreviewAnswers,
    confirmFinalizeRecovery,
    loadRepairHistory,
    renderRepairFailure,
    renderRepairResult,
    repairControls,
  };
}
