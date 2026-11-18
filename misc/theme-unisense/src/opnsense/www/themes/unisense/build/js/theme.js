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
 *   4. keep Chart.js dashboard widgets in step with the colour mode.
 *
 * Nothing here is required for the GUI to work: with JavaScript disabled or
 * storage blocked the theme simply follows the operating system.
 */
(function () {
    'use strict';

    var MODE_KEY = 'unisense.mode';
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
    var CHART_MAP = { '#D94F00': '--us-chart-1', '#A8C49B': '--us-chart-2', '#E5E5E5': '--us-chart-track' };
    var paletteCache = {};
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

    function installChartPlugin() {
        var C = window.Chart;
        if (!C || typeof C.register !== 'function' || installChartPlugin.done) {
            return;
        }
        installChartPlugin.done = true;
        try {
            C.register({
                id: 'unisenseColors',
                beforeUpdate: function (chart) {
                    try {
                        var sets = (chart && chart.data && chart.data.datasets) || [];
                        for (var i = 0; i < sets.length; i++) {
                            recolorDataset(sets[i]);
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

    function modeChanged() {
        paletteCache = {};
        /* wait one frame so the new custom properties are computed */
        var raf = window.requestAnimationFrame || function (fn) { return setTimeout(fn, 16); };
        raf(function () {
            refreshCharts();
            updateMenuState();
        });
    }

    function setMode(m) {
        write(MODE_KEY, m === 'auto' ? null : m);
        applyMode(m);
        modeChanged();
    }

    function setAccent(a) {
        write(ACCENT_KEY, a === DEFAULT_ACCENT ? null : a);
        applyAccent(a);
        modeChanged();
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
        html += '</div>';
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

    /* ------------------------------------------------------------- boot */
    function onReady() {
        installChartPlugin();
        patchSmoothie();
        refreshCharts();
        try {
            mountMenu();
        } catch (e) { /* cosmetic only */ }
        /* enable transitions only after the first paint, avoiding a fade on load */
        setTimeout(function () {
            root.classList.add('us-ready');
        }, 150);
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
