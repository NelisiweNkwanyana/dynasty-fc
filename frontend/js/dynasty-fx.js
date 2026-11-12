/* =====================================================================
   Dynasty FC — effects layer (dynasty-fx)
   Adds 3D depth, light and motion on top of the existing pages without
   changing their markup, images or styles. No external libraries.
   ===================================================================== */
(() => {
    'use strict';

    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const fine = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    const $ = (s, r = document) => r.querySelector(s);
    const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
    const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
    const lerp = (a, b, t) => a + (b - a) * t;
    const DPR = () => Math.min(window.devicePixelRatio || 1, 2);
    const debug = /[?&]fxdebug\b/.test(location.search);
    const dbg = (...a) => { if (debug) console.info('[dfx]', ...a); };

    // ------------------------------------------------ shared pointer + loop
    const ptr = { x: 0, y: 0, tx: 0, ty: 0, cx: -9999, cy: -9999, active: false };
    window.addEventListener('pointermove', e => {
        ptr.tx = e.clientX / innerWidth * 2 - 1;
        ptr.ty = e.clientY / innerHeight * 2 - 1;
        ptr.cx = e.clientX; ptr.cy = e.clientY; ptr.active = true;
    }, { passive: true });
    document.addEventListener('pointerleave', () => { ptr.tx = 0; ptr.ty = 0; ptr.active = false; });

    const loops = new Set();
    let running = false;
    function frame(t) {
        ptr.x = lerp(ptr.x, ptr.tx, 0.07);
        ptr.y = lerp(ptr.y, ptr.ty, 0.07);
        loops.forEach(fn => { try { fn(t); } catch (e) { console.warn('[dfx]', e); loops.delete(fn); } });
        requestAnimationFrame(frame);
    }
    const addLoop = fn => { loops.add(fn); if (!running) { running = true; requestAnimationFrame(frame); } };

    function onScreen(el) {
        const st = { visible: true };
        if ('IntersectionObserver' in window) {
            new IntersectionObserver(([e]) => { st.visible = e.isIntersecting; }, { rootMargin: '80px' }).observe(el);
        }
        return st;
    }

    // Lift a host's existing children above an injected effect layer.
    function elevateChildren(host, layer) {
        if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
        Array.from(host.children).forEach(ch => {
            if (ch === layer || ch.classList.contains('dfx-layer') || ch.classList.contains('dfx-flood')) return;
            const cs = getComputedStyle(ch);
            if (cs.position === 'static') ch.style.position = 'relative';
            if (cs.zIndex === 'auto') ch.style.zIndex = '2';
        });
    }

    // ======================================================== 3D football
    // A truncated icosahedron rendered per pixel: 12 pentagons, 20 hexagons,
    // with diffuse + specular lighting. White panels, navy pentagons, gold seams.
    const PHI = (1 + Math.sqrt(5)) / 2;
    const norm = v => { const l = Math.hypot(v[0], v[1], v[2]); return [v[0] / l, v[1] / l, v[2] / l]; };
    const PENT = [], HEX = [];
    [[0, 1, PHI], [0, 1, -PHI], [0, -1, PHI], [0, -1, -PHI], [1, PHI, 0], [1, -PHI, 0], [-1, PHI, 0], [-1, -PHI, 0],
     [PHI, 0, 1], [PHI, 0, -1], [-PHI, 0, 1], [-PHI, 0, -1]].forEach(v => PENT.push(norm(v)));
    for (const a of [-1, 1]) for (const b of [-1, 1]) for (const c of [-1, 1]) HEX.push(norm([a, b, c]));
    const ip = 1 / PHI;
    // face centres of the icosahedron above (its dual dodecahedron, same chirality)
    [[0, PHI, ip], [0, PHI, -ip], [0, -PHI, ip], [0, -PHI, -ip], [PHI, ip, 0], [PHI, -ip, 0], [-PHI, ip, 0], [-PHI, -ip, 0],
     [ip, 0, PHI], [ip, 0, -PHI], [-ip, 0, PHI], [-ip, 0, -PHI]].forEach(v => HEX.push(norm(v)));
    const PF = new Float32Array(PENT.flat()), HF = new Float32Array(HEX.flat());
    const DELTA = 4.26 * Math.PI / 180, CD = Math.cos(DELTA), SD = Math.sin(DELTA);

    function rotAxis(ax, ay, az, a) {
        const l = Math.hypot(ax, ay, az) || 1; ax /= l; ay /= l; az /= l;
        const c = Math.cos(a), s = Math.sin(a), t = 1 - c;
        return [t * ax * ax + c, t * ax * ay - s * az, t * ax * az + s * ay,
                t * ax * ay + s * az, t * ay * ay + c, t * ay * az - s * ax,
                t * ax * az - s * ay, t * ay * az + s * ax, t * az * az + c];
    }
    function mul(a, b) {
        const o = new Array(9);
        for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++)
            o[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c];
        return o;
    }
    function orthonormalize(m) {
        let x = norm([m[0], m[3], m[6]]), y = [m[1], m[4], m[7]];
        const d = x[0] * y[0] + x[1] * y[1] + x[2] * y[2];
        y = norm([y[0] - d * x[0], y[1] - d * x[1], y[2] - d * x[2]]);
        const z = [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]];
        return [x[0], y[0], z[0], x[1], y[1], z[1], x[2], y[2], z[2]];
    }

    class Ball3D {
        constructor(opts = {}) {
            this.pent = opts.pent || [12, 28, 56];
            this.hex = opts.hex || [250, 249, 244];
            this.seam = opts.seam || [176, 138, 64];
            this.canvas = document.createElement('canvas');
            this.ctx = this.canvas.getContext('2d');
            this.m = mul(rotAxis(1, 0, 0, 0.5), rotAxis(0, 1, 0, 0.4));
            this.r = 0;
        }
        setRadius(r) {
            r = Math.max(6, Math.round(r));
            if (r === this.r) return;
            this.r = r;
            const size = r * 2 + 2;
            this.canvas.width = this.canvas.height = size;
            this.img = this.ctx.createImageData(size, size);
            const idx = [], nx = [], ny = [], nz = [], shade = [], spec = [], alpha = [];
            const L = norm([-0.5, 0.62, 0.6]), H = norm([L[0], L[1], L[2] + 1]);
            const c = r + 1;
            for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) {
                const dx = (x + 0.5 - c) / r, dy = (y + 0.5 - c) / r, d2 = dx * dx + dy * dy;
                if (d2 > 1.0 + 2.5 / r) continue;
                const dd = Math.min(1, Math.sqrt(d2));
                const vx = dx / Math.max(dd, 1e-6) * Math.min(dd, 0.9999), vy = -dy / Math.max(dd, 1e-6) * Math.min(dd, 0.9999);
                const vz = Math.sqrt(Math.max(0, 1 - vx * vx - vy * vy));
                const dif = Math.max(0, vx * L[0] + vy * L[1] + vz * L[2]);
                idx.push((y * size + x) * 4); nx.push(vx); ny.push(vy); nz.push(vz);
                shade.push((0.36 + 0.74 * dif) * (0.62 + 0.38 * Math.sqrt(vz)));
                spec.push(Math.pow(Math.max(0, vx * H[0] + vy * H[1] + vz * H[2]), 46) * 0.55);
                alpha.push(clamp((1 - Math.sqrt(d2)) * r + 0.5, 0, 1) * 255);
            }
            this.P = { idx: Int32Array.from(idx), nx: Float32Array.from(nx), ny: Float32Array.from(ny), nz: Float32Array.from(nz),
                shade: Float32Array.from(shade), spec: Float32Array.from(spec), alpha: Uint8ClampedArray.from(alpha) };
            this.seamW = Math.max(0.006, 0.85 / r);
        }
        spin(ax, ay, az, angle) {
            this.m = mul(rotAxis(ax, ay, az, angle), this.m);
            if (Math.random() < 0.02) this.m = orthonormalize(this.m);
        }
        render() {
            const { idx, nx, ny, nz, shade, spec, alpha } = this.P, d = this.img.data, m = this.m;
            const sw = this.seamW, [pr, pg, pb] = this.pent, [hr, hg, hb] = this.hex, [sr, sg, sb] = this.seam;
            for (let k = 0; k < idx.length; k++) {
                const x = nx[k], y = ny[k], z = nz[k];
                const qx = m[0] * x + m[3] * y + m[6] * z, qy = m[1] * x + m[4] * y + m[7] * z, qz = m[2] * x + m[5] * y + m[8] * z;
                let dp = -2;
                for (let i = 0; i < 36; i += 3) { const v = PF[i] * qx + PF[i + 1] * qy + PF[i + 2] * qz; if (v > dp) dp = v; }
                let h1 = -2, h2 = -2;
                for (let i = 0; i < 60; i += 3) {
                    const v = HF[i] * qx + HF[i + 1] * qy + HF[i + 2] * qz;
                    if (v > h1) { h2 = h1; h1 = v; } else if (v > h2) h2 = v;
                }
                const pc = dp * CD - Math.sqrt(Math.max(0, 1 - dp * dp)) * SD;
                let r, g, b, edge;
                if (pc > h1) { r = pr; g = pg; b = pb; edge = pc - h1; }
                else { r = hr; g = hg; b = hb; edge = Math.min(h1 - pc, h1 - h2); }
                if (edge < sw) { const t = 1 - edge / sw; r = lerp(r, sr, t); g = lerp(g, sg, t); b = lerp(b, sb, t); }
                const s = shade[k], sp = spec[k] * 255, o = idx[k];
                d[o] = r * s + sp; d[o + 1] = g * s + sp; d[o + 2] = b * s + sp; d[o + 3] = alpha[k];
            }
            this.ctx.putImageData(this.img, 0, 0);
            return this.canvas;
        }
    }

    // soft glow sprite for particles
    function glowSprite(rgb) {
        const c = document.createElement('canvas'); c.width = c.height = 64;
        const x = c.getContext('2d'), g = x.createRadialGradient(32, 32, 0, 32, 32, 32);
        g.addColorStop(0, `rgba(${rgb},1)`); g.addColorStop(0.25, `rgba(${rgb},.55)`); g.addColorStop(1, `rgba(${rgb},0)`);
        x.fillStyle = g; x.fillRect(0, 0, 64, 64);
        return c;
    }

    // ====================================================== depth particles
    function particles(host, { rgb = '240,207,134', density = 1, rise = 1, fixed = false } = {}) {
        if (!host || reduce) return;
        const cv = document.createElement('canvas');
        cv.className = fixed ? 'dfx-fixed-layer' : 'dfx-layer';
        cv.setAttribute('aria-hidden', 'true');
        host.insertBefore(cv, host.firstChild);
        if (!fixed) elevateChildren(host, cv);
        const ctx = cv.getContext('2d'), sprite = glowSprite(rgb), vis = onScreen(fixed ? document.body : host);
        let W = 0, H = 0, pts = [];
        function size() {
            const dpr = DPR(), r = fixed ? { width: innerWidth, height: innerHeight } : host.getBoundingClientRect();
            W = cv.width = Math.max(1, Math.round(r.width * dpr)); H = cv.height = Math.max(1, Math.round(r.height * dpr));
            const n = Math.min(150, Math.round(r.width * r.height / 11000 * density));
            pts = Array.from({ length: n }, () => ({
                x: Math.random(), y: Math.random(), z: 0.25 + Math.random() * 0.75,
                v: (0.00006 + Math.random() * 0.00016) * rise, ph: Math.random() * 6.28, sw: Math.random() * 0.0004,
            }));
        }
        size();
        window.addEventListener('resize', size);
        addLoop(t => {
            if (!vis.visible) return;
            const dpr = DPR();
            ctx.clearRect(0, 0, W, H);
            for (const p of pts) {
                p.y -= p.v * (0.6 + p.z); p.x += Math.sin(t * 0.0004 + p.ph) * p.sw;
                if (p.y < -0.05) { p.y = 1.05; p.x = Math.random(); }
                const px = p.x * W + ptr.x * 26 * p.z * dpr, py = p.y * H + ptr.y * 16 * p.z * dpr;
                const tw = 0.55 + 0.45 * Math.sin(t * 0.002 + p.ph * 3);
                const s = (2 + 7 * p.z * p.z) * dpr;
                ctx.globalAlpha = (0.18 + 0.55 * p.z) * tw;
                ctx.drawImage(sprite, px - s, py - s, s * 2, s * 2);
            }
            ctx.globalAlpha = 1;
        });
    }

    // ================================================ hero depth parallax
    function depth(el, { rx = 3, ry = 4, tx = 12, ty = 8, scroll = 0.18 } = {}) {
        if (!el || reduce) return;
        el.classList.add('dfx-depth');
        const vis = onScreen(el);
        addLoop(() => {
            if (!vis.visible) return;
            const sy = Math.min(window.scrollY, innerHeight) * scroll;
            const f = fine ? 1 : 0;
            el.style.transform = `perspective(1100px) translate3d(${ptr.x * tx * f}px, ${ptr.y * ty * f + sy}px, 0) rotateX(${-ptr.y * rx * f}deg) rotateY(${ptr.x * ry * f}deg)`;
            if (scroll) el.style.opacity = String(clamp(1 - window.scrollY / (innerHeight * 0.9), 0.15, 1));
        });
    }

    // ===================================================== card tilt + glare
    const TILT = ['.stat-item', '.news-card', '.testimonial-card', '.benefit-card', '.player-card', '.match-card', '.coach-card',
        '.leader-card', '.donate-card', '.donor-card', '.gallery-item', '.value-item', '.timeline-item', '.info-item',
        '.login-container', '.register-container', '.staff-card', '.logged-in-card', '.opt', '.card'].join(',');
    function tiltCards() {
        if (!fine || reduce) return;
        let cur = null;
        const reset = el => { if (!el) return; el.style.transform = ''; el.classList.remove('dfx-tilting'); };
        document.addEventListener('pointermove', e => {
            const el = e.target.closest && e.target.closest(TILT);
            if (el !== cur) { reset(cur); cur = null; }
            if (!el) return;
            const r = el.getBoundingClientRect();
            if (r.width > 760 || r.height > 760) return;
            cur = el;
            if (!el.querySelector(':scope > .dfx-glare')) {
                if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
                const g = document.createElement('span'); g.className = 'dfx-glare'; el.appendChild(g);
            }
            const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
            const k = clamp(320 / Math.max(r.width, 1), 0.35, 1);
            el.classList.add('dfx-tilting');
            el.style.transform = `perspective(900px) rotateX(${(0.5 - y) * 10 * k}deg) rotateY(${(x - 0.5) * 12 * k}deg) translateY(-4px)`;
            el.style.setProperty('--gx', `${x * 100}%`); el.style.setProperty('--gy', `${y * 100}%`);
        }, { passive: true });
        document.addEventListener('pointerleave', () => { reset(cur); cur = null; });
    }

    // ===================================================== magnetic buttons
    function magnetic() {
        if (!fine || reduce) return;
        $$('.btn-hero, .hero-cta, .btn-cta, .login-btn, .btn-login').forEach(btn => {
            btn.addEventListener('pointermove', e => {
                const r = btn.getBoundingClientRect();
                const dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2);
                btn.style.transform = `translate(${dx * 0.22}px, ${dy * 0.3}px)`;
            });
            btn.addEventListener('pointerleave', () => { btn.style.transform = ''; });
        });
    }

    // ======================================================= scroll progress
    function progressBar() {
        if (document.documentElement.scrollHeight <= innerHeight * 1.3) return;
        const bar = document.createElement('div'); bar.className = 'dfx-progress'; document.body.appendChild(bar);
        const upd = () => {
            const max = document.documentElement.scrollHeight - innerHeight;
            bar.style.transform = `scaleX(${max > 0 ? clamp(scrollY / max, 0, 1) : 0})`;
        };
        addEventListener('scroll', upd, { passive: true }); upd();
    }

    // ================================================ home: 3D crest + orbit
    function crest() {
        const logo = $('.hero .hero-logo');
        if (!logo) return;
        const wrap = document.createElement('span');
        wrap.className = 'dfx-crest';
        logo.parentNode.insertBefore(wrap, logo); wrap.appendChild(logo);
        if (reduce) return;
        const shine = document.createElement('span');
        shine.className = 'dfx-crest-shine';
        const src = `url("${logo.currentSrc || logo.src}")`;
        shine.style.webkitMaskImage = src; shine.style.maskImage = src;
        wrap.appendChild(shine);

        // a small 3D ball orbiting the crest, passing behind and in front of it
        const back = document.createElement('canvas'), front = document.createElement('canvas');
        back.className = 'dfx-orbit back'; front.className = 'dfx-orbit front';
        wrap.insertBefore(back, logo); wrap.appendChild(front);
        const ball = new Ball3D();
        const vis = onScreen(wrap);
        let W = 0, H = 0;
        const size = () => { const d = DPR(); W = back.width = front.width = Math.round(back.offsetWidth * d); H = back.height = front.height = Math.round(back.offsetHeight * d); ball.setRadius(Math.max(8, Math.min(W, H) * 0.08)); };
        size(); addEventListener('resize', size);
        let last = 0;
        addLoop(t => {
            if (!vis.visible) return;
            const dt = Math.min(50, t - (last || t)); last = t;
            const idle = ptr.active ? 0 : Math.sin(t * 0.0006);
            wrap.style.transform = `perspective(900px) rotateY(${ptr.x * 16 + idle * 7}deg) rotateX(${-ptr.y * 10}deg)`;
            wrap.style.setProperty('--gx', `${50 + ptr.x * 40}%`); wrap.style.setProperty('--gy', `${40 + ptr.y * 40}%`);
            const sweep = ((t * 0.00012) % 1.4); // periodic shine across the crest
            wrap.style.setProperty('--sweep', `${180 - sweep * 300}%`);

            const a = t * 0.0011;
            const cx = W / 2 + Math.cos(a) * W * 0.40, cy = H * 0.52 + Math.sin(a) * H * 0.12 - Math.cos(a) * H * 0.06;
            const depthZ = Math.sin(a); // >0 front, <0 behind
            const zs = 0.86 + 0.26 * (depthZ + 1) / 2;
            ball.spin(0.3, 1, 0.2, dt * 0.004);
            const img = ball.render();
            const bc = back.getContext('2d'), fc = front.getContext('2d');
            bc.clearRect(0, 0, W, H); fc.clearRect(0, 0, W, H);
            const ctx = depthZ > 0 ? fc : bc;
            ctx.globalAlpha = 0.75 + 0.25 * depthZ;
            ctx.drawImage(img, cx - img.width * zs / 2, cy - img.height * zs / 2, img.width * zs, img.height * zs);
            ctx.globalAlpha = 1;
        });
    }

    // ====================================== gallery: the boy kicks the ball
    async function galleryKick() {
        const img = $('.gallery-hero .hero-image img');
        if (!img || reduce) return;
        if (!img.complete) await new Promise(r => { img.addEventListener('load', r, { once: true }); img.addEventListener('error', r, { once: true }); });
        try { await img.decode(); } catch (e) { /* already decoded */ }
        const NW = img.naturalWidth, NH = img.naturalHeight;
        if (!NW || !NH) return;

        // 1. analyse a small copy of the photo to find the boy and the ball
        const aw = Math.min(240, NW), ah = Math.round(NH * aw / NW);
        const ac = document.createElement('canvas'); ac.width = aw; ac.height = ah;
        const actx = ac.getContext('2d', { willReadFrequently: true });
        actx.drawImage(img, 0, 0, aw, ah);
        let px;
        try { px = actx.getImageData(0, 0, aw, ah).data; } catch (e) { return; }
        const cornerA = [0, aw - 1, (ah - 1) * aw, ah * aw - 1].reduce((s, i) => s + px[i * 4 + 3], 0) / 4;
        const transparent = cornerA < 20;
        const mask = new Uint8Array(aw * ah);
        for (let i = 0; i < aw * ah; i++) {
            const r = px[i * 4], g = px[i * 4 + 1], b = px[i * 4 + 2], a = px[i * 4 + 3];
            if (transparent) mask[i] = a > 90 ? 1 : 0;
            else { const lum = 0.299 * r + 0.587 * g + 0.114 * b, mx = Math.max(r, g, b), mn = Math.min(r, g, b); mask[i] = (lum < 214 || (mx - mn) / (mx + 1) > 0.22) ? 1 : 0; }
        }
        const label = new Int32Array(aw * ah), comps = [null];
        for (let i = 0; i < aw * ah; i++) {
            if (!mask[i] || label[i]) continue;
            const id = comps.length, c = { id, size: 0, minx: aw, maxx: 0, miny: ah, maxy: 0, sx: 0, sy: 0 };
            const stack = [i]; label[i] = id;
            while (stack.length) {
                const j = stack.pop(), x = j % aw, y = (j / aw) | 0;
                c.size++; c.sx += x; c.sy += y;
                if (x < c.minx) c.minx = x; if (x > c.maxx) c.maxx = x; if (y < c.miny) c.miny = y; if (y > c.maxy) c.maxy = y;
                const nb = [x > 0 ? j - 1 : -1, x < aw - 1 ? j + 1 : -1, y > 0 ? j - aw : -1, y < ah - 1 ? j + aw : -1];
                for (const n of nb) if (n >= 0 && mask[n] && !label[n]) { label[n] = id; stack.push(n); }
            }
            comps.push(c);
        }
        const real = comps.slice(1);
        dbg('analysis', { aw, ah, transparent, components: real.length });
        if (!real.length) return;
        const boy = real.reduce((a, b) => (b.size > a.size ? b : a));
        const minBall = aw * ah * 0.0015;
        const cands = real.filter(c => {
            if (c === boy || c.size < minBall) return false;
            const w = c.maxx - c.minx + 1, h = c.maxy - c.miny + 1;
            return w / h > 0.72 && w / h < 1.38 && c.size / (w * h) > 0.6;
        });
        dbg('candidates', real.filter(c => c.size > 30).map(c => ({ id: c.id, size: c.size, w: c.maxx - c.minx + 1, h: c.maxy - c.miny + 1, x: c.minx, y: c.miny })));
        if (!cands.length) { dbg('no ball found'); return; }
        const bc = cands.reduce((a, b) => (b.size > a.size ? b : a));
        const s = NW / aw;
        const ball = {
            x: (bc.sx / bc.size + 0.5) * s, y: (bc.sy / bc.size + 0.5) * s,
            r: ((bc.maxx - bc.minx + 1) + (bc.maxy - bc.miny + 1)) / 4 * s,
        };

        // 2. find the top of the foot under the ball (scan down from the ball)
        let footY = null;
        const lbx0 = Math.round(bc.minx + (bc.maxx - bc.minx) * 0.2), lbx1 = Math.round(bc.maxx - (bc.maxx - bc.minx) * 0.2);
        for (let y = bc.maxy + 2; y < Math.min(ah, bc.maxy + (bc.maxy - bc.miny) * 5); y++) {
            let hit = 0;
            for (let x = lbx0; x <= lbx1; x++) { const l = label[y * aw + x]; if (l && l !== bc.id && comps[l].size > bc.size * 0.4) hit++; }
            if (hit >= 2) { footY = (y + 0.5) * s; break; }
        }
        const contact = { x: ball.x + ball.r * 0.15, y: footY !== null ? footY - ball.r * 0.92 : ball.y + ball.r * 2.4 };
        dbg('ball', ball, 'footY', footY, 'contact', contact);
        if (contact.y <= ball.y) { dbg('foot not below ball'); return; }
        // kick-up height: at least ~2.6 ball diameters' worth of rise, so it reads as a real kick
        const D = Math.max(contact.y - ball.y, ball.r * 2.6);

        // 3. lift the ball (and its motion streaks) out of the photo
        const full = document.createElement('canvas'); full.width = NW; full.height = NH;
        const fctx = full.getContext('2d', { willReadFrequently: true });
        fctx.drawImage(img, 0, 0);
        const R = ball.r * 2.4;
        const x0 = Math.max(0, Math.floor(ball.x - R)), x1 = Math.min(NW, Math.ceil(ball.x + R));
        const y0 = Math.max(0, Math.floor(ball.y - R)), y1 = Math.min(NH, Math.ceil(ball.y + R));
        const region = fctx.getImageData(x0, y0, x1 - x0, y1 - y0), rd = region.data;
        for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
            const l = label[Math.min(ah - 1, (y / s) | 0) * aw + Math.min(aw - 1, (x / s) | 0)];
            const dist = Math.hypot(x - ball.x, y - ball.y);
            const small = l && l !== boy.id && comps[l].size < bc.size * 0.35;
            const erase = l === bc.id || (small && dist < R) || (!l && dist < ball.r * 1.22);
            if (!erase) continue;
            const o = ((y - y0) * (x1 - x0) + (x - x0)) * 4;
            if (transparent) rd[o + 3] = 0; else { rd[o] = rd[o + 1] = rd[o + 2] = 255; }
        }
        fctx.putImageData(region, x0, y0);
        try { img.src = full.toDataURL('image/png'); await img.decode(); } catch (e) { return; }

        // 4. stage + overlay canvas that floats together with the photo
        const holder = img.parentNode;
        const stage = document.createElement('div'); stage.className = 'dfx-kick';
        stage.style.maxWidth = getComputedStyle(img).maxWidth;
        holder.insertBefore(stage, img); stage.appendChild(img);
        const fx = document.createElement('canvas'); fx.className = 'dfx-kick-fx'; fx.setAttribute('aria-hidden', 'true');
        stage.appendChild(fx);
        const hint = document.createElement('span'); hint.className = 'dfx-kick-hint'; hint.textContent = 'Click for a power kick';
        stage.appendChild(hint);
        stage.classList.add('dfx-ready');
        const fctx2 = fx.getContext('2d');
        const ball3d = new Ball3D();
        const vis = onScreen(stage);
        let W = 0, H = 0, sc = 1, ox = 0, oy = 0;
        function size() {
            stage.style.maxWidth = '';
            img.style.maxWidth = '';
            const mw = getComputedStyle(img).maxWidth;
            stage.style.maxWidth = mw;
            const d = DPR();
            W = fx.width = Math.round(fx.offsetWidth * d); H = fx.height = Math.round(fx.offsetHeight * d);
            sc = img.offsetWidth / NW * d;
            ball3d.setRadius(ball.r * sc);
            ox = 0.40 / 1.80 * W; oy = 0.28 / 1.56 * H; // overlay origin offset (see CSS)
        }
        size(); addEventListener('resize', size);

        // choreography: juggling kick-ups of varying height, apex at the photo's ball position
        const rhythm = [1, 0.62, 1.12, 0.7, 1.3, 0.8];
        // begin at the exact moment shown in the photo (ball rising past its original spot)
        const q = clamp((contact.y - ball.y) / D, 0, 1), u0 = (1 - Math.sqrt(1 - q)) / 2;
        let k = 0, h = 1, start = performance.now() - u0 * flight(1), power = false;
        const sparks = [], trail = [];
        function flight(hh) { return 1080 * Math.sqrt(hh); }
        stage.addEventListener('click', () => { power = true; });
        let squashUntil = 0, last = 0;

        addLoop(t => {
            if (!vis.visible) return;
            const dt = Math.min(50, t - (last || t)); last = t;
            let u = (t - start) / flight(h);
            if (u >= 1) {
                // contact with the boot
                squashUntil = t + 110;
                for (let i = 0; i < (power ? 26 : 12); i++) {
                    const a = Math.PI * (0.95 + Math.random() * 1.1), v = (0.6 + Math.random() * (power ? 2.6 : 1.4));
                    sparks.push({ x: contact.x, y: contact.y + ball.r * 0.85, vx: Math.cos(a) * v, vy: Math.sin(a) * v - 0.4, life: 1 });
                }
                sparks.push({ ring: true, x: contact.x, y: contact.y + ball.r * 0.9, life: 1 });
                k = (k + 1) % rhythm.length;
                h = power ? 1.9 : rhythm[k];
                power = false;
                start = t; u = 0;
            }
            const apexY = contact.y - D * h;
            const apexX = contact.x - ball.r * 0.45 * Math.min(1.4, h);
            const x = contact.x + (apexX - contact.x) * Math.sin(Math.PI * u);
            const y = contact.y - (contact.y - apexY) * 4 * u * (1 - u);
            const toward = h > 1.5 ? Math.sin(Math.PI * u) * 0.45 : Math.sin(Math.PI * u) * 0.06 * h;

            ball3d.spin(1, 0.25, 0.1, -dt * 0.0075 * (0.6 + h));
            const bimg = ball3d.render();

            fctx2.clearRect(0, 0, W, H);
            const X = ox + x * sc, Y = oy + y * sc;
            trail.push({ X, Y }); if (trail.length > 7) trail.shift();
            const speed = trail.length > 1 ? Math.hypot(X - trail[trail.length - 2].X, Y - trail[trail.length - 2].Y) : 0;
            if (speed > 4) trail.slice(0, -1).forEach((p, i) => {
                fctx2.globalAlpha = 0.05 + i * 0.03;
                const gs = 1 + toward;
                fctx2.drawImage(bimg, p.X - bimg.width * gs / 2, p.Y - bimg.height * gs / 2, bimg.width * gs, bimg.height * gs);
            });
            fctx2.globalAlpha = 1;
            const sq = t < squashUntil ? 1 - (squashUntil - t) / 110 : 1;
            const sx = t < squashUntil ? 1.1 - 0.1 * sq : 1, sy = t < squashUntil ? 0.86 + 0.14 * sq : 1;
            fctx2.save();
            fctx2.translate(X, Y); fctx2.scale(sx * (1 + toward), sy * (1 + toward));
            fctx2.shadowColor = 'rgba(10,26,51,.28)'; fctx2.shadowBlur = 18 * DPR(); fctx2.shadowOffsetY = 10 * DPR();
            fctx2.drawImage(bimg, -bimg.width / 2, -bimg.height / 2);
            fctx2.restore();

            for (let i = sparks.length - 1; i >= 0; i--) {
                const p = sparks[i];
                p.life -= dt / (p.ring ? 420 : 620);
                if (p.life <= 0) { sparks.splice(i, 1); continue; }
                const PX = ox + p.x * sc, PY = oy + p.y * sc;
                if (p.ring) {
                    const rr = (1 - p.life) * ball.r * sc * 1.6;
                    fctx2.strokeStyle = `rgba(184,148,74,${p.life * 0.7})`; fctx2.lineWidth = 2 * DPR();
                    fctx2.beginPath(); fctx2.ellipse(PX, PY, rr, rr * 0.35, 0, 0, Math.PI * 2); fctx2.stroke();
                } else {
                    p.x += p.vx * dt * 0.06 * ball.r / 40; p.y += p.vy * dt * 0.06 * ball.r / 40; p.vy += dt * 0.004;
                    fctx2.fillStyle = `rgba(212,175,106,${p.life})`;
                    fctx2.beginPath(); fctx2.arc(PX, PY, (1 + p.life * 2.2) * DPR(), 0, Math.PI * 2); fctx2.fill();
                }
            }
            if (fine) holder.style.transform = `perspective(1000px) rotateY(${ptr.x * -6}deg) rotateX(${ptr.y * 4}deg)`;
        });
    }

    // ============================================ teams: stadium floodlights
    function floodlights() {
        const host = $('.teams-page');
        if (!host || reduce) return;
        const f = document.createElement('div');
        f.className = 'dfx-flood'; f.setAttribute('aria-hidden', 'true');
        f.innerHTML = '<span class="beam l" style="left:7%;top:20%"></span><span class="beam r" style="left:93%;top:22%"></span>' +
            '<span class="lamp" style="left:7%;top:20%"></span><span class="lamp r" style="left:93%;top:22%"></span>';
        host.insertBefore(f, host.firstChild);
        elevateChildren(host, f);
        particles(host, { rgb: '205,225,255', density: 0.9, rise: 0.7 });
    }

    // ============================================ staff login: 3D balls
    function staffBalls() {
        const card = $('.staff-card');
        if (!card || reduce) return;
        const field = document.createElement('div'); field.className = 'dfx-ballfield'; field.setAttribute('aria-hidden', 'true');
        document.body.insertBefore(field, document.body.firstChild);
        const specs = [
            { fx: 0.78, fy: 0.68, r: 0.11, depth: 1.0, axis: [0.4, 1, 0.1], speed: 0.0016, bob: 0.018 },
            { fx: 0.2, fy: 0.24, r: 0.055, depth: 0.5, axis: [1, 0.3, 0.2], speed: 0.0024, bob: 0.012 },
            { fx: 0.12, fy: 0.82, r: 0.035, depth: 0.3, axis: [0.2, 1, 0.6], speed: 0.003, bob: 0.01 },
        ].map(sp => {
            const c = document.createElement('canvas'); field.appendChild(c);
            return { ...sp, c, ctx: c.getContext('2d'), ball: new Ball3D(), ph: Math.random() * 6 };
        });
        let last = 0;
        addLoop(t => {
            const dt = Math.min(50, t - (last || t)); last = t;
            const d = DPR(), m = Math.min(innerWidth, innerHeight);
            specs.forEach(sp => {
                const r = Math.round(m * sp.r);
                const size = r * 2 + 4;
                if (sp.c.width !== Math.round(size * d)) { sp.c.width = sp.c.height = Math.round(size * d); sp.c.style.width = sp.c.style.height = size + 'px'; }
                sp.ball.setRadius(r * d);
                sp.ball.spin(sp.axis[0], sp.axis[1], sp.axis[2], dt * sp.speed);
                const img = sp.ball.render();
                sp.ctx.clearRect(0, 0, sp.c.width, sp.c.height);
                sp.ctx.drawImage(img, (sp.c.width - img.width) / 2, (sp.c.height - img.height) / 2);
                const x = sp.fx * innerWidth + ptr.x * 30 * sp.depth - size / 2;
                const y = sp.fy * innerHeight + Math.sin(t * 0.0012 + sp.ph) * m * sp.bob + ptr.y * 20 * sp.depth - size / 2;
                sp.c.style.transform = `translate(${x}px, ${y}px)`;
                sp.c.style.opacity = String(0.35 + 0.6 * sp.depth);
                sp.c.style.filter = sp.depth < 0.6 ? `blur(${(1 - sp.depth) * 2.5}px)` : 'drop-shadow(0 24px 30px rgba(0,0,0,.45))';
            });
        });
    }

    // ================================================================ init
    function init() {
        progressBar();
        tiltCards();
        magnetic();

        // Home
        if ($('.hero .hero-logo')) {
            crest();
            particles($('.hero'), { density: 0.8 });
            depth($('.hero .hero-content'), { rx: 2.5, ry: 4, tx: 14, ty: 8 });
        }
        // About
        if ($('.about-hero')) {
            particles($('.about-hero'), { density: 0.9, rise: 0.8 });
            depth($('.about-hero .hero-inner'), { rx: 3, ry: 5, tx: 16, ty: 10 });
        }
        // Get involved — warm sunset embers
        if ($('.involved-hero')) {
            particles($('.involved-hero'), { rgb: '255,190,120', density: 1, rise: 1.2 });
            depth($('.involved-hero .hero-inner'), { rx: 2.5, ry: 4, tx: 14, ty: 8 });
        }
        // Contact
        if ($('.video-hero')) {
            particles($('.video-hero'), { density: 0.8 });
            depth($('.video-hero .hero-content'), { rx: 3, ry: 5, tx: 10, ty: 6, scroll: 0.1 });
        }
        // News
        if ($('.page-hero')) {
            particles($('.page-hero'), { density: 0.9 });
            depth($('.page-hero > div'), { rx: 3, ry: 4, tx: 10, ty: 6, scroll: 0.1 });
        }
        // Teams
        floodlights();
        // Gallery
        if ($('.gallery-hero')) {
            depth($('.gallery-hero .hero-text'), { rx: 2, ry: 3, tx: 10, ty: 6, scroll: 0.08 });
            galleryKick().catch(e => console.warn('[dfx] gallery kick skipped:', e));
        }
        // Player login / register — light dust over the existing background
        if ($('.login-container') || $('.register-container')) particles(document.body, { density: 0.6, fixed: true });
        // Staff login
        if ($('.staff-card')) { staffBalls(); particles(document.body, { density: 0.7, fixed: true }); }
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

    window.DynastyFX = { Ball3D };
})();
