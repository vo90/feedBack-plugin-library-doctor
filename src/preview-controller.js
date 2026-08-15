export function createPreviewController({
  actions: actionRegistry,
  apiRoot,
  document,
  duration,
  make,
  number,
  repairChangeCount,
  request,
  state,
  text,
}) {
  function mediaReviewLabel(finding) {
    return finding?.code === 'media.preview-regenerate'
      ? 'Listen and choose a different preview'
      : 'Listen and choose a preview';
  }

  function previewSetConfirmation(result, mode) {
    return {
      mode,
      media: result?.media || {},
    };
  }

  function confirmAutomaticPreviewRepair(report, finding, trigger, manual, region) {
    trigger.disabled = true;
    manual.disabled = true;
    region.replaceChildren();
    const card = make('div', 'lh-repair-card');
    card.appendChild(make('strong', '', 'Create the preview automatically?'));
    card.appendChild(make(
      'p',
      '',
      'Library Doctor will select a representative point from the full song mix, generate a new 30-second preview, and validate the complete Feedpak. A temporary recovery copy protects the write and is removed automatically after validation, so the repair is completely finished in one step. For a song shorter than 30 seconds, it uses the available song length.',
    ));
    card.appendChild(make(
      'p',
      'lh-muted',
      'The existing preview starting point is not reused. Use manual review instead if you want to listen first or choose the start yourself.',
    ));
    const actions = make('div', 'lh-repair-buttons');
    const apply = make('button', 'lh-button lh-button-primary', 'Create preview and finish');
    const cancel = make('button', 'lh-button', 'Cancel');
    apply.type = 'button';
    cancel.type = 'button';
    apply.addEventListener('click', () => applyAutomaticPreviewRepair(
      report, finding, apply, cancel, region,
    ));
    cancel.addEventListener('click', () => {
      region.replaceChildren();
      trigger.disabled = false;
      manual.disabled = false;
    });
    actions.appendChild(apply);
    actions.appendChild(cancel);
    card.appendChild(actions);
    region.appendChild(card);
  }

  async function applyAutomaticPreviewRepair(report, finding, apply, cancel, region) {
    apply.disabled = true;
    cancel.disabled = true;
    text(apply, 'Selecting, creating, validating, and finishing...');
    try {
      const result = await request('/repair/media/automatic', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ package: report.package, rule_code: finding.code }),
      });
      result.id = `repair-${result.backup_id || Date.now()}`;
      result.title = result.report?.title || report.title || report.package;
      result.artist = result.report?.artist || report.artist || '';
      actionRegistry.renderRepairResult(result);
      await actionRegistry.refreshStatus();
      await Promise.all([
        actionRegistry.loadRules(),
        actionRegistry.loadResults(),
        actionRegistry.refreshSelectedSongTool(report.package, {
          previewConfirmation: previewSetConfirmation(result, 'automatic'),
        }),
      ]);
    } catch (error) {
      apply.disabled = false;
      cancel.disabled = false;
      text(apply, 'Create preview and finish');
      region.appendChild(make('p', 'lh-inline-error', error.message));
      actionRegistry.renderRepairFailure(report, error);
    }
  }


  async function previewRepair(report, finding, trigger, region, startSeconds = null) {
    const mediaRepair = state.repairRules[finding.code]?.change_kind === 'replace_media';
    if (!trigger.dataset.idleLabel) trigger.dataset.idleLabel = trigger.textContent;
    trigger.disabled = true;
    text(trigger, mediaRepair ? 'Generating audio preview...' : 'Preparing preview...');
    region.replaceChildren();
    try {
      const body = { package: report.package, rule_code: finding.code };
      if (startSeconds != null) body.start_seconds = startSeconds;
      const plan = await request('/repair/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      trigger.hidden = true;
      const card = make('div', 'lh-repair-card');
      card.appendChild(make('strong', '', plan.title || 'Safe repair'));
      if (plan.available) {
        const itemName = plan.item_name || 'item';
        const changeDescription = plan.change_kind === 'replace_media'
          ? plan.media?.creates_preview
            ? `Add this ${duration(plan.media?.candidate_duration_seconds || 30)} preview selected from the full song mix. The proposed clip starts at ${duration(plan.media?.start_seconds)} and adds about ${plan.media?.candidate_size || 'a short clip'} to the Feedpak.`
            : `Replace the existing ${duration(plan.media?.original_duration_seconds)} preview with this ${duration(plan.media?.candidate_duration_seconds || 30)} excerpt selected from the full song mix. The proposed clip starts at ${duration(plan.media?.start_seconds)} and is about ${plan.media?.candidate_size || 'a short clip'} instead of ${plan.media?.original_size || 'the current preview'}.`
          : plan.change_kind === 'normalize'
            ? `Change ${number(repairChangeCount(plan))} negative ${itemName}${repairChangeCount(plan) === 1 ? '' : 's'} to fret 0 at ${number(plan.musical_positions)} musical ${plan.musical_positions === 1 ? 'position' : 'positions'}, across ${number(plan.arrays_affected)} stored ${plan.arrays_affected === 1 ? 'list' : 'lists'}. Every other property is kept unchanged.`
            : plan.change_kind === 'reorder'
            ? `Put ${number(repairChangeCount(plan))} ${itemName}${repairChangeCount(plan) === 1 ? '' : 's'} into chronological order across ${number(plan.arrays_affected)} stored ${plan.arrays_affected === 1 ? 'list' : 'lists'}, affecting ${number(plan.musical_positions)} musical ${plan.musical_positions === 1 ? 'position' : 'positions'}. Every entry and stored property is kept.`
            : plan.change_kind === 'remove_redundant'
              ? `Remove ${number(plan.removed_count)} redundant stored ${itemName} ${plan.removed_count === 1 ? 'record' : 'records'} at ${number(plan.musical_positions)} musical ${plan.musical_positions === 1 ? 'position' : 'positions'}, across ${number(plan.arrays_affected)} ${itemName} ${plan.arrays_affected === 1 ? 'list' : 'lists'}. Every matching authored chord is kept unchanged.`
              : `Remove ${number(plan.removed_count)} redundant stored ${itemName} ${plan.removed_count === 1 ? 'copy' : 'copies'} at ${number(plan.musical_positions)} musical ${plan.musical_positions === 1 ? 'position' : 'positions'}, across ${number(plan.arrays_affected)} ${itemName} ${plan.arrays_affected === 1 ? 'list' : 'lists'}. The first authored copy is kept.`;
        card.appendChild(make('p', '', changeDescription));
        card.appendChild(make(
          'p',
          '',
          plan.description || 'Only the safe stored issue shown in this preview will be changed.',
        ));
        if (plan.change_kind === 'replace_media') {
          const media = plan.media || {};
          const review = make('section', 'lh-media-review');
          review.appendChild(make('strong', '', 'Listen before applying'));
          review.appendChild(make(
            'p',
            'lh-muted',
            media.creates_preview
              ? `Selected from ${media.selection_reason || 'the song audio'}. Expected Feedpak addition: about ${media.candidate_size || 'one short audio clip'}.`
              : `Selected from ${media.selection_reason || 'the song audio'}. Expected Feedpak reduction: about ${media.estimated_package_savings || 'the removed preview data'}.`,
          ));
          const audio = document.createElement('audio');
          audio.className = 'lh-media-preview-player';
          audio.controls = true;
          audio.preload = 'metadata';
          audio.src = `${apiRoot}/repair/media/candidate/${encodeURIComponent(plan.plan_id)}`;
          audio.setAttribute('aria-label', `Proposed preview for ${report.title || report.package}`);
          review.appendChild(audio);

          const chooser = make('div', 'lh-media-start');
          const label = document.createElement('label');
          label.appendChild(make('span', '', 'Try another start time (seconds)'));
          const input = document.createElement('input');
          input.type = 'number';
          input.min = '0';
          input.max = String(Math.max(0, Number(media.max_start_seconds || 0)));
          input.step = '1';
          const displayedStart = Math.round(Number(media.start_seconds || 0));
          input.value = String(displayedStart);
          label.appendChild(input);
          const regenerate = make('button', 'lh-button', 'Generate another excerpt');
          regenerate.type = 'button';
          const syncRegenerate = () => {
            const nextStart = Number(input.value);
            regenerate.disabled = (
              input.value.trim() === ''
              || !Number.isFinite(nextStart)
              || !input.checkValidity()
              || nextStart === displayedStart
            );
          };
          input.addEventListener('input', syncRegenerate);
          regenerate.addEventListener('click', () => {
            const nextStart = Number(input.value);
            if (!Number.isFinite(nextStart) || nextStart === displayedStart) return;
            audio.pause();
            regenerate.disabled = true;
            previewRepair(report, finding, trigger, region, nextStart);
          });
          syncRegenerate();
          chooser.appendChild(label);
          chooser.appendChild(regenerate);
          review.appendChild(chooser);
          review.appendChild(make(
            'p',
            'lh-repair-warning',
            'A temporary recovery copy protects this change while Library Doctor validates the complete Feedpak. It is removed automatically after a successful repair, so no preview backup is left behind.',
          ));
          card.appendChild(review);
        }
        actionRegistry.appendRepairPreviewAnswers(card, plan);
        if (Array.isArray(plan.blockers) && plan.blockers.length) {
          card.appendChild(make(
            'p',
            'lh-repair-warning',
            `${number(plan.blockers.length)} arrangement file${plan.blockers.length === 1 ? '' : 's'} cannot be changed safely and will be left untouched.`,
          ));
        }
        const actions = make('div', 'lh-repair-buttons');
        const apply = make(
          'button',
          'lh-button lh-button-primary',
          plan.change_kind === 'replace_media' ? 'Keep this preview' : 'Apply safe repair',
        );
        const cancel = make('button', 'lh-button', 'Cancel');
        apply.type = 'button';
        cancel.type = 'button';
        apply.addEventListener('click', () => {
          if (plan.change_kind === 'replace_media') {
            confirmReviewedPreviewRepair(
              report, finding, plan, apply, cancel, actions, region,
            );
          } else {
            applyRepair(report, finding, plan, apply, cancel, region);
          }
        });
        cancel.addEventListener('click', () => {
          region.replaceChildren();
          trigger.hidden = false;
          trigger.disabled = false;
          text(trigger, trigger.dataset.idleLabel || (mediaRepair ? mediaReviewLabel(finding) : 'Review safe fix'));
        });
        actions.appendChild(apply);
        actions.appendChild(cancel);
        card.appendChild(actions);
      } else {
        card.appendChild(make(
          'p',
          '',
          'This safe issue is no longer available to repair in this package.',
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
          text(trigger, trigger.dataset.idleLabel || (mediaRepair ? mediaReviewLabel(finding) : 'Review safe fix'));
        });
        card.appendChild(close);
      }
      region.appendChild(card);
    } catch (error) {
      trigger.hidden = false;
      trigger.disabled = false;
      text(trigger, trigger.dataset.idleLabel || (mediaRepair ? mediaReviewLabel(finding) : 'Review safe fix'));
      region.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  function confirmReviewedPreviewRepair(
    report, finding, plan, reviewed, cancelReview, actions, region,
  ) {
    reviewed.disabled = true;
    cancelReview.disabled = true;
    const confirmation = make('div', 'lh-repair-confirm');
    confirmation.appendChild(make('strong', '', 'Replace the Feedpak preview?'));
    confirmation.appendChild(make(
      'p',
      '',
      plan.media?.creates_preview
        ? 'This adds the preview you reviewed to the existing Feedpak. The full song mix, chart, lyrics, and gameplay audio stay unchanged.'
        : 'This replaces only the current preview with the excerpt you reviewed. The full song mix, chart, lyrics, and gameplay audio stay unchanged.',
    ));
    confirmation.appendChild(make(
      'p',
      'lh-muted',
      'Library Doctor validates the complete Feedpak before saving it. Temporary recovery data is removed after success, so this preview change will not have Undo.',
    ));
    const confirm = make(
      'button', 'lh-button lh-button-primary', 'Confirm replacement and finish',
    );
    const back = make('button', 'lh-button', 'Go back');
    confirm.type = 'button';
    back.type = 'button';
    confirm.addEventListener('click', () => applyRepair(
      report, finding, plan, confirm, back, region,
    ));
    back.addEventListener('click', () => {
      confirmation.remove();
      reviewed.disabled = false;
      cancelReview.disabled = false;
    });
    confirmation.appendChild(confirm);
    confirmation.appendChild(back);
    actions.parentNode.insertBefore(confirmation, actions.nextSibling);
  }

  async function applyRepair(report, finding, plan, apply, cancel, region) {
    apply.disabled = true;
    cancel.disabled = true;
    text(apply, plan.change_kind === 'replace_media'
      ? 'Applying, validating, and finishing...'
      : 'Applying and verifying...');
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
      actionRegistry.renderRepairResult(result);
      await actionRegistry.refreshStatus();
      await Promise.all([
        actionRegistry.loadRules(),
        actionRegistry.loadResults(),
        actionRegistry.refreshSelectedSongTool(report.package, plan.change_kind === 'replace_media'
          ? { previewConfirmation: previewSetConfirmation(result, 'chosen') }
          : {}),
      ]);
    } catch (error) {
      apply.disabled = false;
      cancel.disabled = false;
      text(apply, plan.change_kind === 'replace_media' ? 'Confirm replacement and finish' : 'Apply safe repair');
      region.appendChild(make('p', 'lh-inline-error', error.message));
      actionRegistry.renderRepairFailure(report, error);
    }
  }



  return { confirmAutomaticPreviewRepair, previewRepair };
}
