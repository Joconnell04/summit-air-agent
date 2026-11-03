// The owner view's few interactions. Every page works without this file; it adds the board's
// filters and layout, tabs, the plain and technical call views, and the Results chart filter.
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
    var buttons = document.querySelectorAll("[data-view]");
    function view(name) {
      conv.classList.toggle("plain", name === "plain");
      Array.prototype.forEach.call(buttons, function (b) { press(b, b.dataset.view === name); });
    }
    Array.prototype.forEach.call(buttons, function (b) {
      b.addEventListener("click", function () { remember("call.view", b.dataset.view); view(b.dataset.view); });
    });
    view(recall("call.view", "plain"));

    var select = document.querySelector("#report-turn");
    var text = document.querySelector("#report-text");
    each(conv, "[data-report-turn]", function (button) {
      button.addEventListener("click", function () {
        if (select) select.value = button.dataset.reportTurn;
        each(conv, ".turn.reported", function (t) { t.classList.remove("reported"); });
        button.closest(".turn").classList.add("reported");
        if (text) { text.focus(); text.scrollIntoView({ block: "center", behavior: "smooth" }); }
      });
    });
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

  document.addEventListener("DOMContentLoaded", function () {
    each(document, "#board", setupBoard);
    each(document, "[data-tray]", setupTray);
    each(document, ".conv", setupCall);
    each(document, "[data-call-list]", setupCallList);
    each(document, "[data-hours-chart]", setupHours);
  });
})();
