export function createReviewedRepairController({
  actions,
  apiRoot,
  createConfirmation,
  document,
  focus,
  make,
  number,
  request,
  state,
  text,
  playerReviewController,
  getReviewDifficultyScope,
}) {
  let session = 0;

  function flagCopy(techniques) {
    const enabled = [];
    if (techniques?.hammer_on) enabled.push('hammer-on');
    if (techniques?.pull_off) enabled.push('pull-off');
    if (techniques?.tap) enabled.push('tap');
    return enabled.length ? enabled.join(', ') : 'none';
  }

  function reasonCopy(reason) {
    return {
      both_flags: 'Both hammer-on and pull-off are enabled on this note.',
      direction_mismatch: 'The lone HO/PO flag points opposite to the incoming fret movement.',
      same_fret: 'The previous same-string note uses the same fret, so there is no normal incoming pitch change.',
      no_usable_predecessor: 'There is no single usable previous same-string note for an incoming HO/PO.',
    }[reason] || reason;
  }

  function neighbourCard(label, neighbour, gap, emptyCopy) {
    const card = make('div', 'lh-reviewed-note');
    card.appendChild(make('strong', '', label));
    if (!neighbour) {
      card.appendChild(make('p', 'lh-muted', emptyCopy));
      return card;
    }
    card.appendChild(make('span', 'lh-reviewed-fret', `Fret ${number(neighbour.fret)}`));
    card.appendChild(make(
      'span',
      'lh-muted',
      `${Number(neighbour.time).toFixed(4)}s${gap == null ? '' : ` · gap ${Number(gap).toFixed(4)}s`}`,
    ));
    return card;
  }

  function reviewedRepairControls(report, adapterId) {
    if (
      report.features?.repair_scan_current === false
    ) return null;
    const definition = state.reviewedRepairAdapters?.[adapterId];
    if (!definition) return null;
    const wrapper = make('div', 'lh-repair-action lh-reviewed-action');
    const availability = report.features?.player_review;
    if (availability?.available === false) {
      wrapper.appendChild(make(
        'p',
        'lh-repair-warning lh-player-review-unavailable',
        availability.message || 'Manual Player Review is unavailable because this song is outside the configured song library. Automatic and standard repairs are still available.',
      ));
      return wrapper;
    }
    const difficultyScope = getReviewDifficultyScope();
    const playerButton = make(
      'button',
      'lh-button lh-button-primary',
      playerReviewController.canResume(report.package, adapterId, difficultyScope)
        ? 'Resume Player Review'
        : 'Review in Player',
    );
    playerButton.type = 'button';
    const region = make('div', 'lh-repair-preview lh-reviewed-region');
    playerButton.addEventListener('click', async () => {
      playerButton.disabled = true;
      text(playerButton, 'Opening FeedBack Player…');
      try {
        region.replaceChildren();
        const outcome = await playerReviewController.open(report, adapterId, difficultyScope);
        if (outcome?.opened === false && outcome.message) {
          region.appendChild(make('p', 'lh-repair-warning', outcome.message));
        }
      } finally {
        playerButton.disabled = false;
        text(
          playerButton,
          playerReviewController.canResume(report.package, adapterId, difficultyScope)
            ? 'Resume Player Review'
            : 'Review in Player',
        );
      }
    });
    const button = make('button', 'lh-button', 'Review with text only');
    button.type = 'button';
    button.addEventListener('click', () => openReviewedRepair(
      report, adapterId, button, region,
    ));
    wrapper.appendChild(playerButton);
    wrapper.appendChild(button);
    wrapper.appendChild(region);
    return wrapper;
  }

  async function openReviewedRepair(report, adapterId, trigger, region) {
    const mySession = ++session;
    trigger.disabled = true;
    text(trigger, 'Loading reviewed notes...');
    region.replaceChildren();
    try {
      const inspection = await request('/reviewed-repair/inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          package: report.package,
          adapter_id: adapterId,
          difficulty_scope: getReviewDifficultyScope(),
        }),
      });
      if (!state.active || mySession !== session) return;
      trigger.hidden = true;
      renderSession(report, inspection, trigger, region, mySession);
    } catch (error) {
      if (mySession !== session) return;
      trigger.disabled = false;
      text(trigger, 'Review with text only');
      region.appendChild(make('p', 'lh-inline-error', error.message));
    }
  }

  function renderSession(report, inspection, trigger, region, mySession) {
    let currentInspection = inspection;
    let candidates = Array.isArray(inspection.candidates) ? inspection.candidates : [];
    const decisionDefinitions = new Map(
      (inspection.decision_definitions || []).map((item) => [item.name, item]),
    );
    const selected = new Map();
    const skipped = new Set();
    const optionStates = new Map();
    let index = 0;
    let previewNode = null;
    let previewRevision = 0;
    let previewTrigger = null;
    let pageRequest = 0;

    function restorePreviewTrigger(button) {
      if (!button) return;
      button.disabled = false;
      text(button, 'Preview selected changes');
    }

    function invalidatePreview() {
      previewRevision += 1;
      previewNode?.remove();
      previewNode = null;
      const trigger = previewTrigger;
      previewTrigger = null;
      restorePreviewTrigger(trigger);
    }

    const shell = make('section', 'lh-reviewed-shell');
    shell.setAttribute('aria-label', inspection.title || 'Reviewed repair');
    const header = make('div', 'lh-reviewed-header');
    const heading = make('h4', '', inspection.title || 'Reviewed repair');
    heading.tabIndex = -1;
    const close = make('button', 'lh-button', 'Close');
    close.type = 'button';
    close.addEventListener('click', () => {
      session += 1;
      region.replaceChildren();
      trigger.hidden = false;
      trigger.disabled = false;
      text(trigger, 'Review with text only');
      focus(trigger);
    });
    header.appendChild(heading);
    header.appendChild(close);
    shell.appendChild(header);
    shell.appendChild(make(
      'p',
      '',
      'Library Doctor checks every registered choice on a temporary copy and shows only changes that resolve this issue without introducing another finding. Nothing is preselected.',
    ));
    const hiddenLower = Number(inspection.hidden_lower_candidate_count || 0);
    if (hiddenLower > 0) {
      shell.appendChild(make(
        'p',
        'lh-repair-warning',
        `${number(hiddenLower)} lower-difficulty issue${hiddenLower === 1 ? '' : 's'} hidden by “Full difficulty only.” Change the saved difficulty filter to show them without scanning again.`,
      ));
    }
    if (inspection.inspection_blocker) {
      shell.appendChild(make('p', 'lh-repair-warning', inspection.inspection_blocker));
    }
    const live = make('p', 'lh-visually-hidden');
    live.setAttribute('role', 'status');
    live.setAttribute('aria-live', 'polite');
    shell.appendChild(live);
    const candidateRegion = make('div', 'lh-reviewed-candidate');
    shell.appendChild(candidateRegion);
    const footer = make('div', 'lh-reviewed-footer');
    shell.appendChild(footer);
    region.appendChild(shell);
    focus(heading);

    function changingDecisions() {
      return [...selected.entries()]
        .map(([candidateId, decision]) => ({ candidate_id: candidateId, decision }));
    }

    function allDecisions() {
      return changingDecisions();
    }

    function prefetchNextOptions(candidate) {
      const nextCandidate = candidates.find(
        (item) => item.candidate_id !== candidate.candidate_id
          && !optionStates.has(item.candidate_id),
      );
      if (nextCandidate) loadOptions(nextCandidate, { prefetch: true });
    }

    function loadOptions(candidate, { prefetch = false } = {}) {
      if (!candidate) return Promise.resolve(null);
      const existing = optionStates.get(candidate.candidate_id);
      if (existing?.status === 'ready') {
        if (!prefetch) prefetchNextOptions(candidate);
        return Promise.resolve(existing.response);
      }
      if (existing?.status === 'loading') return existing.promise;
      const stateValue = {
        status: 'loading', response: null, error: null, promise: null,
      };
      optionStates.set(candidate.candidate_id, stateValue);
      stateValue.promise = request('/reviewed-repair/options', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          package: report.package,
          adapter_id: currentInspection.adapter_id,
          difficulty_scope: currentInspection.difficulty_scope || getReviewDifficultyScope(),
          candidate_id: candidate.candidate_id,
        }),
      }).then((response) => {
        if (
          mySession !== session
          || response?.candidate_id !== candidate.candidate_id
          || response?.review_item_id !== candidate.review_item_id
        ) return null;
        stateValue.status = 'ready';
        stateValue.response = response;
        stateValue.promise = null;
        (response.decision_definitions || []).forEach((item) => {
          decisionDefinitions.set(item.name, item);
        });
        const selectedDecision = selected.get(candidate.candidate_id);
        if (selectedDecision && !(response.decision_names || []).includes(selectedDecision)) {
          invalidatePreview();
          selected.delete(candidate.candidate_id);
        }
        if (!prefetch || candidates[index]?.candidate_id === candidate.candidate_id) {
          renderCandidate();
        }
        if (!prefetch) {
          prefetchNextOptions(candidate);
        }
        return response;
      }).catch((error) => {
        if (mySession !== session) return null;
        stateValue.status = 'error';
        stateValue.error = error;
        stateValue.promise = null;
        if (!prefetch || candidates[index]?.candidate_id === candidate.candidate_id) {
          renderCandidate();
        }
        return null;
      });
      return stateValue.promise;
    }

    function renderFooter() {
      footer.replaceChildren();
      const changing = changingDecisions().length;
      const skippedCount = skipped.size;
      footer.appendChild(make(
        'p',
        'lh-muted',
        `${number(changing)} selected change${changing === 1 ? '' : 's'} · ${number(skippedCount)} skipped for now · ${number(Math.max(0, Number(currentInspection.total_candidate_count ?? candidates.length) - selected.size - skippedCount))} unresolved`,
      ));
      const controls = make('div', 'lh-repair-buttons');
      const previousPage = make('button', 'lh-button', 'Previous page');
      const previous = make('button', 'lh-button', 'Previous note');
      const next = make('button', 'lh-button', 'Next note');
      const nextPage = make('button', 'lh-button', 'Next page');
      const preview = make('button', 'lh-button lh-button-primary', 'Preview selected changes');
      previousPage.type = previous.type = next.type = nextPage.type = preview.type = 'button';
      previousPage.disabled = !currentInspection.has_previous;
      previous.disabled = index === 0;
      next.disabled = index >= candidates.length - 1;
      nextPage.disabled = !currentInspection.has_next;
      preview.disabled = changing === 0;
      previousPage.addEventListener('click', () => loadPage(
        currentInspection.previous_offset,
        true,
      ));
      previous.addEventListener('click', () => {
        invalidatePreview();
        index -= 1;
        renderCandidate();
      });
      next.addEventListener('click', () => {
        invalidatePreview();
        index += 1;
        renderCandidate();
      });
      nextPage.addEventListener('click', () => loadPage(
        currentInspection.next_offset,
        false,
      ));
      preview.addEventListener('click', () => previewReviewed(
        report,
        currentInspection,
        allDecisions(),
        preview,
        mySession,
      ));
      controls.appendChild(previousPage);
      controls.appendChild(previous);
      controls.appendChild(next);
      controls.appendChild(nextPage);
      controls.appendChild(preview);
      footer.appendChild(controls);
      if (skippedCount) {
        const reviewSkipped = make('button', 'lh-button', 'Review skipped issues');
        reviewSkipped.type = 'button';
        reviewSkipped.addEventListener('click', async () => {
          skipped.clear();
          await loadPage(0, false);
        });
        footer.appendChild(reviewSkipped);
      }
    }

    async function loadPage(offset, focusLast) {
      if (!Number.isInteger(offset) || offset < 0) return;
      invalidatePreview();
      const requestNumber = ++pageRequest;
      text(live, 'Loading another page of issues.');
      try {
        const nextInspection = await request('/reviewed-repair/inspect', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            package: report.package,
            adapter_id: inspection.adapter_id,
            difficulty_scope: currentInspection.difficulty_scope || getReviewDifficultyScope(),
            offset,
            limit: currentInspection.limit,
          }),
        });
        if (
          mySession !== session
          || requestNumber !== pageRequest
          || !state.active
        ) return;
        currentInspection = nextInspection;
        candidates = Array.isArray(nextInspection.candidates)
          ? nextInspection.candidates
          : [];
        index = focusLast ? Math.max(0, candidates.length - 1) : 0;
        renderCandidate();
      } catch (error) {
        if (mySession !== session || requestNumber !== pageRequest) return;
        footer.appendChild(make('p', 'lh-inline-error', error.message));
      }
    }

    function renderAudio(candidate, container, button) {
      button.disabled = true;
      text(button, 'Generating passage...');
      request('/reviewed-repair/audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          package: report.package,
          adapter_id: inspection.adapter_id,
          candidate_id: candidate.candidate_id,
        }),
      }).then((audioPlan) => {
        if (mySession !== session) return;
        container.replaceChildren();
        const audio = document.createElement('audio');
        audio.className = 'lh-media-preview-player';
        audio.controls = true;
        audio.preload = 'metadata';
        audio.src = `${apiRoot}/reviewed-repair/audio/${encodeURIComponent(audioPlan.audio_token)}`;
        audio.setAttribute('aria-label', `Passage around ${Number(candidate.time).toFixed(4)} seconds`);
        container.appendChild(audio);
        container.appendChild(make('p', 'lh-muted', audioPlan.notice));
      }).catch((error) => {
        if (mySession !== session) return;
        button.disabled = false;
        text(button, 'Listen to short passage');
        container.replaceChildren(make('p', 'lh-inline-error', `${error.message} Visual review is still available.`));
      });
    }

    function renderCandidate() {
      candidateRegion.replaceChildren();
      if (!candidates.length) {
        candidateRegion.appendChild(make('p', '', 'No current HO/PO issues remain in this package.'));
        renderFooter();
        return;
      }
      const candidate = candidates[index];
      const globalIndex = Number(currentInspection.offset || 0) + index + 1;
      const title = make(
        'h5',
        '',
        `Issue ${number(globalIndex)} of ${number(currentInspection.total_candidate_count ?? candidates.length)} · string ${number(candidate.string + 1)}, fret ${number(candidate.fret)}`,
      );
      title.tabIndex = -1;
      candidateRegion.appendChild(title);
      candidateRegion.appendChild(make(
        'p',
        'lh-muted',
        `${candidate.context_kind === 'chord_member' ? 'Chord member' : 'Standalone note'} · ${candidate.stream} · ${Number(candidate.time).toFixed(4)}s`,
      ));
      (candidate.reasons || []).forEach((reason) => {
        candidateRegion.appendChild(make('p', 'lh-reviewed-reason', reasonCopy(reason)));
      });
      if (candidate.outgoing_match) {
        candidateRegion.appendChild(make(
          'p',
          'lh-repair-warning',
          'The current flag matches the movement to the next note. It may have been attached one note early; “Move to next note” is available only when that target is unambiguous.',
        ));
      }
      const notes = make('div', 'lh-reviewed-notes');
      notes.appendChild(neighbourCard(
        'Previous on string', candidate.previous, candidate.previous_gap_seconds,
        candidate.predecessor_state === 'ambiguous' ? 'Several incompatible previous frets.' : 'No previous note.',
      ));
      const current = make('div', 'lh-reviewed-note lh-reviewed-note-current');
      current.appendChild(make('strong', '', 'Current note'));
      current.appendChild(make('span', 'lh-reviewed-fret', `Fret ${number(candidate.fret)}`));
      current.appendChild(make('span', '', `Stored flags: ${flagCopy(candidate.techniques)}`));
      notes.appendChild(current);
      notes.appendChild(neighbourCard(
        'Next on string', candidate.next, candidate.next_gap_seconds,
        candidate.next_state === 'ambiguous' ? 'Several incompatible next frets.' : 'No next note.',
      ));
      candidateRegion.appendChild(notes);

      if (candidate.blockers?.length) {
        candidateRegion.appendChild(make(
          'p',
          'lh-inline-error',
          `Library Doctor cannot safely change this issue here: ${candidate.blockers.join(', ')}. You may skip it for now and continue.`,
        ));
      }
      const optionState = optionStates.get(candidate.candidate_id);
      if (!optionState) loadOptions(candidate);
      const optionResponse = optionState?.status === 'ready' ? optionState.response : null;
      if (!optionState || optionState.status === 'loading') {
        candidateRegion.appendChild(make(
          'p',
          'lh-muted',
          'Checking every possible choice against a temporary copy of this arrangement…',
        ));
      } else if (optionState.status === 'error') {
        const errorPanel = make('div', 'lh-inline-error');
        errorPanel.appendChild(make(
          'p',
          '',
          optionState.error?.message || 'Library Doctor could not evaluate this issue.',
        ));
        const retry = make('button', 'lh-button', 'Retry choice check');
        retry.type = 'button';
        retry.addEventListener('click', () => {
          invalidatePreview();
          optionStates.delete(candidate.candidate_id);
          renderCandidate();
        });
        errorPanel.appendChild(retry);
        candidateRegion.appendChild(errorPanel);
      } else if ((optionResponse?.decision_names || []).length) {
        const fieldset = make('fieldset', 'lh-reviewed-decisions');
        fieldset.appendChild(make('legend', '', 'Choose a change that resolves this issue'));
        optionResponse.decision_names.forEach((name) => {
          const definition = decisionDefinitions.get(name);
          if (!definition) return;
          const label = make('label', 'lh-reviewed-choice');
          const radio = document.createElement('input');
          radio.type = 'radio';
          radio.name = `reviewed-${candidate.candidate_id}`;
          radio.value = name;
          radio.checked = selected.get(candidate.candidate_id) === name;
          radio.disabled = false;
          radio.addEventListener('change', () => {
            const candidateLimit = Number(
              state.reviewedRepairAdapters?.[inspection.adapter_id]?.candidate_limit || 2000,
            );
            if (!selected.has(candidate.candidate_id) && selected.size >= candidateLimit) {
              radio.checked = false;
              text(live, `This bounded session accepts ${candidateLimit} decisions. Preview and apply those choices before continuing.`);
              return;
            }
            invalidatePreview();
            skipped.delete(candidate.review_item_id);
            selected.set(candidate.candidate_id, name);
            text(live, `${definition.label} selected for issue ${globalIndex}.`);
            renderFooter();
          });
          const copy = make('span');
          copy.appendChild(make('strong', '', definition.label));
          copy.appendChild(make('small', '', definition.description));
          label.appendChild(radio);
          label.appendChild(copy);
          fieldset.appendChild(label);
        });
        candidateRegion.appendChild(fieldset);
      } else {
        candidateRegion.appendChild(make(
          'p',
          'lh-repair-warning',
          optionResponse?.message
            || 'No registered HO/PO change resolves this issue without creating another finding. Skip it for now and edit the tab manually.',
        ));
      }

      const skipButton = make(
        'button',
        'lh-button',
        skipped.has(candidate.review_item_id) ? 'Review this issue again' : 'Skip for now',
      );
      skipButton.type = 'button';
      skipButton.addEventListener('click', async () => {
        invalidatePreview();
        if (skipped.has(candidate.review_item_id)) {
          skipped.delete(candidate.review_item_id);
          text(live, `Issue ${globalIndex} is back in the current review pass.`);
          renderCandidate();
          return;
        }
        selected.delete(candidate.candidate_id);
        skipped.add(candidate.review_item_id);
        text(live, `Issue ${globalIndex} was skipped for now and remains unresolved.`);
        if (index < candidates.length - 1) {
          index += 1;
          renderCandidate();
        } else if (currentInspection.has_next) {
          await loadPage(currentInspection.next_offset, false);
        } else {
          renderCandidate();
        }
      });
      candidateRegion.appendChild(skipButton);

      if (state.reviewedRepairAdapters?.[inspection.adapter_id]?.audio_support) {
        const audioButton = make('button', 'lh-button', 'Listen to short passage');
        const audioRegion = make('div', 'lh-reviewed-audio');
        audioButton.type = 'button';
        audioButton.addEventListener('click', () => renderAudio(
          candidate, audioRegion, audioButton,
        ));
        candidateRegion.appendChild(audioButton);
        candidateRegion.appendChild(audioRegion);
      }
      const technical = make('details', 'lh-finding-technical');
      technical.appendChild(make('summary', '', 'Technical evidence'));
      technical.appendChild(make(
        'p',
        'lh-finding-code',
        `${candidate.member_path} · ${candidate.location} · string ${candidate.string + 1} (stored index ${candidate.string}) · candidate ${candidate.candidate_id}`,
      ));
      candidateRegion.appendChild(technical);
      renderFooter();
      text(live, `Showing issue ${globalIndex} of ${currentInspection.total_candidate_count ?? candidates.length}.`);
      focus(title);
    }

    async function previewReviewed(reportValue, inspectionValue, decisions, button, activeSession) {
      invalidatePreview();
      const activePreviewRevision = previewRevision;
      previewTrigger = button;
      button.disabled = true;
      text(button, 'Building exact preview...');
      try {
        const plan = await request('/reviewed-repair/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            package: reportValue.package,
            adapter_id: inspectionValue.adapter_id,
            difficulty_scope: inspectionValue.difficulty_scope || getReviewDifficultyScope(),
            decisions,
          }),
        });
        if (
          activeSession !== session
          || !state.active
          || activePreviewRevision !== previewRevision
        ) return;
        previewNode = make('section', 'lh-repair-card lh-reviewed-preview');
        const previewHeading = make('h5', '', 'Exact reviewed-repair preview');
        previewHeading.tabIndex = -1;
        previewNode.appendChild(previewHeading);
        previewNode.appendChild(make(
          'p',
          '',
          `${number(plan.changing_count)} outcome-checked note decision${plan.changing_count === 1 ? '' : 's'} will change. ${number(plan.unresolved_count)} unselected issue${Number(plan.unresolved_count) === 1 ? '' : 's'} remain in this review pass, and ${number(plan.remaining_review_count)} issue${Number(plan.remaining_review_count) === 1 ? '' : 's'} are expected to remain after these choices. Skipped issues are not included.`,
        ));
        const list = make('ul', 'lh-all-safe-list');
        Object.entries(plan.decision_counts || {})
          .filter(([, count]) => Number(count) > 0)
          .forEach(([name, count]) => {
            list.appendChild(make(
              'li',
              '',
              `${decisionDefinitions.get(name)?.label || name}: ${number(count)}`,
            ));
          });
        previewNode.appendChild(list);
        actions.appendRepairPreviewAnswers(previewNode, plan);
        previewNode.appendChild(make(
          'p',
          'lh-muted',
          'Timing, strings, frets, sustains, chords, and every technique outside HO/PO/tap remain unchanged. The full package must validate before it replaces the existing Feedpak; otherwise nothing is saved. Undo retains the exact original changed files.',
        ));
        const previewActions = make('div', 'lh-repair-buttons');
        const apply = make('button', 'lh-button lh-button-primary', 'Confirm these reviewed changes');
        const cancel = make('button', 'lh-button', 'Back to decisions');
        apply.type = cancel.type = 'button';
        apply.addEventListener('click', () => {
          apply.disabled = true;
          const { region: confirmation } = createConfirmation({
            className: 'lh-repair-confirm',
            message: `Apply ${plan.changing_count} explicit reviewed decision${plan.changing_count === 1 ? '' : 's'}? Library Doctor will build and validate the complete Feedpak, save a recovery backup, and keep Undo available.`,
            confirmLabel: 'Apply reviewed changes',
            trigger: apply,
            onConfirm: (confirm, cancelConfirm) => applyReviewed(
              reportValue,
              inspectionValue,
              decisions,
              plan,
              confirm,
              cancelConfirm,
              activeSession,
            ),
          });
          previewNode.appendChild(confirmation);
        });
        cancel.addEventListener('click', () => {
          previewNode.remove();
          previewNode = null;
          restorePreviewTrigger(button);
          focus(button);
        });
        previewActions.appendChild(apply);
        previewActions.appendChild(cancel);
        previewNode.appendChild(previewActions);
        shell.appendChild(previewNode);
        focus(previewHeading);
      } catch (error) {
        if (
          activeSession !== session
          || activePreviewRevision !== previewRevision
        ) return;
        footer.appendChild(make('p', 'lh-inline-error', error.message));
      } finally {
        restorePreviewTrigger(button);
        if (previewTrigger === button) previewTrigger = null;
      }
    }

    async function applyReviewed(
      reportValue,
      inspectionValue,
      decisions,
      plan,
      confirm,
      cancelConfirm,
      activeSession,
    ) {
      confirm.disabled = cancelConfirm.disabled = true;
      text(confirm, 'Applying and validating...');
      try {
        const result = await request('/reviewed-repair/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            package: reportValue.package,
            adapter_id: inspectionValue.adapter_id,
            difficulty_scope: inspectionValue.difficulty_scope || getReviewDifficultyScope(),
            decisions,
            plan_id: plan.plan_id,
            request_id: `reviewed-${Date.now()}`,
          }),
        });
        if (activeSession !== session || !state.active) return;
        result.id = `repair-${result.backup_id || Date.now()}`;
        result.title = result.report?.title || reportValue.title || reportValue.package;
        result.artist = result.report?.artist || reportValue.artist || '';
        session += 1;
        region.replaceChildren();
        trigger.hidden = false;
        trigger.disabled = false;
        text(trigger, 'Review with text only');
        actions.renderRepairResult(result);
        await actions.refreshStatus();
        await Promise.all([
          actions.loadRules(),
          actions.loadResults(),
          actions.refreshSelectedSongTool(reportValue.package),
        ]);
      } catch (error) {
        if (activeSession !== session) return;
        confirm.disabled = cancelConfirm.disabled = false;
        text(confirm, 'Apply reviewed changes');
        actions.renderRepairFailure(reportValue, error);
      }
    }

    renderCandidate();
  }

  return { reviewedRepairControls };
}
