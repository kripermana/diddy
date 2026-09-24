/* Diddy web UI - vanilla JS single page app on top of /api/v1
   Copyright (c) 2026 kripermana, MIT License */
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let ME = null, H = {};

async function api(method, path, body) {
  const opt = { method, headers: {}, credentials: 'same-origin' };
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch('/api/v1' + path, opt);
  if (r.status === 401 && path !== '/login') { showLogin(); throw new Error('Unauthorized'); }
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) { const e = new Error((data && data.error) || r.statusText); e.data = data; throw e; }
  return data;
}
function toast(msg, bad) {
  const t = document.createElement('div');
  t.className = 'toast' + (bad ? ' bad' : ''); t.textContent = msg;
  $('#toasts').appendChild(t); setTimeout(() => t.remove(), bad ? 6000 : 3000);
}
const main = html => { $('#main').innerHTML = html; };
const isAdmin = () => ME && ME.user.role === 'admin';
const adm = html => isAdmin() ? html : '';
const on = (act, fn) => { H[act] = fn; };
document.addEventListener('click', async e => {
  const el = e.target.closest('[data-act]');
  if (!el || !H[el.dataset.act]) return;
  e.preventDefault();
  try { await H[el.dataset.act](el.dataset, el); } catch (err) { if (err.message !== 'Unauthorized') toast(err.message, true); }
});

function bar(p) {
  const c = p >= 90 ? 'crit' : p >= 70 ? 'warn' : '';
  return `<span class="util"><span class="bar"><i class="${c}" style="width:${Math.min(p, 100)}%"></i></span><span class="pct">${p}%</span></span>`;
}
function table(cols, rows, o = {}) {
  if (!rows.length) return `<div class="empty">${o.empty || 'Nothing here yet.'}</div>`;
  return `<table class="grid"><thead><tr>${cols.map(c => `<th>${c.label}</th>`).join('')}${o.actions ? '<th></th>' : ''}</tr></thead><tbody>${
    rows.map(r => `<tr>${cols.map(c => `<td class="${c.cls || ''}">${c.fmt ? c.fmt(r[c.k], r) : esc(r[c.k])}</td>`).join('')}${o.actions ? `<td class="act">${o.actions(r)}</td>` : ''}</tr>`).join('')
  }</tbody></table>`;
}
function fieldHtml(f) {
  const v = f.value ?? '';
  if (f.type === 'checkbox') return `<label class="chk"><input type="checkbox" name="${f.name}" ${v ? 'checked' : ''}> ${esc(f.label)}</label>`;
  if (f.type === 'select') return `<label>${esc(f.label)}<select name="${f.name}">${f.options.map(o => {
    const [val, lab] = Array.isArray(o) ? o : [o, o];
    return `<option value="${esc(val)}" ${String(val) === String(v) ? 'selected' : ''}>${esc(lab)}</option>`;
  }).join('')}</select>${f.help ? `<small>${esc(f.help)}</small>` : ''}</label>`;
  if (f.type === 'textarea') return `<label>${esc(f.label)}<textarea name="${f.name}" rows="${f.rows || 6}" placeholder="${esc(f.placeholder || '')}">${esc(v)}</textarea>${f.help ? `<small>${esc(f.help)}</small>` : ''}</label>`;
  return `<label>${esc(f.label)}<input name="${f.name}" type="${f.type || 'text'}" value="${esc(v)}" placeholder="${esc(f.placeholder || '')}" ${f.readonly ? 'readonly' : ''}>${f.help ? `<small>${esc(f.help)}</small>` : ''}</label>`;
}
function modal(html, wide) {
  const m = document.createElement('div');
  m.className = 'modal-bg';
  m.innerHTML = `<div class="modal ${wide ? 'wide' : ''}" role="dialog" aria-modal="true">${html}</div>`;
  m.addEventListener('mousedown', e => { if (e.target === m) m.remove(); });
  m.addEventListener('keydown', e => { if (e.key === 'Escape') m.remove(); });
  document.body.appendChild(m);
  return m;
}
function form(title, fields, submit, saveLabel = 'Save') {
  const m = modal(`<h3>${esc(title)}</h3><form>${fields.map(fieldHtml).join('')}<div class="err"></div>
    <div class="btns"><button type="button" class="btn" data-close>Cancel</button><button class="btn primary">${esc(saveLabel)}</button></div></form>`);
  m.querySelector('[data-close]').onclick = () => m.remove();
  const f = m.querySelector('form');
  f.onsubmit = async e => {
    e.preventDefault();
    const data = {};
    fields.forEach(fd => {
      const el = f.querySelector(`[name="${fd.name}"]`); if (!el || fd.readonly) return;
      data[fd.name] = fd.type === 'checkbox' ? el.checked : fd.type === 'number' ? (el.value === '' ? null : Number(el.value)) : el.value.trim();
    });
    const btn = f.querySelector('.btn.primary'); btn.disabled = true;
    try { await submit(data); m.remove(); } catch (err) { m.querySelector('.err').textContent = err.message; btn.disabled = false; }
  };
  const first = f.querySelector('input:not([readonly]):not([type=checkbox]),select,textarea'); first && first.focus();
}
function head(title, sub, tools = '', crumb = '') {
  return `<div class="head"><div>${crumb ? `<div class="crumb">${crumb}</div>` : ''}<h1>${title}</h1>${sub ? `<div class="sub">${sub}</div>` : ''}</div><div class="tools">${tools}</div></div>`;
}

/* ---------------- dashboard */
/* ---------------- dashboards (Overview, DNS, DHCP), widget bisa diatur per user */
const BOARDS = [['overview', 'Overview'], ['dns', 'DNS'], ['dhcp', 'DHCP']];
const DASH = { board: 'overview', range: '24h', edit: false, layout: [], saved: [], data: {}, timer: null, drag: null };
try { DASH.range = localStorage.getItem('diddy-range') || '24h'; } catch (e) { }
const RCODE_COLOR = { NOERROR: '--ok', NXDOMAIN: '--amber', SERVFAIL: '--danger', REFUSED: '--s-unmanaged', FORMERR: '--s-dns' };
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const F = v => Charts.fmt(v);
const pct = (a, b) => b ? (a * 100 / b) : 0;
const noDns = d => !d.series.some(p => p.v) && !d.totals.last_24h;
function dnsEmpty(d) {
  const s = d.source || {};
  return `<div class="chart-empty">${s.available === false
    ? `BIND statistics are not reachable at <span class="mono">${esc(s.url)}</span>.<br>Deploy once so Diddy can enable the statistics channel.<br><small>${esc(s.error || '')}</small>`
    : 'Collecting DNS statistics. The first graph appears after a couple of samples.'}</div>`;
}
function kpis(items) {
  return `<div class="kpis">${items.map(k => `<a class="kpi" ${k.href ? `href="${k.href}"` : ''}>
    <span class="kpi-label">${esc(k.label)}</span><b class="kpi-val">${k.value}</b>
    ${k.sub ? `<span class="kpi-sub">${k.sub}</span>` : ''}${k.spark || ''}</a>`).join('')}</div>`;
}
function hbars(rows, total, colorVar) {
  if (!rows.length) return '<div class="chart-empty">No data yet</div>';
  const max = Math.max(...rows.map(r => r[1])) || 1;
  return `<div class="hbars">${rows.map(([l, v, sub]) => `<div class="hb"><span class="hb-l mono">${esc(l)}</span>
    <span class="hb-bar"><i style="width:${(v * 100 / max).toFixed(1)}%;background:var(${colorVar || '--primary'})"></i></span>
    <b>${F(v)}</b><em>${total ? pct(v, total).toFixed(1) + '%' : (sub || '')}</em></div>`).join('')}</div>`;
}
const hourLbl = t => new Date(t * 1000).toTimeString().slice(0, 2);

