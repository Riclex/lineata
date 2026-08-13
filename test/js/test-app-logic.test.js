// Pure-logic unit tests for app/app-logic.js, run with Node's built-in test runner.
// These cover the error-prone decision/math extracted from the inline SPA JS:
//   - staticLoadMethod : how to load the static fallback given the page protocol
//   - isStaticFallback : whether the cached-data banner should show
//   - nextFocusTarget  : the dialog focus-trap wrap-around math
// The DOM glue that calls these (fetch / classList / focus) stays inline in
// app/index.html and is inspection-verified; this file pins the logic they rely on.
const { test } = require('node:test');
const assert = require('node:assert');
const AppLogic = require('../../app/app-logic.js');

// ---- staticLoadMethod ----
test('staticLoadMethod: file: protocol loads via <script> (fetch is blocked on file://)', () => {
  assert.equal(AppLogic.staticLoadMethod('file:'), 'script');
});

test('staticLoadMethod: http(s) protocols load via fetch', () => {
  assert.equal(AppLogic.staticLoadMethod('http:'), 'fetch');
  assert.equal(AppLogic.staticLoadMethod('https:'), 'fetch');
});

test('staticLoadMethod: unknown/missing protocol defaults to fetch', () => {
  assert.equal(AppLogic.staticLoadMethod(undefined), 'fetch');
  assert.equal(AppLogic.staticLoadMethod(''), 'fetch');
});

// ---- isStaticFallback ----
test('isStaticFallback: true when the live API did not succeed', () => {
  assert.equal(AppLogic.isStaticFallback({ apiOk: false }), true);
});

test('isStaticFallback: false when the live API succeeded', () => {
  assert.equal(AppLogic.isStaticFallback({ apiOk: true }), false);
});

test('isStaticFallback: missing apiOk is treated as not-ok (show banner)', () => {
  assert.equal(AppLogic.isStaticFallback({}), true);
});

// ---- nextFocusTarget ----
test('nextFocusTarget: Tab moves forward one', () => {
  assert.equal(AppLogic.nextFocusTarget(['a', 'b', 'c'], 0, false), 1);
  assert.equal(AppLogic.nextFocusTarget(['a', 'b', 'c'], 1, false), 2);
});

test('nextFocusTarget: Tab at last wraps to first', () => {
  assert.equal(AppLogic.nextFocusTarget(['a', 'b', 'c'], 2, false), 0);
});

test('nextFocusTarget: Shift+Tab moves backward one', () => {
  assert.equal(AppLogic.nextFocusTarget(['a', 'b', 'c'], 2, true), 1);
  assert.equal(AppLogic.nextFocusTarget(['a', 'b', 'c'], 1, true), 0);
});

test('nextFocusTarget: Shift+Tab at first wraps to last', () => {
  assert.equal(AppLogic.nextFocusTarget(['a', 'b', 'c'], 0, true), 2);
});

test('nextFocusTarget: no focusable elements returns -1', () => {
  assert.equal(AppLogic.nextFocusTarget([], 0, false), -1);
  assert.equal(AppLogic.nextFocusTarget([], -1, true), -1);
});

test('nextFocusTarget: single element stays on itself', () => {
  assert.equal(AppLogic.nextFocusTarget(['a'], 0, false), 0);
  assert.equal(AppLogic.nextFocusTarget(['a'], 0, true), 0);
});

test('nextFocusTarget: nothing focused yet -> Tab starts at first, Shift+Tab at last', () => {
  assert.equal(AppLogic.nextFocusTarget(['a', 'b', 'c'], -1, false), 0);
  assert.equal(AppLogic.nextFocusTarget(['a', 'b', 'c'], -1, true), 2);
});