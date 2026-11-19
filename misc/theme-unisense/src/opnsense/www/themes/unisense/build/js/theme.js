/*
 * UniSense for OPNsense — theme runtime
 * (c) ox1d3x3 — BSD 2-Clause License, see LICENSE
 *
 * Loaded synchronously in <head> by OPNsense on every page (MVC, legacy and
 * the login page), after the stylesheets and before <body> is parsed.
 *
 * Responsibilities (kept deliberately small and defensive):
 *   1. apply the saved colour mode (auto / light / dark) before first paint,
 *   2. apply the saved accent colour,
 *   3. add an appearance menu to the top bar,
 *   4. keep Chart.js dashboard widgets in step with the colour mode,
 *   5. motion helpers (smooth mode switch, no layout animation while loading),
 *   6. the author credit in the footer and the sign-in page polish.
 *
 * Nothing here is required for the GUI to work: with JavaScript disabled or
 * storage blocked the theme simply follows the operating system.
 */
/* global BaseGaugeWidget */
(function () {
    'use strict';

    var VERSION = '1.2.0';   /* stamped from VERSION by scripts/build.mjs */
    var REPO_URL = 'https://github.com/ox1d3x3/unisense';
    var MODE_KEY = 'unisense.mode';
    var MENU_KEY = 'unisense.menu';     /* 'expanded': every menu section stays open */
    var ACCENT_KEY = 'unisense.accent';
    var MODES = ['auto', 'light', 'dark'];
    var ACCENTS = [
        { name: 'Blue', value: '#006fff' },
        { name: 'Teal', value: '#0aa5a5' },
        { name: 'Green', value: '#1fa74d' },
        { name: 'Purple', value: '#7b5cff' },
        { name: 'Pink', value: '#e5487f' },
        { name: 'Orange', value: '#f2700f' }
    ];
    var DEFAULT_ACCENT = ACCENTS[0].value;
    var root = document.documentElement;

    /* ---------------------------------------------------------- storage */
    function read(key) {
        try {
            return window.localStorage ? window.localStorage.getItem(key) : null;
        } catch (e) {
            return null;
        }
    }

    function write(key, value) {
        try {
            if (!window.localStorage) {
                return;
            }
            if (value === null) {
                window.localStorage.removeItem(key);
            } else {
                window.localStorage.setItem(key, value);
            }
        } catch (e) {
            /* storage disabled or full: preference simply isn't remembered */
        }
    }

    /* ------------------------------------------------------------- mode */
    function savedMode() {
        var m = read(MODE_KEY);
        return MODES.indexOf(m) === -1 ? 'auto' : m;
    }

    function systemPrefersDark() {
        try {
            return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
        } catch (e) {
            return false;
        }
    }

    function effectiveMode() {
        var m = savedMode();
        if (m === 'auto') {
            return systemPrefersDark() ? 'dark' : 'light';
        }
        return m;
    }

    function applyMode(m) {
        if (m === 'light' || m === 'dark') {
            root.setAttribute('data-us-theme', m);
        } else {
            root.removeAttribute('data-us-theme');
        }
    }

    /* ----------------------------------------------------------- accent */
    function validAccent(v) {
        return typeof v === 'string' && /^#[0-9a-fA-F]{6}$/.test(v);
    }

    function savedAccent() {
        var a = read(ACCENT_KEY);
        return validAccent(a) ? a.toLowerCase() : DEFAULT_ACCENT;
    }

    function applyAccent(a) {
        if (validAccent(a) && a.toLowerCase() !== DEFAULT_ACCENT) {
            root.style.setProperty('--us-accent', a);
        } else {
            root.style.removeProperty('--us-accent');
        }
    }

    /* apply before first paint */
    installChartPlugin();
    patchSmoothie();
    applyMode(savedMode());
    applyAccent(savedAccent());
    root.classList.add('unisense');
    /* no layout animation (dashboard grid etc.) until the page has settled */
    root.classList.add('us-loading');

    /* ----------------------------------------------------------- charts */
    function cssVar(name) {
        try {
            return window.getComputedStyle(document.body || root).getPropertyValue(name).trim();
        } catch (e) {
            return '';
        }
    }

    /*
     * Core dashboard widgets hard-code a few colours (gauge orange #D94F00,
     * ARC green #A8C49B and a light grey track #E5E5E5). Map exactly those
     * onto theme tokens, in place and reversibly, so gauges follow the accent
     * colour and look right in dark mode. Any other colour is left untouched.
     */
    var CHART_MAP = {
        '#D94F00': '--us-chart-1', '#A8C49B': '--us-chart-2', '#E5E5E5': '--us-chart-track',
        /* tableau Classic10 (traffic, interface statistics, firewall) */
        '#1F77B4': '--us-series-1', '#FF7F0E': '--us-series-2', '#2CA02C': '--us-series-3', '#D62728': '--us-series-4',
        '#9467BD': '--us-series-5', '#8C564B': '--us-series-6', '#E377C2': '--us-series-7', '#7F7F7F': '--us-series-8',
        '#BCBD22': '--us-series-9', '#17BECF': '--us-series-10',
        /* Bootstrap status colours (thermal sensors) */
        '#28A745': '--us-ok', '#FFC107': '--us-warn', '#DC3545': '--us-crit'
    };
    var paletteCache = {};
    var meters = [];          /* live usage meters on the dashboard */
    var probe = null;

    /* computed colours come back as rgb()/rgba() or, for color-mix(), color(srgb r g b [/ a]) */
    function normalizeColor(c) {
        if (typeof c !== 'string') {
            return null;
        }
        if (c.indexOf('rgb') === 0) {
            return c;
        }
        var m = c.match(/^color\(srgb\s+([\d.e-]+)\s+([\d.e-]+)\s+([\d.e-]+)(?:\s*\/\s*([\d.e-]+))?\)$/);
        if (!m) {
            return null;
        }
        var ch = function (v) {
            return Math.max(0, Math.min(255, Math.round(parseFloat(v) * 255)));
        };
        if (m[4] !== undefined && parseFloat(m[4]) < 1) {
            return 'rgba(' + ch(m[1]) + ', ' + ch(m[2]) + ', ' + ch(m[3]) + ', ' + parseFloat(m[4]) + ')';
        }
        return 'rgb(' + ch(m[1]) + ', ' + ch(m[2]) + ', ' + ch(m[3]) + ')';
    }

    function resolveToken(name) {
        if (paletteCache[name] !== undefined) {
            return paletteCache[name];
        }
        var out = null;
        try {
            if (document.body) {
                if (!probe || !probe.parentNode) {
                    probe = document.createElement('span');
                    probe.setAttribute('aria-hidden', 'true');
                    probe.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;visibility:hidden;';
                    document.body.appendChild(probe);
                }
                probe.style.color = '';
                probe.style.color = 'var(' + name + ')';
                out = normalizeColor(window.getComputedStyle(probe).color);
            }
        } catch (e) {
            out = null;
        }
        paletteCache[name] = out;
        return out;
    }

    function mapColor(c) {
        if (typeof c !== 'string' || c.charAt(0) !== '#' || (c.length !== 7 && c.length !== 9)) {
            return c;
        }
        var token = CHART_MAP[c.slice(0, 7).toUpperCase()];
        if (!token) {
            return c;
        }
        var rgb = resolveToken(token);
        if (!rgb) {
            return c;
        }
        if (c.length === 9) {
            var a = parseInt(c.slice(7), 16);
            var m = rgb.match(/[\d.]+/g);
            if (!isNaN(a) && m && m.length >= 3) {
                return 'rgba(' + m[0] + ', ' + m[1] + ', ' + m[2] + ', ' + (a / 255).toFixed(3) + ')';
            }
        }
        return rgb;
    }

    /* remembers what we replaced so a later mode change can map it again */
    function recolorSlot(holder, key, rec) {
        var cur = holder[key];
        var r = rec[key];
        var orig = (r && cur === r.out) ? r.orig : cur;
        var out = mapColor(orig);
        if (out !== orig) {
            holder[key] = out;
            rec[key] = { orig: orig, out: out };
        } else if (r) {
            delete rec[key];
        }
    }

    function recolorDataset(ds) {
        if (!ds || typeof ds !== 'object') {
            return;
        }
        if (!ds.__unisense) {
            Object.defineProperty(ds, '__unisense', { value: {}, enumerable: false });
        }
        var store = ds.__unisense;
        ['backgroundColor', 'hoverBackgroundColor', 'borderColor', 'hoverBorderColor'].forEach(function (prop) {
            var v = ds[prop];
            if (Array.isArray(v)) {
                var rec = store[prop] || (store[prop] = {});
                for (var i = 0; i < v.length; i++) {
                    recolorSlot(v, i, rec);
                }
            } else if (typeof v === 'string') {
                recolorSlot(ds, prop, store.scalar || (store.scalar = {}));
            }
        });
    }

    /*
     * A modern look for core's dashboard charts, applied to each chart's
     * config before its first render: slim rounded gauges, ring charts with
     * gaps and dot legends, thin smooth lines with a light fill, flat bars.
     */
    function styleChart(chart) {
        var cfg = chart.config;
        var o = cfg && cfg.options;
        var type = cfg && cfg.type;
        var sets = (cfg && cfg.data && cfg.data.datasets) || [];
        if (!o) {
            return;
        }
        if (type === 'doughnut' || type === 'pie') {
            var gauge = o.circumference === 180;
            o.cutout = gauge ? '80%' : '70%';
            var arc = { borderWidth: gauge ? 0 : 2, borderRadius: gauge ? 6 : 5, hoverOffset: gauge ? 3 : 6, spacing: 0 };
            o.elements = o.elements || {};
            o.elements.arc = Object.assign(o.elements.arc || {}, arc);
            sets.forEach(function (ds) {
                Object.assign(ds, arc);
            });
            if (gauge) {
                /* room for the rounded ends and the hover offset */
                o.layout = Object.assign(o.layout || {}, { padding: { left: 8, right: 8, top: 6, bottom: 0 } });
            }
            o.plugins = o.plugins || {};
            if (!gauge && o.plugins.legend) {
                o.plugins.legend.labels = Object.assign(o.plugins.legend.labels || {}, {
                    usePointStyle: true, pointStyle: 'circle', boxWidth: 8, boxHeight: 8, padding: 12
                });
            }
        } else if (type === 'line') {
            o.elements = o.elements || {};
            o.elements.line = Object.assign(o.elements.line || {}, { borderWidth: 1.75, tension: 0.35 });
            o.elements.point = Object.assign(o.elements.point || {}, { radius: 0, hoverRadius: 3, hitRadius: 8 });
            var scales = o.scales || {};
            Object.keys(scales).forEach(function (k) {
                var sc = scales[k];
                sc.border = Object.assign(sc.border || {}, { display: false });
                sc.grid = Object.assign(sc.grid || {}, { drawTicks: false });
                sc.ticks = Object.assign(sc.ticks || {}, { padding: 8 });
            });
        } else if (type === 'bar') {
            sets.forEach(function (ds) {
                ds.borderWidth = 0;
            });
            /* Thermal Sensors: slimmer pills over a track, theme status colours */
            var track = sets[1] && Array.isArray(sets[1].backgroundColor) && String(sets[1].backgroundColor[0]).toUpperCase() === '#E5E5E5';
            if (o.indexAxis === 'y' && track) {
                [sets[0], sets[1]].forEach(function (ds) {
                    ds.barPercentage = 0.62;
                    ds.borderRadius = 999;
                    ds.borderSkipped = 'middle';    /* round the two outer ends of the stack */
                });
                Object.keys(o.scales || {}).forEach(function (k) {
                    o.scales[k].border = Object.assign(o.scales[k].border || {}, { display: false });
                    o.scales[k].grid = Object.assign(o.scales[k].grid || {}, { display: false, drawBorder: false });
                });
                var pick = sets[0].backgroundColor;
                if (typeof pick === 'function' && !pick.__us) {
                    var wrapped = function (ctx) {
                        var v = pick.call(this, ctx);
                        return Array.isArray(v) ? v.map(mapColor) : mapColor(v);
                    };
                    wrapped.__us = true;
                    sets[0].backgroundColor = wrapped;
                }
            }
        }
    }

    /* ring charts with long labels (Firewall rule names): legend under the
       chart, where each label has the full width, instead of cut off beside it */
    function legendBelow(chart) {
        var cfg = chart.config;
        var lg = cfg && cfg.options && cfg.options.plugins && cfg.options.plugins.legend;
        if (!lg || (cfg.type !== 'doughnut' && cfg.type !== 'pie') || cfg.options.circumference === 180) {
            return;
        }
        var labels = (chart.data && chart.data.labels) || [];
        var long = labels.some(function (l) {
            return String(l).length > 24;
        });
        var want = long ? 'bottom' : 'left';
        if (lg.position !== want && (lg.position === 'left' || lg.position === 'bottom')) {
            lg.position = want;
            lg.align = long ? 'start' : 'center';
        }
    }

    /* line charts: a light wash under the line instead of a 50% fill */
    function softenFill(ds) {
        var c = ds.backgroundColor;
        if (typeof c !== 'string') {
            return;
        }
        var m = c.match(/^rgba\(\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+)\s*\)$/);
        if (m && parseFloat(m[4]) > 0.2) {
            ds.backgroundColor = 'rgba(' + m[1] + ', ' + m[2] + ', ' + m[3] + ', 0.16)';
            if (ds.__unisense && ds.__unisense.scalar && ds.__unisense.scalar.backgroundColor) {
                ds.__unisense.scalar.backgroundColor.out = ds.backgroundColor;
            }
        }
    }

    function installChartPlugin() {
        var C = window.Chart;
        if (!C || typeof C.register !== 'function' || installChartPlugin.done) {
            return;
        }
        installChartPlugin.done = true;
        try {
            C.register({
                id: 'unisenseColors',
                beforeInit: function (chart) {
                    try {
                        styleChart(chart);
                    } catch (e) { /* cosmetic only */ }
                },
                beforeUpdate: function (chart) {
                    try {
                        var sets = (chart && chart.data && chart.data.datasets) || [];
                        var line = chart.config && chart.config.type === 'line';
                        legendBelow(chart);
                        for (var i = 0; i < sets.length; i++) {
                            recolorDataset(sets[i]);
                            if (line) {
                                softenFill(sets[i]);
                            }
                        }
                    } catch (e) { /* cosmetic only */ }
                }
            });
        } catch (e) { /* ignore */ }
    }

    function patchSmoothie() {
        var S = window.SmoothieChart;
        if (!S || !S.prototype || S.prototype.__unisense) {
            return;
        }
        var orig = S.prototype.addTimeSeries;
        if (typeof orig !== 'function') {
            return;
        }
        S.prototype.__unisense = true;
        S.prototype.addTimeSeries = function (ts, opts) {
            try {
                if (opts && typeof opts === 'object') {
                    ['strokeStyle', 'fillStyle'].forEach(function (k) {
                        if (typeof opts[k] === 'string') {
                            var mapped = mapColor(opts[k]);
                            if (mapped !== opts[k]) {
                                opts[k] = mapped;
                            }
                        }
                    });
                }
            } catch (e) { /* ignore */ }
            return orig.apply(this, arguments);
        };
    }

    function refreshCharts() {
        var C = window.Chart;
        if (!C || !C.defaults) {
            return;
        }
        try {
            var font = cssVar('--chart-js-font-color');
            var border = cssVar('--chart-js-border-color');
            var bg = cssVar('--chart-js-background-color');
            if (font) {
                C.defaults.color = font;
            }
            if (border) {
                C.defaults.borderColor = border;
            }
            if (bg) {
                C.defaults.backgroundColor = bg;
            }
            /* doughnut/gauge segment separators: card colour instead of white */
            var surface = resolveToken('--us-surface');
            if (surface && C.defaults.elements && C.defaults.elements.arc) {
                C.defaults.elements.arc.borderColor = surface;
            }
            meters.forEach(function (m) {
                m.draw(0);
            });
            var instances = C.instances || {};
            Object.keys(instances).forEach(function (k) {
                var chart = instances[k];
                if (chart && typeof chart.update === 'function') {
                    chart.update('none');
                }
            });
        } catch (e) {
            /* never let cosmetics break a page */
        }
    }

    /* --------------------------------------------------- live usage meters */
    /*
     * Core's Memory, Disk, Firewall States, Swap and MBUF widgets draw a half
     * doughnut gauge. UniSense shows them like the CPU widget instead: the
     * value in large type, a live area graph of the last minutes (auto-scaled,
     * so small changes are visible, with a hover read-out), a slim capacity
     * bar and a legend. The gauge chart keeps running, hidden, because widget
     * code reads from it; the meter is fed from the same updateChart() call.
     * History survives a page reload (sessionStorage).
     */
    var METER_TICK = { Memory: 5, FirewallStates: 10, Mbuf: 15, Swap: 15, Disk: 60 };
    var METER_POINTS = 90;

    function patchGauges() {
        if (typeof BaseGaugeWidget === 'undefined' || BaseGaugeWidget.prototype.__us) {
            return;
        }
        var P = BaseGaugeWidget.prototype;
        P.__us = true;
        var create = P.createGaugeChart;
        var update = P.updateChart;
        P.createGaugeChart = function (options) {
            create.call(this, options);
            try {
                mountMeter(this, options || {});
            } catch (e) { /* the gauge still works */ }
        };
        P.updateChart = function (data) {
            update.call(this, data);
            try {
                if (this.__usMeter) {
                    this.__usMeter.push(data);
                }
            } catch (e) { /* cosmetic only */ }
        };
    }

    function mountMeter(w, opts) {
        var canvas = document.getElementById(w.id + '-chart');
        var box = canvas && canvas.closest('.canvas-container');
        if (!box || box.style.display === 'none') {
            return;
        }
        var name = w.constructor && w.constructor.name;
        if (METER_TICK[name] && w.tickTimeout > METER_TICK[name]) {
            w.tickTimeout = METER_TICK[name];
        }
        box.classList.add('us-gauge-src');
        var node = document.createElement('div');
        node.className = 'us-meter';
        node.innerHTML =
            '<div class="us-meter-head"><span class="us-meter-value">&mdash;</span><span class="us-meter-sub"></span></div>' +
            '<div class="us-meter-graph"><canvas></canvas><span class="us-meter-hi"></span><span class="us-meter-lo"></span>' +
            '<span class="us-meter-tip" hidden></span></div>' +
            '<div class="us-meter-bar"></div><div class="us-meter-legend"></div>';
        box.parentNode.insertBefore(node, box.nextSibling);
        w.__usMeter = new Meter(w, opts, node);
        meters.push(w.__usMeter);
    }

    function Meter(w, opts, node) {
        var self = this;
        this.w = w;
        this.opts = opts;
        this.el = node;
        this.value = node.querySelector('.us-meter-value');
        this.sub = node.querySelector('.us-meter-sub');
        this.graph = node.querySelector('.us-meter-graph');
        this.canvas = node.querySelector('canvas');
        this.hiEl = node.querySelector('.us-meter-hi');
        this.loEl = node.querySelector('.us-meter-lo');
        this.tip = node.querySelector('.us-meter-tip');
        this.bar = node.querySelector('.us-meter-bar');
        this.legend = node.querySelector('.us-meter-legend');
        this.key = 'unisense.meter.' + w.id;
        this.hist = [];
        this.offset = 0;
        this.hover = -1;
        try {
            var saved = JSON.parse(window.sessionStorage.getItem(this.key) || '[]');
            /* forget history older than the graph spans */
            var since = Date.now() - METER_POINTS * (w.tickTimeout || 10) * 1000;
            this.hist = Array.isArray(saved) ? saved.filter(function (h) { return h && h.t > since; }) : [];
        } catch (e) { /* no storage */ }
        if (typeof ResizeObserver === 'function') {
            new ResizeObserver(function () {
                self.draw(0);
            }).observe(this.graph);
        }
        this.graph.addEventListener('mousemove', function (e) {
            self.hoverAt(e.clientX);
        });
        this.graph.addEventListener('mouseleave', function () {
            self.hover = -1;
            self.tip.hidden = true;
            self.draw(0);
        });
    }

    Meter.prototype.push = function (data) {
        var sum = 0;
        var i;
        for (i = 0; i < data.length; i++) {
            sum += +data[i] || 0;
        }
        if (!(sum > 0)) {
            return;
        }
        var vals = [];
        for (i = 0; i < data.length - 1; i++) {
            vals.push(Math.round((+data[i] || 0) / sum * 10000) / 100);
        }
        var ds = this.w.chart && this.w.chart.config.data.datasets[0];
        if (ds && ds.pct && isFinite(+ds.pct[0])) {
            vals[0] = +ds.pct[0];
        }
        var primary = this.opts.primaryText ? this.opts.primaryText(data, this.w.chart) :
            (data[0] / sum * 100).toFixed(2) + '%';
        var secondary = this.opts.secondaryText ? this.opts.secondaryText(data, this.w.chart) : '';
        var m = String(primary).match(/^\s*([\d.,]+)\s*%\s*$/);
        this.value.innerHTML = m ? escapeHtml(m[1]) + '<small>%</small>' : escapeHtml(String(primary));
        this.sub.textContent = secondary ? String(secondary).replace(/^\((.*)\)$/, '$1') : '';

        /* capacity bar + legend (all but the last value, which is "free") */
        var labels = this.opts.labels || [];
        var bar = '';
        var leg = '';
        for (i = 0; i < vals.length; i++) {
            bar += '<i class="us-s' + i + '" style="width:' + Math.max(0, Math.min(100, vals[i])) + '%"></i>';
            leg += '<span><b class="us-s' + i + '"></b>' + escapeHtml(labels[i] || '') + ' <em>' + vals[i].toFixed(vals[i] < 10 ? 2 : 1) + '%</em></span>';
        }
        if (labels[vals.length]) {
            var free = Math.max(0, 100 - vals.reduce(function (a, b) { return a + b; }, 0));
            leg += '<span><b class="us-free"></b>' + escapeHtml(labels[vals.length]) + ' <em>' + free.toFixed(free < 10 ? 2 : 1) + '%</em></span>';
        }
        this.bar.innerHTML = bar;
        if (this.legend.__last !== leg) {
            this.legend.__last = leg;
            this.legend.innerHTML = leg;
        }

        var last = this.hist[this.hist.length - 1];
        /* the hidden gauge may be redrawn with the same sample: ignore repeats within a second */
        if (last && Date.now() - last.t < 1000) {
            last.v = vals;
        } else {
            this.hist.push({ t: Date.now(), v: vals });
            if (this.hist.length > METER_POINTS) {
                this.hist.splice(0, this.hist.length - METER_POINTS);
            }
            this.slide();
        }
        try {
            window.sessionStorage.setItem(this.key, JSON.stringify(this.hist));
        } catch (e) { /* storage full or blocked */ }
        this.draw(0);
    };

    /* a new point slides in from the right */
    Meter.prototype.slide = function () {
        var self = this;
        if (reducedMotion() || this.hist.length < 2) {
            return;
        }
        var t0 = performance.now();
        cancelAnimationFrame(this.raf || 0);
        var step = function (now) {
            var k = Math.min(1, (now - t0) / 600);
            self.offset = 1 - (1 - Math.pow(1 - k, 3));
            self.draw(0);
            self.raf = k < 1 ? requestAnimationFrame(step) : 0;
        };
        this.offset = 1;
        this.raf = requestAnimationFrame(step);
    };

    Meter.prototype.range = function () {
        var lo = 100, hi = 0;
        this.hist.forEach(function (h) {
            var top = 0;
            h.v.forEach(function (v) {
                top += v;
            });
            lo = Math.min(lo, h.v[0]);
            hi = Math.max(hi, top);
        });
        if (lo > hi) {
            return [0, 100];
        }
        var pad = Math.max(1, (hi - lo) * 0.35);
        var a = Math.max(0, lo - pad), b = Math.min(100, hi + pad);
        if (b - a < 4) {
            var mid = (a + b) / 2;
            a = Math.max(0, mid - 2);
            b = Math.min(100, a + 4);
            a = Math.max(0, b - 4);
        }
        var nice = b - a > 20 ? 5 : 1;
        return [Math.floor(a / nice) * nice, Math.ceil(b / nice) * nice];
    };

    Meter.prototype.geometry = function () {
        var w = this.graph.clientWidth, h = this.graph.clientHeight;
        var n = this.hist.length;
        /* until the history fills the graph, spread it over fewer slots */
        var stepX = w / (Math.max(16, Math.min(METER_POINTS, n)) - 1);
        return { w: w, h: h, n: n, stepX: stepX, x: function (i) { return w - (n - 1 - i) * stepX; } };
    };

    Meter.prototype.draw = function () {
        var c = this.canvas;
        var g = this.geometry();
        if (!g.w || !g.h) {
            return;
        }
        var dpr = Math.min(window.devicePixelRatio || 1, 2);
        if (c.width !== Math.round(g.w * dpr) || c.height !== Math.round(g.h * dpr)) {
            c.width = Math.round(g.w * dpr);
            c.height = Math.round(g.h * dpr);
        }
        var ctx = c.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, g.w, g.h);
        var r = this.range();
        this.hiEl.textContent = r[1] + '%';
        this.loEl.textContent = r[0] + '%';
        var top = 6, bottom = g.h - 2;
        var y = function (v) {
            return bottom - (v - r[0]) / (r[1] - r[0]) * (bottom - top);
        };
        /* grid */
        ctx.strokeStyle = cssVar('--us-line') || 'rgba(128,128,128,.2)';
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 4]);
        ctx.beginPath();
        for (var gl = 0; gl <= 3; gl++) {
            var gy = Math.round(top + (bottom - top) * gl / 3) + 0.5;
            ctx.moveTo(0, gy);
            ctx.lineTo(g.w, gy);
        }
        ctx.stroke();
        ctx.setLineDash([]);
        if (g.n < 1) {
            return;
        }
        var shift = this.offset * g.stepX;
        var hist = this.hist;
        var colors = [resolveToken('--us-chart-1') || '#006fff', resolveToken('--us-chart-2') || '#38cc65'];
        var series = hist[g.n - 1].v.length;
        ctx.save();
        ctx.translate(shift, 0);
        /* stacked areas, top series first so the lower one is drawn over it */
        for (var s = Math.min(series, 2) - 1; s >= 0; s--) {
            var pts = [];
            for (var i = 0; i < g.n; i++) {
                pts.push([g.x(i), y(stackTop(hist[i], s))]);
            }
            if (pts.length === 1) {
                pts.unshift([pts[0][0] - g.stepX, pts[0][1]]);
            }
            ctx.beginPath();
            ctx.moveTo(pts[0][0], pts[0][1]);
            for (var j = 1; j < pts.length; j++) {
                /* smooth: quadratic segments through the midpoints */
                var mx = (pts[j - 1][0] + pts[j][0]) / 2, my = (pts[j - 1][1] + pts[j][1]) / 2;
                ctx.quadraticCurveTo(pts[j - 1][0], pts[j - 1][1], mx, my);
            }
            ctx.lineTo(pts[pts.length - 1][0], pts[pts.length - 1][1]);
            ctx.strokeStyle = colors[s];
            ctx.lineWidth = s === 0 ? 2 : 1.25;
            ctx.lineJoin = 'round';
            ctx.stroke();
            ctx.lineTo(pts[pts.length - 1][0], bottom + 2);
            ctx.lineTo(pts[0][0], bottom + 2);
            ctx.closePath();
            var grad = ctx.createLinearGradient(0, top, 0, bottom);
            grad.addColorStop(0, withAlpha(colors[s], s === 0 ? 0.32 : 0.22));
            grad.addColorStop(1, withAlpha(colors[s], 0.02));
            ctx.fillStyle = grad;
            ctx.fill();
        }
        /* the latest value */
        var lx = g.x(g.n - 1), ly = y(hist[g.n - 1].v[0]);
        ctx.fillStyle = withAlpha(colors[0], 0.22);
        ctx.beginPath();
        ctx.arc(lx - 1, ly, 6, 0, 6.2832);
        ctx.fill();
        ctx.fillStyle = colors[0];
        ctx.beginPath();
        ctx.arc(lx - 1, ly, 3, 0, 6.2832);
        ctx.fill();
        /* hover guide */
        if (this.hover >= 0 && this.hover < g.n) {
            var hx = Math.round(g.x(this.hover)) + 0.5;
            ctx.strokeStyle = withAlpha(colors[0], 0.5);
            ctx.beginPath();
            ctx.moveTo(hx, top);
            ctx.lineTo(hx, bottom);
            ctx.stroke();
            ctx.fillStyle = colors[0];
            ctx.beginPath();
            ctx.arc(hx, y(hist[this.hover].v[0]), 3, 0, 6.2832);
            ctx.fill();
        }
        ctx.restore();
    };

    Meter.prototype.hoverAt = function (clientX) {
        var g = this.geometry();
        if (!g.n) {
            return;
        }
        var rect = this.graph.getBoundingClientRect();
        var i = Math.round(g.n - 1 - (g.w - (clientX - rect.left)) / g.stepX);
        if (i < 0 || i >= g.n) {
            this.hover = -1;
            this.tip.hidden = true;
            this.draw(0);
            return;
        }
        this.hover = i;
        var h = this.hist[i];
        var labels = this.opts.labels || [];
        var d = new Date(h.t);
        var pad = function (v) { return (v < 10 ? '0' : '') + v; };
        this.tip.innerHTML = '<b>' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) + '</b>' +
            h.v.map(function (v, k) {
                return '<span><i class="us-s' + k + '"></i>' + escapeHtml(labels[k] || '') + ' ' + v.toFixed(2) + '%</span>';
            }).join('');
        this.tip.hidden = false;
        var x = g.x(i);
        this.tip.style.left = Math.max(4, Math.min(g.w - this.tip.offsetWidth - 4, x - this.tip.offsetWidth / 2)) + 'px';
        this.draw(0);
    };

    function stackTop(h, s) {
        var t = 0;
        for (var k = 0; k <= s; k++) {
            t += h.v[k] || 0;
        }
        return t;
    }

    function withAlpha(color, a) {
        var m = String(color).match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
        if (m) {
            return 'rgba(' + m[1] + ',' + m[2] + ',' + m[3] + ',' + a + ')';
        }
        var hx = String(color).match(/^#([0-9a-f]{6})$/i);
        if (hx) {
            var n = parseInt(hx[1], 16);
            return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
        }
        return color;
    }

    function escapeHtml(v) {
        return String(v).replace(/[&<>"']/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
        });
    }

    function reducedMotion() {
        try {
            return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (e) {
            return false;
        }
    }

    /* inside a colour switch: recolour synchronously so the new snapshot is right */
    function modeChangedNow() {
        paletteCache = {};
        refreshCharts();
        updateMenuState();
    }

    function modeChanged() {
        paletteCache = {};
        /* wait one frame so the new custom properties are computed */
        var raf = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 16); };
        raf(function () {
            refreshCharts();
            updateMenuState();
        });
    }

    /*
     * Switching colours restyles every element. Rather than hundreds of
     * per-element colour transitions (janky on big pages) the change is
     * applied in one frame with transitions switched off. (A View Transitions
     * cross-fade was measured too: its snapshots cost more frames than the
     * single restyle itself.)
     */
    function switchSmoothly(change) {
        root.classList.add('us-switching');
        change();
        var raf = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 16); };
        raf(function () {
            raf(function () {
                root.classList.remove('us-switching');
            });
        });
    }

    function setMode(m) {
        write(MODE_KEY, m === 'auto' ? null : m);
        switchSmoothly(function () {
            applyMode(m);
            modeChangedNow();
        });
    }

    function setAccent(a) {
        write(ACCENT_KEY, a === DEFAULT_ACCENT ? null : a);
        switchSmoothly(function () {
            applyAccent(a);
            modeChangedNow();
        });
    }

    /* system theme changes while in auto mode */
    try {
        var mq = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)');
        if (mq) {
            var onSystemChange = function () {
                if (savedMode() === 'auto') {
                    modeChanged();
                }
            };
            if (mq.addEventListener) {
                mq.addEventListener('change', onSystemChange);
            } else if (mq.addListener) {
                mq.addListener(onSystemChange);
            }
        }
    } catch (e) { /* ignore */ }

    /* keep other open tabs in sync */
    window.addEventListener('storage', function (e) {
        if (e.key === MODE_KEY) {
            applyMode(savedMode());
            modeChanged();
        } else if (e.key === ACCENT_KEY) {
            applyAccent(savedAccent());
            modeChanged();
        }
    });

    /* -------------------------------------------------- appearance menu */
    var ICONS = {
        auto: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 3a7 7 0 0 1 0 14z" fill="currentColor"/></svg>',
        light: '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 1.8v2.2M10 16v2.2M1.8 10H4M16 10h2.2M4.2 4.2l1.6 1.6M14.2 14.2l1.6 1.6M4.2 15.8l1.6-1.6M14.2 5.8l1.6-1.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
        dark: '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16.5 12.4A7 7 0 0 1 7.6 3.5a7 7 0 1 0 8.9 8.9z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>'
    };
    var LABELS = { auto: 'System', light: 'Light', dark: 'Dark' };

    var menuButton = null;
    var menuPanel = null;

    function el(tag, attrs, html) {
        var e = document.createElement(tag);
        Object.keys(attrs || {}).forEach(function (k) {
            e.setAttribute(k, attrs[k]);
        });
        if (html !== undefined) {
            e.innerHTML = html;
        }
        return e;
    }

    function updateMenuState() {
        if (!menuButton) {
            return;
        }
        var m = savedMode();
        menuButton.innerHTML = ICONS[effectiveMode()] || ICONS.light;
        menuButton.setAttribute('title', 'Appearance: ' + LABELS[m]);
        menuButton.setAttribute('aria-label', 'Appearance: ' + LABELS[m]);
        if (menuPanel) {
            var items = menuPanel.querySelectorAll('[data-us-mode]');
            for (var i = 0; i < items.length; i++) {
                var on = items[i].getAttribute('data-us-mode') === m;
                items[i].setAttribute('aria-checked', on ? 'true' : 'false');
                items[i].classList.toggle('is-active', on);
            }
            var mx = menuPanel.querySelector('[data-us-menu]');
            if (mx) {
                mx.setAttribute('aria-checked', menuExpanded() ? 'true' : 'false');
                mx.classList.toggle('is-active', menuExpanded());
            }
            var acc = savedAccent();
            var sw = menuPanel.querySelectorAll('[data-us-accent]');
            for (var j = 0; j < sw.length; j++) {
                var hit = sw[j].getAttribute('data-us-accent') === acc;
                sw[j].setAttribute('aria-checked', hit ? 'true' : 'false');
                sw[j].classList.toggle('is-active', hit);
            }
        }
    }

    function closeMenu() {
        if (menuPanel && !menuPanel.hidden) {
            menuPanel.hidden = true;
            menuButton.setAttribute('aria-expanded', 'false');
        }
    }

    function openMenu() {
        if (!menuPanel) {
            return;
        }
        menuPanel.hidden = false;
        menuButton.setAttribute('aria-expanded', 'true');
        var first = menuPanel.querySelector('.is-active') || menuPanel.querySelector('button');
        if (first) {
            first.focus();
        }
    }

    function buildMenu() {
        var wrap = el('div', { 'class': 'us-appearance' });
        menuButton = el('button', {
            'type': 'button',
            'class': 'us-appearance-btn',
            'aria-haspopup': 'true',
            'aria-expanded': 'false'
        });
        menuPanel = el('div', { 'class': 'us-appearance-menu', 'role': 'menu' });
        menuPanel.hidden = true;

        var html = '<div class="us-appearance-title">Appearance</div><div class="us-appearance-modes">';
        MODES.forEach(function (m) {
            html += '<button type="button" role="menuitemradio" data-us-mode="' + m + '">' +
                ICONS[m] + '<span>' + LABELS[m] + '</span></button>';
        });
        html += '</div><div class="us-appearance-title">Accent</div><div class="us-appearance-accents">';
        ACCENTS.forEach(function (a) {
            html += '<button type="button" role="menuitemradio" data-us-accent="' + a.value + '" title="' + a.name +
                '" aria-label="' + a.name + '" style="--us-swatch:' + a.value + '"></button>';
        });
        html += '</div><div class="us-appearance-title">Menu</div>' +
            '<button type="button" class="us-appearance-switch" role="menuitemcheckbox" data-us-menu="expanded">' +
            '<span>Keep all sections open</span><i class="us-sw" aria-hidden="true"></i></button>';
        menuPanel.innerHTML = html;

        wrap.appendChild(menuButton);
        wrap.appendChild(menuPanel);

        menuButton.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (menuPanel.hidden) {
                openMenu();
            } else {
                closeMenu();
            }
        });
        menuPanel.addEventListener('click', function (e) {
            e.stopPropagation();
            var t = e.target.closest ? e.target.closest('button') : null;
            if (!t) {
                return;
            }
            if (t.hasAttribute('data-us-mode')) {
                setMode(t.getAttribute('data-us-mode'));
            } else if (t.hasAttribute('data-us-accent')) {
                setAccent(t.getAttribute('data-us-accent'));
            } else if (t.hasAttribute('data-us-menu')) {
                setMenuExpanded(!menuExpanded());
            }
        });
        document.addEventListener('click', closeMenu);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' || e.keyCode === 27) {
                if (menuPanel && !menuPanel.hidden) {
                    closeMenu();
                    menuButton.focus();
                }
            }
        });
        return wrap;
    }

    /*
     * Regular pages: the control sits in the top bar's right-hand tools on
     * wide screens and next to the menu toggler on phones (core hides the
     * right-hand tools below 768px). Login page: floating in the corner.
     */
    var menuWrap = null;
    var menuItem = null;

    function placeMenu() {
        var header = document.querySelector('.page-head .navbar-header');
        var narrow = false;
        try {
            narrow = window.matchMedia('(max-width: 767px)').matches;
        } catch (e) {
            narrow = window.innerWidth < 768;
        }
        if (narrow && header) {
            var toggler = header.querySelector('.navbar-toggle');
            if (menuWrap.parentNode !== header) {
                header.insertBefore(menuWrap, toggler || null);
            }
        } else if (menuItem && menuWrap.parentNode !== menuItem) {
            menuItem.appendChild(menuWrap);
        }
    }

    function mountMenu() {
        if (document.querySelector('.us-appearance')) {
            return;
        }
        var navRight = document.querySelector('.page-head .navbar-right');
        if (navRight) {
            menuWrap = buildMenu();
            menuItem = el('li', { 'class': 'us-appearance-item' });
            navRight.insertBefore(menuItem, navRight.firstChild);
            placeMenu();
            try {
                var narrowMq = window.matchMedia('(max-width: 767px)');
                var onChange = function () {
                    closeMenu();
                    placeMenu();
                };
                if (narrowMq.addEventListener) {
                    narrowMq.addEventListener('change', onChange);
                } else if (narrowMq.addListener) {
                    narrowMq.addListener(onChange);
                }
            } catch (e) { /* ignore */ }
        } else if (document.body && document.body.classList.contains('page-login')) {
            menuWrap = buildMenu();
            menuWrap.classList.add('us-appearance-floating');
            document.body.appendChild(menuWrap);
        } else {
            return;
        }
        updateMenuState();
    }

    /* ------------------------------------------------- collapsed rail */
    /*
     * core opnsense_theme.js toggles .col-sidebar-left on #navigation; mirror
     * it on <html> so the top bar can follow with a plain class selector
     * (a body:has() selector is re-checked on every DOM change).
     */
    function mirrorRail() {
        var nav = document.getElementById('navigation');
        if (!nav) {
            return;
        }
        var wasRail = null;
        var sync = function () {
            var rail = nav.classList.contains('col-sidebar-left');
            root.classList.toggle('us-rail-collapsed', rail);
            if (wasRail !== null && wasRail !== rail) {
                applyMenuExpanded();
            }
            wasRail = rail;
        };
        sync();
        if (window.MutationObserver) {
            new MutationObserver(sync).observe(nav, { attributes: true, attributeFilter: ['class'] });
        }
    }

    /* ------------------------------------------------- expanded menu */
    /*
     * "Keep all sections open": every section and sub-section of the sidebar
     * is shown and the accordion links (data-parent) are set aside, so opening
     * one never closes another. Only in the full sidebar: the collapsed rail
     * uses the same panels as hover fly-outs (core closes them all on entry).
     */
    function menuExpanded() {
        return read(MENU_KEY) === 'expanded';
    }

    function railCollapsed() {
        var nav = document.getElementById('navigation');
        return !!(nav && nav.classList.contains('col-sidebar-left'));
    }

    function applyMenuExpanded() {
        var menu = document.getElementById('mainmenu');
        if (!menu) {
            return;
        }
        var on = menuExpanded() && !railCollapsed();
        root.classList.toggle('us-menu-expanded', on);
        var toggles = menu.querySelectorAll('a[data-toggle="collapse"]');
        for (var i = 0; i < toggles.length; i++) {
            var a = toggles[i];
            var target = document.querySelector(a.getAttribute('href') || '#none');
            if (on) {
                if (a.hasAttribute('data-parent')) {
                    a.setAttribute('data-us-parent', a.getAttribute('data-parent'));
                    a.removeAttribute('data-parent');
                }
                if (target) {
                    target.classList.add('in');
                    target.setAttribute('aria-expanded', 'true');
                    target.style.height = '';
                }
                a.classList.remove('collapsed');
                a.setAttribute('aria-expanded', 'true');
            } else if (a.hasAttribute('data-us-parent')) {
                /* back to the accordion: keep only the current page's sections open */
                a.setAttribute('data-parent', a.getAttribute('data-us-parent'));
                a.removeAttribute('data-us-parent');
                if (target && !target.querySelector('.active') && !target.classList.contains('active-menu')) {
                    target.classList.remove('in');
                    target.setAttribute('aria-expanded', 'false');
                    a.classList.add('collapsed');
                    a.setAttribute('aria-expanded', 'false');
                }
            }
        }
    }

    /*
     * Core's layout closes every open sub-section whenever a section header
     * is clicked (an accordion). With all sections kept open, a header click
     * should only fold or unfold its own section, so it is handled here, in
     * the capture phase, before core's and Bootstrap's handlers see it.
     */
    function guardMenuClicks() {
        var menu = document.getElementById('mainmenu');
        if (!menu || menu.__usGuard) {
            return;
        }
        menu.__usGuard = true;
        menu.addEventListener('click', function (e) {
            if (!root.classList.contains('us-menu-expanded')) {
                return;
            }
            var a = e.target.closest && e.target.closest('a[data-toggle="collapse"]');
            if (!a || !menu.contains(a)) {
                return;
            }
            var target = document.querySelector(a.getAttribute('href') || '#none');
            if (!target) {
                return;
            }
            e.preventDefault();
            e.stopImmediatePropagation();
            var open = !target.classList.contains('in');
            target.classList.toggle('in', open);
            target.style.height = '';
            target.setAttribute('aria-expanded', String(open));
            a.classList.toggle('collapsed', !open);
            a.setAttribute('aria-expanded', String(open));
            if (open) {
                target.classList.remove('us-sub-enter');
                void target.offsetWidth;
                target.classList.add('us-sub-enter');
                setTimeout(function () {
                    target.classList.remove('us-sub-enter');
                }, 260);
            }
        }, true);
    }

    function setMenuExpanded(on) {
        write(MENU_KEY, on ? 'expanded' : null);
        applyMenuExpanded();
        updateMenuState();
    }

    /* ------------------------------------------------- menu sections */
    /*
     * Bootstrap animates a section's height (350 ms) while the accordion
     * closes the previous one: two panels resize at once and the whole menu
     * slides under the pointer. Sections now open and close in one frame and
     * only the newly shown items fade/slide in (opacity + transform: GPU).
     */
    function smoothMenu() {
        var $ = window.jQuery;
        if (!$ || !$.fn || !$.fn.collapse || !$.fn.collapse.Constructor) {
            return;
        }
        $.fn.collapse.Constructor.TRANSITION_DURATION = 0;
        $(document).on('show.bs.collapse', '#mainmenu .collapse', function (e) {
            var panel = e.target;
            if (panel !== this) {
                return;
            }
            panel.classList.remove('us-sub-enter');
            void panel.offsetWidth;   /* restart the animation when reopened quickly */
            panel.classList.add('us-sub-enter');
            setTimeout(function () {
                panel.classList.remove('us-sub-enter');
            }, 260);
        });
    }

    /* ----------------------------------------------------------- credit */
    function creditHtml() {
        return 'Author <a href="' + REPO_URL + '" target="_blank" rel="noopener">Ox1d3x3</a>' +
            ' <span class="us-credit-x" aria-hidden="true">&times;</span> UniSense v' + VERSION;
    }

    function mountCredit() {
        if (document.querySelector('.us-credit')) {
            return;
        }
        var foot = document.querySelector('.page-foot > .container-fluid') || document.querySelector('.page-foot');
        var credit = el('span', { 'class': 'us-credit' }, creditHtml());
        if (foot) {
            foot.classList.add('us-foot');
            foot.appendChild(credit);
        } else if (document.body && document.body.classList.contains('page-login')) {
            credit.classList.add('us-credit-fixed');
            document.body.appendChild(credit);
        }
    }

    /* ---------------------------------------------------- sign-in page */
    var SVG_EYE = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M1.8 10S4.8 4.2 10 4.2 18.2 10 18.2 10 15.2 15.8 10 15.8 1.8 10 1.8 10z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><circle cx="10" cy="10" r="2.6" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
    var SVG_EYE_OFF = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 3l14 14M8.2 4.5A8.6 8.6 0 0 1 10 4.2c5.2 0 8.2 5.8 8.2 5.8a14 14 0 0 1-2.4 3.1M5.6 6A13.6 13.6 0 0 0 1.8 10S4.8 15.8 10 15.8c1.4 0 2.6-.4 3.7-1M8.3 8.4a2.6 2.6 0 0 0 3.4 3.4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    var SVG_USER = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="7" r="3.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M3.6 17c.9-3.2 3.4-5 6.4-5s5.5 1.8 6.4 5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
    var SVG_LOCK = '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="4" y="8.6" width="12" height="8.4" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M6.8 8.6V6.4a3.2 3.2 0 0 1 6.4 0v2.2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';

    /* wrap an input with an inline-SVG icon (no data: URLs: the sign-in page's CSP blocks them) */
    function fieldWrap(input, svg, extraClass) {
        var wrap = el('div', { 'class': 'us-field' + (extraClass ? ' ' + extraClass : '') });
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(el('span', { 'class': 'us-field-icon' }, svg));
        wrap.appendChild(input);
        return wrap;
    }

    function enhanceLogin() {
        var card = document.querySelector('body.page-login .login-modal-container');
        var content = card && card.querySelector('.login-modal-content');
        var form = content && content.querySelector('form');
        if (!form || card.classList.contains('us-login-card')) {
            return;
        }
        card.classList.add('us-login-card');
        root.classList.add('us-login');

        /* brand block: app icon, product, the host being signed in to */
        var logo = card.querySelector('.login-modal-head img');
        var icon = logo ? logo.getAttribute('src').replace(/default-logo\.svg/, 'icon-logo.svg') : '';
        var host = (window.location && window.location.hostname) || '';
        var brand = el('div', { 'class': 'us-login-brand' });
        brand.innerHTML = (icon && icon.indexOf('icon-logo.svg') !== -1 ? '<img class="us-login-icon" alt="" src="' + icon.replace(/"/g, '&quot;') + '">' : '') +
            '<div class="us-login-title">UniSense</div>' +
            '<div class="us-login-sub"></div>';
        brand.querySelector('.us-login-sub').textContent = host ? 'Sign in to ' + host : 'Sign in to continue';
        content.insertBefore(brand, content.firstChild);

        /* the server-side error line: only show it when it has text */
        var errors = content.querySelector('#inputerrors');
        if (errors && (errors.textContent || '').replace(/\u00a0/g, ' ').trim()) {
            errors.classList.add('us-has-error');
        }

        /* password: show/hide and Caps Lock hint */
        var user = form.querySelector('#usernamefld');
        if (user && user.parentNode) {
            fieldWrap(user, SVG_USER);
        }
        var pwd = form.querySelector('#passwordfld');
        if (pwd && pwd.parentNode) {
            var wrap = fieldWrap(pwd, SVG_LOCK, 'us-pwd');
            var eye = el('button', { 'type': 'button', 'class': 'us-pwd-toggle', 'aria-label': 'Show password', 'tabindex': '3' }, SVG_EYE);
            wrap.appendChild(eye);
            eye.addEventListener('click', function () {
                var show = pwd.getAttribute('type') === 'password';
                pwd.setAttribute('type', show ? 'text' : 'password');
                eye.innerHTML = show ? SVG_EYE_OFF : SVG_EYE;
                eye.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
                pwd.focus();
            });
            var caps = el('div', { 'class': 'us-caps', 'aria-live': 'polite' }, 'Caps Lock is on');
            caps.hidden = true;
            wrap.parentNode.appendChild(caps);
            var onKey = function (e) {
                if (e.getModifierState) {
                    caps.hidden = !e.getModifierState('CapsLock');
                }
            };
            pwd.addEventListener('keydown', onKey);
            pwd.addEventListener('keyup', onKey);
            pwd.addEventListener('blur', function () {
                caps.hidden = true;
            });
        }

        /*
         * busy state on submit. The button is NOT disabled: a disabled submit
         * button is left out of the form data and core checks for "login".
         */
        form.addEventListener('submit', function () {
            var btn = form.querySelector('button[type="submit"]');
            if (btn) {
                btn.classList.add('us-busy');
                btn.setAttribute('aria-busy', 'true');
            }
        });
    }

    /* ------------------------------------------------------------- boot */
    try {
        patchGauges();    /* BaseGaugeWidget.js loads before this file on the dashboard */
    } catch (e) { /* cosmetic only */ }

    function onReady() {
        installChartPlugin();
        try {
            patchGauges();
        } catch (e) { /* cosmetic only */ }
        patchSmoothie();
        refreshCharts();
        try {
            mountMenu();
        } catch (e) { /* cosmetic only */ }
        try {
            mirrorRail();
            smoothMenu();
            guardMenuClicks();
            applyMenuExpanded();
            mountCredit();
            enhanceLogin();
        } catch (e) { /* cosmetic only */ }
    }

    /* the dashboard grid settles while widgets load: animate only afterwards */
    function settled() {
        setTimeout(function () {
            root.classList.remove('us-loading');
        }, 1500);
    }
    if (document.readyState === 'complete') {
        settled();
    } else {
        window.addEventListener('load', settled);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', onReady);
    } else {
        onReady();
    }
    window.addEventListener('load', refreshCharts);

    /* small public API, e.g. for the browser console */
    window.UniSense = {
        getMode: savedMode,
        setMode: function (m) {
            if (MODES.indexOf(m) !== -1) {
                setMode(m);
            }
        },
        setAccent: function (a) {
            if (validAccent(a)) {
                setAccent(a.toLowerCase());
            }
        }
    };
})();
