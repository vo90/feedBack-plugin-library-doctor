export function createFindingView({ actions, document, make, number, state }) {
  function relatedFindingDetails(findings) {
    const technical = make('details', 'lh-finding-technical');
    technical.appendChild(make('summary', '', `Related checks (${number(findings.length)})`));
    const list = make('ul', 'lh-related-findings');
    findings.forEach((finding) => {
      const rule = currentRule(finding);
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

  function currentRule(finding) {
    const saved = finding?.rule || {};
    const current = state.ruleMetadata[finding?.code] || {};
    return { ...current, ...saved };
  }

  function repairEligibility(report, ruleCode) {
    const eligibility = report?.features?.repair_eligibility?.[ruleCode];
    return eligibility && typeof eligibility === 'object' ? eligibility : null;
  }

  function repairBlockerCopy(report, finding, rule) {
    const eligibility = repairEligibility(report, finding.code);
    if (!eligibility || eligibility.status === 'automatic') return '';
    if (eligibility.message) return eligibility.message;
    if (eligibility.reason_code === 'manifest_tones_require_manual_edit') {
      return 'These effective tone changes are stored in the manifest and require a manual edit.';
    }
    if (eligibility.reason_code === 'jsonc_requires_lossless_writer') {
      return 'This source uses JSONC comments and requires a comment-preserving writer before Library Doctor can change it safely.';
    }
    return rule.guidance || 'Library Doctor cannot safely change this stored data automatically.';
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
    const rule = currentRule(representative);
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
    const displayItem = affected === 1 ? itemName : pluralItem;
    const scope = arrangements.size
      ? `${number(arrangements.size)} arrangement${arrangements.size === 1 ? '' : 's'}`
      : `${number(sourceFiles.size || findings.length)} source ${sourceFiles.size === 1 ? 'file' : 'files'}`;
    const blockerCopy = repairBlockerCopy(report, representative, rule);

    const item = make('li', 'lh-finding lh-finding-repair-group');
    item.dataset.severity = representative.severity || 'warning';
    item.dataset.category = representative.category || 'validation';
    item.appendChild(make('strong', 'lh-finding-title', rule.title || definition.title || 'Safe repair available'));
    const technicalSummary = definition.change_kind === 'omit_empty'
      ? `${number(affected)} optional ${displayItem} ${affected === 1 ? 'stores' : 'store'} an explicit empty array across ${scope}. Omitting these empty root properties does not delete a musical event or position.`
      : definition.change_kind === 'normalize'
      ? `${number(affected)} pitchless string-mute ${affected === 1 ? 'position uses' : 'positions use'} a negative fret across ${scope}. Library Doctor can change only those fret values to 0 while preserving every other stored property.`
      : definition.change_kind === 'reorder'
      ? `${number(affected)} ${displayItem} ${affected === 1 ? 'is' : 'are'} stored outside chronological order across ${scope}.${blockerCopy ? ' Library Doctor leaves the source unchanged automatically.' : ' Every stored entry and property can be preserved by one package-wide repair.'}`
      : definition.change_kind === 'remove_redundant'
        ? `${number(affected)} musical ${affected === 1 ? 'position has' : 'positions have'} a zero-duration shape guide across ${scope}. Library Doctor removes only records whose exact matching chord already preserves the complete playable instruction.`
        : definition.change_kind === 'remove_duplicates'
          ? `${number(affected)} later ${itemName} ${affected === 1 ? 'copy is' : 'copies are'} complete JSON-identical duplicates across ${scope}.${blockerCopy ? ' Library Doctor leaves the source unchanged automatically.' : ' Library Doctor keeps the first complete stored event and never chooses between different same-time data.'}`
        : `${number(affected)} musical ${affected === 1 ? 'position contains' : 'positions contain'} redundant ${pluralItem} with identical stored values across ${scope}. These arrangement-level findings share one package-wide repair.`;
    appendFindingExplanation(
      item,
      technicalSummary,
      rule.player_impact,
      rule.fix_benefit,
      blockerCopy || 'Review the single package-wide fix below. Its preview recalculates every declared source file and shows the complete change before anything is saved.',
    );
    const repair = actions.repairControls(report, representative);
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
      evidence.appendChild(make('p', '', finding.message || 'A safe song-data issue was found.'));
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

  function reviewedFindingGroupNode(findings, report, adapterId) {
    const definition = state.reviewedRepairAdapters?.[adapterId] || {};
    const item = make('li', 'lh-finding lh-finding-group lh-reviewed-finding-group');
    item.dataset.severity = findings.some((finding) => finding.severity === 'warning')
      ? 'warning'
      : 'info';
    item.dataset.category = 'authoring_review';
    item.appendChild(make(
      'strong',
      'lh-finding-title',
      definition.title || 'Reviewed tab repair available',
    ));
    const affected = findings.reduce(
      (total, finding) => total + Math.max(1, Number(finding.affected_count || 1)),
      0,
    );
    appendFindingExplanation(
      item,
      `${number(affected)} stored HO/PO occurrence${affected === 1 ? '' : 's'} need an author decision. They may include both flags, a lone flag opposite to incoming fret movement, a same-fret transition, or no single usable predecessor.`,
      'The highway may show the wrong HO/PO symbol, hide one of two competing symbols, or present a technique that does not describe the authored transition.',
      'Reviewed repair compares stream-local previous/current/next evidence and changes nothing until you explicitly choose what each note should store.',
      report.features?.player_review?.available === false
        ? 'This song is outside the configured song library, so manual Player Review is unavailable. Automatic and standard repairs remain available, and Library Doctor will not make a manual choice for you.'
        : 'Open Player Review below to use the normal Highway and song playback, or use the text-only fallback. There is no default choice; Library Doctor shows only outcome-checked changes that resolve the current issue, while Skip for now leaves it unresolved. Every selected mutation receives full package validation, recovery backup, and Undo.',
    );
    const controls = actions.reviewedRepairControls(report, adapterId);
    if (controls) item.appendChild(controls);
    item.appendChild(relatedFindingDetails(findings));
    return item;
  }

  function displayFindingNodes(report) {
    const findings = Array.isArray(report.findings) ? [...report.findings] : [];
    if (!report.features?.preview_declared) {
      const previewEligibility = report.features?.repair_eligibility?.['media.preview-missing'];
      const repairScopeUnavailable = state.status?.target?.repairs_available === false;
      const canCreatePreview = !repairScopeUnavailable && previewEligibility?.status === 'automatic';
      findings.unshift({
        severity: 'info',
        category: 'library_optimization',
        code: 'media.preview-missing',
        message: 'The Feedpak does not declare an embedded song preview. Previews are optional in the format, but FeedBack cannot play a quick library excerpt for this song.',
        location: 'manifest.yaml',
        affected_count: 1,
        rule: {
          title: 'Song preview is missing',
          area: 'Audio and artwork',
          confidence: 'high',
          repairability: canCreatePreview ? 'review_required' : 'manual',
          guidance: canCreatePreview
            ? 'Library Doctor can create a representative preview automatically, or you can listen and choose another start before applying it.'
            : repairScopeUnavailable
              ? 'Scan this folder or package again before creating its preview.'
              : previewEligibility?.message || 'This Feedpak does not provide an unambiguous Ogg full mix that Library Doctor can use to create a preview automatically.',
          player_impact: 'Library browsing has no quick audio excerpt for this Feedpak.',
          fix_benefit: 'A compact preview makes the song easier to recognize without changing gameplay audio.',
        },
      });
    }
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
    const reviewedGroups = new Map();
    findings.forEach((finding) => {
      if (consumed.has(finding)) return;
      const adapterId = state.reviewedRuleAdapters?.[finding.code];
      if (adapterId) {
        if (!reviewedGroups.has(adapterId)) reviewedGroups.set(adapterId, []);
        reviewedGroups.get(adapterId).push(finding);
        return;
      }
      const definition = state.repairRules[finding.code];
      if (!definition || definition.safety !== 'safe_automatic') return;
      if (!repairGroups.has(finding.code)) repairGroups.set(finding.code, []);
      repairGroups.get(finding.code).push(finding);
    });

    findings.forEach((finding) => {
      if (consumed.has(finding)) return;
      const adapterId = state.reviewedRuleAdapters?.[finding.code];
      const reviewedGroup = adapterId && reviewedGroups.get(adapterId);
      if (reviewedGroup) {
        reviewedGroup.forEach((member) => consumed.add(member));
        nodes.push(reviewedFindingGroupNode(reviewedGroup, report, adapterId));
        return;
      }
      const group = repairGroups.get(finding.code);
      if (group && group.length > 1) {
        group.forEach((member) => consumed.add(member));
        nodes.push(repairableFindingGroupNode(group, report));
      } else {
        consumed.add(finding);
        nodes.push(findingNode(finding, report));
      }
    });
    const severityPriority = { error: 0, warning: 1, info: 2 };
    return nodes
      .map((node, index) => ({ node, index }))
      .sort((left, right) => (
        (severityPriority[left.node.dataset.severity] ?? 3)
        - (severityPriority[right.node.dataset.severity] ?? 3)
        || left.index - right.index
      ))
      .map(({ node }) => node);
  }

  function findingNode(finding, report) {
    const item = make('li', 'lh-finding');
    item.dataset.severity = finding.severity || 'info';
    item.dataset.category = finding.category || 'validation';
    const rule = currentRule(finding);
    const blockerCopy = repairBlockerCopy(report, finding, rule);
    item.appendChild(make('strong', 'lh-finding-title', rule.title || 'Validation issue'));
    appendFindingExplanation(
      item,
      finding.message || 'No additional description is available.',
      rule.player_impact,
      rule.fix_benefit,
      blockerCopy || rule.guidance,
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
    const repair = actions.repairControls(report, finding);
    if (repair) item.appendChild(repair);
    item.appendChild(technical);
    return item;
  }

  return { displayFindingNodes };
}
