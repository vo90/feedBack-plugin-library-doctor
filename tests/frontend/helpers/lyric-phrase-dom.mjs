import { createDomPrimitives } from '../../../src/dom.js';
import { createFormatters } from '../../../src/formatters.js';
import { createFindingView } from '../../../src/finding-view.js';
import { createPreviewController } from '../../../src/preview-controller.js';
import { createRepairController } from '../../../src/repair-controller.js';
import { createBatchResultsView } from '../../../src/batch-results-view.js';

// Shared by the frontend DOM test and the isolated Chromium smoke check.
// Only HTTP responses are fixtures: rendering, click handlers and confirmations
// are the production modules. This never opens or writes a song package.
export async function checkLyricPhraseDom(document) {
  const checks = [];
  const check = (value, message) => {
    if (!value) throw new Error(message);
    checks.push(message);
  };
  const ruleCode = 'lyrics.too-few-line-breaks';
  const dom = createDomPrimitives(document);
  const formatters = createFormatters(dom);
  const { make } = dom;
  const host = make('section');
  document.body.appendChild(host);
  const elements = Object.fromEntries([
    'repairResult', 'activitySection', 'activityStatus', 'activitySummaryLabel',
    'recoveryList', 'batchPreview',
  ].map((name) => [name, make('div')]));
  Object.values(elements).forEach((node) => host.appendChild(node));
  // JSDOM has no layout; this method is unrelated to the assertions below.
  if (!elements.repairResult.scrollIntoView) elements.repairResult.scrollIntoView = () => {};
  const definition = {
    title: 'Add readable lyric phrase endings', safety: 'safe_automatic',
    change_kind: 'add_line_breaks', action_kind: 'add_lyric_line_breaks',
  };
  const state = {
    active: true, repairRules: { [ruleCode]: definition },
    ruleMetadata: { [ruleCode]: {
      title: 'Lyrics may be missing line breaks', repairability: 'safe_candidate',
      player_impact: 'An oversized lyric block may appear in game.',
      fix_benefit: 'Readable phrase endings preserve the timed words.',
    } },
  };
  const finding = {
    code: ruleCode, severity: 'warning', affected_count: 1,
    message: 'One oversized lyric block was found.', location: 'lyrics.json',
  };
  const report = {
    package: 'Synthetic/Phrases.feedpak', title: 'Synthetic lyric phrases',
    findings: [finding], features: {
      preview_declared: true, repair_scan_current: true,
      repair_eligibility: { [ruleCode]: { status: 'automatic' } },
    },
  };
  const plan = {
    ...definition, available: true, plan_id: 'synthetic-plan', rule_code: ruleCode,
    change_count: 44, removed_count: 0, member_count: 2,
  };
  const receipt = {
    ...plan, ...report, outcome: 'success', backup_id: 'synthetic-backup', undo_available: true,
  };
  const calls = [];
  const request = async (url, options) => {
    const body = options?.body ? JSON.parse(options.body) : null;
    calls.push({ url, body });
    if (url === '/repair/preview') return structuredClone(plan);
    if (url === '/repair/apply') return structuredClone(receipt);
    if (url === '/repair/history?limit=20') return { items: [structuredClone(receipt)] };
    if (url === '/repair/restore') return {
      ...structuredClone(receipt), action: 'restore', outcome: 'restored', undo_available: false,
      file_handling: { backup_removed: true },
    };
    throw new Error(`Unexpected request: ${url}`);
  };
  const noop = () => {};
  const actions = {
    updateDashboardShell: noop, refreshStatus: noop, loadRules: noop, loadResults: noop,
    refreshSelectedSongTool: noop,
  };
  const shared = {
    ...dom, ...formatters, document, state, actions, request,
    getElements: () => elements, isAbortError: () => false, apiRoot: '/synthetic',
  };
  Object.assign(actions, createRepairController(shared), createPreviewController(shared));
  const findings = createFindingView(shared);
  const button = (node, label) => [...node.querySelectorAll('button')]
    .find((candidate) => candidate.textContent === label);
  const settle = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); };
  try {
    const scan = make('ul');
    host.appendChild(scan);
    scan.replaceChildren(...findings.displayFindingNodes(report));
    check(scan.textContent.includes('Lyrics may be missing line breaks'), 'scan shows the lyric problem');
    const review = button(scan, 'Review safe fix');
    check(Boolean(review), 'eligible scan offers the repair action');
    review.click();
    await settle();
    check(calls[0]?.url === '/repair/preview' && calls[0].body.rule_code === ruleCode,
      'scan repair action requests the correct preview');
    check(scan.textContent.includes(definition.title), 'preview shows the phrase repair title');
    check(scan.textContent.includes("Add 44 '+' lyric line-break markers across 2 lyric files"),
      'preview counts added markers across lyric files');
    check(!/Remove 0|redundant stored/.test(scan.textContent), 'preview never claims redundant lyric removal');
    button(scan, 'Apply safe repair').click();
    await settle();
    check(calls.some((call) => call.url === '/repair/apply' && call.body.plan_id === plan.plan_id),
      'apply sends the reviewed plan identity');
    check(elements.repairResult.textContent.includes("Added 44 '+' lyric line-break markers"),
      'completion counts additions');
    check(elements.repairResult.textContent.includes('preserving lyric text, cue timing'),
      'completion explains timing and text preservation');
    check(!elements.repairResult.textContent.includes('redundant'), 'completion does not claim deletions');
    button(elements.repairResult, 'Undo repair').click();
    check(elements.repairResult.textContent.includes("undoing 44 added '+' line-break markers"),
      'Undo confirmation describes reverting only added markers');
    button(elements.repairResult, 'Restore original lyrics').click();
    await settle();
    check(calls.some((call) => call.url === '/repair/restore' && call.body.backup_id === receipt.backup_id),
      'Undo submits the saved recovery identity');
    check(elements.repairResult.textContent.includes('Restored the exact original lyric files'),
      'Undo completion describes the original lyrics');
    await actions.loadRepairHistory();
    check(elements.repairResult.textContent.includes("Added 44 '+' lyric line-break markers"),
      'saved history uses marker addition wording');

    for (const [label, eligibility, current, expected] of [
      ['blocked', { status: 'author_review', message: 'No safe break before overlapping cues.' }, true,
        'No safe break before overlapping cues.'],
      ['missing', undefined, true, 'Safety not verified. Rescan'],
      ['malformed', [], true, 'Safety not verified. Rescan'],
      ['stale', { status: 'automatic' }, false, 'Safety not verified. Rescan'],
    ]) {
      const guarded = { ...report, features: { ...report.features,
        repair_scan_current: current, repair_eligibility: { [ruleCode]: eligibility } } };
      scan.replaceChildren(...findings.displayFindingNodes(guarded));
      check(scan.textContent.includes(expected), `${label} scan explains the repair restriction`);
      check(!button(scan, 'Review safe fix'), `${label} scan cannot offer unverified repair`);
    }
    const grouped = { ...report, findings: [finding, { ...finding, location: 'extra-lyrics.json' }] };
    scan.replaceChildren(...findings.displayFindingNodes(grouped));
    check(scan.textContent.includes('Oversized lyric blocks appear across 2 source files'),
      'multiple lyric tracks share a package repair explanation');
    check(!scan.textContent.includes('redundant'), 'grouped lyrics are never labelled redundant');
    check(scan.querySelectorAll('.lh-repair-action').length === 1, 'multiple tracks have one package repair action');

    createBatchResultsView({ ...shared, summaryGrid: () => make('div') }).renderUndo({
      outcome: 'complete', completed_count: 1, restored_count: 1, restored_change_count: 44,
      outcomes: [{ ...receipt, outcome: 'restored' }],
    });
    check(elements.batchPreview.textContent.includes("undoing 44 added '+' line-break markers"),
      'batch Undo identifies added lyric markers');
    return { checks, requestCount: calls.length };
  } finally {
    host.remove();
  }
}
