/* ===========================================================================
   Bacaan — charts
   Small hand-drawn SVG renderers. No chart library: the shapes here are simple
   and a dependency would cost more than it returns.

   Two constraints this file has to hold:

   1. The script executes once per document. Anything that swaps page content
      without a document load leaves the new chart hosts empty, because
      DOMContentLoaded never fires again. Drawing is therefore driven by a
      MutationObserver over the document, with a signature guard so a host is
      only rebuilt when its data or its size actually changed.

   2. Charts are drawn at the host's real pixel size instead of a fixed 640px
      viewBox. A fixed viewBox scaled to fit a 340px column shrinks 9px axis
      text to under 5px and leaves the chart floating in dead vertical space.
      At 1:1 the labels stay at their stated size and nothing overflows the
      column, so no chart needs a horizontal scroll.
   =========================================================================== */
(function () {
  'use strict';

  if (window.__bcnCharts) { window.__bcnCharts.draw(); return; }

  const NS = 'http://www.w3.org/2000/svg';
  const INK = '#07090B';
  const SIGNAL = '#FF6A00';
  const DIVIDER = '#D7DCE2';
  const MUTED = '#8B939E';
  const MONO = 'IBM Plex Mono, monospace';
  const DISPLAY = 'Space Grotesk, sans-serif';
  const SEL = '[data-bars],[data-line],[data-ranks],[data-donut]';
  const FS = 10;         /* axis label size, real px */
  const CW = FS * 0.62;  /* mono character width at that size */

  function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach((k) => node.setAttribute(k, attrs[k]));
    return node;
  }

  function tick(svg, x, y, s, anchor) {
    const t = el('text', {
      x: x, y: y, 'text-anchor': anchor || 'middle',
      'font-size': FS, fill: MUTED, 'font-family': MONO,
    });
    t.textContent = s;
    svg.appendChild(t);
    return t;
  }

  function svgRoot(host, w, h, aria) {
    const svg = el('svg', {
      viewBox: '0 0 ' + w + ' ' + h,
      width: '100%', height: '100%',
      preserveAspectRatio: 'xMidYMid meet', role: 'img',
    });
    if (aria) svg.setAttribute('aria-label', aria);
    host.textContent = '';
    host.appendChild(svg);
    return svg;
  }

  /* ── Column chart ─────────────────────────────────────────────────────── */
  function bars(host, W, H) {
    let data;
    try { data = JSON.parse(host.getAttribute('data-bars')); } catch (e) { return; }
    if (!data || !data.length) return;

    const padL = 32, padR = 4, padT = 12, padB = 22;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    if (plotW < 60 || plotH < 40) return;

    const svg = svgRoot(host, W, H,
      host.getAttribute('data-label') || 'Units completed per day over the last two weeks');

    const nice = niceMax(Math.max.apply(null, data.map((d) => d.v)) || 1);
    const slot = plotW / data.length;
    const barW = Math.max(3, Math.min(28, slot * 0.56));

    [0, 0.5, 1].forEach((f) => {
      const y = padT + plotH - plotH * f;
      svg.appendChild(el('line', {
        x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: DIVIDER, 'stroke-width': 1,
        'stroke-dasharray': f === 0 ? '0' : '3 4',
      }));
      tick(svg, padL - 6, y + 3.5, String(Math.round(nice * f)), 'end');
    });

    data.forEach((d, i) => {
      const h = d.v > 0 ? Math.max(2, (d.v / nice) * plotH) : 0;
      if (!h) return;
      const rect = el('rect', {
        x: padL + i * slot + (slot - barW) / 2,
        y: padT + plotH - h,
        width: barW, height: h, rx: 2,
        fill: i === data.length - 1 ? SIGNAL : INK,
        opacity: i === data.length - 1 ? 1 : 0.82,
      });
      const title = el('title');
      title.textContent = shortDate(d.d) + ': ' + d.v;
      rect.appendChild(title);
      svg.appendChild(rect);
    });

    /* Date labels, newest first: draw whatever fits without touching the label
       to its right, and clamp the end ones inside the box. A fixed "every 3rd"
       step either collides or leaves gaps once the column narrows. */
    let taken = W + 8;
    for (let i = data.length - 1; i >= 0; i--) {
      const s = shortDate(data[i].d);
      const w = s.length * CW;
      const c = padL + i * slot + slot / 2;
      let anchor = 'middle', x = c, left = c - w / 2;
      if (c + w / 2 > W - 2) { anchor = 'end'; x = W - 2; left = x - w; }
      else if (left < 2) { anchor = 'start'; x = 2; left = 2; }
      if (left + w > taken - 8) continue;
      tick(svg, x, H - 6, s, anchor);
      taken = left;
    }
  }

  /* ── Sparkline / trend line ───────────────────────────────────────────── */
  function line(host, W, H) {
    let data;
    try { data = JSON.parse(host.getAttribute('data-line')); } catch (e) { return; }
    if (!data || data.length < 2) return;

    const padL = 30, padR = 6, padT = 12, padB = 22;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    if (plotW < 60 || plotH < 40) return;

    const svg = svgRoot(host, W, H, host.getAttribute('data-label') || 'Trend');
    const step = plotW / (data.length - 1);

    [0, 25, 50, 75, 100].forEach((v) => {
      const y = padT + plotH - (v / 100) * plotH;
      svg.appendChild(el('line', {
        x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: DIVIDER, 'stroke-width': 1,
        'stroke-dasharray': v === 0 ? '0' : '3 4',
      }));
      tick(svg, padL - 6, y + 3.5, String(v), 'end');
    });

    const pts = data.map((d, i) => [
      padL + i * step,
      padT + plotH - (Math.max(0, Math.min(100, d.v)) / 100) * plotH,
    ]);

    const area = 'M' + pts[0][0] + ',' + (padT + plotH) + ' L' +
      pts.map((p) => p[0] + ',' + p[1]).join(' L') +
      ' L' + pts[pts.length - 1][0] + ',' + (padT + plotH) + ' Z';
    svg.appendChild(el('path', { d: area, fill: SIGNAL, opacity: 0.1 }));

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

    const a = data[0].t || '';
    const b = data[data.length - 1].t || '';
    const fits = padL + a.length * CW + 10 < (W - padR) - b.length * CW;
    if (a && fits) tick(svg, padL, H - 6, a, 'start');
    if (b) tick(svg, W - padR, H - 6, b, 'end');
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
      const label = document.createElement('p');
      label.className = 'text-[12.5px] text-ink m-0 truncate';
      label.textContent = d[0];
      const track = document.createElement('div');
      track.className = 'mt-1 h-[5px] rounded-full bg-divider overflow-hidden';
      const fill = document.createElement('div');
      fill.className = 'h-full rounded-full bg-ink';
      fill.style.width = Math.max(3, (d[1] / max) * 100) + '%';
      track.appendChild(fill);
      left.appendChild(label);
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

    const size = Math.round(Math.max(96, Math.min(W, H || W, 220)));
    const sw = Math.round(size * 0.12);
    const r = (size - sw) / 2 - 1;
    const cx = size / 2, cy = size / 2;
    const svg = svgRoot(host, size, size, host.getAttribute('data-label') || 'Distribution');

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

    const total_t = el('text', {
      x: cx, y: cy - size * 0.013, 'text-anchor': 'middle',
      'font-size': Math.round(size * 0.175), 'font-weight': 700,
      fill: INK, 'font-family': DISPLAY,
    });
    total_t.textContent = total;
    svg.appendChild(total_t);

    const cap = el('text', {
      x: cx, y: cy + size * 0.10, 'text-anchor': 'middle',
      'font-size': Math.max(8, Math.round(size * 0.062)),
      fill: MUTED, 'font-family': MONO, 'letter-spacing': '1.4',
    });
    cap.textContent = (host.getAttribute('data-total-label') || 'TOTAL').toUpperCase();
    svg.appendChild(cap);
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

  function render(host) {
    const box = host.getBoundingClientRect();
    const W = Math.round(box.width);
    const H = Math.round(box.height);
    if (W < 80) return;                       /* hidden, or not laid out yet */

    const key = host.getAttribute('data-bars') || host.getAttribute('data-line') ||
                host.getAttribute('data-ranks') || host.getAttribute('data-donut') || '';
    const sig = W + 'x' + H + '|' + key;
    if (host.__sig === sig && host.firstChild) return;
    host.__sig = sig;

    if (ro && !host.__ro) { host.__ro = 1; ro.observe(host); }

    if (host.hasAttribute('data-bars')) bars(host, W, H || 150);
    else if (host.hasAttribute('data-line')) line(host, W, H || 180);
    else if (host.hasAttribute('data-ranks')) ranks(host);
    else if (host.hasAttribute('data-donut')) donut(host, W, H || W);
  }

  function draw() {
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

  window.addEventListener('resize', schedule);
  window.addEventListener('pageshow', schedule);
  window.addEventListener('popstate', schedule);
  document.addEventListener('DOMContentLoaded', schedule);

  window.__bcnCharts = { draw: draw };
  draw();
})();
