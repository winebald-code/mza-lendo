/* ===========================================================================
   Bacaan — charts
   Small hand-drawn SVG renderers. No chart library: the shapes here are simple
   and a dependency would cost more than it returns.
   =========================================================================== */
(function () {
  'use strict';

  if (window.__bcnCharts) { window.__bcnCharts.draw(); return; }

  const NS = 'http://www.w3.org/2000/svg';

  /* The palette is read from the document rather than frozen here, because the
     charts are drawn as SVG attributes — fill="#07090B" — and an attribute
     cannot inherit a theme the way a CSS property can. On the dark ground the
     ink series was near-black bars on a near-black card, with value labels to
     match: the primary series was invisible.

     Re-read on every draw, and draw() already runs on theme change via the
     resize path, so switching themes repaints the charts with the new ink. */
  const readPalette = () => {
    const cs = getComputedStyle(document.documentElement);
    const v = (name, fallback) => {
      const raw = (cs.getPropertyValue(name) || '').trim();
      return raw ? 'rgb(' + raw + ')' : fallback;
    };
    return {
      INK: v('--c-ink', '#07090B'),
      DIVIDER: v('--c-divider', '#D7DCE2'),
      MUTED: v('--c-slate-400', '#8B939E'),
      SIGNAL: '#FF6A00',
      SIGNAL_D: v('--c-signal-600', '#DB5A00'),
    };
  };

  let PAL = readPalette();
  let INK = PAL.INK;
  let SIGNAL = PAL.SIGNAL;
  let SIGNAL_D = PAL.SIGNAL_D;
  let DIVIDER = PAL.DIVIDER;
  let MUTED = PAL.MUTED;
  const MONO = 'IBM Plex Mono, monospace';
  const DISPLAY = 'Space Grotesk, sans-serif';
  const SEL = '[data-bars],[data-line],[data-ranks],[data-donut]';
  const FS = 10;
  const FSV = 9.5;
  const CW = 0.62;

  function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach((k) => node.setAttribute(k, attrs[k]));
    return node;
  }

  function tw(s, size) { return String(s).length * size * CW; }

  function txt(svg, o) {
    const t = el('text', {
      x: o.x, y: o.y, 'text-anchor': o.anchor || 'middle',
      'font-size': o.size || FS, fill: o.fill || MUTED, 'font-family': o.font || MONO,
    });
    if (o.weight) t.setAttribute('font-weight', o.weight);
    if (o.transform) t.setAttribute('transform', o.transform);
    t.textContent = o.s;
    svg.appendChild(t);
    return t;
  }

  function swatch(svg, x, baseline, colour) {
    svg.appendChild(el('rect', {
      x: x, y: baseline - FS * 0.82, width: 8, height: 8, rx: 2, fill: colour,
    }));
  }

  function svgRoot(host, w, h, aria) {
    const svg = el('svg', {
      viewBox: '0 0 ' + w + ' ' + h,
      width: w, height: h, style: 'max-width:100%',
      preserveAspectRatio: 'xMidYMid meet', role: 'img',
    });
    if (aria) svg.setAttribute('aria-label', aria);
    host.textContent = '';
    host.appendChild(svg);
    return svg;
  }

  function attr(host, name, fallback) {
    return host.hasAttribute(name) ? host.getAttribute(name) : fallback;
  }

  function fmt(v) {
    const n = Number(v) || 0;
    if (Math.abs(n) >= 1000000) return (n / 1000000).toFixed(1).replace('.0', '') + 'M';
    if (Math.abs(n) >= 10000) return Math.round(n / 1000) + 'k';
    return String(n);
  }

  function fit(s, w, size) {
    s = String(s);
    const max = Math.floor(w / (size * CW));
    return s.length <= max ? s : s.slice(0, Math.max(1, max - 1)) + '…';
  }

  function axisTitles(svg, W, H, plot, xt, yt) {
    if (yt) {
      const y = plot.top + plot.h / 2;
      txt(svg, { x: 10, y: y, s: yt, transform: 'rotate(-90 10 ' + y + ')' });
    }
    if (xt) txt(svg, { x: plot.left + plot.w / 2, y: H - 4, s: xt });
  }

  function legendRow(svg, items, right, baseline) {
    let width = -12;
    items.forEach((it) => { width += 26 + tw(it.k, FS); });
    let x = right - width;
    items.forEach((it) => {
      swatch(svg, x, baseline, it.c);
      txt(svg, { x: x + 14, y: baseline, s: it.k, anchor: 'start' });
      x += 26 + tw(it.k, FS);
    });
  }

  function labelRun(n, get) {
    let taken = Infinity;
    for (let i = n - 1; i >= 0; i--) {
      const o = get(i);
      if (!o) continue;
      if (o.left + o.w > taken - o.gap) continue;
      o.draw();
      taken = o.left;
    }
  }

  function place(centre, w, W) {
    let anchor = 'middle', x = centre, left = centre - w / 2;
    if (centre + w / 2 > W - 2) { anchor = 'end'; x = W - 2; left = x - w; }
    else if (left < 2) { anchor = 'start'; x = 2; left = 2; }
    return { anchor: anchor, x: x, left: left, w: w };
  }

  /* ── Column chart ─────────────────────────────────────────────────────── */
  function bars(host, W, H) {
    let data;
    try { data = JSON.parse(host.getAttribute('data-bars')); } catch (e) { return; }
    if (!data || !data.length) return;

    const yt = attr(host, 'data-y-label', 'Units');
    const xt = attr(host, 'data-x-label', 'Date');
    /* The legend used to need 560px, which no phone gives it — the chart card
       is about 316px wide on a handset, so the key simply vanished and the two
       series went unlabelled exactly where they are hardest to tell apart.
       legendRow right-aligns and its width is computable, so the gate is now
       whether it actually fits rather than a guess. */
    const keyItems = [{ c: INK, k: 'Earlier' }, { c: SIGNAL, k: 'Latest' }];
    let keyW = -12;
    keyItems.forEach((it) => { keyW += 26 + tw(it.k, FS); });
    const key = W - (yt ? 44 : 32) - 6 >= keyW;
    const padL = yt ? 44 : 32, padR = 6, padT = key ? 26 : 16, padB = xt ? 34 : 22;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    if (plotW < 60 || plotH < 40) return;

    const svg = svgRoot(host, W, H,
      attr(host, 'data-label', 'Units completed per day over the last two weeks'));

    const nice = niceMax(Math.max.apply(null, data.map((d) => d.v)) || 1);
    const slot = plotW / data.length;
    const barW = Math.max(3, Math.min(28, slot * 0.56));
    const base = padT + plotH;
    const height = (v) => (v > 0 ? Math.max(2, (v / nice) * plotH) : 0);

    [0, 0.5, 1].forEach((f) => {
      const y = base - plotH * f;
      svg.appendChild(el('line', {
        x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: DIVIDER, 'stroke-width': 1,
        'stroke-dasharray': f === 0 ? '0' : '3 4',
      }));
      txt(svg, { x: padL - 6, y: y + 3.5, s: fmt(Math.round(nice * f)), anchor: 'end' });
    });

    data.forEach((d, i) => {
      const h = height(d.v);
      if (!h) return;
      const rect = el('rect', {
        x: padL + i * slot + (slot - barW) / 2,
        y: base - h, width: barW, height: h, rx: 2,
        fill: i === data.length - 1 ? SIGNAL : INK,
        opacity: i === data.length - 1 ? 1 : 0.82,
      });
      const title = el('title');
      title.textContent = shortDate(d.d) + ': ' + d.v;
      rect.appendChild(title);
      svg.appendChild(rect);
    });

    labelRun(data.length, (i) => {
      if (!(data[i].v > 0)) return null;
      const s = fmt(data[i].v);
      const p = place(padL + i * slot + slot / 2, tw(s, FSV), W);
      p.gap = 6;
      p.draw = () => txt(svg, {
        x: p.x, y: Math.max(padT - 5, base - height(data[i].v) - 4),
        s: s, anchor: p.anchor, size: FSV, weight: 600,
        fill: i === data.length - 1 ? SIGNAL_D : INK,
      });
      return p;
    });

    labelRun(data.length, (i) => {
      const s = shortDate(data[i].d);
      const p = place(padL + i * slot + slot / 2, tw(s, FS), W);
      p.gap = 8;
      p.draw = () => txt(svg, { x: p.x, y: base + 13, s: s, anchor: p.anchor });
      return p;
    });

    axisTitles(svg, W, H, { left: padL, top: padT, w: plotW, h: plotH }, xt, yt);
    if (key) legendRow(svg, keyItems, W - padR, 11);
  }

  /* ── Sparkline / trend line ───────────────────────────────────────────── */
  function line(host, W, H) {
    let data;
    try { data = JSON.parse(host.getAttribute('data-line')); } catch (e) { return; }
    if (!data || data.length < 2) return;

    const yt = attr(host, 'data-y-label', 'Score');
    const xt = attr(host, 'data-x-label', 'Reading');
    const padL = yt ? 42 : 30, padR = 6, padT = 12, padB = xt ? 34 : 22;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    if (plotW < 60 || plotH < 40) return;

    const svg = svgRoot(host, W, H, attr(host, 'data-label', 'Trend'));
    const step = plotW / (data.length - 1);
    const base = padT + plotH;

    [0, 25, 50, 75, 100].forEach((v) => {
      const y = base - (v / 100) * plotH;
      svg.appendChild(el('line', {
        x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: DIVIDER, 'stroke-width': 1,
        'stroke-dasharray': v === 0 ? '0' : '3 4',
      }));
      txt(svg, { x: padL - 6, y: y + 3.5, s: String(v), anchor: 'end' });
    });

    const pts = data.map((d, i) => [
      padL + i * step,
      base - (Math.max(0, Math.min(100, d.v)) / 100) * plotH,
    ]);

    svg.appendChild(el('path', {
      d: 'M' + pts[0][0] + ',' + base + ' L' +
         pts.map((p) => p[0] + ',' + p[1]).join(' L') +
         ' L' + pts[pts.length - 1][0] + ',' + base + ' Z',
      fill: SIGNAL, opacity: 0.1,
    }));

    svg.appendChild(el('path', {
      d: 'M' + pts.map((p) => p[0] + ',' + p[1]).join(' L'),
      fill: 'none', stroke: SIGNAL, 'stroke-width': 2.2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    }));

    const dotR = Math.max(1.4, Math.min(2.4, step / 3));
    pts.forEach((p, i) => {
      const last = i === pts.length - 1;
      const c = el('circle', {
        cx: p[0], cy: p[1], r: last ? 4 : dotR,
        fill: last ? SIGNAL : '#FFFFFF',
        stroke: SIGNAL, 'stroke-width': last ? 1.6 : 1.2,
      });
      const title = el('title');
      title.textContent = (data[i].t || '') + ': ' + data[i].v + '%';
      c.appendChild(title);
      svg.appendChild(c);
    });

    labelRun(pts.length, (i) => {
      const s = fmt(data[i].v);
      const p = place(pts[i][0], tw(s, FSV), W);
      p.gap = 6;
      p.draw = () => txt(svg, {
        x: p.x, y: pts[i][1] < base - 14 ? pts[i][1] + 13 : pts[i][1] - 7,
        s: s, anchor: p.anchor, size: FSV, weight: 600,
        fill: i === pts.length - 1 ? SIGNAL_D : INK,
      });
      return p;
    });

    const a = data[0].t || '';
    const b = data[data.length - 1].t || '';
    if (b) txt(svg, { x: W - padR, y: base + 13, s: b, anchor: 'end' });
    if (a && padL + tw(a, FS) + 10 < (W - padR) - tw(b, FS)) {
      txt(svg, { x: padL, y: base + 13, s: a, anchor: 'start' });
    }

    axisTitles(svg, W, H, { left: padL, top: padT, w: plotW, h: plotH }, xt, yt);
  }

  /* ── Horizontal ranking bars ──────────────────────────────────────────── */
  function ranks(host) {
    let data;
    try { data = JSON.parse(host.getAttribute('data-ranks')); } catch (e) { return; }
    if (!data || !data.length) return;
    const max = Math.max.apply(null, data.map((d) => d[1])) || 1;
    host.textContent = '';
    data.forEach((d) => {
      const row = document.createElement('div');
      row.className = 'grid grid-cols-[minmax(0,1fr)_auto] gap-3 items-center py-[7px]';

      const left = document.createElement('div');
      left.className = 'min-w-0';
      const key = document.createElement('p');
      key.className = 'text-[12.5px] text-ink m-0 truncate';
      key.textContent = d[0];
      const track = document.createElement('div');
      track.className = 'mt-1 h-[5px] rounded-full bg-divider overflow-hidden';
      const fill = document.createElement('div');
      fill.className = 'h-full rounded-full bg-ink';
      fill.style.width = Math.max(3, (d[1] / max) * 100) + '%';
      track.appendChild(fill);
      left.appendChild(key);
      left.appendChild(track);

      const value = document.createElement('span');
      value.className = 'font-mono tabular-nums text-[12px] font-semibold text-slate-500';
      value.textContent = Number(d[1]).toLocaleString();

      row.appendChild(left);
      row.appendChild(value);
      host.appendChild(row);
    });
  }

  /* ── Donut ────────────────────────────────────────────────────────────── */
  function donut(host, W, H) {
    let data;
    try { data = JSON.parse(host.getAttribute('data-donut')); } catch (e) { return; }
    if (!data || !data.length) return;
    const total = data.reduce((a, d) => a + d.v, 0);
    if (!total) return;

    const rowH = 16;
    const legendH = data.length * rowH + 6;
    let mode = 'none';
    let ring = Math.round(Math.min(W, H || W, 220));
    if (W - ring >= 104 && ring >= 96) {
      mode = 'right';
    } else if (H >= 96 + legendH && W >= 120) {
      mode = 'below';
      ring = Math.round(Math.min(W, H - legendH, 220));
    }
    if (ring < 80) return;

    const vw = mode === 'none' ? ring : W;
    const vh = mode === 'none' ? ring : H;
    const svg = svgRoot(host, vw, vh, attr(host, 'data-label', 'Distribution'));

    const sw = Math.round(ring * 0.12);
    const r = (ring - sw) / 2 - 1;
    const cx = mode === 'right' ? ring / 2 : vw / 2;
    const cy = mode === 'below' ? ring / 2 : vh / 2;

    let offset = 0;
    const circumference = 2 * Math.PI * r;
    data.forEach((d) => {
      const frac = d.v / total;
      const arc = el('circle', {
        cx: cx, cy: cy, r: r, fill: 'none', stroke: d.c,
        'stroke-width': sw,
        'stroke-dasharray': Math.max(0, frac * circumference - 2) + ' ' + circumference,
        'stroke-dashoffset': -offset * circumference,
        transform: 'rotate(-90 ' + cx + ' ' + cy + ')',
        'stroke-linecap': 'butt',
      });
      const title = el('title');
      title.textContent = d.k + ': ' + d.v;
      arc.appendChild(title);
      svg.appendChild(arc);
      offset += frac;
    });

    txt(svg, {
      x: cx, y: cy - ring * 0.013, s: total, size: Math.round(ring * 0.175),
      weight: 700, fill: INK, font: DISPLAY,
    });
    txt(svg, {
      x: cx, y: cy + ring * 0.10, size: Math.max(8, Math.round(ring * 0.062)),
      s: (attr(host, 'data-total-label', 'TOTAL') || 'TOTAL').toUpperCase(),
    });

    if (mode === 'none') return;

    const lx = mode === 'right' ? ring + 16 : 4;
    const lw = mode === 'right' ? vw - lx - 4 : vw - 8;
    let y = mode === 'right' ? cy - (data.length * rowH) / 2 + 11 : ring + 16;
    data.forEach((d) => {
      swatch(svg, lx, y, d.c);
      txt(svg, { x: lx + 14, y: y, s: fit(d.k, lw - 46, FS), anchor: 'start', fill: INK });
      txt(svg, { x: lx + lw, y: y, s: fmt(d.v), anchor: 'end' });
      y += rowH;
    });
  }

  function niceMax(v) {
    if (v <= 5) return 5;
    const mag = Math.pow(10, Math.floor(Math.log10(v)));
    return Math.ceil(v / mag) * mag;
  }

  function shortDate(iso) {
    const d = new Date(iso + 'T00:00:00');
    if (isNaN(d)) return iso;
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  /* ── Render loop ──────────────────────────────────────────────────────── */
  const ro = window.ResizeObserver ? new ResizeObserver(schedule) : null;

  function width(box) {
    const vw = document.documentElement.clientWidth || 0;
    const x = box.left + (window.scrollX || window.pageXOffset || 0);
    const room = vw ? Math.max(220, vw - x - 8) : Infinity;
    return Math.round(Math.min(box.width, room));
  }

  function render(host) {
    const box = host.getBoundingClientRect();
    const W = width(box);
    const H = Math.round(box.height);
    if (W < 80) return;

    const data = host.getAttribute('data-bars') || host.getAttribute('data-line') ||
                 host.getAttribute('data-ranks') || host.getAttribute('data-donut') || '';
    /* The theme belongs in the signature. Colours are baked into SVG attributes
       at draw time, so a flip changes the output even though the size, labels
       and data are identical — without it this cache short-circuits and the
       chart keeps the previous theme's ink until the window is resized. */
    const sig = W + 'x' + H + '|' + attr(host, 'data-x-label', '') + '|' +
                attr(host, 'data-y-label', '') + '|' + data + '|' +
                (document.documentElement.dataset.theme || '');
    if (host.__sig === sig && host.firstChild) return;
    host.__sig = sig;

    if (ro && !host.__ro) { host.__ro = 1; ro.observe(host); }

    if (host.hasAttribute('data-bars')) bars(host, W, H || 150);
    else if (host.hasAttribute('data-line')) line(host, W, H || 180);
    else if (host.hasAttribute('data-ranks')) ranks(host);
    else if (host.hasAttribute('data-donut')) donut(host, W, H || W);
  }

  function draw() {
    /* Re-read first: a theme flip changes the variables, not the markup, and
       every colour below is baked into an SVG attribute at draw time. */
    PAL = readPalette();
    INK = PAL.INK; SIGNAL = PAL.SIGNAL; SIGNAL_D = PAL.SIGNAL_D;
    DIVIDER = PAL.DIVIDER; MUTED = PAL.MUTED;

    const hosts = document.querySelectorAll(SEL);
    for (let i = 0; i < hosts.length; i++) render(hosts[i]);
  }

  let pending = 0;
  function schedule() {
    if (pending) return;
    pending = requestAnimationFrame(function () { pending = 0; draw(); });
  }

  new MutationObserver(schedule)
    .observe(document.documentElement, { childList: true, subtree: true });

  /* The theme control flips data-theme on <html>. That is an attribute change,
     which the observer above does not watch, so the charts kept the old ink
     until something else happened to trigger a redraw. */
  new MutationObserver(schedule)
    .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  window.addEventListener('resize', schedule);
  window.addEventListener('pageshow', schedule);
  window.addEventListener('popstate', schedule);
  document.addEventListener('DOMContentLoaded', schedule);

  window.__bcnCharts = { draw: draw };
  draw();
})();
