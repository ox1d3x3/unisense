/*
 * UniSense for OPNsense — Lobby: Traffic Map
 * (c) Ox1d3x3 — BSD 2-Clause License
 *
 * Live traffic by country: a world map with arcs from this firewall to the
 * countries it is talking to, and the top 20 countries.
 *
 * Data: /api/unisense/trafficmap/flows (pf states, aggregated per country by
 * scripts/unisense/trafficmap.py; rates are the change since the last call).
 * Map:  worldmap.json — Natural Earth shapes, projected at build time.
 *
 * Rendering: the map is static SVG (only a country's level attribute changes);
 * arcs and moving particles are drawn on one canvas, only while there is
 * traffic, the tab is visible and motion is allowed.
 */
(function () {
    'use strict';

    var root = document.getElementById('utm');
    if (!root) {
        return;
    }

    var POLL_MS = 3000;
    var TOP = 20;
    var FLAG_DIR = '/ui/css/unisense/flags/';

    /* ------------------------------------------------------------ strings */
    var T = {};
    Object.keys(root.dataset).forEach(function (k) {
        if (k.indexOf('t') === 0 && k.length > 1) {
            T[k.charAt(1).toLowerCase() + k.slice(2)] = root.dataset[k];
        }
    });
    function t(key, fallback) {
        return T[key] || fallback;
    }

    /* ------------------------------------------------------------ helpers */
    function esc(v) {
        return String(v === undefined || v === null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function scaled(v, units, base) {
        v = +v || 0;
        var i = 0;
        while (v >= base && i < units.length - 1) {
            v /= base;
            i++;
        }
        return (i === 0 ? Math.round(v) : v.toFixed(v >= 100 ? 0 : 1)) + ' ' + units[i];
    }
    function fmtBits(b) {
        return scaled(b, ['b/s', 'Kb/s', 'Mb/s', 'Gb/s'], 1000);
    }
    function fmtBytes(b) {
        return scaled(b, ['B', 'KB', 'MB', 'GB', 'TB'], 1024);
    }

    var regionNames = null;
    try {
        regionNames = new Intl.DisplayNames([document.documentElement.lang || navigator.language || 'en', 'en'], { type: 'region' });
    } catch (e) { /* older browsers: show the code */ }
    function countryName(cc) {
        if (!cc || cc === '??') {
            return t('unknown', 'Unknown');
        }
        try {
            return (regionNames && regionNames.of(cc)) || cc;
        } catch (e) {
            return cc;
        }
    }

    function flag(cc, cls) {
        if (!/^[A-Z]{2}$/.test(cc || '')) {
            return '<span class="utm-flag utm-flag-none ' + (cls || '') + '"><i class="fa fa-question"></i></span>';
        }
        return '<img class="utm-flag ' + (cls || '') + '" alt="" loading="lazy" src="' + FLAG_DIR + cc + '.svg">';
    }

    function setHtml(el, html) {
        if (el && el.__utmHtml !== html) {
            el.innerHTML = html;
            el.__utmHtml = html;
        }
    }

    function fetchJson(url, timeoutMs) {
        var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
        var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, timeoutMs || 30000) : null;
        return fetch(url, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
            signal: ctrl ? ctrl.signal : undefined
        }).then(function (r) {
            return r.ok ? r.json().catch(function () { return null; }) : null;
        }).catch(function () {
            return null;
        }).then(function (v) {
            if (timer) {
                clearTimeout(timer);
            }
            return v;
        });
    }

    /* POST through jQuery so OPNsense's CSRF header is added */
    function post(url) {
        return new Promise(function (resolve) {
            if (!window.jQuery) {
                resolve(null);
                return;
            }
            window.jQuery.ajax({
                url: url, type: 'POST', dataType: 'json', data: '{}',
                contentType: 'application/json; charset=utf-8', global: false,
                success: function (d) { resolve(d); },
                error: function () { resolve(null); }
            });
        });
    }

    function reduceMotion() {
        try {
            return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (e) {
            return false;
        }
    }

    function ago(ts) {
        if (!ts) {
            return '';
        }
        var s = Math.max(0, Date.now() / 1000 - ts);
        if (s < 90) {
            return 'just now';
        }
        if (s < 5400) {
            return Math.round(s / 60) + ' min ago';
        }
        if (s < 129600) {
            return Math.round(s / 3600) + ' h ago';
        }
        return Math.round(s / 86400) + ' days ago';
    }

    /* -------------------------------------------------------------- state */
    var S = {
        world: null,          /* { w, h, byCc: { cc: { c, a } } } */
        data: null,
        live: true,
        failed: false,
        hover: null,
        selected: null,
        updating: false,
        timer: null,
        flows: {},            /* cc -> animated flow */
        colors: null
    };

    /* ------------------------------------------------------------ skeleton */
    function build() {
        root.innerHTML =
            '<div class="utm-head">' +
            '<span class="utm-status" data-utm="status"><span class="utm-status-dot"></span><span data-utm="status-text">' + esc(t('live', 'Live')) + '</span></span>' +
            '<span class="utm-db" data-utm="db"></span>' +
            '<span class="utm-head-actions">' +
            '<button type="button" class="btn btn-default btn-sm" data-utm="pause"><i class="fa fa-pause fa-fw"></i> <span>' + esc(t('pause', 'Pause')) + '</span></button>' +
            '</span></div>' +
            '<div class="utm-banner" data-utm="banner" hidden></div>' +
            '<div class="utm-stats" data-utm="stats"></div>' +
            '<div class="utm-card utm-mapcard">' +
            '<div class="utm-mapbox" data-utm="mapbox">' +
            '<svg class="utm-svg" data-utm="svg" role="img" aria-label="' + esc(t('countries', 'Countries')) + '"></svg>' +
            '<canvas class="utm-canvas" data-utm="canvas" aria-hidden="true"></canvas>' +
            '<canvas class="utm-canvas" data-utm="canvas-anim" aria-hidden="true"></canvas>' +
            '<div class="utm-tip" data-utm="tip" hidden></div>' +
            '</div>' +
            '<div class="utm-legend">' +
            '<span><i class="utm-key utm-key-dl"></i>' + esc(t('download', 'Download')) + '</span>' +
            '<span><i class="utm-key utm-key-ul"></i>' + esc(t('upload', 'Upload')) + '</span>' +
            '<span class="utm-legend-scale"><i class="utm-l1"></i><i class="utm-l2"></i><i class="utm-l3"></i><i class="utm-l4"></i><i class="utm-l5"></i></span>' +
            '</div></div>' +
            '<div class="utm-card">' +
            '<div class="utm-card-head"><b>' + esc(t('top', 'Top 20 countries')) + '</b><small data-utm="top-sub"></small></div>' +
            '<div class="utm-table-wrap" data-utm="table"></div></div>' +
            '<div class="utm-attrib">IP to Country Lite by <a href="https://db-ip.com" target="_blank" rel="noopener">DB-IP</a> (CC BY 4.0) · ' +
            'networks by <a href="https://iptoasn.com" target="_blank" rel="noopener">iptoasn.com</a> · ' +
            'map: <a href="https://www.naturalearthdata.com" target="_blank" rel="noopener">Natural Earth</a> · ' +
            'flags: <a href="https://github.com/madebybowtie/FlagKit" target="_blank" rel="noopener">FlagKit</a></div>';

        root.addEventListener('error', function (e) {
            if (e.target && e.target.classList && e.target.classList.contains('utm-flag')) {
                e.target.classList.add('is-missing');
            }
        }, true);
        q('pause').addEventListener('click', function () {
            setLive(!S.live);
        });
        root.addEventListener('click', onClick);
        var svg = q('svg');
        svg.addEventListener('pointermove', onHover);
        svg.addEventListener('pointerleave', function () {
            setHover(null);
        });
        q('table').addEventListener('pointerover', function (e) {
            var row = e.target.closest && e.target.closest('[data-cc]');
            setHover(row ? row.getAttribute('data-cc') : null, null);
        });
        q('table').addEventListener('pointerleave', function () {
            setHover(null);
        });
    }

    function q(name) {
        return root.querySelector('[data-utm="' + name + '"]');
    }

    /* ---------------------------------------------------------------- map */
    function loadWorld() {
        return fetchJson(root.getAttribute('data-worldmap') || '/ui/js/unisense/worldmap.json', 60000).then(function (w) {
            if (!w || !Array.isArray(w.countries)) {
                return;
            }
            var byCc = {};
            var paths = [];
            var dots = [];
            w.countries.forEach(function (c) {
                if (!/^[A-Z]{2}$/.test(c.cc)) {
                    return;
                }
                byCc[c.cc] = { c: c.c, a: c.a };
                paths.push('<path data-cc="' + c.cc + '" d="' + esc(c.d) + '"></path>');
                if (c.a < 12) {
                    /* tiny countries (Singapore, Hong Kong, ...) get a dot when they have traffic */
                    dots.push('<circle class="utm-dot" data-cc="' + c.cc + '" cx="' + c.c[0] + '" cy="' + c.c[1] + '" r="2.6"></circle>');
                }
            });
            S.world = { w: w.w || 1000, h: w.h || 500, byCc: byCc };
            q('mapbox').style.aspectRatio = S.world.w + ' / ' + S.world.h;
            var svg = q('svg');
            svg.setAttribute('viewBox', '0 0 ' + S.world.w + ' ' + S.world.h);
            svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
            svg.innerHTML = '<g class="utm-countries">' + paths.join('') + '</g><g class="utm-dots">' + dots.join('') + '</g>';
            S.pathEls = {};
            svg.querySelectorAll('[data-cc]').forEach(function (el) {
                var cc = el.getAttribute('data-cc');
                (S.pathEls[cc] = S.pathEls[cc] || []).push(el);
            });
            resizeCanvas();
        });
    }

    function paintCountries() {
        if (!S.world || !S.data) {
            return;
        }
        var list = S.data.countries || [];
        var max = 0;
        list.forEach(function (c) {
            max = Math.max(max, c.in_bps + c.out_bps);
        });
        var level = {};
        list.forEach(function (c) {
            var v = c.in_bps + c.out_bps;
            var l = 0;
            if (max > 0 && v > 0) {
                l = Math.max(1, Math.ceil(5 * Math.log(1 + v) / Math.log(1 + max)));
            } else if (c.conns > 0) {
                l = 1;
            }
            level[c.cc] = l;
        });
        Object.keys(S.pathEls || {}).forEach(function (cc) {
            var l = String(level[cc] || 0);
            S.pathEls[cc].forEach(function (el) {
                if (el.getAttribute('data-l') !== l) {
                    el.setAttribute('data-l', l);
                }
                el.classList.toggle('is-home', cc === S.data.home);
            });
        });
    }

    /* ------------------------------------------------------- canvas flows */
    /*
     * Two canvases over the map:
     *   base  — arcs and this firewall's marker; redrawn only when the data or
     *           the hovered country changes
     *   anim  — particles and destination pulses; redrawn every frame, in a
     *           few batched paths (one per colour and opacity), with all
     *           geometry precomputed per update
     * The loop runs only while there is traffic, the map is on screen, the tab
     * is visible, the page is live and motion is allowed.
     */
    var base = null, bctx = null, anim = null, actx = null;
    var view = { s: 1, ox: 0, oy: 0, dpr: 1, w: 0, h: 0 };
    var raf = 0;
    var lastFrame = 0;
    var onScreen = true;
    var copies = [];          /* drawable arc copies (wrapped flows have two) */

    function resizeCanvas() {
        var box = q('mapbox');
        base = q('canvas');
        anim = q('canvas-anim');
        if (!box || !base || !anim || !S.world) {
            return;
        }
        var w = box.clientWidth;
        var h = box.clientHeight;
        var dpr = Math.min(window.devicePixelRatio || 1, 2);
        [base, anim].forEach(function (c) {
            c.width = Math.round(w * dpr);
            c.height = Math.round(h * dpr);
        });
        bctx = base.getContext('2d');
        actx = anim.getContext('2d');
        var sc = Math.min(w / S.world.w, h / S.world.h);
        view = { s: sc, ox: (w - S.world.w * sc) / 2, oy: (h - S.world.h * sc) / 2, dpr: dpr, w: w, h: h };
        buildCopies();
        drawBase();
        drawAnim(0);
    }

    function readColors() {
        var probe = document.createElement('span');
        probe.style.display = 'none';
        root.appendChild(probe);
        var out = {};
        [['dl', '--utm-accent'], ['ul', '--utm-ok']].forEach(function (p) {
            probe.style.color = 'var(' + p[1] + ')';
            out[p[0]] = getComputedStyle(probe).color;
        });
        probe.remove();
        S.colors = out;
    }

    function speedFor(bps) {
        if (!(bps > 2000)) {
            return 0;
        }
        /* fraction of the arc per second: 10 kb/s ~0.14, 1 Mb/s ~0.32, 100 Mb/s ~0.5 */
        return Math.min(0.6, 0.05 + Math.log10(bps / 1000) * 0.09);
    }

    function particleCount(bps) {
        return bps > 2000 ? Math.max(1, Math.min(5, Math.round(Math.log10(bps / 1000)))) : 0;
    }

    /* rebuild the flow list after each poll, keeping particles of existing flows */
    function updateFlows() {
        var next = {};
        var home = S.data && S.world && S.world.byCc[S.data.home];
        if (home) {
            (S.data.countries || []).slice(0, TOP).forEach(function (c, i) {
                var dest = S.world.byCc[c.cc];
                if (!dest || c.cc === S.data.home || (c.in_bps + c.out_bps) <= 2000) {
                    return;
                }
                var f = S.flows[c.cc] || { cc: c.cc, dl: [], ul: [], phase: (i * 0.37) % 1 };
                f.from = home.c;
                f.to = dest.c;
                /* more than half the map away: take the short way round the
                   back (Perth -> Americas over the Pacific), drawn as two
                   copies, one leaving each edge */
                var dx = dest.c[0] - home.c[0];
                f.wrap = Math.abs(dx) > S.world.w / 2 ? (dx < 0 ? S.world.w : -S.world.w) : 0;
                f.sdl = speedFor(c.in_bps);
                f.sul = speedFor(c.out_bps);
                sizeParticles(f.dl, particleCount(c.in_bps));
                sizeParticles(f.ul, particleCount(c.out_bps));
                next[c.cc] = f;
            });
        }
        S.flows = next;
        buildCopies();
        drawBase();
        scheduleDraw();
    }

    function sizeParticles(list, n) {
        while (list.length < n) {
            list.push(list.length ? (list[list.length - 1] + 1 / (n + 1)) % 1 : Math.random());
        }
        list.length = n;
    }

    /* screen-space quadratic arcs, bent upwards, more for longer ones */
    function makeCopy(f, from, to, end) {
        var s = view.s, ox = view.ox, oy = view.oy;
        var x0 = ox + from[0] * s, y0 = oy + from[1] * s;
        var x2 = ox + to[0] * s, y2 = oy + to[1] * s;
        var dx = x2 - x0, dy = y2 - y0;
        var d = Math.sqrt(dx * dx + dy * dy) || 1;
        var nx = -dy / d, ny = dx / d;
        if (ny > 0) {
            nx = -nx;
            ny = -ny;
        }
        var k = Math.min(0.32, 40 * s / d + 0.12) * d;
        return { f: f, x0: x0, y0: y0, cx: (x0 + x2) / 2 + nx * k, cy: (y0 + y2) / 2 + ny * k, x2: x2, y2: y2, end: end };
    }

    function buildCopies() {
        copies = [];
        Object.keys(S.flows).forEach(function (cc) {
            var f = S.flows[cc];
            if (!f.wrap) {
                copies.push(makeCopy(f, f.from, f.to, true));
            } else {
                copies.push(makeCopy(f, f.from, [f.to[0] + f.wrap, f.to[1]], false));
                copies.push(makeCopy(f, [f.from[0] - f.wrap, f.from[1]], f.to, true));
            }
        });
    }

    function animating() {
        return S.live && copies.length > 0 && onScreen && !document.hidden && !reduceMotion();
    }

    function scheduleDraw() {
        if (raf || !actx) {
            return;
        }
        if (animating()) {
            lastFrame = 0;
            raf = requestAnimationFrame(frame);
        } else {
            drawAnim(0);
        }
    }

    function frame(ts) {
        raf = 0;
        var dt = lastFrame ? Math.min(0.1, (ts - lastFrame) / 1000) : 0;
        lastFrame = ts;
        drawAnim(dt);
        if (animating()) {
            raf = requestAnimationFrame(frame);
        }
    }

    function clipToMap(c) {
        c.beginPath();
        c.rect(view.ox, view.oy, S.world.w * view.s, S.world.h * view.s);
        c.clip();
    }

    function drawBase() {
        if (!bctx) {
            return;
        }
        if (!S.colors) {
            readColors();
        }
        var c = bctx;
        c.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
        c.clearRect(0, 0, view.w, view.h);
        if (!copies.length) {
            return;
        }
        c.save();
        clipToMap(c);
        c.lineWidth = 1.2;
        c.strokeStyle = S.colors.dl;
        [false, true].forEach(function (dimmed) {
            c.globalAlpha = dimmed ? 0.12 : 0.35;
            c.beginPath();
            copies.forEach(function (k) {
                if ((S.hover && S.hover !== k.f.cc) === dimmed) {
                    c.moveTo(k.x0, k.y0);
                    c.quadraticCurveTo(k.cx, k.cy, k.x2, k.y2);
                }
            });
            c.stroke();
        });
        c.restore();
        /* this firewall */
        var h = copies[0].f.from;
        var hx = view.ox + h[0] * view.s, hy = view.oy + h[1] * view.s;
        c.fillStyle = S.colors.dl;
        c.globalAlpha = 0.2;
        c.beginPath();
        c.arc(hx, hy, 9, 0, 6.2832);
        c.fill();
        c.globalAlpha = 1;
        c.beginPath();
        c.arc(hx, hy, 3.6, 0, 6.2832);
        c.fill();
    }

    function drawAnim(dt) {
        if (!actx) {
            return;
        }
        if (!S.colors) {
            readColors();
        }
        var c = actx;
        c.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
        c.clearRect(0, 0, view.w, view.h);
        if (!copies.length) {
            return;
        }
        Object.keys(S.flows).forEach(function (cc) {
            var f = S.flows[cc];
            var i;
            for (i = 0; i < f.dl.length; i++) {
                f.dl[i] = (f.dl[i] + f.sdl * dt) % 1;
            }
            for (i = 0; i < f.ul.length; i++) {
                f.ul[i] = (f.ul[i] + f.sul * dt) % 1;
            }
        });
        c.save();
        clipToMap(c);
        /* particles, batched: [colour, lane, downloads travel towards us] */
        [[S.colors.dl, 'dl', true], [S.colors.ul, 'ul', false]].forEach(function (lane) {
            c.fillStyle = lane[0];
            [[4.2, 0.22, 0.08], [1.9, 1, 0.3]].forEach(function (ring) {
                [false, true].forEach(function (dimmed) {
                    c.globalAlpha = dimmed ? ring[2] : ring[1];
                    c.beginPath();
                    copies.forEach(function (k) {
                        if ((S.hover && S.hover !== k.f.cc) !== dimmed) {
                            return;
                        }
                        var list = k.f[lane[1]];
                        for (var i = 0; i < list.length; i++) {
                            var u = lane[2] ? 1 - list[i] : list[i];
                            var a = (1 - u) * (1 - u), b = 2 * (1 - u) * u, d = u * u;
                            var px = a * k.x0 + b * k.cx + d * k.x2;
                            var py = a * k.y0 + b * k.cy + d * k.y2;
                            c.moveTo(px + ring[0], py);
                            c.arc(px, py, ring[0], 0, 6.2832);
                        }
                    });
                    c.fill();
                });
            });
        });
        /* destination pulses */
        var now = performance.now() / 1000;
        c.fillStyle = S.colors.dl;
        c.strokeStyle = S.colors.dl;
        c.lineWidth = 1.2;
        copies.forEach(function (k) {
            if (!k.end) {
                return;
            }
            var dim = S.hover && S.hover !== k.f.cc;
            var ph = ((now * 0.6) + k.f.phase) % 1;
            c.globalAlpha = (1 - ph) * (dim ? 0.2 : 0.6);
            c.beginPath();
            c.arc(k.x2, k.y2, 2 + ph * 9, 0, 6.2832);
            c.stroke();
            c.globalAlpha = dim ? 0.35 : 0.95;
            c.beginPath();
            c.arc(k.x2, k.y2, 2.2, 0, 6.2832);
            c.fill();
        });
        c.restore();
    }

    /* ------------------------------------------------------------ tooltip */
    function onHover(e) {
        var el = e.target && e.target.closest ? e.target.closest('[data-cc]') : null;
        setHover(el ? el.getAttribute('data-cc') : null, e);
    }

    function countryData(cc) {
        var list = (S.data && S.data.countries) || [];
        for (var i = 0; i < list.length; i++) {
            if (list[i].cc === cc) {
                return list[i];
            }
        }
        return null;
    }

    function setHover(cc, e) {
        var tip = q('tip');
        if (cc !== S.hover) {
            if (S.hover && S.pathEls && S.pathEls[S.hover]) {
                S.pathEls[S.hover].forEach(function (el) { el.classList.remove('is-hover'); });
            }
            S.hover = cc;
            if (cc && S.pathEls && S.pathEls[cc]) {
                S.pathEls[cc].forEach(function (el) { el.classList.add('is-hover'); });
            }
            root.querySelectorAll('.utm-row.is-hover').forEach(function (r) { r.classList.remove('is-hover'); });
            if (cc) {
                var row = root.querySelector('.utm-row[data-cc="' + cc + '"]');
                if (row) {
                    row.classList.add('is-hover');
                }
            }
            drawBase();
            if (!raf) {
                drawAnim(0);
            }
        }
        if (!tip) {
            return;
        }
        if (!cc || !e) {
            tip.hidden = true;
            return;
        }
        var c = countryData(cc);
        setHtml(tip, '<div class="utm-tip-head">' + flag(cc) + '<b>' + esc(countryName(cc)) + '</b>' +
            (S.data && cc === S.data.home ? '<small>' + esc(t('home', 'This firewall')) + '</small>' : '') + '</div>' +
            (c ? '<div class="utm-tip-rates"><span class="utm-dl">&darr; ' + esc(fmtBits(c.in_bps)) + '</span>' +
                '<span class="utm-ul">&uarr; ' + esc(fmtBits(c.out_bps)) + '</span></div>' +
                '<div class="utm-tip-meta">' + c.conns + ' ' + esc(t('connections', 'Connections').toLowerCase()) + ' · ' +
                esc(fmtBytes(c.bytes_in + c.bytes_out)) + '</div>' +
                (c.asns && c.asns.length ? '<div class="utm-tip-nets">' + c.asns.slice(0, 3).map(function (a) {
                    return a.asn ? '<span>' + esc(a.name || ('AS' + a.asn)) + '</span>' : '';
                }).join('') + '</div>' : '')
                : '<div class="utm-tip-meta">' + esc(t('idle', 'No traffic')) + '</div>'));
        tip.hidden = false;
        var box = q('mapbox').getBoundingClientRect();
        var x = e.clientX - box.left + 14;
        var y = e.clientY - box.top + 14;
        var tw = tip.offsetWidth, th = tip.offsetHeight;
        if (x + tw > box.width - 6) {
            x = e.clientX - box.left - tw - 14;
        }
        if (y + th > box.height - 6) {
            y = e.clientY - box.top - th - 14;
        }
        tip.style.transform = 'translate3d(' + Math.max(6, x) + 'px,' + Math.max(6, y) + 'px,0)';
    }

    /* ------------------------------------------------------- stats + table */
    function renderStats() {
        var d = S.data;
        var tot = (d && d.totals) || {};
        var list = (d && d.countries) || [];
        var active = list.filter(function (c) { return c.cc !== '??' && c.conns > 0; }).length;
        var moving = list.filter(function (c) { return c.cc !== '??' && (c.in_bps + c.out_bps) > 2000; }).length;
        setHtml(q('stats'),
            stat('fa-arrow-down', t('download', 'Download'), fmtBits(tot.in_bps), fmtBytes(tot.bytes_in), 'utm-dl') +
            stat('fa-arrow-up', t('upload', 'Upload'), fmtBits(tot.out_bps), fmtBytes(tot.bytes_out), 'utm-ul') +
            stat('fa-earth-americas', t('countries', 'Countries'), String(active), moving + ' ' + t('active', 'active now'), '') +
            stat('fa-arrow-right-arrow-left', t('connections', 'Connections'), String(tot.conns || 0), '', ''));
    }

    function stat(icon, title, value, sub, cls) {
        return '<div class="utm-stat"><span class="utm-stat-icon ' + cls + '"><i class="fa ' + icon + '"></i></span>' +
            '<span class="utm-stat-body"><span class="utm-stat-title">' + esc(title) + '</span>' +
            '<span class="utm-stat-value">' + esc(value) + '</span>' +
            '<span class="utm-stat-sub">' + esc(sub) + '</span></span></div>';
    }

    function renderTable() {
        var d = S.data;
        var list = ((d && d.countries) || []).slice(0, TOP);
        var tot = (d && d.totals) || {};
        var totalRate = (tot.in_bps || 0) + (tot.out_bps || 0);
        var totalBytes = (tot.bytes_in || 0) + (tot.bytes_out || 0);
        setHtml(q('top-sub'), list.length ? esc(fmtBits(tot.in_bps)) + ' &darr; · ' + esc(fmtBits(tot.out_bps)) + ' &uarr;' : '');
        if (!list.length) {
            setHtml(q('table'), '<div class="utm-empty">' + esc(t('idle', 'No traffic to other countries right now.')) + '</div>');
            return;
        }
        var html = '<table class="utm-table"><thead><tr>' +
            '<th class="utm-c-rank">#</th><th>' + esc(t('country', 'Country')) + '</th>' +
            '<th class="utm-c-num">&darr; ' + esc(t('download', 'Download')) + '</th>' +
            '<th class="utm-c-num">&uarr; ' + esc(t('upload', 'Upload')) + '</th>' +
            '<th class="utm-c-share"></th>' +
            '<th class="utm-c-num">' + esc(t('connections', 'Connections')) + '</th>' +
            '<th class="utm-c-num utm-c-hide">' + esc(t('transferred', 'Transferred')) + '</th>' +
            '<th class="utm-c-hide">' + esc(t('networks', 'Networks')) + '</th>' +
            '</tr></thead><tbody>';
        list.forEach(function (c, i) {
            var rate = c.in_bps + c.out_bps;
            var share = totalRate > 0 ? rate / totalRate : (totalBytes > 0 ? (c.bytes_in + c.bytes_out) / totalBytes : 0);
            var open = S.selected === c.cc;
            html += '<tr class="utm-row' + (open ? ' is-open' : '') + (S.hover === c.cc ? ' is-hover' : '') + '" data-cc="' + esc(c.cc) + '" tabindex="0">' +
                '<td class="utm-c-rank">' + (i + 1) + '</td>' +
                '<td><span class="utm-c-country">' + flag(c.cc) + '<span>' + esc(countryName(c.cc)) + '</span>' +
                (d.home === c.cc ? ' <small class="utm-home-tag">' + esc(t('home', 'This firewall')) + '</small>' : '') + '</span></td>' +
                '<td class="utm-c-num utm-dl">' + esc(fmtBits(c.in_bps)) + '</td>' +
                '<td class="utm-c-num utm-ul">' + esc(fmtBits(c.out_bps)) + '</td>' +
                '<td class="utm-c-share"><span class="utm-bar"><i style="width:' + Math.max(1, Math.round(share * 100)) + '%"></i></span>' +
                '<small>' + Math.round(share * 100) + '%</small></td>' +
                '<td class="utm-c-num">' + c.conns + '</td>' +
                '<td class="utm-c-num utm-c-hide">' + esc(fmtBytes(c.bytes_in + c.bytes_out)) + '</td>' +
                '<td class="utm-c-hide utm-c-nets">' + (c.asns || []).slice(0, 2).map(function (a) {
                    return a.asn ? '<span>' + esc(a.name || ('AS' + a.asn)) + '</span>' : '';
                }).join('') + '</td></tr>';
            if (open) {
                html += '<tr class="utm-detail"><td></td><td colspan="7"><div class="utm-nets">' +
                    (c.asns || []).filter(function (a) { return a.asn; }).map(function (a) {
                        return '<div class="utm-net"><b>' + esc(a.name || 'Unknown network') + '</b>' +
                            '<small>AS' + esc(a.asn) + ' · ' + a.conns + ' ' + esc(t('connections', 'Connections').toLowerCase()) + ' · ' + esc(fmtBytes(a.bytes)) + '</small>' +
                            '<span class="utm-dl">&darr; ' + esc(fmtBits(a.in_bps)) + '</span><span class="utm-ul">&uarr; ' + esc(fmtBits(a.out_bps)) + '</span></div>';
                    }).join('') + '</div></td></tr>';
            }
        });
        html += '</tbody></table>';
        setHtml(q('table'), html);
    }

    function onClick(e) {
        var btn = e.target.closest && e.target.closest('[data-utm-action]');
        if (btn) {
            if (btn.getAttribute('data-utm-action') === 'update') {
                startUpdate();
            }
            return;
        }
        var el = e.target.closest && e.target.closest('[data-cc]');
        if (el && root.contains(el)) {
            var cc = el.getAttribute('data-cc');
            S.selected = S.selected === cc ? null : cc;
            renderTable();
            var row = root.querySelector('.utm-row[data-cc="' + cc + '"]');
            if (row && el.closest('svg')) {
                row.scrollIntoView({ block: 'nearest', behavior: reduceMotion() ? 'auto' : 'smooth' });
            }
        }
    }

    /* ----------------------------------------------------- db + banners */
    function renderDb() {
        var db = (S.data && S.data.db) || {};
        var parts = [];
        if (db.geo && db.geo.month) {
            parts.push('DB-IP ' + db.geo.month);
        }
        if (db.asn && db.asn.updated) {
            parts.push(t('networks', 'Networks').toLowerCase() + ' ' + ago(db.asn.updated));
        }
        var text = parts.length ? esc(t('db', 'IP database')) + ': ' + esc(parts.join(' · ')) + ' · ' + esc(t('schedule', 'updates daily at 01:00')) : '';
        setHtml(q('db'), text + (db.ready ? ' <button type="button" class="btn btn-link btn-xs" data-utm-action="update"' + (S.updating ? ' disabled' : '') + '>' +
            esc(t('updateNow', 'Update now')) + '</button>' : ''));

        var banner = q('banner');
        if (S.failed) {
            banner.className = 'utm-banner is-error';
            setHtml(banner, '<i class="fa fa-triangle-exclamation"></i> ' + esc(t('error', 'Could not read live traffic.')));
            banner.hidden = false;
        } else if (S.updating && !db.ready) {
            banner.className = 'utm-banner';
            setHtml(banner, '<i class="fa fa-spinner fa-pulse"></i> ' + esc(t('downloading', 'Downloading the IP-to-country databases…')));
            banner.hidden = false;
        } else if (S.data && !db.ready) {
            banner.className = 'utm-banner is-warn';
            setHtml(banner, '<i class="fa fa-database"></i> ' + esc(t('noDb', 'The IP-to-country database has not been downloaded yet.')) +
                ' <button type="button" class="btn btn-primary btn-xs" data-utm-action="update">' + esc(t('downloadNow', 'Download now')) + '</button>');
            banner.hidden = false;
        } else {
            banner.hidden = true;
        }
    }

    function startUpdate() {
        S.updating = true;
        renderDb();
        post('/api/unisense/trafficmap/update').then(function () {
            var started = Date.now();
            var check = function () {
                fetchJson('/api/unisense/trafficmap/status').then(function (st) {
                    var db = st && st.db;
                    var done = db && db.checked && db.checked * 1000 >= started - 5000;
                    if (done || Date.now() - started > 300000) {
                        S.updating = false;
                        if (S.data && db) {
                            S.data.db = db;
                        }
                        renderDb();
                    } else {
                        setTimeout(check, 4000);
                    }
                });
            };
            setTimeout(check, 4000);
        });
    }

    /* --------------------------------------------------------------- poll */
    function setLive(on) {
        S.live = on;
        var btn = q('pause');
        btn.innerHTML = on ? '<i class="fa fa-pause fa-fw"></i> <span>' + esc(t('pause', 'Pause')) + '</span>' :
            '<i class="fa fa-play fa-fw"></i> <span>' + esc(t('resume', 'Resume')) + '</span>';
        q('status').classList.toggle('is-paused', !on);
        setHtml(q('status-text'), esc(on ? t('live', 'Live') : t('paused', 'Paused')));
        clearTimeout(S.timer);
        if (on) {
            poll();
        } else {
            if (raf) {
                cancelAnimationFrame(raf);
                raf = 0;
            }
            drawAnim(0);
        }
    }

    function poll() {
        clearTimeout(S.timer);
        if (!S.live || document.hidden) {
            return;
        }
        fetchJson('/api/unisense/trafficmap/flows').then(function (d) {
            if (d && d.ok) {
                S.failed = false;
                S.data = d;
                q('status').classList.remove('is-tick');
                void q('status').offsetWidth;
                q('status').classList.add('is-tick');
            } else {
                S.failed = true;
            }
            renderAll();
            if (S.live) {
                /* the first call only sets the baseline: ask again soon */
                S.timer = setTimeout(poll, d && d.ok && !d.interval ? 1200 : POLL_MS);
            }
        });
    }

    function renderAll() {
        renderStats();
        renderTable();
        renderDb();
        paintCountries();
        updateFlows();
    }

    /* -------------------------------------------------------------- start */
    build();
    renderStats();
    loadWorld().then(function () {
        paintCountries();
        updateFlows();
    });
    poll();

    if (window.ResizeObserver) {
        new ResizeObserver(function () {
            resizeCanvas();
        }).observe(q('mapbox'));
    } else {
        window.addEventListener('resize', resizeCanvas);
    }
    /* stop animating while the map is scrolled out of view */
    if (window.IntersectionObserver) {
        new IntersectionObserver(function (entries) {
            onScreen = entries[entries.length - 1].isIntersecting;
            scheduleDraw();
        }).observe(q('mapbox'));
    }
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden && S.live) {
            poll();
            scheduleDraw();
        }
    });
    /* colours follow the UniSense light/dark mode and accent */
    if (window.MutationObserver) {
        new MutationObserver(function () {
            S.colors = null;
            drawBase();
            if (!raf) {
                drawAnim(0);
            }
        }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-us-theme', 'style', 'class'] });
    }

    window.UniSenseTrafficMap = { refresh: poll, state: S };
}());
