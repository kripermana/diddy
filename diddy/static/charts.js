/* Diddy charts - grafik SVG tanpa dependency, mengikuti warna tema.
   (c) 2026 kripermana, MIT License */
'use strict';
const Charts = (() => {
  const NS = 'http://www.w3.org/2000/svg';
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const palette = () => [css('--primary'), css('--s-lease'), css('--s-dns'), css('--amber'), css('--s-unmanaged'),
    css('--ok'), css('--danger'), css('--muted')];
  const escT = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  let uid = 0;

  function fmt(v) {
    if (v === null || v === undefined || isNaN(v)) return '-';
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(1).replace(/\.0$/, '') + 'G';
    if (a >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    if (a >= 1e4) return (v / 1e3).toFixed(1).replace(/\.0$/, '') + 'k';
    if (a >= 100) return Math.round(v).toLocaleString();
    if (a >= 10) return (Math.round(v * 10) / 10).toString();
    return (Math.round(v * 100) / 100).toString();
  }
  function timeLabel(t, span) {
    const d = new Date(t * 1000);
    const hm = d.toTimeString().slice(0, 5);
    if (span > 2 * 86400) return d.toLocaleDateString(undefined, { weekday: 'short' }) + ' ' + hm;
    return hm;
  }
  function niceMax(v) {
    if (!v || v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }
  function svg(w, h) {
    const s = document.createElementNS(NS, 'svg');
    s.setAttribute('width', w); s.setAttribute('height', h); s.setAttribute('viewBox', `0 0 ${w} ${h}`);
    s.setAttribute('class', 'chart');
    return s;
  }
  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function tip(host) {
    let t = host.querySelector('.chart-tip');
    if (!t) { t = document.createElement('div'); t.className = 'chart-tip'; host.appendChild(t); }
    return t;
  }

  /* Grafik garis/area. data: {points:[{t,v}], series:[{name,values,color}], span, unit} */
  function area(host, data, opts = {}) {
    host.innerHTML = '';
    host.style.position = 'relative';
    const w = Math.max(240, host.clientWidth), h = opts.height || 220;
    const pad = { l: 44, r: 12, t: 12, b: 26 };
    const pts = data.points || [];
    const series = data.series || [{ name: data.name || '', values: pts.map(p => p.v), color: css('--primary') }];
    const n = pts.length;
    if (!n) { host.innerHTML = `<div class="chart-empty">${escT(opts.empty || 'No data yet')}</div>`; return; }
    const all = series.flatMap(s => s.values.filter(v => v !== null && v !== undefined));
    const max = niceMax(Math.max(0, ...all) * 1.05);
    const X = i => pad.l + (n === 1 ? 0 : i * (w - pad.l - pad.r) / (n - 1));
    const Y = v => pad.t + (h - pad.t - pad.b) * (1 - v / max);
    const s = svg(w, h);
    const defs = el('defs', {}, s);
    for (let g = 0; g <= 4; g++) {
      const v = max * g / 4, y = Y(v);
      el('line', { x1: pad.l, x2: w - pad.r, y1: y, y2: y, class: 'grid-line' }, s);
      const tx = el('text', { x: pad.l - 6, y: y + 4, class: 'axis', 'text-anchor': 'end' }, s); tx.textContent = fmt(v);
    }
    const ticks = Math.min(6, n);
    for (let k = 0; k < ticks; k++) {
      const i = Math.round(k * (n - 1) / Math.max(1, ticks - 1));
      const tx = el('text', { x: X(i), y: h - 8, class: 'axis', 'text-anchor': k === 0 ? 'start' : k === ticks - 1 ? 'end' : 'middle' }, s);
      tx.textContent = timeLabel(pts[i].t, data.span || 86400);
    }
    series.forEach((se, si) => {
      const id = 'g' + (++uid);
      const color = se.color || palette()[si % 8];
      const gr = el('linearGradient', { id, x1: 0, x2: 0, y1: 0, y2: 1 }, defs);
      el('stop', { offset: '0%', 'stop-color': color, 'stop-opacity': si ? 0.12 : 0.35 }, gr);
      el('stop', { offset: '100%', 'stop-color': color, 'stop-opacity': 0 }, gr);
      let line = '', fill = '', started = false, first = 0;
      se.values.forEach((v, i) => {
        if (v === null || v === undefined) return;
        line += (started ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(v).toFixed(1);
        if (!started) first = i;
        started = true;
      });
      if (!started) return;
      const lastIdx = se.values.map((v, i) => (v === null || v === undefined) ? -1 : i).reduce((a, b) => Math.max(a, b));
      fill = line + `L${X(lastIdx).toFixed(1)},${Y(0)}L${X(first).toFixed(1)},${Y(0)}Z`;
      if (opts.area !== false) el('path', { d: fill, fill: `url(#${id})` }, s);
      el('path', { d: line, fill: 'none', stroke: color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }, s);
    });
    const cross = el('line', { y1: pad.t, y2: h - pad.b, class: 'crosshair', visibility: 'hidden' }, s);
    const dots = series.map((se, si) => el('circle', { r: 4, fill: se.color || palette()[si % 8], stroke: css('--panel'), 'stroke-width': 2, visibility: 'hidden' }, s));
    const hit = el('rect', { x: pad.l, y: pad.t, width: w - pad.l - pad.r, height: h - pad.t - pad.b, fill: 'transparent' }, s);
    const tp = tip(host);
    host.appendChild(s); host.appendChild(tp);
    hit.addEventListener('mousemove', ev => {
      const r = s.getBoundingClientRect();
      const i = Math.max(0, Math.min(n - 1, Math.round((ev.clientX - r.left - pad.l) / ((w - pad.l - pad.r) / Math.max(1, n - 1)))));
      cross.setAttribute('x1', X(i)); cross.setAttribute('x2', X(i)); cross.setAttribute('visibility', 'visible');
      let rows = '';
      series.forEach((se, si) => {
        const v = se.values[i];
        if (v === null || v === undefined) { dots[si].setAttribute('visibility', 'hidden'); return; }
        dots[si].setAttribute('cx', X(i)); dots[si].setAttribute('cy', Y(v)); dots[si].setAttribute('visibility', 'visible');
        rows += `<div><i style="background:${se.color || palette()[si % 8]}"></i>${escT(se.name)} <b>${fmt(v)}${escT(opts.unit || '')}</b></div>`;
      });
      tp.innerHTML = `<div class="tt">${new Date(pts[i].t * 1000).toLocaleString()}</div>${rows}`;
      tp.style.display = 'block';
      const x = X(i) + 12, tw = tp.offsetWidth;
      tp.style.left = (x + tw > w ? X(i) - tw - 12 : x) + 'px'; tp.style.top = pad.t + 'px';
    });
    hit.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); dots.forEach(d => d.setAttribute('visibility', 'hidden')); tp.style.display = 'none'; });
  }

  /* Grafik batang vertikal. data: {items:[{label,v,title}]} */
  function bars(host, data, opts = {}) {
    host.innerHTML = ''; host.style.position = 'relative';
    const w = Math.max(240, host.clientWidth), h = opts.height || 220;
    const pad = { l: 44, r: 8, t: 12, b: 26 };
    const items = data.items || [];
    if (!items.length || !items.some(i => i.v)) { host.innerHTML = `<div class="chart-empty">${escT(opts.empty || 'No data yet')}</div>`; return; }
    const max = niceMax(Math.max(...items.map(i => i.v || 0)) * 1.05);
    const bw = (w - pad.l - pad.r) / items.length;
    const Y = v => pad.t + (h - pad.t - pad.b) * (1 - v / max);
    const s = svg(w, h);
    for (let g = 0; g <= 4; g++) {
      const v = max * g / 4, y = Y(v);
      el('line', { x1: pad.l, x2: w - pad.r, y1: y, y2: y, class: 'grid-line' }, s);
      const tx = el('text', { x: pad.l - 6, y: y + 4, class: 'axis', 'text-anchor': 'end' }, s); tx.textContent = fmt(v);
    }
    const every = Math.ceil(items.length / 8);
    const tp = tip(host);
    items.forEach((it, i) => {
      const x = pad.l + i * bw + bw * 0.15, bh = Math.max(0, Y(0) - Y(it.v || 0));
      const r = el('rect', { x, y: Y(it.v || 0), width: Math.max(1, bw * 0.7), height: bh, rx: Math.min(4, bw * 0.2), fill: opts.color || css('--primary'), class: 'cbar' }, s);
      r.addEventListener('mousemove', () => {
        tp.innerHTML = `<div class="tt">${escT(it.title || it.label)}</div><div><b>${fmt(it.v)}</b> ${escT(opts.unit || '')}</div>`;
        tp.style.display = 'block';
        const left = x + bw; tp.style.left = (left + tp.offsetWidth > w ? x - tp.offsetWidth : left) + 'px'; tp.style.top = Math.max(0, Y(it.v || 0) - 10) + 'px';
      });
      r.addEventListener('mouseleave', () => { tp.style.display = 'none'; });
      if (i % every === 0) {
        const tx = el('text', { x: x + bw * 0.35, y: h - 8, class: 'axis', 'text-anchor': 'middle' }, s); tx.textContent = it.label;
      }
    });
    host.appendChild(s); host.appendChild(tp);
  }

  /* Donut dengan legenda. data: {items:[{label,v,color}], center} */
  function donut(host, data, opts = {}) {
    const items = (data.items || []).filter(i => i.v > 0);
    const total = items.reduce((a, i) => a + i.v, 0);
    if (!total) { host.innerHTML = `<div class="chart-empty">${escT(opts.empty || 'No data yet')}</div>`; return; }
    const pal = palette();
    const size = opts.size || 150, r = size / 2 - 8, cx = size / 2, cy = size / 2, sw = opts.thickness || 20;
    const C = 2 * Math.PI * r;
    let off = 0, arcs = '';
    items.forEach((it, i) => {
      const len = C * it.v / total;
      arcs += `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${it.color || pal[i % 8]}" stroke-width="${sw}"
        stroke-dasharray="${Math.max(0, len - 1.5)} ${C - Math.max(0, len - 1.5)}" stroke-dashoffset="${-off}" transform="rotate(-90 ${cx} ${cy})"><title>${escT(it.label)}: ${fmt(it.v)}</title></circle>`;
      off += len;
    });
    host.innerHTML = `<div class="donut"><svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
      <circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--line-2)" stroke-width="${sw}"/>${arcs}
      <text x="${cx}" y="${cy - 2}" text-anchor="middle" class="donut-total">${fmt(total)}</text>
      <text x="${cx}" y="${cy + 16}" text-anchor="middle" class="axis">${escT(data.center || 'total')}</text></svg>
      <ul class="legend-list">${items.map((it, i) => { const inner = `<i style="background:${it.color || pal[i % 8]}"></i><span>${escT(it.label)}</span><b>${fmt(it.v)}</b><em>${(it.v * 100 / total).toFixed(1)}%</em>`;
        return it.href ? `<li><a class="legend-link" href="${escT(it.href)}" title="Show details">${inner}</a></li>` : `<li>${inner}</li>`; }).join('')}</ul></div>`;
  }

  /* Gauge setengah lingkaran. pct 0-100 */
  function gauge(host, pct, opts = {}) {
    const p = Math.max(0, Math.min(100, pct || 0));
    const w = 220, h = 130, cx = 110, cy = 115, r = 90, sw = 18;
    const color = p >= (opts.crit || 90) ? css('--danger') : p >= (opts.warn || 70) ? css('--amber') : css('--ok');
    const arc = (a0, a1) => {
      const x0 = cx + r * Math.cos(Math.PI * (1 - a0)), y0 = cy - r * Math.sin(Math.PI * (1 - a0));
      const x1 = cx + r * Math.cos(Math.PI * (1 - a1)), y1 = cy - r * Math.sin(Math.PI * (1 - a1));
      return `M${x0.toFixed(1)},${y0.toFixed(1)} A${r},${r} 0 0 1 ${x1.toFixed(1)},${y1.toFixed(1)}`;
    };
    host.innerHTML = `<div class="gauge"><svg width="100%" viewBox="0 0 ${w} ${h}" style="max-width:260px">
      <path d="${arc(0, 1)}" fill="none" stroke="var(--line-2)" stroke-width="${sw}" stroke-linecap="round"/>
      ${p > 0 ? `<path d="${arc(0, Math.max(0.01, p / 100))}" fill="none" stroke="${color}" stroke-width="${sw}" stroke-linecap="${p < 6 ? 'butt' : 'round'}"/>` : ''}
      <text x="${cx}" y="${cy - 18}" text-anchor="middle" class="gauge-val">${p.toFixed(p < 10 ? 1 : 0)}%</text></svg>
      <div class="gauge-sub">${opts.sub || ''}</div></div>`;
  }

  /* Sparkline kecil untuk KPI */
  function spark(values, color) {
    const v = values.filter(x => x !== null && x !== undefined);
    if (v.length < 2) return '';
    const w = 110, h = 32, max = Math.max(...v) || 1, min = Math.min(...v);
    const X = i => i * w / (values.length - 1), Y = x => h - 3 - (h - 6) * ((x - min) / ((max - min) || 1));
    let d = '', st = false;
    values.forEach((x, i) => { if (x === null || x === undefined) return; d += (st ? 'L' : 'M') + X(i).toFixed(1) + ',' + Y(x).toFixed(1); st = true; });
    return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="${d}" fill="none" stroke="${color || 'var(--primary)'}" stroke-width="1.8" stroke-linejoin="round"/></svg>`;
  }

  return { area, bars, donut, gauge, spark, fmt, palette };
})();
