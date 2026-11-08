/* planlens-agent inspector. Presentation only: it highlights what a recorded
   finding points at. It never re-evaluates the plan and has no network access. */
(function () {
  "use strict";
  var node = document.getElementById("planlens-view");
  if (!node) { return; }
  var view = JSON.parse(node.textContent);
  var byId = {};
  view.diagnostics.forEach(function (d) { byId[d.id] = d; });

  var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-finding-select]"));
  var live = document.getElementById("selection-status");
  document.documentElement.classList.add("js");

  function clearFocus() {
    document.body.classList.remove("has-selection");
    document.querySelectorAll(".is-focus").forEach(function (el) { el.classList.remove("is-focus"); });
    document.querySelectorAll(".is-producer").forEach(function (el) { el.classList.remove("is-producer"); });
    document.querySelectorAll(".finding.is-selected").forEach(function (el) { el.classList.remove("is-selected"); });
    buttons.forEach(function (b) { b.setAttribute("aria-pressed", "false"); });
  }

  function mark(selector) {
    document.querySelectorAll(selector).forEach(function (el) { el.classList.add("is-focus"); });
  }

  function select(id, announce) {
    clearFocus();
    var d = byId[id];
    if (!d) { return; }
    var f = d.focus;
    document.body.classList.add("has-selection");
    var card = document.getElementById("finding-" + id);
    if (card) { card.classList.add("is-selected"); }
    var btn = document.querySelector('[data-finding-select="' + id + '"]');
    if (btn) { btn.setAttribute("aria-pressed", "true"); }
    f.steps.forEach(function (i) { mark('[data-step="' + i + '"]'); });
    f.producer_steps.forEach(function (i) {
      document.querySelectorAll('[data-step="' + i + '"]').forEach(function (el) {
        el.classList.add("is-producer");
      });
    });
    f.artifact_keys.forEach(function (k) { mark('[data-artifact="' + CSS.escape(k) + '"]'); });
    if (f.prerequisite_rule) { mark('[data-prereq="' + CSS.escape(f.prerequisite_rule) + '"]'); }
    if (f.same_artifact_rule) { mark('[data-same="' + CSS.escape(f.same_artifact_rule) + '"]'); }
    if (f.required_action) { mark('[data-required="' + CSS.escape(f.required_action) + '"]'); }
    if (announce && live) {
      var where = f.steps.length ? "steps " + f.steps.join(", ") : "no plan step";
      live.textContent = "Selected " + d.rule_id + ", highlighting " + where + ".";
    }
  }

  buttons.forEach(function (b, n) {
    b.addEventListener("click", function () {
      if (b.getAttribute("aria-pressed") === "true") { clearFocus(); if (live) { live.textContent = "Selection cleared."; } }
      else { select(b.getAttribute("data-finding-select"), true); }
    });
    b.addEventListener("keydown", function (e) {
      var next = null;
      if (e.key === "ArrowDown") { next = buttons[Math.min(n + 1, buttons.length - 1)]; }
      else if (e.key === "ArrowUp") { next = buttons[Math.max(n - 1, 0)]; }
      else if (e.key === "Home") { next = buttons[0]; }
      else if (e.key === "End") { next = buttons[buttons.length - 1]; }
      if (next) {
        e.preventDefault();
        next.focus();
        select(next.getAttribute("data-finding-select"), true);
      }
    });
  });

  document.querySelectorAll("[data-goto-finding]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      var id = a.getAttribute("data-goto-finding");
      var btn = document.querySelector('[data-finding-select="' + id + '"]');
      if (btn) { e.preventDefault(); btn.focus(); select(id, true); btn.scrollIntoView({ block: "nearest" }); }
    });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && document.body.classList.contains("has-selection")) {
      clearFocus();
      if (live) { live.textContent = "Selection cleared."; }
    }
  });

  if (buttons.length) { select(buttons[0].getAttribute("data-finding-select"), false); }
})();
