// app-logic.js — pure helpers extracted from the SPA's inline JS (app/index.html).
//
// The DOM glue (fetch / classList / focus) stays inline in index.html and is
// inspection-verified; this file holds the error-prone decision + math logic so
// it can be unit-tested with Node's built-in test runner (test/js/).
//
// Dual-scope: usable as a browser global (window.AppLogic) and as a Node CommonJS
// module (require('../../app/app-logic.js')) — no dependencies, stdlib-only.
var AppLogic = {

  // How to load the static fallback dataset when the live API is unavailable.
  // Browsers block fetch() on the file: protocol, so when the page is opened
  // directly from disk we must load a <script>-loadable wrapper (data.js) instead
  // of fetch('data.json'). On http(s):, fetch('data.json') works fine.
  staticLoadMethod: function (protocol) {
    return protocol === 'file:' ? 'script' : 'fetch';
  },

  // Whether the cached-data banner should be shown — i.e. whether the app is
  // serving the static fallback instead of the live API. Missing apiOk is
  // treated as "not ok" so the banner errs toward showing.
  isStaticFallback: function (opts) {
    return !opts || !opts.apiOk;
  },

  // Dialog focus-trap math. Given the list of focusable elements in the dialog,
  // the index currently holding focus (-1 if none), and whether this is a
  // Shift+Tab (backward), return the index that should receive focus next.
  // Wraps last->first on Tab and first->last on Shift+Tab. Returns -1 when there
  // is nothing to focus; a single-element list stays on itself.
  nextFocusTarget: function (focusables, currentIndex, shift) {
    var n = focusables.length;
    if (n === 0) return -1;
    if (currentIndex < 0) return shift ? n - 1 : 0;
    if (n === 1) return 0;
    if (shift) return (currentIndex - 1 + n) % n;
    return (currentIndex + 1) % n;
  }

};

if (typeof module !== 'undefined' && module.exports) module.exports = AppLogic;
if (typeof window !== 'undefined') window.AppLogic = AppLogic;