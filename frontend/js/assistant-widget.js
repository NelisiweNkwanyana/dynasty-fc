/* Dynasty FC Assistant — floating chat widget (public site + dashboard).
   Answers come from /api/assistant/ask, which only uses records the current
   viewer is allowed to see. */
(() => {
    'use strict';
    if (window.__dfcAssistant) return;
    window.__dfcAssistant = true;
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const onDashboard = location.pathname.startsWith('/dashboard');

    const css = `
    .dfa-fab{position:fixed;right:22px;bottom:22px;z-index:1200;width:58px;height:58px;border-radius:50%;border:0;cursor:pointer;
      background:linear-gradient(145deg,#f0cf86,#b8944a 60%,#8a6a2c);color:#0a1a33;font-size:24px;box-shadow:0 14px 34px rgba(6,15,31,.35);
      display:grid;place-items:center;transition:transform .25s}
    .dfa-fab:hover{transform:translateY(-3px) scale(1.04)} .dfa-fab:focus-visible{outline:3px solid #0a1a33;outline-offset:3px}
    .dfa-panel{position:fixed;right:22px;bottom:92px;z-index:1200;width:min(390px,calc(100vw - 28px));height:min(560px,calc(100vh - 120px));
      background:#fff;border-radius:20px;box-shadow:0 24px 60px rgba(6,15,31,.32);display:none;flex-direction:column;overflow:hidden;
      font-family:Manrope,'Segoe UI',system-ui,sans-serif;color:#0f1b2d;border:1px solid #e6e9f0}
    .dfa-panel.open{display:flex;animation:dfaIn .3s cubic-bezier(.2,.8,.2,1)}
    @keyframes dfaIn{from{opacity:0;transform:translateY(14px) scale(.98)}to{opacity:1;transform:none}}
    .dfa-h{padding:14px 16px;background:linear-gradient(120deg,#0a1a33,#163563);color:#fff;display:flex;align-items:center;gap:10px}
    .dfa-h .av{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;background:linear-gradient(145deg,#f0cf86,#b8944a);color:#0a1a33}
    .dfa-h b{display:block;font-size:15px} .dfa-h small{color:#b9c5da;font-size:11.5px}
    .dfa-h button{margin-left:auto;border:0;background:rgba(255,255,255,.12);color:#fff;width:32px;height:32px;border-radius:9px;cursor:pointer}
    .dfa-body{flex:1;overflow-y:auto;padding:14px;background:#f5f7fb;display:flex;flex-direction:column;gap:10px}
    .dfa-m{max-width:88%;padding:10px 12px;border-radius:14px;font-size:14px;line-height:1.5;white-space:pre-wrap;word-wrap:break-word}
    .dfa-m.u{align-self:flex-end;background:#0a1a33;color:#fff;border-bottom-right-radius:4px}
    .dfa-m.b{align-self:flex-start;background:#fff;border:1px solid #e6e9f0;border-bottom-left-radius:4px}
    .dfa-m.b.denied{border-color:#f0c9c9;background:#fff8f8}
    .dfa-src{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px} .dfa-src a{font-size:12px;font-weight:700;color:#7a5c1e;background:rgba(184,148,74,.14);padding:3px 8px;border-radius:999px;text-decoration:none}
    .dfa-meta{font-size:11px;color:#94a0b4;margin-top:6px}
    .dfa-sug{display:flex;flex-wrap:wrap;gap:6px;padding:0 14px 10px;background:#f5f7fb}
    .dfa-sug button{border:1px solid #e0d2ae;background:#fff;color:#0a1a33;font:600 12.5px inherit;padding:6px 10px;border-radius:999px;cursor:pointer}
    .dfa-sug button:hover{background:#fbf5e6}
    .dfa-f{display:flex;gap:8px;padding:10px;border-top:1px solid #e6e9f0;background:#fff}
    .dfa-f input{flex:1;border:1px solid #e6e9f0;border-radius:12px;padding:11px 12px;font:500 14px inherit;outline:none}
    .dfa-f input:focus{border-color:#b8944a;box-shadow:0 0 0 3px rgba(184,148,74,.15)}
    .dfa-f button{border:0;border-radius:12px;padding:0 14px;background:#0a1a33;color:#fff;cursor:pointer;font-size:15px}
    .dfa-typing span{display:inline-block;width:7px;height:7px;border-radius:50%;background:#b8944a;margin-right:3px;animation:dfaB 1s infinite}
    .dfa-typing span:nth-child(2){animation-delay:.15s}.dfa-typing span:nth-child(3){animation-delay:.3s}
    @keyframes dfaB{0%,80%,100%{opacity:.25}40%{opacity:1}}
    .toasts{bottom:96px!important}
    @media(max-width:520px){.dfa-panel{right:14px;bottom:86px}.dfa-fab{right:14px;bottom:14px}}`;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

    const fab = document.createElement('button');
    fab.className = 'dfa-fab'; fab.type = 'button'; fab.setAttribute('aria-label', 'Ask the Dynasty FC assistant');
    fab.innerHTML = '<i class="fas fa-comment-dots" aria-hidden="true"></i>';
    const panel = document.createElement('section');
    panel.className = 'dfa-panel'; panel.setAttribute('aria-label', 'Dynasty FC assistant');
    panel.innerHTML = `<div class="dfa-h"><span class="av"><i class="fas fa-crown"></i></span><div><b>Dynasty FC Assistant</b><small id="dfaScope">Answers from club records</small></div>
        <button type="button" data-x aria-label="Close"><i class="fas fa-xmark"></i></button></div>
        <div class="dfa-body" id="dfaBody" aria-live="polite"></div><div class="dfa-sug" id="dfaSug"></div>
        <form class="dfa-f" id="dfaForm"><input id="dfaIn" placeholder="Ask about fixtures, players, finance…" maxlength="500" autocomplete="off" aria-label="Your question"><button aria-label="Send"><i class="fas fa-paper-plane"></i></button></form>`;
    document.body.append(fab, panel);
    const body = panel.querySelector('#dfaBody'), input = panel.querySelector('#dfaIn');
    let started = false;

    function add(html, cls) {
        const d = document.createElement('div'); d.className = 'dfa-m ' + cls; d.innerHTML = html; body.appendChild(d); body.scrollTop = body.scrollHeight; return d;
    }
    function linkFor(src) {
        if (!src.link) return '';
        const isDash = src.link.startsWith('/dashboard');
        if (isDash && !onDashboard) return '';
        return `<a href="${esc(src.link)}" ${isDash ? 'data-hash="' + esc(src.link.split('#')[1] || '') + '"' : ''}>${esc(src.label)}</a>`;
    }
    async function ask(q) {
        panel.querySelector('#dfaSug').style.display = 'none';
        add(esc(q), 'u');
        const t = add('<span class="dfa-typing"><span></span><span></span><span></span></span>', 'b');
        try {
            const r = await fetch('/api/assistant/ask', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q }) });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || 'Something went wrong');
            const links = (d.sources || []).map(linkFor).filter(Boolean).join('');
            t.className = 'dfa-m b' + (d.intent === 'denied' ? ' denied' : '');
            t.innerHTML = esc(d.answer) + (links ? `<div class="dfa-src">${links}</div>` : '') + (d.phrased_by_ai ? '<div class="dfa-meta">Worded by AI from the records above</div>' : '');
        } catch (e) { t.textContent = 'Sorry – ' + e.message; }
    }
    async function start() {
        if (started) return; started = true;
        try {
            const c = await (await fetch('/api/me/capabilities', { credentials: 'same-origin' })).json();
            panel.querySelector('#dfaScope').textContent = c.logged_in ? `Signed in as ${c.role} · answers use only records you can access` : 'Public information · log in for more';
        } catch (e) { /* ignore */ }
        add("Hi! I answer questions from Dynasty FC's own records — fixtures, results and predictions" + (onDashboard ? ', players, documents and finance (depending on your access).' : '.') + ' What would you like to know?', 'b');
        try {
            const s = await (await fetch('/api/assistant/suggestions', { credentials: 'same-origin' })).json();
            panel.querySelector('#dfaSug').innerHTML = s.map(x => `<button type="button">${esc(x)}</button>`).join('');
        } catch (e) { /* ignore */ }
    }
    fab.addEventListener('click', () => { panel.classList.toggle('open'); if (panel.classList.contains('open')) { start(); input.focus(); } });
    panel.querySelector('[data-x]').addEventListener('click', () => panel.classList.remove('open'));
    panel.querySelector('#dfaSug').addEventListener('click', e => { const b = e.target.closest('button'); if (b) { ask(b.textContent); } });
    panel.querySelector('#dfaForm').addEventListener('submit', e => { e.preventDefault(); const q = input.value.trim(); if (q) { input.value = ''; ask(q); } });
    body.addEventListener('click', e => {
        const a = e.target.closest('a[data-hash]');
        if (a && onDashboard) { e.preventDefault(); location.hash = a.dataset.hash; window.dispatchEvent(new HashChangeEvent('hashchange')); }
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') panel.classList.remove('open'); });
})();
