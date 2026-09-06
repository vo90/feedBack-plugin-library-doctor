export function createSourceRecoveryTool({ actions, document, make, request, isCurrent }) {
  function open(region, report) {
    region.replaceChildren();
    const title = make('h4', '', 'Recover source bends');
    const intro = make('p', '', 'Choose the original PSARC used to create this song. Library Doctor compares every arrangement and difficulty before showing any recoverable bend trajectories.');
    const label = make('label', 'lh-song-tool-search', 'Original PSARC path');
    const input = document.createElement('input');
    input.type = 'text';
    input.autocomplete = 'off';
    input.placeholder = 'C:\\Songs\\Original.psarc';
    label.appendChild(input);
    const inspect = make('button', 'lh-button', 'Inspect original source');
    inspect.type = 'button';
    const controls = make('div', 'lh-repair-buttons');
    controls.appendChild(inspect);
    const result = make('div', 'lh-repair-preview');
    result.setAttribute('aria-live', 'polite');
    region.append(title, intro, label, controls, result);
    let generation = 0;
    const alive = (token) => token === generation && isCurrent() && region.isConnected;
    input.addEventListener('input', () => { generation += 1; result.replaceChildren(); inspect.disabled = false; });
    const desktop = document.defaultView?.feedBackDesktop;
    if (typeof desktop?.pickFile === 'function') {
      const choose = make('button', 'lh-button', 'Choose original PSARC');
      choose.type = 'button';
      choose.addEventListener('click', async () => {
        if (input.disabled) return;
        try {
          const selected = await desktop.pickFile([{ name: 'Original Rocksmith source', extensions: ['psarc'] }]);
          if (selected && !input.disabled && isCurrent() && region.isConnected) {
            input.value = selected;
            generation += 1;
            result.replaceChildren();
            inspect.disabled = false;
          }
        } catch (error) {
          if (isCurrent()) result.replaceChildren(make('p', 'lh-inline-error', error.message));
        }
      });
      controls.insertBefore(choose, inspect);
    }
    inspect.addEventListener('click', async () => {
      const token = ++generation;
      const selected = input.value.trim();
      inspect.disabled = true;
      result.replaceChildren(make('p', '', 'Comparing the selected original with all stored chart copies...'));
      try {
        const plan = await request('/source-recovery/preview', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ package: report.package, source_path: selected }),
        });
        if (!alive(token)) return;
        result.replaceChildren(make('p', '', `${plan.change_count || 0} recoverable bend trajectories across ${plan.member_count || 0} song-data files. Original: ${plan.source_name || 'selected source'}.`));
        for (const blocker of plan.blockers || []) {
          result.appendChild(make('p', 'lh-repair-warning', `${blocker.member_path}: ${blocker.message}`));
        }
        if (plan.excluded_count) {
          result.appendChild(make('p', '', `${plan.excluded_count} existing or ambiguous bend occurrences are excluded and stay unchanged, including related copies.`));
          const excluded = make('details');
          excluded.appendChild(make('summary', '', 'Why some bends are excluded'));
          for (const item of plan.excluded || []) excluded.appendChild(make('p', '', `${item.member_path}: ${item.message}`));
          result.appendChild(excluded);
        }
        if (!plan.available) {
          result.appendChild(make('p', '', plan.blockers?.length ? 'Recovery is blocked; no package data was changed.' : 'No source-proven bend data needs recovery.'));
          return;
        }
        const rows = make('div', 'lh-source-recovery-changes');
        const pager = make('div', 'lh-repair-buttons');
        let page = 0;
        const renderPage = () => {
          rows.replaceChildren();
          const start = page * 50;
          rows.appendChild(make('p', '', `Changes ${start + 1}–${Math.min(start + 50, plan.changes.length)} of ${plan.changes.length}`));
          for (const change of plan.changes.slice(start, start + 50)) {
            const detail = make('details', 'lh-source-change');
            detail.appendChild(make('summary', '', `${change.member_path} · ${change.time}s · string ${change.string + 1}, fret ${change.fret} · ${change.path[0] === 'phrases' ? 'difficulty copy' : 'full chart'}`));
            detail.appendChild(make('pre', '', `Before: ${JSON.stringify(change.before)}\nAfter: ${JSON.stringify(change.after)}`));
            if (change.adjustments?.length) detail.appendChild(make('p', '', `Source boundary adjustments: ${change.adjustments.join(', ')}`));
            rows.appendChild(detail);
          }
          previous.disabled = page === 0;
          next.disabled = start + 50 >= plan.changes.length;
        };
        const previous = make('button', 'lh-button', 'Previous changes');
        const next = make('button', 'lh-button', 'Next changes');
        previous.type = 'button'; next.type = 'button';
        previous.addEventListener('click', () => { page -= 1; renderPage(); });
        next.addEventListener('click', () => { page += 1; renderPage(); });
        pager.append(previous, next);
        result.append(rows, pager);
        renderPage();
        result.appendChild(make('p', '', 'The complete candidate passed validation. Applying saves the original changed chart files for exact Undo; audio and other song data are preserved.'));
        const apply = make('button', 'lh-button lh-button-primary', 'Apply reviewed source recovery');
        apply.type = 'button';
        const requestId = `source-recovery-${Date.now()}`;
        apply.addEventListener('click', async () => {
          if (!alive(token)) return;
          apply.disabled = true; inspect.disabled = true; input.disabled = true;
          try {
            const receipt = await request('/source-recovery/apply', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
              package: report.package, source_path: selected, plan_id: plan.plan_id, request_id: requestId,
            }) });
            if (!alive(token)) return;
            receipt.id = `repair-${receipt.backup_id}`;
            receipt.title = receipt.report?.title || report.title;
            actions.renderRepairResult(receipt);
            result.replaceChildren(make('p', '', 'Source bend recovery completed. Undo is available in Activity and recovery.'));
            await actions.refreshStatus();
            await actions.loadResults();
          } catch (error) {
            if (!alive(token)) return;
            result.appendChild(make('p', 'lh-inline-error', error.message));
            actions.renderRepairFailure(report, error);
          } finally {
            if (alive(token)) { inspect.disabled = false; input.disabled = false; }
          }
        });
        result.appendChild(apply);
      } catch (error) {
        if (alive(token)) result.replaceChildren(make('p', 'lh-inline-error', error.message));
      } finally {
        if (alive(token)) inspect.disabled = false;
      }
    });
    input.focus();
  }
  return { open };
}
