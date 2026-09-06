import assert from 'node:assert/strict';
import test from 'node:test';
import { createFormatters } from '../../src/formatters.js';

test('value normalization describes repairs without claiming deletion or negative frets', () => {
  const { plannedRepairChange, completedRepairChange } = createFormatters({ number: String });
  const value = { change_kind: 'normalize_values', item_name: 'muted fret sentinel', change_count: 3, member_count: 2 };
  assert.equal(plannedRepairChange(value), 'normalize 3 muted fret sentinels');
  assert.equal(completedRepairChange(value), 'Normalized 3 muted fret sentinels across 2 song-data files while preserving every other stored property');
});
