// landing-logic.js — pure helpers extracted from the landing page's inline JS
// (landing-page/index.html).
//
// The DOM glue (fetch / textContent / localStorage) stays inline in the page
// and is inspection-verified; this file holds the error-prone decision +
// partitioning logic so it can be unit-tested with Node's built-in test runner
// (test/js/test-landing-logic.test.js).
//
// Dual-scope: usable as a browser global (window.LandingLogic) and as a Node
// CommonJS module (require('../../landing-page/landing-logic.js')) — no deps.
var LandingLogic = {

  // M5: map the /api/summary response onto the 4 landing metric cards.
  //   byStatus = response.by_status   ({completed: N, delayed: N, ...})
  //   dataset  = response.dataset     ({tracked: N, scored: N, ...})
  // Missing keys (e.g. no 'cancelled' status exists this month) read as 0, so a
  // status disappearing never renders "undefined" on a card. Null inputs (API
  // returned nothing usable) default everything to 0 — the caller then keeps the
  // hardcoded fallback values instead of overwriting them with 0.
  parseSummaryMetrics: function (byStatus, dataset) {
    var bs = byStatus || {};
    var ds = dataset || {};
    return {
      tracked:    Number(ds.tracked)    || 0,
      completed:  Number(bs.completed)  || 0,
      delayed:    Number(bs.delayed)    || 0,
      cancelled:  Number(bs.cancelled)  || 0
    };
  },

  // M6: drain the localStorage lead queue — re-post each queued lead to the API
  // so leads captured while the server was unreachable become operator-visible.
  // postFn(lead) must return a Promise that resolves truthy on success; a
  // rejected or falsy promise counts as a failure and the lead is kept for the
  // next retry. Returns {sent, remaining}. An empty/null queue is a no-op
  // (postFn is never called) so a healthy queue costs nothing on every load.
  drainLeadQueue: async function (queue, postFn) {
    if (!queue || queue.length === 0) return { sent: 0, remaining: [] };
    var sent = 0;
    var remaining = [];
    for (var i = 0; i < queue.length; i++) {
      var ok = false;
      try { ok = await postFn(queue[i]); } catch (e) { ok = false; }
      if (ok) { sent++; } else { remaining.push(queue[i]); }
    }
    return { sent: sent, remaining: remaining };
  }

};

if (typeof module !== 'undefined' && module.exports) module.exports = LandingLogic;
if (typeof window !== 'undefined') window.LandingLogic = LandingLogic;