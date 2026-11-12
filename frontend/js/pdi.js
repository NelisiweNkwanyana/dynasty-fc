/* =====================================================================
   Dynasty FC — Player Development Intelligence + dashboard enhancements
   No external dependencies. Charts are hand-built SVG.
   ===================================================================== */
(() => {
    'use strict';

    const ATTRS = [
        ['pace', 'PAC', 'Pace'], ['technique', 'TEC', 'Technique'], ['passing', 'PAS', 'Passing'],
        ['defending', 'DEF', 'Defending'], ['physical', 'PHY', 'Physical'], ['mentality', 'MEN', 'Mentality'],
    ];
    const POS = {
        'goalkeeper': 'GK', 'centre back': 'CB', 'center back': 'CB', 'right back': 'RB', 'left back': 'LB',
        'wing back': 'WB', 'defensive midfielder': 'CDM', 'central midfielder': 'CM', 'attacking midfielder': 'CAM',
        'left winger': 'LW', 'right winger': 'RW', 'striker': 'ST', 'forward': 'FW', 'defender': 'DEF', 'midfielder': 'MID',
    };
    const POSITIONS = ['Goalkeeper', 'Centre Back', 'Right Back', 'Left Back', 'Wing Back', 'Defensive Midfielder',
        'Central Midfielder', 'Attacking Midfielder', 'Left Winger', 'Right Winger', 'Striker'];
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    const state = {
        squad: [], insights: null, team: '', q: '', sort: 'overall',
        compare: false, picked: [], current: null, order: [], started: false, matches: [],
    };

    // ------------------------------------------------------------ utilities
    const $ = (s, r = document) => r.querySelector(s);
    const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const posAbbr = p => POS[(p || '').toLowerCase()] || (p ? p.slice(0, 3).toUpperCase() : '—');
    const fullName = p => `${p.first_name || ''} ${p.last_name || ''}`.trim();
    const photo = p => p && p.photo_url ? esc(p.photo_url) : '';
    const fmtDate = d => d ? new Date(d + 'T00:00:00').toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
    const shortDate = d => d ? new Date(d + 'T00:00:00').toLocaleDateString('en-ZA', { day: 'numeric', month: 'short' }) : '';
    const today = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); };
    const plural = (n, w) => `${n ?? 0} ${w}${n === 1 ? '' : 's'}`;
    let uid = 0;

    async function api(url, opts = {}) {
        const res = await fetch(url, { credentials: 'same-origin', ...opts });
        let data = {};
        try { data = await res.json(); } catch (e) { /* empty body */ }
        if (!res.ok || data.error) throw new Error(data.error || data.message || `Request failed (${res.status})`);
        return data;
    }
    const jsonBody = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

    // -------------------------------------------------------------- toasts
    function toast(msg, type = 'ok') {
        let host = $('.toasts');
        if (!host) { host = document.createElement('div'); host.className = 'toasts'; host.setAttribute('aria-live', 'polite'); document.body.appendChild(host); }
        const t = document.createElement('div');
        t.className = `toast ${type}`;
        t.innerHTML = `<i class="fas ${type === 'err' ? 'fa-circle-exclamation' : 'fa-circle-check'}"></i><span>${esc(msg)}</span>`;
        host.appendChild(t);
        setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 320); }, type === 'err' ? 5200 : 3200);
    }
    // Upgrade the dashboard's existing alert() feedback to toasts.
    window.alert = m => toast(String(m), /error|fail|invalid|could not|cannot|occurred/i.test(String(m)) ? 'err' : 'ok');

    // ---------------------------------------------------------- count-ups
    function animateCount(el, to, { decimals = 0, suffix = '', duration = 1100 } = {}) {
        if (!el) return;
        const target = Number(to) || 0;
        if (reduceMotion) { el.textContent = target.toFixed(decimals) + suffix; return; }
        const start = performance.now();
        const step = now => {
            const k = Math.min(1, (now - start) / duration);
            const e = 1 - Math.pow(1 - k, 3);
            el.textContent = (target * e).toFixed(decimals) + suffix;
            if (k < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    }
    window.animateCount = animateCount;

    // --------------------------------------------------------- SVG charts
    function radar(series, { size = 280, labels = true } = {}) {
        const c = size / 2, R = size * (labels ? 0.31 : 0.4), n = ATTRS.length;
        const pt = (i, v) => {
            const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
            return [c + Math.cos(a) * R * (v / 100), c + Math.sin(a) * R * (v / 100)];
        };
        let g = '';
        [25, 50, 75, 100].forEach(r => {
            g += `<polygon points="${ATTRS.map((_, i) => pt(i, r).join(',')).join(' ')}" fill="${r === 100 ? 'rgba(10,26,51,.03)' : 'none'}" stroke="#dfe4ec" stroke-width="1"/>`;
        });
        ATTRS.forEach((_, i) => { const [x, y] = pt(i, 100); g += `<line x1="${c}" y1="${c}" x2="${x}" y2="${y}" stroke="#e6eaf1"/>`; });
        series.forEach((s, si) => {
            if (!s.values) return;
            const pts = ATTRS.map(([k], i) => pt(i, s.values[k] || 0).join(',')).join(' ');
            g += `<polygon class="rd-poly" style="animation-delay:${si * 120}ms" points="${pts}" fill="${s.fill}" stroke="${s.stroke}" stroke-width="2.2" stroke-linejoin="round" ${s.dash ? 'stroke-dasharray="5 4"' : ''}/>`;
            if (!s.dash) ATTRS.forEach(([k], i) => { const [x, y] = pt(i, s.values[k] || 0); g += `<circle cx="${x}" cy="${y}" r="3.4" fill="#fff" stroke="${s.stroke}" stroke-width="2"/>`; });
        });
        if (labels) {
            const main = series.find(s => s.values && !s.dash) || series[0];
            ATTRS.forEach(([k, ab], i) => {
                const [x, y] = pt(i, 132);
                const v = main && main.values ? main.values[k] : '';
                g += `<text x="${x}" y="${y - 6}" text-anchor="middle" font-size="11" font-weight="700" fill="#6b778c" font-family="Manrope, sans-serif">${ab}</text>`;
                g += `<text x="${x}" y="${y + 12}" text-anchor="middle" font-size="16" font-weight="700" fill="#0f1b2d" font-family="'Barlow Condensed', sans-serif">${v}</text>`;
            });
        }
        return `<svg viewBox="0 0 ${size} ${size}" width="100%" style="max-width:${size}px" role="img" aria-label="Attribute radar chart"><style>.rd-poly{transform-origin:${c}px ${c}px;animation:rdIn .8s cubic-bezier(.2,.8,.2,1) both}@keyframes rdIn{from{transform:scale(.2);opacity:0}to{transform:none;opacity:1}}</style>${g}</svg>`;
    }

    function lineChart(points, { w = 540, h = 210, color = '#b8944a', label = 'Overall' } = {}) {
        if (!points.length) return '<div class="empty">No assessments recorded yet.</div>';
        const pad = { l: 34, r: 18, t: 22, b: 30 };
        const vals = points.map(p => p.v);
        let lo = Math.max(0, Math.floor((Math.min(...vals) - 6) / 5) * 5);
        let hi = Math.min(100, Math.ceil((Math.max(...vals) + 6) / 5) * 5);
        if (hi - lo < 10) { lo = Math.max(0, lo - 5); hi = Math.min(100, hi + 5); }
        const X = i => pad.l + (points.length === 1 ? (w - pad.l - pad.r) / 2 : i * (w - pad.l - pad.r) / (points.length - 1));
        const Y = v => pad.t + (1 - (v - lo) / (hi - lo)) * (h - pad.t - pad.b);
        const id = 'lg' + (++uid);
        let grid = '';
        for (let v = lo; v <= hi; v += (hi - lo) / 4) {
            grid += `<line x1="${pad.l}" x2="${w - pad.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="#edf0f5"/><text x="${pad.l - 8}" y="${Y(v) + 4}" text-anchor="end" font-size="10.5" fill="#94a0b4" font-family="Manrope">${Math.round(v)}</text>`;
        }
        const path = points.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ');
        const area = `${path} L${X(points.length - 1)},${h - pad.b} L${X(0)},${h - pad.b} Z`;
        const dots = points.map((p, i) => `<g><circle cx="${X(i)}" cy="${Y(p.v)}" r="4.5" fill="#fff" stroke="${color}" stroke-width="2.5"/><text x="${X(i)}" y="${Y(p.v) - 11}" text-anchor="middle" font-size="12.5" font-weight="700" fill="#0f1b2d" font-family="'Barlow Condensed'">${p.v}</text><text x="${X(i)}" y="${h - 10}" text-anchor="middle" font-size="10.5" fill="#94a0b4" font-family="Manrope">${esc(p.label)}</text></g>`).join('');
        return `<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="${esc(label)} progression chart">
            <defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>
            ${grid}<path d="${area}" fill="url(#${id})"/>
            <path d="${path}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" pathLength="1" style="stroke-dasharray:1;stroke-dashoffset:1;animation:draw 1.1s .1s cubic-bezier(.2,.8,.2,1) forwards"/>
            <style>@keyframes draw{to{stroke-dashoffset:0}}</style>${dots}</svg>`;
    }

    function ring(pct, { size = 54, stroke = 6, color = '#b8944a', text } = {}) {
        const r = (size - stroke) / 2, C = 2 * Math.PI * r, v = Math.max(0, Math.min(100, pct || 0));
        return `<svg class="ring" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" role="img" aria-label="${v}%">
            <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="#edf0f5" stroke-width="${stroke}"/>
            <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round"
                stroke-dasharray="${C}" stroke-dashoffset="${C}" transform="rotate(-90 ${size / 2} ${size / 2})"
                style="transition:stroke-dashoffset 1.1s cubic-bezier(.2,.8,.2,1)" data-offset="${C * (1 - v / 100)}"/>
            <text x="50%" y="50%" dy=".35em" text-anchor="middle" font-family="'Barlow Condensed'" font-weight="700" font-size="${size * .3}" fill="#0f1b2d">${text ?? v}</text></svg>`;
    }
    const kickRings = root => requestAnimationFrame(() => requestAnimationFrame(() =>
        $$('circle[data-offset]', root).forEach(c => c.setAttribute('stroke-dashoffset', c.dataset.offset))));

    const trendChip = d => {
        if (d === null || d === undefined) return '<span class="chip flat">New</span>';
        if (d > 0) return `<span class="chip up"><i class="fas fa-arrow-trend-up"></i> +${d}</span>`;
        if (d < 0) return `<span class="chip down"><i class="fas fa-arrow-trend-down"></i> ${d}</span>`;
        return '<span class="chip flat">±0</span>';
    };

    // ------------------------------------------------------------- data
    async function loadSquad() {
        const data = await api('/api/pdi/squad');
        state.squad = data.players || [];
        state.season = data.season;
    }
    async function loadInsights() {
        state.insights = await api('/api/pdi/insights' + (state.team ? `?team=${encodeURIComponent(state.team)}` : ''));
    }
    const byId = id => state.squad.find(p => p.player_id === id);
    window.PDI_photoFor = id => (byId(id) || {}).photo_url || null;

    // -------------------------------------------------- development panel
    function visiblePlayers() {
        const q = state.q.trim().toLowerCase();
        let list = state.squad.filter(p => (!state.team || p.team_group === state.team) &&
            (!q || `${fullName(p)} ${p.position || ''} ${posAbbr(p.position)} ${p.jersey_number ?? ''}`.toLowerCase().includes(q)));
        const s = state.sort;
        const num = v => (v === null || v === undefined ? -999 : v);
        list.sort((a, b) =>
            s === 'name' ? fullName(a).localeCompare(fullName(b)) :
            s === 'improved' ? num(b.season_change) - num(a.season_change) :
            s === 'goals' ? (b.stats.goals - a.stats.goals) || (b.stats.assists - a.stats.assists) :
            s === 'attendance' ? num(b.stats.attendance_pct) - num(a.stats.attendance_pct) :
            s === 'number' ? num(a.jersey_number) - num(b.jersey_number) :
            num(b.overall) - num(a.overall));
        state.order = list.map(p => p.player_id);
        return list;
    }

    function cardHTML(p, i) {
        const a = p.attributes || {};
        const d = p.overall !== null && p.previous_overall !== null ? p.overall - p.previous_overall : null;
        return `<div class="pcard ${p.tier} ${state.picked.includes(p.player_id) ? 'picked' : ''}" role="button" tabindex="0"
                    data-id="${p.player_id}" style="animation-delay:${Math.min(i, 16) * 35}ms" aria-label="${esc(fullName(p))}, overall ${p.overall ?? 'unrated'}">
            ${p.photo_url ? `<div class="photo"><img src="${photo(p)}" alt="" loading="lazy"></div>` : '<div class="noimg"><i class="fas fa-user"></i></div>'}
            <div class="shine"></div>
            <div class="ovr"><b>${p.overall ?? '–'}</b><span>${posAbbr(p.position)}</span></div>
            <div class="num">${p.jersey_number ? '#' + esc(p.jersey_number) : ''}</div>
            <div class="flags">
                ${p.is_injured ? '<span class="flag inj"><i class="fas fa-kit-medical"></i> INJ</span>' : ''}
                ${d > 0 ? `<span class="flag up">▲ ${d}</span>` : d < 0 ? `<span class="flag down">▼ ${Math.abs(d)}</span>` : ''}
            </div>
            <div class="body">
                <div class="nm">${esc(p.last_name || p.first_name)}</div>
                <div class="pos">${esc(p.first_name)} · ${esc(p.team_group || '')}</div>
                <div class="attrs">${ATTRS.map(([k, ab]) => `<div><b>${a[k] ?? '–'}</b><span>${ab}</span></div>`).join('')}</div>
            </div>
            <button class="pick" type="button" aria-label="Select for comparison"><i class="fas fa-check"></i></button>
        </div>`;
    }

    function renderSquad() {
        const grid = $('#pdiGrid');
        if (!grid) return;
        const list = visiblePlayers();
        $('#pdiCount').textContent = `${list.length} player${list.length === 1 ? '' : 's'}`;
        grid.innerHTML = list.length ? list.map(cardHTML).join('') :
            `<div class="insight" style="grid-column:1/-1;text-align:center;padding:40px"><i class="fas fa-user-slash" style="font-size:28px;color:#b8944a"></i><p style="margin-top:10px;color:#6b778c">No players match your filters.</p></div>`;
    }

    function renderKPIs() {
        const ins = state.insights;
        if (!ins) return;
        const best = (ins.most_improved || [])[0];
        $('#pdiKpis').innerHTML = `
            <div class="kpi">${ring(ins.avg_overall ?? 0, { text: ins.avg_overall !== null ? Math.round(ins.avg_overall) : '–' })}<div><div class="v sm">Squad avg</div><div class="l">${ins.rated}/${ins.squad_size} rated</div></div></div>
            <div class="kpi">${ring(ins.avg_attendance ?? 0, { color: '#1f9d6b', text: ins.avg_attendance !== null ? ins.avg_attendance + '%' : '–' })}<div><div class="v sm">Training</div><div class="l">Attendance avg</div></div></div>
            <div class="kpi"><div class="ic" style="background:rgba(184,148,74,.14);color:#b8944a"><i class="fas fa-futbol"></i></div><div><div class="v" data-count="${ins.total_goals}">0</div><div class="l">Goals scored</div></div></div>
            <div class="kpi"><div class="ic" style="background:rgba(31,157,107,.12);color:#1f9d6b"><i class="fas fa-rocket"></i></div><div style="min-width:0"><div class="v" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${best ? esc(best.last_name) : '–'}</div><div class="l">Most improved ${best && best.season_change !== null ? '(+' + best.season_change + ')' : ''}</div></div></div>
            <div class="kpi"><div class="ic" style="background:rgba(214,69,69,.1);color:#d64545"><i class="fas fa-kit-medical"></i></div><div><div class="v" data-count="${(ins.injured || []).length}">0</div><div class="l">Injured</div></div></div>`;
        $$('#pdiKpis [data-count]').forEach(el => animateCount(el, el.dataset.count));
        kickRings($('#pdiKpis'));
        const chips = [`<span class="chip gold"><i class="fas fa-calendar"></i> Season ${esc(ins.season)}</span>`];
        if (ins.has_sample_data) chips.push('<span class="chip warn" title="Seeded sample stats and assessments – replace with real records"><i class="fas fa-flask"></i> Includes sample data</span>');
        $('#pdiChips').innerHTML = chips.join('');
    }

    function miniItem(p, right) {
        return `<li data-open="${p.player_id}">${p.photo_url ? `<img src="${esc(p.photo_url)}" alt="">` : '<span class="ph"><i class="fas fa-user"></i></span>'}
            <span class="nm">${esc(fullName(p))}<small>${esc(p.position || '')} · ${esc(p.team_group || '')}</small></span>${right}</li>`;
    }

    function renderInsights() {
        const ins = state.insights;
        const host = $('#pdiInsights');
        if (!ins || !host) return;
        const improved = (ins.most_improved || []).slice(0, 4);
        const alerts = (ins.attendance_alerts || []).slice(0, 4);
        const needs = (ins.needs_assessment || []).slice(0, 4);
        const attrs = ins.squad_attributes || {};
        host.innerHTML = `
            <div class="insight"><h4><i class="fas fa-chart-line"></i> Squad progression</h4>
                ${lineChart((ins.trend || []).map(t => ({ v: Math.round(t.overall), label: shortDate(t.date) })), { w: 320, h: 160 })}</div>
            <div class="insight"><h4><i class="fas fa-rocket"></i> Most improved</h4>
                <ul class="mini-list">${improved.map(p => miniItem(p, trendChip(p.season_change))).join('') || '<li class="empty">Not enough assessments yet.</li>'}</ul></div>
            <div class="insight"><h4><i class="fas fa-bell"></i> Attendance alerts</h4>
                ${alerts.length ? `<ul class="mini-list">${alerts.map(p => miniItem(p, `<span class="chip down">${p.stats.attendance_pct}%</span>`)).join('')}</ul>`
                    : '<div class="empty"><i class="fas fa-circle-check" style="color:#1f9d6b"></i> Everyone is above 75% attendance.</div>'}</div>
            <div class="insight"><h4><i class="fas fa-clipboard-check"></i> Due for assessment</h4>
                ${needs.length ? `<ul class="mini-list">${needs.map(p => miniItem(p, '<span class="chip warn">Due</span>')).join('')}</ul>`
                    : '<div class="empty"><i class="fas fa-circle-check" style="color:#1f9d6b"></i> All players assessed in the last 45 days.</div>'}</div>
            <div class="insight"><h4><i class="fas fa-sliders"></i> Squad profile</h4>
                <div class="attr-bars">${ATTRS.map(([k, , name]) => `<div class="attr-bar"><span class="lbl">${name}</span><div class="track"><div class="fill" data-w="${attrs[k] || 0}"></div></div><b>${attrs[k] ? Math.round(attrs[k]) : '–'}</b></div>`).join('')}</div></div>`;
        requestAnimationFrame(() => requestAnimationFrame(() => $$('.fill[data-w]', host).forEach(f => f.style.width = f.dataset.w + '%')));
    }

    async function refreshDevelopment({ squad = true } = {}) {
        try {
            if (squad) await loadSquad();
            await loadInsights();
            renderSquad(); renderKPIs(); renderInsights(); renderSpotlight();
        } catch (e) {
            const grid = $('#pdiGrid');
            if (grid) grid.innerHTML = `<div class="insight" style="grid-column:1/-1;padding:30px;text-align:center"><i class="fas fa-triangle-exclamation" style="color:#d64545;font-size:24px"></i><p style="margin-top:10px">Couldn't load player development data.</p><p style="color:#6b778c;font-size:13px">${esc(e.message)}</p></div>`;
        }
    }

    function bindDevelopment() {
        const panel = $('#panel-development');
        if (!panel || panel.dataset.bound) return;
        panel.dataset.bound = '1';
        $$('#pdiTeams button').forEach(b => b.addEventListener('click', () => {
            $$('#pdiTeams button').forEach(x => x.classList.toggle('on', x === b));
            state.team = b.dataset.team;
            renderSquad();
            loadInsights().then(() => { renderKPIs(); renderInsights(); }).catch(() => {});
        }));
        $('#pdiSearch').addEventListener('input', e => { state.q = e.target.value; renderSquad(); });
        $('#pdiSort').addEventListener('change', e => { state.sort = e.target.value; renderSquad(); });
        $('#pdiCompareBtn').addEventListener('click', toggleCompare);
        $('#pdiCompareGo').addEventListener('click', openCompare);
        $('#pdiCompareCancel').addEventListener('click', toggleCompare);

        const grid = $('#pdiGrid');
        grid.addEventListener('click', e => {
            const card = e.target.closest('.pcard'); if (!card) return;
            const id = +card.dataset.id;
            if (state.compare) return pick(id);
            openPlayer(id);
        });
        grid.addEventListener('keydown', e => {
            const card = e.target.closest('.pcard');
            if (card && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); card.click(); }
        });
        if (!reduceMotion && window.matchMedia('(hover: hover)').matches) {
            grid.addEventListener('pointermove', e => {
                const card = e.target.closest('.pcard'); if (!card) return;
                const r = card.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
                card.style.transform = `perspective(700px) rotateY(${(x - .5) * 10}deg) rotateX(${(.5 - y) * 10}deg) translateY(-4px)`;
                card.style.setProperty('--mx', x * 100 + '%'); card.style.setProperty('--my', y * 100 + '%');
            });
            grid.addEventListener('pointerout', e => {
                const card = e.target.closest('.pcard');
                if (card && !card.contains(e.relatedTarget)) card.style.transform = '';
            });
        }
        $('#pdiInsights').addEventListener('click', e => { const li = e.target.closest('[data-open]'); if (li) openPlayer(+li.dataset.open); });
    }

    // ----------------------------------------------------------- compare
    function toggleCompare() {
        state.compare = !state.compare;
        state.picked = [];
        $('#panel-development').classList.toggle('compare-mode', state.compare);
        $('#pdiCompareBtn').classList.toggle('active', state.compare);
        renderCompareBar(); renderSquad();
        if (state.compare) toast('Pick two players to compare');
    }
    function pick(id) {
        const i = state.picked.indexOf(id);
        if (i >= 0) state.picked.splice(i, 1);
        else { if (state.picked.length === 2) state.picked.shift(); state.picked.push(id); }
        $$('#pdiGrid .pcard').forEach(c => c.classList.toggle('picked', state.picked.includes(+c.dataset.id)));
        renderCompareBar();
    }
    function renderCompareBar() {
        const slots = [0, 1].map(i => {
            const p = byId(state.picked[i]);
            return p ? `<span class="slot">${p.photo_url ? `<img src="${esc(p.photo_url)}" alt="">` : ''}${esc(fullName(p))}</span>` : `<span class="slot empty">Player ${i + 1}</span>`;
        });
        $('#pdiCompareSlots').innerHTML = slots.join('');
        $('#pdiCompareGo').disabled = state.picked.length !== 2;
    }
    function openCompare() {
        const [a, b] = state.picked.map(byId);
        if (!a || !b) return;
        const ca = '#b8944a', cb = '#3b82f6';
        const side = (p, col, cls) => `<div class="side ${cls}"><img src="${photo(p)}" alt="" style="border-color:${col}">
            <div><div class="nm">${esc(fullName(p))}</div><div style="color:#b9c5da;font-size:13px">${esc(p.position || '')} · ${esc(p.team_group || '')}</div>
            <div class="ov" style="color:${col}">${p.overall ?? '–'}</div></div></div>`;
        const rows = ATTRS.map(([k, ab]) => {
            const va = (a.attributes || {})[k] || 0, vb = (b.attributes || {})[k] || 0;
            return `<div class="diff-row"><b class="${va > vb ? 'win' : ''}">${va}</b><div class="bar l"><span style="width:0;background:${ca}" data-w="${va}"></span></div>
                <span class="lbl">${ab}</span><div class="bar"><span style="width:0;background:${cb}" data-w="${vb}"></span></div><b class="${vb > va ? 'win' : ''}">${vb}</b></div>`;
        }).join('');
        const statRow = (label, va, vb, suffix = '') => `<div class="diff-row"><b class="${va > vb ? 'win' : ''}">${va ?? '–'}${suffix}</b><span></span><span class="lbl">${label}</span><span></span><b class="${vb > va ? 'win' : ''}">${vb ?? '–'}${suffix}</b></div>`;
        $('#cmpContent').innerHTML = `
            <div class="cmp-head">${side(a, ca, '')}<div class="vs">VS</div>${side(b, cb, 'r')}
                <button class="drawer-close" data-close-cmp aria-label="Close comparison"><i class="fas fa-xmark"></i></button></div>
            <div class="cmp-body">
                <div class="card-s"><h5>Attribute overlay</h5><div class="radar-wrap">${radar([
                    { values: a.attributes, fill: 'rgba(184,148,74,.22)', stroke: ca },
                    { values: b.attributes, fill: 'rgba(59,130,246,.16)', stroke: cb }], { size: 300, labels: false })}</div>
                    <div class="legend"><span><i style="background:${ca}"></i>${esc(a.last_name)}</span><span><i style="background:${cb}"></i>${esc(b.last_name)}</span></div></div>
                <div class="card-s"><h5>Head to head</h5>${rows}
                    <div style="height:8px"></div>
                    ${statRow('Goals', a.stats.goals, b.stats.goals)}${statRow('Assists', a.stats.assists, b.stats.assists)}
                    ${statRow('Apps', a.stats.appearances, b.stats.appearances)}${statRow('Attend', a.stats.attendance_pct, b.stats.attendance_pct, '%')}
                    ${statRow('Growth', a.season_change, b.season_change)}</div>
            </div>`;
        $('#cmpModal').classList.add('open');
        requestAnimationFrame(() => requestAnimationFrame(() => $$('#cmpContent [data-w]').forEach(s => s.style.width = s.dataset.w + '%')));
    }

    // ------------------------------------------------------------ drawer
    async function openPlayer(id, tab = 'overview') {
        const drawer = $('#pdiDrawer'), bg = $('#pdiDrawerBg');
        if (!drawer) return;
        if (!state.order.length || !state.order.includes(id)) visiblePlayers();
        drawer.classList.add('open'); bg.classList.add('open');
        document.body.style.overflow = 'hidden';
        const cached = byId(id);
        if (cached) renderDrawerHead(cached);
        $('#pdiDrawerBody').innerHTML = '<div class="skeleton" style="aspect-ratio:auto;height:260px;margin-bottom:14px"></div><div class="skeleton" style="aspect-ratio:auto;height:160px"></div>';
        try {
            const p = await api(`/api/pdi/players/${id}`);
            state.current = p;
            renderDrawerHead(p);
            renderDrawerBody(p, tab);
        } catch (e) {
            $('#pdiDrawerBody').innerHTML = `<div class="card-s">Couldn't load this player: ${esc(e.message)}</div>`;
        }
    }
    function closeDrawer() {
        $('#pdiDrawer')?.classList.remove('open'); $('#pdiDrawerBg')?.classList.remove('open');
        document.body.style.overflow = '';
        state.current = null;
    }
    function stepPlayer(dir) {
        if (!state.current || !state.order.length) return;
        const i = state.order.indexOf(state.current.player_id);
        const next = state.order[(i + dir + state.order.length) % state.order.length];
        const tab = $('.tabs button.on', $('#pdiDrawer'))?.dataset.tab || 'overview';
        openPlayer(next, tab === 'edit' || tab === 'assess' ? 'overview' : tab);
    }

    function renderDrawerHead(p) {
        $('#pdiDrawerHead').innerHTML = `
            <div class="drawer-nav"><button data-step="-1" aria-label="Previous player"><i class="fas fa-chevron-left"></i></button><button data-step="1" aria-label="Next player"><i class="fas fa-chevron-right"></i></button></div>
            <button class="drawer-close" data-close-drawer aria-label="Close"><i class="fas fa-xmark"></i></button>
            ${p.photo_url ? `<img class="bigphoto" src="${photo(p)}" alt="Photo of ${esc(fullName(p))}">` : '<div class="bigphoto" style="display:grid;place-items:center;font-size:40px;color:#4a5f82"><i class="fas fa-user"></i></div>'}
            <div class="info">
                <div class="team">${esc(p.team_group || '')} · ${esc(p.age_group || '')}</div>
                <h2>${esc(p.first_name)}<br>${esc(p.last_name)}</h2>
                <div class="sub"><span><i class="fas fa-shirt"></i> ${p.jersey_number ? '#' + esc(p.jersey_number) : '—'}</span><span>${esc(p.position || 'Position not set')}</span>${p.preferred_foot ? `<span><i class="fas fa-shoe-prints"></i> ${esc(p.preferred_foot)} foot</span>` : ''}
                ${p.is_injured ? '<span style="color:#ff9c9c"><i class="fas fa-kit-medical"></i> Injured</span>' : ''}</div>
            </div>
            <div class="bigovr"><b>${p.overall ?? '–'}</b><span>OVERALL</span></div>`;
    }

    function renderDrawerBody(p, tab) {
        const hist = p.assessments || [];
        const latest = hist[hist.length - 1], prev = hist[hist.length - 2], first = hist[0];
        const s = p.stats || {};
        const ga = s.appearances ? ((s.goals + s.assists) / s.appearances).toFixed(2) : '–';
        const delta = k => (latest && prev) ? latest[k] - prev[k] : null;

        const overview = `
            ${p.development_focus ? `<div class="focus-box"><i class="fas fa-bullseye"></i><div><b>Development focus</b>${esc(p.development_focus)}</div></div>` : ''}
            <div class="two">
                <div class="card-s"><h5>Attributes <span>${latest ? 'as of ' + fmtDate(latest.assessed_on) : ''}</span></h5>
                    <div class="radar-wrap">${latest ? radar([
                        first && hist.length > 1 ? { values: first, fill: 'rgba(148,160,180,.10)', stroke: '#94a0b4', dash: true } : null,
                        { values: latest, fill: 'rgba(184,148,74,.22)', stroke: '#b8944a' }].filter(Boolean), { size: 260 }) : '<div class="empty">No assessment yet.</div>'}</div>
                    ${hist.length > 1 ? '<div class="legend"><span><i style="background:#b8944a"></i>Now</span><span><i style="background:#94a0b4"></i>Start of season</span></div>' : ''}</div>
                <div class="card-s"><h5>Since last assessment</h5>
                    <div class="attr-bars">${ATTRS.map(([k, , name]) => {
                        const d = delta(k);
                        return `<div class="attr-bar" style="grid-template-columns:78px 1fr 34px 40px"><span class="lbl">${name}</span><div class="track"><div class="fill" data-w="${latest ? latest[k] : 0}"></div></div><b>${latest ? latest[k] : '–'}</b>
                            <span style="font-size:12px;font-weight:700;color:${d > 0 ? '#1f9d6b' : d < 0 ? '#d64545' : '#94a0b4'}">${d === null ? '' : d > 0 ? '+' + d : d}</span></div>`;
                    }).join('')}</div>
                    <div style="margin-top:16px;display:flex;gap:10px;align-items:center;justify-content:space-between;flex-wrap:wrap">
                        <span style="font-size:13px;color:#6b778c">Season growth</span>${trendChip(p.season_change)}</div></div>
            </div>
            <div class="card-s"><h5>Season ${esc(state.season || '')} <span>${s.minutes_played ? s.minutes_played + ' min played' : ''}</span></h5>
                <div class="stat-tiles"><div><b data-count="${s.appearances}">0</b><small>Apps</small></div><div><b data-count="${s.goals}">0</b><small>Goals</small></div>
                <div><b data-count="${s.assists}">0</b><small>Assists</small></div><div><b>${ga}</b><small>G+A / game</small></div></div></div>
            <div class="card-s" style="display:flex;align-items:center;gap:18px">
                ${ring(s.attendance_pct ?? 0, { size: 76, stroke: 8, color: (s.attendance_pct ?? 0) < 75 ? '#d64545' : '#1f9d6b', text: s.attendance_pct !== null && s.attendance_pct !== undefined ? s.attendance_pct + '%' : '–' })}
                <div><h5 style="margin-bottom:4px">Training attendance</h5><div style="font-size:14px">${s.sessions_attended || 0} of ${s.sessions_total || 0} sessions this season</div>
                ${(s.attendance_pct ?? 100) < 75 ? '<div style="color:#d64545;font-size:13px;margin-top:4px"><i class="fas fa-bell"></i> Below the 75% target – worth a check-in with the family.</div>' : ''}</div></div>`;

        const progress = `
            <div class="card-s"><h5>Progression <span class="seg" id="progSeg" style="padding:3px">${[['overall', 'OVR'], ...ATTRS.map(([k, ab]) => [k, ab])].map(([k, ab], i) => `<button data-k="${k}" class="${i ? '' : 'on'}" style="padding:5px 8px;font-size:11.5px">${ab}</button>`).join('')}</span></h5>
                <div id="progChart">${lineChart(hist.map(a => ({ v: a.overall, label: shortDate(a.assessed_on) })))}</div></div>
            <div class="card-s"><h5>Assessment history <span>${hist.length} record${hist.length === 1 ? '' : 's'}</span></h5>
                <ul class="timeline">${hist.slice().reverse().map((a, i, arr) => {
                    const before = arr[i + 1];
                    const d = before ? a.overall - before.overall : null;
                    return `<li><span class="dot">${a.overall}</span><div class="when">${fmtDate(a.assessed_on)}</div>
                        <div class="what">Overall ${a.overall} ${d === null ? '' : trendChip(d)}</div>
                        ${a.notes ? `<div class="note">${esc(a.notes)}</div>` : ''}</li>`;
                }).join('') || '<li class="empty">No assessments yet.</li>'}</ul></div>`;

        const base = latest || { pace: 60, technique: 60, passing: 60, defending: 60, physical: 60, mentality: 60 };
        const assess = `
            <form id="assessForm">
                <div class="preview-ovr"><div><div style="font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#b9c5da;font-weight:700">New overall</div>
                    <div style="font-size:13px;color:#b9c5da;margin-top:4px">Previous: ${latest ? latest.overall : '—'}</div></div><b id="assessOvr">${latest ? latest.overall : 60}</b></div>
                <div class="two">
                    <div class="card-s">${ATTRS.map(([k, , name]) => `<div class="slider-row"><label for="as_${k}">${name}</label>
                        <input class="gold" type="range" min="1" max="99" value="${base[k]}" id="as_${k}" name="${k}"><output id="out_${k}">${base[k]}</output></div>`).join('')}</div>
                    <div class="card-s"><h5>Live preview</h5><div class="radar-wrap" id="assessRadar"></div>
                        <div class="legend"><span><i style="background:#b8944a"></i>New</span><span><i style="background:#94a0b4"></i>Last</span></div></div>
                </div>
                <div class="card-s"><div class="form-grid">
                    <div class="fld"><label for="as_date">Assessment date</label><input type="date" id="as_date" value="${today()}" max="${today()}" required></div>
                    <div class="fld full"><label for="as_notes">Coach notes</label><textarea id="as_notes" rows="3" maxlength="2000" placeholder="Strengths, what to work on, how they responded…"></textarea></div>
                </div></div>
                <button class="btn-gold" type="submit" style="width:100%;padding:14px"><i class="fas fa-floppy-disk"></i> Save assessment</button>
            </form>`;

        const posOpts = POSITIONS.concat(p.position && !POSITIONS.includes(p.position) ? [p.position] : [])
            .map(x => `<option ${x === p.position ? 'selected' : ''}>${esc(x)}</option>`).join('');
        const edit = `
            <form id="editForm">
                <div class="card-s"><h5>Player details</h5><div class="form-grid">
                    <div class="fld"><label for="ed_first">First name</label><input id="ed_first" value="${esc(p.first_name)}" required maxlength="60"></div>
                    <div class="fld"><label for="ed_last">Last name</label><input id="ed_last" value="${esc(p.last_name)}" maxlength="60"></div>
                    <div class="fld"><label for="ed_pos">Position</label><select id="ed_pos">${posOpts}</select></div>
                    <div class="fld"><label for="ed_num">Jersey number</label><input id="ed_num" type="number" min="1" max="99" value="${esc(p.jersey_number ?? '')}"></div>
                    <div class="fld"><label for="ed_foot">Preferred foot</label><select id="ed_foot">${['', 'Right', 'Left', 'Both'].map(f => `<option value="${f}" ${f === (p.preferred_foot || '') ? 'selected' : ''}>${f || '—'}</option>`).join('')}</select></div>
                    <div class="fld"><label for="ed_inj">Fitness</label><select id="ed_inj"><option value="0" ${p.is_injured ? '' : 'selected'}>Fit</option><option value="1" ${p.is_injured ? 'selected' : ''}>Injured</option></select></div>
                    <div class="fld full"><label for="ed_focus">Development focus</label><input id="ed_focus" value="${esc(p.development_focus || '')}" maxlength="255" placeholder="e.g. Weak-foot finishing"></div>
                </div></div>
                <div class="card-s"><h5>Season ${esc(state.season || '')} stats</h5><div class="form-grid">
                    ${[['appearances', 'Appearances'], ['goals', 'Goals'], ['assists', 'Assists'], ['minutes_played', 'Minutes played'], ['sessions_attended', 'Sessions attended'], ['sessions_total', 'Total sessions']]
                        .map(([k, l]) => `<div class="fld"><label for="st_${k}">${l}</label><input id="st_${k}" type="number" min="0" value="${s[k] ?? 0}"></div>`).join('')}
                </div></div>
                <div class="card-s"><h5>Photo</h5><div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
                    <input type="file" id="ed_photo" accept="image/jpeg,image/png,image/webp" style="flex:1;min-width:200px">
                    <button type="button" class="btn-ghost" id="ed_photo_btn"><i class="fas fa-upload"></i> Upload</button></div>
                    <p style="font-size:12px;color:#6b778c;margin-top:8px">Only upload photos the player's guardian has consented to.</p></div>
                <button class="btn-gold" type="submit" style="width:100%;padding:14px"><i class="fas fa-floppy-disk"></i> Save changes</button>
            </form>`;

        $('#pdiDrawerBody').innerHTML = `
            <div class="tab-pane" data-pane="overview">${overview}</div>
            <div class="tab-pane" data-pane="progress">${progress}</div>
            <div class="tab-pane" data-pane="assess">${assess}</div>
            <div class="tab-pane" data-pane="edit">${edit}</div>`;
        switchTab(tab);
        bindDrawerForms(p, hist);
    }

    function switchTab(tab) {
        $$('#pdiDrawer .tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
        $$('#pdiDrawerBody .tab-pane').forEach(p => p.classList.toggle('on', p.dataset.pane === tab));
        const pane = $(`#pdiDrawerBody [data-pane="${tab}"]`);
        if (!pane) return;
        requestAnimationFrame(() => requestAnimationFrame(() => $$('.fill[data-w]', pane).forEach(f => f.style.width = f.dataset.w + '%')));
        $$('[data-count]', pane).forEach(el => animateCount(el, el.dataset.count, { duration: 800 }));
        kickRings(pane);
        $('#pdiDrawerBody').scrollTop = 0;
    }

    function bindDrawerForms(p, hist) {
        const latest = hist[hist.length - 1];
        // progression attribute switcher
        $('#progSeg')?.addEventListener('click', e => {
            const b = e.target.closest('button'); if (!b) return;
            $$('#progSeg button').forEach(x => x.classList.toggle('on', x === b));
            const k = b.dataset.k;
            $('#progChart').innerHTML = lineChart(hist.map(a => ({ v: a[k], label: shortDate(a.assessed_on) })), { label: b.textContent });
        });

        // assessment sliders + live radar
        const form = $('#assessForm');
        const readVals = () => Object.fromEntries(ATTRS.map(([k]) => [k, +$('#as_' + k).value]));
        const paint = () => {
            const v = readVals();
            ATTRS.forEach(([k]) => { const el = $('#as_' + k); $('#out_' + k).textContent = el.value; el.style.setProperty('--p', ((el.value - 1) / 98 * 100) + '%'); });
            const ovr = Math.round(Object.values(v).reduce((a, b) => a + b, 0) / ATTRS.length);
            const o = $('#assessOvr'); o.textContent = ovr;
            o.style.color = latest && ovr > latest.overall ? '#4be3a4' : latest && ovr < latest.overall ? '#ff9c9c' : '';
            $('#assessRadar').innerHTML = radar([latest ? { values: latest, fill: 'rgba(148,160,180,.10)', stroke: '#94a0b4', dash: true } : null,
                { values: v, fill: 'rgba(184,148,74,.22)', stroke: '#b8944a' }].filter(Boolean), { size: 240 }).replace(/class="rd-poly"/g, '');
        };
        ATTRS.forEach(([k]) => $('#as_' + k).addEventListener('input', paint));
        paint();
        form.addEventListener('submit', async e => {
            e.preventDefault();
            const btn = form.querySelector('button[type=submit]'); btn.disabled = true;
            try {
                const res = await api(`/api/pdi/players/${p.player_id}/assessments`, jsonBody('POST', { ...readVals(), assessed_on: $('#as_date').value, notes: $('#as_notes').value }));
                toast(`Assessment saved – ${p.first_name} is now ${res.overall} overall`);
                await refreshDevelopment();
                openPlayer(p.player_id, 'progress');
            } catch (err) { toast(err.message, 'err'); btn.disabled = false; }
        });

        // edit details + stats
        const ef = $('#editForm');
        ef.addEventListener('submit', async e => {
            e.preventDefault();
            const btn = ef.querySelector('button[type=submit]'); btn.disabled = true;
            const stats = Object.fromEntries(['appearances', 'goals', 'assists', 'minutes_played', 'sessions_attended', 'sessions_total'].map(k => [k, +($('#st_' + k).value || 0)]));
            try {
                await api(`/api/pdi/players/${p.player_id}/profile`, jsonBody('PUT', {
                    first_name: $('#ed_first').value.trim(), last_name: $('#ed_last').value.trim(), position: $('#ed_pos').value,
                    jersey_number: $('#ed_num').value ? +$('#ed_num').value : null, is_injured: +$('#ed_inj').value,
                    preferred_foot: $('#ed_foot').value || null, development_focus: $('#ed_focus').value.trim() || null,
                }));
                await api(`/api/pdi/players/${p.player_id}/stats`, jsonBody('PUT', stats));
                toast('Player updated');
                await refreshDevelopment();
                if (typeof window.loadPlayers === 'function') window.loadPlayers();
                openPlayer(p.player_id, 'overview');
            } catch (err) { toast(err.message, 'err'); btn.disabled = false; }
        });
        $('#ed_photo_btn').addEventListener('click', async () => {
            const f = $('#ed_photo').files[0];
            if (!f) return toast('Choose a photo first', 'err');
            if (f.size > 8 * 1024 * 1024) return toast('That photo is larger than 8 MB', 'err');
            const fd = new FormData(); fd.append('file', f);
            try {
                await api(`/api/pdi/players/${p.player_id}/photo`, { method: 'POST', body: fd });
                toast('Photo updated');
                await refreshDevelopment();
                openPlayer(p.player_id, 'edit');
            } catch (err) { toast(err.message, 'err'); }
        });
    }

    // ---------------------------------------------------- home spotlight
    async function loadMatches() {
        try {
            const m = await api('/api/team-matches');
            state.matches = Array.isArray(m) ? m : [];
        } catch (e) { state.matches = []; }
    }
    function nextMatch() {
        const now = new Date();
        return state.matches
            .filter(m => !Number(m.is_completed) && m.match_date)
            .map(m => ({ ...m, at: new Date(`${m.match_date}T${(m.match_time || '10:00').slice(0, 5)}:00`) }))
            .filter(m => m.at >= new Date(now.getTime() - 3 * 3600e3))
            .sort((a, b) => a.at - b.at)[0];
    }
    let cdTimer;
    function renderSpotlight() {
        const host = $('#homeSpotlight');
        if (!host || !state.insights) return;
        const ins = state.insights;
        const star = (ins.top_rated || [])[0];
        const m = nextMatch();
        const alerts = ins.attendance_alerts || [];
        const improved = (ins.most_improved || []).slice(0, 3);
        host.innerHTML = `
            ${star ? `<div class="spot star" data-open="${star.player_id}" role="button" tabindex="0">
                ${star.photo_url ? `<img src="${esc(star.photo_url)}" alt="">` : ''}
                <div class="who"><div><h4>Top performer</h4><div class="name">${esc(star.first_name)}<br>${esc(star.last_name)}</div>
                <div class="meta">${esc(star.position || '')} · ${esc(star.team_group || '')}</div></div>
                <div><div class="ovr">${star.overall}<small>OVR</small></div><div class="meta">${plural(star.stats.goals, 'goal')} · ${plural(star.stats.assists, 'assist')} · ${star.stats.attendance_pct ?? '–'}% attendance</div></div></div></div>`
                : '<div class="spot"><h4>Top performer</h4><div class="empty" style="margin-top:12px;color:#6b778c">Record an assessment to see your top performer.</div></div>'}
            <div class="spot"><h4>Next fixture <a data-go="matches">All matches →</a></h4>
                ${m ? `<div class="match"><div class="teams">${esc(m.home_team)} <span>vs</span> ${esc(m.away_team)}</div>
                    <div class="when"><i class="fas fa-location-dot"></i> ${esc(m.venue || 'Venue TBC')} · ${esc(m.team_group || '')}</div></div>
                    <div class="countdown" id="countdown" data-at="${m.at.toISOString()}"></div>`
                    : '<div class="empty" style="margin-top:12px;color:#6b778c">No upcoming fixtures. Add one under Matches.</div>'}</div>
            <div class="spot"><h4>Development pulse <a data-go="development">Open →</a></h4>
                <ul class="mini-list">${improved.map(p => miniItem(p, trendChip(p.season_change))).join('')}</ul>
                ${alerts.length ? `<div style="margin-top:10px;font-size:13px;color:#c98a12;cursor:pointer" data-go="development"><i class="fas fa-bell"></i> ${alerts.length} player${alerts.length === 1 ? '' : 's'} below 75% attendance</div>` : ''}</div>`;
        clearInterval(cdTimer);
        const cd = $('#countdown');
        if (cd) {
            const tick = () => {
                const ms = Math.max(0, new Date(cd.dataset.at) - new Date());
                const d = Math.floor(ms / 864e5), h = Math.floor(ms / 36e5) % 24, mi = Math.floor(ms / 6e4) % 60, s = Math.floor(ms / 1e3) % 60;
                cd.innerHTML = [[d, 'Days'], [h, 'Hrs'], [mi, 'Min'], [s, 'Sec']].map(([v, l]) => `<div><b>${String(v).padStart(2, '0')}</b><small>${l}</small></div>`).join('');
            };
            tick(); cdTimer = setInterval(tick, 1000);
        }
    }
    function bindHome() {
        const host = $('#homeSpotlight');
        if (!host || host.dataset.bound) return;
        host.dataset.bound = '1';
        host.addEventListener('click', e => {
            const go = e.target.closest('[data-go]');
            if (go) return window.showPanel(go.dataset.go);
            const o = e.target.closest('[data-open]');
            if (o) openPlayer(+o.dataset.open);
        });
        host.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('[data-open]')) openPlayer(+e.target.dataset.open); });
    }

    function startClock() {
        const c = $('#heroClock'), d = $('#heroDate'), g = $('#heroGreeting');
        const hour = new Date().getHours();
        if (g) g.textContent = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
        const tick = () => {
            const n = new Date();
            if (c) c.textContent = n.toLocaleTimeString('en-ZA', { hour: '2-digit', minute: '2-digit' });
            if (d) d.textContent = n.toLocaleDateString('en-ZA', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
        };
        tick(); setInterval(tick, 15000);
        const name = ($('#userName')?.textContent || '').trim();
        const hn = $('#heroName'); if (hn && name) hn.textContent = name;
    }

    // -------------------------------------------------- command palette
    let cmdItems = [], cmdSel = 0;
    function buildCmdItems(q) {
        q = q.trim().toLowerCase();
        const panels = $$('.sidebar-menu a[data-panel]').filter(a => a.closest('li')?.style.display !== 'none').map(a => ({
            type: 'Go to', label: a.textContent.replace(/\s+\d+\s*$/, '').replace(/NEW$/, '').trim(), icon: a.querySelector('i')?.className || 'fas fa-arrow-right', run: () => window.showPanel(a.dataset.panel),
        }));
        const actions = [
            { type: 'Action', label: 'Compare two players', icon: 'fas fa-code-compare', run: () => { window.showPanel('development'); if (!state.compare) toggleCompare(); } },
            { type: 'Action', label: 'Add a match', icon: 'fas fa-calendar-plus', run: () => typeof window.openMatchModal === 'function' && window.openMatchModal() },
            { type: 'Action', label: 'Add a player', icon: 'fas fa-user-plus', run: () => typeof window.openPlayerModal === 'function' && window.openPlayerModal() },
        ];
        const players = state.squad.map(p => ({
            type: 'Player', label: fullName(p), sub: `${posAbbr(p.position)} · ${p.team_group || ''} · ${p.overall ?? '–'}`, img: p.photo_url,
            run: () => { window.showPanel('development'); openPlayer(p.player_id); }, hay: `${fullName(p)} ${p.position} ${p.team_group} ${p.jersey_number}`,
        }));
        const match = it => !q || (it.hay || it.label).toLowerCase().includes(q);
        return [...panels.filter(match), ...actions.filter(match), ...players.filter(match).slice(0, q ? 12 : 6)];
    }
    function renderCmd() {
        const q = $('#cmdkInput').value;
        cmdItems = buildCmdItems(q);
        cmdSel = Math.min(cmdSel, Math.max(0, cmdItems.length - 1));
        let html = '', last = '';
        cmdItems.forEach((it, i) => {
            if (it.type !== last) { html += `<li class="grp">${it.type === 'Go to' ? 'Pages' : it.type === 'Action' ? 'Actions' : 'Players'}</li>`; last = it.type; }
            html += `<li class="it ${i === cmdSel ? 'sel' : ''}" data-i="${i}">${it.img ? `<img src="${esc(it.img)}" alt="">` : `<span class="ic"><i class="${esc(it.icon || 'fas fa-user')}"></i></span>`}${esc(it.label)}${it.sub ? `<small>${esc(it.sub)}</small>` : ''}</li>`;
        });
        $('#cmdkList').innerHTML = html || '<li class="grp">No results</li>';
        $('#cmdkList .sel')?.scrollIntoView({ block: 'nearest' });
    }
    function openCmd() { const bg = $('#cmdk'); if (!bg) return; bg.classList.add('open'); $('#cmdkInput').value = ''; cmdSel = 0; renderCmd(); setTimeout(() => $('#cmdkInput').focus(), 20); }
    function closeCmd() { $('#cmdk')?.classList.remove('open'); }
    function runCmd(i) { const it = cmdItems[i]; closeCmd(); if (it) it.run(); }
    function bindCmd() {
        const bg = $('#cmdk'); if (!bg || bg.dataset.bound) return; bg.dataset.bound = '1';
        $('#cmdkInput').addEventListener('input', () => { cmdSel = 0; renderCmd(); });
        $('#cmdkInput').addEventListener('keydown', e => {
            if (e.key === 'ArrowDown') { e.preventDefault(); cmdSel = (cmdSel + 1) % Math.max(1, cmdItems.length); renderCmd(); }
            if (e.key === 'ArrowUp') { e.preventDefault(); cmdSel = (cmdSel - 1 + cmdItems.length) % Math.max(1, cmdItems.length); renderCmd(); }
            if (e.key === 'Enter') { e.preventDefault(); runCmd(cmdSel); }
        });
        $('#cmdkList').addEventListener('click', e => { const li = e.target.closest('.it'); if (li) runCmd(+li.dataset.i); });
        bg.addEventListener('click', e => { if (e.target === bg) closeCmd(); });
        $$('[data-open-cmdk]').forEach(b => b.addEventListener('click', openCmd));
    }

    // ------------------------------------------------------ welcome intro
    function maybeIntro() {
        let just = false;
        try { just = sessionStorage.getItem('dfc_just_logged_in') === '1'; sessionStorage.removeItem('dfc_just_logged_in'); } catch (e) {}
        if (!just || reduceMotion) return;
        const hour = new Date().getHours();
        const greet = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
        const name = ($('#userName')?.textContent || 'Coach').trim();
        const el = document.createElement('div');
        el.className = 'intro';
        el.setAttribute('role', 'dialog'); el.setAttribute('aria-label', 'Welcome');
        el.innerHTML = `<div class="wrap"><div class="crest"><i class="fas fa-crown"></i></div>
            <h1>${greet},<br><span>${esc(name)}</span></h1><p>Dynasty FC · Matchday command centre</p>
            <div class="facts" id="introFacts"></div></div><div class="skip">Click anywhere to continue</div>`;
        document.body.appendChild(el);
        const done = () => { el.classList.add('done'); setTimeout(() => el.remove(), 800); };
        el.addEventListener('click', done);
        document.addEventListener('keydown', done, { once: true });
        const fill = () => {
            const ins = state.insights; if (!ins || !el.isConnected) return;
            const m = nextMatch();
            $('#introFacts', el).innerHTML = `
                <div class="fact"><b data-count="${ins.squad_size}">0</b><small>Players</small></div>
                <div class="fact"><b data-count="${ins.avg_overall || 0}">0</b><small>Squad OVR</small></div>
                <div class="fact"><b>${m ? m.at.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short' }) : '—'}</b><small>Next fixture</small></div>`;
            $$('#introFacts [data-count]', el).forEach(n => animateCount(n, n.dataset.count, { duration: 900 }));
        };
        state.onInsights = fill;
        fill();
        setTimeout(done, 3400);
    }

    // --------------------------------------------- players table tweaks
    window.PDI_rowPrefix = p => {
        const url = window.PDI_photoFor(p.player_id);
        return url ? `<img src="${esc(url)}" alt="">` : '<span class="ph"><i class="fas fa-user"></i></span>';
    };

    // ------------------------------------------------------------- global
    function bindGlobal() {
        if (document.body.dataset.pdiGlobal) return;
        document.body.dataset.pdiGlobal = '1';
        const sb = $('.sidebar'), scrim = $('.sidebar-scrim');
        const setNav = open => { sb?.classList.toggle('open', open); scrim?.classList.toggle('open', open); };
        $('#mobileMenuBtn')?.addEventListener('click', () => setNav(!sb.classList.contains('open')));
        scrim?.addEventListener('click', () => setNav(false));
        sb?.addEventListener('click', e => { if (e.target.closest('a[data-panel], [data-open-cmdk]')) setNav(false); });
        document.addEventListener('keydown', e => {
            const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '');
            if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#cmdk')?.classList.contains('open') ? closeCmd() : openCmd(); return; }
            if (e.key === '/' && !typing) { e.preventDefault(); openCmd(); return; }
            if (e.key === 'Escape') {
                if ($('#cmdk')?.classList.contains('open')) return closeCmd();
                if ($('#cmpModal')?.classList.contains('open')) return $('#cmpModal').classList.remove('open');
                if ($('#pdiDrawer')?.classList.contains('open')) return closeDrawer();
            }
            if ($('#pdiDrawer')?.classList.contains('open') && !typing) {
                if (e.key === 'ArrowRight') stepPlayer(1);
                if (e.key === 'ArrowLeft') stepPlayer(-1);
            }
        });
        $('#pdiDrawerBg')?.addEventListener('click', closeDrawer);
        $('#pdiDrawer')?.addEventListener('click', e => {
            if (e.target.closest('[data-close-drawer]')) return closeDrawer();
            const st = e.target.closest('[data-step]'); if (st) return stepPlayer(+st.dataset.step);
            const t = e.target.closest('.tabs button'); if (t) switchTab(t.dataset.tab);
        });
        $('#cmpModal')?.addEventListener('click', e => { if (e.target.id === 'cmpModal' || e.target.closest('[data-close-cmp]')) $('#cmpModal').classList.remove('open'); });
    }

    async function init() {
        if (state.started) return;
        state.started = true;
        bindGlobal(); bindCmd(); bindHome(); bindDevelopment(); startClock();
        maybeIntro();
        const grid = $('#pdiGrid');
        if (grid) grid.innerHTML = Array.from({ length: 8 }, () => '<div class="skeleton"></div>').join('');
        await Promise.all([loadMatches(), refreshDevelopment()]);
        renderSpotlight();
        if (state.onInsights) state.onInsights();
        if (typeof window.loadPlayers === 'function') window.loadPlayers();
    }

    window.PDI = { init, openPlayer, refresh: refreshDevelopment, toast, openCmd, onShow: () => { renderSquad(); } };
})();
