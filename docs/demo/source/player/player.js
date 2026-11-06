// Draws one demo video frame from time alone, for the video-maker renderer (window.seek(t)).
// window.FILM comes from build.py: segments with absolute start and end times, each with steps.
// Segment kinds: intro, ask and outro cards; image (a screenshot under a moving camera, with a
// highlight box); term and code (lines with a highlighted range).
(() => {
  const F = window.FILM;
  window.WIDTH = 1920; window.HEIGHT = 1080; window.FPS = 30; window.DURATION = F.duration;

  const PANEL_W = 1792, PANEL_H = 704, LINE = 34, CAM_MOVE = 0.9;
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  const prog = (t, a, b) => clamp((t - a) / (b - a));
  const lerp = (a, b, k) => a + (b - a) * k;
  const outCubic = k => 1 - Math.pow(1 - k, 3);
  const inOutCubic = k => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const same = (a, b) => JSON.stringify(a || null) === JSON.stringify(b || null);

  // ---------- build every segment once ----------
  const stage = document.getElementById('stage');
  const panel = document.getElementById('panel');
  document.querySelector('#head .num').textContent = `${F.num} of ${F.total}`;
  document.querySelector('#head .title').textContent = F.title;
  const chips = F.chips.map((name, i) => {
    const c = el('div', 'chip');
    c.append(el('b', '', String(i + 1)), el('span', '', name));
    document.getElementById('chips').append(c);
    return c;
  });
  const showV = document.querySelector('.row.show .v'), sayV = document.querySelector('.row.say .v');
  const rows = [document.querySelector('.row.show'), document.querySelector('.row.say')];
  const progress = document.getElementById('progress');
  const loads = [];

  for (const seg of F.segments) {
    const m = el('div', 'media');
    seg.node = m;
    m.style.display = 'none';
    if (seg.kind === 'intro' || seg.kind === 'ask' || seg.kind === 'outro') {
      m.classList.add('card', seg.kind);
      const inner = el('div', 'inner');
      inner.append(el('div', 'kicker', seg.kicker || ''), el('div', 'big', seg.big || ''));
      if (seg.sub) inner.append(el('div', 'sub', seg.sub));
      if (seg.list) {
        const ol = el('ol');
        seg.list.forEach((item, i) => { const li = el('li'); li.append(el('b', '', String(i + 1)), el('span', '', item)); ol.append(li); });
        inner.append(ol);
      }
      seg.inner = inner;
      m.append(inner);
    } else if (seg.kind === 'image') {
      const img = el('img');
      img.src = seg.src;
      img.style.width = seg.w + 'px';
      loads.push(img.decode().catch(() => {}));
      seg.img = img;
      m.append(img);
      for (const st of seg.steps) {
        if (!st.box) continue;
        st.boxNode = el('div', 'box');
        m.append(st.boxNode);
        if (st.tag) { st.tagNode = el('div', 'tag', st.tag); m.append(st.tagNode); }
      }
    } else {
      m.classList.add(seg.kind);
      const bar = el('div', 'bar');
      bar.append(el('span', '', seg.kind === 'term' ? 'Terminal' : 'Code'), el('span', 'path', seg.label || ''));
      const lines = el('div', 'lines'), scroll = el('div', 'scroll');
      seg.lineNodes = seg.lines.map((text, i) => {
        const ln = el('div', 'ln');
        if (seg.kind === 'code') { ln.append(el('span', 'no', String(seg.first + i)), document.createTextNode(text)); }
        else { ln.textContent = text; if (text.startsWith('$ ')) ln.classList.add('cmd'); }
        scroll.append(ln);
        return ln;
      });
      for (const st of seg.steps) {
        if (!st.hl) continue;
        st.hlNode = el('div', 'hl');
        st.hlNode.style.top = (st.hl[0] - 1) * LINE - 1 + 'px';
        st.hlNode.style.height = (st.hl[1] - st.hl[0] + 1) * LINE + 2 + 'px';
        scroll.prepend(st.hlNode);
      }
      seg.scroll = scroll;
      lines.append(scroll);
      m.append(bar, lines);
    }
    panel.append(m);
  }
  window.ready = Promise.all([document.fonts.ready, ...loads]);

  // ---------- pieces of a frame ----------
  const stepAt = (seg, t) => {
    let j = 0;
    while (j + 1 < seg.steps.length && t >= seg.steps[j + 1].t0) j++;
    return j;
  };

  // A value set per step that eases to each new target as its step starts.
  function eased(seg, t, key, fallback) {
    let from = seg.steps[0][key] ?? fallback, cur = from;
    for (let j = 1; j < seg.steps.length; j++) {
      const st = seg.steps[j];
      if (t < st.t0) break;
      const to = st[key] ?? from;
      const k = inOutCubic(prog(t, st.t0, st.t0 + CAM_MOVE));
      cur = Array.isArray(from) ? from.map((v, i) => lerp(v, to[i], k)) : lerp(from, to, k);
      from = to;
    }
    return cur;
  }

  // How visible a step's highlight is: in after the camera lands, out as the step ends, and held
  // across steps that keep the same highlight.
  function markAlpha(seg, j, t, key) {
    const st = seg.steps[j], prev = seg.steps[j - 1], next = seg.steps[j + 1];
    const moved = prev && !same(prev.cam, st.cam) && st.cam;
    const delay = moved ? CAM_MOVE - 0.1 : 0.2;
    const kin = prev && same(prev[key], st[key]) ? 1 : outCubic(prog(t, st.t0 + delay, st.t0 + delay + 0.35));
    const kout = next && same(next[key], st[key]) ? 1 : 1 - prog(t, st.t1 - 0.25, st.t1);
    return kin * kout;
  }

  function drawImage(seg, t) {
    const [cx, cy, cw] = eased(seg, t, 'cam', [0, 0, seg.w]);
    const s = PANEL_W / cw;
    seg.img.style.transform = `translate(${-cx * s}px, ${-cy * s}px) scale(${s})`;
    const j = stepAt(seg, t);
    seg.steps.forEach((st, i) => {
      if (!st.boxNode) return;
      const a = i === j ? markAlpha(seg, i, t, 'box') : 0;
      st.boxNode.style.opacity = a;
      if (st.tagNode) st.tagNode.style.opacity = a;
      if (a <= 0) return;
      const [bx, by, bw, bh] = st.box, pad = 8;
      const left = (bx - cx) * s - pad, top = (by - cy) * s - pad, w = bw * s + 2 * pad, h = bh * s + 2 * pad;
      Object.assign(st.boxNode.style, { left: left + 'px', top: top + 'px', width: w + 'px', height: h + 'px' });
      if (st.tagNode) {
        const below = top < 60;
        st.tagNode.style.left = Math.max(12, left) + 'px';
        st.tagNode.style.top = (below ? top + h + 12 : top - 50) + 'px';
      }
    });
  }

  function drawLines(seg, t) {
    const scroll = eased(seg, t, 'scroll', 0);
    seg.scroll.style.transform = `translateY(${-scroll * LINE}px)`;
    // A terminal types its first command, then prints the rest a line at a time.
    const typed = seg.kind === 'term' ? seg.lines[0].length : 0;
    const typeEnd = seg.t0 + 0.35 + Math.min(0.9, typed * 0.012);
    seg.lineNodes.forEach((ln, i) => {
      if (seg.kind !== 'term') { ln.style.opacity = 1; return; }
      if (i === 0) {
        const n = Math.floor(prog(t, seg.t0 + 0.35, typeEnd) * typed);
        ln.textContent = seg.lines[0].slice(0, Math.max(2, n));
        ln.style.opacity = 1;
      } else {
        ln.style.opacity = t >= typeEnd + 0.15 + i * 0.035 ? 1 : 0;
      }
    });
    // A highlight waits until the lines under it have printed.
    const printed = seg.kind === 'term' ? typeEnd + 0.15 + seg.lines.length * 0.035 : seg.t0;
    const ready = outCubic(prog(t, printed, printed + 0.3));
    const j = stepAt(seg, t);
    seg.steps.forEach((st, i) => {
      if (st.hlNode) st.hlNode.style.opacity = i === j ? ready * markAlpha(seg, i, t, 'hl') : 0;
    });
  }

  function drawCard(seg, t) {
    // The first frame is a finished picture, so the opening card is already in place.
    const k = seg === F.segments[0] ? 1 : outCubic(prog(t, seg.t0 + 0.1, seg.t0 + 0.6));
    seg.inner.style.opacity = k;
    seg.inner.style.transform = `translateY(${(1 - k) * 16}px)`;
  }

  function caption(seg, t) {
    const owner = seg.steps ? seg.steps[stepAt(seg, t)] : seg;
    const show = owner.show || '', say = owner.say || '';
    showV.textContent = show;
    sayV.textContent = say;
    rows[0].style.visibility = show ? 'visible' : 'hidden';
    rows[1].style.visibility = say ? 'visible' : 'hidden';
    const first = owner.t0 === 0;
    const k = first ? 1 : outCubic(prog(t, owner.t0 + 0.05, owner.t0 + 0.4));
    const out = 1 - prog(t, owner.t1 - 0.18, owner.t1);
    const last = owner.t1 >= F.duration - 0.01;
    rows.forEach((r, i) => {
      const ki = first ? 1 : outCubic(prog(t, owner.t0 + 0.05 + i * 0.06, owner.t0 + 0.4 + i * 0.06));
      r.style.opacity = ki * (last ? 1 : out);
      r.style.transform = `translateY(${(1 - k) * 10}px)`;
    });
  }

  window.seek = (t) => {
    t = clamp(t, 0, F.duration - 1e-6);
    let i = 0;
    while (i + 1 < F.segments.length && t >= F.segments[i + 1].t0) i++;
    const seg = F.segments[i], prev = F.segments[i - 1];
    F.segments.forEach(s => { s.node.style.display = 'none'; });
    const draw = s => {
      s.node.style.display = 'block';
      if (s.kind === 'image') drawImage(s, t);
      else if (s.kind === 'term' || s.kind === 'code') drawLines(s, t);
      else drawCard(s, t);
    };
    draw(seg);
    const kin = outCubic(prog(t, seg.t0, seg.t0 + 0.45));
    seg.node.style.opacity = prev ? kin : 1;
    seg.node.style.transform = prev ? `translateX(${(1 - kin) * 48}px)` : 'none';
    if (prev && t < seg.t0 + 0.3) {
      draw(prev);
      const kout = prog(t, seg.t0, seg.t0 + 0.3);
      prev.node.style.opacity = 1 - kout;
      prev.node.style.transform = `translateX(${-kout * 32}px)`;
      prev.node.style.zIndex = 0;
      seg.node.style.zIndex = 1;
    }
    caption(seg, t);
    chips.forEach((c, n) => c.classList.toggle('on', n === seg.scenario));
    progress.style.width = (t / F.duration) * 1920 + 'px';
  };
})();
