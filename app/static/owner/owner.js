// The owner view's few interactions. Every page works without this file; it adds the board's
// filters and layout, tabs, whole-row links, tooltips, and the Results chart filter.
(function () {
  "use strict";

  function remember(key, value) {
    try { localStorage.setItem("owner." + key, JSON.stringify(value)); } catch (e) { /* private mode */ }
  }
  function recall(key, fallback) {
    try {
      var raw = localStorage.getItem("owner." + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) { return fallback; }
  }
  function each(root, selector, fn) { Array.prototype.forEach.call(root.querySelectorAll(selector), fn); }
  function press(button, on) { button.setAttribute("aria-pressed", on ? "true" : "false"); }

  // Day board

  var ROW = 30;     // px between stacked rows of marks in a lane
  var GAP = 6;      // px kept clear between marks in one row

  function placeBoard(board, start, end) {
    var span = end - start;
    function pct(minute) { return Math.min(Math.max((minute - start) / span * 100, 0), 100); }
    each(board, "[data-min]", function (el) {
      var minute = +el.dataset.min;
      el.style.left = pct(minute) + "%";
      el.hidden = minute < start || minute > end;
    });
    each(board, ".hour-label", function (el) {
      var minute = +el.dataset.min;
      var step = span > 16 * 60 ? 3 : 2;
      var hour = minute / 60;
      var first = minute === start, last = minute === end;
      el.classList.toggle("first", first);
      el.classList.toggle("last", last);
      // The "now" label sits on the axis; an hour label right beside it would collide.
      var nearNow = board.dataset.now !== "" && Math.abs(minute - +board.dataset.now) <= span / 24;
      el.textContent = !nearNow && (first || last || (hour - start / 60) % step === 0) ? hourName(hour) : "";
    });
    each(board, ".hour", function (el) {
      el.classList.toggle("major", (+el.dataset.min - start) / 60 % 2 === 0);
    });
    each(board, ".window", function (el) {
      var from = Math.max(+el.dataset.from, start), to = Math.min(+el.dataset.to, end);
      el.hidden = to <= from;
      el.style.left = "calc(" + pct(from) + "% + 2px)";
      el.style.width = "calc(" + (pct(to) - pct(from)) + "% - 4px)";
    });
    packBoard(board);
  }

  function hourName(h) {
    h = h % 24;
    if (h === 0) return "12 AM";
    if (h === 12) return "12 PM";
    return h < 12 ? h + " AM" : (h - 12) + " PM";
  }

  // Marks that would overlap move down a row, first fit, in time order.
  function packBoard(board) {
    each(board, "[data-pack]", function (track) {
      var rows = [];
      var width = track.getBoundingClientRect().width;
      var marks = Array.prototype.filter.call(track.querySelectorAll(".mark"), function (m) {
        return !m.hidden && m.offsetParent !== null;
      });
      marks.sort(function (a, b) { return +a.dataset.min - +b.dataset.min; });
      marks.forEach(function (mark) {
        var left = parseFloat(mark.style.left) / 100 * width - 7;
        var right = left + mark.firstElementChild.getBoundingClientRect().width;
        var row = 0;
        while (rows[row] !== undefined && rows[row] + GAP > left) row++;
        rows[row] = right;
        mark.style.setProperty("--top", (12 + row * ROW) + "px");
      });
      track.style.minHeight = (Math.max(rows.length, 1) * ROW + 22) + "px";
    });
  }

  function setupBoard(board) {
    var lanes = recall("board.lanes", {});
    each(board, "[data-lane]", function (button) {
      var key = button.dataset.lane;
      var on = lanes[key] !== false;
      press(button, on);
      board.querySelector('[data-lane-row="' + key + '"]').hidden = !on;
      button.addEventListener("click", function () {
        var now = button.getAttribute("aria-pressed") !== "true";
        press(button, now);
        board.querySelector('[data-lane-row="' + key + '"]').hidden = !now;
        lanes[key] = now;
        remember("board.lanes", lanes);
        packBoard(board);
      });
    });

    var handled = board.querySelector("[data-handled]");
    function showHandled(on) {
      press(handled, on);
      board.dataset.showHandled = on ? "true" : "false";
      each(board, ".mark.handled", function (m) { m.style.display = on ? "" : "none"; });
      packBoard(board);
    }
    showHandled(recall("board.handled", false));
    handled.addEventListener("click", function () {
      var on = handled.getAttribute("aria-pressed") !== "true";
      remember("board.handled", on);
      showHandled(on);
    });

    var ranges = board.querySelectorAll("[data-range]");
    function useRange(button) {
      Array.prototype.forEach.call(ranges, function (b) { press(b, b === button); });
      var parts = button.dataset.range.split("-");
      placeBoard(board, +parts[0], +parts[1]);
    }
    var saved = recall("board.range", null);
    Array.prototype.forEach.call(ranges, function (button) {
      button.addEventListener("click", function () {
        remember("board.range", button.dataset.range === "0-1440" ? "whole" : "default");
        useRange(button);
      });
    });
    useRange(saved === "whole" ? ranges[ranges.length - 1] : ranges[0]);
    window.addEventListener("resize", function () { packBoard(board); });
  }

  // Tabs inside a panel

  function setupTray(tray) {
    var tabs = tray.querySelectorAll('[role="tab"]');
    Array.prototype.forEach.call(tabs, function (tab) {
      tab.addEventListener("click", function () {
        Array.prototype.forEach.call(tabs, function (t) {
          var on = t === tab;
          t.setAttribute("aria-selected", on ? "true" : "false");
          document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
        });
      });
    });
  }

  // Call page

  function setupCall(conv) {
    var panel = conv.closest("[data-conv]");
    var tech = panel && panel.querySelector("[data-tech]");
    function techMode(on) { panel.classList.toggle("tech", on); press(tech, on); }
    if (tech) {
      techMode(recall("call.tech", false));
      tech.addEventListener("click", function () {
        var on = tech.getAttribute("aria-pressed") !== "true";
        remember("call.tech", on);
        techMode(on);
      });
    }
    var text = document.querySelector("#report-text");
    if (text) {
      text.addEventListener("input", function () {
        text.style.height = "auto";
        text.style.height = Math.min(text.scrollHeight + 2, 160) + "px";
        text.style.overflowY = text.scrollHeight > 160 ? "auto" : "hidden";
      });
    }

    var select = document.querySelector("#report-turn");
    each(conv, "[data-report-turn]", function (button) {
      button.addEventListener("click", function () {
        if (select) select.value = button.dataset.reportTurn;
        each(conv, ".turn.reported", function (t) { t.classList.remove("reported"); });
        button.closest(".turn").classList.add("reported");
        if (text) { text.focus(); text.scrollIntoView({ block: "center", behavior: "smooth" }); }
      });
    });
  }

  // Player: the play button, scrub bar and time in place of the browser's audio controls.

  function clockTime(seconds) {
    if (!isFinite(seconds)) return "0:00";
    var s = Math.floor(seconds);
    return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
  }

  function setupPlayer(box) {
    var audio = box.querySelector("audio");
    var play = box.querySelector("[data-play]");
    var scrub = box.querySelector("[data-scrub]");
    var time = box.querySelector("[data-time]");
    audio.controls = false;
    box.setAttribute("data-ready", "");
    [play, scrub, time].forEach(function (el) { el.hidden = false; });
    function show() {
      var length = isFinite(audio.duration) ? audio.duration : 0;
      scrub.max = length;
      scrub.value = audio.currentTime;
      scrub.style.setProperty("--played", (length ? (audio.currentTime / length) * 100 : 0) + "%");
      time.textContent = clockTime(audio.currentTime) + " / " + clockTime(length);
    }
    function playing(on) {
      box.classList.toggle("playing", on);
      play.setAttribute("aria-label", on ? "Pause" : "Play");
    }
    play.addEventListener("click", function () { if (audio.paused) audio.play(); else audio.pause(); });
    scrub.addEventListener("input", function () { audio.currentTime = Number(scrub.value); show(); });
    audio.addEventListener("loadedmetadata", show);
    audio.addEventListener("timeupdate", show);
    audio.addEventListener("play", function () { playing(true); });
    audio.addEventListener("pause", function () { playing(false); });
    audio.addEventListener("ended", function () { playing(false); });
    show();
  }

  // Calls list filter

  function setupCallList(table) {
    var buttons = document.querySelectorAll("[data-show]");
    function show(name) {
      Array.prototype.forEach.call(buttons, function (b) { press(b, b.dataset.show === name); });
      var shown = 0;
      each(table, "tbody tr", function (row) {
        var on = name === "all" || (" " + row.dataset.tags + " ").indexOf(" " + name + " ") >= 0;
        row.hidden = !on;
        if (on) shown++;
      });
      var none = document.querySelector("[data-none]");
      if (none) none.hidden = shown > 0;
    }
    Array.prototype.forEach.call(buttons, function (b) {
      b.addEventListener("click", function () { show(b.dataset.show); });
    });
  }

  // Results: the cost estimate. The same sum as app/cost.py monthly(), rerun as the inputs slide.

  function setupCost(panel) {
    var suggested = JSON.parse(document.getElementById("cost-data").textContent);
    var inputs = panel.querySelectorAll("input[type=range]");
    function money(v, cents) {
      if (v === null || !isFinite(v)) { return "-"; }
      return "$" + (cents ? v.toFixed(2) : Math.round(v).toLocaleString("en-US"));
    }
    function shown(kind, v) {
      if (kind === "count") { return Math.round(v).toLocaleString("en-US"); }
      if (kind === "minutes") { return v.toFixed(1) + " min"; }
      if (kind === "share") { return Math.round(v * 100) + "%"; }
      if (kind === "rate") { return "$" + v.toFixed(3); }
      return "$" + Math.round(v);
    }
    function draw() {
      var x = {};
      Array.prototype.forEach.call(inputs, function (i) {
        x[i.name] = parseFloat(i.value);
        panel.querySelector('[data-out="' + i.name + '"]').textContent = shown(i.dataset.kind, x[i.name]);
      });
      var perCall = x.minutes_per_call * x.voice_per_minute + suggested.model_per_call;
      var total = x.calls_per_month * perCall + x.fixed_monthly;
      var booked = x.calls_per_month * x.booked_share;
      panel.querySelector('[data-fig="total"]').textContent = money(total);
      panel.querySelector('[data-fig="per_call"]').textContent = money(perCall, true);
      panel.querySelector('[data-fig="per_booked_job"]').textContent = booked ? money(total / booked, true) : "-";
      panel.querySelector('[data-fig="per_year"]').textContent = money(total * 12);
    }
    Array.prototype.forEach.call(inputs, function (i) { i.addEventListener("input", draw); });
    panel.querySelector("[data-cost-reset]").addEventListener("click", function () {
      Array.prototype.forEach.call(inputs, function (i) { i.value = suggested[i.name]; });
      draw();
    });
    draw();
  }

  // Results: calls by hour, filtered by call type

  function setupHours(chart) {
    var data = JSON.parse(document.getElementById("hours-data").textContent);
    var select = document.querySelector("#hours-type");
    function draw(type) {
      var calls = data.calls.filter(function (c) { return type === "all" || c.type === type; });
      var max = 1;
      var counts = data.hours.map(function (h) {
        var inHour = calls.filter(function (c) { return c.hour === h; });
        var row = {
          booked: inHour.filter(function (c) { return c.booked && !c.safety; }).length,
          safety: inHour.filter(function (c) { return c.safety; }).length,
          other: inHour.filter(function (c) { return !c.booked && !c.safety; }).length,
        };
        row.total = row.booked + row.safety + row.other;
        max = Math.max(max, row.total);
        return row;
      });
      each(chart, ".bar", function (bar, i) {
        var c = counts[i], per = 160 / max;
        bar.querySelector(".n").textContent = c.total;
        bar.querySelector(".n").classList.toggle("zero", c.total === 0);
        bar.querySelector(".seg-other").style.height = c.other * per + "px";
        bar.querySelector(".seg-safety").style.height = c.safety * per + "px";
        bar.querySelector(".seg-booked").style.height = c.booked * per + "px";
        bar.title = c.total + " calls: " + c.booked + " booked, " + c.safety + " safety, " + c.other + " other";
      });
      var total = document.querySelector("[data-hours-total]");
      if (total) total.textContent = calls.length + (calls.length === 1 ? " call" : " calls");
    }
    if (select) select.addEventListener("change", function () { draw(select.value); });
    draw(select ? select.value : "all");
  }

  // Copy buttons: the value goes to the clipboard, or is selected for the person to copy.
  function setupCopy() {
    document.addEventListener("click", function (e) {
      var button = e.target.closest("[data-copy]");
      if (!button) return;
      var done = function () { button.textContent = "Copied"; setTimeout(function () { button.textContent = "Copy"; }, 1500); };
      var select = function () {
        var text = button.parentNode.querySelector("[data-copy-text]");
        if (text && window.getSelection) { var range = document.createRange(); range.selectNodeContents(text); var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range); }
      };
      if (navigator.clipboard) navigator.clipboard.writeText(button.dataset.copy).then(done, select);
      else select();
    });
  }

  // Rows that open a call. The name in the row is the real link; this makes the rest of the row
  // act the same, and leaves other controls in the row alone.
  function setupRowLinks() {
    document.addEventListener("click", function (e) {
      var row = e.target.closest("[data-href]");
      if (!row || e.target.closest("a, button, summary, input, select, textarea, label, [data-tip]")) return;
      if (window.getSelection && String(window.getSelection())) return;
      if (e.metaKey || e.ctrlKey) { window.open(row.dataset.href, "_blank"); return; }
      window.location.href = row.dataset.href;
    });
    document.addEventListener("auxclick", function (e) {
      var row = e.target.closest("[data-href]");
      if (e.button !== 1 || !row || e.target.closest("a")) return;
      window.open(row.dataset.href, "_blank");
    });
  }

  // Tooltips for [data-tip]: one floating box, placed under the element, kept on screen.
  function setupTips() {
    var tip = document.createElement("div");
    tip.className = "tooltip";
    tip.setAttribute("role", "tooltip");
    document.body.appendChild(tip);
    function show(el) {
      tip.textContent = el.dataset.tip;
      var r = el.getBoundingClientRect();
      tip.style.left = "0px"; tip.style.top = "0px";
      tip.classList.add("on");
      var w = tip.offsetWidth, h = tip.offsetHeight;
      var left = Math.min(Math.max(r.left, 8), window.innerWidth - w - 8);
      var top = r.bottom + 6 + h > window.innerHeight ? r.top - h - 6 : r.bottom + 6;
      tip.style.left = left + "px"; tip.style.top = top + "px";
    }
    function hide() { tip.classList.remove("on"); }
    each(document, "[data-tip]", function (el) {
      if (!el.matches("a, button")) el.tabIndex = 0;
      el.addEventListener("mouseenter", function () { show(el); });
      el.addEventListener("focus", function () { show(el); });
      el.addEventListener("mouseleave", hide);
      el.addEventListener("blur", hide);
    });
    // Capture, so a table scrolling sideways hides it too, not only the page.
    window.addEventListener("scroll", hide, { capture: true, passive: true });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
  }

  // Resizable table columns. Dragging the edge between two headings moves width from one to the
  // other, so the table keeps its width; the widths are kept per browser.
  function setupResize(table) {
    var key = "cols." + table.dataset.resize;
    var cols = table.querySelectorAll("col");
    var heads = table.querySelectorAll("thead th");
    var start = Array.prototype.map.call(cols, function (c) { return c.style.width; });
    function freeze() {
      Array.prototype.forEach.call(heads, function (th, i) { cols[i].style.width = th.getBoundingClientRect().width + "px"; });
    }
    var saved = recall(key, null);
    if (saved && saved.length === cols.length) {
      Array.prototype.forEach.call(cols, function (c, i) { c.style.width = saved[i]; });
    }
    Array.prototype.forEach.call(heads, function (th, i) {
      if (i >= heads.length - 2) return;
      var grip = document.createElement("span");
      grip.className = "grip";
      grip.setAttribute("aria-hidden", "true");
      th.appendChild(grip);
      grip.addEventListener("dblclick", function () {
        Array.prototype.forEach.call(cols, function (c, n) { c.style.width = start[n]; });
        remember(key, null);
      });
      grip.addEventListener("pointerdown", function (e) {
        e.preventDefault();
        freeze();
        var x = e.clientX, a = parseFloat(cols[i].style.width), b = parseFloat(cols[i + 1].style.width);
        grip.classList.add("on");
        grip.setPointerCapture(e.pointerId);
        function move(ev) {
          var dx = Math.max(64 - a, Math.min(ev.clientX - x, b - 64));
          cols[i].style.width = a + dx + "px";
          cols[i + 1].style.width = b - dx + "px";
        }
        function up() {
          grip.classList.remove("on");
          grip.removeEventListener("pointermove", move);
          grip.removeEventListener("pointerup", up);
          remember(key, Array.prototype.map.call(cols, function (c) { return c.style.width; }));
        }
        grip.addEventListener("pointermove", move);
        grip.addEventListener("pointerup", up);
      });
    });
  }

  // Search box: the best matches as it is typed, from /owner/search/suggest. Arrow keys move,
  // Enter opens the highlighted match or, with none, the full results page.
  function setupSuggest(form) {
    var input = form.querySelector("input[name=q]");
    var list = form.querySelector(".suggest");
    var timer = null, asked = "", pick = -1;
    function rows() { return list.querySelectorAll("li"); }
    function close() { list.hidden = true; input.setAttribute("aria-expanded", "false"); pick = -1; }
    function mark(i) {
      var all = rows();
      pick = Math.max(-1, Math.min(i, all.length - 1));
      Array.prototype.forEach.call(all, function (li, n) { li.setAttribute("aria-selected", n === pick ? "true" : "false"); });
    }
    function row(cls, href, parts) {
      var li = document.createElement("li");
      li.setAttribute("role", "option");
      li.className = cls;
      li.dataset.go = href;
      parts.forEach(function (p) {
        var span = document.createElement("span");
        span.className = p[0];
        span.textContent = p[1];
        li.appendChild(span);
      });
      li.addEventListener("mousedown", function (e) { e.preventDefault(); window.location.href = href; });
      return li;
    }
    function show(items, q) {
      list.textContent = "";
      items.forEach(function (it) {
        list.appendChild(row("", it.href, [["t", it.title], ["k", it.kind + ", " + it.when], ["x", it.text]]));
      });
      var company = form.querySelector("input[name=tenant]");
      var all = form.action + "?q=" + encodeURIComponent(q) + (company ? "&tenant=" + encodeURIComponent(company.value) : "");
      list.appendChild(row("all", all, [["t", items.length ? "See all results" : "No quick matches. Search everything"]]));
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
      pick = -1;
    }
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) { close(); return; }
      timer = setTimeout(function () {
        asked = q;
        var url = form.dataset.suggest + (form.dataset.suggest.indexOf("?") < 0 ? "?" : "&") + "q=" + encodeURIComponent(q);
        fetch(url, { credentials: "same-origin" })
          .then(function (r) { return r.ok ? r.json() : { items: [] }; })
          .then(function (data) { if (asked === q && input.value.trim() === q) show(data.items, q); })
          .catch(close);
      }, 120);
    });
    input.addEventListener("keydown", function (e) {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); mark(pick + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); mark(pick - 1); }
      else if (e.key === "Escape") { close(); }
      else if (e.key === "Enter" && pick >= 0) { e.preventDefault(); window.location.href = rows()[pick].dataset.go; }
    });
    input.addEventListener("blur", function () { setTimeout(close, 100); });
  }

  // Node graph (debug bench): picking a step shows its detail beside the graph without moving
  // the page; arrow keys move between steps. Without this, each step's link opens its detail by
  // its anchor.
  function setupNodeGraph(root) {
    var graph = root.querySelector("[data-node-graph]");
    var detail = root.querySelector("[data-node-detail]");
    if (!graph || !detail) return;
    var links = Array.prototype.slice.call(graph.querySelectorAll("[data-node]"));
    function pick(link, focus, quiet) {
      links.forEach(function (l) { l.removeAttribute("aria-current"); });
      each(detail, "section.shown", function (s) { s.classList.remove("shown"); });
      var section = document.getElementById("d-" + link.dataset.node);
      if (!section) return;
      link.setAttribute("aria-current", "true");
      section.classList.add("shown");
      detail.classList.add("picked");
      detail.scrollTop = 0;
      if (focus) link.focus();
      if (!quiet) history.replaceState(null, "", "#d-" + link.dataset.node);
    }
    links.forEach(function (link, i) {
      link.addEventListener("click", function (e) { e.preventDefault(); pick(link, false); });
      link.addEventListener("keydown", function (e) {
        if (e.key === "ArrowDown" && links[i + 1]) { e.preventDefault(); pick(links[i + 1], true); }
        else if (e.key === "ArrowUp" && links[i - 1]) { e.preventDefault(); pick(links[i - 1], true); }
      });
    });
    each(detail, "[data-node-ref]", function (ref) {
      ref.addEventListener("click", function (e) {
        var target = links.filter(function (l) { return l.dataset.node === ref.dataset.nodeRef; })[0];
        if (target) { e.preventDefault(); pick(target, true); }
      });
    });
    var wanted = location.hash.indexOf("#d-") === 0 ? location.hash.slice(3) : null;
    // A call in progress follows its newest step; a finished one opens on its first.
    var fallback = root.closest("[data-live]") ? links[links.length - 1] : links[0];
    var start = links.filter(function (l) { return l.dataset.node === wanted; })[0] || fallback;
    if (start) pick(start, false, true);
  }

  // A call in progress: the server says when a step is recorded and the graph is fetched again,
  // keeping the step picked. The stream ends with the call.
  function setupLive(page) {
    if (!window.EventSource) return;
    var slot = page.querySelector("[data-graph-slot]");
    var source = new EventSource(page.dataset.live);
    var first = true;
    function refresh() {
      var picked = slot.querySelector("[data-node][aria-current]");
      var keep = picked ? picked.dataset.node : null;
      var all = slot.querySelectorAll("[data-node]");
      var atEnd = !picked || picked === all[all.length - 1];
      fetch(page.dataset.liveGraph, { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) {
          if (html === null) return;
          slot.innerHTML = html;
          if (keep && !atEnd) history.replaceState(null, "", "#d-" + keep);
          else history.replaceState(null, "", location.pathname);
          setupNodeGraph(slot);
          if (atEnd) {
            var links = slot.querySelectorAll("[data-node]");
            if (links.length) links[links.length - 1].click();
          }
        });
    }
    source.addEventListener("changed", function () { if (first) { first = false; return; } refresh(); });
    source.addEventListener("ended", function () {
      source.close();
      var tag = page.querySelector("[data-live-tag]");
      if (tag) { tag.textContent = "Ended"; tag.className = "tag outline"; }
      refresh();
    });
  }

  // What changed in ServiceTitan (debug bench schedule): the list is read again every few seconds,
  // so an edit or a booking made anywhere shows here as it lands. Rows are built as text nodes.
  function setupChanges(panel) {
    var body = panel.querySelector("[data-changes-body]");
    var url = panel.getAttribute("data-changes");
    function cell(row, text, cls) {
      var td = document.createElement("td");
      if (cls) td.className = cls;
      td.textContent = text;
      row.appendChild(td);
      return td;
    }
    function draw(items) {
      body.textContent = "";
      if (!items.length) {
        var none = document.createElement("tr");
        cell(none, "No changes yet.", "muted").colSpan = 4;
        body.appendChild(none);
      }
      items.forEach(function (c) {
        var row = document.createElement("tr");
        cell(row, c.at_local, "time");
        var who = cell(row, c.by + " ", "fit");
        if (c.where === "owner" || c.where === "bench") {
          var where = document.createElement("span");
          where.className = "faint";
          where.textContent = c.where;
          who.appendChild(where);
        }
        var what = cell(row, "");
        if (c.ok) { what.textContent = c.what; } else {
          var err = document.createElement("span");
          err.className = "error";
          err.textContent = c.what + ": " + c.error;
          what.appendChild(err);
        }
        cell(row, c.landed_local, "time");
        body.appendChild(row);
      });
    }
    setInterval(function () {
      if (document.hidden) return;
      fetch(url, { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) { if (data) draw(data.items); })
        .catch(function () {});
    }, 3000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    each(document, "[data-changes]", setupChanges);
    each(document, "form.search[data-suggest]", setupSuggest);
    each(document, "table[data-resize]", setupResize);
    setupRowLinks();
    setupCopy();
    setupTips();
    each(document, "#board", setupBoard);
    each(document, "[data-tray]", setupTray);
    each(document, ".conv", setupCall);
    each(document, "[data-player]", setupPlayer);
    each(document, "[data-call-list]", setupCallList);
    each(document, "[data-hours-chart]", setupHours);
    each(document, "[data-cost]", setupCost);
    each(document, "[data-graph]", setupNodeGraph);
    each(document, "[data-live]", setupLive);
  });
})();

// Call checks: while a run works, poll its progress, move the bar, and reload when it's done.
(function () {
  "use strict";
  var box = document.querySelector("[data-run-poll]");
  if (!box || !window.fetch) { return; }
  var bar = box.querySelector(".bar span");
  var text = box.querySelector("[data-run-text]");
  function tick() {
    fetch(box.dataset.runPoll, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (run) {
        if (!run) { return; }
        if (bar) { bar.style.width = run.percent + "%"; }
        if (text) {
          text.textContent = run.status === "queued" ? "Waiting to start" : "Checked " + run.done + " of " + run.total;
        }
        if (run.finished) { window.location.reload(); } else { setTimeout(tick, 1500); }
      })
      .catch(function () { setTimeout(tick, 4000); });
  }
  setTimeout(tick, 1000);
})();
