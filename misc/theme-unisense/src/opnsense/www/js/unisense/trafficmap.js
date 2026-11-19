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
        hist: [],             /* recent [download, upload] totals for the sparklines */
        view: 'top',          /* table: 'top' (top 20) or 'all' (every country seen) */
        sort: { key: 'rate', dir: -1 },
        hsort: { key: 'htot', dir: -1 },
        query: '',
        history: null,        /* every country seen (trafficmap.py history) */
        histAt: 0,
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
            '<div class="utm-card-head">' +
            '<div class="utm-tabs" role="tablist">' +
            '<button type="button" role="tab" class="utm-tab is-active" aria-selected="true" data-utm-view="top">' + esc(t('top', 'Top 20 countries')) + '</button>' +
            '<button type="button" role="tab" class="utm-tab" aria-selected="false" data-utm-view="all">' + esc(t('allCountries', 'All countries')) +
            ' <span class="utm-tab-count" data-utm="all-count"></span></button></div>' +
            '<small data-utm="top-sub"></small>' +
            '<label class="utm-search" hidden data-utm="search-wrap"><i class="fa fa-search" aria-hidden="true"></i>' +
            '<input type="search" data-utm="search" placeholder="' + esc(t('search', 'Search countries')) + '" aria-label="' + esc(t('search', 'Search countries')) + '"></label>' +
            '</div>' +
            '<div class="utm-table-wrap" data-utm="table"></div>' +
            '<div class="utm-table-wrap" data-utm="history" hidden></div>' +
            '<div class="utm-hist-note" data-utm="hist-note" hidden></div></div>' +
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
        root.addEventListener('keydown', function (e) {
            var th = e.target.closest && e.target.closest('[data-utm-sort]');
            if (th && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault();
                onClick({ target: th });
            }
        });
        var search = q('search');
        var searchTimer = 0;
        search.addEventListener('input', function () {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(function () {
                S.query = search.value.trim();
                renderTable();
            }, 120);
        });
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
     *   base  — this firewall's marker; redrawn only on resize or theme change
     *   anim  — arcs, "strings" of light travelling along them and the
     *           destination pulses; redrawn every frame from precomputed
     *           geometry, batched into a handful of Path2D strokes keyed by
     *           width and opacity
     * Everything eases: speeds glide to the new rate after each poll, arcs fade
     * in and out, and streaks are added at the source and retired only when
     * they reach the end, so a refresh never makes anything jump.
     * The loop runs only while there is traffic, the map is on screen, the tab
     * is visible, the page is live and motion is allowed.
     */
    var base = null, bctx = null, anim = null, actx = null;
    var view = { s: 1, ox: 0, oy: 0, dpr: 1, w: 0, h: 0 };
    var raf = 0;
    var lastFrame = 0;
    var onScreen = true;
    var copies = [];          /* drawable arc copies (wrapped flows have two) */
    var TAU = 6.2832;
    var pace = { ema: 16.7, half: false, until: 0, skip: false, lastTs: 0, scrolling: 0 };
    var SEG = 96;             /* samples per arc */

    function resizeCanvas() {
        var box = q('mapbox');
        base = q('canvas');
        anim = q('canvas-anim');
        if (!box || !base || !anim || !S.world) {
            return;
        }
        var w = box.clientWidth;
        var h = box.clientHeight;
        if (!w || !h) {
            return;
        }
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
        [['dl', '--utm-accent'], ['ul', '--utm-ok'], ['home', '--utm-ok']].forEach(function (p) {
            probe.style.color = 'var(' + p[1] + ')';
            out[p[0]] = getComputedStyle(probe).color;
        });
        probe.remove();
        S.colors = out;
    }

    /* travel speed in px/s: 10 kb/s ~75, 1 Mb/s ~165, 100 Mb/s ~255 */
    function pxSpeed(bps) {
        return bps > 2000 ? Math.min(300, 30 + Math.log10(bps / 1000) * 45) : 0;
    }

    function streakCount(bps) {
        return bps > 2000 ? Math.max(1, Math.min(6, Math.round(Math.log10(bps / 1000) * 1.2))) : 0;
    }

    /* rebuild the flow list after each poll; existing flows keep their streaks */
    function updateFlows() {
        var home = S.data && S.world && S.world.byCc[S.data.home];
        Object.keys(S.flows).forEach(function (cc) {
            S.flows[cc].want = false;
        });
        if (home) {
            var top = (S.data.countries || []).slice(0, TOP);
            var max = 0;
            top.forEach(function (c) {
                max = Math.max(max, c.in_bps + c.out_bps);
            });
            top.forEach(function (c, i) {
                var dest = S.world.byCc[c.cc];
                var bps = c.in_bps + c.out_bps;
                if (!dest || c.cc === S.data.home || bps <= 2000) {
                    return;
                }
                var f = S.flows[c.cc];
                if (!f) {
                    f = S.flows[c.cc] = { cc: c.cc, dl: [], ul: [], vis: 0, sdl: 0, sul: 0, phase: (i * 0.37) % 1, fresh: true };
                }
                f.want = true;
                f.from = home.c;
                f.to = dest.c;
                /* more than half the map away: take the short way round the
                   back (Perth -> Americas over the Pacific), drawn as two
                   copies, one leaving each edge */
                var dx = dest.c[0] - home.c[0];
                f.wrap = Math.abs(dx) > S.world.w / 2 ? (dx < 0 ? S.world.w : -S.world.w) : 0;
                f.tdl = pxSpeed(c.in_bps);
                f.tul = pxSpeed(c.out_bps);
                f.ndl = streakCount(c.in_bps);
                f.nul = streakCount(c.out_bps);
                /* busier countries get a slightly bolder arc */
                f.lw = 0.8 + 1.4 * (max > 0 ? Math.log(1 + bps) / Math.log(1 + max) : 0);
            });
        }
        buildCopies();
        drawBase();
        Object.keys(S.flows).forEach(function (cc) {
            var f = S.flows[cc];
            if (f.fresh && f.len) {
                /* a new flow starts already populated, evenly spaced */
                f.fresh = false;
                f.sdl = f.tdl;
                f.sul = f.tul;
                fill(f.dl, f.ndl, f.phase);
                fill(f.ul, f.nul, (f.phase + 0.5) % 1);
            }
        });
        scheduleDraw();
    }

    function fill(list, n, start) {
        list.length = 0;
        for (var i = 0; i < n; i++) {
            list.push((start + i / n) % 1);
        }
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
        var cx = (x0 + x2) / 2 + nx * k, cy = (y0 + y2) / 2 + ny * k;
        /* arc length: mean of the chord and the control polygon (close enough) */
        var poly = Math.hypot(cx - x0, cy - y0) + Math.hypot(x2 - cx, y2 - cy);
        /* the curve sampled once (LUT), so a frame only interpolates */
        var lut = new Float32Array((SEG + 1) * 2);
        for (var i = 0; i <= SEG; i++) {
            var u = i / SEG, a = (1 - u) * (1 - u), b = 2 * (1 - u) * u, e = u * u;
            lut[i * 2] = a * x0 + b * cx + e * x2;
            lut[i * 2 + 1] = a * y0 + b * cy + e * y2;
        }
        return { f: f, x0: x0, y0: y0, cx: cx, cy: cy, x2: x2, y2: y2, end: end, len: (d + poly) / 2, lut: lut };
    }

    function buildCopies() {
        copies = [];
        if (!S.world || !view.w) {
            return;
        }
        Object.keys(S.flows).forEach(function (cc) {
            var f = S.flows[cc];
            if (!f.from) {
                return;
            }
            var a, b;
            if (!f.wrap) {
                a = makeCopy(f, f.from, f.to, true);
                copies.push(a);
                f.len = a.len;
            } else {
                a = makeCopy(f, f.from, [f.to[0] + f.wrap, f.to[1]], false);
                b = makeCopy(f, [f.from[0] - f.wrap, f.from[1]], f.to, true);
                copies.push(a, b);
                f.len = Math.max(a.len, b.len);
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
            pace.lastTs = 0;
            raf = requestAnimationFrame(frame);
        } else {
            drawAnim(0);
        }
    }

    /*
     * Frame pacing: draw every frame while the browser keeps up; if frames
     * start to drop (slow machine, software rendering, a busy tab), draw every
     * other frame for a while — a steady 30 fps looks smoother than an uneven
     * 40 — and try full rate again later.
     */
    function frame(ts) {
        raf = 0;
        if (pace.lastTs) {
            pace.ema += (Math.min(100, ts - pace.lastTs) - pace.ema) * 0.08;
        }
        pace.lastTs = ts;
        if (!pace.half && pace.ema > 20) {
            pace.half = true;
            pace.until = ts + 8000;
        } else if (pace.half && ts > pace.until && pace.ema < 18) {
            pace.half = false;
        }
        /* on a struggling machine, let scrolling have the frames */
        pace.skip = pace.half && (!pace.skip || ts < pace.scrolling);
        if (!pace.skip) {
            var dt = lastFrame ? Math.min(0.1, (ts - lastFrame) / 1000) : 0;
            lastFrame = ts;
            drawAnim(dt);
        }
        if (animating()) {
            raf = requestAnimationFrame(frame);
        } else {
            pace.lastTs = 0;
        }
    }

    function clipToMap(c) {
        c.beginPath();
        c.rect(view.ox, view.oy, S.world.w * view.s, S.world.h * view.s);
        c.clip();
    }

    function homeXY() {
        var h = S.data && S.world && S.world.byCc[S.data.home];
        return h ? [view.ox + h.c[0] * view.s, view.oy + h.c[1] * view.s] : null;
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
        /* the arcs: few strokes, grouped by width and opacity */
        if (copies.length) {
            var groups = {};
            copies.forEach(function (k) {
                var f = k.f;
                if (f.vis <= 0) {
                    return;
                }
                var dim = S.hover && S.hover !== f.cc ? 0.3 : 1;
                var lw = Math.round((S.hover === f.cc ? f.lw + 0.8 : f.lw) * 5) / 5;
                var key = lw + '|' + Math.round(0.32 * f.vis * dim * 40) / 40;
                var g = groups[key] || (groups[key] = []);
                g.push(k);
            });
            c.save();
            clipToMap(c);
            c.strokeStyle = S.colors.dl;
            c.lineCap = 'round';
            Object.keys(groups).forEach(function (key) {
                var parts = key.split('|');
                c.lineWidth = +parts[0];
                c.globalAlpha = +parts[1];
                c.beginPath();
                groups[key].forEach(function (k) {
                    c.moveTo(k.x0, k.y0);
                    c.quadraticCurveTo(k.cx, k.cy, k.x2, k.y2);
                });
                c.stroke();
            });
            c.restore();
        }
        var h = homeXY();
        if (!h) {
            return;
        }
        /* this firewall */
        c.globalAlpha = 0.22;
        c.fillStyle = S.colors.home;
        c.beginPath();
        c.arc(h[0], h[1], 8, 0, TAU);
        c.fill();
        c.globalAlpha = 1;
        c.beginPath();
        c.arc(h[0], h[1], 3.6, 0, TAU);
        c.fill();
    }

    /* advance one lane: streaks are spawned at the source, spaced evenly,
       and removed only when they arrive, so count changes never pop */
    function stepLane(list, n, du) {
        var i;
        var minP = 1;
        for (i = list.length - 1; i >= 0; i--) {
            var p = list[i] + du;
            if (p >= 1) {
                if (list.length > n) {
                    list.splice(i, 1);
                    continue;
                }
                p -= 1;
            }
            list[i] = p;
            minP = Math.min(minP, p);
        }
        if (list.length < n && (!list.length || minP >= 1 / n)) {
            list.push(0);
        }
    }

    /* a point on a sampled arc at u (0..1) */
    function at(lut, u, out) {
        var x = u * SEG;
        var i = x | 0;
        if (i >= SEG) {
            i = SEG - 1;
        }
        var r = x - i;
        out[0] = lut[i * 2] + (lut[i * 2 + 2] - lut[i * 2]) * r;
        out[1] = lut[i * 2 + 1] + (lut[i * 2 + 3] - lut[i * 2 + 1]) * r;
    }

    var TAIL = [0.12, 0.38, 0.85];      /* opacity per tail piece, tail -> head */
    var NT = TAIL.length;
    var pa = [0, 0], pb = [0, 0];
    var MAIN = null;

    /*
     * One frame. All streaks of fully visible, undimmed flows go into a fixed
     * set of paths — one per colour and tail piece, plus two for the heads —
     * so a frame is ~14 strokes and fills however much is moving. Flows that
     * are fading or dimmed by a hover get their own small batch.
     */
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

        /* ease speeds and visibility, advance streaks, drop faded flows */
        var ease = 1 - Math.exp(-dt * 2.5);
        var changed = false;
        var gone = false;
        var keys = Object.keys(S.flows);
        for (var fi = 0; fi < keys.length; fi++) {
            var f = S.flows[keys[fi]];
            var target = f.want ? 1 : 0;
            var v0 = f.vis;
            if (!dt) {
                f.vis = reduceMotion() || !S.live ? target : f.vis;
            } else {
                f.vis += (target - f.vis) * Math.min(1, dt * 3.2);
                if (Math.abs(f.vis - target) < 0.01) {
                    f.vis = target;
                }
            }
            if (f.vis !== v0) {
                changed = true;
            }
            if (!f.want && f.vis === 0) {
                delete S.flows[keys[fi]];
                gone = true;
                continue;
            }
            if (dt && f.len) {
                f.sdl += ((f.want ? f.tdl : 0) - f.sdl) * ease;
                f.sul += ((f.want ? f.tul : 0) - f.sul) * ease;
                stepLane(f.dl, f.want ? f.ndl : 0, f.sdl * dt / f.len);
                stepLane(f.ul, f.want ? f.nul : 0, f.sul * dt / f.len);
            }
        }
        if (gone) {
            buildCopies();
        }
        if (changed || gone) {
            drawBase();
        }
        if (!copies.length) {
            return;
        }

        if (!MAIN) {
            MAIN = { dl: newBatch(), ul: newBatch() };
        }
        c.save();
        clipToMap(c);
        var main = { dl: resetBatch(MAIN.dl), ul: resetBatch(MAIN.ul) };
        var extra = [];
        for (var ci = 0; ci < copies.length; ci++) {
            var k = copies[ci];
            var fl = k.f;
            if (fl.vis <= 0) {
                continue;
            }
            var mul = fl.vis * (S.hover && S.hover !== fl.cc ? 0.25 : 1);
            var bt = main;
            if (mul < 0.999) {
                bt = { dl: newBatch(), ul: newBatch(), mul: mul };
                extra.push(bt);
            }
            var tail = Math.min(0.4, 64 / (fl.len || 64));
            addStreaks(bt.dl, k.lut, fl.dl, tail, true);
            addStreaks(bt.ul, k.lut, fl.ul, tail, false);
        }
        paintBatch(c, main.dl, S.colors.dl, 1);
        paintBatch(c, main.ul, S.colors.ul, 1);
        extra.forEach(function (x) {
            paintBatch(c, x.dl, S.colors.dl, x.mul);
            paintBatch(c, x.ul, S.colors.ul, x.mul);
        });

        /* destination pulses, batched into a few opacity steps */
        var now = performance.now() / 1000;
        var rings = [];
        var dots = new Path2D();
        var dimDots = new Path2D();
        var anyDim = false;
        for (ci = 0; ci < copies.length; ci++) {
            k = copies[ci];
            if (!k.end || k.f.vis <= 0) {
                continue;
            }
            var dim = (S.hover && S.hover !== k.f.cc ? 0.3 : 1) * k.f.vis;
            var ph = ((now * 0.6) + k.f.phase) % 1;
            var step = Math.min(7, Math.floor((1 - ph) * dim * 8));
            if (step > 0) {
                var rp = rings[step] || (rings[step] = new Path2D());
                var rr = 2 + ph * 9;
                rp.moveTo(k.x2 + rr, k.y2);
                rp.arc(k.x2, k.y2, rr, 0, TAU);
            }
            var dp = dim > 0.99 ? dots : dimDots;
            anyDim = anyDim || dp === dimDots;
            dp.moveTo(k.x2 + 2.2, k.y2);
            dp.arc(k.x2, k.y2, 2.2, 0, TAU);
        }
        c.fillStyle = S.colors.dl;
        c.strokeStyle = S.colors.dl;
        c.lineWidth = 1.2;
        for (var ri = 1; ri < rings.length; ri++) {
            if (rings[ri]) {
                c.globalAlpha = ri / 8 * 0.6;
                c.stroke(rings[ri]);
            }
        }
        c.globalAlpha = 0.95;
        c.fill(dots);
        if (anyDim) {
            c.globalAlpha = 0.3;
            c.fill(dimDots);
        }
        /* this firewall: a slow "live" ring */
        var h = homeXY();
        if (h && S.live) {
            var hp = (now * 0.45) % 1;
            c.strokeStyle = S.colors.home;
            c.globalAlpha = (1 - hp) * 0.55;
            c.beginPath();
            c.arc(h[0], h[1], 4 + hp * 16, 0, TAU);
            c.stroke();
        }
        c.restore();
    }

    /* reusable coordinate buffers: no allocations per frame */
    function newBatch() {
        var b = { pieces: [], heads: [], n: 0 };
        for (var i = 0; i < NT; i++) {
            b.pieces.push([]);
        }
        return b;
    }

    function resetBatch(b) {
        for (var i = 0; i < NT; i++) {
            b.pieces[i].length = 0;
        }
        b.heads.length = 0;
        b.n = 0;
        return b;
    }

    /* list: progress of each streak; toUs: downloads travel towards home (u = 1 - p) */
    function addStreaks(b, lut, list, tail, toUs) {
        var step = tail / NT;
        for (var i = 0; i < list.length; i++) {
            var p = list[i];
            for (var s = 0; s < NT; s++) {
                var p1 = p - step * (NT - s - 1);
                if (p1 <= 0) {
                    continue;
                }
                var p0 = p1 - step;
                if (p0 < 0) {
                    p0 = 0;
                }
                at(lut, toUs ? 1 - p0 : p0, pa);
                at(lut, toUs ? 1 - p1 : p1, pb);
                b.pieces[s].push(pa[0], pa[1], pb[0], pb[1]);
            }
            at(lut, toUs ? 1 - p : p, pa);
            b.heads.push(pa[0], pa[1]);
            b.n++;
        }
    }

    function paintBatch(c, b, colour, mul) {
        if (!b.n) {
            return;
        }
        var i, s, a;
        c.strokeStyle = colour;
        c.fillStyle = colour;
        c.lineWidth = 2;
        for (s = 0; s < NT; s++) {
            a = b.pieces[s];
            if (!a.length) {
                continue;
            }
            c.globalAlpha = TAIL[s] * mul;
            c.beginPath();
            for (i = 0; i < a.length; i += 4) {
                c.moveTo(a[i], a[i + 1]);
                c.lineTo(a[i + 2], a[i + 3]);
            }
            c.stroke();
        }
        a = b.heads;
        c.globalAlpha = mul;
        c.beginPath();
        for (i = 0; i < a.length; i += 2) {
            c.moveTo(a[i] + 2.2, a[i + 1]);
            c.arc(a[i], a[i + 1], 2.2, 0, TAU);
        }
        c.fill();
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
    /*
     * Built once, then updated in place: numbers glide to their new value,
     * the download/upload sparklines scroll continuously, table rows are
     * keyed by country and slide (FLIP) to their new rank instead of the
     * whole table being replaced every poll.
     */
    var HIST = 40;
    var stats = null;

    function buildStats() {
        var box = q('stats');
        box.innerHTML =
            statHtml('dl', 'fa-arrow-down', t('download', 'Download'), 'utm-dl', true) +
            statHtml('ul', 'fa-arrow-up', t('upload', 'Upload'), 'utm-ul', true) +
            statHtml('cc', 'fa-earth-americas', t('countries', 'Countries'), '', false) +
            statHtml('cn', 'fa-arrow-right-arrow-left', t('connections', 'Connections'), '', false);
        stats = {};
        ['dl', 'ul', 'cc', 'cn'].forEach(function (k) {
            var el = box.querySelector('[data-utm-stat="' + k + '"]');
            stats[k] = {
                value: el.querySelector('.utm-stat-value'),
                sub: el.querySelector('.utm-stat-sub'),
                spark: el.querySelector('.utm-spark'),
                cur: 0
            };
        });
    }

    function statHtml(key, icon, title, cls, spark) {
        return '<div class="utm-stat" data-utm-stat="' + key + '"><span class="utm-stat-icon ' + cls + '"><i class="fa ' + icon + '"></i></span>' +
            '<span class="utm-stat-body"><span class="utm-stat-title">' + esc(title) + '</span>' +
            '<span class="utm-stat-value">&mdash;</span>' +
            '<span class="utm-stat-sub"></span></span>' +
            (spark ? '<span class="utm-spark ' + cls + '" aria-hidden="true"><svg viewBox="0 0 100 32" preserveAspectRatio="none">' +
                '<path class="utm-spark-area"></path><path class="utm-spark-line"></path></svg></span>' : '') +
            '</div>';
    }

    /* count a number up or down to its new value */
    function tween(st, to, fmt) {
        var from = st.cur;
        st.cur = to;
        if (st.anim) {
            cancelAnimationFrame(st.anim);
            st.anim = 0;
        }
        if (reduceMotion() || document.hidden || from === to || !isFinite(from)) {
            st.value.textContent = fmt(to);
            return;
        }
        var t0 = performance.now();
        var step = function (now) {
            var k = Math.min(1, (now - t0) / 650);
            k = 1 - Math.pow(1 - k, 3);
            st.value.textContent = fmt(from + (to - from) * k);
            st.anim = k < 1 ? requestAnimationFrame(step) : 0;
        };
        st.anim = requestAnimationFrame(step);
    }

    function sparkline(st, series, max) {
        var svg = st.spark;
        if (!svg) {
            return;
        }
        /* until the history fills up, extend the oldest value to the left */
        while (series.length && series.length < HIST) {
            series.unshift(series[0]);
        }
        var n = series.length;
        var dx = 100 / (HIST - 2);
        var pts = series.map(function (v, i) {
            var x = 100 - (n - 1 - i) * dx;
            return [x, 30 - (max > 0 ? v / max : 0) * 26];
        });
        if (n < 2) {
            pts = [[0, 30], [100, 30]];
        }
        var line = 'M' + pts.map(function (p) { return p[0].toFixed(1) + ' ' + p[1].toFixed(1); }).join('L');
        var area = line + 'L' + pts[pts.length - 1][0].toFixed(1) + ' 32L' + pts[0][0].toFixed(1) + ' 32Z';
        var g = svg.firstChild;              /* the <svg>; the wrapper clips it */
        g.lastChild.setAttribute('d', line);
        g.firstChild.setAttribute('d', area);
        /* the new point arrives off to the right: slide it in over one poll.
           The animated element is the HTML-level <svg> box, so the compositor
           moves it without re-laying-out the SVG every frame. */
        if (typeof g.animate === 'function' && !reduceMotion()) {
            if (g.__utmAnim) {
                g.__utmAnim.cancel();
            }
            var wpx = svg.clientWidth || 100;
            var shift = dx * wpx / 100;
            g.__utmAnim = g.animate([{ transform: 'translate3d(' + shift + 'px,0,0)' }, { transform: 'translate3d(0,0,0)' }],
                { duration: POLL_MS, easing: 'linear' });
        }
    }

    function renderStats() {
        if (!stats) {
            buildStats();
        }
        var d = S.data;
        var tot = (d && d.totals) || {};
        var list = (d && d.countries) || [];
        var active = list.filter(function (c) { return c.cc !== '??' && c.conns > 0; }).length;
        var moving = list.filter(function (c) { return c.cc !== '??' && (c.in_bps + c.out_bps) > 2000; }).length;
        if (!d) {
            return;
        }
        tween(stats.dl, tot.in_bps || 0, fmtBits);
        tween(stats.ul, tot.out_bps || 0, fmtBits);
        stats.cc.value.textContent = String(active);
        stats.cn.value.textContent = String(tot.conns || 0);
        stats.dl.sub.textContent = fmtBytes(tot.bytes_in);
        stats.ul.sub.textContent = fmtBytes(tot.bytes_out);
        stats.cc.sub.textContent = moving + ' ' + t('active', 'active now');
        if (d.interval) {
            S.hist.push([tot.in_bps || 0, tot.out_bps || 0]);
            if (S.hist.length > HIST) {
                S.hist.shift();
            }
            var max = 0;
            S.hist.forEach(function (h) {
                max = Math.max(max, h[0], h[1]);
            });
            sparkline(stats.dl, S.hist.map(function (h) { return h[0]; }), max);
            sparkline(stats.ul, S.hist.map(function (h) { return h[1]; }), max);
        }
    }

    var table = null;

    function buildTable() {
        var wrap = q('table');
        wrap.innerHTML = '<table class="utm-table"><thead><tr>' +
            '<th class="utm-c-rank">#</th>' + sortTh('name', t('country', 'Country'), '') +
            sortTh('dl', '&darr; ' + esc(t('download', 'Download')), 'utm-c-num', true) +
            sortTh('ul', '&uarr; ' + esc(t('upload', 'Upload')), 'utm-c-num', true) +
            sortTh('rate', esc(t('share', 'Share')), 'utm-c-share', true) +
            sortTh('conns', esc(t('connections', 'Connections')), 'utm-c-num', true) +
            sortTh('bytes', esc(t('transferred', 'Transferred')), 'utm-c-num utm-c-hide', true) +
            '<th class="utm-c-hide">' + esc(t('networks', 'Networks')) + '</th>' +
            '</tr></thead><tbody></tbody></table>' +
            '<div class="utm-empty" hidden>' + esc(t('idle', 'No traffic to other countries right now.')) + '</div>';
        wrap.__utmHtml = null;
        table = { el: wrap.firstChild, body: wrap.querySelector('tbody'), empty: wrap.lastChild, rows: {} };
    }

    function sortTh(key, label, cls, raw) {
        return '<th class="utm-sortable ' + cls + '" data-utm-sort="' + key + '" tabindex="0" aria-sort="none">' +
            '<span>' + (raw ? label : esc(label)) + '</span><i class="utm-sort-ico" aria-hidden="true"></i></th>';
    }

    var SORTS = {
        name: function (c) { return countryName(c.cc).toLowerCase(); },
        dl: function (c) { return c.in_bps; },
        ul: function (c) { return c.out_bps; },
        rate: function (c) { return c.in_bps + c.out_bps + (c.bytes_in + c.bytes_out) * 1e-9; },
        conns: function (c) { return c.conns; },
        bytes: function (c) { return c.bytes_in + c.bytes_out; },
        /* history table */
        last: function (c) { return c.last; },
        hin: function (c) { return c.bytes_in; },
        hout: function (c) { return c.bytes_out; },
        htot: function (c) { return c.bytes_in + c.bytes_out; },
        now: function (c) { return liveRate(c.cc); }
    };

    function liveRate(cc) {
        var l = S.liveBy && S.liveBy[cc];
        return l ? l.in_bps + l.out_bps : 0;
    }

    function sorted(list, by) {
        by = by || S.sort;
        var get = SORTS[by.key] || SORTS.rate;
        var dir = by.dir;
        return list.slice().sort(function (a, b) {
            var x = get(a), y = get(b);
            if (x < y) {
                return -dir;
            }
            if (x > y) {
                return dir;
            }
            return a.cc < b.cc ? -1 : 1;
        });
    }

    function makeRow(cc) {
        var tr = document.createElement('tr');
        tr.className = 'utm-row';
        tr.setAttribute('data-cc', cc);
        tr.tabIndex = 0;
        tr.innerHTML = '<td class="utm-c-rank"></td>' +
            '<td><span class="utm-c-country">' + flag(cc) + '<span>' + esc(countryName(cc)) + '</span><small class="utm-home-tag" hidden>' +
            esc(t('home', 'This firewall')) + '</small></span></td>' +
            '<td class="utm-c-num utm-dl"></td><td class="utm-c-num utm-ul"></td>' +
            '<td class="utm-c-share"><span class="utm-bar"><i></i></span><small></small></td>' +
            '<td class="utm-c-num"></td><td class="utm-c-num utm-c-hide"></td><td class="utm-c-hide utm-c-nets"></td>';
        var td = tr.children;
        return {
            tr: tr, rank: td[0], home: td[1].querySelector('.utm-home-tag'), dl: td[2], ul: td[3],
            bar: td[4].querySelector('i'), pct: td[4].querySelector('small'), conns: td[5], bytes: td[6], nets: td[7],
            detail: null, cache: {}
        };
    }

    function put(r, key, el, text) {
        if (r.cache[key] !== text) {
            r.cache[key] = text;
            el.textContent = text;
        }
    }

    function putHtml(r, key, el, html) {
        if (r.cache[key] !== html) {
            r.cache[key] = html;
            el.innerHTML = html;
        }
    }

    function renderTable() {
        var all = S.view === 'all';
        q('table').hidden = all;
        q('history').hidden = !all;
        q('hist-note').hidden = !all;
        S.liveBy = {};
        ((S.data && S.data.countries) || []).forEach(function (c) {
            S.liveBy[c.cc] = c;
        });
        if (all) {
            renderHistory();
            return;
        }
        if (!table || !table.el.isConnected) {
            buildTable();
        }
        var d = S.data;
        var every = ((d && d.countries) || []).filter(function (c) {
            return c.conns > 0 || c.in_bps + c.out_bps > 0;
        });
        /* rank = place by traffic, whatever the table is sorted by */
        var rankOf = {};
        every.forEach(function (c, i) {
            rankOf[c.cc] = i + 1;
        });
        var list = sorted(every.slice(0, TOP));
        table.el.querySelectorAll('[data-utm-sort]').forEach(function (th) {
            var on = th.getAttribute('data-utm-sort') === S.sort.key;
            th.setAttribute('aria-sort', on ? (S.sort.dir > 0 ? 'ascending' : 'descending') : 'none');
        });
        var tot = (d && d.totals) || {};
        var totalRate = (tot.in_bps || 0) + (tot.out_bps || 0);
        var totalBytes = (tot.bytes_in || 0) + (tot.bytes_out || 0);
        setHtml(q('top-sub'), list.length ? esc(fmtBits(tot.in_bps)) + ' &darr; · ' + esc(fmtBits(tot.out_bps)) + ' &uarr;' : '');
        table.el.hidden = !list.length;
        table.empty.hidden = !!list.length;

        /* FLIP: remember where each row was */
        var before = {};
        var animate = list.length <= 40 && !reduceMotion() && typeof Element.prototype.animate === 'function';
        if (animate) {
            Object.keys(table.rows).forEach(function (cc) {
                before[cc] = table.rows[cc].tr.getBoundingClientRect().top;
            });
        }

        var keep = {};
        var order = [];
        list.forEach(function (c, i) {
            keep[c.cc] = true;
            var r = table.rows[c.cc] || (table.rows[c.cc] = makeRow(c.cc));
            var rate = c.in_bps + c.out_bps;
            var share = totalRate > 0 ? rate / totalRate : (totalBytes > 0 ? (c.bytes_in + c.bytes_out) / totalBytes : 0);
            put(r, 'rank', r.rank, String(rankOf[c.cc] || i + 1));
            r.home.hidden = d.home !== c.cc;
            put(r, 'dl', r.dl, fmtBits(c.in_bps));
            put(r, 'ul', r.ul, fmtBits(c.out_bps));
            var pct = Math.round(share * 100);
            if (r.cache.share !== pct) {
                r.cache.share = pct;
                r.bar.style.transform = 'scaleX(' + Math.max(0.01, share).toFixed(3) + ')';
                r.pct.textContent = pct + '%';
            }
            put(r, 'conns', r.conns, String(c.conns));
            put(r, 'bytes', r.bytes, fmtBytes(c.bytes_in + c.bytes_out));
            putHtml(r, 'nets', r.nets, (c.asns || []).slice(0, 2).map(function (a) {
                return a.asn ? '<span>' + esc(a.name || ('AS' + a.asn)) + '</span>' : '';
            }).join(''));
            r.tr.classList.toggle('is-open', S.selected === c.cc);
            r.tr.classList.toggle('is-hover', S.hover === c.cc);
            order.push(r.tr);
            if (S.selected === c.cc) {
                if (!r.detail) {
                    r.detail = document.createElement('tr');
                    r.detail.className = 'utm-detail';
                    r.detail.innerHTML = '<td></td><td colspan="7"><div class="utm-nets"></div></td>';
                }
                setHtml(r.detail.querySelector('.utm-nets'), (c.asns || []).filter(function (a) { return a.asn; }).map(function (a) {
                    return '<div class="utm-net"><b>' + esc(a.name || 'Unknown network') + '</b>' +
                        '<small>AS' + esc(a.asn) + ' · ' + a.conns + ' ' + esc(t('connections', 'Connections').toLowerCase()) + ' · ' + esc(fmtBytes(a.bytes)) + '</small>' +
                        '<span class="utm-dl">&darr; ' + esc(fmtBits(a.in_bps)) + '</span><span class="utm-ul">&uarr; ' + esc(fmtBits(a.out_bps)) + '</span></div>';
                }).join(''));
                order.push(r.detail);
            } else if (r.detail) {
                r.detail.remove();
                r.detail = null;
            }
        });
        Object.keys(table.rows).forEach(function (cc) {
            if (!keep[cc]) {
                var r = table.rows[cc];
                r.tr.remove();
                if (r.detail) {
                    r.detail.remove();
                }
                delete table.rows[cc];
            }
        });
        /* move only what is out of place, so the DOM churns as little as possible */
        var body = table.body;
        order.forEach(function (el, i) {
            if (body.children[i] !== el) {
                body.insertBefore(el, body.children[i] || null);
            }
        });
        if (animate) {
            list.forEach(function (c) {
                var r = table.rows[c.cc];
                if (before[c.cc] === undefined) {
                    r.tr.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, easing: 'ease-out' });
                    return;
                }
                var dy = before[c.cc] - r.tr.getBoundingClientRect().top;
                if (Math.abs(dy) > 1) {
                    r.tr.animate([{ transform: 'translateY(' + dy + 'px)' }, { transform: 'none' }],
                        { duration: 420, easing: 'cubic-bezier(.2, 0, 0, 1)' });
                }
            });
        }
    }

    /* ------------------------------------------------- all countries (history) */
    var hist = null;

    function buildHistory() {
        var wrap = q('history');
        wrap.innerHTML = '<table class="utm-table utm-hist"><thead><tr>' +
            '<th class="utm-c-rank">#</th>' + sortTh('name', t('country', 'Country'), '') +
            sortTh('now', esc(t('now', 'Now')), 'utm-c-num', true) +
            sortTh('hin', '&darr; ' + esc(t('downloaded', 'Downloaded')), 'utm-c-num', true) +
            sortTh('hout', '&uarr; ' + esc(t('uploaded', 'Uploaded')), 'utm-c-num', true) +
            sortTh('htot', esc(t('share', 'Share')), 'utm-c-share', true) +
            sortTh('conns', esc(t('connections', 'Connections')), 'utm-c-num utm-c-hide', true) +
            sortTh('last', esc(t('lastSeen', 'Last seen')), 'utm-c-num', true) +
            '</tr></thead><tbody></tbody></table>' +
            '<div class="utm-empty" hidden></div>';
        hist = { el: wrap.firstChild, body: wrap.querySelector('tbody'), empty: wrap.lastChild, rows: {} };
    }

    function histRow(cc) {
        var tr = document.createElement('tr');
        tr.className = 'utm-row utm-hist-row';
        tr.setAttribute('data-cc', cc);
        tr.innerHTML = '<td class="utm-c-rank"></td>' +
            '<td><span class="utm-c-country">' + flag(cc) + '<span>' + esc(countryName(cc)) + '</span></span></td>' +
            '<td class="utm-c-num utm-c-now"></td><td class="utm-c-num utm-dl"></td><td class="utm-c-num utm-ul"></td>' +
            '<td class="utm-c-share"><span class="utm-bar"><i></i></span><small></small></td>' +
            '<td class="utm-c-num utm-c-hide"></td><td class="utm-c-num utm-c-last"></td>';
        var td = tr.children;
        return { tr: tr, rank: td[0], now: td[2], dl: td[3], ul: td[4], bar: td[5].querySelector('i'), pct: td[5].querySelector('small'),
            conns: td[6], last: td[7], cache: {} };
    }

    function renderHistory() {
        if (!hist || !hist.el.isConnected) {
            buildHistory();
        }
        var h = S.history;
        var every = (h && h.countries) || [];
        setHtml(q('all-count'), every.length ? String(every.length) : '');
        var total = 0;
        var rankOf = {};
        every.forEach(function (c, i) {
            total += c.bytes_in + c.bytes_out;
            rankOf[c.cc] = i + 1;
        });
        var list = every;
        if (S.query) {
            var qy = S.query.toLowerCase();
            list = list.filter(function (c) {
                return countryName(c.cc).toLowerCase().indexOf(qy) !== -1 || c.cc.toLowerCase() === qy;
            });
        }
        list = sorted(list, S.hsort);
        hist.el.querySelectorAll('[data-utm-sort]').forEach(function (th) {
            var on = th.getAttribute('data-utm-sort') === S.hsort.key;
            th.setAttribute('aria-sort', on ? (S.hsort.dir > 0 ? 'ascending' : 'descending') : 'none');
        });
        hist.el.hidden = !list.length;
        hist.empty.hidden = !!list.length;
        hist.empty.textContent = !h ? t('loading', 'Loading…') : S.query ? t('noMatch', 'No country matches your search.') :
            t('noHistory', 'No countries recorded yet.');

        var keep = {};
        list.forEach(function (c, i) {
            keep[c.cc] = true;
            var r = hist.rows[c.cc] || (hist.rows[c.cc] = histRow(c.cc));
            var rate = liveRate(c.cc);
            put(r, 'rank', r.rank, String(rankOf[c.cc]));
            putHtml(r, 'now', r.now, rate > 0 ? '<span class="utm-now-dot"></span>' + esc(fmtBits(rate)) : '<span class="utm-muted">&mdash;</span>');
            put(r, 'dl', r.dl, fmtBytes(c.bytes_in));
            put(r, 'ul', r.ul, fmtBytes(c.bytes_out));
            var share = total > 0 ? (c.bytes_in + c.bytes_out) / total : 0;
            var pct = share * 100;
            var label = pct >= 1 || pct === 0 ? Math.round(pct) + '%' : '<1%';
            if (r.cache.share !== label) {
                r.cache.share = label;
                r.bar.style.transform = 'scaleX(' + Math.max(0.01, share).toFixed(3) + ')';
                r.pct.textContent = label;
            }
            put(r, 'conns', r.conns, String(c.conns));
            put(r, 'last', r.last, S.liveBy[c.cc] ? t('justNow', 'now') : ago(c.last));
            r.tr.classList.toggle('is-live', rate > 0);
            r.tr.classList.toggle('is-hover', S.hover === c.cc);
            if (hist.body.children[i] !== r.tr) {
                hist.body.insertBefore(r.tr, hist.body.children[i] || null);
            }
        });
        Object.keys(hist.rows).forEach(function (cc) {
            if (!keep[cc]) {
                hist.rows[cc].tr.remove();
                delete hist.rows[cc];
            }
        });
        var since = h && h.since ? new Date(h.since * 1000) : null;
        setHtml(q('hist-note'), (since ? esc(t('since', 'Recorded since')) + ' ' + esc(since.toLocaleDateString()) + ' · ' : '') +
            esc(t('histHow', 'sampled every minute, also while this page is closed')) +
            ' <button type="button" class="btn btn-link btn-xs" data-utm-action="reset">' +
            esc(S.resetArmed ? t('resetConfirm', 'Click again to clear the history') : t('reset', 'Clear history')) + '</button>');
    }

    function loadHistory() {
        S.histAt = Date.now();
        return fetchJson('/api/unisense/trafficmap/history').then(function (d) {
            if (d && d.ok) {
                S.history = d;
            }
            if (S.view === 'all') {
                renderTable();
            } else {
                setHtml(q('all-count'), S.history && S.history.countries.length ? String(S.history.countries.length) : '');
            }
        });
    }

    function setView(v) {
        S.view = v;
        root.querySelectorAll('[data-utm-view]').forEach(function (b) {
            var on = b.getAttribute('data-utm-view') === v;
            b.classList.toggle('is-active', on);
            b.setAttribute('aria-selected', String(on));
        });
        q('search-wrap').hidden = v !== 'all';
        renderTable();
        if (v === 'all' && Date.now() - S.histAt > 5000) {
            loadHistory();
        }
    }

    function onClick(e) {
        var tab = e.target.closest && e.target.closest('[data-utm-view]');
        if (tab) {
            setView(tab.getAttribute('data-utm-view'));
            return;
        }
        var stat = e.target.closest && e.target.closest('[data-utm-stat="cc"]');
        if (stat) {
            setView('all');
            q('table').scrollIntoView({ block: 'start', behavior: reduceMotion() ? 'auto' : 'smooth' });
            return;
        }
        var th = e.target.closest && e.target.closest('[data-utm-sort]');
        if (th) {
            var key = th.getAttribute('data-utm-sort');
            var cur = S.view === 'all' ? S.hsort : S.sort;
            var next = cur.key === key ? { key: key, dir: -cur.dir } : { key: key, dir: key === 'name' ? 1 : -1 };
            if (S.view === 'all') {
                S.hsort = next;
            } else {
                S.sort = next;
            }
            renderTable();
            return;
        }
        var btn = e.target.closest && e.target.closest('[data-utm-action]');
        if (btn) {
            var act = btn.getAttribute('data-utm-action');
            if (act === 'update') {
                startUpdate();
            } else if (act === 'reset') {
                if (!S.resetArmed) {
                    S.resetArmed = true;
                    setTimeout(function () {
                        S.resetArmed = false;
                        if (S.view === 'all') {
                            renderTable();
                        }
                    }, 4000);
                    renderTable();
                } else {
                    S.resetArmed = false;
                    post('/api/unisense/trafficmap/reset').then(loadHistory);
                }
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
            if (S.view === 'all' ? Date.now() - S.histAt > 15000 : !S.histAt) {
                loadHistory();
            }
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
    window.addEventListener('scroll', function () {
        pace.scrolling = performance.now() + 150;
    }, { passive: true, capture: true });
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
