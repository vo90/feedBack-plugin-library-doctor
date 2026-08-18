import assert from 'node:assert/strict';
import test from 'node:test';

import { createPlayerReviewNavigation } from '../../src/player-review-navigation.js';
import { createPlayerReviewOptions } from '../../src/player-review-options.js';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function candidate(id, itemId) {
  return {
    candidate_id: id,
    review_item_id: itemId,
  };
}

function response(item, decisions) {
  return {
    candidate_id: item.candidate_id,
    review_item_id: item.review_item_id,
    decision_names: decisions,
  };
}

test('fresh options quarantine preserved choices until each exact decision is re-approved', async () => {
  const oldFirst = candidate('hopo-old-a', 'hopo-item-a');
  const oldSecond = candidate('hopo-old-b', 'hopo-item-b');
  const freshFirst = candidate('hopo-fresh-a', 'hopo-item-a');
  const freshSecond = candidate('hopo-fresh-b', 'hopo-item-b');
  const requests = new Map([
    [freshFirst.candidate_id, deferred()],
    [freshSecond.candidate_id, deferred()],
  ]);
  const session = {
    package: 'Song.feedpak',
    adapterId: 'review.hopo-techniques',
    difficultyScope: 'full_only',
    context: { inspection: { candidates: [freshFirst, freshSecond] } },
    accepted: new Map([
      [oldFirst.review_item_id, { candidate: oldFirst, decision: 'remove_hopo' }],
      [oldSecond.review_item_id, { candidate: oldSecond, decision: 'set_pull_off' }],
    ]),
    options: new Map(),
    pendingPlan: { plan_id: 'stale-plan' },
  };
  let renders = 0;
  let refreshes = 0;
  const requestCalls = [];
  const options = createPlayerReviewOptions({
    getCurrentCandidate: () => freshFirst,
    getSession: () => session,
    getUnresolvedCandidates: () => [],
    render: () => { renders += 1; },
    request: (_path, init) => {
      const body = JSON.parse(init.body);
      requestCalls.push(body.candidate_id);
      return requests.get(body.candidate_id).promise;
    },
    refreshTransform: () => { refreshes += 1; },
  });

  const firstLoad = options.load(freshFirst);
  assert.equal(session.accepted.size, 0, 'stale choices cannot reach Preview or Apply');
  assert.equal(session.pendingPlan, null);
  assert.deepEqual(new Set(requestCalls), new Set([
    freshFirst.candidate_id,
    freshSecond.candidate_id,
  ]));
  assert.ok(renders >= 1);
  assert.equal(refreshes, 1);

  requests.get(freshFirst.candidate_id).resolve(response(freshFirst, ['remove_hopo']));
  await firstLoad;
  assert.deepEqual(session.accepted.get(freshFirst.review_item_id), {
    candidate: freshFirst,
    decision: 'remove_hopo',
  });
  assert.equal(options.approvalState().pending, 1);
  assert.equal(options.approvalState().failed, 0);
  assert.equal(options.approvalState().blocked, true);

  requests.get(freshSecond.candidate_id).resolve(response(freshSecond, ['set_hammer_on']));
  await requests.get(freshSecond.candidate_id).promise;
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  assert.equal(session.accepted.has(freshSecond.review_item_id), false);
  assert.equal(options.approvalState().blocked, false);
  assert.ok(refreshes >= 3, 'both restored and removed choices refresh the live transform');
  assert.ok(renders >= 3, 'both restored and removed choices refresh the review UI');
});

test('preserved approval queue keeps 25 checks at two concurrent requests', async () => {
  const fresh = Array.from({ length: 25 }, (_value, index) => candidate(
    `hopo-fresh-${index}`,
    `hopo-item-${index}`,
  ));
  const session = {
    package: 'Song.feedpak',
    adapterId: 'review.hopo-techniques',
    difficultyScope: 'full_only',
    context: { inspection: { candidates: fresh } },
    accepted: new Map(fresh.map((item) => [item.review_item_id, {
      candidate: { ...item, candidate_id: `hopo-old-${item.review_item_id}` },
      decision: 'remove_hopo',
    }])),
    options: new Map(),
    pendingPlan: null,
  };
  const requests = [];
  let active = 0;
  let maximumActive = 0;
  const options = createPlayerReviewOptions({
    getCurrentCandidate: () => fresh[0],
    getSession: () => session,
    getUnresolvedCandidates: () => fresh.filter(
      (item) => !session.accepted.has(item.review_item_id),
    ),
    render() {},
    request: (_path, init) => {
      const body = JSON.parse(init.body);
      const gate = deferred();
      const item = fresh.find((candidateItem) => candidateItem.candidate_id === body.candidate_id);
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      const requestItem = { gate, item, settled: false };
      requests.push(requestItem);
      return gate.promise.finally(() => { active -= 1; });
    },
    refreshTransform() {},
  });

  options.load(fresh[0]);
  assert.equal(requests.length, 2);
  assert.deepEqual(options.approvalState(), {
    pending: 25,
    failed: 0,
    active: 2,
    queued: 23,
    blocked: true,
  });

  for (let completed = 0; completed < fresh.length; completed += 1) {
    const requestItem = requests.find((item) => !item.settled);
    assert.ok(requestItem, `approval request ${completed + 1} should be available`);
    requestItem.settled = true;
    requestItem.gate.resolve(response(requestItem.item, ['remove_hopo']));
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  }

  assert.equal(maximumActive, 2);
  assert.equal(requests.length, 25);
  assert.equal(session.accepted.size, 25);
  assert.equal(options.approvalState().blocked, false);
});

