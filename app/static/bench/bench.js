// Bench call page: the panes behind the tabs, picked by the address's #name so a pane can be
// linked to, and Retell's side of the transcript read the first time its pane opens.
(function () {
  "use strict";
  var tabs = document.querySelector("[data-panes]");
  if (!tabs) { return; }
  var links = tabs.querySelectorAll('[role="tab"]');
  var panes = Array.prototype.map.call(links, function (a) { return document.getElementById(a.getAttribute("aria-controls")); });
  function paneFor(hash) {
    var target = hash && document.getElementById(hash.slice(1));
    if (!target) { return null; }
    for (var i = 0; i < panes.length; i++) {
      if (panes[i] === target || panes[i].contains(target)) { return panes[i]; }
    }
    return null;
  }
  function show(pane) {
    Array.prototype.forEach.call(links, function (a, i) {
      var on = panes[i] === pane;
      a.setAttribute("aria-selected", on ? "true" : "false");
      panes[i].hidden = !on;
    });
    if (pane.dataset.heard && !pane.dataset.read) {
      pane.dataset.read = "1";
      var slot = pane.querySelector("[data-heard-slot]");
      fetch(pane.dataset.heard, { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) { if (html !== null) { slot.innerHTML = html; } });
    }
  }
  Array.prototype.forEach.call(links, function (a, i) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      history.replaceState(null, "", a.getAttribute("href"));
      show(panes[i]);
    });
  });
  window.addEventListener("hashchange", function () { var p = paneFor(location.hash); if (p) { show(p); } });
  show(paneFor(location.hash) || panes[0]);
})();

// Cost: the tested stacks as bars. A measure and a group are picked with the chips; a name
// toggles its own bar. Bars scale to the largest value shown, so leaving one out rescales the rest.
document.querySelectorAll("[data-chart]").forEach((chart) => {
  const rows = [...chart.querySelectorAll("[data-row]")];
  const picked = (attr) => chart.querySelector("[" + attr + "][aria-current='true']");
  const field = (prefix, key) => prefix + key.charAt(0).toUpperCase() + key.slice(1);
  const isOn = (row) => row.querySelector(".bar-name").getAttribute("aria-pressed") === "true";
  const show = () => {
    const measure = picked("data-measure");
    const key = measure.dataset.measure;
    const unit = measure.dataset.unit;
    const group = picked("data-group").dataset.group;
    rows.forEach((row) => { row.hidden = row.dataset.in !== group; });
    const shown = rows.filter((row) => row.dataset.in === group);
    const values = shown.filter(isOn).map((row) => Number(row.dataset[field("v", key)] || 0));
    const top = Math.max(0.001, ...values);
    shown.forEach((row) => {
      const raw = row.dataset[field("v", key)];
      const bar = row.querySelector(".hbar");
      const label = row.querySelector(".bar-value");
      const off = !isOn(row);
      row.classList.toggle("off", off);
      if (raw === undefined) {
        bar.style.width = "0";
        label.textContent = "-";
        bar.dataset.tip = "Not measured";
        return;
      }
      const v = Number(raw);
      bar.style.width = off ? "0" : (v / top) * 100 + "%";
      label.textContent = unit === "$" ? "$" + (v < 0.01 ? v.toFixed(4) : v.toFixed(3)) : v.toFixed(2) + " s";
      bar.dataset.tip = row.dataset[field("src", key)];
    });
  };
  chart.querySelectorAll("[data-measure], [data-group]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const attr = chip.hasAttribute("data-measure") ? "data-measure" : "data-group";
      chart.querySelectorAll("[" + attr + "]").forEach((c) => c.setAttribute("aria-current", String(c === chip)));
      show();
    });
  });
  rows.forEach((row) => {
    const name = row.querySelector(".bar-name");
    name.addEventListener("click", () => {
      name.setAttribute("aria-pressed", String(!isOn(row)));
      show();
    });
  });
  show();
});

// Transcript: a tool call's result is read when the call is first opened, not with the page.
document.addEventListener("toggle", (event) => {
  const details = event.target;
  if (!(details instanceof HTMLDetailsElement) || !details.open) return;
  const slot = details.querySelector("[data-result]");
  if (!slot || slot.dataset.loaded) return;
  slot.dataset.loaded = "1";
  fetch(slot.dataset.result, { credentials: "same-origin" })
    .then((response) => (response.ok ? response.text() : Promise.reject(response.status)))
    .then((html) => { slot.innerHTML = "<span class='label'>Result</span>" + html; })
    .catch(() => { slot.innerHTML = "<span class='label'>Result</span><span class='faint'>Couldn't read it.</span>"; });
}, true);
