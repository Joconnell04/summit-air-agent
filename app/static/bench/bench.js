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