test('skipping an issue prevents an in-flight preserved choice from returning', async () => {
  const fresh = candidate('hopo-fresh-a', 'hopo-item-a');
  const pendingResponse = deferred();
  const session = {
    package: 'Song.feedpak',
    adapterId: 'review.hopo-techniques',
    difficultyScope: 'full_only',
    context: { inspection: { candidates: [fresh] } },
    accepted: new Map([
      [fresh.review_item_id, { candidate: fresh, decision: 'remove_hopo' }],
    ]),
    options: new Map(),
    pendingPlan: null,
  };
  const options = createPlayerReviewOptions({
    getCurrentCandidate: () => fresh,
    getSession: () => session,
    getUnresolvedCandidates: () => [],
    render() {},
    request: () => pendingResponse.promise,
    refreshTransform() {},
  });

  const loading = options.load(fresh);
  assert.equal(session.accepted.size, 0);
  assert.equal(options.discardPending(fresh.review_item_id), true);
  pendingResponse.resolve(response(fresh, ['remove_hopo']));
  await loading;
  assert.equal(session.accepted.size, 0);
});

test('failed preserved approval blocks the group until navigation skips it', async () => {
  const fresh = candidate('hopo-fresh-a', 'hopo-item-a');
  const pendingResponse = deferred();
  const session = {
    package: 'Song.feedpak',
    adapterId: 'review.hopo-techniques',
    difficultyScope: 'full_only',
    context: { inspection: { candidates: [fresh] } },
    accepted: new Map([
      [fresh.review_item_id, { candidate: fresh, decision: 'remove_hopo' }],
    ]),
    skipped: new Set(),
    options: new Map(),
    pendingPlan: null,
    tentative: null,
    busy: false,
    index: 0,
  };
  const options = createPlayerReviewOptions({
    getCurrentCandidate: () => fresh,
    getSession: () => session,
    getUnresolvedCandidates: () => [fresh],
    render() {},
    request: () => pendingResponse.promise,
    refreshTransform() {},
  });
  const navigation = createPlayerReviewNavigation({
    cancelPreview() {},
    getCandidates: () => [fresh],
    getCurrentCandidate: () => fresh,
    getSession: () => session,
    highlight: { update() {} },
    number: (value) => String(value),
    async openCurrentInPlayer() {},
    options,
    async refreshTransform() {},
    render() {},
    setStatus() {},
    timeline: { supersede() {} },
  });

  const loading = options.load(fresh);
  pendingResponse.reject(new Error('Synthetic approval failure'));
  await loading;
  assert.deepEqual(options.approvalState(), {
    pending: 0,
    failed: 1,
    active: 1,
    queued: 0,
    blocked: true,
  });

  await navigation.skipCurrent();
  assert.equal(session.skipped.has(fresh.review_item_id), true);
  assert.equal(options.approvalState().failed, 0);
  assert.equal(options.approvalState().blocked, false);
});

test('a failed preserved approval retries only after the explicit retry action', async () => {
  const fresh = candidate('hopo-fresh-a', 'hopo-item-a');
  const requests = [deferred(), deferred()];
  const session = {
    package: 'Song.feedpak',
    adapterId: 'review.hopo-techniques',
    difficultyScope: 'full_only',
    context: { inspection: { candidates: [fresh] } },
    accepted: new Map([[
      fresh.review_item_id,
      { candidate: fresh, decision: 'remove_hopo' },
    ]]),
    options: new Map(),
    pendingPlan: null,
  };
  let requestCount = 0;
  const options = createPlayerReviewOptions({
    getCurrentCandidate: () => fresh,
    getSession: () => session,
    getUnresolvedCandidates: () => [fresh],
    render() {},
    request: () => requests[requestCount++].promise,
    refreshTransform() {},
  });

  const loading = options.load(fresh);
  requests[0].reject(new Error('Synthetic approval failure'));
  await loading;
  await options.load(fresh);
  assert.equal(requestCount, 1, 'navigation and rendering must not retry a failed choice');
  assert.equal(options.approvalState().failed, 1);

  options.retry(fresh);
  assert.equal(requestCount, 2);
  assert.equal(options.approvalState().pending, 1);
  requests[1].resolve(response(fresh, ['remove_hopo']));
  await requests[1].promise;
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
  assert.deepEqual(session.accepted.get(fresh.review_item_id), {
    candidate: fresh,
    decision: 'remove_hopo',
  });
  assert.equal(options.approvalState().blocked, false);
});
