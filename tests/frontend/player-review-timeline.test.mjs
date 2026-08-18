import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { JSDOM } from 'jsdom';

import { createPlayerReviewTimeline } from '../../src/player-review-timeline.js';
import { createPlayerReviewTransport } from '../../src/player-review-transport.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

async function waitFor(predicate, message = 'condition') {
  const deadline = Date.now() + 2_000;
  while (Date.now() < deadline) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  throw new Error(`Timed out waiting for ${message}`);
}

function createHarness({ duration = 100, issueTime = 25, playing = false, time = 10 } = {}) {
  const dom = new JSDOM('<!doctype html><body></body>', {
    pretendToBeVisual: true,
    url: 'http://127.0.0.1/',
  });
  const { document } = dom.window;
  const calls = [];
  const statuses = [];
  const reviewSession = { active: true, busy: false };
  let candidate = { review_item_id: 'issue-1', time: issueTime };
  const playbackClock = {
    duration,
    isPlaying: playing,
    mediaTime: time,
    chartToMedia: (value) => Number(value),
    clamp(value) {
      return Math.max(0, Math.min(this.duration, Number(value)));
    },
  };
  const transport = createPlayerReviewTransport({
    isAvailable: () => reviewSession.active,
    window: dom.window,
  });
  const make = (tag, className = '', value = null) => {
    const node = document.createElement(tag);
    node.className = className;
    if (value !== null) node.textContent = String(value);
    return node;
  };
  const timeline = createPlayerReviewTimeline({
    cancelPreview: () => calls.push(['cancel-preview']),
    document,
    getCurrentCandidate: () => candidate,
    getReviewSession: () => reviewSession,
    layout: {
      installHandle() {},
      position() {},
    },
    make,
    async pausePlayer(operation) {
      transport.assertCurrent(operation);
      calls.push(['pause']);
      playbackClock.isPlaying = false;
    },
    playbackClock,
    renderReview() {},
    async resumePlayer(operation) {
      transport.assertCurrent(operation);
      calls.push(['resume']);
      playbackClock.isPlaying = true;
    },
    async seekPlayer(operation, target, action) {
      transport.assertCurrent(operation);
      calls.push(['seek', target, action]);
      playbackClock.mediaTime = target;
    },
    setStatus(value, tone) {
      statuses.push([value, tone]);
    },
    transport,
  });
  timeline.render();
  return {
    calls,
    close() {
      timeline.destroy();
      dom.window.close();
    },
    document,
    playbackClock,
    setCandidate(value) { candidate = value; },
    statuses,
    timeline,
    transport,
    window: dom.window,
  };
}

function transportCalls(harness) {
  return harness.calls.filter(([name]) => name !== 'cancel-preview');
}

test('timeline renders one accessible whole-song range with an inset issue marker rail', async (t) => {
  const harness = createHarness();
  t.after(() => harness.close());
  const ranges = harness.document.querySelectorAll('.lh-player-review-timeline-range');
  assert.equal(ranges.length, 1);
  assert.equal(harness.document.querySelector('.lh-player-review-timeline-fine'), null);
  const range = ranges[0];
  const issue = harness.document.querySelector('#lh-player-review-timeline-issue');
  const marker = harness.document.querySelector('.lh-player-review-timeline-marker');
  assert.equal(range.getAttribute('aria-describedby'), issue.id);
  assert.equal(range.getAttribute('aria-label'), 'Move through the whole song');
  assert.equal(marker.parentElement.className, 'lh-player-review-timeline-marker-rail');
  assert.equal(marker.style.left, '25%');
  assert.equal(range.value, '10');
  assert.equal(range.getAttribute('aria-valuetext'), '0:10.00 of 1:40.0');
  assert.deepEqual(
    [...harness.document.querySelectorAll('.lh-player-review-timeline-nudges button')]
      .map((button) => button.getAttribute('aria-label')),
    [
      'Move backward 1 second',
      'Move backward 0.1 seconds',
      'Move forward 0.1 seconds',
      'Move forward 1 second',
    ],
  );

  const css = await fs.readFile(path.join(ROOT, 'assets', 'library-doctor.css'), 'utf8');
  assert.match(css, /--lh-timeline-thumb-size:\s*1rem/);
  assert.match(
    css,
    /\.lh-player-review-timeline-marker-rail\s*\{[^}]*inset:\s*0 calc\(var\(--lh-timeline-thumb-size\) \/ 2\)/s,
  );
});

test('range drag stays local and commits one pause, seek, and owned resume', async (t) => {
  const harness = createHarness({ playing: true });
  t.after(() => harness.close());
  const range = harness.document.querySelector('.lh-player-review-timeline-range');
  range.dispatchEvent(new harness.window.Event('pointerdown', { bubbles: true }));
  await waitFor(() => transportCalls(harness).some(([name]) => name === 'pause'), 'owned pause');
  for (let index = 0; index < 100; index += 1) {
    range.value = String(20 + (index * 0.25));
    range.dispatchEvent(new harness.window.Event('input', { bubbles: true }));
  }
  assert.equal(transportCalls(harness).some(([name]) => name === 'seek'), false);
  assert.equal(range.value, '44.75');
  range.dispatchEvent(new harness.window.Event('change', { bubbles: true }));
  harness.window.dispatchEvent(new harness.window.Event('pointerup'));
  await harness.timeline.settle();
  assert.deepEqual(transportCalls(harness), [
    ['pause'],
    ['seek', 44.75, 'timeline'],
    ['resume'],
  ]);
  assert.equal(harness.playbackClock.mediaTime, 44.75);
  assert.equal(harness.playbackClock.isPlaying, true);
});

