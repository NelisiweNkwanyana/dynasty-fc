/* =====================================================================
   Dynasty FC — v4 dashboard additions
   · password change + weak-password warning
   · User Accounts (admin)            · Follow-up Tasks
   · Club Analytics (SVG charts)      · edit fixtures / staff / inventory
   Every figure comes from the API at the moment the panel opens.
   ===================================================================== */
(() => {
    'use strict';
    const $ = (s, r = document) => r.querySelector(s);
    const U = () => window.Modules.ui;
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const has = c => window.Modules && window.Modules.hasCap(c);
    const isoDate = d => {
        if (!d) return '';
        const s = String(d);
        if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 10);
        const x = new Date(s);
        return isNaN(x) ? '' : x.toISOString().slice(0, 10);
    };
    const hhmm = t => { const m = String(t || '').match(/(\d{1,2}):(\d{2})/); return m ? m[1].padStart(2, '0') + ':' + m[2] : ''; };

    function formModal(id, title, fields, submitLabel, onSubmit, note = '') {
        const { modal, closeModal, toast } = U();
        const body = `<form id="${id}Form" class="fgrid" novalidate>${fields.map(f => {
            const req = f.required ? 'required' : '';
            const cls = f.full ? 'full' : '';
            let input;
            if (f.type === 'select') input = `<select name="${f.name}" ${req}>${f.options.map(o => { const [v, l] = Array.isArray(o) ? o : [o, o]; return `<option value="${esc(v)}" ${String(v) === String(f.value ?? '') ? 'selected' : ''}>${esc(l)}</option>`; }).join('')}</select>`;
            else if (f.type === 'textarea') input = `<textarea name="${f.name}" rows="3" ${req}>${esc(f.value)}</textarea>`;
            else input = `<input type="${f.type || 'text'}" name="${f.name}" value="${esc(f.value)}" ${req} ${f.attrs || ''}>`;
            return `<div class="${cls}"><label>${esc(f.label)}</label>${input}</div>`;
        }).join('')}${note ? `<div class="full" style="font-size:12.5px;color:#6b778c">${note}</div>` : ''}<div class="full msg-err" style="color:#c0392b;font-size:13px" aria-live="polite"></div></form>`;
        const m = modal(id, { title, body, small: true, foot: `<button class="btn-ghost" data-close type="button">Cancel</button><button class="btn-gold" form="${id}Form">${esc(submitLabel)}</button>` });
        const form = $(`#${id}Form`, m);
        form.querySelector('input,select,textarea')?.focus();
        form.addEventListener('submit', async e => {
            e.preventDefault();
            const err = form.querySelector('.msg-err');
            err.textContent = '';
            if (!form.checkValidity()) { const bad = form.querySelector(':invalid'); err.textContent = (bad?.closest('div')?.querySelector('label')?.textContent || 'A field') + ': ' + bad?.validationMessage; bad?.focus(); return; }
            const btn = m.querySelector('.btn-gold'); btn.disabled = true;
            try { const done = await onSubmit(Object.fromEntries(new FormData(form))); if (done !== false) closeModal(id); }
            catch (er) { err.textContent = er.message; }
            finally { btn.disabled = false; }
        });
        return m;
    }

    // ============================================================ ACCOUNT
    async function checkSecurity() {
        try {
            const s = await U().api('/api/me/security');
            const b = $('#weakPwBanner'); if (b) b.hidden = !s.weak_password;
        } catch (e) { /* not critical */ }
    }

    function changePassword() {
        const { api, J, toast } = U();
        formModal('pwModal', 'Change your password', [
            { name: 'current_password', label: 'Current password', type: 'password', required: true, full: true, attrs: 'autocomplete="current-password"' },
            { name: 'new_password', label: 'New password', type: 'password', required: true, full: true, attrs: 'minlength="8" autocomplete="new-password"' },
            { name: 'confirm', label: 'Type the new password again', type: 'password', required: true, full: true, attrs: 'autocomplete="new-password"' },
        ], 'Change password', async f => {
            if (f.new_password !== f.confirm) throw new Error('The two new passwords do not match');
            await api('/api/me/password', J('POST', { current_password: f.current_password, new_password: f.new_password }));
            toast('Password changed');
            checkSecurity();
        }, 'At least 8 characters with letters and a number. Common passwords like “admin123” are refused.');
    }

    // ============================================================ USERS
    let usersCache = null;
    async function loadUsers() {
        const host = $('#usersBody'); if (!host) return;
        const { api } = U();
        try {
            const d = usersCache = await api('/api/admin/users');
            host.innerHTML = `<div class="box"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th></th></tr></thead><tbody>
                ${d.users.map(u => `<tr data-uid="${u.user_id}"><td><b>${esc(u.username)}</b>${u.user_id === d.me ? ' <span class="pill type">you</span>' : ''}</td><td>${esc(u.email)}</td>
                <td><select data-role ${u.user_id === d.me ? 'disabled' : ''} aria-label="Role for ${esc(u.username)}">${d.roles.map(r => `<option ${r === u.role ? 'selected' : ''}>${esc(r)}</option>`).join('')}</select></td>
                <td><span class="pill ${u.is_active ? 'approved' : 'rejected'}">${u.is_active ? 'Active' : 'Disabled'}</span></td>
                <td style="white-space:nowrap">${u.user_id === d.me ? '' : `<button class="btn-xs ghost" data-act="toggle">${u.is_active ? 'Disable' : 'Enable'}</button> <button class="btn-xs nav" data-act="reset">Reset password</button>`}</td></tr>`).join('')}
                </tbody></table></div></div>
                <div class="box mt" style="font-size:13px;color:#6b778c"><b>What each role can do.</b> System Administrator – everything, including accounts and the audit trail. Club Administrator – everything except accounts and medical paperwork. Coach – squads, fixtures and results, development, tasks, analytics and medical paperwork; no finance, messages or admin. Player – public site and fan predictions only.</div>`;
        } catch (e) { host.innerHTML = `<div class="box">${esc(e.message)}</div>`; }
    }

    function bindUsers() {
        const host = $('#usersBody'); if (!host || host.dataset.bound) return; host.dataset.bound = '1';
        const { api, J, toast } = U();
        host.addEventListener('change', async e => {
            const sel = e.target.closest('[data-role]'); if (!sel) return;
            const uid = +sel.closest('tr').dataset.uid;
            try { await api(`/api/admin/users/${uid}`, J('PUT', { role: sel.value })); toast('Role updated'); }
            catch (er) { toast(er.message, 'err'); }
            loadUsers();
        });
        host.addEventListener('click', async e => {
            const b = e.target.closest('[data-act]'); if (!b) return;
            const uid = +b.closest('tr').dataset.uid;
            const u = usersCache.users.find(x => x.user_id === uid);
            try {
                if (b.dataset.act === 'toggle') {
                    await api(`/api/admin/users/${uid}`, J('PUT', { is_active: !u.is_active })); toast(u.is_active ? 'Account disabled' : 'Account enabled');
                } else {
                    if (!confirm(`Set a new temporary password for ${u.username}? Their old password stops working.`)) return;
                    const r = await api(`/api/admin/users/${uid}/reset-password`, J('POST'));
                    showTemp(u.username, u.email, r.temporary_password);
                }
            } catch (er) { toast(er.message, 'err'); }
            loadUsers();
        });
    }

    function showTemp(name, email, pw) {
        U().modal('tempPw', { title: 'Temporary password', small: true,
            body: `<p>Give this to <b>${esc(name)}</b> (${esc(email)}) privately. It is shown only once – they should change it straight after logging in (key icon, bottom-left).</p>
                <p style="font:600 20px/1.4 ui-monospace,monospace;background:#f4f6fa;padding:12px;border-radius:10px;text-align:center;user-select:all">${esc(pw)}</p>`,
            foot: '<button class="btn-gold" data-close>Done</button>' });
    }

    function addUser() {
        const { api, J } = U();
        const roles = usersCache?.roles || ['System Administrator', 'Club Administrator', 'Coach', 'Player'];
        formModal('userModal', 'New account', [
            { name: 'username', label: 'Full name', required: true, full: true },
            { name: 'email', label: 'Email (used to log in)', type: 'email', required: true, full: true },
            { name: 'role', label: 'Role', type: 'select', options: roles, value: 'Coach', full: true },
        ], 'Create account', async f => {
            const r = await api('/api/admin/users', J('POST', f));
            loadUsers();
            setTimeout(() => showTemp(f.username, f.email, r.temporary_password), 50);
        }, 'A random temporary password is generated for you to pass on.');
    }

    // ============================================================ TASKS
    let taskStatus = 'open';
    const CAP_LABEL = { documents: 'Everyone', development: 'Coaches', players: 'Squad staff', fixtures: 'Fixtures', finance: 'Finance', finance_approve: 'Finance approvers', documents_finance: 'Finance staff', documents_medical: 'Medical paperwork', staff: 'Administrators' };
    async function loadTasks() {
        const host = $('#taskBody'); if (!host) return;
        const { api, fmtD } = U();
        document.querySelectorAll('#taskTabs [data-s]').forEach(b => b.classList.toggle('on', b.dataset.s === taskStatus));
        try {
            const rows = await api('/api/tasks?status=' + taskStatus);
            if (!rows.length) { host.innerHTML = `<div class="empty-state"><i class="fas fa-circle-check"></i>${taskStatus === 'open' ? 'Nothing waiting. New tasks appear here automatically.' : 'No completed tasks yet.'}</div>`; return; }
            host.innerHTML = `<div class="box"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Task</th><th>For</th><th>Due</th><th>${taskStatus === 'open' ? 'Created' : 'Completed'}</th><th></th></tr></thead><tbody>
                ${rows.map(t => `<tr${t.overdue ? ' style="background:#fff4f2"' : ''}><td><b>${esc(t.title)}</b>${t.details ? `<div style="font-size:12.5px;color:#6b778c">${esc(t.details)}</div>` : ''}</td>
                <td><span class="pill type">${esc(CAP_LABEL[t.capability] || t.capability)}</span></td>
                <td style="white-space:nowrap">${t.due_date ? fmtD(t.due_date) : '–'}${t.overdue ? ' <span class="pill failed">overdue</span>' : ''}</td>
                <td style="font-size:12.5px;color:#6b778c">${taskStatus === 'open' ? esc(t.created_by_name || 'automation') : `${esc(t.completed_by_name || '')}${t.auto_completed ? ' (automatic)' : ''}`}</td>
                <td style="white-space:nowrap">${t.link && /^\/dashboard#[\w/]+$/.test(t.link) ? `<a class="btn-xs nav" href="${esc(t.link)}">Open</a> ` : ''}${taskStatus === 'open' ? `<button class="btn-xs ok" data-done="${t.task_id}">Done</button>` : ''}</td></tr>`).join('')}
                </tbody></table></div></div>`;
        } catch (e) { host.innerHTML = `<div class="box">${esc(e.message)}</div>`; }
    }
    function bindTasks() {
        const tabs = $('#taskTabs'), host = $('#taskBody');
        if (!tabs || tabs.dataset.bound) return; tabs.dataset.bound = '1';
        tabs.addEventListener('click', e => { const b = e.target.closest('[data-s]'); if (b) { taskStatus = b.dataset.s; loadTasks(); } });
        host.addEventListener('click', async e => {
            const b = e.target.closest('[data-done]'); if (!b) return;
            try { await U().api(`/api/tasks/${b.dataset.done}/done`, U().J('POST')); U().toast('Task completed'); loadTasks(); window.Modules.loadOverview(); }
            catch (er) { U().toast(er.message, 'err'); }
        });
    }
    function addTask() {
        const { api, J, toast } = U();
        const opts = Object.entries(CAP_LABEL).filter(([c]) => has(c));
        formModal('taskModal', 'Add a task', [
            { name: 'title', label: 'What needs doing?', required: true, full: true, attrs: 'maxlength="255"' },
            { name: 'details', label: 'Details (optional)', type: 'textarea', full: true },
            { name: 'due_date', label: 'Due date', type: 'date' },
            { name: 'capability', label: 'Who should see it', type: 'select', options: opts, value: 'documents' },
        ], 'Add task', async f => {
            await api('/api/tasks', J('POST', f)); toast('Task added'); taskStatus = 'open'; loadTasks(); window.Modules.loadOverview();
        });
    }

    // ============================================================ ANALYTICS
    const C = { a: '#2a64ad', b: '#b0821f', c: '#1f9d6b', win: '#1f9d6b', draw: '#9aa5b4', loss: '#d64545', inc: '#1f9d6b', out: '#d64545', ink: '#5b6576', grid: '#e7eaf0' };
    const SERIES = [C.a, C.b, C.c];
    let tip;
    function tooltip() {
        if (tip) return tip;
        tip = document.createElement('div');
        tip.className = 'v4-tip'; tip.setAttribute('role', 'status');
        document.body.appendChild(tip);
        return tip;
    }
    function bindTips(root) {
        root.addEventListener('mousemove', e => {
            const t = e.target.closest('[data-tip]'); const el = tooltip();
            if (!t) { el.style.opacity = 0; return; }
            el.innerHTML = t.dataset.tip; el.style.opacity = 1;
            const x = Math.min(e.clientX + 14, window.innerWidth - el.offsetWidth - 8);
            el.style.left = x + 'px'; el.style.top = (e.clientY - el.offsetHeight - 12) + 'px';
        });
        root.addEventListener('mouseleave', () => { if (tip) tip.style.opacity = 0; });
    }
    const nice = m => { if (m <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(m))); const f = m / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; };

    // bars: groups=[{label, values:[..]}], series=[{name,color}], stacked?
    function barChart({ groups, series, stacked = false, fmt = v => v, h = 200, int = true }) {
        const W = 640, pad = { l: 44, r: 8, t: 10, b: 26 };
        let max = nice(Math.max(1, ...groups.map(g => stacked ? g.values.reduce((a, b) => a + b, 0) : Math.max(...g.values))));
        if (int) max = Math.ceil(max / 4) * 4;
        const iw = W - pad.l - pad.r, ih = h - pad.t - pad.b, gw = iw / groups.length;
        const y = v => pad.t + ih - (v / max) * ih;
        let s = `<svg viewBox="0 0 ${W} ${h}" class="v4-chart" role="img">`;
        for (let i = 0; i <= 4; i++) { const v = max * i / 4; s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="${C.grid}"/><text x="${pad.l - 6}" y="${y(v) + 4}" text-anchor="end" class="ax">${esc(fmt(v))}</text>`; }
        groups.forEach((g, i) => {
            const x0 = pad.l + i * gw;
            const tipHtml = `<b>${esc(g.label)}</b>` + series.map((se, k) => `<div><i style="background:${se.color}"></i>${esc(se.name)}: <b>${esc(fmt(g.values[k]))}</b></div>`).join('');
            if (stacked) {
                const bw = Math.min(28, gw * 0.6); let acc = 0;
                g.values.forEach((v, k) => { if (!v) return; const top = y(acc + v), bot = y(acc); s += `<rect x="${x0 + (gw - bw) / 2}" y="${top + 1}" width="${bw}" height="${Math.max(0, bot - top - 2)}" rx="2" fill="${series[k].color}"/>`; acc += v; });
            } else {
                const n = series.length, bw = Math.min(16, (gw * 0.7) / n);
                g.values.forEach((v, k) => { const bx = x0 + (gw - bw * n - 2 * (n - 1)) / 2 + k * (bw + 2); s += `<rect x="${bx}" y="${y(v)}" width="${bw}" height="${Math.max(0, pad.t + ih - y(v))}" rx="3" fill="${series[k].color}"/>`; });
            }
            s += `<text x="${x0 + gw / 2}" y="${h - 8}" text-anchor="middle" class="ax">${esc(g.label)}</text>`;
            s += `<rect x="${x0}" y="${pad.t}" width="${gw}" height="${ih}" fill="transparent" data-tip="${esc(tipHtml)}"/>`;
        });
        return s + '</svg>';
    }

    // lines: series=[{name,color,points:[{x:label,y,n?}]}] on a shared ordered x list
    function lineChart({ xs, series, min = 0, max = null, fmt = v => v, h = 200 }) {
        const W = 640, pad = { l: 44, r: 28, t: 10, b: 26 };
        const vals = series.flatMap(s => s.points.map(p => p.y));
        const top = max ?? nice(Math.max(1, ...vals));
        const lo = min;
        const iw = W - pad.l - pad.r, ih = h - pad.t - pad.b;
        const X = i => pad.l + (xs.length === 1 ? iw / 2 : (i / (xs.length - 1)) * iw);
        const Y = v => pad.t + ih - ((v - lo) / (top - lo)) * ih;
        let s = `<svg viewBox="0 0 ${W} ${h}" class="v4-chart" role="img">`;
        for (let i = 0; i <= 4; i++) { const v = lo + (top - lo) * i / 4; s += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="${C.grid}"/><text x="${pad.l - 6}" y="${Y(v) + 4}" text-anchor="end" class="ax">${esc(fmt(v))}</text>`; }
        const step = Math.max(1, Math.ceil(xs.length / 8));
        xs.forEach((x, i) => { if (i % step === 0 || i === xs.length - 1) s += `<text x="${X(i)}" y="${h - 8}" text-anchor="middle" class="ax">${esc(x)}</text>`; });
        series.forEach(se => {
            const pts = se.points.map(p => [X(xs.indexOf(p.x)), Y(p.y)]);
            if (pts.length > 1) s += `<polyline fill="none" stroke="${se.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" points="${pts.map(p => p.join(',')).join(' ')}"/>`;
            pts.forEach(p => { s += `<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="${se.color}" stroke="#fff" stroke-width="2"/>`; });
        });
        xs.forEach((x, i) => {
            const rows = series.map(se => { const p = se.points.find(q => q.x === x); return p ? `<div><i style="background:${se.color}"></i>${esc(se.name)}: <b>${esc(fmt(p.y))}</b>${p.n ? ` <small>(${p.n})</small>` : ''}</div>` : ''; }).join('');
            const w = xs.length === 1 ? iw : iw / (xs.length - 1);
            s += `<rect x="${X(i) - w / 2}" y="${pad.t}" width="${w}" height="${ih}" fill="transparent" data-tip="${esc(`<b>${esc(x)}</b>${rows}`)}"/>`;
        });
        return s + '</svg>';
    }
    const legend = series => `<span class="legend2">${series.map(s => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</span>`;
    const table = (head, rows) => `<details class="v4-tbl"><summary>Show as table</summary><div class="tbl-wrap"><table class="tbl"><thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;
    const tile = (k, v, s, icon) => `<div class="box tile"><div class="k"><i class="fas fa-${icon}"></i>${esc(k)}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;

    async function loadAnalytics() {
        const host = $('#anaBody'); if (!host) return;
        const { api, R, pct, label } = U();
        try {
            const a = await api('/api/analytics');
            const out = [];
            // ---- results
            const res = a.results, squads = Object.keys(res.squads);
            const totals = Object.entries(res.totals);
            const all = { W: 0, D: 0, L: 0, gf: 0, ga: 0, played: 0 };
            totals.forEach(([, t]) => Object.keys(all).forEach(k => all[k] += t[k]));
            out.push(`<div class="mod-grid c4">
                ${tile('Matches played', all.played, 'completed fixtures on record', 'futbol')}
                ${tile('Win rate', all.played ? pct(all.W / all.played) : '–', `${all.W} W · ${all.D} D · ${all.L} L`, 'trophy')}
                ${tile('Goals', `${all.gf}<small style="font-size:16px;color:#6b778c"> : ${all.ga}</small>`, all.played ? `${(all.gf / all.played).toFixed(1)} scored per game` : 'for : against', 'bullseye')}
                ${a.documents ? tile('Documents handled', a.documents.total, a.documents.avg_hours_to_decision != null ? `${a.documents.avg_hours_to_decision} h average to a decision` : 'no decisions yet', 'file-circle-check') : ''}
            </div>`);
            const wdl = [{ name: 'Won', color: C.win }, { name: 'Drawn', color: C.draw }, { name: 'Lost', color: C.loss }];
            const groups = res.keys.map((k, i) => {
                const v = [0, 0, 0]; squads.forEach(sq => { const m = res.squads[sq][i]; v[0] += m.W; v[1] += m.D; v[2] += m.L; });
                return { label: res.months[i], values: v };
            });
            out.push(`<div class="mod-grid c2 mt">
                <div class="box"><h3><span><i class="fas fa-chart-column"></i>Results, last 12 months</span>${legend(wdl)}</h3>
                    ${all.played ? barChart({ groups, series: wdl, stacked: true }) : '<div class="empty-state">No completed matches yet.</div>'}
                    ${table(['Month', 'Won', 'Drawn', 'Lost'], groups.map(g => [g.label, ...g.values]))}</div>
                <div class="box"><h3><span><i class="fas fa-people-group"></i>By squad</span></h3>
                    ${totals.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Squad</th><th class="num">P</th><th class="num">W</th><th class="num">D</th><th class="num">L</th><th class="num">GF</th><th class="num">GA</th><th class="num">Win %</th></tr></thead><tbody>
                    ${totals.map(([sq, t]) => `<tr><td><b>${esc(sq)}</b></td><td class="num">${t.played}</td><td class="num">${t.W}</td><td class="num">${t.D}</td><td class="num">${t.L}</td><td class="num">${t.gf}</td><td class="num">${t.ga}</td><td class="num">${t.played ? pct(t.W / t.played) : '–'}</td></tr>`).join('')}
                    </tbody></table></div>` : '<div class="empty-state">No completed matches yet.</div>'}</div></div>`);
            // ---- development
            if (a.development && !a.development.error) {
                const d = a.development, sq = Object.keys(d.rating_trend);
                const xs = [...new Set(sq.flatMap(s => d.rating_trend[s].map(p => p.date)))].sort();
                const fmtX = x => new Date(x + 'T00:00:00').toLocaleDateString('en-ZA', { day: 'numeric', month: 'short' });
                const series = sq.map((s, i) => ({ name: s, color: SERIES[i % 3], points: d.rating_trend[s].map(p => ({ x: fmtX(p.date), y: p.avg, n: `${p.n} player${p.n === 1 ? '' : 's'}` })) }));
                const vals = series.flatMap(s => s.points.map(p => p.y));
                out.push(`<div class="mod-grid c2 mt">
                    <div class="box"><h3><span><i class="fas fa-ranking-star"></i>Average squad rating per assessment</span>${series.length > 1 ? legend(series) : ''}</h3>
                        ${xs.length ? lineChart({ xs: xs.map(fmtX), series, min: Math.max(0, Math.floor((Math.min(...vals) - 5) / 10) * 10), max: Math.min(100, Math.ceil((Math.max(...vals) + 5) / 10) * 10) }) : '<div class="empty-state">No assessments recorded yet.</div>'}
                        ${table(['Squad', 'Date', 'Average', 'Players rated'], sq.flatMap(s => d.rating_trend[s].map(p => [s, p.date, p.avg, p.n])))}</div>
                    <div class="box"><h3><span><i class="fas fa-user-check"></i>Training attendance</span></h3>
                        ${Object.keys(d.attendance).length ? Object.entries(d.attendance).map(([s, v]) => `<div class="budget"><div class="top"><span>${esc(s)}</span><b>${v}%</b></div><div class="track"><span style="width:${Math.min(100, v)}%"></span></div></div>`).join('') : '<div class="empty-state">No attendance recorded yet.</div>'}
                        <p style="font-size:12.5px;color:#6b778c;margin-top:12px">${d.rated_players} of ${d.players} active players have at least one assessment.</p></div></div>`);
            }
            // ---- documents + finance
            const row = [];
            if (a.documents) {
                const docS = [{ name: 'Uploaded', color: C.a }, { name: 'Approved', color: C.c }];
                const g = a.documents.weeks.map(w => ({ label: w.week, values: [w.uploaded, w.approved] }));
                const types = Object.entries(a.documents.by_type).sort((x, y) => y[1] - x[1]);
                row.push(`<div class="box"><h3><span><i class="fas fa-file-lines"></i>Documents per week</span>${legend(docS)}</h3>
                    ${barChart({ groups: g, series: docS, h: 180 })}
                    <p style="font-size:12.5px;color:#6b778c;margin:10px 0 0">${a.documents.auto_ready_share != null ? `${pct(a.documents.auto_ready_share)} were recognised confidently without help. ` : ''}${types.map(([t, n]) => `${esc(label(t))} ${n}`).join(' · ')}</p>
                    ${table(['Week of', 'Uploaded', 'Approved'], g.map(x => [x.label, ...x.values]))}</div>`);
            }
            if (a.finance) {
                const finS = [{ name: 'Income', color: C.inc }, { name: 'Expenses', color: C.out }];
                const g = a.finance.map(m => ({ label: m.month, values: [m.income, m.expenses] }));
                const any = g.some(x => x.values[0] || x.values[1]);
                const kfmt = v => v >= 1000 ? 'R' + Math.round(v / 1000) + 'k' : 'R' + Math.round(v);
                row.push(`<div class="box"><h3><span><i class="fas fa-coins"></i>Approved money in and out</span>${legend(finS)}</h3>
                    ${any ? barChart({ groups: g, series: finS, fmt: kfmt, h: 180, int: false }) : '<div class="empty-state">No approved transactions in the last 12 months.</div>'}
                    ${table(['Month', 'Income', 'Expenses'], g.map(x => [x.label, R(x.values[0]), R(x.values[1])]))}</div>`);
            }
            if (row.length) out.push(`<div class="mod-grid c2 mt">${row.join('')}</div>`);
            // ---- model + assistant
            const row2 = [];
            if (a.model) {
                const xs = a.model.running_accuracy.map((_, i) => String(i + 1));
                row2.push(`<div class="box"><h3><span><i class="fas fa-brain"></i>Prediction model on Dynasty matches</span></h3>
                    ${a.model.graded ? lineChart({ xs, series: [{ name: 'Running accuracy', color: C.a, points: a.model.running_accuracy.map((y, i) => ({ x: xs[i], y })) }], min: 0, max: 1, fmt: v => Math.round(v * 100) + '%', h: 180 })
                    + `<p style="font-size:12.5px;color:#6b778c;margin:8px 0 0">Share of graded club fixtures where the model's most likely outcome happened, after each result (${a.model.graded} graded so far).</p>` : '<div class="empty-state">No club fixtures graded yet – record a result and it appears here.</div>'}</div>`);
            }
            if (a.assistant) {
                row2.push(`<div class="box"><h3><span><i class="fas fa-comments"></i>Assistant usage</span></h3>
                    ${a.assistant.questions ? `<p style="font-size:28px;font-weight:700;margin:4px 0">${a.assistant.questions} <small style="font-size:14px;color:#6b778c;font-weight:400">questions · ${pct(a.assistant.answered_share)} answered from club records</small></p>
                    ${a.assistant.top_topics.map(([t, n]) => `<div class="budget"><div class="top"><span>${esc(label(t || 'unmatched'))}</span><b>${n}</b></div><div class="track"><span style="width:${Math.round(n / a.assistant.questions * 100)}%"></span></div></div>`).join('')}` : '<div class="empty-state">Nobody has asked the assistant anything yet.</div>'}</div>`);
            }
            if (row2.length) out.push(`<div class="mod-grid c2 mt">${row2.join('')}</div>`);
            host.innerHTML = out.join('');
            if (!host.dataset.tips) { bindTips(host); host.dataset.tips = '1'; }
        } catch (e) { host.innerHTML = `<div class="box">Analytics unavailable: ${esc(e.message)}</div>`; }
    }

    // ============================================================ EDITS
    const rows = k => (window.__rows && window.__rows[k]) || [];

    function editMatch(id) {
        const m = rows('matches').find(x => +x.match_id === id); if (!m) return;
        const { api, J, toast } = U();
        const fields = [
            { name: 'team_group', label: 'Squad', type: 'select', options: [...new Set(['Dynamights', 'Diamond Divas', m.team_group].filter(Boolean))], value: m.team_group },
            { name: 'venue', label: 'Venue', value: m.venue, required: true, attrs: 'maxlength="150"' },
            { name: 'home_team', label: 'Home team', value: m.home_team, required: true, attrs: 'maxlength="120"' },
            { name: 'away_team', label: 'Away team', value: m.away_team, required: true, attrs: 'maxlength="120"' },
            { name: 'match_date', label: 'Date', type: 'date', value: isoDate(m.match_date), required: true },
            { name: 'match_time', label: 'Kick-off', type: 'time', value: hhmm(m.match_time) },
        ];
        if (m.is_completed) fields.push(
            { name: 'home_score', label: `${m.home_team} score`, type: 'number', value: m.home_score, required: true, attrs: 'min="0" max="50" step="1"' },
            { name: 'away_score', label: `${m.away_team} score`, type: 'number', value: m.away_score, required: true, attrs: 'min="0" max="50" step="1"' });
        formModal('editMatchModal', 'Edit fixture', fields, 'Save changes', async f => {
            if (f.home_team.trim().toLowerCase() === f.away_team.trim().toLowerCase()) throw new Error('A team cannot play itself');
            if (m.is_completed) { f.home_score = +f.home_score; f.away_score = +f.away_score; }
            await api(`/api/team-matches/${id}`, J('PUT', f));
            toast(m.is_completed ? 'Fixture updated – the prediction was re-graded if the score changed' : 'Fixture updated – prediction refreshed');
            window.loadMatches?.(); window.Modules.loadOverview();
        }, m.is_completed ? 'Correcting a score re-grades the model and updates team form.' : 'Changing teams or date re-runs the match prediction.');
    }

    function editStaff(id) {
        const s = rows('staff').find(x => +x.staff_id === id); if (!s) return;
        const { api, J, toast } = U();
        formModal('editStaffModal', 'Edit staff member', [
            { name: 'first_name', label: 'First name', value: s.first_name, required: true, attrs: 'maxlength="80"' },
            { name: 'last_name', label: 'Last name', value: s.last_name, required: true, attrs: 'maxlength="80"' },
            { name: 'role', label: 'Role', value: s.role, required: true, attrs: 'maxlength="80"' },
            { name: 'employment_status', label: 'Status', type: 'select', options: [...new Set(['Full-time', 'Part-time', 'Volunteer', 'Contract', s.employment_status].filter(Boolean))], value: s.employment_status },
            { name: 'contact_email', label: 'Email', type: 'email', value: s.contact_email, attrs: 'maxlength="150"' },
            { name: 'contact_phone', label: 'Phone', type: 'tel', value: s.contact_phone, attrs: 'maxlength="30"' },
        ], 'Save changes', async f => {
            await api(`/api/staff/${id}`, J('PUT', f)); toast('Staff member updated'); window.loadStaff?.();
        });
    }

    function editItem(id) {
        const it = rows('inventory').find(x => +(x.item_id ?? x.inventory_id) === id); if (!it) return;
        const { api, J, toast } = U();
        formModal('editItemModal', 'Edit inventory item', [
            { name: 'item_name', label: 'Item', value: it.item_name, required: true, attrs: 'maxlength="120"' },
            { name: 'category', label: 'Category', value: it.category, required: true, attrs: 'maxlength="80"' },
            { name: 'quantity', label: 'Quantity', type: 'number', value: it.quantity, required: true, attrs: 'min="0" step="1"' },
            { name: 'item_condition', label: 'Condition', type: 'select', options: ['New', 'Good', 'Worn', 'Damaged'], value: it.item_condition },
            { name: 'storage_location', label: 'Storage location', value: it.storage_location, full: true, attrs: 'maxlength="120"' },
        ], 'Save changes', async f => {
            f.quantity = +f.quantity;
            await api(`/api/inventory/${id}`, J('PUT', f)); toast('Item updated'); window.loadInventory?.();
        });
    }

    async function deleteItem(id) {
        const it = rows('inventory').find(x => +(x.item_id ?? x.inventory_id) === id);
        if (!confirm(`Delete ${it ? it.item_name : 'this item'} from the inventory?`)) return;
        try { await U().api(`/api/inventory/${id}`, { method: 'DELETE' }); U().toast('Item deleted'); window.loadInventory?.(); }
        catch (er) { U().toast(er.message, 'err'); }
    }

    // ============================================================ wiring
    const loaders = { tasks: loadTasks, analytics: loadAnalytics, users: loadUsers };
    window.V4 = {
        init() {
            checkSecurity(); bindTasks(); bindUsers();
            // hide sidebar section labels whose items are all hidden for this role
            document.querySelectorAll('.sidebar-menu .menu-label').forEach(lbl => {
                let n = lbl.nextElementSibling, any = false;
                while (n && !n.classList.contains('menu-label')) { if (n.style.display !== 'none') any = true; n = n.nextElementSibling; }
                lbl.style.display = any ? '' : 'none';
            });
            const nm = document.getElementById('userName')?.textContent.trim();
            const av = document.querySelector('.user-info .avatar');
            if (av && nm) av.textContent = nm.charAt(0).toUpperCase();
            const active = document.querySelector('.content-panel.active');
            if (active) { const p = active.id.replace('panel-', ''); if (loaders[p]) loaders[p](); }
        },
        onShow(panel) { if (loaders[panel] && window.Modules?.ui) loaders[panel](); },
        changePassword, addUser, addTask, editMatch, editStaff, editItem, deleteItem,
    };
})();
