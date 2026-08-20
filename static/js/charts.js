/* ===========================================================================
   Bacaan — charts
   Small hand-drawn SVG renderers. No chart library: the shapes here are simple
   and a dependency would cost more than it returns.
   =========================================================================== */
(function () {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const INK = '#07090B';
  const SIGNAL = '#FF6A00';
  const DIVIDER = '#D7DCE2';
  const MUTED = '#8B939E';

  function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    Object.keys(attrs || {}).forEach((k) => node.setAttribute(k, attrs[k]));
    return node;
  }

  function svgRoot(host, w, h) {
    const svg = el('svg', {
      viewBox: '0 0 ' + w + ' ' + h,
      width: '100%', height: '100%',
      preserveAspectRatio: 'none', role: 'img',
    });
    host.textContent = '';
    host.appendChild(svg);
    return svg;
  }

  /* ── Column chart ─────────────────────────────────────────────────────── */
  function bars(host) {
    let data;
    try { data = JSON.parse(host.getAttribute('data-bars')); } catch (e) { return; }
    if (!data || !data.length) return;

    const W = 640, H = 170, padL = 34, padR = 6, padT = 12, padB = 26;
    const svg = svgRoot(host, W, H);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('aria-label', 'Units completed per day over the last two weeks');

    const max = Math.max.apply(null, data.map((d) => d.v)) || 1;
    const nice = niceMax(max);
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const slot = plotW / data.length;
    const barW = Math.max(6, slot * 0.56);

    [0, 0.5, 1].forEach((f) => {
      const y = padT + plotH - plotH * f;
      svg.appendChild(el('line', {
        x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: DIVIDER, 'stroke-width': 1,
        'stroke-dasharray': f === 0 ? '0' : '3 4',
      }));
      const t = el('text', {
        x: padL - 7, y: y + 3.5, 'text-anchor': 'end',
        'font-size': 9, fill: MUTED, 'font-family': 'IBM Plex Mono, monospace',
      });
      t.textContent = Math.round(nice * f);
      svg.appendChild(t);
    });

    data.forEach((d, i) => {
      const h = d.v > 0 ? Math.max(2, (d.v / nice) * plotH) : 0;
      const x = padL + i * slot + (slot - barW) / 2;
      const y = padT + plotH - h;
      if (h > 0) {
        const r = el('rect', {
          x: x, y: y, width: barW, height: h, rx: 2,
          fill: i === data.length - 1 ? SIGNAL : INK,
          opacity: i === data.length - 1 ? 1 : 0.82,
        });
        const title = el('title');
        title.textContent = shortDate(d.d) + ': ' + d.v;
        r.appendChild(title);
        svg.appendChild(r);
      }
      if (i % 3 === 0 || i === data.length - 1) {
        const label = el('text', {
          x: padL + i * slot + slot / 2, y: H - 8, 'text-anchor': 'middle',
          'font-size': 9, fill: MUTED, 'font-family': 'IBM Plex Mono, monospace',
        });
        label.textContent = shortDate(d.d);
        svg.appendChild(label);
      }
    });
  }

  /* ── Sparkline / trend line ───────────────────────────────────────────── */
  function line(host) {
    let data;
    try { data = JSON.parse(host.getAttribute('data-line')); } catch (e) { return; }
    if (!data || data.length < 2) return;

    const W = 640, H = 180, padL = 32, padR = 8, padT = 14, padB = 24;
    const svg = svgRoot(host, W, H);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('aria-label', host.getAttribute('data-label') || 'Trend');

    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const step = plotW / (data.length - 1);

    [0, 25, 50, 75, 100].forEach((v) => {
      const y = padT + plotH - (v / 100) * plotH;
      svg.appendChild(el('line', {
        x1: padL, y1: y, x2: W - padR, y2: y,
        stroke: DIVIDER, 'stroke-width': 1,
        'stroke-dasharray': v === 0 ? '0' : '3 4',
      }));
      const t = el('text', {
        x: padL - 6, y: y + 3.5, 'text-anchor': 'end',
        'font-size': 9, fill: MUTED, 'font-family': 'IBM Plex Mono, monospace',
      });
      t.textContent = v;
      svg.appendChild(t);
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

    pts.forEach((p, i) => {
      const c = el('circle', {
        cx: p[0], cy: p[1], r: i === pts.length - 1 ? 4 : 2.4,
        fill: i === pts.length - 1 ? SIGNAL : '#FFFFFF',
        stroke: SIGNAL, 'stroke-width': 1.6,
      });
      const title = el('title');
      title.textContent = (data[i].t || '') + ': ' + data[i].v + '%';
      c.appendChild(title);
      svg.appendChild(c);
    });

    const first = el('text', {
      x: padL, y: H - 7, 'font-size': 9, fill: MUTED,
      'font-family': 'IBM Plex Mono, monospace',
    });
    first.textContent = data[0].t || '';
    svg.appendChild(first);

    const last = el('text', {
      x: W - padR, y: H - 7, 'text-anchor': 'end', 'font-size': 9, fill: MUTED,
      'font-family': 'IBM Plex Mono, monospace',
    });
    last.textContent = data[data.length - 1].t || '';
    svg.appendChild(last);
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
  function donut(host) {
    let data;
    try { data = JSON.parse(host.getAttribute('data-donut')); } catch (e) { return; }
    if (!data || !data.length) return;
    const total = data.reduce((a, d) => a + d.v, 0);
    if (!total) return;

    const size = 148, r = 58, cx = size / 2, cy = size / 2, sw = 18;
    const svg = svgRoot(host, size, size);
    svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    svg.setAttribute('aria-label', host.getAttribute('data-label') || 'Distribution');

    let offset = 0;
    const circumference = 2 * Math.PI * r;
    data.forEach((d) => {
      const frac = d.v / total;
      const arc = el('circle', {
        cx: cx, cy: cy, r: r, fill: 'none', stroke: d.c,
        'stroke-width': sw,
        'stroke-dasharray': (frac * circumference - 2) + ' ' + circumference,
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
      x: cx, y: cy - 2, 'text-anchor': 'middle', 'font-size': 26,
      'font-weight': 700, fill: INK, 'font-family': 'Space Grotesk, sans-serif',
    });
    total_t.textContent = total;
    svg.appendChild(total_t);

    const cap = el('text', {
      x: cx, y: cy + 15, 'text-anchor': 'middle', 'font-size': 9,
      fill: MUTED, 'font-family': 'IBM Plex Mono, monospace',
      'letter-spacing': '1.4',
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

  function draw() {
    document.querySelectorAll('[data-bars]').forEach(bars);
    document.querySelectorAll('[data-line]').forEach(line);
    document.querySelectorAll('[data-ranks]').forEach(ranks);
    document.querySelectorAll('[data-donut]').forEach(donut);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', draw);
  } else {
    draw();
  }
  window.addEventListener('resize', () => {
    clearTimeout(window.__mzChartTimer);
    window.__mzChartTimer = setTimeout(draw, 200);
  });
})();
