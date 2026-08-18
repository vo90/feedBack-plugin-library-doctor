import test from 'node:test';
import assert from 'node:assert/strict';

import { playerReviewHighlightCopy } from '../../src/player-review-choice-view.js';
import { createPlayerReviewHighlight } from '../../src/player-review-highlight.js';

function candidate(overrides = {}) {
  return {
    review_item_id: 'hopo-item-111111111111111111111111',
    context_kind: 'standalone_note',
    time: 2,
    string: 0,
    fret: 5,
    ...overrides,
  };
}

function highwayWith(provider = null) {
  let activeProvider = provider;
  return {
    getNoteStateProvider() { return activeProvider; },
    setNoteStateProvider(value) { activeProvider = value; },
  };
}

function host({ provider = null, reducedMotion = false } = {}) {
  let now = 0;
  return {
    window: {
      highway: highwayWith(provider),
      matchMedia() { return { matches: reducedMotion }; },
      performance: { now: () => now },
    },
    setNow(value) { now = value; },
  };
}

test('composite provider highlights only the current time, string, and fret', () => {
  const incumbentCalls = [];
  const incumbent = (note, chartTime) => {
    incumbentCalls.push([note, chartTime]);
    return 'miss';
  };
  const fixture = host({ provider: incumbent });
  const highlight = createPlayerReviewHighlight(fixture);
  highlight.update(candidate());

  assert.equal(highlight.install().state, 'installed');
  const provider = fixture.window.highway.getNoteStateProvider();
  const first = provider({ s: 0, f: 5 }, 2);
  assert.deepEqual(
    { state: first.state, color: first.color, live: first.live },
    { state: 'active', color: '#ffd166', live: true },
  );
  assert.ok(first.alpha >= 0.62 && first.alpha <= 1);
  const firstAlpha = first.alpha;
  assert.equal(provider({ s: 1, f: 5 }, 2), 'miss');
  assert.equal(provider({ s: 0, f: 6 }, 2), 'miss');
  assert.equal(provider({ s: 0, f: 5 }, 2.0001), 'miss');
  assert.equal(incumbentCalls.length, 3);

  fixture.setNow(275);
  assert.notEqual(provider({ s: 0, f: 5 }, 2).alpha, firstAlpha);
  assert.equal(highlight.release().state, 'idle');
  assert.equal(fixture.window.highway.getNoteStateProvider(), incumbent);
});

test('chord members use the supplied chord chart time rather than note.t', () => {
  const fixture = host();
  const highlight = createPlayerReviewHighlight(fixture);
  highlight.update(candidate({
    context_kind: 'chord_member',
    time: 4,
    string: 2,
    fret: 7,
  }));
  highlight.install();

  const provider = fixture.window.highway.getNoteStateProvider();
  assert.equal(provider({ t: 99, s: 2, f: 7 }, 4).state, 'active');
  assert.equal(provider({ t: 4, s: 2, f: 7 }, 99), null);
  assert.deepEqual(highlight.describe(), {
    reviewItemId: 'hopo-item-111111111111111111111111',
    contextKind: 'chord_member',
    time: 4,
    string: 2,
    fret: 7,
  });
});

test('reduced motion uses a steady full-strength highlight', () => {
  const fixture = host({ reducedMotion: true });
  const highlight = createPlayerReviewHighlight(fixture);
  highlight.update(candidate());
  highlight.install();
  const provider = fixture.window.highway.getNoteStateProvider();

  assert.equal(provider({ s: 0, f: 5 }, 2).alpha, 1);
  fixture.setNow(825);
  assert.equal(provider({ s: 0, f: 5 }, 2).alpha, 1);
});

test('invalid targets and unavailable hooks fail soft', () => {
  const window = { highway: { setNoteStateProvider() {} } };
  const highlight = createPlayerReviewHighlight({ window });

  assert.equal(highlight.update(candidate({ time: 'not-a-time' })).target, null);
  assert.deepEqual(highlight.install(), {
    state: 'unsupported',
    reason: 'note-state-provider-unavailable',
    supported: false,
    installed: false,
    displaced: false,
    target: null,
  });
});