test('internal playback confirmation keeps the active adjustment owned', async (t) => {
  const harness = createHarness({ playing: true });
  t.after(() => harness.close());
  const button = harness.document.querySelector('[aria-label="Move forward 1 second"]');
  button.dispatchEvent(new harness.window.KeyboardEvent('keydown', {
    bubbles: true,
    key: 'Enter',
  }));
  await waitFor(() => transportCalls(harness).some(([name]) => name === 'pause'), 'internal pause');
  assert.equal(harness.timeline.handlePlaybackEvent('pause', {}, { internal: true }), false);
  button.dispatchEvent(new harness.window.KeyboardEvent('keyup', {
    bubbles: true,
    key: 'Enter',
  }));
  await harness.timeline.settle();
  assert.deepEqual(transportCalls(harness), [
    ['pause'],
    ['seek', 11, 'timeline'],
    ['resume'],
  ]);
});

test('held keyboard nudges coalesce locally without floating-point drift', async (t) => {
  const harness = createHarness({ playing: true });
  t.after(() => harness.close());
  const button = harness.document.querySelector('[aria-label="Move forward 0.1 seconds"]');
  button.dispatchEvent(new harness.window.KeyboardEvent('keydown', {
    bubbles: true,
    key: ' ',
  }));
  for (let index = 1; index < 100; index += 1) {
    button.dispatchEvent(new harness.window.KeyboardEvent('keydown', {
      bubbles: true,
      key: ' ',
      repeat: true,
    }));
  }
  await waitFor(() => transportCalls(harness).some(([name]) => name === 'pause'), 'nudge pause');
  assert.equal(transportCalls(harness).some(([name]) => name === 'seek'), false);
  assert.equal(harness.document.querySelector('.lh-player-review-timeline-range').value, '20');
  button.dispatchEvent(new harness.window.KeyboardEvent('keyup', {
    bubbles: true,
    key: ' ',
  }));
  await harness.timeline.settle();
  assert.deepEqual(transportCalls(harness), [
    ['pause'],
    ['seek', 20, 'timeline'],
    ['resume'],
  ]);
});

test('pointer hold repeats locally and sends one final seek on release', async (t) => {
  const harness = createHarness({ playing: false });
  t.after(() => harness.close());
  const button = harness.document.querySelector('[aria-label="Move forward 1 second"]');
  button.dispatchEvent(new harness.window.Event('pointerdown', { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 520));
  assert.equal(transportCalls(harness).some(([name]) => name === 'seek'), false);
  const localTarget = Number(
    harness.document.querySelector('.lh-player-review-timeline-range').value,
  );
  assert.ok(localTarget >= 13, `expected held local target, got ${localTarget}`);
  harness.window.dispatchEvent(new harness.window.Event('pointerup'));
  await harness.timeline.settle();
  assert.deepEqual(transportCalls(harness), [['seek', localTarget, 'timeline']]);
});

test('an external playback action revokes ownership with no delayed seek or resume', async (t) => {
  const harness = createHarness({ playing: true });
  t.after(() => harness.close());
  const button = harness.document.querySelector('[aria-label="Move forward 1 second"]');
  button.dispatchEvent(new harness.window.KeyboardEvent('keydown', {
    bubbles: true,
    key: 'Enter',
  }));
  await waitFor(() => transportCalls(harness).some(([name]) => name === 'pause'), 'external test pause');
  harness.playbackClock.mediaTime = 42;
  harness.playbackClock.isPlaying = true;
  assert.equal(harness.timeline.handlePlaybackEvent('seek', {}, { internal: false }), true);
  await harness.transport.settle();
  assert.deepEqual(transportCalls(harness), [['pause']]);
  assert.equal(harness.timeline.isScrubbing(), false);
  assert.equal(
    harness.document.querySelector('.lh-player-review-timeline-range').value,
    '42',
  );
});

test('local cancellation restores only playback paused by the adjustment', async (t) => {
  const harness = createHarness({ playing: true });
  t.after(() => harness.close());
  const button = harness.document.querySelector('[aria-label="Move backward 0.1 seconds"]');
  button.dispatchEvent(new harness.window.KeyboardEvent('keydown', {
    bubbles: true,
    key: ' ',
  }));
  await waitFor(() => transportCalls(harness).some(([name]) => name === 'pause'), 'cancel pause');
  button.dispatchEvent(new harness.window.KeyboardEvent('keydown', {
    bubbles: true,
    key: 'Escape',
  }));
  await harness.timeline.settle();
  assert.deepEqual(transportCalls(harness), [['pause'], ['resume']]);
  assert.equal(harness.playbackClock.mediaTime, 10);
  assert.equal(harness.playbackClock.isPlaying, true);
});

test('a click while already paused seeks once and never resumes playback', async (t) => {
  const harness = createHarness({ playing: false, time: 99.9 });
  t.after(() => harness.close());
  harness.document.querySelector('[aria-label="Move forward 1 second"]').click();
  await harness.timeline.settle();
  assert.deepEqual(transportCalls(harness), [['seek', 100, 'timeline']]);
  assert.equal(harness.playbackClock.isPlaying, false);
});
