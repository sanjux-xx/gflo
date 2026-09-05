/* CSRF: the server sets a gflo_csrf cookie; every write must echo it back. */
function csrfToken() {
  var m = document.cookie.match(/(?:^|;\s*)gflo_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

/* ============================================================================
   G-FLO Admin Console — shared behaviour
   Toasts · inline cell editing · bulk selection bar · sidebar drawer ·
   unsaved-changes guard · password reveal · keyboard shortcuts
   ========================================================================== */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ icons */
  const ICON = {
    ok: '<path d="M20 6L9 17l-5-5"/>',
    err: '<path d="M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
  };
  function svg(kind, size) {
    return '<svg class="ic" width="' + (size || 17) + '" height="' + (size || 17) + '" viewBox="0 0 24 24" ' +
      'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      (ICON[kind] || ICON.info) + '</svg>';
  }

  /* ----------------------------------------------------------------- toasts */
  const host = document.getElementById("toasts");
  function toast(message, kind, ms) {
    if (!host) return;
    const el = document.createElement("div");
    el.className = "toast " + (kind || "ok");
    el.innerHTML = svg(kind === "err" ? "err" : kind === "info" ? "info" : "ok") +
      "<span>" + String(message).replace(/</g, "&lt;") + "</span>";
    host.appendChild(el);
    const life = ms || (kind === "err" ? 6000 : 2800);
    setTimeout(function () {
      el.classList.add("out");
      setTimeout(function () { el.remove(); }, 220);
    }, life);
  }
  window.gfloToast = toast;

  /* --------------------------------------------------------- sidebar drawer */
  const side = document.getElementById("side");
  const scrim = document.getElementById("scrim");
  const burger = document.getElementById("burger");
  function closeSide() { side && side.classList.remove("open"); scrim && scrim.classList.remove("on"); }
  if (burger) burger.addEventListener("click", function () {
    side.classList.toggle("open");
    scrim.classList.toggle("on", side.classList.contains("open"));
  });
  if (scrim) scrim.addEventListener("click", closeSide);

  /* ------------------------------------------------------ keyboard shortcuts */
  document.addEventListener("keydown", function (e) {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || "")) || e.target.isContentEditable;
    if (e.key === "/" && !typing) {
      const q = document.getElementById("gq");
      if (q) { e.preventDefault(); q.focus(); q.select(); }
    }
    if (e.key === "Escape") closeSide();
  });

  /* ------------------------------------------------------- password reveal */
  document.querySelectorAll("[data-pwd-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const input = document.getElementById(btn.getAttribute("data-pwd-toggle"));
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      btn.querySelectorAll("svg").forEach(function (s, i) { s.style.display = (i === (show ? 1 : 0)) ? "" : "none"; });
      input.focus();
    });
  });

  /* caps-lock hint on password fields */
  document.querySelectorAll("[data-caps]").forEach(function (input) {
    const hint = document.getElementById(input.getAttribute("data-caps"));
    if (!hint) return;
    ["keyup", "keydown"].forEach(function (evt) {
      input.addEventListener(evt, function (e) {
        if (typeof e.getModifierState !== "function") return;
        hint.classList.toggle("on", e.getModifierState("CapsLock"));
      });
    });
  });

  /* ------------------------------------------ submit buttons show a spinner */
  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    form.addEventListener("submit", function () {
      const btn = form.querySelector('[type="submit"]');
      if (btn && !btn.classList.contains("loading")) {
        btn.classList.add("loading");
        setTimeout(function () { btn.classList.remove("loading"); }, 8000);
      }
    });
  });

  /* --------------------------------------------------- inline cell editing */
  async function saveCell(id, field, value, el) {
    el.classList.remove("saved", "failed");
    el.classList.add("saving");
    try {
      const res = await fetch("/admin/products/inline", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
        body: JSON.stringify({ id: id, field: field, value: value }),
      });
      const data = await res.json().catch(function () { return {}; });
      if (!res.ok || !data.ok) throw new Error(data.error || ("Save failed (" + res.status + ")"));
      el.classList.remove("saving");
      el.classList.add("saved");
      setTimeout(function () { el.classList.remove("saved"); }, 1400);
      return data;
    } catch (err) {
      el.classList.remove("saving");
      el.classList.add("failed");
      toast(err.message, "err");
      throw err;
    }
  }

  document.querySelectorAll(".ce").forEach(function (input) {
    let original = input.value;
    input.addEventListener("focus", function () { original = input.value; input.select(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
      if (e.key === "Escape") { input.value = original; input.blur(); }
      /* arrow down / up moves to the same column in the next row — spreadsheet feel */
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        const cells = Array.prototype.slice.call(
          document.querySelectorAll('.ce[data-field="' + input.dataset.field + '"]'));
        const i = cells.indexOf(input);
        const next = cells[i + (e.key === "ArrowDown" ? 1 : -1)];
        if (next) { e.preventDefault(); next.focus(); }
      }
    });
    input.addEventListener("blur", function () {
      const value = input.value.trim();
      if (value === original.trim()) return;
      const label = input.dataset.name || "Product";
      saveCell(input.dataset.id, input.dataset.field, value, input).then(function () {
        const what = input.dataset.field === "stock" ? "Stock" : input.dataset.field.toUpperCase();
        const shown = value === "" && input.dataset.field !== "stock" ? "price on request" : value;
        toast(label + " — " + what + " set to " + shown);
        original = input.value;
      }).catch(function () { input.value = original; });
    });
  });

  document.querySelectorAll(".vis-toggle").forEach(function (box) {
    box.addEventListener("change", function () {
      saveCell(box.dataset.id, "visible", box.checked, box).then(function () {
        toast((box.dataset.name || "Product") + (box.checked ? " is now visible" : " hidden from the store"),
          box.checked ? "ok" : "info");
      }).catch(function () { box.checked = !box.checked; });
    });
  });

  /* ------------------------------------------------------- bulk action bar */
  const bulkForm = document.getElementById("bulkform");
  if (bulkForm) {
    const boxes = Array.prototype.slice.call(bulkForm.querySelectorAll(".rowck"));
    const all = document.getElementById("ckall");
    const bar = document.getElementById("bulkbar");
    const count = document.getElementById("bulkn");
    const action = document.getElementById("bulkaction");
    const amount = document.getElementById("bulkamount");
    const catSel = document.getElementById("bulkcat");

    function refresh() {
      const picked = boxes.filter(function (b) { return b.checked; });
      if (count) count.innerHTML = "<span>" + picked.length + "</span> selected";
      if (bar) bar.classList.toggle("up", picked.length > 0);
      boxes.forEach(function (b) {
        const row = b.closest("tr");
        if (row) row.classList.toggle("sel", b.checked);
      });
      if (all) {
        all.checked = picked.length === boxes.length && boxes.length > 0;
        all.indeterminate = picked.length > 0 && picked.length < boxes.length;
      }
    }
    boxes.forEach(function (b) { b.addEventListener("change", refresh); });
    if (all) all.addEventListener("change", function () {
      boxes.forEach(function (b) { b.checked = all.checked; });
      refresh();
    });
    const clear = document.getElementById("bulkclear");
    if (clear) clear.addEventListener("click", function () {
      boxes.forEach(function (b) { b.checked = false; });
      refresh();
    });

    if (action) action.addEventListener("change", function () {
      const needsAmount = ["price_pct", "price_flat", "set_stock"].indexOf(action.value) >= 0;
      if (amount) {
        amount.style.display = needsAmount ? "" : "none";
        amount.placeholder = action.value === "price_pct" ? "% e.g. -5"
          : action.value === "set_stock" ? "Qty e.g. 50" : "₹ e.g. 10";
      }
      if (catSel) catSel.style.display = action.value === "move" ? "" : "none";
    });

    bulkForm.addEventListener("submit", function (e) {
      if (bulkForm.dataset.skipConfirm === "1") { bulkForm.dataset.skipConfirm = ""; return; }
      const picked = boxes.filter(function (b) { return b.checked; }).length;
      if (!action || !action.value) { e.preventDefault(); toast("Choose a bulk action first", "info"); return; }
      if (!picked) { e.preventDefault(); toast("Tick at least one product", "info"); return; }
      const label = action.options[action.selectedIndex].text;
      const question = action.value === "delete"
        ? "Delete " + picked + " product(s) permanently? This can't be undone."
        : 'Apply "' + label + '" to ' + picked + " product(s)?";
      if (!window.confirm(question)) e.preventDefault();
    });
    refresh();
  }

  /* row-level buttons (copy / delete) submit the same form — skip bulk confirm */
  document.querySelectorAll("[data-row-action]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      const form = btn.form;
      const kind = btn.getAttribute("data-row-action");
      if (kind === "delete" && !window.confirm('Delete "' + (btn.dataset.name || "this product") + '" permanently?')) {
        e.preventDefault();
        return;
      }
      if (form) form.dataset.skipConfirm = "1";
    });
  });

  /* ------------------------------------------- unsaved changes guard (forms) */
  document.querySelectorAll("form[data-guard]").forEach(function (form) {
    const status = form.querySelector("[data-dirty-note]");
    let dirty = false;
    const mark = function () {
      if (dirty) return;
      dirty = true;
      if (status) { status.textContent = "Unsaved changes"; status.classList.add("dirty"); }
    };
    form.addEventListener("input", mark);
    form.addEventListener("change", mark);
    form.addEventListener("submit", function () { dirty = false; });
    window.addEventListener("beforeunload", function (e) {
      if (!dirty) return;
      e.preventDefault();
      e.returnValue = "";
    });
    /* Cmd/Ctrl+S saves */
    form.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        form.requestSubmit ? form.requestSubmit() : form.submit();
      }
    });
  });

  /* --------------------------------------------------------- photo dropzone */
  const drop = document.getElementById("drop");
  const files = document.getElementById("files");
  if (drop && files) {
    const list = document.getElementById("filelist");
    const show = function () {
      if (!list) return;
      list.textContent = files.files.length
        ? files.files.length + " file(s) ready: " + Array.prototype.map.call(files.files, function (f) { return f.name; }).join(", ")
        : "";
    };
    drop.addEventListener("click", function () { files.click(); });
    drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); files.click(); } });
    files.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (evt) {
      drop.addEventListener(evt, function (e) { e.preventDefault(); drop.classList.add("over"); });
    });
    ["dragleave", "drop"].forEach(function (evt) {
      drop.addEventListener(evt, function (e) { e.preventDefault(); drop.classList.remove("over"); });
    });
    drop.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) { files.files = e.dataTransfer.files; show(); }
    });
  }

  /* --------------------------------- surface server flash messages as toasts */
  const params = new URLSearchParams(location.search);
  if (params.get("toast")) toast(params.get("toast"), params.get("toastkind") || "ok");

  /* ------------------------------------------------------------------ CSP-safe
     These behaviours used to live in inline <script> blocks and inline on*
     attributes. The admin sends a strict Content-Security-Policy
     (script-src 'self'), which blocks inline JS outright — so they run from
     here instead. Do NOT move them back into the templates.                 */

  /* confirm-before-submit: <form data-confirm="Are you sure?"> */
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    });
  });

  /* rows-per-page selector: <select data-perpage-base="/admin/products?..."> */
  document.querySelectorAll("[data-perpage-base]").forEach(function (sel) {
    sel.addEventListener("change", function () {
      location.href = sel.getAttribute("data-perpage-base") + sel.value;
    });
  });

  /* broken product thumbnail -> grey placeholder: <img data-thumb-fallback> */
  document.querySelectorAll("img[data-thumb-fallback]").forEach(function (img) {
    img.addEventListener("error", function () {
      var ph = document.createElement("div");
      ph.className = "thumb-none";
      if (img.parentNode) img.parentNode.replaceChild(ph, img);
    });
  });

  /* Categories page: "Edit" loads the row into the add/update form */
  document.querySelectorAll("[data-edit-cat]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var d = btn.dataset, set = function (id, v) {
        var el = document.getElementById(id); if (el) el.value = v == null ? "" : v;
      };
      var title = document.getElementById("catformtitle");
      if (title) title.textContent = "Update “" + d.name + "”";
      set("c-name", d.name); set("c-id", d.id); set("c-desc", d.desc);
      set("c-img", d.img); set("c-code", d.code); set("c-order", d.order);
      set("c-hue", d.hue);
      var pop = document.getElementById("c-popular");
      if (pop) pop.checked = d.popular === "1";
      var form = document.getElementById("catform");
      if (form) form.scrollIntoView({ behavior: "smooth", block: "center" });
      if (window.gfloToast) window.gfloToast("Editing " + d.name + " — change what you need and save", "info");
    });
  });
  var catReset = document.getElementById("catreset");
  if (catReset) catReset.addEventListener("click", function () {
    var t = document.getElementById("catformtitle");
    if (t) t.textContent = "Add a category";
  });

  /* Brands page: same pattern */
  document.querySelectorAll("[data-edit-brand]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var d = btn.dataset, set = function (id, v) {
        var el = document.getElementById(id); if (el) el.value = v == null ? "" : v;
      };
      var title = document.getElementById("brandformtitle");
      if (title) title.textContent = "Update “" + d.name + "”";
      set("b-name", d.name); set("b-id", d.id); set("b-hue", d.hue); set("b-order", d.order);
      var form = document.getElementById("brandform");
      if (form) form.scrollIntoView({ behavior: "smooth", block: "center" });
      if (window.gfloToast) window.gfloToast("Editing " + d.name, "info");
    });
  });
  var brandReset = document.getElementById("brandreset");
  if (brandReset) brandReset.addEventListener("click", function () {
    var t = document.getElementById("brandformtitle");
    if (t) t.textContent = "Add a brand";
  });

})();
