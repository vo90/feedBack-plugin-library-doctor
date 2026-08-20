export function createBatchResultsView({
  badge,
  completedRepairChange,
  fileSize,
  getElements,
  make,
  number,
  onReviewUndo,
  onStartFinalizePreview,
  onStartUndoPreview,
  repairActions,
  repairChangeCount,
  summaryGrid,
  text,
}) {
  const el = new Proxy({}, {
    get(_target, key) { return getElements()?.[key]; },
  });

  function outcomeSearchText(outcome) {
    return [
      outcome?.title,
      outcome?.artist,
      outcome?.package,
      outcome?.message,
      outcome?.code,
    ].filter(Boolean).join(' ').toLocaleLowerCase();
  }

  function appendOutcomeExplorer(container, outcomes, options) {
    const source = Array.isArray(outcomes) ? outcomes : [];
    const filters = options.filters;
    let activeFilter = (
      filters.find((item) => item.preferred && source.some(item.test))
      || filters.find((item) => item.id === 'all')
      || filters[0]
    ).id;
    let query = '';
    let sortMode = options.defaultSort || 'attention';
    let rendered = 0;
    let filtered = [];
    const chunkSize = 100;

    const controls = make('div', 'lh-outcome-controls');
    const search = make('input');
    search.type = 'search';
    search.placeholder = 'Search title, artist, package, or reason...';
    search.setAttribute('aria-label', 'Search batch outcomes');
    const sort = make('select');
    sort.setAttribute('aria-label', 'Sort batch outcomes');
    [
      ['attention', 'Needs attention first'],
      ['changes', 'Largest change count'],
      ['title', 'Song title'],
      ['artist', 'Artist'],
      ['path', 'Package path'],
    ].forEach(([value, label]) => {
      const option = make('option', '', label);
      option.value = value;
      sort.appendChild(option);
    });
    sort.value = sortMode;
    controls.appendChild(search);
    controls.appendChild(sort);
    container.appendChild(controls);

    const chips = make('div', 'lh-outcome-filters');
    const chipButtons = new Map();
    filters.forEach((filter) => {
      const count = source.filter(filter.test).length;
      const button = make('button', '', `${filter.label} (${number(count)})`);
      button.type = 'button';
      button.dataset.filter = filter.id;
      button.setAttribute('aria-pressed', String(filter.id === activeFilter));
      button.addEventListener('click', () => {
        activeFilter = filter.id;
        chipButtons.forEach((item, id) => item.setAttribute(
          'aria-pressed', String(id === activeFilter),
        ));
        redraw();
      });
      chipButtons.set(filter.id, button);
      chips.appendChild(button);
    });
    container.appendChild(chips);

    const resultCount = make('p', 'lh-outcome-result-count');
    const list = make('ul', 'lh-batch-list lh-batch-list-scroll');
    list.tabIndex = 0;
    const empty = make('p', 'lh-outcome-empty', 'No outcomes match these filters.');
    empty.hidden = true;
    container.appendChild(resultCount);
    container.appendChild(list);
    container.appendChild(empty);

    function attentionRank(item) {
      if (item.outcome === 'failed') return 0;
      if (item.outcome === 'partial') return 1;
      if (item.outcome === 'skipped') return 2;
      if (item.cache_updated === false || item.preview_cleanup_required) return 3;
      if (item.outcome === 'success') return 4;
      if (item.outcome === 'restored') return 5;
      return 6;
    }

    function compare(left, right) {
      const textCompare = (a, b) => String(a || '').localeCompare(
        String(b || ''), undefined, { sensitivity: 'base', numeric: true },
      );
      if (sortMode === 'changes') {
        const delta = repairChangeCount(right) - repairChangeCount(left);
        return delta || textCompare(left.title || left.package, right.title || right.package);
      }
      if (sortMode === 'title') return textCompare(left.title || left.package, right.title || right.package);
      if (sortMode === 'artist') return textCompare(left.artist, right.artist) || textCompare(left.title, right.title);
      if (sortMode === 'path') return textCompare(left.package, right.package);
      return attentionRank(left) - attentionRank(right)
        || textCompare(left.title || left.package, right.title || right.package);
    }

    function drawMore() {
      const next = filtered.slice(rendered, rendered + chunkSize);
      next.forEach((outcome) => list.appendChild(options.renderRow(outcome)));
      rendered += next.length;
      text(
        resultCount,
        `Showing ${number(rendered)} of ${number(filtered.length)} matching outcomes`,
      );
    }

    function redraw() {
      const selected = filters.find((item) => item.id === activeFilter) || filters[0];
      filtered = source.filter((item) => (
        selected.test(item)
        && (!query || outcomeSearchText(item).includes(query))
      )).sort(compare);
      rendered = 0;
      list.replaceChildren();
      list.scrollTop = 0;
      empty.hidden = filtered.length > 0;
      list.hidden = filtered.length === 0;
      if (filtered.length) drawMore();
      else text(resultCount, '0 matching outcomes');
    }

    search.addEventListener('input', () => {
      query = search.value.trim().toLocaleLowerCase();
      redraw();
    });
    sort.addEventListener('change', () => {
      sortMode = sort.value;
      redraw();
    });
    list.addEventListener('scroll', () => {
      if (
        rendered < filtered.length
        && list.scrollTop + list.clientHeight >= list.scrollHeight - 160
      ) drawMore();
    });
    redraw();
  }

  function standardOutcomeFilters({ completedLabel = 'Completed' } = {}) {
    return [
      {
        id: 'attention',
        label: 'Needs attention',
        preferred: true,
        test: (item) => ['failed', 'partial', 'skipped'].includes(item.outcome)
          || item.cache_updated === false || item.preview_cleanup_required,
      },
      { id: 'failed', label: 'Failed', test: (item) => item.outcome === 'failed' },
      { id: 'skipped', label: 'Skipped safely', test: (item) => item.outcome === 'skipped' },
      {
        id: 'completed',
        label: completedLabel,
        test: (item) => ['success', 'finalized', 'restored'].includes(item.outcome),
      },
      { id: 'all', label: 'All', test: () => true },
    ];
  }

  function renderUndo(result) {
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
    card.appendChild(summaryGrid([
      [result.restored_count, 'Originals restored'],
      [result.skipped_count, 'Skipped safely'],
      [result.failed_count, 'Failed'],
      [result.restored_change_count ?? result.restored_entry_count, 'Safe changes restored'],
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
    appendOutcomeExplorer(details, outcomes, {
      filters: standardOutcomeFilters({ completedLabel: 'Originals restored' }),
      renderRow: (outcome) => {
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
            ? `${number(repairChangeCount(outcome))} safe song-data ${repairChangeCount(outcome) === 1 ? 'change was' : 'changes were'} restored to the original state.${outcome.preview_repaired ? ' The finalized generated preview remains.' : ''} ${outcome.cache_updated === false ? 'Displayed scan result needs a manual refresh. ' : ''}${outcome.package}`
            : `${outcome.message || 'No additional details.'} ${outcome.package}`,
        ));
        return row;
      },
    });
    card.appendChild(details);
    el.batchPreview.appendChild(card);
  }

  function renderFinalize(result) {
    const card = make('div', 'lh-batch-card');
    card.appendChild(make(
      'h4',
      '',
      result.outcome === 'complete' ? 'Batch finalization complete' : 'Batch finalization stopped',
    ));
    card.appendChild(make(
      'p',
      '',
      result.outcome === 'complete'
        ? `${number(result.completed_count)} planned recovery-copy transactions finished. The playable Feedpaks were not changed.`
        : `${number(result.completed_count)} recovery copies were processed; ${number(result.remaining_count)} were not started. Completed finalizations remain permanent.`,
    ));
    card.appendChild(summaryGrid([
      [result.finalized_count, 'Recovery copies removed'],
      [result.skipped_count, 'Copies kept for safety'],
      [result.failed_count, 'Failed'],
      [fileSize(result.recovery_bytes_freed), 'Recovery storage freed'],
    ]));

    const outcomes = Array.isArray(result.outcomes) ? result.outcomes : [];
    const details = make('details', 'lh-batch-details');
    details.open = Number(result.failed_count || 0) > 0 || Number(result.skipped_count || 0) > 0;
    details.appendChild(make('summary', '', `Finalization outcomes (${number(outcomes.length)})`));
    appendOutcomeExplorer(details, outcomes, {
      filters: standardOutcomeFilters({ completedLabel: 'Finalized' }),
      renderRow: (outcome) => {
        const row = make('li');
        const heading = make('div', 'lh-batch-outcome-heading');
        heading.appendChild(make('strong', '', `${outcome.title || outcome.package}${outcome.artist ? ` - ${outcome.artist}` : ''}`));
        heading.appendChild(badge(
          outcome.outcome === 'finalized' ? 'Finalized' : outcome.outcome === 'failed' ? 'Failed' : 'Kept for safety',
          outcome.outcome === 'finalized' ? 'good' : outcome.outcome === 'failed' ? 'error' : 'warning',
        ));
        row.appendChild(heading);
        row.appendChild(make(
          'span',
          '',
          `${outcome.message || 'No additional details.'}${outcome.outcome === 'finalized' ? ` ${fileSize(outcome.recovery_bytes_freed)} freed.` : ''} ${outcome.package}`,
        ));
        return row;
      },
    });
    card.appendChild(details);
    el.batchPreview.appendChild(card);
  }

  function renderBatch(result, previousSession, batchState) {
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
    const finalizedCount = Number(
      result.finalized_count ?? outcomes.filter((item) => item.outcome === 'finalized').length,
    );
    const currentlyRepaired = Number(
      result.currently_repaired_count
        ?? outcomes.filter((item) => ['success', 'finalized', 'partial'].includes(item.outcome)).length,
    );
    const previewSuccessful = Number(
      result.preview_successful_count
        ?? outcomes.filter((item) => item.preview_repaired).length,
    );
    const undoableCount = Number(
      result.undoable_count
        ?? outcomes.filter((item) => item.backup_id && ['success', 'partial'].includes(item.outcome)).length,
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
    card.appendChild(summaryGrid([
      [currentlyRepaired, 'Currently repaired'],
      [restoredCount, 'Originals restored'],
      [result.include_preview_repairs ? previewSuccessful : finalizedCount,
        result.include_preview_repairs ? 'Previews repaired' : 'Recovery finalized'],
      [result.failed_count, 'Repair failures'],
    ]));
    card.appendChild(make('p', '', result.recovery_summary));
    if (Number(result.preview_cleanup_required_count || 0) > 0) {
      card.appendChild(make(
        'p',
        'lh-batch-warning',
        `${number(result.preview_cleanup_required_count)} repaired preview${Number(result.preview_cleanup_required_count) === 1 ? '' : 's'} still ${Number(result.preview_cleanup_required_count) === 1 ? 'has' : 'have'} a temporary recovery copy. The repaired audio is active; remove the copy from the package outcome below to finish cleanup.`,
      ));
    }
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
    appendOutcomeExplorer(details, outcomes, {
      filters: repairOutcomeFilters(),
      renderRow: renderRepairOutcome,
    });
    card.appendChild(details);

    const phase = batchState?.phase || 'idle';
    const undoActive = ['undo_previewing', 'undo_ready', 'undoing'].includes(phase)
      || (batchState?.running && ['paused', 'cancelling'].includes(phase)
        && String(batchState?.mode || '').startsWith('undo'));
    const finalizeActive = ['finalize_previewing', 'finalize_ready', 'finalizing'].includes(phase)
      || (batchState?.running && ['paused', 'cancelling'].includes(phase)
        && String(batchState?.mode || '').startsWith('finalize'));
    if (undoableCount > 0 && !batchState?.running) {
      const buttons = make('div', 'lh-repair-buttons');
      if (!undoActive) {
        const reviewUndo = make('button', 'lh-button lh-button-danger', 'Review Undo all remaining repairs');
        reviewUndo.type = 'button';
        reviewUndo.addEventListener('click', () => onStartUndoPreview(reviewUndo));
        buttons.appendChild(reviewUndo);
      }
      if (!finalizeActive) {
        const reviewFinalize = make('button', 'lh-button', 'Review Finalize all remaining repairs');
        reviewFinalize.type = 'button';
        reviewFinalize.addEventListener('click', () => onStartFinalizePreview(reviewFinalize));
        buttons.appendChild(reviewFinalize);
      }
      card.appendChild(buttons);
      card.appendChild(make(
        'p',
        'lh-muted',
        `Undo all restores saved original song data. Finalize all keeps the repaired Feedpaks and permanently removes their private recovery copies. Both choices independently verify every Feedpak. Packages changed since repair will be excluded rather than overwritten or finalized.${previewSuccessful ? ' Finalized automatic previews remain in place when song-data repairs are undone.' : ''}`,
      ));
    }
    el.batchResult.appendChild(card);
  }

  function repairOutcomeFilters() {
    return [
      {
        id: 'attention', label: 'Needs attention', preferred: true,
        test: (item) => ['failed', 'partial', 'skipped'].includes(item.outcome)
          || item.cache_updated === false || item.preview_cleanup_required,
      },
      { id: 'failed', label: 'Failed', test: (item) => item.outcome === 'failed' },
      { id: 'skipped', label: 'Skipped safely', test: (item) => item.outcome === 'skipped' },
      {
        id: 'undoable', label: 'Repaired with Undo',
        test: (item) => ['success', 'partial'].includes(item.outcome)
          && !!item.backup_id && item.undo_available !== false,
      },
      {
        id: 'previews', label: 'Preview finalized',
        test: (item) => !!item.preview_repaired && item.preview_finalized !== false,
      },
      { id: 'all', label: 'All', test: () => true },
    ];
  }

  function renderRepairOutcome(outcome) {
    const row = make('li');
    const heading = make('div', 'lh-batch-outcome-heading');
    heading.appendChild(make('strong', '', `${outcome.title || outcome.package}${outcome.artist ? ` - ${outcome.artist}` : ''}`));
    const tone = ['success', 'finalized'].includes(outcome.outcome) ? 'good'
      : outcome.outcome === 'failed' ? 'error'
        : outcome.outcome === 'restored' ? 'review' : 'warning';
    const label = outcome.outcome === 'success' ? 'Repaired'
      : outcome.outcome === 'restored' ? 'Original restored'
        : outcome.outcome === 'finalized' && outcome.preview_repaired ? 'Preview repaired'
          : outcome.outcome === 'finalized' ? 'Recovery finalized'
            : outcome.outcome === 'partial' ? 'Partially repaired'
              : outcome.outcome === 'failed' ? 'Failed' : 'Skipped';
    heading.appendChild(badge(label, tone));
    row.appendChild(heading);
    row.appendChild(make(
      'span',
      '',
      ['success', 'finalized'].includes(outcome.outcome)
        ? `${completedRepairChange(outcome)}. ${outcome.cache_updated === false ? 'Displayed scan result needs a manual refresh. ' : ''}${outcome.package}`
        : `${outcome.message || 'No additional details.'} ${outcome.package}`,
    ));
    if (['success', 'partial'].includes(outcome.outcome) && outcome.backup_id && outcome.undo_available !== false) {
      const rowActions = make('div', 'lh-batch-outcome-actions');
      const undo = make('button', 'lh-button', 'Review Undo');
      undo.type = 'button';
      undo.addEventListener('click', () => onReviewUndo(outcome, undo, row));
      rowActions.appendChild(undo);
      const finalize = make('button', 'lh-button', 'Finalize repair');
      finalize.type = 'button';
      finalize.addEventListener('click', () => repairActions.confirmFinalizeRecovery(
        outcome, finalize, rowActions,
      ));
      rowActions.appendChild(finalize);
      row.appendChild(rowActions);
    }
    if (outcome.preview_cleanup_required && outcome.preview_cleanup_backup_id) {
      const cleanupActions = make('div', 'lh-batch-outcome-actions');
      const cleanup = make('button', 'lh-button', 'Remove preview recovery copy');
      cleanup.type = 'button';
      const cleanupReceipt = {
        ...outcome,
        backup_id: outcome.preview_cleanup_backup_id,
        preview_cleanup: true,
        file_handling: {
          backup_size_bytes: Number(outcome.preview_cleanup_size_bytes || 0),
        },
      };
      cleanup.addEventListener('click', () => repairActions.confirmFinalizeRecovery(
        cleanupReceipt, cleanup, cleanupActions,
      ));
      cleanupActions.appendChild(cleanup);
      row.appendChild(cleanupActions);
    }
    return row;
  }

  return { renderBatch, renderFinalize, renderUndo };
}
