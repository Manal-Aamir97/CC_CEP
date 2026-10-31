/* Sends every form change to the server's event log (build plan Step 6).
 *
 * form.js fires "configchange" on every edit with { path, old, new, config }. This script batches
 * those events and posts them about once a second, on submit (form.js calls cepEvents.flush()),
 * and when the page is left (sendBeacon). Nothing is sent in researcher preview.
 */
(function () {
  "use strict";

  var form = document.getElementById("config-form");
  if (!form || form.dataset.preview === "true" || !form.dataset.eventsUrl) return;

  var url = form.dataset.eventsUrl;
  var queue = [];
  var timer = null;

  function send(leaving) {
    if (timer) { clearTimeout(timer); timer = null; }
    if (!queue.length) return Promise.resolve();
    var body = JSON.stringify({ events: queue.splice(0, queue.length) });
    if (leaving && navigator.sendBeacon) {
      navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
      return Promise.resolve();
    }
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      keepalive: true,
      body: body
    }).catch(function () { /* a lost batch must not block the participant */ });
  }

  function later() {
    if (!timer) timer = setTimeout(function () { timer = null; send(false); }, 1000);
  }

  document.addEventListener("configchange", function (event) {
    var d = event.detail;
    queue.push({ type: "field_change", path: d.path, old: d.old, new: d.new,
                 at: new Date().toISOString(), config: d.config });
    later();
  });

  window.addEventListener("pagehide", function () { send(true); });

  window.cepEvents = { flush: function () { return send(false); } };

  if (window.cepForm && window.cepForm.restored) {
    queue.push({ type: "draft_restored", at: new Date().toISOString(), config: window.cepForm.getConfig() });
    later();
  }
})();
