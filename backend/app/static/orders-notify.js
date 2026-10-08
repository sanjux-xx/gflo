/* New-order alerts for the admin console.
   Checks /admin/orders/feed every 20 s while any admin page is open (even in a
   background tab): updates the sidebar badge and tab title, plays a short chime
   and shows a desktop notification when a new order arrives. */
(function () {
  "use strict";
  Array.prototype.forEach.call(document.querySelectorAll("[data-print]"), function (b) {
    b.addEventListener("click", function () { window.print(); });
  });
  var KEY = "gflo_last_order_id", POLL = 20000;
  var badge = document.getElementById("ord-badge");
  var baseTitle = document.title.replace(/^\(\d+\)\s*/, "");
  var btn = document.getElementById("notify-enable"), state = document.getElementById("notify-state");
  var canNotify = "Notification" in window;

  function showPermUI() {
    if (!btn || !state) return;
    if (!canNotify) { state.textContent = "This browser can't show alerts."; return; }
    if (Notification.permission === "granted") { btn.hidden = true; state.textContent = "🔔 Order alerts are on"; }
    else if (Notification.permission === "denied") { btn.hidden = true; state.textContent = "Alerts blocked — allow notifications for this site in browser settings."; }
    else { btn.hidden = false; state.textContent = ""; }
  }
  if (btn) btn.addEventListener("click", function () {
    if (!canNotify) return;
    Notification.requestPermission().then(showPermUI);
    chime();                                   // unlocks audio after a click
  });
  showPermUI();

  var ctx = null;
  function chime() {
    try {
      ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
      [880, 1320].forEach(function (f, i) {
        var o = ctx.createOscillator(), g = ctx.createGain();
        o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
        var t = ctx.currentTime + i * 0.18;
        g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.3);
        o.start(t); o.stop(t + 0.32);
      });
    } catch (e) {}
  }

  function render(unseen) {
    if (badge) { badge.textContent = unseen; badge.hidden = !unseen; }
    document.title = (unseen ? "(" + unseen + ") " : "") + baseTitle;
  }

  function check() {
    fetch("/admin/orders/feed", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.ok) return;
        render(d.unseen || 0);
        var latest = d.latest;
        if (!latest) return;
        var last = parseInt(localStorage.getItem(KEY) || "0", 10);
        if (!last) { localStorage.setItem(KEY, latest.id); return; }      // first visit: no alert
        if (latest.id > last) {
          localStorage.setItem(KEY, latest.id);
          chime();
          if (canNotify && Notification.permission === "granted") {
            var n = new Notification("🛒 New order " + latest.number, {
              body: latest.name + " · ₹" + Math.round(latest.total).toLocaleString("en-IN"),
              tag: "gflo-order-" + latest.id
            });
            n.onclick = function () { window.focus(); location.href = "/admin/orders/" + latest.id; };
          }
          if (/^(\/admin)?\/orders$/.test(location.pathname.replace(/\/$/, ""))) setTimeout(function () { location.reload(); }, 1200);
        }
      }).catch(function () {});
  }
  check();
  setInterval(check, POLL);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) check(); });
})();
