// Pure-logic unit tests for landing-page/landing-logic.js, run with Node's
// built-in test runner. Covers the error-prone logic extracted from the
// landing page's inline JS:
//   - parseSummaryMetrics : map /api/summary counts onto the 4 metric cards,
//                           tolerant of missing status keys (e.g. no cancelled)
//   - drainLeadQueue      : re-sync queued localStorage leads to the API —
//                           all-succeed empties the queue, partial failure
//                           keeps the failures, empty queue is a no-op
// The DOM glue that calls these (fetch / textContent / localStorage) stays
// inline in landing-page/index.html and is inspection-verified.
const { test } = require('node:test');
const assert = require('node:assert');
const Landing = require('../../landing-page/landing-logic.js');

// ---- parseSummaryMetrics ----
test('parseSummaryMetrics: maps tracked + completed/delayed/cancelled from by_status', () => {
  const m = Landing.parseSummaryMetrics(
    { completed: 6, delayed: 2, operational: 30, announced: 46 },
    { tracked: 104, scored: 103 });
  assert.deepEqual(m, { tracked: 104, completed: 6, delayed: 2, cancelled: 0 });
});

test('parseSummaryMetrics: missing cancelled key reads as 0 (not undefined)', () => {
  const m = Landing.parseSummaryMetrics({ completed: 6, delayed: 2 }, { tracked: 104 });
  assert.equal(m.cancelled, 0);
  assert.equal(m.tracked, 104);
});

test('parseSummaryMetrics: null/missing inputs default everything to 0', () => {
  assert.deepEqual(Landing.parseSummaryMetrics(null, null),
    { tracked: 0, completed: 0, delayed: 0, cancelled: 0 });
  assert.deepEqual(Landing.parseSummaryMetrics({}, {}),
    { tracked: 0, completed: 0, delayed: 0, cancelled: 0 });
});

// ---- drainLeadQueue ----
test('drainLeadQueue: all-succeed empties the queue and counts every send', async () => {
  const queue = [{ email: 'a@x.test' }, { email: 'b@x.test' }];
  const out = await Landing.drainLeadQueue(queue, async () => true);
  assert.equal(out.sent, 2);
  assert.deepEqual(out.remaining, []);
});

test('drainLeadQueue: partial failure keeps only the failures', async () => {
  const queue = [{ email: 'a@x.test' }, { email: 'b@x.test' }, { email: 'c@x.test' }];
  // b fails, a and c succeed.
  const out = await Landing.drainLeadQueue(queue, async (lead) => lead.email !== 'b@x.test');
  assert.equal(out.sent, 2);
  assert.deepEqual(out.remaining, [{ email: 'b@x.test' }]);
});

test('drainLeadQueue: a throwing postFn is treated as a failure (lead kept)', async () => {
  const queue = [{ email: 'a@x.test' }];
  const out = await Landing.drainLeadQueue(queue, async () => { throw new Error('network'); });
  assert.equal(out.sent, 0);
  assert.deepEqual(out.remaining, [{ email: 'a@x.test' }]);
});

test('drainLeadQueue: empty queue is a no-op (postFn never called)', async () => {
  let calls = 0;
  const out = await Landing.drainLeadQueue([], async () => { calls++; return true; });
  assert.equal(calls, 0);
  assert.equal(out.sent, 0);
  assert.deepEqual(out.remaining, []);
});

test('drainLeadQueue: null queue is a no-op', async () => {
  const out = await Landing.drainLeadQueue(null, async () => true);
  assert.equal(out.sent, 0);
  assert.deepEqual(out.remaining, []);
});