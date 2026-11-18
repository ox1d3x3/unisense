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
(function () {
    'use strict';

    var VERSION = '1.1.0';   /* stamped from VERSION by scripts/build.mjs */
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
    function onReady() {
        installChartPlugin();
        patchSmoothie();
        refreshCharts();
        try {
            mountMenu();
        } catch (e) { /* cosmetic only */ }
        try {
            mirrorRail();
            smoothMenu();
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
