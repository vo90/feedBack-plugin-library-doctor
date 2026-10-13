import assert from 'node:assert/strict';
import test from 'node:test';
import { createFormatters } from '../../src/formatters.js';
import { checkLyricPhraseDom } from './helpers/lyric-phrase-dom.mjs';

test('lyric repair counts added markers, including a combined receipt, without claiming removal', () => {
  const formatters = createFormatters({ number: String });
  for (const count of [1, 44]) {
    const repair = {
      change_kind: 'add_line_breaks', change_count: count, removed_count: 0, member_count: 2,
    };
    assert.equal(formatters.plannedRepairChange(repair),
      `add ${count} '+' lyric line-break marker${count === 1 ? '' : 's'}`);
    const completed = formatters.completedRepairChange(repair);
    assert.match(completed, new RegExp(`Added ${count} `));
    assert.match(completed, /preserving lyric text, cue timing, and every other stored property/);
    assert.doesNotMatch(completed, /[Rr]emov|redundant/);
    assert.equal(formatters.completedRepairChange({
      change_kind: 'combined', change_count: count, repair_summaries: [repair],
    }), completed);
  }
});

test('lyric scan repair, preview, completion, history, Undo and eligibility use actual DOM controllers', async (t) => {
  let JSDOM;
  try {
    ({ JSDOM } = await import('jsdom'));
  } catch (error) {
    if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
    t.skip('Repository jsdom dependency is not installed; the same fixture can run in Chromium.');
    return;
  }
  const dom = new JSDOM('<!doctype html><body></body>');
  try {
    const result = await checkLyricPhraseDom(dom.window.document);
    assert.ok(result.checks.length >= 25);
  } finally {
    dom.window.close();
  }
});
