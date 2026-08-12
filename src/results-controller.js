export function createResultsController({
  actions: actionRegistry,
  apiRoot,
  badge,
  document,
  getElements,
  isAbortError,
  make,
  number,
  pageSize,
  request,
  setHidden,
  state,
  text,
}) {
  const el = new Proxy({}, {
    get(_target, key) { return getElements()?.[key]; },
  });

  function updateFilterButtons() {
    const nodes = el.root.querySelectorAll('[data-filter]');
    nodes.forEach((node) => {
      node.setAttribute('aria-pressed', String(node.dataset.filter === state.filter));
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


  function packageNode(report) {
    const details = make('details', 'lh-package');
    const summary = make('summary');
    const heading = make('div', 'lh-package-title');
    const displayTitle = report.title || report.package || 'Unnamed package';
    const findings = Array.isArray(report.findings) ? report.findings : [];
    const findingNodes = (findings.length || !report.features?.preview_declared)
      ? actionRegistry.displayFindingNodes(report) : [];
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
    const allSafe = actionRegistry.allSafeRepairControls(report);
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

    const pages = Math.max(1, Math.ceil(total / pageSize));
    const page = Math.min(pages, Math.floor(state.offset / pageSize) + 1);
    setHidden(el.pagination, total <= pageSize);
    text(el.pageLabel, `Page ${page} of ${pages}`);
    el.prev.disabled = state.offset <= 0;
    el.next.disabled = state.offset + pageSize >= total;
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
      state.ruleMetadata = {};
      (payload.items || []).forEach((item) => {
        if (item?.code && item?.rule) state.ruleMetadata[item.code] = item.rule;
      });
      const available = (payload.items || []).some((item) => item.code === state.ruleCode);
      if (state.ruleCode && !available) state.ruleCode = '';
      renderRules(payload);
    } catch (error) {
      if (isAbortError(error) || !state.active) return;
      state.ruleMetadata = {};
      text(el.ruleError, error.message);
      setHidden(el.ruleError, false);
    }
  }

  async function loadRepairCatalog() {
    try {
      const [payload, reviewed] = await Promise.all([
        request('/repairs'),
        request('/reviewed-repairs'),
      ]);
      if (!state.active) return;
      state.repairRules = {};
      state.reviewedRepairAdapters = {};
      state.reviewedRuleAdapters = {};
      state.allSafeRepair = payload.combined || null;
      (payload.items || []).forEach((definition) => {
        if (definition && definition.rule_code) {
          state.repairRules[definition.rule_code] = definition;
        }
      });
      (reviewed.items || []).forEach((definition) => {
        if (!definition?.adapter_id) return;
        state.reviewedRepairAdapters[definition.adapter_id] = definition;
        (definition.trigger_rule_codes || []).forEach((code) => {
          state.reviewedRuleAdapters[code] = definition.adapter_id;
        });
      });
    } catch (error) {
      if (isAbortError(error) || !state.active) return;
      state.repairRules = {};
      state.reviewedRepairAdapters = {};
      state.reviewedRuleAdapters = {};
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
    link.href = `${apiRoot}/export?${params}`;
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
      limit: String(pageSize),
      offset: String(state.offset),
    });
    try {
      const payload = await request(`/results?${params}`);
      if (requestId !== state.resultRequest || !state.active) return;
      renderResults(payload);
    } catch (error) {
      if (isAbortError(error) || requestId !== state.resultRequest || !state.active) return;
      text(el.resultsError, error.message);
      setHidden(el.resultsError, false);
    }
  }


  return {
    exportResults,
    loadRepairCatalog,
    loadResults,
    loadRules,
    renderSummary,
    setFilter,
    setRule,
    updateFilterButtons,
  };
}
