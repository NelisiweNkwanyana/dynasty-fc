/* =====================================================================
   Dynasty FC — dashboard panels for the integrated modules:
   Documents · Finance · Match Intelligence · Audit & Automation ·
   notifications · result recording · deep links (#documents/12 etc.)
   Every number shown here comes from the API; nothing is hard-coded.
   ===================================================================== */
(() => {
    'use strict';
    const $ = (s, r = document) => r.querySelector(s);
    const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const R = n => (n === null || n === undefined || n === '') ? '–' : 'R ' + Number(n).toLocaleString('en-ZA', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const pct = n => (n === null || n === undefined) ? '–' : Math.round(n * 100) + '%';
    const fmtD = d => d ? new Date(String(d).slice(0, 10) + 'T00:00:00').toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' }) : '–';
    const label = s => String(s || '').replace(/_/g, ' ');
    const toast = (m, t) => (window.PDI && PDI.toast ? PDI.toast(m, t) : console.log(m));
    let caps = new Set();

    async function api(url, opts = {}) {
        const res = await fetch(url, { credentials: 'same-origin', ...opts });
        let data = {};
        try { data = await res.json(); } catch (e) { /* no body */ }
        if (!res.ok || (data && data.error)) { const e = new Error((data && data.error) || `Request failed (${res.status})`); e.data = data; e.status = res.status; throw e; }
        return data;
    }
    const J = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });

    // ------------------------------------------------------------ modal
    function modal(id, { title, body, foot = '', small = false }) {
        let m = document.getElementById(id);
        if (!m) { m = document.createElement('div'); m.id = id; m.className = 'mmodal'; document.body.appendChild(m); }
        m.innerHTML = `<div class="sheet ${small ? 'sm' : ''}" role="dialog" aria-label="${esc(title)}"><div class="sh"><h2>${esc(title)}</h2><button type="button" data-close aria-label="Close"><i class="fas fa-xmark"></i></button></div>
            <div class="sb">${body}</div>${foot ? `<div class="sf">${foot}</div>` : ''}</div>`;
        m.classList.add('open');
        m.onclick = e => { if (e.target === m || e.target.closest('[data-close]')) closeModal(id); };
        return m;
    }
    function closeModal(id) { document.getElementById(id)?.classList.remove('open'); }
    document.addEventListener('keydown', e => { if (e.key === 'Escape') $$('.mmodal.open').forEach(m => m.classList.remove('open')); });

    function dropzone(el, onFiles) {
        const input = el.querySelector('input[type=file]');
        el.addEventListener('click', e => { if (e.target !== input) input.click(); });
        input.addEventListener('change', () => { if (input.files.length) onFiles(Array.from(input.files)); input.value = ''; });
        ['dragenter', 'dragover'].forEach(ev => el.addEventListener(ev, e => { e.preventDefault(); el.classList.add('over'); }));
        ['dragleave', 'drop'].forEach(ev => el.addEventListener(ev, e => { e.preventDefault(); el.classList.remove('over'); }));
        el.addEventListener('drop', e => { if (e.dataTransfer.files.length) onFiles(Array.from(e.dataTransfer.files)); });
    }

    // ===================================================== OVERVIEW pulse
    async function loadOverview() {
        const host = $('#opsPulse');
        if (!host) return;
        try {
            const o = await api('/api/overview');
            const tiles = [];
            if (o.finance) tiles.push(`<div class="box tile link" data-go="finance"><div class="k"><i class="fas fa-coins"></i>Net this month</div>
                <div class="v ${o.finance.month_income - o.finance.month_expenses >= 0 ? 'pos' : 'neg'}">${R(o.finance.month_income - o.finance.month_expenses)}</div>
                <div class="s">${R(o.finance.month_income)} in · ${R(o.finance.month_expenses)} out${o.finance.pending ? ` · <b>${o.finance.pending} to approve</b>` : ''}</div></div>`);
            if (o.documents) tiles.push(`<div class="box tile link" data-go="documents"><div class="k"><i class="fas fa-file-circle-check"></i>Documents</div>
                <div class="v">${o.documents.waiting}</div><div class="s">waiting for review · ${o.documents.players_missing_docs} players missing paperwork</div></div>`);
            if (o.tasks) {
                tiles.push(`<div class="box tile link" data-go="tasks"><div class="k"><i class="fas fa-list-check"></i>Follow-up tasks</div>
                    <div class="v ${o.tasks.overdue ? 'neg' : ''}">${o.tasks.open}</div><div class="s">open${o.tasks.overdue ? ` · <b>${o.tasks.overdue} overdue</b>` : ' · none overdue'}</div></div>`);
                const tb = $('#taskBadge'); if (tb) { tb.textContent = o.tasks.open; tb.style.display = o.tasks.open ? '' : 'none'; }
            }
            if (o.predictions) {
                const n = o.predictions.next;
                tiles.push(`<div class="box tile link" data-go="ml"><div class="k"><i class="fas fa-brain"></i>Next match model</div>
                    <div class="v">${n && n.dynasty ? pct(n.dynasty.win) : '–'}</div>
                    <div class="s">${n ? `Dynasty win chance vs ${esc(/dynasty/i.test(n.home_team) ? n.away_team : n.home_team)}${n.data_quality !== 'good' ? ` (${esc(n.data_quality)} data)` : ''}` : 'No upcoming fixtures'}</div></div>`);
                tiles.push(`<div class="box tile link" data-go="ml"><div class="k"><i class="fas fa-bullseye"></i>Model accuracy</div>
                    <div class="v">${pct(o.predictions.test_accuracy)}</div><div class="s">on unseen matches · club record ${o.predictions.track_record.evaluated ? `${o.predictions.track_record.correct}/${o.predictions.track_record.evaluated}` : 'none yet'}</div></div>`);
            }
            tiles.push(`<div class="box tile link" data-go="matches"><div class="k"><i class="fas fa-futbol"></i>Recent form</div>
                <div class="v"><span class="formchips">${(o.matches.form || []).map(r => `<span class="${r}">${r}</span>`).join('') || '<span class="none">No results</span>'}</span></div>
                <div class="s">${o.matches.played} played · ${o.matches.upcoming} upcoming · ${o.players.injured} injured</div></div>`);
            host.innerHTML = tiles.join('');
            host.onclick = e => { const t = e.target.closest('[data-go]'); if (t) window.showPanel(t.dataset.go); };
            setBell(o.notifications);
        } catch (e) { host.innerHTML = `<div class="box">Live overview unavailable: ${esc(e.message)}</div>`; }
    }

    // ===================================================== NOTIFICATIONS
    function setBell(n) { const c = $('#bellCount'); if (c) { c.textContent = n; c.classList.toggle('zero', !n); } }
    async function openNotifications() {
        const p = $('#notifPanel');
        if (p.classList.contains('open')) { p.classList.remove('open'); return; }
        p.classList.add('open');
        p.innerHTML = '<div class="nh"><b>Notifications</b></div><div class="empty-state">Loading…</div>';
        try {
            const list = await api('/api/notifications');
            setBell(list.length);
            const icon = k => ({ finance: 'fa-coins', document: 'fa-file-lines', player: 'fa-user', match: 'fa-futbol', ml: 'fa-brain', error: 'fa-triangle-exclamation' }[k] || 'fa-bell');
            p.innerHTML = `<div class="nh"><b>Notifications</b>${list.length ? '<button class="btn-xs ghost" data-all>Clear all</button>' : ''}</div>` +
                (list.length ? list.map(n => `<div class="notif-item" data-link="${esc(n.link || '')}"><span class="ic"><i class="fas ${icon(n.kind)}"></i></span>
                    <span class="tx"><b>${esc(n.title)}</b><small>${esc(n.body || '')}</small><small>${esc(n.created_at)}</small></span>
                    <button class="x" data-dismiss="${n.notification_id}" aria-label="Dismiss"><i class="fas fa-xmark"></i></button></div>`).join('')
                    : '<div class="empty-state"><i class="fas fa-circle-check"></i>You\'re all caught up.</div>');
        } catch (e) { p.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
    }
    function bindNotifications() {
        $('#bellBtn')?.addEventListener('click', openNotifications);
        $('#notifPanel')?.addEventListener('click', async e => {
            const d = e.target.closest('[data-dismiss]');
            if (d) { e.stopPropagation(); await api(`/api/notifications/${d.dataset.dismiss}/dismiss`, { method: 'POST' }); d.closest('.notif-item').remove(); const c = $('#bellCount'); setBell(Math.max(0, (+c.textContent || 1) - 1)); return; }
            if (e.target.closest('[data-all]')) { await api('/api/notifications/dismiss-all', { method: 'POST' }); setBell(0); $('#notifPanel').classList.remove('open'); return; }
            const it = e.target.closest('[data-link]');
            if (it && it.dataset.link) { $('#notifPanel').classList.remove('open'); const h = it.dataset.link.split('#')[1]; if (h) { location.hash = h; route(); } }
        });
        document.addEventListener('click', e => { if (!e.target.closest('#notifPanel, #bellBtn')) $('#notifPanel')?.classList.remove('open'); });
    }

    // ===================================================== DOCUMENTS
    const FIELD_DEFS = {
        invoice: [['supplier', 'Supplier'], ['document_number', 'Invoice number'], ['document_date', 'Invoice date', 'date'], ['due_date', 'Due date', 'date'],
            ['subtotal', 'Subtotal (R)', 'number'], ['vat', 'VAT (R)', 'number'], ['total', 'Total (R)', 'number'], ['category', 'Category', 'category'], ['description', 'Description', 'text', 'full']],
        receipt: [['supplier', 'Supplier'], ['document_number', 'Receipt number'], ['document_date', 'Date', 'date'], ['total', 'Total (R)', 'number'],
            ['category', 'Category', 'category'], ['description', 'Description', 'text', 'full']],
        bank_statement: [['account_last4', 'Account (last 4 digits)'], ['opening_balance', 'Opening balance', 'number'], ['closing_balance', 'Closing balance', 'number']],
        player_registration: [['first_name', 'First name'], ['last_name', 'Last name'], ['date_of_birth', 'Date of birth', 'date'], ['mysafa_id', 'MYSAFA ID'],
            ['team_group', 'Team', ['', 'Dynamights', 'Diamond Divas']], ['age_group', 'Age group', ['', 'U10', 'U15']], ['position', 'Position'],
            ['guardian_name', 'Parent / guardian'], ['guardian_email', 'Guardian email'], ['guardian_phone', 'Guardian phone'], ['id_number_last4', 'ID number (last 4)']],
        medical_certificate: [['player_name', 'Player name'], ['practitioner', 'Doctor / practitioner'], ['issue_date', 'Issue date', 'date'],
            ['valid_until', 'Valid until', 'date'], ['fit_to_play', 'Fit to play?', ['', 'yes', 'no']]],
        consent_form: [['player_name', 'Player name'], ['guardian_name', 'Guardian name'], ['signed_date', 'Signed date', 'date'],
            ['consent_given', 'Consent given?', ['', 'yes', 'no']], ['guardian_phone', 'Guardian phone']],
        contract: [['party', 'Other party'], ['start_date', 'Start date', 'date'], ['end_date', 'End date', 'date'], ['value', 'Value (R)', 'number'], ['category', 'Category']],
        other: [],
    };
    const ROUTE_HINT = {
        invoice: 'On approval: added to the finance ledger as an expense and posted to accounting.',
        receipt: 'On approval: added to the finance ledger as an expense and posted to accounting.',
        bank_statement: 'On approval: every line goes into a finance import for checking and approval.',
        player_registration: 'On approval: creates the player (or fills in missing details on an existing one).',
        medical_certificate: 'On approval: linked to the player and their fit/injured status is updated.',
        consent_form: 'On approval: linked to the player\'s file.',
        contract: 'On approval: stored with a renewal reminder before the end date.',
        other: 'Choose a document type so the data can be routed.',
    };
    const docState = { filter: 'waiting', docs: [], types: {}, ocr: true, finCats: [] };

    async function loadDocs() {
        const host = $('#docList');
        if (!host) return;
        try {
            const d = await api('/api/docs');
            docState.docs = d.documents; docState.types = d.types; docState.ocr = d.ocr_available;
            renderDocs();
            $('#ocrNote').innerHTML = d.ocr_available ? '<span class="pill good"><i class="fas fa-eye"></i> OCR on</span>'
                : '<span class="pill needs_manual_entry" title="Install Tesseract on the server to read scans and photos"><i class="fas fa-eye-slash"></i> OCR not installed – scans need manual entry</span>';
        } catch (e) { host.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
    }
    function renderDocs() {
        const groups = { waiting: ['ready', 'needs_review', 'needs_manual_entry'], approved: ['approved'], rejected: ['rejected'], all: null };
        const counts = Object.fromEntries(Object.entries(groups).map(([k, v]) => [k, docState.docs.filter(d => !v || v.includes(d.status)).length]));
        $('#docTabs').innerHTML = [['waiting', 'Waiting for review'], ['approved', 'Approved'], ['rejected', 'Rejected'], ['all', 'All']]
            .map(([k, l]) => `<button class="${docState.filter === k ? 'on' : ''}" data-f="${k}">${l}<span class="n">${counts[k]}</span></button>`).join('')
            + `<button class="${docState.filter === 'outstanding' ? 'on' : ''}" data-f="outstanding">Outstanding player documents</button>`;
        if (docState.filter === 'outstanding') return renderOutstanding();
        const list = docState.docs.filter(d => !groups[docState.filter] || groups[docState.filter].includes(d.status));
        $('#docList').innerHTML = list.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>#</th><th>Document</th><th>Type</th><th>Confidence</th><th>Key data</th><th>Status</th><th>Uploaded</th></tr></thead><tbody>
            ${list.map(d => {
                const f = d.fields || {};
                const key = f.total !== undefined && f.total !== null ? R(f.total) + (f.supplier ? ' · ' + esc(f.supplier) : '')
                    : f.first_name ? esc(`${f.first_name} ${f.last_name || ''}`) : f.player_name ? esc(f.player_name) : f.transactions ? `${f.transactions.length} lines` : f.party ? esc(f.party) : '–';
                const errs = d.issues.filter(i => i.level === 'error').length, warns = d.issues.filter(i => i.level === 'warning').length;
                return `<tr class="click" data-doc="${d.document_id}"><td>${d.document_id}</td><td><b>${esc(d.title)}</b><div style="font-size:12px;color:#6b778c">${esc(d.extraction_method)} · ${Math.round((d.size_bytes || 0) / 1024)} KB</div></td>
                <td><span class="pill type">${esc(d.type_label)}</span></td><td><span class="conf"><span style="width:${Math.round((d.type_confidence || 0) * 100)}%"></span></span> ${Math.round((d.type_confidence || 0) * 100)}%</td>
                <td>${key}</td><td><span class="pill ${d.status}">${esc(label(d.status))}</span>${errs ? ` <span class="pill invalid">${errs} error${errs > 1 ? 's' : ''}</span>` : ''}${warns ? ` <span class="pill flagged">${warns}</span>` : ''}
                ${d.status === 'approved' && d.review_note ? `<div style="font-size:12px;color:#12603f;margin-top:4px">${esc(d.review_note)}</div>` : ''}</td>
                <td style="white-space:nowrap">${esc((d.created_at || '').slice(0, 16))}<div style="font-size:12px;color:#6b778c">${esc(d.uploaded_by_name || '')}</div></td></tr>`;
            }).join('')}</tbody></table></div>`
            : `<div class="empty-state"><i class="fas fa-inbox"></i>No documents here yet.</div>`;
    }
    async function renderOutstanding() {
        $('#docList').innerHTML = '<div class="empty-state">Loading…</div>';
        try {
            const o = await api('/api/docs/outstanding');
            $('#docList').innerHTML = o.players.length ? `<p style="color:#6b778c;margin:0 0 10px">Required for every active player: ${o.required.map(esc).join(', ')}. Upload a document and approve it to tick it off automatically.</p>
                <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Player</th><th>Team</th><th>Missing</th></tr></thead><tbody>
                ${o.players.map(p => `<tr><td><b>${esc(p.first_name)} ${esc(p.last_name)}</b></td><td>${esc(p.team_group)} ${esc(p.age_group)}</td><td>${p.missing_labels.map(m => `<span class="pill needs_review">${esc(m)}</span>`).join(' ')}</td></tr>`).join('')}
                </tbody></table></div>` : '<div class="empty-state"><i class="fas fa-circle-check"></i>Every player has all required documents.</div>';
        } catch (e) { $('#docList').innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
    }
    async function uploadDocs(files) {
        const dz = $('#docDrop');
        dz.classList.add('busy');
        $('#docDrop b').textContent = `Reading ${files.length} file${files.length > 1 ? 's' : ''}… (OCR can take a few seconds per page)`;
        const fd = new FormData();
        files.forEach(f => fd.append('files', f));
        const t = $('#docForceType').value;
        if (t) fd.append('doc_type', t);
        try {
            const r = await api('/api/docs/upload', { method: 'POST', body: fd });
            toast(`${r.documents.length} document${r.documents.length !== 1 ? 's' : ''} processed${r.errors.length ? ` · ${r.errors.length} failed` : ''}`, r.errors.length ? 'err' : 'ok');
            r.errors.forEach(er => toast(er, 'err'));
            docState.filter = 'waiting';
            await loadDocs();
            if (r.documents.length === 1) openDoc(r.documents[0].document_id);
        } catch (e) { toast(e.message, 'err'); (e.data?.errors || []).forEach(er => toast(er, 'err')); }
        finally { dz.classList.remove('busy'); $('#docDrop b').textContent = 'Drop documents here or click to choose'; }
    }

    async function openDoc(id) {
        modal('docModal', { title: 'Loading document…', body: '<div class="empty-state">Loading…</div>' });
        let d;
        try { d = await api(`/api/docs/${id}`); } catch (e) { modal('docModal', { title: 'Document', body: `<div class="empty-state">${esc(e.message)}</div>` }); return; }
        if (!docState.finCats.length && caps.has('finance')) { try { docState.finCats = await api('/api/fin/categories'); } catch (e) { /* optional */ } }
        const ext = d.mime_ext;
        const fileUrl = `/api/docs/${d.document_id}/file`;
        const preview = ext === 'pdf' ? `<iframe src="${fileUrl}" title="Document preview"></iframe>`
            : ['png', 'jpg', 'jpeg', 'webp', 'bmp'].includes(ext) ? `<img src="${fileUrl}" alt="Document image">` : `<pre>${esc(d.extracted_text || '(no text)')}</pre>`;
        const locked = ['approved', 'rejected'].includes(d.status);
        const issuesBy = {};
        d.issues.forEach(i => { (issuesBy[i.field] = issuesBy[i.field] || []).push(i); });
        const fieldHTML = (FIELD_DEFS[d.doc_type] || []).map(([k, lab, type = 'text', full]) => {
            const v = d.fields[k] ?? '';
            const iss = issuesBy[k] || [];
            const cls = iss.some(i => i.level === 'error') ? 'bad' : iss.length ? 'warn' : '';
            let input;
            if (Array.isArray(type)) input = `<select data-k="${k}" ${locked ? 'disabled' : ''}>${type.map(o => `<option value="${esc(o)}" ${String(v) === o ? 'selected' : ''}>${esc(o || '—')}</option>`).join('')}</select>`;
            else if (type === 'category') input = `<input data-k="${k}" list="finCatList" value="${esc(v)}" ${locked ? 'disabled' : ''}>`;
            else input = `<input data-k="${k}" type="${type === 'number' ? 'number' : type === 'date' ? 'date' : 'text'}" ${type === 'number' ? 'step="0.01"' : ''} value="${esc(v)}" ${locked ? 'disabled' : ''}>`;
            return `<div class="${cls} ${full ? 'full' : ''}"><label>${esc(lab)}</label>${input}${iss.map(i => `<div class="msg">${esc(i.message)}</div>`).join('')}</div>`;
        }).join('');
        const other = d.issues.filter(i => !(FIELD_DEFS[d.doc_type] || []).some(([k]) => k === i.field));
        const txns = d.fields.transactions;
        const body = `<div class="doc-split">
            <div class="doc-preview"><div class="pt"><button class="on" data-pv="file">Original</button><button data-pv="text">Extracted text</button>
                <a class="btn-xs ghost" style="margin-left:auto" href="${fileUrl}?download=1"><i class="fas fa-download"></i> Download</a></div>
                <div id="pvFile" style="flex:1;display:flex;min-height:0">${preview}</div><pre id="pvText" style="display:none">${esc(d.extracted_text || '(no text could be read)')}</pre></div>
            <div>
                <div class="fgrid">
                    <div><label>Document type</label><select id="docType" ${locked ? 'disabled' : ''}>${Object.entries(docState.types).map(([k, l]) => `<option value="${k}" ${k === d.doc_type ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>
                        <div class="msg" style="color:#6b778c">Detected with ${Math.round((d.type_confidence || 0) * 100)}% confidence · ${esc(d.extraction_method)}</div></div>
                    <div><label>Title</label><input id="docTitle" value="${esc(d.title)}" ${locked ? 'disabled' : ''}></div>
                    ${fieldHTML}
                </div>
                ${txns ? `<div class="mt"><b>${txns.length} statement lines read</b><div class="tbl-wrap" style="max-height:200px;overflow:auto;margin-top:6px"><table class="tbl"><tbody>${txns.slice(0, 50).map(t => `<tr><td>${esc(t.date)}</td><td>${esc(t.description)}</td><td class="num ${t.amount < 0 ? 'neg' : 'pos'}">${R(t.amount)}</td></tr>`).join('')}</tbody></table></div></div>` : ''}
                ${other.length ? `<ul class="issues mt">${other.map(i => `<li class="${i.level}"><i class="fas ${i.level === 'error' ? 'fa-circle-xmark' : 'fa-triangle-exclamation'}"></i>${esc(i.message)}</li>`).join('')}</ul>` : ''}
                <div class="route-note mt"><i class="fas fa-route"></i> ${d.status === 'approved' ? esc(d.review_note || 'Approved') + ` (by ${esc(d.reviewed_by_name || '')})` : d.status === 'rejected' ? 'Rejected: ' + esc(d.review_note || '') : esc(ROUTE_HINT[d.doc_type] || '')}</div>
            </div></div><datalist id="finCatList">${docState.finCats.map(c => `<option value="${esc(c)}">`).join('')}</datalist>`;
        const foot = locked ? `<span class="pill ${d.status}">${esc(label(d.status))}</span><button class="btn-ghost" data-close>Close</button>`
            : `<button class="btn-xs no" id="docReject"><i class="fas fa-ban"></i> Reject</button><button class="btn-xs ghost" id="docReprocess"><i class="fas fa-rotate"></i> Re-read file</button>
               <span style="flex:1"></span><button class="btn-ghost" id="docSave"><i class="fas fa-floppy-disk"></i> Save &amp; re-check</button>
               <button class="btn-gold" id="docApprove" ${d.can_approve ? '' : 'disabled title="Only finance approvers can approve financial documents"'}><i class="fas fa-check"></i> Approve &amp; send</button>`;
        const m = modal('docModal', { title: `#${d.document_id} · ${d.type_label}`, body, foot });
        $$('[data-pv]', m).forEach(b => b.addEventListener('click', () => {
            $$('[data-pv]', m).forEach(x => x.classList.toggle('on', x === b));
            $('#pvFile', m).style.display = b.dataset.pv === 'file' ? 'flex' : 'none';
            $('#pvText', m).style.display = b.dataset.pv === 'text' ? 'block' : 'none';
        }));
        if (locked) return;
        const collect = () => {
            const fields = {};
            $$('[data-k]', m).forEach(el => { let v = el.value.trim(); if (el.type === 'number') v = v === '' ? null : Number(v); fields[el.dataset.k] = v === '' ? null : v; });
            return fields;
        };
        const save = async () => {
            const newType = $('#docType', m).value;
            const payload = { doc_type: newType, title: $('#docTitle', m).value };
            if (newType === d.doc_type) payload.fields = collect();
            return api(`/api/docs/${d.document_id}`, J('PUT', payload));
        };
        $('#docType', m).addEventListener('change', async () => { try { await save(); openDoc(d.document_id); loadDocs(); } catch (e) { toast(e.message, 'err'); } });
        $('#docSave', m).addEventListener('click', async () => {
            try { const r = await save(); toast(r.issues.some(i => i.level === 'error') ? 'Saved – some problems remain' : 'Saved – all checks passed', r.issues.some(i => i.level === 'error') ? 'err' : 'ok'); openDoc(d.document_id); loadDocs(); } catch (e) { toast(e.message, 'err'); }
        });
        $('#docApprove', m).addEventListener('click', async () => {
            const btn = $('#docApprove', m); btn.disabled = true;
            try { await save(); const r = await api(`/api/docs/${d.document_id}/approve`, { method: 'POST' }); toast(r.message); closeModal('docModal'); loadDocs(); loadOverview(); refreshLinked(r.routed_to); }
            catch (e) { toast(e.message, 'err'); btn.disabled = false; openDoc(d.document_id); }
        });
        $('#docReject', m).addEventListener('click', async () => {
            const reason = window.prompt('Reason for rejecting this document?', '');
            if (reason === null) return;
            try { await api(`/api/docs/${d.document_id}/reject`, J('POST', { reason })); toast('Document rejected'); closeModal('docModal'); loadDocs(); } catch (e) { toast(e.message, 'err'); }
        });
        $('#docReprocess', m).addEventListener('click', async () => {
            try { await api(`/api/docs/${d.document_id}/reprocess`, { method: 'POST' }); toast('File read again'); openDoc(d.document_id); loadDocs(); } catch (e) { toast(e.message, 'err'); }
        });
    }
    function refreshLinked(target) {
        if (target === 'finance' || target === 'finance_import') loadFinance();
        if (target === 'players') { if (typeof window.loadPlayers === 'function') window.loadPlayers(); if (window.PDI) PDI.refresh(); }
    }

    // ===================================================== FINANCE
    const finState = { tab: 'ledger', txFilter: '', overview: null };
    async function loadFinance() {
        if (!$('#finPanel')) return;
        try {
            const o = await api('/api/fin/overview');
            finState.overview = o;
            const max = Math.max(1, ...o.by_month.map(m => Math.max(m.income, m.expenses)));
            $('#finTiles').innerHTML = `
                <div class="box tile"><div class="k"><i class="fas fa-scale-balanced"></i>Ledger balance</div><div class="v ${o.balance >= 0 ? 'pos' : 'neg'}">${R(o.balance)}</div><div class="s">${o.approved_count} approved transactions</div></div>
                <div class="box tile"><div class="k"><i class="fas fa-arrow-trend-up"></i>Income this month</div><div class="v pos">${R(o.month_income)}</div><div class="s">approved only</div></div>
                <div class="box tile"><div class="k"><i class="fas fa-arrow-trend-down"></i>Expenses this month</div><div class="v neg">${R(o.month_expenses)}</div><div class="s">approved only</div></div>
                <div class="box tile link" data-tab="imports"><div class="k"><i class="fas fa-hourglass-half"></i>Waiting for approval</div><div class="v">${o.pending_transactions + o.pending_import_rows}</div><div class="s">${o.pending_import_rows} imported lines · ${o.pending_transactions} entries</div></div>`;
            $('#finChart').innerHTML = `<div class="bars">${o.by_month.map(m => `<div class="m" title="${m.month}: in ${R(m.income)}, out ${R(m.expenses)}"><div class="pair">
                <span class="in" data-h="${Math.round(m.income / max * 140)}"></span><span class="out" data-h="${Math.round(m.expenses / max * 140)}"></span></div><small>${m.label}</small></div>`).join('')}</div>`;
            requestAnimationFrame(() => requestAnimationFrame(() => $$('#finChart [data-h]').forEach(s => s.style.height = s.dataset.h + 'px')));
            $('#finCats').innerHTML = o.expense_categories.length ? o.expense_categories.slice(0, 6).map(c => `<div class="budget"><div class="top"><span>${esc(c.category)}</span><b>${R(c.amount)}</b></div>
                <div class="track"><span style="width:${Math.round(c.amount / o.expense_categories[0].amount * 100)}%;background:var(--navy)"></span></div></div>`).join('') : '<div class="empty-state">No approved expenses this month.</div>';
            const a = o.accounting;
            $('#finAcct').innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap"><div><b>ERPNext</b> (open-source accounting)
                <div style="font-size:12.5px;color:#6b778c">${a.configured ? `Connected to ${esc(a.url)} · entries are created as ${a.submit ? 'submitted' : 'drafts for your accountant to submit'}` : 'Not connected yet – approved transactions are kept and will sync once the ERPNEXT_* settings are added.'}</div></div>
                <div class="mod-actions">${a.configured ? '<button class="btn-xs ghost" id="erpTest"><i class="fas fa-plug"></i> Test</button>' : ''}<button class="btn-xs nav" id="erpSync"><i class="fas fa-rotate"></i> Sync now</button></div></div>
                <div style="margin-top:10px;display:flex;gap:6px;flex-wrap:wrap">${Object.entries(o.sync).map(([k, v]) => `<span class="pill ${k}">${esc(label(k))}: ${v}</span>`).join('')}</div>`;
            $('#erpTest')?.addEventListener('click', async () => { try { const r = await api('/api/fin/accounting/test', { method: 'POST' }); toast(`Connected as ${r.user}`); } catch (e) { toast(e.message, 'err'); } });
            $('#erpSync').addEventListener('click', async () => { try { const r = await api('/api/fin/accounting/sync', { method: 'POST' }); toast(`Sync: ${Object.entries(r.results).map(([k, v]) => `${v} ${label(k)}`).join(', ') || 'nothing to sync'}`); loadFinance(); } catch (e) { toast(e.message, 'err'); } });
            renderBudgets(o.budgets);
            $$('#finTiles [data-tab]').forEach(t => t.addEventListener('click', () => finTab(t.dataset.tab)));
            finTab(finState.tab);
        } catch (e) { $('#finTiles').innerHTML = `<div class="box">${esc(e.message)}</div>`; }
    }
    function renderBudgets(budgets) {
        $('#finBudgets').innerHTML = (budgets.length ? budgets.map(b => {
            const p = b.pct || 0;
            return `<div class="budget"><div class="top"><span>${esc(b.category)}</span><span><b>${R(b.spent)}</b> / ${R(b.monthly_limit)} ${caps.has('finance_approve') ? `<button class="btn-xs ghost" data-delb="${b.budget_id}" title="Remove"><i class="fas fa-xmark"></i></button>` : ''}</span></div>
                <div class="track"><span class="${p > 100 ? 'over' : p > 80 ? 'warn' : ''}" style="width:${Math.min(100, p)}%"></span></div></div>`;
        }).join('') : '<div style="color:#6b778c;font-size:13px;margin-bottom:10px">No budgets yet.</div>')
            + (caps.has('finance_approve') ? `<form class="inline-form" id="budgetForm"><input name="category" list="finCatList2" placeholder="Category" required style="flex:1;min-width:120px"><input name="monthly_limit" type="number" min="1" step="1" placeholder="Monthly limit (R)" required style="width:150px"><button class="btn-xs nav">Set</button></form>` : '');
        $('#budgetForm')?.addEventListener('submit', async e => {
            e.preventDefault();
            const f = new FormData(e.target);
            try { await api('/api/fin/budgets', J('POST', { category: f.get('category'), monthly_limit: f.get('monthly_limit') })); toast('Budget saved'); loadFinance(); } catch (er) { toast(er.message, 'err'); }
        });
        $$('[data-delb]').forEach(b => b.addEventListener('click', async () => { await api(`/api/fin/budgets/${b.dataset.delb}`, { method: 'DELETE' }); loadFinance(); }));
    }
    function finTab(tab) {
        finState.tab = tab;
        $$('#finTabs button').forEach(b => b.classList.toggle('on', b.dataset.t === tab));
        ({ ledger: renderLedger, imports: renderImports, manual: renderManual })[tab]();
    }
    async function renderLedger() {
        const host = $('#finBody');
        host.innerHTML = '<div class="empty-state">Loading…</div>';
        try {
            const rows = await api('/api/fin/transactions' + (finState.txFilter ? `?status=${finState.txFilter}` : ''));
            host.innerHTML = `<div class="inline-form" style="margin-bottom:10px"><select id="txFilter"><option value="">All statuses</option>${['approved', 'pending', 'rejected'].map(s => `<option ${finState.txFilter === s ? 'selected' : ''}>${s}</option>`).join('')}</select>
                <a class="btn-xs ghost" href="/api/fin/export.csv"><i class="fas fa-file-csv"></i> Export approved ledger</a></div>` +
                (rows.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Date</th><th>Description</th><th>Category</th><th>Source</th><th class="num">Amount</th><th>Status</th><th>Accounting</th><th></th></tr></thead><tbody>
                ${rows.map(t => `<tr><td style="white-space:nowrap">${esc(t.txn_date)}</td><td>${esc(t.description)}${t.reference ? `<div style="font-size:12px;color:#6b778c">Ref ${esc(t.reference)}</div>` : ''}</td><td>${esc(t.category)}</td>
                <td><span class="pill type">${esc(t.source)}</span></td><td class="num ${t.amount < 0 ? 'neg' : 'pos'}"><b>${R(t.amount)}</b></td><td><span class="pill ${t.status}">${t.status}</span></td>
                <td>${t.sync_status ? `<span class="pill ${t.sync_status}" title="${esc(t.sync_error || t.sync_ref || '')}">${esc(label(t.sync_status))}</span>${t.sync_ref ? `<div style="font-size:11px;color:#6b778c">${esc(t.sync_ref)}</div>` : ''}${t.sync_error ? `<div style="font-size:11px;color:#d64545;max-width:220px">${esc(t.sync_error.slice(0, 90))}</div>` : ''}` : '–'}</td>
                <td style="white-space:nowrap">${t.status === 'pending' && caps.has('finance_approve') ? `<button class="btn-xs ok" data-tx="${t.txn_id}" data-a="approve">Approve</button> <button class="btn-xs no" data-tx="${t.txn_id}" data-a="reject">Reject</button>` : ''}
                ${t.sync_status === 'failed' && caps.has('finance_approve') ? `<button class="btn-xs ghost" data-tx="${t.txn_id}" data-a="sync">Retry</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`
                    : '<div class="empty-state"><i class="fas fa-receipt"></i>No transactions yet. Import a bank export or approve an invoice in Documents.</div>');
            $('#txFilter').addEventListener('change', e => { finState.txFilter = e.target.value; renderLedger(); });
            $$('[data-tx]', host).forEach(b => b.addEventListener('click', async () => {
                try { const r = await api(`/api/fin/transactions/${b.dataset.tx}/${b.dataset.a}`, { method: 'POST' }); toast(b.dataset.a === 'sync' ? `Sync: ${label(r.result)}` : `Transaction ${b.dataset.a}d`); loadFinance(); } catch (e) { toast(e.message, 'err'); }
            }));
        } catch (e) { host.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`; }
    }
    async function renderImports() {
        const host = $('#finBody');
        host.innerHTML = `<div class="drop" id="finDrop"><i class="fas fa-file-import"></i><b>Drop a bank export (CSV or Excel) here</b><small>Columns are detected automatically (Date, Description, Amount or Debit/Credit). Nothing is added to the ledger until it is approved.</small><input type="file" accept=".csv,.xlsx,.txt" hidden></div><div id="batchList" class="mt">Loading…</div>`;
        dropzone($('#finDrop'), async files => {
            const dz = $('#finDrop'); dz.classList.add('busy');
            for (const f of files) {
                const fd = new FormData(); fd.append('file', f);
                try { const r = await api('/api/fin/import', { method: 'POST', body: fd }); toast(`${f.name}: ${r.stats.clean} clean, ${r.stats.flagged} to check, ${r.stats.duplicates} duplicates`); openBatch(r.batch_id); }
                catch (e) { toast(`${f.name}: ${e.message}`, 'err'); }
            }
            dz.classList.remove('busy'); loadFinance();
        });
        try {
            const b = await api('/api/fin/batches');
            $('#batchList').innerHTML = b.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Import</th><th>Source</th><th>Rows</th><th>Clean</th><th>To check</th><th>Duplicates</th><th>Status</th><th>When</th></tr></thead><tbody>
                ${b.map(x => `<tr class="click" data-batch="${x.batch_id}"><td><b>${esc(x.filename)}</b></td><td>${esc(x.source)}</td><td>${x.total_rows}</td><td><span class="pill clean">${x.clean_rows}</span></td><td><span class="pill flagged">${x.flagged_rows + x.invalid_rows}</span></td><td><span class="pill duplicate">${x.duplicate_rows}</span></td><td><span class="pill ${x.status === 'open' ? 'pending' : 'approved'}">${x.status}</span></td><td>${esc((x.created_at || '').slice(0, 16))}</td></tr>`).join('')}
                </tbody></table></div>` : '<div class="empty-state">No imports yet.</div>';
            $$('[data-batch]').forEach(r => r.addEventListener('click', () => openBatch(+r.dataset.batch)));
        } catch (e) { $('#batchList').innerHTML = esc(e.message); }
    }
    function renderManual() {
        $('#finBody').innerHTML = `<form id="manualTx" class="fgrid" style="max-width:640px">
            <div><label>Type</label><select name="kind"><option value="expense">Expense (money out)</option><option value="income">Income (money in)</option></select></div>
            <div><label>Date</label><input type="date" name="date" required max="${new Date().toISOString().slice(0, 10)}"></div>
            <div class="full"><label>Description</label><input name="description" required maxlength="250"></div>
            <div><label>Amount (R)</label><input type="number" name="amount" min="0.01" step="0.01" required></div>
            <div><label>Category</label><input name="category" list="finCatList2" placeholder="Auto if empty"></div>
            <div><label>Counterparty</label><input name="counterparty"></div><div><label>Reference</label><input name="reference"></div>
            <div class="full"><p style="font-size:12.5px;color:#6b778c">Manual entries are for corrections and exceptions; they wait for a finance approver. Upload invoices in Documents or import bank exports instead where possible.</p>
            <button class="btn-gold">Submit for approval</button></div></form>`;
        $('#manualTx').addEventListener('submit', async e => {
            e.preventDefault();
            const body = Object.fromEntries(new FormData(e.target));
            try { await api('/api/fin/transactions', J('POST', body)); toast('Submitted for approval'); finTab('ledger'); loadFinance(); }
            catch (er) {
                if (er.status === 409 && window.confirm(er.message + '\n\nAdd it anyway?')) { await api('/api/fin/transactions', J('POST', { ...body, override: true })); toast('Submitted for approval'); finTab('ledger'); loadFinance(); }
                else if (er.status !== 409) toast(er.message, 'err');
            }
        });
    }
    async function openBatch(id) {
        let d;
        try { d = await api(`/api/fin/batches/${id}`); } catch (e) { toast(e.message, 'err'); return; }
        const pendingClean = d.rows.filter(r => r.decision === 'pending' && r.check_status === 'clean').length;
        const cats = await api('/api/fin/categories').catch(() => []);
        const body = `<p style="margin:0 0 12px;color:#6b778c">Columns used: ${Object.entries(d.batch.column_map || {}).map(([k, v]) => `<b>${esc(k)}</b> ← “${esc(v)}”`).join(' · ') || esc(d.batch.source)}</p>
            <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Line</th><th>Date</th><th>Description</th><th>Category</th><th class="num">Amount</th><th>Check</th><th>Decision</th></tr></thead><tbody>
            ${d.rows.map(r => {
                const editable = r.decision === 'pending';
                return `<tr data-row="${r.row_id}"><td>${r.line_no}</td>
                <td>${editable ? `<input type="date" data-f="date" value="${esc(r.txn_date || '')}">` : esc(r.txn_date)}</td>
                <td>${editable ? `<input data-f="description" value="${esc(r.description || '')}">` : esc(r.description)}<ul class="issues">${r.issues.map(i => `<li class="${i.level}">${esc(i.message)}</li>`).join('')}</ul></td>
                <td>${editable ? `<input data-f="category" list="finCatList2" value="${esc(r.category || '')}">` : esc(r.category)}</td>
                <td class="num">${editable ? `<input type="number" step="0.01" data-f="amount" value="${r.amount ?? ''}" style="width:110px;text-align:right">` : `<b class="${r.amount < 0 ? 'neg' : 'pos'}">${R(r.amount)}</b>`}</td>
                <td><span class="pill ${r.check_status}">${r.check_status}</span></td>
                <td style="white-space:nowrap">${editable ? (d.can_approve ? `<button class="btn-xs ok" data-dec="approve">Approve</button> <button class="btn-xs no" data-dec="reject">Reject</button>` : '<span class="pill pending">pending</span>') : `<span class="pill ${r.decision}">${r.decision}</span>${r.txn_id ? `<div style="font-size:11px;color:#6b778c">txn #${r.txn_id}</div>` : ''}`}</td></tr>`;
            }).join('')}</tbody></table></div><datalist id="finCatList2">${cats.map(c => `<option value="${esc(c)}">`).join('')}</datalist>`;
        const foot = `<span style="flex:1;font-size:13px;color:#6b778c">Edit a line and press Tab to re-check it. Flagged and duplicate lines need an individual decision.</span>
            ${d.can_approve && pendingClean ? `<button class="btn-gold" id="approveClean"><i class="fas fa-check-double"></i> Approve ${pendingClean} clean line${pendingClean > 1 ? 's' : ''}</button>` : ''}`;
        const m = modal('batchModal', { title: `Import · ${d.batch.filename}`, body, foot });
        $$('tr[data-row] input', m).forEach(inp => inp.addEventListener('change', async () => {
            const tr = inp.closest('tr');
            const payload = {};
            $$('input[data-f]', tr).forEach(i => payload[i.dataset.f] = i.value);
            try { await api(`/api/fin/rows/${tr.dataset.row}`, J('PUT', payload)); openBatch(id); } catch (e) { toast(e.message, 'err'); }
        }));
        $$('[data-dec]', m).forEach(b => b.addEventListener('click', async () => {
            const tr = b.closest('tr');
            const row = d.rows.find(r => r.row_id == tr.dataset.row);
            let body2 = { decision: b.dataset.dec };
            if (b.dataset.dec === 'approve' && row.check_status === 'duplicate') {
                if (!window.confirm('This line looks like a duplicate. Import it anyway?')) return;
                body2.override = true;
            }
            try { await api(`/api/fin/rows/${tr.dataset.row}/decision`, J('POST', body2)); openBatch(id); loadFinance(); } catch (e) { toast(e.message, 'err'); }
        }));
        $('#approveClean', m)?.addEventListener('click', async () => {
            try { const r = await api(`/api/fin/batches/${id}/approve-clean`, { method: 'POST' }); toast(`${r.approved} lines approved and sent to the ledger`); openBatch(id); loadFinance(); } catch (e) { toast(e.message, 'err'); }
        });
    }

    // ===================================================== MATCH INTELLIGENCE
    async function loadML() {
        const host = $('#mlBody');
        if (!host) return;
        host.innerHTML = '<div class="empty-state">Loading model…</div>';
        try {
            const [st, pr] = await Promise.all([api('/api/ml/status'), api('/api/ml/predictions')]);
            if (!st.trained) {
                host.innerHTML = `<div class="box"><div class="empty-state"><i class="fas fa-brain"></i>${st.training ? 'The model is training now (about 10 seconds)…' : 'No model trained yet.'}
                    <div class="mt"><button class="btn-gold" id="mlTrain">Train model now</button></div></div></div>`;
                $('#mlTrain')?.addEventListener('click', trainML);
                if (st.training) setTimeout(loadML, 4000);
                return;
            }
            const m = st.metrics, sel = m.comparison[m.selected];
            const tr = st.track_record;
            const fx = pr.fixtures || [];
            host.innerHTML = `
            <div class="mod-grid c4">
                <div class="box tile"><div class="k"><i class="fas fa-brain"></i>Model in use</div><div class="v" style="font-size:22px">${esc(st.algorithm)}</div><div class="s">v${esc(st.version)} · trained ${esc((st.trained_at || '').slice(0, 16))}</div></div>
                <div class="box tile"><div class="k"><i class="fas fa-bullseye"></i>Accuracy on unseen matches</div><div class="v">${pct(sel.accuracy)}</div><div class="s">baseline ${pct(m.comparison['Baseline (always the historical average)'].accuracy)} · ${m.test_period[0]} → ${m.test_period[1]}</div></div>
                <div class="box tile"><div class="k"><i class="fas fa-wave-square"></i>Log loss (lower is better)</div><div class="v">${sel.log_loss}</div><div class="s">baseline ${m.comparison['Baseline (always the historical average)'].log_loss} · Brier ${sel.brier}</div></div>
                <div class="box tile"><div class="k"><i class="fas fa-clipboard-check"></i>Dynasty FC track record</div><div class="v">${tr.evaluated ? `${tr.correct}/${tr.evaluated}` : '–'}</div><div class="s">${tr.evaluated ? `graded after each result · avg log loss ${tr.avg_log_loss}` : 'graded automatically when results are recorded'}</div></div>
            </div>
            <div class="mod-grid c2 mt" style="grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)">
                <div><h3 style="font-family:var(--display);font-size:22px;text-transform:uppercase;margin-bottom:10px">Upcoming fixtures</h3>
                    ${fx.length ? fx.map(fixtureCard).join('') : '<div class="box"><div class="empty-state"><i class="fas fa-calendar"></i>No upcoming fixtures. Add one under Matches and it will be predicted automatically.</div></div>'}</div>
                <div>
                    <div class="box"><h3><span><i class="fas fa-flask"></i>How the model was chosen</span></h3>
                        <table class="tbl cmp-tbl"><thead><tr><th>Model</th><th>Accuracy</th><th>Log loss</th></tr></thead><tbody>
                        ${Object.entries(m.comparison).map(([k, v]) => `<tr class="${k === m.selected ? 'sel' : ''}"><td>${esc(k)}${k === m.selected ? ' ✓' : ''}</td><td>${pct(v.accuracy)}</td><td>${v.log_loss}</td></tr>`).join('')}</tbody></table>
                        <p style="font-size:12.5px;color:#6b778c;margin-top:10px">Trained on ${m.n_used.toLocaleString()} matches (${m.train_period[0]} → ${m.train_period[1]}) and tested on later matches it never saw, so the scores are honest. Data: ${esc(m.data_source)}. Club results used: ${m.n_club}; imported local results: ${m.n_imported}.</p></div>
                    <div class="box mt"><h3><span><i class="fas fa-chart-line"></i>Calibration</span><small style="text-transform:none;letter-spacing:0">does 60% mean 60%?</small></h3>${calibration(sel.calibration)}</div>
                    <div class="box mt"><h3><span><i class="fas fa-upload"></i>Add local match history</span></h3>
                        <p style="font-size:12.5px;color:#6b778c;margin-bottom:8px">CSV with columns <b>date, home_team, away_team, home_goals, away_goals</b>. More local results = better estimates for Dynasty fixtures.</p>
                        <form class="inline-form" id="histForm"><select name="team_group"><option>Dynamights</option><option>Diamond Divas</option></select><select name="age_group"><option>U15</option><option>U10</option></select>
                        <input type="file" name="file" accept=".csv" required style="flex:1;min-width:160px"><button class="btn-xs nav">Import</button></form></div>
                    <div class="box mt" style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap"><div style="font-size:13px">Retrain with the latest results (about 10 seconds).</div><button class="btn-gold" id="mlTrain"><i class="fas fa-rotate"></i> Retrain</button></div>
                </div>
            </div>
            ${tr.recent.length ? `<div class="box mt"><h3><span><i class="fas fa-list-check"></i>Graded predictions</span></h3><div class="tbl-wrap"><table class="tbl"><thead><tr><th>Date</th><th>Match</th><th>Score</th><th>Predicted</th><th>Probability given to the actual result</th><th></th></tr></thead><tbody>
                ${tr.recent.map(r => { const pa = { H: r.p_home, D: r.p_draw, A: r.p_away }[r.actual_outcome]; return `<tr><td>${esc(r.match_date)}</td><td>${esc(r.home_team)} vs ${esc(r.away_team)}</td><td>${r.home_score}–${r.away_score}</td><td>${({ H: 'Home win', D: 'Draw', A: 'Away win' })[r.predicted_outcome]}</td><td>${pct(pa)}</td><td><span class="pill ${r.is_correct ? 'approved' : 'rejected'}">${r.is_correct ? 'correct' : 'missed'}</span></td></tr>`; }).join('')}
            </tbody></table></div></div>` : ''}`;
            $('#mlTrain')?.addEventListener('click', trainML);
            $('#histForm')?.addEventListener('submit', async e => {
                e.preventDefault();
                try { const r = await api('/api/ml/history/import', { method: 'POST', body: new FormData(e.target) }); toast(`${r.added} matches imported${r.problems.length ? `, ${r.problems.length} skipped` : ''}`); loadML(); } catch (er) { toast(er.message, 'err'); }
            });
            requestAnimationFrame(() => requestAnimationFrame(() => $$('#mlBody .probbar span[data-g]').forEach(s => s.style.flexGrow = s.dataset.g)));
        } catch (e) { host.innerHTML = `<div class="box">${esc(e.message)}</div>`; }
    }
    function fixtureCard(f) {
        const p = f.probabilities;
        const q = f.data_quality;
        const club = t => /dynasty/i.test(t) ? 'club' : '';
        const team = (t, info, side) => `<div><b style="font-size:13px">${esc(t)}</b><div class="formchips">${info.form.length ? info.form.map(r => `<span class="${r}">${r}</span>`).join('') : '<span class="none">no results yet</span>'}</div>
            <div class="statline">${info.played} played${info.gf_avg !== null ? ` · scores ${info.gf_avg}/game · concedes ${info.ga_avg}/game` : ''} · rating ${info.rating}</div></div>`;
        const feats = f.features ? Object.entries(f.features).map(([k, v]) => `<tr><td>${esc(({ elo_diff: 'Rating difference ÷100 (incl. home advantage)', ppg5_diff: 'Points per game, last 5 (home − away)', gf10_diff: 'Goals scored per game (home − away)', ga10_diff: 'Goals conceded per game (home − away)', home_ppg_home5: 'Home team: points per home game', away_ppg_away5: 'Away team: points per away game', gd10_home: 'Home goal difference per game', gd10_away: 'Away goal difference per game' })[k] || k)}</td><td>${v}</td></tr>`).join('') : '';
        return `<div class="fx"><div class="when"><span><i class="fas fa-calendar"></i> ${fmtD(f.date)} ${esc(f.time)}</span><span><i class="fas fa-location-dot"></i> ${esc(f.venue || 'TBC')}</span><span class="pill type">${esc(f.team_group)} ${esc(f.age_group)}</span>
            <span class="pill ${q}" title="How much match history the two teams have">${q === 'good' ? 'Good data' : q === 'limited' ? 'Limited history' : 'Insufficient history'}</span></div>
            <div class="teams"><div class="t ${club(f.home_team)}">${esc(f.home_team)}</div><div class="vs">VS</div><div class="t r ${club(f.away_team)}">${esc(f.away_team)}</div></div>
            ${p ? `<div class="probbar"><span class="h" data-g="${p.H}" style="flex-grow:.33">${pct(p.H)}</span><span class="d" data-g="${p.D}" style="flex-grow:.33">${pct(p.D)}</span><span class="a" data-g="${p.A}" style="flex-grow:.33">${pct(p.A)}</span></div>
                <div class="problabels"><span>Home win</span><span>Draw</span><span>Away win</span></div>` : '<div class="empty-state">Prediction pending…</div>'}
            ${q !== 'good' ? `<p style="font-size:12.5px;color:#9a6a0c;margin-top:8px"><i class="fas fa-triangle-exclamation"></i> ${q === 'insufficient' ? 'At least one team has fewer than 3 recorded matches, so this estimate mostly reflects home advantage. Record results or import history to improve it.' : 'Fewer than 8 recorded matches for one side – treat with some caution.'}</p>` : ''}
            <div class="formrow">${team(f.home_team, f.home, 'h')}${team(f.away_team, f.away, 'a')}</div>
            ${feats ? `<details class="why"><summary>What the model looked at</summary><table>${feats}</table></details>` : ''}</div>`;
    }
    function calibration(bins) {
        if (!bins || !bins.length) return '<div class="empty-state">Not enough test matches.</div>';
        const W = 300, H = 180, P = 30;
        const x = v => P + (v - .3) / .6 * (W - P - 10), y = v => H - P - (v - .3) / .6 * (H - P - 10);
        const pts = bins.map(b => `${x(b.avg_confidence)},${y(b.actual_hit_rate)}`).join(' ');
        return `<svg class="calib" viewBox="0 0 ${W} ${H}" role="img" aria-label="Calibration chart">
            <line x1="${x(.3)}" y1="${y(.3)}" x2="${x(.9)}" y2="${y(.9)}" stroke="#cfd6e2" stroke-dasharray="4 4"/>
            ${[.3, .5, .7, .9].map(v => `<text x="${x(v)}" y="${H - 10}" font-size="10" text-anchor="middle" fill="#94a0b4">${v * 100}%</text><text x="${P - 6}" y="${y(v) + 3}" font-size="10" text-anchor="end" fill="#94a0b4">${v * 100}%</text>`).join('')}
            <polyline points="${pts}" fill="none" stroke="#b8944a" stroke-width="2.5"/>
            ${bins.map(b => `<circle cx="${x(b.avg_confidence)}" cy="${y(b.actual_hit_rate)}" r="${3 + Math.min(6, b.matches / 800)}" fill="#fff" stroke="#b8944a" stroke-width="2"><title>${b.range}: said ${pct(b.avg_confidence)}, right ${pct(b.actual_hit_rate)} of ${b.matches}</title></circle>`).join('')}
            <text x="${W - 10}" y="${H - P - 4}" font-size="10" text-anchor="end" fill="#6b778c">model confidence →</text></svg>
            <p style="font-size:12px;color:#6b778c">Each point compares how sure the model was with how often it was right. On the dashed line = perfectly honest probabilities.</p>`;
    }
    async function trainML() {
        const b = $('#mlTrain'); if (b) { b.disabled = true; b.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Training…'; }
        try { const r = await api('/api/ml/train', { method: 'POST' }); toast(`Retrained: ${r.metrics.selected}, ${pct(r.metrics.comparison[r.metrics.selected].accuracy)} on unseen matches`); }
        catch (e) { toast(e.message, 'err'); }
        loadML(); loadOverview();
    }

    // ===================================================== MATCHES: record results
    window.recordResult = function (id, home, away) {
        const m = modal('resultModal', {
            title: 'Record result', small: true,
            body: `<form id="resForm" class="fgrid"><div class="full" style="font-weight:700">${esc(home)} vs ${esc(away)}</div>
                <div><label>${esc(home)}</label><input type="number" name="home_score" min="0" max="50" required></div>
                <div><label>${esc(away)}</label><input type="number" name="away_score" min="0" max="50" required></div>
                <div class="full" style="font-size:12.5px;color:#6b778c">Saving grades the model's prediction, updates team form, re-predicts the other fixtures and publishes the result on the website.</div></form>`,
            foot: '<button class="btn-ghost" data-close>Cancel</button><button class="btn-gold" form="resForm">Save result</button>',
        });
        $('#resForm', m).querySelector('input').focus();
        $('#resForm', m).addEventListener('submit', async e => {
            e.preventDefault();
            const f = Object.fromEntries(new FormData(e.target));
            try {
                const r = await api(`/api/team-matches/${id}/result`, J('PUT', { home_score: +f.home_score, away_score: +f.away_score }));
                const ev = (r.automation || []).find(a => a.evaluation);
                toast(ev && ev.evaluation ? `Result saved · model ${ev.evaluation.correct ? 'predicted it correctly' : 'did not predict this'} (${pct(ev.evaluation.probability_of_actual)} for this result)` : 'Result saved');
                closeModal('resultModal');
                if (typeof window.loadMatches === 'function') window.loadMatches();
                loadOverview(); if (location.hash.includes('ml')) loadML();
            } catch (er) { toast(er.message, 'err'); }
        });
    };

    // ===================================================== AUDIT & AUTOMATION
    async function loadAudit() {
        const host = $('#auditBody');
        if (!host) return;
        try {
            const [rules, log] = await Promise.all([api('/api/automation/rules'), caps.has('audit') ? api('/api/audit-log?limit=150') : Promise.resolve([])]);
            host.innerHTML = `<div class="box"><h3><span><i class="fas fa-diagram-project"></i>Automation rules</span></h3><div class="tbl-wrap"><table class="tbl"><thead><tr><th>When…</th><th>…then automatically</th><th>Activity</th></tr></thead><tbody>
                ${rules.map(r => `<tr><td><b>${esc(r.when)}</b></td><td>${esc(r.then)}</td><td>${r.times === null ? '–' : r.times}</td></tr>`).join('')}</tbody></table></div></div>
                ${caps.has('audit') ? `<div class="box mt"><h3><span><i class="fas fa-shield-halved"></i>Audit trail</span><small style="text-transform:none;letter-spacing:0">who did what, newest first</small></h3><div class="tbl-wrap" style="max-height:520px;overflow:auto"><table class="tbl"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Details</th></tr></thead><tbody>
                ${log.map(a => `<tr><td style="white-space:nowrap">${esc(a.created_at)}</td><td>${esc(a.username || 'system')}</td><td><span class="pill type">${esc(label(a.action))}</span></td><td>${esc(a.entity || '')} ${esc(a.entity_id || '')}</td><td style="font-size:12px;color:#6b778c;max-width:420px">${esc(typeof a.details === 'object' && a.details ? Object.entries(a.details).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ') : (a.details || ''))}</td></tr>`).join('')}
                </tbody></table></div></div>` : ''}`;
        } catch (e) { host.innerHTML = `<div class="box">${esc(e.message)}</div>`; }
    }

    // ===================================================== ROUTING
    function route() {
        const h = location.hash.replace(/^#/, '');
        if (!h) return;
        const [panel, a, b] = h.split('/');
        const map = { documents: 'documents', finance: 'finance', ml: 'ml', matches: 'matches', development: 'development', audit: 'audit', player: 'development', players: 'players', tasks: 'tasks', analytics: 'analytics', users: 'users' };
        if (!map[panel] || typeof window.showPanel !== 'function') return;
        window.showPanel(map[panel]);
        if (panel === 'documents' && a === 'outstanding') { docState.filter = 'outstanding'; renderDocs(); }
        else if (panel === 'documents' && a) openDoc(+a);
        if (panel === 'finance' && a === 'batch' && b) { finTab('imports'); openBatch(+b); }
        if (panel === 'player' && a && window.PDI) PDI.openPlayer(+a);
    }

    const loaders = { documents: loadDocs, finance: loadFinance, ml: loadML, audit: loadAudit, dashboard: loadOverview };
    window.Modules = {
        async init() {
            try { const c = await api('/api/me/capabilities'); caps = new Set(c.capabilities); } catch (e) { caps = new Set(); }
            $$('[data-cap]').forEach(el => { if (!caps.has(el.dataset.cap)) el.style.display = 'none'; });
            bindNotifications();
            const dz = $('#docDrop');
            if (dz) dropzone(dz, uploadDocs);
            $('#docTabs')?.addEventListener('click', e => { const b = e.target.closest('[data-f]'); if (b) { docState.filter = b.dataset.f; renderDocs(); } });
            $('#docList')?.addEventListener('click', e => { const r = e.target.closest('[data-doc]'); if (r) openDoc(+r.dataset.doc); });
            $('#finTabs')?.addEventListener('click', e => { const b = e.target.closest('[data-t]'); if (b) finTab(b.dataset.t); });
            loadOverview();
            window.addEventListener('hashchange', route);
            setTimeout(route, 300);
            setInterval(() => { if (!document.hidden) api('/api/overview').then(o => setBell(o.notifications)).catch(() => {}); }, 60000);
        },
        onShow(panel) { if (loaders[panel]) loaders[panel](); },
        openDoc, openBatch, loadOverview,
        hasCap: c => caps.has(c),
        ui: { modal, closeModal, api, J, esc, toast, fmtD, R, pct, label },
    };
})();
