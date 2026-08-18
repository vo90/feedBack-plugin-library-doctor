import assert from 'node:assert/strict';
import test from 'node:test';

import { createPlayerReviewChartTransform } from '../../src/player-review-chart-transform.js';

const providerId = 'library_doctor.player-review';

function harness({ refreshed = true } = {}) {
  const requests = [];
  const input = {
    chords: [{
      t: 4,
      notes: [
        { s: 0, f: 0, po: true },
        { s: 2, f: 7, ho: true },
      ],
    }],
  };
  let active = 'another-provider';
  let transform = null;
  let choices = [];
  const dispatch = async (_capability, command, args = {}) => {
    requests.push({ command, args });
    if (command === 'inspect') {
      return { ok: true, payload: { active, installed: Boolean(active), surfaces: 1 } };
    }
    if (command === 'register-provider') {
      transform = args.transform;
      return { ok: true, payload: { providerId: args.providerId } };
    }
    if (command === 'select-provider') {
      active = args.providerId;
      transform?.(input);
      return {
        ok: true,
        payload: { active, installed: true, surfaces: 1 },
      };
    }
    if (command === 'refresh') {
      if (refreshed) transform?.(input);
      return {
        ok: true,
        payload: { active, installed: true, surfaces: 1, refreshed },
      };
    }
    if (command === 'unregister-provider') return { ok: true, payload: {} };
    throw new Error(`Unexpected command: ${command}`);
  };
  const candidate = {
    time: 4,
    string: 2,
    fret: 7,
    context_kind: 'chord_member',
    techniques: { hammer_on: true, pull_off: false, tap: false },
    stream_context: { kind: 'top_level' },
  };
  const manager = createPlayerReviewChartTransform({
    dispatch,
    getChoices: () => choices,
    getLoadedFilename: () => 'song.feedpak',
    getSession: () => ({
      active: true,
      context: { playback_filename: 'song.feedpak' },
    }),
    window: { console: { info() {} } },
  });
  return {
    input,
    manager,
    requests,
    selectChoice() {
      choices = [{ candidate, decision: 'convert_to_tap' }];
    },
  };
}

test('refresh rebinds the provider and changes only the reviewed chord member', async () => {
  const {
    input, manager, requests, selectChoice,
  } = harness();

  assert.equal(await manager.activate(), true);
  selectChoice();
  const result = await manager.refresh({ reason: 'choice-selected', requireChange: true });

  assert.equal(result.verified, true);
  assert.equal(input.chords[0].notes[1].tp, true);
  assert.equal(input.chords[0].notes[1].ho, undefined);
  assert.equal(input.chords[0].notes[0].po, true);
  assert.deepEqual(
    requests.slice(-3).map(({ command }) => command),
    ['inspect', 'select-provider', 'refresh'],
  );
});

test('a host refresh that did not run cannot be presented as a live preview', async () => {
  const { manager, selectChoice } = harness({ refreshed: false });

  assert.equal(await manager.activate(), true);
  selectChoice();
  const result = await manager.refresh({ reason: 'choice-selected', requireChange: true });

  assert.equal(result.refreshSucceeded, false);
  assert.equal(result.verified, false);
});
