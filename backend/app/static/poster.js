/* G-FLO festival poster & promo strip.
   Reads /api/posters (managed at /admin/posters) and shows:
     - a popup poster when a visitor opens the site
     - an optional slim banner strip above the header
   Nothing at all happens if no poster is live or the API can't be reached. */
(function () {
  "use strict";
  var BASE = (window.GFLO_API_BASE || "").replace(/\/$/, "");
  var MOBILE = "(max-width: 640px)";
  var DAY = 864e5;

  function store(kind) { try { var s = window[kind]; s.setItem("_t", "1"); s.removeItem("_t"); return s; } catch (e) { return null; } }
  var LS = store("localStorage"), SS = store("sessionStorage");

  function abs(u) { return u && u.charAt(0) === "/" && u.charAt(1) !== "/" ? BASE + u : u; }
  function el(tag, cls, attrs) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    for (var k in (attrs || {})) n.setAttribute(k, attrs[k]);
    return n;
  }
  function safeLink(u) { return /^(\/(?!\/)|https?:\/\/|tel:|mailto:)/i.test(u || "") ? u : ""; }
  function external(u) { return /^https?:\/\//i.test(u) && u.indexOf(location.origin) !== 0; }

  /* same-site links use the shop's own router so the page doesn't reload */
  function go(u, e) {
    if (!u) return;
    if (u.charAt(0) === "/" && typeof Router !== "undefined" && Router && typeof Router.push === "function") {
      if (e) e.preventDefault();
      Router.push(u);
    }
  }

  function picture(p, cls) {
    var pic = el("picture");
    if (p.imgMobile) pic.appendChild(el("source", "", { media: MOBILE, srcset: abs(p.imgMobile) }));
    var img = el("img", cls, { src: abs(p.img), alt: p.title || "", decoding: "async" });
    pic.appendChild(img);
    return { pic: pic, img: img };
  }

  function linkWrap(p, child, onGo) {
    var href = safeLink(p.link);
    if (!href) return child;
    var a = el("a", "gp-link", { href: abs(href), "aria-label": p.title || "View offer" });
    if (external(href)) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
    a.addEventListener("click", function (e) { if (onGo) onGo(); go(href, e); });
    a.appendChild(child);
    return a;
  }

  /* ---------- popup: how often a visitor sees it ---------- */
  function seenKey(p) { return "gflo_poster_" + p.v; }
  function shouldShow(p) {
    var k = seenKey(p);
    if (p.freq === "visit") return !(SS && SS.getItem(k));
    if (!LS) return !(SS && SS.getItem(k));
    var t = +LS.getItem(k) || 0;
    if (!t) return true;
    return p.freq === "daily" ? (Date.now() - t) > DAY : false;
  }
  function markSeen(p) {
    var k = seenKey(p);
    try { if (SS) SS.setItem(k, "1"); if (LS && p.freq !== "visit") LS.setItem(k, String(Date.now())); } catch (e) {}
  }

  function showPopup(p) {
    if (!p || !p.img || !shouldShow(p)) return;
    if (/^\/admin/.test(location.pathname)) return;

    var root = el("div", "gp-ovl", { role: "dialog", "aria-modal": "true", "aria-label": p.title || "Offer" });
    var box = el("div", "gp-box");
    var close = el("button", "gp-x", { type: "button", "aria-label": "Close" });
    close.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>';
    var im = picture(p, "gp-img");
    var lastFocus = document.activeElement;

    function shut() {
      markSeen(p);
      root.classList.remove("on");
      document.documentElement.classList.remove("gp-lock");
      document.removeEventListener("keydown", onKey);
      setTimeout(function () { if (root.parentNode) root.parentNode.removeChild(root); }, 260);
      try { if (lastFocus && lastFocus.focus) lastFocus.focus(); } catch (e) {}
    }
    function onKey(e) {
      if (e.key === "Escape") shut();
      if (e.key === "Tab") {               // keep focus inside the dialog
        var f = box.querySelectorAll("a[href],button");
        if (!f.length) return;
        var first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }

    box.appendChild(close);
    box.appendChild(linkWrap(p, im.pic, shut));
    var href = safeLink(p.link);
    if (p.cta && href) {
      var cta = el("a", "gp-cta", { href: abs(href) });
      cta.textContent = p.cta;
      if (external(href)) { cta.target = "_blank"; cta.rel = "noopener noreferrer"; }
      cta.addEventListener("click", function (e) { shut(); go(href, e); });
      box.appendChild(cta);
    }
    root.appendChild(box);
    close.addEventListener("click", shut);
    root.addEventListener("click", function (e) { if (e.target === root) shut(); });

    // only open once the image has actually loaded — never an empty frame
    function open() {
      document.body.appendChild(root);
      document.documentElement.classList.add("gp-lock");
      document.addEventListener("keydown", onKey);
      requestAnimationFrame(function () { requestAnimationFrame(function () { root.classList.add("on"); close.focus(); }); });
      markSeen(p);
    }
    var pre = new Image();
    pre.onload = function () { setTimeout(open, 700); };
    pre.src = (p.imgMobile && window.matchMedia && matchMedia(MOBILE).matches) ? abs(p.imgMobile) : abs(p.img);
  }

  /* ---------- strip above the header ---------- */
  function showStrip(p) {
    if (!p || !p.img) return;
    var k = "gflo_strip_closed_" + p.v;
    if (SS && SS.getItem(k)) return;
    var bar = el("div", "gp-strip", { role: "region", "aria-label": p.title || "Offer" });
    var im = picture(p, "gp-strip-img");
    var x = el("button", "gp-strip-x", { type: "button", "aria-label": "Hide banner" });
    x.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>';
    x.addEventListener("click", function () {
      try { if (SS) SS.setItem(k, "1"); } catch (e) {}
      if (bar.parentNode) bar.parentNode.removeChild(bar);
    });
    bar.appendChild(linkWrap(p, im.pic));
    bar.appendChild(x);
    im.img.addEventListener("load", function () { bar.classList.add("on"); });
    var hdr = document.getElementById("hdr");
    document.body.insertBefore(bar, hdr || document.body.firstChild);
  }

  function start() {
    if (!window.fetch) return;
    fetch(BASE + "/api/posters", { credentials: "omit", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        try { showStrip(d.strip); } catch (e) { console.warn("[G-FLO] strip:", e.message); }
        try { showPopup(d.popup); } catch (e) { console.warn("[G-FLO] poster:", e.message); }
      })
      .catch(function () { /* no poster — the shop works exactly as before */ });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