test('visually ambiguous candidates use the text locator without pulsing gems', () => {
  const incumbent = () => 'incumbent';
  const fixture = host({ provider: incumbent });
  const highlight = createPlayerReviewHighlight(fixture);
  const ambiguousCandidate = candidate({ visual_target_ambiguous: true });

  assert.equal(highlight.update(ambiguousCandidate).target, null);
  const status = highlight.install();
  assert.equal(status.state, 'installed');
  assert.equal(status.target, null);
  assert.equal(fixture.window.highway.getNoteStateProvider()({ s: 0, f: 5 }, 2), 'incumbent');

  const copy = playerReviewHighlightCopy(ambiguousCandidate, status, String);
  assert.doesNotMatch(copy, /Pulsing note/);
  assert.match(copy, /Current issue/);
});

test('highlight copy requires an installed target before claiming a pulse', () => {
  const copy = playerReviewHighlightCopy(
    candidate(),
    { installed: true, target: null },
    String,
  );

  assert.doesNotMatch(copy, /Pulsing note/);
  assert.match(copy, /Current issue/);
});

test('a displaced provider is wrapped on reacquisition and restored on release', () => {
  const incumbent = () => 'hit';
  const usurper = () => 'miss';
  const fixture = host({ provider: incumbent });
  const highlight = createPlayerReviewHighlight(fixture);
  highlight.update(candidate());
  highlight.install();
  fixture.window.highway.setNoteStateProvider(usurper);

  assert.equal(highlight.status().state, 'displaced');
  assert.equal(highlight.status().reason, 'note-state-provider-replaced');
  assert.equal(highlight.install().state, 'installed');
  const reacquired = fixture.window.highway.getNoteStateProvider();
  assert.notEqual(reacquired, usurper);
  assert.equal(reacquired({ s: 3, f: 9 }, 8), 'miss');
  assert.equal(reacquired({ s: 0, f: 5 }, 2).state, 'active');
  highlight.release();
  assert.equal(fixture.window.highway.getNoteStateProvider(), usurper);
});

test('release after reacquisition does not overwrite a later displacement', () => {
  const incumbent = () => 'incumbent';
  const usurper = () => 'usurper';
  const latestProvider = () => 'latest';
  const fixture = host({ provider: incumbent });
  const highlight = createPlayerReviewHighlight(fixture);
  highlight.update(candidate());
  highlight.install();
  fixture.window.highway.setNoteStateProvider(usurper);
  highlight.install();
  fixture.window.highway.setNoteStateProvider(latestProvider);

  assert.equal(highlight.status().state, 'displaced');
  assert.equal(highlight.release().state, 'idle');
  assert.equal(fixture.window.highway.getNoteStateProvider(), latestProvider);
});

test('release permits clean reacquisition and destroy restores the latest incumbent', () => {
  const firstIncumbent = () => 'hit';
  const secondIncumbent = () => 'active';
  const fixture = host({ provider: firstIncumbent });
  const highlight = createPlayerReviewHighlight(fixture);
  highlight.update(candidate());
  highlight.install();
  highlight.release();
  fixture.window.highway.setNoteStateProvider(secondIncumbent);

  assert.equal(highlight.install().state, 'installed');
  const composite = fixture.window.highway.getNoteStateProvider();
  assert.equal(composite({ s: 4, f: 9 }, 8), 'active');
  assert.equal(highlight.destroy().state, 'destroyed');
  assert.equal(fixture.window.highway.getNoteStateProvider(), secondIncumbent);
  assert.equal(highlight.describe(), null);
  assert.equal(highlight.destroy().state, 'destroyed');
  assert.equal(highlight.update(candidate()).target, null);
});

test('an incumbent exception cannot break Player Review highlighting', () => {
  const fixture = host({ provider() { throw new Error('provider failed'); } });
  const highlight = createPlayerReviewHighlight(fixture);
  highlight.update(candidate());
  highlight.install();
  const provider = fixture.window.highway.getNoteStateProvider();

  assert.equal(provider({ s: 3, f: 3 }, 3), null);
  assert.equal(provider({ s: 0, f: 5 }, 2).state, 'active');
});
