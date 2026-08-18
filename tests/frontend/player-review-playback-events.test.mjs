import assert from 'node:assert/strict';
import test from 'node:test';

import {
  createPlayerReviewPlaybackObserver,
  subscribePlayerReviewPlaybackEvents,
} from '../../src/player-review-playback-events.js';

function harness({ requesterId = 'library_doctor' } = {}) {
  const calls = [];
  const binding = { sessionId: 'session-1', targetId: 'target-1' };
  let preview = { completing: false };
  const observer = createPlayerReviewPlaybackObserver({
    cancelPreview() { calls.push('cancel-preview'); preview = null; },
    getBinding: () => binding,
    getOperation: () => ({ expectedSignal: 'pause' }),
    getPreviewRun: () => preview,
    getSession: () => ({ active: true }),
    highlight: { install() { calls.push('highlight'); } },
    playbackClock: {
      isPlaying: true,
      update(media) { calls.push(['clock', media]); },
    },
    render() { calls.push('render'); },
    requesterId,
    setStatus(message) { calls.push(['status', message]); },
    timeline: {
      handlePlaybackEvent(type, detail, options) {
        calls.push(['timeline', type, detail.requesterId, options.internal]);
      },
      update() { calls.push('timeline-update'); },
    },
    transport: { supersede(reason) { calls.push(['supersede', reason]); } },
  });
  return { calls, observer };
}

test('a user pause stays external even while Library Doctor expects a pause signal', () => {
  const { calls, observer } = harness();
  observer('paused', {
    requesterId: 'core.player.controls',
    sessionId: 'session-1',
    target: { targetId: 'target-1' },
    media: { mediaTime: 8 },
  });
  assert.ok(calls.some((call) => Array.isArray(call)
    && call[0] === 'timeline' && call[3] === false));
  assert.ok(calls.includes('cancel-preview'));
  assert.ok(calls.some((call) => Array.isArray(call)
    && call[0] === 'supersede' && call[1] === 'preview-pause-with-player-controls'));
});

test('an exactly attributed Library Doctor pause remains internal', () => {
  const { calls, observer } = harness();
  observer('paused', {
    requesterId: 'library_doctor',
    sessionId: 'session-1',
    target: { targetId: 'target-1' },
    media: { mediaTime: 8 },
  });
  assert.ok(calls.some((call) => Array.isArray(call)
    && call[0] === 'timeline' && call[3] === true));
  assert.equal(calls.includes('cancel-preview'), false);
});

test('capability subscriptions unwrap authoritative playback payloads and clean up', () => {
  const listeners = new Map();
  const seen = [];
  const unsubscribers = subscribePlayerReviewPlaybackEvents({
    subscribe(name, callback) {
      listeners.set(name, callback);
      return () => listeners.delete(name);
    },
  }, (type, payload) => seen.push([type, payload.requesterId]));
  listeners.get('playback:paused')({ payload: { requesterId: 'core.player.controls' } });
  listeners.get('playback:resumed')({ payload: { requesterId: 'library_doctor' } });
  assert.deepEqual(seen, [
    ['paused', 'core.player.controls'],
    ['resumed', 'library_doctor'],
  ]);
  unsubscribers.forEach((unsubscribe) => unsubscribe());
  assert.equal(listeners.size, 0);
});