const WIDGETS = {
  kpi_overview: {
    title: 'Summary', src: ['dash', 'dns', 'dhcp'], render: (D, b) => {
      const c = D.dash.counts, dn = D.dns.totals, dh = D.dhcp.totals;
      b.innerHTML = kpis([
        { label: 'Networks', value: F(c.networks), href: '#/ipam' }, { label: 'Hosts', value: F(c.hosts), href: '#/hosts' },
        { label: 'DNS zones', value: F(c.zones), href: '#/dns' },
        { label: 'DNS queries (24h)', value: F(dn.last_24h), sub: `${F(dn.current_qps)} q/s now`, spark: Charts.spark(D.dns.series.map(p => p.v)), href: '#/dashboard/dns' },
        { label: 'Active leases', value: F(dh.active), sub: `${dh.pct}% of pools`, spark: Charts.spark(D.dhcp.series.map(p => p.v), 'var(--s-lease)'), href: '#/dashboard/dhcp' },
        { label: 'DHCP networks', value: F(c.dhcp_networks), href: '#/dhcp' }]);
    }
  },
  health: {
    title: 'Health', src: ['health'], render: (D, b) => {
      const h = D.health;
      b.innerHTML = `<div class="w-pill">${PILL(h.overall)} <a class="lnk" href="#/health">details</a></div><div class="health compact">${h.items.map(i =>
        `<div class="hrow"><span class="dot ${i.state === 'ok' ? 'on' : i.state === 'fail' ? 'off' : i.state === 'warn' ? 'warn' : ''}"></span><b>${esc(i.name)}</b></div>`).join('')}</div>`;
    }
  },
  net_util: {
    title: 'Network utilization', src: ['dash'], render: (D, b) => {
      b.innerHTML = D.dash.top_networks.map(n => `<a class="urow" href="#/ipam/${n.id}"><span class="mono">${esc(n.cidr)}<small>${esc(n.comment)}</small></span>${bar(n.utilization)}</a>`).join('')
        || '<div class="chart-empty">No networks yet.</div>';
    }
  },
  activity: { title: 'Recent activity', src: ['dash'], flush: true, render: (D, b) => { b.innerHTML = auditTable(D.dash.recent.slice(0, 8)); } },
  dns_kpi: {
    title: 'DNS at a glance', src: ['dns'], render: (D, b) => {
      const t = D.dns.totals, rc = D.dns.rcodes, all = Object.values(rc).reduce((a, v) => a + v, 0);
      b.innerHTML = kpis([
        { label: 'Requests last hour', value: F(t.last_hour) }, { label: 'Requests 24h', value: F(t.last_24h), spark: Charts.spark(D.dns.hourly.map(p => p.v)) },
        { label: 'Requests today', value: F(t.today) }, { label: 'Current rate', value: F(t.current_qps), sub: 'queries/s' },
        { label: 'Peak rate', value: F(t.peak_qps), sub: `queries/s, ${DASH.range}` },
        { label: 'NXDOMAIN', value: pct(rc.NXDOMAIN || 0, all).toFixed(1) + '%', sub: `${F(rc.NXDOMAIN || 0)} answers` },
        { label: 'SERVFAIL', value: pct(rc.SERVFAIL || 0, all).toFixed(1) + '%', sub: `${F(rc.SERVFAIL || 0)} answers` }]);
    }
  },
  dns_qps: {
    title: 'DNS queries per second', src: ['dns'], render: (D, b) => {
      if (noDns(D.dns)) { b.innerHTML = dnsEmpty(D.dns); return; }
      const t = D.dns.totals;
      b.innerHTML = `<div class="w-meta">avg <b>${F(t.avg_qps)}</b> &middot; peak <b>${F(t.peak_qps)}</b> &middot; total <b>${F(t.range)}</b> in ${DASH.range}</div><div class="chart-host"></div>`;
      Charts.area(b.querySelector('.chart-host'), { points: D.dns.series, span: rangeSecs(), name: 'queries/s' }, { unit: ' q/s' });
    }
  },
  dns_hourly: {
    title: 'DNS requests per hour', src: ['dns'], render: (D, b) => {
      if (noDns(D.dns)) { b.innerHTML = dnsEmpty(D.dns); return; }
      const hs = D.dns.hourly.slice(-24);
      b.innerHTML = `<div class="w-meta"><b>${F(hs.reduce((a, p) => a + p.v, 0))}</b> requests in the last 24 hours &middot; busiest hour <b>${F(Math.max(...hs.map(p => p.v)))}</b></div><div class="chart-host"></div>`;
      Charts.bars(b.querySelector('.chart-host'), { items: hs.map(p => ({ label: hourLbl(p.t), v: p.v, title: new Date(p.t * 1000).toLocaleString() })) }, { unit: 'requests' });
    }
  },
  dns_util: {
    title: 'DNS utilization', src: ['dns'], render: (D, b) => {
      const u = D.dns.utilization;
      Charts.gauge(b, u.pct, { sub: `${F(u.current_qps)} of ${F(u.capacity_qps)} queries/s capacity<br><small>last 5 minutes, capacity set by <span class="mono">dns_capacity_qps</span></small>` });
    }
  },
  dns_rcodes: {
    title: 'Response codes', src: ['dns'], render: (D, b) => {
      Charts.donut(b, { center: 'answers', items: Object.entries(D.dns.rcodes).map(([k, v]) => ({ label: k, v, color: RCODE_COLOR[k] ? cssv(RCODE_COLOR[k]) : undefined })) });
    }
  },
  dns_qtypes: {
    title: 'Query types', src: ['dns'], render: (D, b) => {
      const e = Object.entries(D.dns.qtypes), tot = e.reduce((a, [, v]) => a + v, 0);
      b.innerHTML = hbars(e.slice(0, 8), tot, '--s-dns');
    }
  },
  dns_answers: {
    title: 'Answer sources', src: ['dns'], render: (D, b) => {
      const a = D.dns.answers;
      Charts.donut(b, { center: 'queries', items: [{ label: 'Authoritative', v: a.authoritative, color: cssv('--primary') }, { label: 'Recursive', v: a.recursion, color: cssv('--s-lease') },
        { label: 'Non-authoritative', v: a.non_authoritative, color: cssv('--s-dns') }, { label: 'Refused', v: a.refused, color: cssv('--s-unmanaged') }] });
    }
  },
  dns_zones: {
    title: 'Records per zone', src: ['dns'], render: (D, b) => {
      const zs = D.dns.zones;
      if (!zs.length) { b.innerHTML = '<div class="chart-empty">No zones yet.</div>'; return; }
      const max = Math.max(...zs.map(z => z.manual + z.host + z.dhcp)) || 1;
      b.innerHTML = `<div class="legend inline"><span><i style="background:var(--primary)"></i>Manual</span><span><i style="background:var(--s-lease)"></i>Host</span><span><i style="background:var(--amber)"></i>DHCP (DDNS)</span></div>
        <div class="hbars">${zs.map(z => { const t = z.manual + z.host + z.dhcp; return `<div class="hb"><span class="hb-l mono" title="${esc(z.zone)}">${esc(z.zone)}</span>
        <span class="hb-bar hb-stack" style="width:${(t * 100 / max).toFixed(1)}%">${[['--primary', z.manual], ['--s-lease', z.host], ['--amber', z.dhcp]].map(([c, v]) => v ? `<i style="flex:${v};background:var(${c})"></i>` : '').join('')}</span>
        <b>${F(t)}</b><em></em></div>`; }).join('')}</div>`;
    }
  },
  dhcp_kpi: {
    title: 'DHCP at a glance', src: ['dhcp'], render: (D, b) => {
      const t = D.dhcp.totals;
      b.innerHTML = kpis([
        { label: 'Active leases', value: F(t.active), spark: Charts.spark(D.dhcp.series.map(p => p.v), 'var(--s-lease)') },
        { label: 'Pool utilization', value: t.pct + '%', sub: `${F(t.leased_in_pools)} of ${F(t.pool_size)}` },
        { label: 'Free in pools', value: F(t.free) }, { label: `Peak leases (${DASH.range})`, value: F(t.peak_active) },
        { label: 'Leases granted 24h', value: t.acks_24h === null ? '-' : F(t.acks_24h), sub: t.acks_24h === null ? 'Kea statistics unavailable' : 'DHCPACK sent' }]);
    }
  },
  dhcp_leases: {
    title: 'Active DHCP leases', src: ['dhcp'], render: (D, b) => {
      if (!D.dhcp.series.some(p => p.v !== null)) { b.innerHTML = '<div class="chart-empty">Collecting lease statistics. The graph appears after a couple of samples.</div>'; return; }
      b.innerHTML = `<div class="w-meta">now <b>${F(D.dhcp.totals.active)}</b> &middot; peak <b>${F(D.dhcp.totals.peak_active)}</b> in ${DASH.range}</div><div class="chart-host"></div>`;
      Charts.area(b.querySelector('.chart-host'), { points: D.dhcp.series, span: rangeSecs(), series: [{ name: 'active leases', values: D.dhcp.series.map(p => p.v), color: cssv('--s-lease') }] });
    }
  },
  dhcp_util: {
    title: 'DHCP pool utilization', src: ['dhcp'], render: (D, b) => {
      const t = D.dhcp.totals;
      Charts.gauge(b, t.pct, { sub: `${F(t.leased_in_pools)} of ${F(t.pool_size)} pool addresses leased` });
    }
  },
  dhcp_pools: {
    title: 'Pools by network', src: ['dhcp'], render: (D, b) => {
      const ps = D.dhcp.pools;
      b.innerHTML = ps.length ? `<div class="pools">${ps.map(p => `<a class="pool" href="#/ipam/${p.id}"><span class="mono">${esc(p.cidr)}<small>${esc(p.comment)}</small></span>
        ${bar(p.pct)}<span class="muted pool-n">${F(p.leased)}/${F(p.pool_size)} leased &middot; ${F(p.reservations)} reserved</span></a>`).join('')}</div>`
        : '<div class="chart-empty">No DHCP network with a range yet.</div>';
    }
  },
  dhcp_packets: {
    title: 'DHCP traffic', src: ['dhcp'], render: (D, b) => {
      const s = D.dhcp.source;
      if (!s.kea_stats && !D.dhcp.hourly_ack.length) {
        b.innerHTML = `<div class="chart-empty">Kea packet statistics are not available${s.kea_error ? `:<br><small>${esc(s.kea_error)}</small>` : '.'}<br>They come from the Kea control socket while kea-dhcp4 is running.</div>`; return;
      }
      const p = D.dhcp.packets, rows = [['DISCOVER', p['pkt4-discover-received']], ['OFFER', p['pkt4-offer-sent']], ['REQUEST', p['pkt4-request-received']],
        ['ACK', p['pkt4-ack-sent']], ['NAK', p['pkt4-nak-sent']], ['RELEASE', p['pkt4-release-received']], ['DECLINE', p['pkt4-decline-received']]].filter(r => r[1] !== undefined);
      b.innerHTML = `<div class="chart-host"></div><div class="w-meta" style="margin-top:8px">Packets in ${DASH.range}</div>${hbars(rows, 0, '--s-lease')}`;
      Charts.bars(b.querySelector('.chart-host'), { items: D.dhcp.hourly_ack.slice(-24).map(x => ({ label: hourLbl(x.t), v: x.v, title: new Date(x.t * 1000).toLocaleString() })) },
        { unit: 'leases granted', color: cssv('--s-lease'), height: 170, empty: 'No DHCPACK in the last 24 hours' });
    }
  },
};
const rangeSecs = () => ({ '1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800 }[DASH.range]);

async function vDashboard(board) {
  DASH.board = BOARDS.some(b => b[0] === board) ? board : 'overview';
  DASH.edit = false;
  const lay = await api('GET', '/dashboard/layout?board=' + DASH.board);
  DASH.layout = lay.widgets; DASH.saved = JSON.parse(JSON.stringify(lay.widgets));
  main(`<div class="head dash-head"><div><h1>Dashboard</h1><div class="sub">${esc(ME.name || 'Diddy')} ${esc(ME.version)} &middot; <em>${esc(ME.slogan || '')}</em></div></div>
    <div class="tools" id="dash-tools"></div></div>
    <div class="tabs board-tabs">${BOARDS.map(([k, l]) => `<a href="#/dashboard/${k}" class="${k === DASH.board ? 'on' : ''}">${l}</a>`).join('')}</div>
    <div id="dash-grid" class="dash-grid"></div>`);
  drawTools();
  await loadDash();
  clearInterval(DASH.timer);
  DASH.timer = setInterval(() => { if (!DASH.edit && document.getElementById('dash-grid')) loadDash(true); else if (!document.getElementById('dash-grid')) clearInterval(DASH.timer); }, 60000);
  on('range', d => { DASH.range = d.r; try { localStorage.setItem('diddy-range', d.r); } catch (e) { } drawTools(); loadDash(); });
  on('customize', () => { DASH.edit = true; drawTools(); drawGrid(); });
  on('cancel-edit', () => { DASH.edit = false; DASH.layout = JSON.parse(JSON.stringify(DASH.saved)); drawTools(); loadDash(); });
  on('save-layout', async () => {
    const r = await api('PUT', '/dashboard/layout?board=' + DASH.board, { widgets: DASH.layout });
    DASH.saved = r.widgets; DASH.edit = false; toast('Dashboard saved'); drawTools(); loadDash();
  });
  on('reset-layout', async () => {
    if (!confirm('Reset this dashboard to the default widgets?')) return;
    const r = await api('DELETE', '/dashboard/layout?board=' + DASH.board);
    DASH.layout = r.widgets; DASH.saved = JSON.parse(JSON.stringify(r.widgets)); DASH.edit = false; toast('Dashboard reset'); drawTools(); loadDash();
  });
  on('w-size', d => { DASH.layout[+d.i].size = d.s; drawGrid(); });
  on('w-remove', d => { DASH.layout.splice(+d.i, 1); drawGrid(); });
  on('w-up', d => { const i = +d.i; if (i > 0) { [DASH.layout[i - 1], DASH.layout[i]] = [DASH.layout[i], DASH.layout[i - 1]]; drawGrid(); } });
}
function drawTools() {
  const t = $('#dash-tools'); if (!t) return;
  if (DASH.edit) {
    const avail = Object.keys(WIDGETS).filter(k => !DASH.layout.some(w => w.id === k));
    t.innerHTML = `<select id="add-widget" class="filter" style="width:auto"><option value="">+ Add widget</option>${avail.map(k => `<option value="${k}">${esc(WIDGETS[k].title)}</option>`).join('')}</select>
      <button class="btn" data-act="reset-layout">Reset to default</button><button class="btn" data-act="cancel-edit">Cancel</button><button class="btn primary" data-act="save-layout">Save layout</button>`;
    $('#add-widget').onchange = e => { if (e.target.value) { DASH.layout.push({ id: e.target.value, size: 'M' }); drawTools(); loadDash(); } };
  } else {
    t.innerHTML = `<div class="seg">${['1h', '6h', '24h', '7d'].map(r => `<button class="${r === DASH.range ? 'on' : ''}" data-act="range" data-r="${r}">${r}</button>`).join('')}</div>
      <button class="btn" data-act="customize">Customize</button>`;
  }
}
async function loadDash(quiet) {
  const need = new Set(DASH.layout.flatMap(w => (WIDGETS[w.id] || { src: [] }).src));
  const src = {
    dash: () => api('GET', '/dashboard'), health: () => api('GET', '/health'),
    dns: () => api('GET', '/metrics/dns?range=' + DASH.range), dhcp: () => api('GET', '/metrics/dhcp?range=' + DASH.range)
  };
  const keys = [...need];
  try {
    const vals = await Promise.all(keys.map(k => src[k]()));
    keys.forEach((k, i) => { DASH.data[k] = vals[i]; });
  } catch (e) { if (!quiet) toast(e.message, true); }
  drawGrid();
}
function drawGrid() {
  const g = $('#dash-grid'); if (!g) return;
  g.classList.toggle('editing', DASH.edit);
  g.innerHTML = DASH.layout.map((w, i) => {
    const W = WIDGETS[w.id]; if (!W) return '';
    const ctl = DASH.edit ? `<span class="w-ctl"><span class="seg small">${['S', 'M', 'L'].map(s => `<button class="${w.size === s ? 'on' : ''}" data-act="w-size" data-i="${i}" data-s="${s}" title="${{ S: 'Small', M: 'Medium', L: 'Full width' }[s]}">${s}</button>`).join('')}</span>
      <button class="lnk" data-act="w-up" data-i="${i}" title="Move up">&uarr;</button><button class="lnk danger" data-act="w-remove" data-i="${i}" title="Remove">&times;</button></span>` : '';
    return `<section class="widget size-${w.size}${W.flush ? ' flush' : ''}" data-i="${i}" ${DASH.edit ? 'draggable="true"' : ''}>
      <header>${DASH.edit ? '<span class="grip" aria-hidden="true">&#8942;&#8942;</span>' : ''}<h3>${esc(W.title)}</h3>${ctl}</header><div class="w-body"></div></section>`;
  }).join('') || '<div class="panel empty">This dashboard is empty. Click Customize, then Add widget.</div>';
  g.querySelectorAll('.widget').forEach(sec => {
    const w = DASH.layout[+sec.dataset.i], W = WIDGETS[w.id], body = sec.querySelector('.w-body');
    if (W.src.some(s => !DASH.data[s])) { body.innerHTML = '<div class="chart-empty">Loading...</div>'; return; }
    try { W.render(DASH.data, body); } catch (e) { body.innerHTML = `<div class="chart-empty">${esc(e.message)}</div>`; }
    if (DASH.edit) {
      sec.addEventListener('dragstart', ev => { DASH.drag = +sec.dataset.i; sec.classList.add('dragging'); ev.dataTransfer.effectAllowed = 'move'; });
      sec.addEventListener('dragend', () => sec.classList.remove('dragging'));
      sec.addEventListener('dragover', ev => { ev.preventDefault(); sec.classList.add('drop'); });
      sec.addEventListener('dragleave', () => sec.classList.remove('drop'));
      sec.addEventListener('drop', ev => {
        ev.preventDefault(); const to = +sec.dataset.i, from = DASH.drag;
        if (from === null || from === to) return;
        const [m] = DASH.layout.splice(from, 1); DASH.layout.splice(to, 0, m); DASH.drag = null; drawGrid();
      });
    }
  });
}
let _rsz = null;
window.addEventListener('resize', () => { clearTimeout(_rsz); _rsz = setTimeout(() => { if ($('#dash-grid')) drawGrid(); }, 200); });

function auditTable(rows) {
  return table([{ k: 'ts', label: 'Time', cls: 'mono' }, { k: 'username', label: 'User' }, { k: 'action', label: 'Action' }, { k: 'object', label: 'Object' },
    { k: 'detail', label: 'Detail', fmt: v => `<span class="muted clip" title="${esc(v || '')}">${esc(String(v || ''))}</span>` }], rows, { empty: 'No activity yet.' });
}

/* ---------------- IPAM */
async function vIpam() {
  const nets = await api('GET', '/networks');
  main(head('Networks', 'Nested networks are shown under their parent', adm(`<button class="btn primary" data-act="add-net">Add network</button>`) +
    `<a class="btn" href="/api/v1/export/networks.csv">Export CSV</a>`) +
    `<div class="panel flush">${table([
      { k: 'cidr', label: 'Network', fmt: (v, r) => `<span class="tree-pad">${r.depth ? '&nbsp;'.repeat(r.depth * 3) + '└ ' : ''}</span><a class="mono" href="#/ipam/${r.id}">${esc(v)}</a>` },
      { k: 'comment', label: 'Comment' }, { k: 'site', label: 'Site' }, { k: 'vlan', label: 'VLAN' },
      { k: 'gateway', label: 'Gateway', cls: 'mono' },
      { k: 'dhcp_enabled', label: 'DHCP', fmt: (v, r) => (v ? '<span class="tag on">On</span>' : '<span class="tag">Off</span>') + (r.ddns_enabled ? ' <span class="tag host">DDNS</span>' : '') },
      { k: 'utilization', label: 'Utilization', fmt: (v, r) => bar(v) + ` <span class="muted">${r.used}/${r.total}</span>` }],
      nets, { empty: 'No networks yet. Add your first network to start tracking IP addresses.', actions: r => adm(`<button class="lnk" data-act="edit-net" data-id="${r.id}">Edit</button><button class="lnk danger" data-act="del-net" data-id="${r.id}">Delete</button>`) })}</div>`);
  on('add-net', () => netForm());
  on('edit-net', d => netForm(nets.find(n => n.id == d.id)));
  on('del-net', async d => delNet(nets.find(n => n.id == d.id)));
}
async function delNet(n) {
  if (!confirm(`Delete network ${n.cidr} and its DHCP ranges? Hosts and DNS records are kept.`)) return;
  await api('DELETE', '/networks/' + n.id); toast(`Deleted ${n.cidr}`); location.hash = '#/ipam'; route();
}
function netForm(n = {}) {
  form(n.id ? `Edit network ${n.cidr}` : 'Add network', [
    { name: 'cidr', label: 'Network (CIDR)', value: n.cidr, placeholder: '10.20.30.0/24', readonly: !!n.id, help: n.id ? 'The address block cannot be changed. Create a new network instead.' : '' },
    { name: 'comment', label: 'Comment', value: n.comment, placeholder: 'Branch office LAN' },
    { name: 'gateway', label: 'Gateway', value: n.gateway, placeholder: '10.20.30.1' },
    { name: 'site', label: 'Site', value: n.site }, { name: 'vlan', label: 'VLAN', value: n.vlan },
    { name: 'dhcp_enabled', label: 'Serve DHCP on this network (IPv4)', type: 'checkbox', value: n.dhcp_enabled },
    { name: 'dns_servers', label: 'DHCP option: DNS servers', value: n.dns_servers, placeholder: '10.20.30.2, 10.20.30.3' },
    { name: 'domain_name', label: 'DHCP option: domain name', value: n.domain_name, placeholder: 'corp.local' },
    { name: 'ddns_enabled', label: 'DDNS: register DHCP clients in DNS', type: 'checkbox', value: n.ddns_enabled },
    { name: 'ddns_domain', label: 'DDNS domain (empty = use the domain name above)', value: n.ddns_domain, placeholder: 'corp.local', help: 'Client names are registered as <client>.<domain>; host objects and manual records always win' },
    { name: 'lease_time', label: 'Lease time (seconds)', type: 'number', value: n.lease_time ?? 86400 },
    ...(n.id ? [] : [{ name: 'auto_reverse', label: 'Create the reverse DNS zone for this network', type: 'checkbox', value: true }])
  ], async data => {
    const r = n.id ? await api('PUT', '/networks/' + n.id, data) : await api('POST', '/networks', data);
    toast(n.id ? 'Network saved' : `Added ${r.cidr}`); route();
  }, n.id ? 'Save network' : 'Add network');
}

const LEGEND = [['free', 'Free'], ['range', 'DHCP range'], ['host', 'Host'], ['lease', 'Active lease'], ['dns', 'DNS record only'],
  ['gateway', 'Gateway'], ['unmanaged', 'Unmanaged (answered ping)'], ['network', 'Network / broadcast']];

async function vNetwork(id) {
  const [n, ranges] = await Promise.all([api('GET', '/networks/' + id), api('GET', '/ranges?network_id=' + id)]);
  let map = null, mapErr = '';
  try { map = await api('GET', `/networks/${id}/ipmap`); } catch (e) { mapErr = e.message; }
  const fact = (l, v) => `<div><span>${l}</span><b>${v || '-'}</b></div>`;
  main(head(`<span class="mono">${esc(n.cidr)}</span>`, esc(n.comment),
    adm(`<button class="btn primary" data-act="next">Add host at next free IP</button><button class="btn" data-act="discover">Ping sweep</button><button class="btn" data-act="add-range">Add DHCP range</button><button class="btn" data-act="edit">Edit</button><button class="btn danger" data-act="del">Delete</button>`),
    `<a href="#/ipam">Networks</a> / ${esc(n.cidr)}`) +
    `<div class="panel"><div class="facts">${fact('Utilization', bar(n.utilization))}${fact('Used / usable', `${n.used} / ${n.total}`)}
      ${fact('Gateway', esc(n.gateway))}${fact('DHCP', n.dhcp_enabled ? 'On' : 'Off')}${fact('DDNS', n.ddns_enabled ? `On (${esc(n.ddns_domain || n.domain_name)})` : 'Off')}${fact('DNS servers', esc(n.dns_servers))}
      ${fact('Domain', esc(n.domain_name))}${fact('Site / VLAN', esc([n.site, n.vlan].filter(Boolean).join(' / ')))}${fact('Reverse zone', esc(n.reverse_zone))}</div></div>` +
    (map ? `<div class="ipmap-wrap"><div class="panel"><h2>IP map ${map.conflicts ? `<span class="tag warn">${map.conflicts} conflict${map.conflicts > 1 ? 's' : ''}</span>` : ''}</h2>
      <div class="legend">${LEGEND.map(([k, l]) => `<span><i class="s-${k}"></i>${l}${map.counts[k] ? ` (${map.counts[k] + (k === 'network' ? (map.counts.broadcast || 0) : 0)})` : ''}</span>`).join('')}</div>
      <div class="ipmap">${map.cells.map((c, i) => `<button type="button" class="s-${c.status}${c.conflict ? ' cf' : ''}" data-act="cell" data-i="${i}" title="${esc(c.ip + (c.name ? ' ' + c.name : ''))}">${c.ip.split('.')[3]}</button>`).join('')}</div></div>
      <div class="panel ipinfo" id="ipinfo"><h2>Address details</h2><p class="muted">Select an address in the map to see what uses it.</p></div></div>`
      : `<div class="panel muted">${esc(mapErr)}</div>`) +
    `<div class="panel flush"><div style="padding:16px 16px 0"><h2>DHCP ranges</h2></div>${table([
      { k: 'start_ip', label: 'Start', cls: 'mono' }, { k: 'end_ip', label: 'End', cls: 'mono' }, { k: 'size', label: 'Size' }, { k: 'comment', label: 'Comment' }],
      ranges, { empty: n.dhcp_enabled ? 'No DHCP range yet. Clients only get reserved addresses until you add one.' : 'DHCP is off for this network.', actions: r => adm(`<button class="lnk" data-act="edit-range" data-id="${r.id}">Edit</button><button class="lnk danger" data-act="del-range" data-id="${r.id}">Delete</button>`) })}</div>`);

  on('cell', (d, el) => {
    const c = map.cells[d.i];
    document.querySelectorAll('.ipmap .sel').forEach(b => b.classList.remove('sel')); el.classList.add('sel');
    const label = (LEGEND.find(l => l[0] === c.status) || [0, c.status])[1];
    const row = (k, v) => v ? `<dt>${k}</dt><dd>${v}</dd>` : '';
    $('#ipinfo').innerHTML = `<h2 class="mono">${c.ip}</h2><dl>${row('Status', label)}${row('Name', esc(c.name))}${row('MAC', `<span class="mono">${esc(c.mac)}</span>`)}
      ${row('Lease ends', esc(c.expires))}${row('Ping', c.alive ? 'Answered' + (c.last_seen ? ' ' + esc(c.last_seen) : '') : '')}${row('In DHCP range', c.in_range ? 'Yes' : '')}${row('Reservation', c.dhcp ? 'Yes' : '')}</dl>
      ${c.conflict ? `<div class="conflict">${esc(c.conflict)}</div>` : ''}
      <div class="btns" style="justify-content:flex-start;margin-top:14px">${
        c.host_id ? adm(`<button class="btn small" data-act="edit-host-cell" data-id="${c.host_id}">Edit host</button>`) :
        ['free', 'range', 'unmanaged', 'lease', 'dns'].includes(c.status) ? adm(`<button class="btn small primary" data-act="host-here" data-ip="${c.ip}" data-mac="${esc(c.mac || '')}" data-name="${esc(c.name || '')}">${c.status === 'lease' ? 'Convert to reservation' : 'Add host here'}</button>`) : ''}</div>`;
  });
  on('host-here', d => hostForm({ ip: d.ip, mac: d.mac, fqdn: d.name && d.name.includes('.') ? d.name : (d.name && n.domain_name ? `${d.name}.${n.domain_name}` : ''), configure_dns: true, configure_dhcp: !!d.mac || n.dhcp_enabled }));
  on('edit-host-cell', async d => hostForm(await api('GET', '/hosts/' + d.id)));
  on('next', async () => {
    const r = await api('GET', `/networks/${id}/next_available`);
    if (!r.ips.length) return toast('No free address left in this network', true);
    hostForm({ ip: r.ips[0], configure_dns: true, configure_dhcp: false, fqdn: n.domain_name ? '.' + n.domain_name : '' });
  });
  on('discover', async (d, el) => {
    el.disabled = true; el.textContent = 'Scanning';
    try { const r = await api('POST', `/networks/${id}/discover`); toast(`${r.alive.length} of ${r.scanned} addresses answered ping`); route(); }
    finally { el.disabled = false; el.textContent = 'Ping sweep'; }
  });
  on('add-range', () => rangeForm({ network_id: n.id }, n));
  on('edit-range', d => rangeForm(ranges.find(r => r.id == d.id), n));
  on('del-range', async d => { const r = ranges.find(x => x.id == d.id); if (confirm(`Delete DHCP range ${r.start_ip} - ${r.end_ip}?`)) { await api('DELETE', '/ranges/' + r.id); toast('Range deleted'); route(); } });
  on('edit', () => netForm(n));
  on('del', () => delNet(n));
}
function rangeForm(r, n) {
  form(r.id ? 'Edit DHCP range' : `Add DHCP range to ${n.cidr}`, [
    { name: 'start_ip', label: 'Start IP', value: r.start_ip, placeholder: n.cidr.split('/')[0].replace(/\d+$/, '100') },
    { name: 'end_ip', label: 'End IP', value: r.end_ip, placeholder: n.cidr.split('/')[0].replace(/\d+$/, '199') },
    { name: 'comment', label: 'Comment', value: r.comment }
  ], async data => {
    data.network_id = n.id;
    r.id ? await api('PUT', '/ranges/' + r.id, data) : await api('POST', '/ranges', data);
    toast('DHCP range saved'); route();
  }, r.id ? 'Save range' : 'Add range');
}

/* ---------------- hosts */
function hostForm(h = {}) {
  form(h.id ? `Edit host ${h.fqdn}` : 'Add host', [
    { name: 'fqdn', label: 'Host name (FQDN)', value: h.fqdn, placeholder: 'printer01.corp.local' },
    { name: 'ip', label: 'IP address', value: h.ip, placeholder: '10.20.30.40', help: 'Or next:<network>, for example next:10.20.30.0/24, to take the next free address' },
    { name: 'mac', label: 'MAC address', value: h.mac, placeholder: 'aa:bb:cc:dd:ee:ff' },
    { name: 'configure_dns', label: 'Create DNS records (A/AAAA and PTR)', type: 'checkbox', value: h.configure_dns ?? true },
    { name: 'configure_dhcp', label: 'Create DHCP reservation (needs MAC)', type: 'checkbox', value: h.configure_dhcp },
    { name: 'comment', label: 'Comment', value: h.comment }
  ], async data => {
    const r = h.id ? await api('PUT', '/hosts/' + h.id, data) : await api('POST', '/hosts', data);
    toast(`${h.id ? 'Saved' : 'Added'} ${r.fqdn} (${r.ip})`); route();
  }, h.id ? 'Save host' : 'Add host');
}
async function vHosts() {
  const hosts = await api('GET', '/hosts');
  main(head('Hosts', 'A host keeps its DNS name, PTR record and DHCP reservation together',
    adm(`<button class="btn primary" data-act="add">Add host</button><button class="btn" data-act="import">Import CSV</button>`) + `<a class="btn" href="/api/v1/export/hosts.csv">Export CSV</a>`) +
    `<div class="panel flush"><div style="padding:12px 12px 0"><input class="filter" id="flt" placeholder="Filter hosts"></div><div id="tbl"></div></div>`);
  const draw = f => {
    const rows = hosts.filter(h => !f || [h.fqdn, h.ip, h.mac, h.comment, h.network].join(' ').toLowerCase().includes(f));
    $('#tbl').innerHTML = table([
      { k: 'fqdn', label: 'Name' }, { k: 'ip', label: 'IP address', cls: 'mono' }, { k: 'mac', label: 'MAC', cls: 'mono' },
      { k: 'network', label: 'Network', cls: 'mono' },
      { k: 'configure_dns', label: 'DNS', fmt: v => v ? '<span class="tag on">Yes</span>' : '<span class="tag">No</span>' },
      { k: 'configure_dhcp', label: 'DHCP reservation', fmt: v => v ? '<span class="tag on">Yes</span>' : '<span class="tag">No</span>' },
      { k: 'comment', label: 'Comment' }], rows,
      { empty: f ? 'No host matches this filter.' : 'No hosts yet. Add one, or import a CSV.', actions: r => adm(`<button class="lnk" data-act="edit" data-id="${r.id}">Edit</button><button class="lnk danger" data-act="del" data-id="${r.id}">Delete</button>`) });
  };
  draw(''); $('#flt').oninput = e => draw(e.target.value.trim().toLowerCase());
  on('add', () => hostForm());
  on('edit', d => hostForm(hosts.find(h => h.id == d.id)));
  on('del', async d => { const h = hosts.find(x => x.id == d.id); if (confirm(`Delete ${h.fqdn} (${h.ip})? Its DNS records and DHCP reservation are removed on the next deploy.`)) { await api('DELETE', '/hosts/' + h.id); toast(`Deleted ${h.fqdn}`); route(); } });
  on('import', () => form('Import hosts from CSV', [
    { name: 'csv', label: 'CSV with header row', type: 'textarea', rows: 10, placeholder: 'fqdn,ip,mac,configure_dns,configure_dhcp,comment\nap01.corp.local,10.20.30.41,aa:bb:cc:dd:ee:01,1,1,Lobby AP\ncam01.corp.local,next:10.20.30.0/24,,1,0,' }
  ], async data => {
    const r = await api('POST', '/import/hosts', data);
    toast(`Imported ${r.imported} host(s)` + (r.errors.length ? `, ${r.errors.length} failed` : ''), r.errors.length > 0);
    if (r.errors.length) alert('Rows not imported:\n\n' + r.errors.join('\n'));
    route();
  }, 'Import'));
}

/* ---------------- DNS */
async function vDns() {
  const zones = await api('GET', '/zones');
  const cols = [{ k: 'name', label: 'Zone', fmt: (v, r) => `<a href="#/dns/${r.id}">${esc(v)}</a>` }, { k: 'record_count', label: 'Records' },
    { k: 'dynamic_count', label: 'From DHCP', fmt: v => v ? `<span class="tag warn">${v}</span>` : '<span class="muted">0</span>' },
    { k: 'primary_ns', label: 'Primary NS' }, { k: 'serial', label: 'Serial', cls: 'mono' }, { k: 'comment', label: 'Comment' }];
  const acts = r => adm(`<button class="lnk" data-act="edit" data-id="${r.id}">Edit</button><button class="lnk danger" data-act="del" data-id="${r.id}">Delete</button>`);
  main(head('DNS zones', 'Authoritative zones served by BIND on this server', adm(`<button class="btn primary" data-act="add">Add zone</button>`) + `<a class="btn" href="#/resolver">Resolver &amp; forwarders</a><a class="btn" href="/api/v1/export/records.csv">Export records</a>`) +
    `<div class="panel flush"><div style="padding:16px 16px 0"><h2>Forward zones</h2></div>${table(cols, zones.filter(z => !z.reverse), { empty: 'No forward zone yet. Add one, for example corp.local.', actions: acts })}</div>
     <div class="panel flush"><div style="padding:16px 16px 0"><h2>Reverse zones</h2></div>${table(cols, zones.filter(z => z.reverse), { empty: 'No reverse zone yet. They are created when you add a network with the reverse zone option.', actions: acts })}</div>`);
  on('add', () => zoneForm());
  on('edit', d => zoneForm(zones.find(z => z.id == d.id)));
  on('del', async d => { const z = zones.find(x => x.id == d.id); if (confirm(`Delete zone ${z.name} and all its manual records?`)) { await api('DELETE', '/zones/' + z.id); toast(`Deleted ${z.name}`); route(); } });
}
function zoneForm(z = {}) {
  form(z.id ? `Edit zone ${z.name}` : 'Add zone', [
    { name: 'name', label: 'Zone name', value: z.name, placeholder: 'corp.local or 30.20.10.in-addr.arpa', readonly: !!z.id },
    { name: 'primary_ns', label: 'Primary name server', value: z.primary_ns || ME.defaults.primary_ns },
    ...(z.id ? [] : [{ name: 'ns_ip', label: 'Name server IP (optional)', placeholder: '10.20.30.2', help: 'Adds the glue A record when the name server is inside this zone' }]),
    { name: 'admin_email', label: 'Admin email (SOA)', value: z.admin_email || ME.defaults.admin_email },
    { name: 'ttl', label: 'Default TTL (seconds)', type: 'number', value: z.ttl ?? 3600 },
    { name: 'comment', label: 'Comment', value: z.comment }
  ], async data => {
    z.id ? await api('PUT', '/zones/' + z.id, data) : await api('POST', '/zones', data);
    toast('Zone saved'); route();
  }, z.id ? 'Save zone' : 'Add zone');
}
const HINT = { A: '10.20.30.40', AAAA: '2001:db8::10', CNAME: 'target.corp.local', MX: '10 mail.corp.local', TXT: 'v=spf1 mx -all', NS: 'ns2.corp.local', PTR: 'host.corp.local', SRV: '10 5 5060 sip.corp.local' };
async function vZone(id) {
  const [z, recs] = await Promise.all([api('GET', '/zones/' + id), api('GET', `/zones/${id}/records`)]);
  main(head(esc(z.name), `Serial ${z.serial || 'not deployed'} · TTL ${z.ttl} · primary NS ${esc(z.primary_ns)}`,
    adm(`<button class="btn primary" data-act="add">Add record</button>`) + `<button class="btn" data-act="preview">View zone file</button>`, `<a href="#/dns">DNS zones</a> / ${esc(z.name)}`) +
    `<div class="panel flush"><div style="padding:12px 12px 0"><input class="filter" id="flt" placeholder="Filter records"></div><div id="tbl"></div></div>`);
  const draw = f => {
    const rows = recs.filter(r => !f || [r.name, r.type, r.value, r.comment].join(' ').toLowerCase().includes(f));
    $('#tbl').innerHTML = table([
      { k: 'name', label: 'Name', cls: 'mono' }, { k: 'type', label: 'Type', fmt: v => `<b>${esc(v)}</b>` }, { k: 'value', label: 'Value', cls: 'mono' },
      { k: 'ttl', label: 'TTL', fmt: v => v ?? '<span class="muted">zone</span>' },
      { k: 'source', label: 'Source', fmt: v => v === 'host' ? `<a class="tag host" href="#/hosts" title="Managed by a host object">host</a>`
        : v === 'lease' ? `<span class="tag warn" title="Created from an active DHCP lease (DDNS)">DHCP</span>` : '<span class="tag">manual</span>' },
      { k: 'comment', label: 'Comment' }], rows,
      { empty: f ? 'No record matches this filter.' : 'No records yet.', actions: r => r.source === 'manual' ? adm(`<button class="lnk" data-act="edit" data-id="${r.id}">Edit</button><button class="lnk danger" data-act="del" data-id="${r.id}">Delete</button>`) : '' });
  };
  draw(''); $('#flt').oninput = e => draw(e.target.value.trim().toLowerCase());
  const recForm = (r = {}) => {
    form(r.id ? 'Edit record' : `Add record to ${z.name}`, [
      { name: 'name', label: 'Name', value: r.name, placeholder: '@ for the zone itself, or www' },
      { name: 'type', label: 'Type', type: 'select', options: z.reverse ? ['PTR', 'NS', 'CNAME', 'TXT'] : ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'NS', 'SRV', 'PTR'], value: r.type },
      { name: 'value', label: 'Value', value: r.value, placeholder: 'See the example for the selected type' },
      { name: 'ttl', label: 'TTL (empty = zone default)', type: 'number', value: r.ttl },
      { name: 'comment', label: 'Comment', value: r.comment }
    ], async data => {
      data.zone_id = z.id;
      r.id ? await api('PUT', '/records/' + r.id, data) : await api('POST', '/records', data);
      toast('Record saved'); route();
    }, r.id ? 'Save record' : 'Add record');
    const sel = $('.modal select[name=type]'), val = $('.modal input[name=value]');
    const upd = () => { val.placeholder = 'e.g. ' + HINT[sel.value]; }; sel.onchange = upd; upd();
  };
  on('add', () => recForm());
  on('edit', d => recForm(recs.find(r => String(r.id) === d.id)));
  on('del', async d => { const r = recs.find(x => String(x.id) === d.id); if (confirm(`Delete ${r.type} record ${r.name}?`)) { await api('DELETE', '/records/' + r.id); toast('Record deleted'); route(); } });
  on('preview', async () => {
    const p = await api('GET', '/deploy/preview');
    modal(`<h3>Zone file preview: ${esc(z.name)}</h3><pre class="code">${esc(p.zones[z.name])}</pre><div class="btns"><button class="btn" onclick="this.closest('.modal-bg').remove()">Close</button></div>`, true);
  });
}

/* ---------------- resolver / forwarders */
async function vResolver() {
  const [cfg, fws] = await Promise.all([api('GET', '/dns-settings'), api('GET', '/forwarders')]);
  main(head('Resolver and forwarders', 'How BIND answers for names it is not authoritative for',
    adm(`<button class="btn primary" data-act="edit-cfg">Edit resolver</button><button class="btn" data-act="add-fw">Add conditional forwarder</button>`),
    `<a href="#/dns">DNS zones</a> / Resolver`) +
    `<div class="panel"><h2>Resolver</h2><div class="facts">
      <div><span>Recursion</span><b>${cfg.recursion ? 'On' : 'Off (authoritative only)'}</b></div>
      <div><span>Upstream mode</span><b>${cfg.upstream_mode === 'encrypted' ? 'Encrypted (dnsdist)' : 'Plain DNS (UDP/TCP 53)'}</b></div>
      <div><span>Upstream forwarders</span><b>${esc(cfg.upstream_mode === 'encrypted' ? cfg.encrypted_upstreams.map(u => `${u.protocol.toUpperCase()} ${u.address} (${u.hostname})`).join(', ') : (cfg.forwarders.join(', ') || '-'))}</b></div>
      <div><span>Forward policy</span><b>${esc(cfg.forward_policy)}</b></div>
      <div><span>DNSSEC validation</span><b>${esc(cfg.dnssec_validation)}</b></div>
      <div style="grid-column:1/-1"><span>Allowed to use this resolver</span><b>${esc(cfg.effective_acl.join(', '))}</b></div></div>
      ${cfg.recursion ? '' : '<p class="muted" style="margin-bottom:0">With recursion off, this server only answers for its own zones. Turn it on to use it as the DNS server for clients.</p>'}
      ${cfg.recursion && cfg.upstream_mode !== 'encrypted' && !cfg.forwarders.length ? '<div class="conflict">No upstream forwarder set: BIND will resolve from the root servers directly.</div>' : ''}
      ${cfg.upstream_mode === 'encrypted' ? `<p class="muted" style="margin-bottom:0">BIND forwards to the local dnsdist proxy, which talks to the upstream over ${esc(cfg.encrypted_upstreams[0] ? cfg.encrypted_upstreams[0].protocol.toUpperCase() : 'TLS')}. Certificate validation is ${cfg.validate_certificates ? 'on' : '<b>off</b>'}.</p>` : ''}</div>
     <div class="panel flush"><div style="padding:16px 16px 0"><h2>Conditional forwarders</h2>
       <p class="muted">Queries for these domains go straight to the servers you name, for example an Active Directory domain.</p></div>
       ${table([{ k: 'domain', label: 'Domain', cls: 'mono' }, { k: 'servers', label: 'Servers', cls: 'mono' },
                { k: 'policy', label: 'Policy', fmt: v => `<span class="tag">${esc(v)}</span>` }, { k: 'comment', label: 'Comment' }],
         fws, { empty: 'No conditional forwarder yet.', actions: f => adm(`<button class="lnk" data-act="edit-fw" data-id="${f.id}">Edit</button><button class="lnk danger" data-act="del-fw" data-id="${f.id}">Delete</button>`) })}</div>`);
  on('edit-cfg', () => form('Resolver settings', [
    { name: 'recursion', label: 'Act as a resolver for clients (recursion)', type: 'checkbox', value: cfg.recursion },
    { name: 'upstream_mode', label: 'Upstream mode', type: 'select', value: cfg.upstream_mode,
      options: [['plain', 'Plain DNS on port 53'], ['encrypted', 'Encrypted DoT/DoH through a local dnsdist proxy']] },
    { name: 'forwarders', label: 'Upstream forwarders (plain mode)', value: cfg.forwarders.join(', '), placeholder: '8.8.8.8, 1.1.1.1', help: 'Empty = resolve from the root servers. Use "10.1.1.1 port 5353" for a custom port' },
    { name: 'preset', label: 'Encrypted preset (fills the box below when it is empty)', type: 'select', value: '',
      options: [['', 'none'], ['cloudflare', 'Cloudflare DoT'], ['quad9', 'Quad9 DoT'], ['google', 'Google DoT'], ['adguard', 'AdGuard DoT']] },
    { name: 'encrypted_upstreams', label: 'Encrypted upstreams, one per line', type: 'textarea', rows: 4,
      value: cfg.encrypted_upstreams.map(u => `${u.protocol} ${u.address}${(u.protocol === 'dot' && u.port !== 853) || (u.protocol === 'doh' && u.port !== 443) ? ':' + u.port : ''} ${u.hostname}${u.protocol === 'doh' ? ' ' + u.path : ''}`).join('\n'),
      placeholder: 'dot 1.1.1.1 cloudflare-dns.com\ndoh 9.9.9.9 dns.quad9.net /dns-query',
      help: 'Format: <dot|doh> <ip>[:port] <certificate name> [path]' },
    { name: 'validate_certificates', label: 'Validate upstream TLS certificates', type: 'checkbox', value: cfg.validate_certificates },
    { name: 'dnsdist_policy', label: 'Upstream selection policy', type: 'select', value: cfg.dnsdist_policy,
      options: [['leastOutstanding', 'leastOutstanding (fastest responding)'], ['firstAvailable', 'firstAvailable (in order)'], ['roundrobin', 'roundrobin'], ['wrandom', 'wrandom']] },
    { name: 'forward_policy', label: 'Forward policy', type: 'select', options: [['first', 'first (fall back to resolving itself)'], ['only', 'only (fail if upstream fails)']], value: cfg.forward_policy },
    { name: 'auto_allow_ipam', label: 'Allow every network in IPAM to use this resolver', type: 'checkbox', value: cfg.auto_allow_ipam },
    { name: 'allow_recursion', label: 'Extra networks allowed', value: cfg.allow_recursion.join(', '), placeholder: '10.0.0.0/8, 192.168.1.0/24' },
    { name: 'dnssec_validation', label: 'DNSSEC validation', type: 'select', options: [['auto', 'auto'], ['no', 'no']], value: cfg.dnssec_validation }
  ], async d => { await api('PUT', '/dns-settings', d); toast('Resolver settings saved'); route(); }, 'Save'));
  const fwForm = (f = {}) => form(f.id ? `Edit forwarder ${f.domain}` : 'Add conditional forwarder', [
    { name: 'domain', label: 'Domain', value: f.domain, placeholder: 'ad.corp.local' },
    { name: 'servers', label: 'Servers', value: f.servers, placeholder: '10.1.1.10, 10.1.1.11' },
    { name: 'policy', label: 'Policy', type: 'select', options: [['only', 'only'], ['first', 'first']], value: f.policy },
    { name: 'comment', label: 'Comment', value: f.comment }
  ], async d => { f.id ? await api('PUT', '/forwarders/' + f.id, d) : await api('POST', '/forwarders', d); toast('Forwarder saved'); route(); }, 'Save');
  on('add-fw', () => fwForm());
  on('edit-fw', d => fwForm(fws.find(f => f.id == d.id)));
  on('del-fw', async d => { const f = fws.find(x => x.id == d.id); if (confirm(`Delete conditional forwarder for ${f.domain}?`)) { await api('DELETE', '/forwarders/' + f.id); toast('Deleted'); route(); } });
}

/* ---------------- DHCP */
async function vDhcp() {
  const [nets, ranges, leases] = await Promise.all([api('GET', '/networks'), api('GET', '/ranges'), api('GET', '/leases')]);
  const dn = nets.filter(n => n.dhcp_enabled);
  main(head('DHCP', 'IPv4 service by ISC Kea', adm(`<button class="btn" data-act="ddns">Refresh DDNS now</button>`) + `<a class="btn" href="/api/v1/export/leases.csv">Export leases</a>`) +
    `<div class="panel flush"><div style="padding:16px 16px 0"><h2>DHCP networks</h2></div>${table([
      { k: 'cidr', label: 'Network', fmt: (v, r) => `<a class="mono" href="#/ipam/${r.id}">${esc(v)}</a>` }, { k: 'comment', label: 'Comment' },
      { k: 'gateway', label: 'Router', cls: 'mono' }, { k: 'dns_servers', label: 'DNS servers', cls: 'mono' },
      { k: 'id', label: 'Ranges', cls: 'mono', fmt: v => ranges.filter(r => r.network_id === v).map(r => `${r.start_ip} - ${r.end_ip}`).join('<br>') || '<span class="muted">none</span>' },
      { k: 'lease_time', label: 'Lease time', fmt: v => `${Math.round(v / 3600 * 10) / 10} h` }],
      dn, { empty: 'No network serves DHCP yet. Edit a network in IPAM and turn on DHCP.' })}</div>
     <div class="panel flush"><div style="padding:12px 12px 0;display:flex;justify-content:space-between;align-items:center"><h2 style="margin:0">Active leases (${leases.length})</h2><input class="filter" id="flt" placeholder="Filter leases"></div><div id="tbl"></div></div>`);
  const draw = f => {
    const rows = leases.filter(l => !f || [l.ip, l.mac, l.hostname, l.network].join(' ').toLowerCase().includes(f));
    $('#tbl').innerHTML = table([{ k: 'ip', label: 'IP address', cls: 'mono' }, { k: 'mac', label: 'MAC', cls: 'mono' }, { k: 'hostname', label: 'Client name' },
      { k: 'network', label: 'Network', cls: 'mono' }, { k: 'expires', label: 'Expires', cls: 'mono' }], rows,
      { empty: 'No active leases found in the Kea lease file.', actions: l => adm(`<button class="lnk" data-act="reserve" data-ip="${l.ip}">Make reservation</button>`) });
  };
  draw(''); $('#flt').oninput = e => draw(e.target.value.trim().toLowerCase());
  on('ddns', async (d, el) => {
    el.disabled = true;
    try { const r = await api('POST', '/ddns/refresh'); toast(r.changed.length ? `Updated zone(s): ${r.changed.join(', ')}` : 'DNS already matches the current leases'); }
    finally { el.disabled = false; }
  });
  on('reserve', d => { const l = leases.find(x => x.ip === d.ip); hostForm({ ip: l.ip, mac: l.mac, fqdn: l.hostname, configure_dns: true, configure_dhcp: true }); });
}

/* ---------------- deploy */
async function vDeploy() {
  const [p, dr] = await Promise.all([api('GET', '/deploy/preview'), api('GET', '/drift')]);
  const files = [['named.conf.diddy', p.named_conf], ['named.conf.options.diddy', p.named_options], ...Object.entries(p.zones).map(([k, v]) => ['db.' + k, v]), ['kea-dhcp4.conf', p.kea], ...(p.dnsdist ? [['dnsdist.conf', p.dnsdist]] : [])];
  main(head('Deploy to services', p.pending ? 'Changes are waiting to be applied.' : 'Services are up to date with the database.',
    adm(`<button class="btn ${p.pending ? 'amber' : 'primary'}" data-act="deploy">Validate and deploy</button>`)) +
    (p.dry_run ? `<div class="panel"><b>Dry run mode is on</b>: files are written, but BIND and Kea are not reloaded.</div>` : '') +
    `<div class="panel"><h2>Service configuration ${dr.drifted ? `<span class="pill fail">${dr.drifted} file drifted</span>` : `<span class="pill ok">In sync</span>`}</h2>
      <p class="muted">Diddy keeps a copy of every file it writes and compares it with what the services are using now. Checked every ${dr.interval}s${dr.checked_at ? `, last check ${esc(dr.checked_at)}` : ''}${dr.auto_repair ? ', automatic repair is on' : ''}.</p>
      <div class="health">${dr.items.map(i => `<div class="hrow"><span class="dot ${i.state === 'ok' ? 'on' : i.state === 'unknown' ? '' : 'off'}"></span><b class="mono">${esc(i.path)}</b><span class="detail">${i.state === 'ok' ? 'sama dengan deploy ' + esc(i.deployed_at) : esc(i.state + ' - ' + i.detail)}</span></div>`).join('') || '<div class="empty">Nothing deployed yet.</div>'}</div>
      ${dr.drifted ? adm(`<div class="btns" style="justify-content:flex-start;margin-top:12px"><button class="btn danger" data-act="repair">Restore the last deployed files</button></div>`) : ''}</div>` +
    `<div id="report"></div><div class="panel"><h2>Files to be written</h2><div class="tabs">${files.map((f, i) => `<button data-act="tab" data-i="${i}" class="${i ? '' : 'on'}">${esc(f[0])}</button>`).join('')}</div><pre class="code" id="file"></pre></div>`);
  const show = i => { $('#file').textContent = files[i][1]; document.querySelectorAll('.tabs button').forEach((b, j) => b.classList.toggle('on', i === j)); };
  show(0);
  on('tab', d => show(+d.i));
  on('repair', async (d, el) => {
    if (!confirm('Restore every drifted file to the last version Diddy deployed? The current files are kept as .drift.bak.')) return;
    el.disabled = true;
    const r = await api('POST', '/drift/repair');
    toast(r.repaired.length ? `Restored ${r.repaired.length} file(s)` : 'Nothing to restore');
    route();
  });
  on('deploy', async (d, el) => {
    el.disabled = true; el.textContent = 'Deploying';
    let r;
    try { r = await api('POST', '/deploy'); } catch (e) { r = e.data || { ok: false, report: [{ step: 'deploy', ok: false, output: e.message }] }; }
    el.disabled = false; el.textContent = 'Validate and deploy';
    $('#report').innerHTML = `<div class="panel"><h2>${r.ok ? 'Deployed' : 'Deploy stopped: nothing was changed on the services'}</h2><div class="report">${(r.report || []).map(s =>
      `<div><b class="${s.ok ? 'ok' : 'bad'}">${s.ok ? '✓' : '✗'}</b><div><div>${esc(s.step)}</div>${s.output ? `<pre>${esc(s.output)}</pre>` : ''}</div></div>`).join('')}</div></div>`;
    toast(r.ok ? 'Deployed' : 'Deploy failed, see the report', !r.ok);
    refreshMe();
  });
}

/* ---------------- health and system information */
const PILL = s => `<span class="pill ${s === 'ok' ? 'ok' : s === 'fail' ? 'fail' : 'warn'}">${s === 'ok' ? 'OK' : s === 'fail' ? 'Problem' : s === 'warn' ? 'Attention' : 'Unknown'}</span>`;
async function vHealth() {
  const h = await api('GET', '/health');
  main(head('Health', 'Live status of everything Diddy depends on', `<button class="btn" data-act="refresh">Refresh</button>`) +
    `<div class="panel"><h2>Overall ${PILL(h.overall)}</h2><div class="health">${h.items.map(i =>
      `<div class="hrow"><span class="dot ${i.state === 'ok' ? 'on' : i.state === 'fail' ? 'off' : i.state === 'warn' ? 'warn' : ''}"></span><b>${esc(i.name)}</b><span class="detail">${esc(i.detail)}</span></div>`).join('')}</div></div>`);
  on('refresh', () => route());
}
async function vSystem() {
  const s = await api('GET', '/system');
  const row = (l, v) => `<div><span>${l}</span><b>${esc(v)}</b></div>`;
  main(head('System information', `Diddy ${esc(s.diddy)} by ${esc(s.author)}`) +
    `<div class="panel"><h2>Software</h2><div class="kv">
      ${row('Diddy version', s.diddy)}${row('Python', s.python)}${row('BIND', s.bind)}${row('Kea DHCPv4', s.kea)}
      ${row('Operating system', s.distribution)}${row('Kernel', s.os)}</div></div>
     <div class="panel"><h2>This server</h2><div class="kv">
      ${row('Hostname', s.hostname)}${row('Service uptime', s.uptime)}${row('Server time', s.server_time)}
      ${row('Dry run mode', s.dry_run ? 'on (services not reloaded)' : 'off')}</div></div>
     <div class="panel"><h2>Storage and paths</h2><div class="kv">
      ${row('Database', s.database)}${row('Table prefix', s.table_prefix)}${row('Kea lease storage', s.kea_lease_backend)}
      ${row('dnsdist (encrypted upstream)', s.dnsdist)}${row('Upstream mode', s.upstream_mode)}
      ${row('DHCP interfaces', s.dhcp_interfaces)}${row('BIND directory', s.bind_dir)}${row('Kea config', s.kea_conf)}
      ${row('Diddy config', s.config_file)}${row('DDNS refresh', s.ddns_refresh_interval + ' s')}</div></div>
     <div class="panel"><h2>About</h2><p style="margin:0 0 6px;font-size:17px"><b>${esc(s.name)}</b> &mdash; <em>${esc(s.slogan)}</em></p><p class="muted" style="margin:0">Version ${esc(s.version)}. DNS, DHCP and IPAM management for Linux, built on BIND9 and ISC Kea.<br>${esc(s.copyright)}</p></div>`);
}

/* ---------------- audit, admin, search */
async function vAudit() {
  main(head('Audit log', 'Every change, login and deploy') + `<div class="panel flush">${auditTable(await api('GET', '/audit?limit=500'))}</div>`);
}
async function vAdmin() {
  const users = await api('GET', '/users');
  main(head('Admin', `Diddy ${esc(ME.version)}`, adm(`<button class="btn primary" data-act="add">Add user</button>`)) +
    `<div class="panel flush"><div style="padding:16px 16px 0"><h2>Users</h2></div>${table([{ k: 'username', label: 'Username' },
      { k: 'role', label: 'Role', fmt: v => v === 'admin' ? '<span class="tag on">Admin</span>' : '<span class="tag">Read only</span>' }, { k: 'created', label: 'Created', cls: 'mono' }], users,
      { actions: u => `<button class="lnk" data-act="pw" data-id="${u.id}">Change password</button>` + (u.id !== ME.user.id ? adm(`<button class="lnk danger" data-act="del" data-id="${u.id}">Delete</button>`) : '') })}</div>
     <div class="panel"><h2>API</h2><p class="muted">Every screen uses the REST API at <span class="mono">/api/v1</span> with HTTP Basic authentication. Example:</p>
     <pre class="code">curl -u admin:PASSWORD -H 'Content-Type: application/json' \\
  -d '{"fqdn":"srv01.corp.local","ip":"next:10.10.1.0/24","mac":"aa:bb:cc:dd:ee:ff","configure_dhcp":true}' \\
  http://${esc(location.host)}/api/v1/hosts
curl -u admin:PASSWORD -X POST http://${esc(location.host)}/api/v1/deploy</pre></div>`);
  on('add', () => form('Add user', [{ name: 'username', label: 'Username' }, { name: 'password', label: 'Password (min. 8 characters)', type: 'password' },
    { name: 'role', label: 'Role', type: 'select', options: [['readonly', 'Read only'], ['admin', 'Admin']] }],
    async d => { await api('POST', '/users', d); toast(`Added ${d.username}`); route(); }, 'Add user'));
  on('pw', d => form('Change password', [{ name: 'password', label: 'New password (min. 8 characters)', type: 'password' }],
    async data => { await api('PUT', '/users/' + d.id, data); toast('Password changed'); }, 'Change password'));
  on('del', async d => { const u = users.find(x => x.id == d.id); if (confirm(`Delete user ${u.username}?`)) { await api('DELETE', '/users/' + u.id); toast('User deleted'); route(); } });
}
async function vSearch(s) {
  const r = await api('GET', '/search?q=' + encodeURIComponent(s));
  const total = r.networks.length + r.hosts.length + r.records.length + r.leases.length;
  const sec = (t, html) => `<div class="panel flush"><div style="padding:16px 16px 0"><h2>${t}</h2></div>${html}</div>`;
  main(head(`Results for “${esc(s)}”`, `${total} match${total === 1 ? '' : 'es'}`) + (total ? '' : '<div class="panel empty">Nothing matches. Try part of an IP address, host name or MAC.</div>') +
    (r.networks.length ? sec('Networks', table([{ k: 'cidr', label: 'Network', fmt: (v, n) => `<a class="mono" href="#/ipam/${n.id}">${esc(v)}</a>` }, { k: 'comment', label: 'Comment' }, { k: 'site', label: 'Site' }], r.networks)) : '') +
    (r.hosts.length ? sec('Hosts', table([{ k: 'fqdn', label: 'Name' }, { k: 'ip', label: 'IP', cls: 'mono' }, { k: 'mac', label: 'MAC', cls: 'mono' }, { k: 'comment', label: 'Comment' }], r.hosts)) : '') +
    (r.records.length ? sec('DNS records', table([{ k: 'zone', label: 'Zone', fmt: (v, x) => `<a href="#/dns/${x.zone_id}">${esc(v)}</a>` }, { k: 'name', label: 'Name', cls: 'mono' }, { k: 'type', label: 'Type' }, { k: 'value', label: 'Value', cls: 'mono' }], r.records)) : '') +
    (r.leases.length ? sec('DHCP leases', table([{ k: 'ip', label: 'IP', cls: 'mono' }, { k: 'mac', label: 'MAC', cls: 'mono' }, { k: 'hostname', label: 'Client' }, { k: 'expires', label: 'Expires', cls: 'mono' }], r.leases)) : ''));
}

/* ---------------- shell */
const ROUTES = [[/^#\/?(dashboard)?$/, vDashboard], [/^#\/dashboard\/(overview|dns|dhcp)$/, vDashboard], [/^#\/ipam$/, vIpam], [/^#\/ipam\/(\d+)$/, vNetwork], [/^#\/dns$/, vDns], [/^#\/dns\/(\d+)$/, vZone], [/^#\/resolver$/, vResolver],
  [/^#\/dhcp$/, vDhcp], [/^#\/hosts$/, vHosts], [/^#\/audit$/, vAudit], [/^#\/admin$/, vAdmin], [/^#\/deploy$/, vDeploy], [/^#\/health$/, vHealth], [/^#\/system$/, vSystem], [/^#\/search\/(.+)$/, vSearch]];
async function refreshMe() {
  ME = await api('GET', '/me');
  $('#uname').textContent = `${ME.user.username} (${ME.user.role === 'admin' ? 'admin' : 'read only'})`;
  $('#pending').hidden = !ME.pending || location.hash === '#/deploy';
  $('#footver').textContent = ME.version;
  if (ME.slogan) $('#footslogan').textContent = ME.slogan;
}
async function route() {
  H = {};
  const h = location.hash || '#/dashboard';
  const sect = (h.match(/^#\/(\w+)/) || [0, 'dashboard'])[1];
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.dataset.nav === sect));
  try {
    if (!ME) await refreshMe();
    for (const [re, fn] of ROUTES) {
      const m = h.match(re);
      if (m) { await fn(...m.slice(1).filter(x => x !== undefined && x !== 'dashboard').map(decodeURIComponent)); await refreshMe(); return; }
    }
    main('<div class="panel empty">This page does not exist. <a href="#/dashboard">Go to the dashboard</a>.</div>');
  } catch (e) {
    if (e.message !== 'Unauthorized') main(`<div class="panel"><b>Could not load this page.</b><p class="muted">${esc(e.message)}</p></div>`);
  }
}
function showLogin() { ME = null; $('#login').hidden = false; $('#loginForm input[name=username]').focus(); }
$('#loginForm').onsubmit = async e => {
  e.preventDefault();
  const f = e.target;
  try {
    await api('POST', '/login', { username: f.username.value, password: f.password.value });
    $('#login').hidden = true; f.password.value = ''; $('#loginErr').textContent = ''; route();
  } catch (err) { $('#loginErr').textContent = err.message; }
};
$('#logout').onclick = async () => { await api('POST', '/logout'); showLogin(); };
$('#search').onsubmit = e => { e.preventDefault(); const v = e.target.querySelector('input').value.trim(); if (v) location.hash = '#/search/' + encodeURIComponent(v); };
const themeSel = $('#theme');
try { themeSel.value = localStorage.getItem('diddy-theme') || localStorage.getItem('liteddi-theme') || 'light'; } catch (e) { }
themeSel.onchange = () => {
  document.documentElement.dataset.theme = themeSel.value;
  if (typeof drawGrid === 'function' && document.getElementById('dash-grid')) setTimeout(drawGrid, 30);
  try { localStorage.setItem('diddy-theme', themeSel.value); } catch (e) { }
};
window.addEventListener('hashchange', route);
route();
