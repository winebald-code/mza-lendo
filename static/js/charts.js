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
      width: '100%',
      height: '100%',
      preserveAspectRatio: 'xMidYMid meet',
      role: 'img'
    });

    host.replaceChildren(svg);
    return svg;
  }

  function bars(host) {
    let data;

    try {
      data = JSON.parse(host.getAttribute('data-bars') || '[]');
    } catch (e) {
      return;
    }

    if (!Array.isArray(data) || !data.length) return;

    const W = 640;
    const H = 170;
    const padL = 34;
    const padR = 6;
    const padT = 12;
    const padB = 26;

    const svg = svgRoot(host, W, H);
    svg.setAttribute(
      'aria-label',
      'Units completed per day over the last two weeks'
    );

    const values = data.map((d) => Number(d.v) || 0);
    const max = Math.max(...values, 1);
    const nice = niceMax(max);

    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const slot = plotW / data.length;
    const barW = Math.max(6, slot * 0.56);

    [0, 0.5, 1].forEach((f) => {
      const y = padT + plotH - plotH * f;

      svg.appendChild(el('line', {
        x1: padL,
        y1: y,
        x2: W - padR,
        y2: y,
        stroke: DIVIDER,
        'stroke-width': 1,
        'stroke-dasharray': f === 0 ? '0' : '3 4'
      }));

      const t = el('text', {
        x: padL - 7,
        y: y + 3.5,
        'text-anchor': 'end',
        'font-size': 9,
        fill: MUTED,
        'font-family': 'IBM Plex Mono, monospace'
      });

      t.textContent = Math.round(nice * f);
      svg.appendChild(t);
    });

    data.forEach((d, i) => {
      const value = Number(d.v) || 0;
      const h = value > 0
        ? Math.max(2, (value / nice) * plotH)
        : 0;

      const x = padL + i * slot + (slot - barW) / 2;
      const y = padT + plotH - h;

      if (h > 0) {
        const r = el('rect', {
          x,
          y,
          width: barW,
          height: h,
          rx: 2,
          fill: i === data.length - 1 ? SIGNAL : INK,
          opacity: i === data.length - 1 ? 1 : 0.82
        });

        const title = document.createElementNS(NS, 'title');
        title.textContent = shortDate(d.d) + ': ' + value;
        r.appendChild(title);

        svg.appendChild(r);
      }

      if (i % 3 === 0 || i === data.length - 1) {
        const label = el('text', {
          x: padL + i * slot + slot / 2,
          y: H - 8,
          'text-anchor': 'middle',
          'font-size': 9,
          fill: MUTED,
          'font-family': 'IBM Plex Mono, monospace'
        });

        label.textContent = shortDate(d.d);
        svg.appendChild(label);
      }
    });
  }

  function line(host) {
    let data;

    try {
      data = JSON.parse(host.getAttribute('data-line') || '[]');
    } catch (e) {
      return;
    }

    if (!Array.isArray(data) || data.length < 2) return;

    const W = 640;
    const H = 180;
    const padL = 32;
    const padR = 8;
    const padT = 14;
    const padB = 24;

    const svg = svgRoot(host, W, H);
    svg.setAttribute(
      'aria-label',
      host.getAttribute('data-label') || 'Trend'
    );

    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const step = plotW / (data.length - 1);

    [0, 25, 50, 75, 100].forEach((v) => {
      const y = padT + plotH - (v / 100) * plotH;

      svg.appendChild(el('line', {
        x1: padL,
        y1: y,
        x2: W - padR,
        y2: y,
        stroke: DIVIDER,
        'stroke-width': 1,
        'stroke-dasharray': v === 0 ? '0' : '3 4'
      }));

      const t = el('text', {
        x: padL - 6,
        y: y + 3.5,
        'text-anchor': 'end',
        'font-size': 9,
        fill: MUTED,
        'font-family': 'IBM Plex Mono, monospace'
      });

      t.textContent = v;
      svg.appendChild(t);
    });

    const pts = data.map((d, i) => {
      const value = Math.max(
        0,
        Math.min(100, Number(d.v) || 0)
      );

      return [
        padL + i * step,
        padT + plotH - (value / 100) * plotH
      ];
    });

    const baseY = padT + plotH;

    const area =
      'M' + pts[0][0] + ',' + baseY +
      ' L' +
      pts.map((p) => p[0] + ',' + p[1]).join(' L') +
      ' L' +
      pts[pts.length - 1][0] + ',' + baseY +
      ' Z';

    svg.appendChild(el('path', {
      d: area,
      fill: SIGNAL,
      opacity: 0.1
    }));

    svg.appendChild(el('path', {
      d: 'M' + pts.map((p) => p[0] + ',' + p[1]).join(' L'),
      fill: 'none',
      stroke: SIGNAL,
      'stroke-width': 2.2,
      'stroke-linejoin': 'round',
      'stroke-linecap': 'round'
    }));

    pts.forEach((p, i) => {
      const c = el('circle', {
        cx: p[0],
        cy: p[1],
        r: i === pts.length - 1 ? 4 : 2.4,
        fill: i === pts.length - 1 ? SIGNAL : '#FFFFFF',
        stroke: SIGNAL,
        'stroke-width': 1.6
      });

      const title = document.createElementNS(NS, 'title');
      title.textContent =
        (data[i].t || '') + ': ' + (Number(data[i].v) || 0) + '%';

      c.appendChild(title);
      svg.appendChild(c);
    });

    const first = el('text', {
      x: padL,
      y: H - 7,
      'font-size': 9,
      fill: MUTED,
      'font-family': 'IBM Plex Mono, monospace'
    });

    first.textContent = data[0].t || '';
    svg.appendChild(first);

    const last = el('text', {
      x: W - padR,
      y: H - 7,
      'text-anchor': 'end',
      'font-size': 9,
      fill: MUTED,
      'font-family': 'IBM Plex Mono, monospace'
    });

    last.textContent = data[data.length - 1].t || '';
    svg.appendChild(last);
  }

  function ranks(host) {
    let data;

    try {
      data = JSON.parse(host.getAttribute('data-ranks') || '[]');
    } catch (e) {
      return;
    }

    if (!Array.isArray(data) || !data.length) return;

    const max = Math.max(
      ...data.map((d) => Number(d[1]) || 0),
      1
    );

    host.replaceChildren();

    data.forEach((d) => {
      const value = Number(d[1]) || 0;

      const row = document.createElement('div');
      row.className =
        'grid grid-cols-[minmax(0,1fr)_auto] gap-3 items-center py-[7px]';

      const left = document.createElement('div');
      left.className = 'min-w-0';

      const label = document.createElement('p');
      label.className = 'text-[12.5px] text-ink m-0 truncate';
      label.textContent = d[0];

      const track = document.createElement('div');
      track.className =
        'mt-1 h-[5px] rounded-full bg-divider overflow-hidden';

      const fill = document.createElement('div');
      fill.className = 'h-full rounded-full bg-ink';
      fill.style.width = Math.max(3, (value / max) * 100) + '%';

      track.appendChild(fill);
      left.appendChild(label);
      left.appendChild(track);

      const valueNode = document.createElement('span');
      valueNode.className =
        'font-mono tabular-nums text-[12px] font-semibold text-slate-500';
      valueNode.textContent = value.toLocaleString();

      row.appendChild(left);
      row.appendChild(valueNode);

      host.appendChild(row);
    });
  }

  function donut(host) {
    let data;

    try {
      data = JSON.parse(host.getAttribute('data-donut') || '[]');
    } catch (e) {
      return;
    }

    if (!Array.isArray(data) || !data.length) return;

    const total = data.reduce(
      (sum, d) => sum + (Number(d.v) || 0),
      0
    );

    if (!total) return;

    const size = 148;
    const r = 58;
    const cx = size / 2;
    const cy = size / 2;
    const sw = 18;

    const svg = svgRoot(host, size, size);
    svg.setAttribute(
      'aria-label',
      host.getAttribute('data-label') || 'Distribution'
    );

    let offset = 0;
    const circumference = 2 * Math.PI * r;

    data.forEach((d) => {
      const value = Number(d.v) || 0;
      const frac = value / total;

      if (frac <= 0) return;

      const dash = Math.max(
        0,
        frac * circumference - 2
      );

      const arc = el('circle', {
        cx,
        cy,
        r,
        fill: 'none',
        stroke: d.c || SIGNAL,
        'stroke-width': sw,
        'stroke-dasharray': dash + ' ' + circumference,
        'stroke-dashoffset': -offset * circumference,
        transform: 'rotate(-90 ' + cx + ' ' + cy + ')',
        'stroke-linecap': 'butt'
      });

      const title = document.createElementNS(NS, 'title');
      title.textContent = d.k + ': ' + value;

      arc.appendChild(title);
      svg.appendChild(arc);

      offset += frac;
    });

    const totalText = el('text', {
      x: cx,
      y: cy - 2,
      'text-anchor': 'middle',
      'font-size': 26,
      'font-weight': 700,
      fill: INK,
      'font-family': 'Space Grotesk, sans-serif'
    });

    totalText.textContent = total;
    svg.appendChild(totalText);

    const cap = el('text', {
      x: cx,
      y: cy + 15,
      'text-anchor': 'middle',
      'font-size': 9,
      fill: MUTED,
      'font-family': 'IBM Plex Mono, monospace',
      'letter-spacing': '1.4'
    });

    cap.textContent =
      (host.getAttribute('data-total-label') || 'TOTAL').toUpperCase();

    svg.appendChild(cap);
  }

  function niceMax(v) {
    if (v <= 5) return 5;

    const mag = Math.pow(
      10,
      Math.floor(Math.log10(v))
    );

    return Math.ceil(v / mag) * mag;
  }

  function shortDate(iso) {
    if (!iso) return '';

    const value = String(iso);

    const d = new Date(
      value.length === 10
        ? value + 'T00:00:00'
        : value
    );

    if (isNaN(d.getTime())) return value;

    return d.toLocaleDateString(undefined, {
      day: 'numeric',
      month: 'short'
    });
  }

  function draw() {
    document.querySelectorAll('[data-bars]').forEach(bars);
    document.querySelectorAll('[data-line]').forEach(line);
    document.querySelectorAll('[data-ranks]').forEach(ranks);
    document.querySelectorAll('[data-donut]').forEach(donut);
  }

  function redrawCharts() {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        draw();
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', redrawCharts, {
      once: true
    });
  } else {
    redrawCharts();
  }

  window.addEventListener('load', redrawCharts);
  window.addEventListener('pageshow', redrawCharts);

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) redrawCharts();
  });

  window.addEventListener('resize', () => {
    clearTimeout(window.__mzChartTimer);
    window.__mzChartTimer = setTimeout(redrawCharts, 200);
  });
})();
