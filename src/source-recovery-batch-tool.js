import { createSourceRecoveryTool } from './source-recovery-tool.js';

const API = '/source-recovery/batch';
const PAGE_SIZE = 25;
const SOURCE_ISSUE_LIMIT = 25;

export function createSourceRecoveryBatchTool({
  actions, document, make, request, isCurrent,
  schedule = setTimeout, unschedule = clearTimeout,
}) {
  let region, input, inspect, choose, refresh, progress, notice, content, details;
  let status = null;
  let timer = null;
  let visit = 0;
  let detailVisit = 0;
  let requestEpoch = 0;
  let invalidPlanId = '';
  let submittedFolder = null;
  let pending = false;
  let active = false;
  let page = 0;
  let query = '';
  let filter = 'all';
  let lastViewKey = '';
  const alive = (token) => active && token === visit && isCurrent() && region?.isConnected;
  const count = (value) => Number(value || 0).toLocaleString();
  const songs = (value) => `${count(value)} song${Number(value) === 1 ? '' : 's'}`;
  const button = (label, handler, primary = false) => {
    const node = make('button', `lh-button${primary ? ' lh-button-primary' : ''}`, label);
    node.type = 'button';
    node.addEventListener('click', handler);
    return node;
  };
  const showError = (error) => notice.replaceChildren(make('p', 'lh-inline-error', error.message || String(error)));
  const clearTimer = () => { if (timer !== null) unschedule(timer); timer = null; };
  const sameFolder = () => input.value.trim() === String(status?.preview?.source_folder || '').trim();
  const currentPlan = () => sameFolder() && status?.preview?.batch_plan_id !== invalidPlanId;

  function queuePoll() {
    clearTimer();
    if (active && !pending && status?.running) timer = schedule(() => { timer = null; poll(); }, 1000);
  }

  async function poll() {
    if (!active || !isCurrent() || pending) return;
    const token = visit;
    const epoch = ++requestEpoch;
    try {
      const next = await request(`${API}/status`);
      if (!alive(token) || epoch !== requestEpoch) return;
      accept(next);
    } catch (error) {
      if (alive(token) && epoch === requestEpoch) showError(error);
    } finally {
      if (alive(token) && epoch === requestEpoch) queuePoll();
    }
  }

  function accept(next) {
    status = next.status || next;
    notice.replaceChildren();
    if (submittedFolder !== null && !status.running) {
      if (status.phase === 'ready' && input.value.trim() === submittedFolder && status.preview?.source_folder) {
        input.value = status.preview.source_folder;
      }
      submittedFolder = null;
    }
    if (!input.value && status.preview?.source_folder) input.value = status.preview.source_folder;
    render();
  }

  async function post(path, body) {
    if (pending || !active || !isCurrent()) return;
    const token = visit;
    const epoch = ++requestEpoch;
    if (path === 'preview') submittedFolder = body.source_folder;
    pending = true;
    clearTimer();
    notice.replaceChildren();
    detailVisit += 1;
    details.replaceChildren();
    render();
    try {
      const next = await request(`${API}/${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (!alive(token) || epoch !== requestEpoch) return;
      accept(next);
    } catch (error) {
      if (path === 'preview') submittedFolder = null;
      if (alive(token) && epoch === requestEpoch) showError(error);
    } finally {
      pending = false;
      if (alive(token) && epoch === requestEpoch) { render(); queuePoll(); }
      else if (active && isCurrent() && region?.isConnected) poll();
    }
  }

  function pager(parent, total, currentPage, onPage, itemName) {
    const nav = make('nav', 'lh-pagination');
    nav.setAttribute('aria-label', `${itemName} pages`);
    const previous = button(`Previous ${itemName}`, () => onPage(currentPage - 1));
    const next = button(`Next ${itemName}`, () => onPage(currentPage + 1));
    previous.disabled = currentPage === 0;
    next.disabled = (currentPage + 1) * (itemName === 'changes' ? 50 : PAGE_SIZE) >= total;
    nav.append(previous, make('span', '', `Page ${currentPage + 1} of ${Math.max(1, Math.ceil(total / (itemName === 'changes' ? 50 : PAGE_SIZE)))}`), next);
    parent.appendChild(nav);
  }

  function renderChanges(parent, plan) {
    const changes = plan.changes || [];
    let changePage = 0;
    const area = make('div');
    parent.appendChild(area);
    const paint = () => {
      area.replaceChildren();
      for (const change of changes.slice(changePage * 50, (changePage + 1) * 50)) {
        const row = make('details', 'lh-source-change');
        row.appendChild(make('summary', '', `${change.member_path} · ${change.time}s · string ${Number(change.string) + 1}, fret ${change.fret} · ${change.path?.[0] === 'phrases' ? 'difficulty copy' : 'full chart'}`));
        row.appendChild(make('pre', '', `Before: ${JSON.stringify(change.before)}\nAfter: ${JSON.stringify(change.after)}`));
        if (change.adjustments?.length) row.appendChild(make('p', '', `Source boundary adjustments: ${change.adjustments.join(', ')}`));
        area.appendChild(row);
      }
      if (changes.length) pager(area, changes.length, changePage, (next) => { changePage = next; paint(); }, 'changes');
    };
    paint();
  }

  function renderSourceIssues(parent, preview) {
    const index = preview.source_index || {};
    const errors = preview.source_errors || index.errors || [];
    const links = index.skipped_links || [];
    const errorCount = Number(index.error_count ?? errors.length);
    const linkCount = Number(index.skipped_link_count ?? links.length);
    if (!errorCount && !linkCount && !index.limit_reached) return;
    const issues = make('details', 'lh-batch-details');
    issues.appendChild(make('summary', '', `Source folder issues: ${count(errorCount)} read errors; ${count(linkCount)} skipped links`));
    issues.appendChild(make('p', 'lh-repair-warning', 'The source folder could not be fully checked. Resolve these paths and preview again so unique matches can be verified.'));
    for (const error of errors.slice(0, SOURCE_ISSUE_LIMIT)) {
      issues.appendChild(make('p', 'lh-repair-warning', typeof error === 'string' ? error : `${error.relative_path || error.path || error.source || ''}: ${error.message || error.reason || error.code || 'Source could not be read'}`));
    }
    if (errorCount > SOURCE_ISSUE_LIMIT || index.errors_truncated) {
      issues.appendChild(make('p', '', `Showing ${count(Math.min(errors.length, SOURCE_ISSUE_LIMIT))} of ${count(errorCount)} read errors. Additional error details are omitted.`));
    }
    for (const path of links.slice(0, SOURCE_ISSUE_LIMIT)) issues.appendChild(make('p', 'lh-repair-warning', `Skipped linked path: ${path}`));
    if (linkCount > SOURCE_ISSUE_LIMIT || index.skipped_links_truncated) {
      issues.appendChild(make('p', '', `Showing ${count(Math.min(links.length, SOURCE_ISSUE_LIMIT))} of ${count(linkCount)} skipped links. Additional paths are omitted.`));
    }
    if (index.limit_reached) issues.appendChild(make('p', 'lh-repair-warning', 'The source folder exceeds the supported indexing limit. Choose a smaller source folder and preview again.'));
    parent.appendChild(issues);
  }

  function openIndividual(row, token) {
    if (!alive(token) || pending || status?.running) return;
    detailVisit += 1;
    const selected = detailVisit;
    const individualActions = { ...actions, renderRepairResult(receipt) {
      invalidPlanId = status?.preview?.batch_plan_id || '';
      actions.renderRepairResult(receipt);
      render();
    } };
    createSourceRecoveryTool({ actions: individualActions, document, make, request,
      isCurrent: () => alive(token) && selected === detailVisit && !status?.running && !pending,
    }).open(details, row);
    details.scrollIntoView?.({ block: 'nearest' });
  }

  async function openDetails(row) {
    if (!active || !isCurrent() || status?.running || pending) return;
    const token = visit;
    const selected = ++detailVisit;
    details.replaceChildren(make('h4', '', row.title || row.package), make('p', '', 'Loading exact source comparison...'));
    try {
      const plan = await request(`${API}/details?package=${encodeURIComponent(row.package)}`);
      if (!alive(token) || selected !== detailVisit) return;
      details.replaceChildren(make('h4', '', row.title || row.package));
      details.appendChild(make('p', 'lh-song-tool-path', row.package));
      details.appendChild(make('p', '', `Original: ${plan.source_name || row.source_name || 'unavailable'}. ${count(plan.change_count)} recoverable bend trajectories; ${count(plan.excluded_count)} excluded.`));
      for (const match of plan.matches || []) details.appendChild(make('p', '', `${match.member_path} matches ${match.source_member}`));
      for (const item of plan.blockers || []) details.appendChild(make('p', 'lh-repair-warning', `${item.member_path || ''}: ${item.message}`));
      renderChanges(details, plan);
      if (plan.excluded_count) {
        const excluded = make('details', 'lh-batch-details');
        excluded.appendChild(make('summary', '', 'Excluded bends stay unchanged'));
        for (const item of plan.excluded || []) excluded.appendChild(make('p', '', `${item.member_path || ''}: ${item.message}`));
        details.appendChild(excluded);
      }
    } catch (error) {
      if (!alive(token) || selected !== detailVisit) return;
      details.replaceChildren(make('h4', '', row.title || row.package), make('p', 'lh-repair-warning', error.message));
    }
    if (!alive(token) || selected !== detailVisit) return;
    details.appendChild(button('Choose an original for this song', () => openIndividual(row, token)));
    details.focus({ preventScroll: true });
    details.scrollIntoView?.({ block: 'nearest' });
  }

  function reviewRows(parent, rows, kind) {
    const rowState = (row) => (kind === 'result' ? row.outcome || row.status : row.status || row.outcome) || 'unknown';
    const controls = make('div', 'lh-outcome-controls');
    const search = document.createElement('input');
    search.type = 'search'; search.value = query; search.placeholder = 'Find song, artist, package, or source...';
    search.setAttribute('aria-label', 'Filter source recovery songs');
    const select = document.createElement('select');
    select.setAttribute('aria-label', 'Source recovery result type');
    const values = [...new Set(rows.map(rowState))];
    for (const value of ['all', ...values]) {
      const option = make('option', '', value === 'all' ? 'All results' : value.replaceAll('_', ' '));
      option.value = value; select.appendChild(option);
    }
    if (!['all', ...values].includes(filter)) filter = 'all';
    select.value = filter;
    controls.append(search, select);
    const listRegion = make('div');
    parent.append(controls, listRegion);
    function paint() {
      listRegion.replaceChildren();
      const needle = query.toLowerCase();
      const visible = rows.filter((row) => (filter === 'all' || rowState(row) === filter)
        && [row.title, row.artist, row.package, row.source_name, row.source_path].some((v) => String(v || '').toLowerCase().includes(needle)));
      page = Math.min(page, Math.max(0, Math.ceil(visible.length / PAGE_SIZE) - 1));
      listRegion.appendChild(make('p', 'lh-outcome-result-count', `${count(visible.length)} matching songs. Each row identifies the stored package and verified original source.`));
      const list = make('ul', 'lh-batch-list lh-batch-list-scroll');
      for (const row of visible.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)) {
        const item = make('li');
        item.appendChild(make('strong', '', `${row.title || row.package}${row.artist ? ` — ${row.artist}` : ''}`));
        item.appendChild(make('span', '', row.package));
        item.appendChild(make('span', '', `${rowState(row).replaceAll('_', ' ')} · ${count(row.change_count)} bend trajectories${kind === 'preview' ? ` · ${count(row.excluded_count)} excluded · ${count(row.member_count)} song-data files` : ''}`));
        if (row.source_name || row.source_path) item.appendChild(make('span', '', `Original: ${row.source_path || row.source_name}`));
        const reason = row.reason || row.message;
        if (reason) item.appendChild(make('span', '', typeof reason === 'string' ? reason : reason.message || JSON.stringify(reason)));
        if (kind === 'preview') {
          const buttons = make('div', 'lh-repair-buttons');
          buttons.appendChild(button('Review song details', () => openDetails(row)));
          if (row.status !== 'eligible') buttons.appendChild(button('Choose an original for this song', () => openIndividual(row, visit)));
          item.appendChild(buttons);
        }
        list.appendChild(item);
      }
      listRegion.appendChild(list);
      if (visible.length) pager(listRegion, visible.length, page, (next) => { page = next; paint(); }, 'songs');
    }
    search.addEventListener('input', () => { query = search.value; page = 0; paint(); });
    select.addEventListener('change', () => { filter = select.value; page = 0; paint(); });
    paint();
  }

  function render() {
    if (!region) return;
    const busy = pending || !!status?.running;
    input.disabled = busy;
    inspect.disabled = busy || !input.value.trim();
    if (choose) choose.disabled = busy;
    refresh.disabled = busy;
    progress.replaceChildren();
    progress.appendChild(make('strong', '', status?.message || 'Choose a source folder, then preview the matches.'));
    if (status?.running) {
      progress.appendChild(make('p', '', `Progress: ${count(status.done)} of ${count(status.total)}${status.current ? ` · ${status.current}` : ''}`));
      const bar = document.createElement('progress');
      bar.max = Math.max(1, Number(status.total || 0)); bar.value = Number(status.done || 0);
      bar.setAttribute('aria-label', 'Source bend recovery progress'); progress.appendChild(bar);
      const cancel = button('Stop after current song', () => post('cancel', {}));
      cancel.disabled = pending; progress.appendChild(cancel);
    }
    const viewKey = JSON.stringify([busy, status?.phase, status?.preview, status?.result, status?.last_result, status?.undo_preview, currentPlan()]);
    if (viewKey === lastViewKey) return;
    lastViewKey = viewKey;
    content.replaceChildren();
    if (busy) return;
    const preview = status?.preview;
    if (preview && status.phase === 'ready') {
      const card = make('div', 'lh-batch-card');
      card.appendChild(make('h4', '', 'Review source bend recovery'));
      card.appendChild(make('p', '', `${count(preview.scope_package_count)} packages checked: ${count(preview.eligible_count)} eligible, ${count(preview.blocked_count)} blocked or unavailable, ${count(preview.unchanged_count)} unchanged. ${count(preview.change_count)} recoverable bend trajectories.`));
      card.appendChild(make('p', 'lh-song-tool-path', `Source folder: ${preview.source_folder}`));
      card.appendChild(make('p', '', 'Only uniquely verified source matches are included. Review the song rows and exact changes before applying. Excluded bends and unavailable songs stay unchanged. Audio, including MinusMix tracks, is preserved.'));
      renderSourceIssues(card, preview);
      reviewRows(card, preview.packages || [], 'preview');
      if (!currentPlan()) card.appendChild(make('p', 'lh-repair-warning', 'The folder or a reviewed song changed. Preview again before applying.'));
      if (preview.eligible_count > 0 && preview.batch_plan_id && currentPlan()) {
        card.appendChild(button(`Apply reviewed recovery to ${songs(preview.eligible_count)}`, () => {
          if (status?.phase === 'ready' && currentPlan() && !status.running) post('apply', { batch_plan_id: preview.batch_plan_id });
        }, true));
      }
      content.appendChild(card);
    }
    const result = status?.result || status?.last_result;
    const retained = status?.last_result || (status?.result?.mode === 'apply' ? status.result : null);
    const remaining = (retained?.outcomes || []).filter((row) => row.outcome === 'success' && row.backup_id);
    let resultCard;
    if (result) {
      const card = make('div', 'lh-batch-card');
      card.appendChild(make('h4', '', result.mode === 'undo' ? 'Batch Undo results' : 'Source recovery results'));
      if (result.message) card.appendChild(make('p', '', result.message));
      reviewRows(card, result.outcomes || [], 'result');
      content.appendChild(card);
      resultCard = card;
    }
    if (remaining.length) {
      const card = result?.mode === 'undo' || !resultCard ? make('div', 'lh-batch-card') : resultCard;
      if (card !== resultCard) {
        card.appendChild(make('h4', '', 'Source repairs remaining to undo'));
        reviewRows(card, remaining, 'result');
        content.appendChild(card);
      }
      card.appendChild(make('p', '', `${songs(remaining.length)} retain saved original chart files. Preview Undo to check which backups can still be restored.`));
      card.appendChild(button('Preview batch Undo', () => post('undo/preview', {})));
    }
    const undo = status?.undo_preview;
    if (undo && status.phase === 'undo_ready') {
      const card = make('div', 'lh-batch-card lh-batch-undo-card');
      card.appendChild(make('h4', '', 'Review batch Undo'));
      card.appendChild(make('p', '', `${count(undo.eligible_count)} songs can be restored; ${count(undo.blocked_count)} are blocked. Only saved chart files will be restored; audio is preserved.`));
      reviewRows(card, undo.packages || [], 'undo');
      if (undo.eligible_count > 0 && undo.undo_plan_id) card.appendChild(button(`Restore originals for ${songs(undo.eligible_count)}`, () => {
        if (status?.phase === 'undo_ready' && !status.running) post('undo/apply', { undo_plan_id: undo.undo_plan_id });
      }));
      content.appendChild(card);
    }
  }

  function mount(node) {
    if (region === node) return;
    pause();
    region = node;
    if (!region) return;
    region.replaceChildren();
    region.append(make('h4', '', 'Recover source bends across the scanned library'),
      make('p', '', 'Choose a folder of original PSARCs. Preview compares every package in the completed Library Health scan, including songs with no bend warning. This can recover curves that were discarded during conversion. Selecting a folder does not change files.'));
    const label = make('label', 'lh-song-tool-search', 'Original PSARC folder');
    input = document.createElement('input'); input.type = 'text'; input.autocomplete = 'off'; input.placeholder = 'C:\\Songs\\Original PSARCs';
    label.appendChild(input);
    const controls = make('div', 'lh-repair-buttons');
    inspect = button('Preview source recovery', () => {
      if (input.value.trim() && !status?.running) { page = 0; query = ''; filter = 'all'; post('preview', { source_folder: input.value.trim() }); }
    });
    const desktop = document.defaultView?.feedBackDesktop;
    if (typeof desktop?.pickDirectory === 'function') {
      choose = button('Choose PSARC folder', async () => {
        const token = visit;
        try {
          const selected = await desktop.pickDirectory();
          if (selected && alive(token) && !input.disabled) { submittedFolder = null; input.value = selected; detailVisit += 1; details.replaceChildren(); render(); }
        } catch (error) { if (alive(token)) showError(error); }
      });
      controls.appendChild(choose);
    }
    refresh = button('Refresh batch status', poll);
    controls.append(inspect, refresh);
    progress = make('div', 'lh-batch-progress'); progress.setAttribute('role', 'status'); progress.setAttribute('aria-live', 'polite');
    notice = make('div'); notice.setAttribute('role', 'alert');
    content = make('div', 'lh-batch-content');
    details = make('div', 'lh-repair-preview');
    details.tabIndex = -1;
    details.setAttribute('aria-label', 'Selected song source comparison');
    input.addEventListener('input', () => { submittedFolder = null; detailVisit += 1; details.replaceChildren(); render(); });
    region.append(label, controls, progress, notice, content, details);
    lastViewKey = '';
    render();
  }

  function pause() { active = false; visit += 1; detailVisit += 1; requestEpoch += 1; clearTimer(); details?.replaceChildren(); }
  function resume() {
    if (!region || active) return;
    active = true; visit += 1;
    poll();
  }
  return { mount, resume, pause };
}
