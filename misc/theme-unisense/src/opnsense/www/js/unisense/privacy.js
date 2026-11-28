/*
 * UniSense for OPNsense — Lobby: Privacy
 * (c) Ox1d3x3 — BSD 2-Clause License
 *
 * Ad and tracker blocking for every device: built in (a policy in Unbound's
 * own blocklist, with its query statistics) or an AdGuard Home server. One
 * dashboard either way: queries, share blocked, the last 24 hours, top
 * blocked / domains / devices, the query log with allow / block buttons, the
 * blocking level, your own rules, and protection against devices that try to
 * use their own DNS.
 *
 * Data: /api/unisense/privacy/* (models/OPNsense/Unisense/Privacy.php,
 * scripts/unisense/privacy.py).
 */
(function () {
    'use strict';

    var root = document.getElementById('upv');
    if (!root) {
        return;
    }

    var API = '/api/unisense/privacy/';
    var POLL_MS = 20000;

    var T = {};
    Object.keys(root.dataset).forEach(function (k) {
        if (k.indexOf('t') === 0 && k.length > 1) {
            T[k.charAt(1).toLowerCase() + k.slice(2)] = root.dataset[k];
        }
    });
    function t(key, fallback) {
        return T[key] || fallback;
    }

    /* OPNsense's API HTML-escapes some text; undo that first so it is escaped exactly once */
    function unescape(v) {
        return String(v).replace(/&(lt|gt|quot|#39|#039|amp);/g, function (m, e) {
            return { lt: '<', gt: '>', quot: '"', '#39': "'", '#039': "'", amp: '&' }[e];
        });
    }

    function esc(v) {
        return unescape(v === undefined || v === null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function num(n) {
        n = +n || 0;
        if (n >= 1000000) {
            return (n / 1000000).toFixed(1) + 'M';
        }
        return n >= 10000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + 'k' : String(n);
    }

    function setHtml(el, html) {
        if (el && el.__upvHtml !== html) {
            el.innerHTML = html;
            el.__upvHtml = html;
        }
    }

    function q(name) {
        return root.querySelector('[data-upv="' + name + '"]');
    }

    function clock(ts) {
        var d = new Date(ts * 1000);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function fetchJson(url, timeoutMs) {
        var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
        var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, timeoutMs || 30000) : null;
        return fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' }, signal: ctrl ? ctrl.signal : undefined })
            .then(function (r) {
                return r.ok ? r.json().catch(function () { return null; }) : null;
            })
            .catch(function () { return null; })
            .then(function (v) {
                if (timer) {
                    clearTimeout(timer);
                }
                return v;
            });
    }

    /* POST through jQuery so OPNsense's CSRF header is added */
    function post(url, body) {
        return new Promise(function (resolve) {
            if (!window.jQuery) {
                resolve(null);
                return;
            }
            window.jQuery.ajax({
                url: url, type: 'POST', dataType: 'json', data: JSON.stringify(body || {}),
                contentType: 'application/json; charset=utf-8', global: false, timeout: 120000,
                success: function (d) { resolve(d); },
                error: function () { resolve(null); }
            });
        });
    }

    var LEVELS = [
        ['light', 'Light', 'Ads and trackers that are clearly bad, plus the worst malware. Almost nothing breaks.'],
        ['balanced', 'Balanced', 'Ads, trackers, telemetry and malware (hagezi Pro + threat feeds). Recommended.'],
        ['strict', 'Strict', 'Everything in Balanced plus aggressive tracking and badware hosters. Some sites may need an allow.']
    ];
    var EXTRAS = {
        scams: ['Scams and fake shops', 'Known scam, fake-shop and phishing domains.'],
        popups: ['Pop-up ads', 'Pop-under and pop-up ad networks.'],
        gambling: ['Gambling', 'Betting and casino sites.'],
        social: ['Social networks', 'Facebook, Instagram, TikTok, X and others, for the whole network.'],
        adult: ['Adult content', 'Adult sites (OISD NSFW).'],
        bypass: ['DNS-over-HTTPS, VPN and proxy services', 'Stops apps and browsers using their own encrypted DNS. Also blocks public VPN and proxy services: leave off if you use one.']
    };

    var S = { settings: null, info: null, sum: null, log: null, names: {}, filter: 'all', search: '', timer: null, busy: false };

    /* ------------------------------------------------------------ skeleton */
    function card(name, title, sub, body, head) {
        return '<div class="utm-card ush-card" data-upv="' + name + '-card"><div class="utm-card-head"><b>' + esc(title) + '</b>' + (head || '') + '</div>' +
            (sub ? '<p class="ush-help">' + esc(sub) + '</p>' : '') + '<div data-upv="' + name + '">' + (body || '') + '</div></div>';
    }

    function build() {
        root.innerHTML =
            '<div class="utm-head"><span class="ush-state" data-upv="state"></span><span class="utm-db" data-upv="sub"></span>' +
            '<span class="utm-head-actions"><a class="btn btn-default btn-sm" data-upv="agh-link" target="_blank" rel="noopener" hidden>' +
            '<i class="fa fa-arrow-up-right-from-square fa-fw"></i> AdGuard Home</a></span></div>' +
            '<div class="utm-banner is-error" data-upv="banner" hidden></div>' +
            '<div data-upv="setup"></div>' +
            '<div data-upv="dash" hidden>' +
            '<div class="utm-stats ush-stats" data-upv="stats"></div>' +
            '<div class="utm-card upv-chart-card"><div class="utm-card-head"><b>' + esc(t('last24', 'last 24 hours')) + '</b>' +
            '<span class="upv-legend"><i class="is-total"></i>queries<i class="is-blocked"></i>blocked</span></div><div class="upv-chart" data-upv="chart"></div></div>' +
            '<div class="ush-grid"><div class="ush-col">' +
            '<div class="upv-pair">' + card('top-blocked', t('topBlocked', 'Top blocked'), '', '') + card('top-domains', t('topDomains', 'Top domains'), '', '') + '</div>' +
            card('log', t('log', 'Query log'), '', '',
                '<span class="ush-seg"><button type="button" class="is-active" data-upv-filter="all">All</button>' +
                '<button type="button" data-upv-filter="blocked">Blocked</button></span>' +
                '<input type="search" class="form-control upv-search" data-upv="search" placeholder="Filter by domain or device" aria-label="Filter the query log">') +
            card('clients', t('topClient', 'Busiest device') + 's', '', '') +
            '</div><div class="ush-col">' +
            card('settings', t('blocking', 'Blocking'), '', '') +
            card('rules', t('rules', 'Your rules'), 'Domains you allow are never blocked; domains you block are blocked with all their subdomains.', '') +
            card('bypass', t('bypass', 'Stop devices going around it'), 'Some devices and apps ignore your DNS: hard-coded servers (8.8.8.8), DNS-over-TLS, DNS-over-HTTPS.', '') +
            '</div></div></div>';
        root.addEventListener('click', onClick);
        root.addEventListener('change', onChange);
        root.addEventListener('input', function (e) {
            if (e.target === q('search')) {
                S.search = e.target.value.trim().toLowerCase();
                renderLog();
            }
        });
        root.addEventListener('submit', function (e) {
            e.preventDefault();
            var f = e.target.getAttribute('data-upv-form');
            if (f === 'rule') {
                var inp = e.target.querySelector('input');
                var act = (e.submitter && e.submitter.getAttribute('data-upv-act')) || 'block';
                domain(inp.value.trim(), act).then(function (ok) {
                    if (ok) {
                        inp.value = '';
                    }
                });
            } else if (f === 'bp') {
                save({ bp_address: e.target.bp_address.value.trim() }, 'bp-msg').then(loadBlockPage);
            } else if (f === 'agh') {
                saveAgh(e.target);
            }
        });
    }

    /* ------------------------------------------------------------ loading */
    function load() {
        return fetchJson(API + 'get', 30000).then(function (d) {
            if (!d || !d.settings) {
                var b = q('banner');
                b.hidden = false;
                setHtml(b, '<i class="fa fa-triangle-exclamation"></i> ' + esc(t('error', 'Could not read Privacy.')));
                return;
            }
            S.settings = d.settings;
            S.info = d.info;
            render();
        });
    }

    function loadData() {
        if (!S.settings || S.settings.provider === 'off') {
            return Promise.resolve();
        }
        return Promise.all([fetchJson(API + 'summary', 60000), fetchJson(API + 'log/300', 60000)]).then(function (r) {
            // a refresh that failed keeps the last good numbers on screen (the error shows in the banner)
            var good = S.sum && S.sum.ok && S.sum.provider === S.settings.provider;
            S.sumError = r[0] && r[0].ok ? '' : ((r[0] && r[0].error) || 'Could not read the statistics.');
            S.sum = r[0] && r[0].ok ? r[0] : (good ? S.sum : r[0]);
            S.log = r[1] && r[1].ok ? r[1].rows : (S.log || []);
            renderDash();
        });
    }

    function loadBlockPage() {
        if (!S.settings || !S.settings.bp_enabled) {
            return Promise.resolve();
        }
        return new Promise(function (r) {
            setTimeout(r, 800);       // give it a moment to (re)start
        }).then(function () {
            return fetchJson(API + 'blockpage', 15000);
        }).then(function (d) {
            S.bp = d || {};
            renderBypass();
        });
    }

    function loadNames() {
        Promise.all([fetchJson('/api/diagnostics/interface/get_arp', 20000), fetchJson('/api/unisense/networkmap/search_device')]).then(function (r) {
            var labels = {};
            ((r[1] && r[1].rows) || []).forEach(function (l) {
                if (l && l.mac && l.name) {
                    labels[String(l.mac).toLowerCase()] = l.name;
                }
            });
            (Array.isArray(r[0]) ? r[0] : ((r[0] && r[0].rows) || [])).forEach(function (a) {
                if (a && a.ip) {
                    var n = labels[String(a.mac || '').toLowerCase()] || a.hostname || '';
                    if (n && n !== '?') {
                        S.names[a.ip] = n;
                    }
                }
            });
            renderDash();
        });
    }

    function poll() {
        clearTimeout(S.timer);
        S.timer = setTimeout(function () {
            if (document.hidden || S.busy || root.offsetParent === null) {
                poll();
                return;
            }
            loadData().then(poll);
        }, POLL_MS);
    }

    /* ------------------------------------------------------------- render */
    function render() {
        var s = S.settings;
        var on = s.provider !== 'off';
        var st = q('state');
        st.className = 'ush-state ' + (on ? 'is-on' : 'is-off');
        setHtml(st, '<i class="fa ' + (on ? 'fa-user-shield' : 'fa-user') + ' fa-fw"></i><span>' +
            esc(on ? (s.provider === 'adguard' ? t('onAdguard', 'AdGuard Home') : t('onUnbound', 'Blocking ads and trackers')) : t('off', 'Ad blocking off')) + '</span>' +
            (on && s.provider === 'unbound' ? '<small>' + esc(levelName(s.level)) + '</small>' : ''));
        var link = q('agh-link');
        link.hidden = s.provider !== 'adguard' || !s.agh_url;
        if (!link.hidden) {
            link.href = s.agh_url;
        }
        q('dash').hidden = !on;
        renderSetup();
        if (on) {
            renderSettings();
            renderRules();
            renderBypass();
            renderDash();
        }
    }

    function levelName(l) {
        for (var i = 0; i < LEVELS.length; i++) {
            if (LEVELS[i][0] === l) {
                return LEVELS[i][1];
            }
        }
        return l;
    }

    function aghFields(s) {
        return '<form class="upv-agh" data-upv-form="agh">' +
            '<label class="ush-field"><span>Address</span><input class="form-control" name="agh_url" placeholder="http://192.168.1.5:3000" value="' + esc(s.agh_url) + '"></label>' +
            '<div class="upv-two"><label class="ush-field"><span>User name</span><input class="form-control" name="agh_user" autocomplete="off" value="' + esc(s.agh_user) + '"></label>' +
            '<label class="ush-field"><span>Password</span><input class="form-control" type="password" name="agh_pass" autocomplete="new-password" placeholder="' +
            (s.agh_pass ? '•••••••• saved' : '') + '"></label></div>' +
            '<div class="ush-row"><button type="submit" class="btn btn-primary btn-sm">Save and connect</button>' +
            '<span class="ush-msg" data-upv="agh-msg"></span></div></form>';
    }

    function renderSetup() {
        var s = S.settings;
        var el = q('setup');
        if (s.provider !== 'off') {
            setHtml(el, '');
            return;
        }
        var unboundOff = S.info && !S.info.unbound;
        setHtml(el, '<div class="utm-card ush-card upv-setup"><div class="utm-card-head"><b>' + esc(t('setup', 'Block ads and trackers for every device')) + '</b></div>' +
            '<p class="ush-help">' + esc(t('setupHelp', 'Choose where the blocking happens. Nothing changes in your DNS until you switch it on.')) + '</p>' +
            '<div class="upv-choices">' +
            '<div class="upv-choice is-recommended"><span class="upv-choice-tag">Recommended</span><b><i class="fa fa-shield-halved"></i> Built in</b>' +
            '<p>Blocking runs in this firewall\'s own DNS (Unbound): nothing to install, no extra box, a gaming-safe allow list, and a dashboard here.</p>' +
            (unboundOff ? '<p class="ush-err">Unbound DNS is switched off on this firewall: turn it on under Services: Unbound DNS first.</p>' : '') +
            '<button type="button" class="btn btn-primary btn-sm" data-upv-provider="unbound"' + (unboundOff ? ' disabled' : '') + '>Turn on</button></div>' +
            '<div class="upv-choice"><b><i class="fa fa-server"></i> AdGuard Home</b>' +
            '<p>Already run AdGuard Home on another machine? Connect it: its numbers, query log and allow / block show up here.</p>' + aghFields(s) + '</div>' +
            '</div><div class="ush-msg" data-upv="setup-msg"></div></div>');
    }

    function stat(icon, title, value, sub, cls) {
        return '<div class="utm-stat ush-stat ' + (cls || '') + '"><span class="utm-stat-icon"><i class="fa ' + icon + '"></i></span>' +
            '<span class="utm-stat-body"><span class="utm-stat-title">' + esc(title) + '</span><span class="utm-stat-value">' + esc(value) + '</span>' +
            '<span class="utm-stat-sub">' + esc(sub) + '</span></span></div>';
    }

    function devLabel(c) {
        var ip = c.ip || c.client;
        var name = c.name || S.names[ip] || (c.client !== ip ? c.client : '');
        return '<span class="utm-dev"><b>' + esc(name || ip) + '</b>' + (name && ip ? '<small>' + esc(ip) + '</small>' : '') + '</span>';
    }

    function renderDash() {
        var s = S.settings;
        var d = S.sum;
        if (!s || s.provider === 'off') {
            return;
        }
        var banner = q('banner');
        if (d && (!d.ok || S.sumError)) {
            banner.hidden = false;
            setHtml(banner, '<i class="fa fa-circle-info"></i> ' + esc(S.sumError || d.error || 'No data yet.'));
        } else {
            banner.hidden = true;
        }
        if (!d || !d.ok) {
            setHtml(q('stats'), '');
            setHtml(q('chart'), '<div class="utm-empty">…</div>');
            return;
        }
        var top = (d.top_clients || [])[0];
        setHtml(q('stats'),
            stat('fa-arrow-right-arrow-left', t('queries', 'DNS queries'), num(d.total), t('last24', 'last 24 hours'), 'is-in') +
            stat('fa-ban', t('blocked', 'Blocked'), num(d.blocked), d.pct + '% of all queries', 'is-out') +
            (d.provider === 'unbound' ? stat('fa-list-check', t('listed', 'Domains on block lists'), num(d.listed), levelName(s.level), 'is-list') :
                stat('fa-gauge-high', t('speed', 'Average answer'), d.ms + ' ms', d.protection ? 'filtering on' : 'filtering OFF', d.protection ? 'is-list' : 'is-alert')) +
            stat('fa-laptop', t('topClient', 'Busiest device'), top ? (top.name || S.names[top.client] || top.client) : '–',
                top ? num(top.count) + ' queries' : '', 'is-clear'));
        setHtml(q('sub'), d.provider === 'adguard' ? 'AdGuard Home ' + esc(d.version || '') : 'Unbound DNS · updated every ' + (POLL_MS / 1000) + ' s');
        // chart
        var ser = d.series || [];
        var max = Math.max.apply(null, ser.map(function (x) { return x.total; }).concat([1]));
        setHtml(q('chart'), ser.length ? '<div class="upv-bars">' + ser.map(function (x) {
            var h = Math.round(100 * x.total / max);
            var b = x.total ? Math.round(100 * x.blocked / x.total) : 0;
            var tm = new Date(x.t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            return '<span style="height:' + Math.max(2, h) + '%" title="' + esc(tm + ': ' + x.total + ' queries, ' + x.blocked + ' blocked') + '">' +
                '<i style="height:' + b + '%"></i></span>';
        }).join('') + '</div>' : '<div class="utm-empty">No queries yet.</div>');
        // tops
        setHtml(q('top-blocked'), list(d.top_blocked, 'allow'));
        setHtml(q('top-domains'), list(d.top_domains, 'block'));
        setHtml(q('clients'), (d.top_clients || []).length ? '<ul class="upv-list">' + d.top_clients.map(function (c) {
            return '<li>' + devLabel(c) + '<span class="upv-count">' + esc(num(c.count)) + '</span></li>';
        }).join('') + '</ul>' : '<div class="utm-empty">–</div>');
        renderLog();
    }

    function actBtn(dom, act, compact) {
        var label = (act === 'allow' ? 'Never block ' : 'Block ') + dom;
        return '<button type="button" class="btn btn-default btn-xs upv-act" data-upv-domain="' + esc(dom) + '" data-upv-act="' + act + '" title="' +
            esc(label) + '" aria-label="' + esc(label) + '">' + (act === 'allow' ? '<i class="fa fa-check"></i>' : '<i class="fa fa-ban"></i>') +
            (compact ? '' : (act === 'allow' ? ' Allow' : ' Block')) + '</button>';
    }

    function list(items, act) {
        if (!items || !items.length) {
            return '<div class="utm-empty">–</div>';
        }
        var max = Math.max.apply(null, items.map(function (x) { return x.count; }).concat([1]));
        return '<ul class="upv-list">' + items.map(function (x) {
            return '<li><span class="upv-dom"><b>' + esc(x.domain) + '</b>' + (x.list ? '<small>' + esc(x.list) + '</small>' : '') +
                '<i class="upv-meter ' + (act === 'allow' ? 'is-blocked' : '') + '" style="width:' + Math.round(100 * x.count / max) + '%"></i></span>' +
                '<span class="upv-count">' + esc(num(x.count)) + '</span>' + actBtn(x.domain, act) + '</li>';
        }).join('') + '</ul>';
    }

    function renderLog() {
        var rows = (S.log || []).filter(function (r) {
            if (S.filter === 'blocked' && !r.blocked) {
                return false;
            }
            if (S.search) {
                var hay = (r.domain + ' ' + r.client + ' ' + (r.ip || '') + ' ' + (S.names[r.ip || r.client] || '')).toLowerCase();
                return hay.indexOf(S.search) !== -1;
            }
            return true;
        }).slice(0, 150);
        setHtml(q('log'), rows.length ? '<div class="utm-table-wrap upv-log-wrap"><table class="utm-table ush-table"><thead><tr><th>Time</th><th>Device</th>' +
            '<th>Domain</th><th>Result</th><th></th></tr></thead><tbody>' + rows.map(function (r) {
            return '<tr class="' + (r.blocked ? 'is-blocked' : '') + '"><td class="ush-nowrap utm-muted">' + esc(clock(r.t)) + '</td>' +
                '<td>' + devLabel(r) + '</td><td class="upv-domain">' + esc(r.domain) + (r.type ? '<small>' + esc(r.type) + '</small>' : '') + '</td>' +
                '<td>' + (r.blocked ? '<span class="ush-tag is-both">Blocked</span>' + (r.list ? '<small>' + esc(r.list) + '</small>' : '') :
                    '<span class="utm-muted">' + esc(r.source || 'Allowed') + (r.ms ? ' · ' + esc(r.ms) + ' ms' : '') + '</span>') + '</td>' +
                '<td class="ush-c-act">' + actBtn(r.domain, r.blocked ? 'allow' : 'block', true) + '</td></tr>';
        }).join('') + '</tbody></table></div>' : '<div class="utm-empty">No queries' + (S.search || S.filter !== 'all' ? ' match.' : ' yet.') + '</div>');
    }

    function sw(name, on, disabled, title) {
        return '<label class="ush-switch ush-switch-sm"' + (title ? ' title="' + esc(title) + '"' : '') + '><input type="checkbox" data-upv-set="' + name + '"' +
            (on ? ' checked' : '') + (disabled ? ' disabled' : '') + '><span></span></label>';
    }

    function renderSettings() {
        var s = S.settings;
        var info = S.info || { extras: [] };
        var html = '<div class="upv-provider"><span>Provider</span><span class="ush-seg">' +
            [['unbound', 'Built in'], ['adguard', 'AdGuard Home'], ['off', 'Off']].map(function (p) {
                return '<button type="button" data-upv-provider="' + p[0] + '"' + (s.provider === p[0] ? ' class="is-active"' : '') + '>' + p[1] + '</button>';
            }).join('') + '</span></div>';
        if (S.aghForm && s.provider !== 'adguard') {
            html += '<p class="upv-note">Enter your AdGuard Home server. Blocking moves there when you save; until then nothing changes.</p>' + aghFields(s);
        }
        if (s.provider === 'unbound') {
            html += '<div class="upv-levels">' + LEVELS.map(function (l) {
                return '<button type="button" class="upv-level' + (s.level === l[0] ? ' is-active' : '') + '" data-upv-level="' + l[0] + '"><b>' + esc(l[1]) + '</b><small>' + esc(l[2]) + '</small></button>';
            }).join('') + '</div>';
            html += '<ul class="ush-feeds">' + Object.keys(EXTRAS).filter(function (x) {
                return info.extras.indexOf(x) !== -1;
            }).map(function (x) {
                var on = s.extras.indexOf(x) !== -1;
                return '<li class="ush-feed' + (on ? ' is-on' : '') + '">' + sw('extra:' + x, on) +
                    '<span class="ush-feed-body"><span class="ush-feed-name">' + esc(EXTRAS[x][0]) + '</span><small>' + esc(EXTRAS[x][1]) + '</small></span></li>';
            }).join('') + '<li class="ush-feed' + (s.gaming ? ' is-on' : '') + '">' + sw('gaming', s.gaming) +
                '<span class="ush-feed-body"><span class="ush-feed-name"><i class="fa fa-gamepad"></i> Gaming-safe</span><small>' +
                esc(t('gaming', 'Logins, stores, matchmaking, anti-cheat and voice are never blocked.')) + '</small></span></li></ul>' +
                '<p class="upv-note">Shown in <i>Services: Unbound DNS: Blocklist</i> as the policy “UniSense Privacy”' +
                (S.info && S.info.policies > 1 ? '; your other policies there keep working.' : '.') + ' Lists are downloaded and refreshed by Unbound.</p>';
        } else if (s.provider === 'adguard') {
            var d = S.sum || {};
            html += aghFields(s) +
                '<ul class="ush-feeds"><li class="ush-feed' + (d.protection ? ' is-on' : '') + '">' + sw('protection', !!d.protection, !d.ok) +
                '<span class="ush-feed-body"><span class="ush-feed-name">Filtering</span><small>Switch AdGuard Home\'s blocking on or off.</small></span></li></ul>' +
                '<div class="ush-row"><button type="button" class="btn btn-default btn-sm" data-upv-action="gaming"><i class="fa fa-gamepad"></i> Add the gaming-safe allow rules</button>' +
                '<span class="ush-msg" data-upv="gaming-msg"></span></div>' +
                '<p class="upv-note">Block lists are chosen in AdGuard Home itself.</p>';
        }
        setHtml(q('settings'), html + '<div class="ush-msg" data-upv="settings-msg"></div>');
    }

    function renderRules() {
        var s = S.settings;
        var chips = function (arr, act) {
            return arr.map(function (dmn) {
                return '<span class="ush-chip upv-chip is-' + act + '"><i class="fa ' + (act === 'allow' ? 'fa-check' : 'fa-ban') + '"></i><span>' + esc(dmn) + '</span>' +
                    '<button type="button" class="ush-chip-x" data-upv-domain="' + esc(dmn) + '" data-upv-act="remove" aria-label="Remove ' + esc(dmn) + '"><i class="fa fa-xmark"></i></button></span>';
            }).join('');
        };
        var aghNote = s.provider === 'adguard' ? '<p class="upv-note">With AdGuard Home, allow and block go into its own custom rules.</p>' : '';
        setHtml(q('rules'), (s.provider === 'unbound' ? '<div class="ush-chips">' + (chips(s.allow, 'allow') + chips(s.block, 'block') ||
            '<span class="utm-muted">No rules yet. Use Allow or Block on a domain, or add one here.</span>') + '</div>' : aghNote) +
            '<form class="ush-add" data-upv-form="rule"><input type="text" class="form-control" placeholder="example.com" aria-label="Domain" autocomplete="off" spellcheck="false">' +
            '<button type="submit" class="btn btn-default btn-sm" data-upv-act="allow"><i class="fa fa-check"></i> Allow</button>' +
            '<button type="submit" class="btn btn-default btn-sm" data-upv-act="block"><i class="fa fa-ban"></i> Block</button></form>' +
            '<div class="ush-msg" data-upv="rules-msg"></div>');
    }

    function renderBypass() {
        var s = S.settings;
        var info = S.info || { interfaces: [] };
        var unb = s.provider === 'unbound';
        var noRdr = !info.redirect;
        setHtml(q('bypass'), '<div class="upv-ifs"><span>On</span>' + info.interfaces.map(function (i) {
            return '<label class="upv-if"><input type="checkbox" data-upv-if="' + esc(i.name) + '"' + (s.interfaces.indexOf(i.name) !== -1 ? ' checked' : '') + '> ' + esc(i.descr) + '</label>';
        }).join('') + '</div><ul class="ush-feeds">' +
            '<li class="ush-feed' + (s.redirect && unb ? ' is-on' : '') + '">' + sw('redirect', s.redirect && unb, !unb || noRdr) +
            '<span class="ush-feed-body"><span class="ush-feed-name">Send all plain DNS to this firewall</span><small>' +
            (noRdr ? 'Needs OPNsense 26.1 or newer.' : unb ? 'Devices that hard-code 8.8.8.8 or 1.1.1.1 are answered by this firewall instead, so blocking still applies.' :
                'Built in only: with AdGuard Home, point your DHCP DNS at it instead.') + '</small></span></li>' +
            '<li class="ush-feed' + (s.dot ? ' is-on' : '') + '">' + sw('dot', s.dot) +
            '<span class="ush-feed-body"><span class="ush-feed-name">Refuse DNS-over-TLS to other servers</span><small>Port 853 is only allowed to ' +
            (unb ? 'this firewall' : 'your AdGuard Home') + '. Phones (Android Private DNS) then fall back to your DNS.</small></span></li></ul>' +
            (unb ? '<p class="upv-note">DNS-over-HTTPS hides inside normal web traffic: switch on “DNS-over-HTTPS, VPN and proxy services” under Blocking to stop it.</p>' : '') + '<div class="ush-msg" data-upv="bypass-msg"></div>' +
            (unb ? blockPage() : ''));
    }

    function blockPage() {
        var s = S.settings;
        var info = S.info || {};
        var host = 'unblock.' + (info.domain || 'home.arpa');
        var st = S.bp || {};
        return '<div class="upv-bp"><h4><i class="fa fa-hand"></i> Block page</h4><ul class="ush-feeds">' +
            '<li class="ush-feed' + (s.bp_enabled ? ' is-on' : '') + '">' + sw('bp_enabled', s.bp_enabled) +
            '<span class="ush-feed-body"><span class="ush-feed-name">Show a page instead of a broken site</span><small>' +
            'Blocked websites show “Stopped by UniSense Shield”, the reason and a Request to unblock button. Requests arrive in the Requests tab.' +
            '</small></span></li></ul>' +
            (s.bp_enabled ? '<form class="upv-bp-form" data-upv-form="bp">' +
                '<label class="ush-field"><span>Address</span><input class="form-control" name="bp_address" value="' + esc(s.bp_address) + '" spellcheck="false">' +
                '<small>Only the firewall answers on it. Change it if it is on one of your networks.</small></label>' +
                '<label class="ush-field"><span>Certificate authority for HTTPS</span><select class="form-control" data-upv-ca>' +
                '<option value="">None: HTTP only (HTTPS sites show the browser\'s own error)</option>' +
                (info.cas || []).map(function (c) {
                    return '<option value="' + esc(c.refid) + '"' + (c.refid === s.bp_ca ? ' selected' : '') + '>' + esc(c.descr) + '</option>';
                }).join('') + '</select><small>Devices that trust this CA see the page on HTTPS sites too. Others see a certificate warning.</small></label>' +
                '<div class="ush-row"><button type="submit" class="btn btn-default btn-sm">Save address</button>' +
                '<a class="btn btn-default btn-sm" href="http://' + esc(host) + '/" target="_blank" rel="noopener"><i class="fa fa-arrow-up-right-from-square"></i> ' + esc(host) + '</a>' +
                '<span class="utm-muted upv-bp-status">' + (st.running ? '<i class="fa fa-circle is-ok"></i> running' + (st.https ? ', HTTP and HTTPS' : ', HTTP only') :
                    (st.ok ? '<i class="fa fa-circle is-off"></i> not running' : '')) + '</span></div></form>' : '') +
            '<div class="ush-msg" data-upv="bp-msg"></div></div>';
    }

    /* ------------------------------------------------------------- actions */
    function msg(name, text, cls) {
        var el = q(name);
        if (el) {
            el.className = 'ush-msg' + (cls ? ' ' + cls : '');
            el.textContent = text || '';
        }
    }

    function errText(r) {
        if (r && r.validations) {
            return Object.keys(r.validations).map(function (k) { return r.validations[k]; }).join(' ');
        }
        return (r && r.error) || 'Could not save.';
    }

    function save(body, where) {
        S.busy = true;
        msg(where, 'Saving…');
        return post(API + 'set', { privacy: body }).then(function (r) {
            S.busy = false;
            if (r && r.result === 'saved') {
                S.settings = r.settings;
                render();
                msg(where, (r.applied || []).indexOf('dnsbl') !== -1 ? 'Saved. Unbound is loading the lists (a minute the first time).' : 'Saved.', 'is-ok');
                setTimeout(loadData, 1500);
                return true;
            }
            msg(where, errText(r), 'is-error');
            return false;
        });
    }

    function saveAgh(form) {
        var body = { provider: 'adguard', agh_url: form.agh_url.value.trim(), agh_user: form.agh_user.value.trim() };
        if (form.agh_pass.value) {
            body.agh_pass = form.agh_pass.value;
        }
        msg('agh-msg', 'Connecting…');
        save(body, 'agh-msg').then(function (ok) {
            if (!ok) {
                return;
            }
            S.aghForm = false;
            fetchJson(API + 'test', 20000).then(function (r) {
                msg('agh-msg', r && r.ok ? 'Connected to AdGuard Home ' + (r.version || '') + '.' : (r && r.error) || 'Could not connect.', r && r.ok ? 'is-ok' : 'is-error');
                loadData();
            });
        });
    }

    function domain(dmn, act) {
        if (!/^[a-z0-9_.-]+\.[a-z0-9-]{2,}$/i.test(dmn || '')) {
            msg('rules-msg', 'Type a domain such as example.com.', 'is-error');
            return Promise.resolve(false);
        }
        msg('rules-msg', (act === 'allow' ? 'Allowing ' : act === 'block' ? 'Blocking ' : 'Removing ') + dmn + '…');
        return post(API + 'domain', { domain: dmn, action: act }).then(function (r) {
            if (r && r.ok) {
                if (r.settings) {
                    S.settings = r.settings;
                    renderRules();
                }
                msg('rules-msg', dmn + (act === 'allow' ? ' allowed.' : act === 'block' ? ' blocked.' : ' removed.'), 'is-ok');
                setTimeout(loadData, 1500);
                return true;
            }
            msg('rules-msg', errText(r), 'is-error');
            return false;
        });
    }

    function onChange(e) {
        var el = e.target;
        var set = el.getAttribute && el.getAttribute('data-upv-set');
        if (set) {
            if (set === 'protection') {
                post(API + 'protection', { on: el.checked ? 1 : 0 }).then(loadData);
            } else if (set.indexOf('extra:') === 0) {
                var x = set.slice(6);
                var ex = S.settings.extras.filter(function (v) { return v !== x; });
                if (el.checked) {
                    ex.push(x);
                }
                save({ extras: ex }, 'settings-msg');
            } else {
                var b = {};
                b[set] = el.checked ? '1' : '0';
                save(b, set === 'gaming' ? 'settings-msg' : set === 'bp_enabled' ? 'bp-msg' : 'bypass-msg').then(set === 'bp_enabled' ? loadBlockPage : null);
            }
            return;
        }
        if (el.hasAttribute && el.hasAttribute('data-upv-ca')) {
            save({ bp_ca: el.value }, 'bp-msg').then(loadBlockPage);
            return;
        }
        var ifn = el.getAttribute && el.getAttribute('data-upv-if');
        if (ifn) {
            var ifs = S.settings.interfaces.filter(function (v) { return v !== ifn; });
            if (el.checked) {
                ifs.push(ifn);
            }
            save({ interfaces: ifs }, 'bypass-msg');
        }
    }

    function onClick(e) {
        var c = function (sel) {
            return e.target.closest && e.target.closest(sel);
        };
        var p = c('[data-upv-provider]');
        if (p) {
            var prov = p.getAttribute('data-upv-provider');
            if (prov === 'adguard' && S.settings.provider !== 'adguard' && !S.settings.agh_url) {
                // nothing to connect to yet: ask for it first instead of switching the blocking off
                S.aghForm = true;
                renderSettings();
                var f = root.querySelector('[data-upv-form="agh"]');
                if (f) {
                    f.agh_url.focus();
                }
                return;
            }
            S.aghForm = false;
            if (prov === 'off' && !window.confirm('Switch ad blocking off? Your rules and settings are kept.')) {
                return;
            }
            save({ provider: prov }, S.settings.provider === 'off' ? 'setup-msg' : 'settings-msg').then(function () {
                loadData();
            });
            return;
        }
        var lv = c('[data-upv-level]');
        if (lv) {
            save({ level: lv.getAttribute('data-upv-level') }, 'settings-msg');
            return;
        }
        var dm = c('[data-upv-domain]');
        if (dm) {
            domain(dm.getAttribute('data-upv-domain'), dm.getAttribute('data-upv-act'));
            return;
        }
        var fl = c('[data-upv-filter]');
        if (fl) {
            S.filter = fl.getAttribute('data-upv-filter');
            root.querySelectorAll('[data-upv-filter]').forEach(function (b) {
                b.classList.toggle('is-active', b === fl);
            });
            renderLog();
            return;
        }
        var a = c('[data-upv-action]');
        if (a && a.getAttribute('data-upv-action') === 'gaming') {
            msg('gaming-msg', 'Adding…');
            post(API + 'gaming', {}).then(function (r) {
                msg('gaming-msg', r && r.ok ? (r.added ? r.added + ' allow rules added.' : 'Already there.') : (r && r.error) || 'Could not add them.', r && r.ok ? 'is-ok' : 'is-error');
            });
        }
    }

    /* ------------------------------------------------------------- start */
    var started = false;

    function start() {
        if (started) {
            return;
        }
        started = true;
        build();
        load().then(function () {
            loadBlockPage();
            return loadData();
        }).then(poll);
        loadNames();
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden && root.offsetParent !== null) {
                loadData();
            }
        });
    }

    // inside Shield's Privacy tab: start the first time the tab is opened
    window.UniSense = window.UniSense || {};
    window.UniSense.privacyStart = start;
    if (!root.closest('[data-ush-pane][hidden]')) {
        start();
    }
}());
