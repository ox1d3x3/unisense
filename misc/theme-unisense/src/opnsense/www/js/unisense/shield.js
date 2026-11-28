/*
 * UniSense for OPNsense — Lobby: Shield
 * (c) Ox1d3x3 — BSD 2-Clause License
 *
 * Country blocking and automatically refreshed threat lists, with what they
 * blocked, alerts for devices that tried to reach a known-bad address, an
 * address check (lists, country, network and optional VirusTotal / AbuseIPDB
 * reputation) and a short security checkup.
 *
 * Data: /api/unisense/shield/* (models/OPNsense/Unisense/Shield.php for the
 * firewall rules, scripts/unisense/shield.py for the tables and the log).
 */
(function () {
    'use strict';

    var root = document.getElementById('ush');
    if (!root) {
        return;
    }

    var POLL_MS = 15000;
    var FLAG_DIR = '/ui/css/unisense/flags/';
    var API = '/api/unisense/shield/';

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
        return n >= 10000 ? (n / 1000).toFixed(n >= 100000 ? 0 : 1) + 'k' : String(n);
    }

    var regionNames = null;
    try {
        regionNames = new Intl.DisplayNames([document.documentElement.lang || navigator.language || 'en', 'en'], { type: 'region' });
    } catch (e) { /* older browsers: show the code */ }
    function countryName(cc) {
        if (!cc || cc === '??') {
            return 'Unknown';
        }
        try {
            return (regionNames && regionNames.of(cc)) || cc;
        } catch (e) {
            return cc;
        }
    }

    function flag(cc) {
        if (!/^[A-Z]{2}$/.test(cc || '')) {
            return '<span class="utm-flag utm-flag-none"><i class="fa fa-question"></i></span>';
        }
        return '<img class="utm-flag" alt="" loading="lazy" src="' + FLAG_DIR + cc + '.svg">';
    }

    function setHtml(el, html) {
        if (el && el.__ushHtml !== html) {
            el.innerHTML = html;
            el.__ushHtml = html;
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

    function clock(ts) {
        var d = new Date(ts * 1000);
        var today = new Date();
        var hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        return d.toDateString() === today.toDateString() ? hm : d.toLocaleDateString([], { day: 'numeric', month: 'short' }) + ' ' + hm;
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
    function post(url, body) {
        return new Promise(function (resolve) {
            if (!window.jQuery) {
                resolve(null);
                return;
            }
            window.jQuery.ajax({
                url: url, type: 'POST', dataType: 'json', data: JSON.stringify(body || {}),
                contentType: 'application/json; charset=utf-8', global: false, timeout: 300000,
                success: function (d) { resolve(d); },
                error: function () { resolve(null); }
            });
        });
    }

    function q(name) {
        return root.querySelector('[data-ush="' + name + '"]');
    }

    /* every ISO 3166 country, for the "block a country" picker */
    var CODES = ('AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ ' +
        'CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB ' +
        'GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH ' +
        'KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX ' +
        'MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE ' +
        'SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC ' +
        'VE VG VI VN VU WF WS XK YE YT ZA ZM ZW').split(' ');

    var KIND = {
        'country-in': { icon: 'fa-earth-americas', label: 'Country', dir: 'in' },
        'country-out': { icon: 'fa-earth-americas', label: 'Country', dir: 'out' },
        'threat-in': { icon: 'fa-skull-crossbones', label: 'Threat list', dir: 'in' },
        'threat-out': { icon: 'fa-skull-crossbones', label: 'Threat list', dir: 'out' },
        'scanner-in': { icon: 'fa-satellite-dish', label: 'Scanner list', dir: 'in' }
    };

    /* -------------------------------------------------------------- state */
    var S = {
        settings: null,
        status: null,
        hits: null,
        alerts: null,
        checkup: null,
        names: null,
        failed: false,
        busy: {},
        timer: null,
        check: null,
        checkIp: '',
        filter: 'all',
        dismissed: {},
        acked: 0
    };

    var menu = null;   /* the open "Allow for …" menu */

    /* ------------------------------------------------------------ skeleton */
    function card(name, title, sub, body, extraHead) {
        return '<div class="utm-card ush-card" data-ush="' + name + '-card">' +
            '<div class="utm-card-head"><b>' + esc(title) + '</b>' + (extraHead || '') + '</div>' +
            (sub ? '<p class="ush-help">' + esc(sub) + '</p>' : '') +
            '<div data-ush="' + name + '">' + (body || '') + '</div></div>';
    }

    function build() {
        root.innerHTML =
            '<div class="utm-head">' +
            '<span class="ush-state" data-ush="state"></span>' +
            '<span class="utm-db" data-ush="updated"></span>' +
            '<span class="utm-head-actions"><button type="button" class="btn btn-default btn-sm" data-ush-action="update">' +
            '<i class="fa fa-rotate fa-fw"></i> <span>' + esc(t('updateNow', 'Update lists now')) + '</span></button></span></div>' +
            '<div class="utm-banner is-error" data-ush="banner" hidden></div>' +
            '<div class="utm-stats ush-stats" data-ush="stats"></div>' +
            '<div class="ush-grid">' +
            '<div class="ush-col">' +
            '<div class="utm-card ush-card ush-alerts" data-ush="alerts-card" hidden>' +
            '<div class="utm-card-head"><b><i class="fa fa-triangle-exclamation"></i> ' + esc(t('alertsTitle', 'Devices that tried to reach a known-bad address')) + '</b>' +
            '<button type="button" class="btn btn-default btn-xs ush-head-btn" data-ush-action="ack" data-ush="ack">' + esc(t('markSeen', 'Mark as seen')) + '</button></div>' +
            '<p class="ush-help">' + esc(t('alertsHelp', 'Shield refused these connections. A device that keeps trying may be infected: check it, update it, or scan it.')) + '</p>' +
            '<div data-ush="alerts"></div></div>' +
            card('recent', t('recent', 'Recent blocks'), '', '',
                '<span class="ush-seg" role="tablist">' +
                '<button type="button" class="is-active" data-ush-filter="all">All</button>' +
                '<button type="button" data-ush-filter="out">Your devices</button>' +
                '<button type="button" data-ush-filter="in">From the Internet</button></span>') +
            card('check', t('check', 'Check an address'), t('checkHelp', 'Country, network, block lists and reputation of any IP address.'),
                '<form class="ush-check-form" data-ush="check-form"><input type="text" class="form-control" data-ush="check-ip" ' +
                'placeholder="203.0.113.7" aria-label="IP address" autocomplete="off" spellcheck="false">' +
                '<button type="submit" class="btn btn-primary btn-sm"><i class="fa fa-magnifying-glass fa-fw"></i> Check</button></form>' +
                '<div data-ush="check-result"></div>') +
            card('checkup', t('checkup', 'Security checkup'), '', '') +
            '</div>' +
            '<div class="ush-col">' +
            card('lists', t('lists', 'Threat lists'), t('listsHelp', 'Free public lists of botnet servers, criminal networks and attackers, refreshed automatically.'), '',
                '<label class="ush-switch" title="Threat lists on or off"><input type="checkbox" data-ush="enabled"><span></span></label>') +
            card('countries', t('countries', 'Blocked countries'), t('countriesHelp', 'Connections from these countries are dropped, and your devices cannot connect to them.'),
                '<div class="ush-chips" data-ush="chips"></div>' +
                '<form class="ush-add" data-ush="add-form"><input type="text" class="form-control" list="ush-cc" data-ush="add-cc" ' +
                'placeholder="' + esc(t('pickCountry', 'Country to block')) + '" aria-label="' + esc(t('pickCountry', 'Country to block')) + '" autocomplete="off">' +
                '<datalist id="ush-cc" data-ush="cc-list"></datalist>' +
                '<button type="submit" class="btn btn-default btn-sm"><i class="fa fa-ban fa-fw"></i> ' + esc(t('block', 'Block')) + '</button></form>' +
                '<div class="ush-msg" data-ush="add-msg"></div>') +
            card('allow', t('allow', 'Allowed addresses'), t('allowHelp', 'Addresses Shield lets through even when a list or a blocked country includes them. Use Allow on a block or alert for a set time; the box below is for addresses that should never be blocked (one per line).'),
                '<ul class="ush-allows" data-ush="allows"></ul>' +
                '<textarea class="form-control ush-textarea" rows="3" data-ush="allowlist" spellcheck="false" placeholder="203.0.113.7&#10;198.51.100.0/24"></textarea>' +
                '<div class="ush-row"><button type="button" class="btn btn-default btn-sm" data-ush-action="save-allow">' + esc(t('save', 'Save')) + '</button>' +
                '<span class="ush-msg" data-ush="allow-msg"></span></div>') +
            card('keys', t('keys', 'Reputation lookups'), t('keysHelp', 'Optional free API keys: the address check then also shows VirusTotal and AbuseIPDB scores. Keys stay on the firewall.'),
                '<label class="ush-field"><span>VirusTotal <a href="https://www.virustotal.com/gui/join-us" target="_blank" rel="noopener">get a free key</a></span>' +
                '<input type="password" class="form-control" data-ush="vtkey" autocomplete="off"></label>' +
                '<label class="ush-field"><span>AbuseIPDB <a href="https://www.abuseipdb.com/register" target="_blank" rel="noopener">get a free key</a></span>' +
                '<input type="password" class="form-control" data-ush="abuseipdbkey" autocomplete="off"></label>' +
                '<div class="ush-row"><button type="button" class="btn btn-default btn-sm" data-ush-action="save-keys">' + esc(t('save', 'Save')) + '</button>' +
                '<span class="ush-msg" data-ush="keys-msg"></span></div>') +
            '</div></div>' +
            '<div class="ush-note"><i class="fa fa-gamepad"></i><span>' + esc(t('gaming', 'Gaming-safe: Shield only adds lookups in the firewall address tables. Nothing is inspected or slowed down, and scanner lists never touch your outgoing connections.')) + '</span></div>' +
            '<div class="utm-attrib">Lists: <a href="https://www.spamhaus.org/drop/" target="_blank" rel="noopener">Spamhaus DROP</a> · ' +
            '<a href="https://abuse.ch" target="_blank" rel="noopener">abuse.ch</a> · ' +
            '<a href="https://rules.emergingthreats.net/" target="_blank" rel="noopener">Emerging Threats</a> · ' +
            '<a href="https://cinsscore.com/" target="_blank" rel="noopener">CINS Army</a> · ' +
            '<a href="https://isc.sans.edu/" target="_blank" rel="noopener">DShield</a> · countries: ' +
            '<a href="https://db-ip.com" target="_blank" rel="noopener">DB-IP</a> (CC BY 4.0)</div>';

        q('lists-card').insertAdjacentHTML('beforeend', '<div class="ush-msg" data-ush="lists-msg"></div>');
        var list = q('cc-list');
        list.innerHTML = CODES.map(function (cc) {
            return '<option value="' + esc(countryName(cc) + ' (' + cc + ')') + '">';
        }).join('');

        root.addEventListener('click', onClick);
        document.addEventListener('click', function (e) {
            if (menu && !root.contains(e.target)) {
                closeMenu();
            }
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                closeMenu();
            }
        });
        root.addEventListener('change', onChange);
        q('add-form').addEventListener('submit', function (e) {
            e.preventDefault();
            addCountry();
        });
        q('check-form').addEventListener('submit', function (e) {
            e.preventDefault();
            runCheck(q('check-ip').value.trim());
        });
        root.addEventListener('error', function (e) {
            if (e.target && e.target.classList && e.target.classList.contains('utm-flag')) {
                e.target.classList.add('is-missing');
            }
        }, true);
    }

    /* ------------------------------------------------------------ loading */
    function load() {
        return fetchJson(API + 'get', 60000).then(function (d) {
            if (!d || !d.settings) {
                S.failed = true;
                render();
                return;
            }
            S.failed = false;
            S.settings = d.settings;
            S.status = d.status && d.status.ok ? d.status : S.status;
            render();
            /* allowed addresses show in these tables too */
            renderAlerts();
            renderRecent();
        });
    }

    function loadHits() {
        return fetchJson(API + 'hits', 60000).then(function (d) {
            if (d && d.ok) {
                S.hits = d.hits;
                renderRecent();
            }
        });
    }

    function loadAlerts() {
        return fetchJson(API + 'alerts', 60000).then(function (d) {
            if (d && d.ok) {
                if (S.acked && Date.now() - S.acked < 30000) {    // a cached answer from before "Mark as seen"
                    d.alerts.forEach(function (x) {
                        x['new'] = false;
                    });
                    d.unseen = 0;
                }
                S.alerts = d;
                renderAlerts();
                renderStats();
            }
        });
    }

    function loadCheckup() {
        fetchJson(API + 'checkup', 30000).then(function (d) {
            if (d && d.ok) {
                S.checkup = d;
                renderCheckup();
            }
        });
    }

    /* device names: ARP + the Network Map's labels, like the Traffic Map */
    function loadNames() {
        Promise.all([
            fetchJson('/api/diagnostics/interface/get_arp', 20000),
            fetchJson('/api/unisense/networkmap/search_device')
        ]).then(function (r) {
            var labels = {};
            ((r[1] && r[1].rows) || []).forEach(function (l) {
                if (l && l.mac && l.name) {
                    labels[String(l.mac).toLowerCase()] = l.name;
                }
            });
            var names = {};
            (Array.isArray(r[0]) ? r[0] : ((r[0] && r[0].rows) || [])).forEach(function (a) {
                if (a && a.ip) {
                    var name = labels[String(a.mac || '').toLowerCase()] || a.hostname || a.manufacturer || '';
                    if (name && name !== '?') {
                        names[a.ip] = name;
                    }
                }
            });
            S.names = names;
            window.UniSense = window.UniSense || {};
            window.UniSense.deviceNames = names;
            renderRecent();
            renderAlerts();
        });
    }

    function poll() {
        clearTimeout(S.timer);
        S.timer = setTimeout(function () {
            if (document.hidden) {
                poll();
                return;
            }
            Promise.all([load(), loadHits(), loadAlerts()]).then(poll);
        }, POLL_MS);
    }

    /* ------------------------------------------------------------- render */
    function render() {
        if (S.failed && !S.settings) {
            var b = q('banner');
            b.hidden = false;
            setHtml(b, '<i class="fa fa-triangle-exclamation"></i> ' + esc(t('error', 'Could not read Shield. Check your privileges or try again.')));
            return;
        }
        renderHead();
        renderStats();
        renderLists();
        renderCountries();
        renderForms();
    }

    function renderHead() {
        var s = S.settings;
        var st = S.status || {};
        var on = s.enabled;
        var nc = s.countries.length;
        var state = q('state');
        state.className = 'ush-state ' + (on ? 'is-on' : 'is-off');
        setHtml(state, '<i class="fa ' + (on ? 'fa-shield-halved' : 'fa-shield') + ' fa-fw"></i><span>' +
            esc(on ? t('on', 'Protected') : t('off', 'Threat lists off')) + '</span>' +
            (nc ? '<small>' + nc + ' ' + (nc === 1 ? 'country' : 'countries') + ' blocked</small>' : ''));
        setHtml(q('updated'), st.updated ? esc(t('updated', 'Lists updated') + ' ' + ago(st.updated) + ' · ' + t('schedule', 'checked every 15 minutes')) : '');
        var msgs = [];
        (st.errors || []).forEach(function (e) {
            msgs.push(e);
        });
        if (st.ok && !st.db && nc) {
            msgs.push('The IP-to-country database is not downloaded yet, so blocked countries are not enforced. Open the Traffic Map to download it.');
        }
        var banner = q('banner');
        banner.hidden = !msgs.length;
        setHtml(banner, msgs.map(function (m) {
            return '<div><i class="fa fa-triangle-exclamation"></i> ' + esc(m) + '</div>';
        }).join(''));
    }

    function plural(n, one, many) {
        return n + ' ' + (n === 1 ? one : many);
    }

    function stat(icon, title, value, sub, cls, bars) {
        return '<div class="utm-stat ush-stat ' + (cls || '') + '"><span class="utm-stat-icon"><i class="fa ' + icon + '"></i></span>' +
            '<span class="utm-stat-body"><span class="utm-stat-title">' + esc(title) + '</span>' +
            '<span class="utm-stat-value">' + esc(value) + '</span><span class="utm-stat-sub">' + esc(sub) + '</span></span>' +
            (bars || '') + '</div>';
    }

    function weekBars(days, kinds) {
        if (!days || !days.length) {
            return '';
        }
        var vals = days.map(function (d) {
            return kinds.reduce(function (a, k) { return a + ((d.counts || {})[k] || 0); }, 0);
        });
        var max = Math.max.apply(null, vals.concat([1]));
        return '<span class="ush-bars" aria-hidden="true">' + vals.map(function (v, i) {
            return '<i style="height:' + Math.max(6, Math.round(100 * v / max)) + '%" title="' + esc(days[i].day + ': ' + v) + '"' +
                (v ? '' : ' class="is-zero"') + '></i>';
        }).join('') + '</span>';
    }

    function renderStats() {
        var st = S.status;
        if (!st || !st.stats) {
            setHtml(q('stats'), '');
            return;
        }
        var today = st.stats.today;
        var week = st.stats.week;
        var inK = ['country-in', 'threat-in', 'scanner-in'];
        var outK = ['country-out', 'threat-out'];
        var sum = function (o, ks) {
            return ks.reduce(function (a, k) { return a + (o[k] || 0); }, 0);
        };
        var unseen = S.alerts ? S.alerts.unseen : st.alerts;
        var total = S.alerts ? S.alerts.alerts.length : st.alerts_total;
        var listed = 0;
        Object.keys(st.tables || {}).forEach(function (k) {
            listed += st.tables[k].entries || 0;
        });
        setHtml(q('stats'),
            stat('fa-shield-halved', t('attacks', 'Blocked from the Internet'), num(sum(today, inK)),
                num(sum(week, inK)) + ' ' + t('thisWeek', 'this week'), 'is-in', weekBars(st.stats.days, inK)) +
            stat('fa-ban', t('stopped', 'Your devices stopped'), num(sum(today, outK)),
                num(sum(week, outK)) + ' ' + t('thisWeek', 'this week'), 'is-out', weekBars(st.stats.days, outK)) +
            stat('fa-triangle-exclamation', t('alerts', 'Alerts'), String(unseen || 0),
                total ? total + ' device ' + (total === 1 ? 'alert' : 'alerts') + ' kept' : 'all clear', unseen ? 'is-alert' : 'is-clear') +
            stat('fa-list-check', t('listed', 'Addresses on block lists'), num(listed),
                plural(S.settings ? S.settings.feeds.length : 0, 'list', 'lists') + ' · ' +
                plural(S.settings ? S.settings.countries.length : 0, 'country', 'countries'), 'is-list'));
    }

    function renderLists() {
        var s = S.settings;
        var st = S.status || {};
        q('enabled').checked = !!s.enabled;
        var feeds = st.feeds || [];
        if (!feeds.length) {
            setHtml(q('lists'), '<div class="utm-empty">…</div>');
            return;
        }
        setHtml(q('lists'), '<ul class="ush-feeds' + (s.enabled ? '' : ' is-off') + '">' + feeds.map(function (f) {
            var on = s.feeds.indexOf(f.id) !== -1;
            var meta = [];
            if (on && f.entries) {
                meta.push(num(f.entries) + (f.entries === 1 ? ' network' : ' networks'));
            }
            if (on && f.updated) {
                meta.push('updated ' + ago(f.updated));
            }
            return '<li class="ush-feed' + (on ? ' is-on' : '') + '">' +
                '<label class="ush-switch ush-switch-sm"><input type="checkbox" data-ush-feed="' + esc(f.id) + '"' + (on ? ' checked' : '') +
                (s.enabled ? '' : ' disabled') + '><span></span></label>' +
                '<span class="ush-feed-body"><span class="ush-feed-name"><a href="' + esc(f.home) + '" target="_blank" rel="noopener">' + esc(f.name) + '</a>' +
                '<span class="ush-tag ' + (f.kind === 'threat' ? 'is-both' : 'is-in') + '">' +
                esc(f.kind === 'threat' ? t('both', 'Both ways') : t('inbound', 'Inbound only')) + '</span>' +
                (f['default'] ? '' : '<span class="ush-tag">optional</span>') + '</span>' +
                '<small>' + esc(f.about) + '</small>' +
                (meta.length ? '<small class="ush-feed-meta">' + esc(meta.join(' · ')) + '</small>' : '') +
                (on && f.error ? '<small class="ush-err"><i class="fa fa-triangle-exclamation"></i> ' + esc(f.error) + '</small>' : '') +
                '</span></li>';
        }).join('') + '</ul>');
    }

    function renderCountries() {
        var s = S.settings;
        var home = (S.status || {}).home;
        setHtml(q('chips'), s.countries.length ? s.countries.map(function (cc) {
            return '<span class="ush-chip">' + flag(cc) + '<span>' + esc(countryName(cc)) + '</span>' +
                (cc === home ? '<small>your country</small>' : '') +
                '<button type="button" class="ush-chip-x" data-ush-unblock="' + esc(cc) + '" title="' + esc(t('unblock', 'Unblock') + ' ' + countryName(cc)) +
                '" aria-label="' + esc(t('unblock', 'Unblock') + ' ' + countryName(cc)) + '"><i class="fa fa-xmark"></i></button></span>';
        }).join('') : '<span class="utm-muted">' + esc(t('noCountries', 'No country is blocked.')) + '</span>');
    }

    function renderAllows() {
        var list = ((S.status && S.status.allows) || []).filter(function (a) {
            return !a.forever;
        });
        setHtml(q('allows'), list.map(function (a) {
            return '<li><code class="utm-addr">' + esc(a.addr) + '</code><span>' + esc(left(a)) + '</span>' +
                '<button type="button" class="ush-chip-x" data-ush-unallow="' + esc(a.net) + '" title="Block again" aria-label="Block ' + esc(a.addr) + ' again">' +
                '<i class="fa fa-xmark"></i></button></li>';
        }).join(''));
    }

    function renderForms() {
        renderAllows();
        var s = S.settings;
        var ta = q('allowlist');
        if (document.activeElement !== ta && !ta.__dirty) {
            ta.value = s.allowlist.join('\n');
        }
        ['vtkey', 'abuseipdbkey'].forEach(function (k) {
            var el = q(k);
            el.placeholder = s[k] ? '•••••••• saved' : 'not set';
        });
    }

    function devName(ip) {
        return (S.names || {})[ip] || '';
    }

    function devHtml(ip) {
        var n = devName(ip);
        return '<span class="utm-dev"><b>' + esc(n || ip) + '</b>' + (n ? '<small>' + esc(ip) + '</small>' : '') + '</span>';
    }

    function remoteHtml(ip, cc, org) {
        var v6 = ip.indexOf(':') !== -1;
        return '<span class="ush-remote">' + flag(cc) + '<span><code class="utm-addr">' + esc(v6 ? '[' + ip + ']' : ip) + '</code>' +
            '<small>' + esc((cc ? countryName(cc) : '') + (org ? (cc ? ' · ' : '') + org : '')) + '</small></span></span>';
    }

    var DUR = [['1h', '1 hour'], ['6h', '6 hours'], ['24h', '24 hours'], ['1mo', '1 month'],
        ['restart', 'Until restart'], ['forever', 'Always']];

    function allowedEntry(ip) {
        var list = (S.status && S.status.allows) || [];
        for (var i = 0; i < list.length; i++) {
            if (list[i].addr === ip || list[i].net === ip) {
                return list[i];
            }
        }
        return null;
    }

    function left(a) {
        if (a.forever) {
            return 'always';
        }
        if (a.dur === 'restart') {
            return 'until restart';
        }
        var s = Math.max(0, a.until - Date.now() / 1000);
        if (s < 3600) {
            return Math.max(1, Math.round(s / 60)) + ' min left';
        }
        if (s < 172800) {
            return Math.round(s / 3600) + ' h left';
        }
        return Math.round(s / 86400) + ' days left';
    }

    /* "Allow" (opens the duration menu) or, once allowed, a tag with the time left */
    function allowBtn(ip) {
        var a = allowedEntry(ip);
        if (a) {
            return '<span class="ush-allowed" title="Allowed ' + esc(left(a)) + '"><i class="fa fa-check"></i> ' + esc(left(a)) + '</span>';
        }
        return '<button type="button" class="btn btn-default btn-xs ush-allow-btn" data-ush-allow="' + esc(ip) + '" aria-haspopup="menu" ' +
            'title="' + esc(t('allowBtn', 'Allow this address…')) + '" aria-label="' + esc(t('allowBtn', 'Allow this address…')) + '">' +
            '<i class="fa fa-unlock"></i><i class="fa fa-caret-down ush-caret"></i></button>';
    }

    function checkBtn(ip) {
        return '<button type="button" class="btn btn-default btn-xs" data-ush-check="' + esc(ip) + '" title="Check this address">' +
            '<i class="fa fa-magnifying-glass"></i></button>';
    }

    function alertKey(x) {
        return x.device + '|' + x.remote;
    }

    function renderAlerts() {
        var a = S.alerts;
        var cardEl = q('alerts-card');
        var list = a ? a.alerts.filter(function (x) {
            return !S.dismissed[alertKey(x)];
        }) : [];
        if (!list.length) {
            cardEl.hidden = true;
            return;
        }
        cardEl.hidden = false;
        var fresh = list.filter(function (x) {
            return x['new'];
        });
        var earlier = list.filter(function (x) {
            return !x['new'];
        });
        cardEl.classList.toggle('has-new', fresh.length > 0);
        q('ack').hidden = !fresh.length;
        var feeds = {};
        ((S.status || {}).feeds || []).forEach(function (f) {
            feeds[f.id] = f.name;
        });
        function table(rows) {
            return '<div class="utm-table-wrap"><table class="utm-table ush-table"><thead><tr><th>Device</th><th>Tried to reach</th>' +
                '<th>List</th><th>Seen</th><th></th></tr></thead><tbody>' +
                rows.map(function (x) {
                    return '<tr class="' + (x['new'] ? 'is-new' : '') + '"><td>' + devHtml(x.device) + '</td>' +
                        '<td>' + remoteHtml(x.remote, x.cc, x.org) + (x.port ? '<small class="ush-port">' + esc(x.proto.toUpperCase() + ' ' + x.port) + '</small>' : '') + '</td>' +
                        '<td>' + (x.lists || []).map(function (l) {
                            return '<span class="ush-tag is-both">' + esc(feeds[l] || l) + '</span>';
                        }).join(' ') + '</td>' +
                        '<td class="ush-nowrap">' + esc(ago(x.last)) + '<small>' + esc(plural(x.count, 'try', 'tries')) + '</small></td>' +
                        '<td class="ush-c-act">' + allowBtn(x.remote) + checkBtn(x.remote) +
                        '<button type="button" class="btn btn-default btn-xs ush-icon-btn" data-ush-action="dismiss" data-device="' + esc(x.device) +
                        '" data-remote="' + esc(x.remote) + '" title="' + esc(t('dismiss', 'Dismiss: hide until this device tries again')) +
                        '" aria-label="Dismiss"><i class="fa fa-xmark"></i></button></td></tr>';
                }).join('') + '</tbody></table></div>';
        }
        var openEarlier = cardEl.querySelector('details.ush-earlier[open]') !== null;
        setHtml(q('alerts'), (fresh.length ? table(fresh) : '<p class="ush-help ush-allclear"><i class="fa fa-circle-check"></i> ' +
            esc(t('allSeen', 'Nothing new. Earlier alerts are kept below until you dismiss them.')) + '</p>') +
            (earlier.length ? '<details class="ush-earlier"' + (openEarlier ? ' open' : '') + '><summary>' +
                esc(t('earlier', 'Earlier alerts')) + ' (' + earlier.length + ')</summary>' + table(earlier) + '</details>' : ''));
    }

    function renderRecent() {
        var hits = S.hits;
        var el = q('recent');
        if (!hits) {
            setHtml(el, '<div class="utm-empty">…</div>');
            return;
        }
        var f = S.filter;
        var rows = hits.filter(function (h) {
            return f === 'all' || (KIND[h.kind] || {}).dir === f;
        }).slice(0, 100);
        if (!rows.length) {
            setHtml(el, '<div class="utm-empty">' + esc(t('noBlocks', 'Nothing blocked yet.')) + '</div>');
            return;
        }
        setHtml(el, '<div class="utm-table-wrap ush-recent-wrap"><table class="utm-table ush-table"><thead><tr><th>Time</th><th>Blocked</th>' +
            '<th>Address</th><th>Port</th><th class="utm-c-hide">Interface</th><th></th></tr></thead><tbody>' +
            rows.map(function (h) {
                var k = KIND[h.kind] || { icon: 'fa-ban', label: h.kind, dir: 'in' };
                var out = k.dir === 'out';
                return '<tr><td class="ush-nowrap utm-muted">' + esc(clock(h.ts)) + '</td>' +
                    '<td><span class="ush-kind ' + (out ? 'is-out' : 'is-in') + '"><i class="fa ' + k.icon + ' fa-fw"></i><span>' + esc(k.label) +
                    '<small>' + (out ? esc(devName(h.device) || h.device) : 'inbound') + '</small></span></span></td>' +
                    '<td>' + remoteHtml(h.remote, h.cc, h.org) + '</td>' +
                    '<td class="ush-nowrap">' + esc((h.proto || '').toUpperCase() + (h.port ? ' ' + h.port : '')) + '</td>' +
                    '<td class="utm-c-hide utm-muted">' + esc(h.iface || '') + '</td>' +
                    '<td class="ush-c-act">' + allowBtn(h.remote) + checkBtn(h.remote) + '</td></tr>';
            }).join('') + '</tbody></table></div>');
    }

    function renderCheckup() {
        var c = S.checkup;
        if (!c) {
            return;
        }
        setHtml(q('checkup'), '<div class="ush-score"><b>' + c.score + '/' + c.total + '</b><span>checks passed</span></div>' +
            '<ul class="ush-checks">' + c.checks.map(function (x) {
                var icon = x.ok ? 'fa-circle-check' : (x.level === 'warn' ? 'fa-triangle-exclamation' : 'fa-circle-info');
                return '<li class="is-' + esc(x.level) + '"><i class="fa ' + icon + ' fa-fw"></i><span><b>' + esc(x.title) + '</b>' +
                    '<small>' + esc(x.detail) + '</small></span>' +
                    (x.ok ? '' : '<a class="btn btn-default btn-xs" href="' + esc(x.url) + '">Open</a>') + '</li>';
            }).join('') + '</ul>');
    }

    function pill(on, yes, no) {
        return '<span class="ush-pill ' + (on ? 'is-bad' : 'is-good') + '">' + esc(on ? yes : no) + '</span>';
    }

    function renderCheck() {
        var el = q('check-result');
        var c = S.check;
        if (!c) {
            setHtml(el, '');
            return;
        }
        if (c.loading) {
            setHtml(el, '<div class="utm-empty"><i class="fa fa-spinner fa-pulse"></i></div>');
            return;
        }
        if (!c.ok) {
            setHtml(el, '<div class="ush-msg is-error">' + esc(c.error || 'Could not check this address.') + '</div>');
            return;
        }
        var rows = [];
        var al = allowedEntry(c.ip);
        rows.push(['Status', c.allowed ? '<span class="ush-pill is-good">Allowed · ' + esc(al ? left(al) : (c.allow_until === -1 ? 'always' : 'for now')) + '</span>' +
            (al ? ' <button type="button" class="btn btn-default btn-xs" data-ush-unallow="' + esc(al.net) + '">Block again</button>' : '') :
            pill(c.blocked, 'Blocked by Shield', c.public ? 'Not blocked' : 'Private address') +
            (c.blocked ? ' ' + allowBtn(c.ip) : '')]);
        if (c.cc) {
            rows.push(['Country', flag(c.cc) + ' ' + esc(countryName(c.cc)) + (c.country_blocked ? ' <span class="ush-tag is-both">blocked country</span>' : '')]);
        }
        if (c.org || c.asn) {
            rows.push(['Network', esc(c.org || '') + (c.asn ? ' <small class="utm-muted">AS' + esc(c.asn) + '</small>' : '')]);
        }
        rows.push(['Block lists', c.lists.length ? c.lists.map(function (l) {
            return '<span class="ush-tag ' + (l.kind === 'threat' ? 'is-both' : 'is-in') + '">' + esc(l.name) + (l.enabled ? '' : ' (off)') + '</span>';
        }).join(' ') : '<span class="utm-muted">on none of the lists</span>']);
        if (c.virustotal) {
            var v = c.virustotal;
            rows.push(['VirusTotal', v.ok ? pill(v.malicious + v.suspicious > 0, v.malicious + ' malicious, ' + v.suspicious + ' suspicious',
                'clean (' + v.harmless + ' harmless)') + (v.owner ? ' <small class="utm-muted">' + esc(v.owner) + '</small>' : '') :
                '<span class="ush-err">' + esc(v.error) + '</span>']);
        }
        if (c.abuseipdb) {
            var a = c.abuseipdb;
            rows.push(['AbuseIPDB', a.ok ? pill(a.score >= 25, 'abuse score ' + a.score + '% · ' + a.reports + ' reports',
                'abuse score ' + a.score + '% · ' + a.reports + ' reports') +
                (a.usage || a.isp ? ' <small class="utm-muted">' + esc([a.usage, a.isp].filter(Boolean).join(' · ')) + '</small>' : '') +
                (a.tor ? ' <span class="ush-tag">Tor</span>' : '') : '<span class="ush-err">' + esc(a.error) + '</span>']);
        }
        var links = '';
        if (c.public) {
            links = '<div class="ush-links"><a class="btn btn-default btn-xs" href="' + esc(c.links.virustotal) + '" target="_blank" rel="noopener">' +
                '<i class="fa fa-arrow-up-right-from-square"></i> VirusTotal</a>' +
                '<a class="btn btn-default btn-xs" href="' + esc(c.links.abuseipdb) + '" target="_blank" rel="noopener">' +
                '<i class="fa fa-arrow-up-right-from-square"></i> AbuseIPDB</a></div>';
        }
        setHtml(el, '<div class="ush-check-head"><code class="utm-addr">' + esc(c.ip) + '</code></div>' +
            '<dl class="ush-dl">' + rows.map(function (r) {
                return '<dt>' + esc(r[0]) + '</dt><dd>' + r[1] + '</dd>';
            }).join('') + '</dl>' + links);
    }

    /* ------------------------------------------------------- allow menu */
    function closeMenu() {
        if (menu) {
            menu.remove();
            menu = null;
        }
    }

    function openMenu(btn) {
        closeMenu();
        var ip = btn.getAttribute('data-ush-allow');
        menu = document.createElement('div');
        menu.className = 'ush-menu';
        menu.setAttribute('role', 'menu');
        menu.innerHTML = '<div class="ush-menu-head">Allow ' + esc(ip) + ' for</div>' + DUR.map(function (d) {
            return '<button type="button" role="menuitem" data-ush-dur="' + d[0] + '">' + esc(d[1]) + '</button>';
        }).join('');
        menu.__ip = ip;
        root.appendChild(menu);
        var r = btn.getBoundingClientRect();
        var rr = root.getBoundingClientRect();
        var w = menu.offsetWidth;
        menu.style.left = Math.max(0, Math.min(r.right - rr.left - w, rr.width - w)) + 'px';
        menu.style.top = (r.bottom - rr.top + 4) + 'px';
        menu.querySelector('button').focus();
    }

    function allow(ip, dur) {
        closeMenu();
        post(API + 'allow', { address: ip, duration: dur }).then(function (r) {
            if (r && r.ok !== false && (r.ok || r.result === 'saved')) {
                Promise.all([load(), loadHits(), loadAlerts()]).then(function () {
                    if (S.check && S.check.ip === ip) {
                        runCheck(ip);
                    }
                });
            } else {
                window.alert((r && (r.error || (r.errors || []).join(' '))) || 'Could not allow this address.');
            }
        });
    }

    function unallow(net) {
        post(API + 'unallow', { address: net }).then(function () {
            Promise.all([load(), loadHits(), loadAlerts()]).then(function () {
                if (S.check && S.check.ip) {
                    runCheck(S.check.ip);
                }
            });
        });
    }

    /* ------------------------------------------------------------- actions */
    function msg(name, text, cls) {
        var el = q(name);
        if (!el) {
            return;
        }
        el.className = 'ush-msg' + (cls ? ' ' + cls : '');
        el.textContent = text || '';
        if (text && cls === 'is-ok') {
            clearTimeout(el.__ushT);
            el.__ushT = setTimeout(function () {
                el.textContent = '';
            }, 4000);
        }
    }

    function validationText(r) {
        if (r && r.validations) {
            return Object.keys(r.validations).map(function (k) {
                return r.validations[k];
            }).join(' ');
        }
        return (r && r.error) || 'Could not save.';
    }

    function save(body, msgName) {
        msg(msgName, 'Saving…');
        return post(API + 'set', { shield: body }).then(function (r) {
            if (r && r.result === 'saved') {
                S.settings = r.settings;
                msg(msgName, t('saved', 'Saved'), 'is-ok');
                render();
                setTimeout(function () {
                    load();
                }, 1500);
                return true;
            }
            msg(msgName, validationText(r), 'is-error');
            render();
            return false;
        });
    }

    function busy(name, on) {
        S.busy[name] = on;
        root.classList.toggle('is-busy-' + name, on);
    }

    function addCountry() {
        var v = q('add-cc').value.trim();
        var m = v.match(/\(([A-Za-z]{2})\)\s*$/) || v.match(/^([A-Za-z]{2})$/);
        var cc = m ? m[1].toUpperCase() : '';
        if (!cc) {
            var low = v.toLowerCase();
            CODES.some(function (c) {
                if (countryName(c).toLowerCase() === low) {
                    cc = c;
                    return true;
                }
                return false;
            });
        }
        if (!cc || CODES.indexOf(cc) === -1) {
            msg('add-msg', 'Pick a country from the list.', 'is-error');
            return;
        }
        if (S.settings.countries.indexOf(cc) !== -1) {
            msg('add-msg', countryName(cc) + ' is already blocked.', 'is-error');
            return;
        }
        var home = (S.status || {}).home;
        if (cc === home && !window.confirm(countryName(cc) + ' is where this firewall is. Blocking it cuts off most local websites and services, ' +
            'and any remote access from inside the country. Block it anyway?')) {
            return;
        }
        msg('add-msg', 'Blocking ' + countryName(cc) + '…');
        post(API + 'block/' + cc, {}).then(function (r) {
            if (r && r.result === 'saved') {
                S.settings = r.settings;
                q('add-cc').value = '';
                var killed = r.apply && r.apply.killed;
                msg('add-msg', countryName(cc) + ' blocked' + (killed ? ' · ' + killed + ' open connection' + (killed === 1 ? '' : 's') + ' ended' : '') + '.', 'is-ok');
                render();
                load();
            } else {
                msg('add-msg', validationText(r), 'is-error');
            }
        });
    }

    function unblock(cc) {
        msg('add-msg', 'Unblocking ' + countryName(cc) + '…');
        post(API + 'unblock/' + cc, {}).then(function (r) {
            if (r && r.result === 'saved') {
                S.settings = r.settings;
                msg('add-msg', countryName(cc) + ' unblocked.', 'is-ok');
                render();
                load();
            } else {
                msg('add-msg', validationText(r), 'is-error');
            }
        });
    }

    function runCheck(ip) {
        if (!ip) {
            return;
        }
        if (!/^[0-9a-fA-F:.]+$/.test(ip)) {
            S.check = { ok: false, error: 'That is not an IP address.' };
            renderCheck();
            return;
        }
        S.checkIp = ip;
        q('check-ip').value = ip;
        S.check = { loading: true };
        renderCheck();
        fetchJson(API + 'check/' + encodeURIComponent(ip), 60000).then(function (d) {
            if (S.checkIp !== ip) {
                return;
            }
            S.check = d || { ok: false };
            renderCheck();
        });
    }

    function onChange(e) {
        var el = e.target;
        if (el === q('enabled')) {
            save({ enabled: el.checked ? '1' : '0' }, 'lists-msg').then(function () {
                if (el.checked) {
                    /* the first download runs in the background: show the numbers as they come */
                    setTimeout(load, 8000);
                    setTimeout(load, 25000);
                }
            });
            return;
        }
        var feed = el.getAttribute && el.getAttribute('data-ush-feed');
        if (feed) {
            var feeds = S.settings.feeds.filter(function (f) {
                return f !== feed;
            });
            if (el.checked) {
                feeds.push(feed);
            }
            save({ feeds: feeds }, 'lists-msg').then(function () {
                setTimeout(load, 8000);
            });
            return;
        }
        if (el === q('allowlist')) {
            el.__dirty = true;
        }
    }

    function onClick(e) {
        var ab = e.target.closest && e.target.closest('[data-ush-allow]');
        if (ab) {
            if (menu && menu.__ip === ab.getAttribute('data-ush-allow')) {
                closeMenu();
            } else {
                openMenu(ab);
            }
            return;
        }
        var du = e.target.closest && e.target.closest('[data-ush-dur]');
        if (du && menu) {
            allow(menu.__ip, du.getAttribute('data-ush-dur'));
            return;
        }
        closeMenu();
        var ua = e.target.closest && e.target.closest('[data-ush-unallow]');
        if (ua) {
            unallow(ua.getAttribute('data-ush-unallow'));
            return;
        }
        var a = e.target.closest && e.target.closest('[data-ush-action]');
        if (a) {
            var act = a.getAttribute('data-ush-action');
            if (act === 'update' && !S.busy.update) {
                busy('update', true);
                a.disabled = true;
                a.querySelector('span').textContent = t('updating', 'Updating…');
                post(API + 'update', {}).then(function () {
                    busy('update', false);
                    a.disabled = false;
                    a.querySelector('span').textContent = t('updateNow', 'Update lists now');
                    load();
                    loadHits();
                });
            } else if (act === 'dismiss') {
                var key = a.getAttribute('data-device') + '|' + a.getAttribute('data-remote');
                S.dismissed[key] = true;      // the alert list is cached for a few seconds: hide it here at once
                renderAlerts();
                post(API + 'dismiss', { device: a.getAttribute('data-device'), remote: a.getAttribute('data-remote') });
            } else if (act === 'ack') {
                if (S.alerts) {                // same: show it seen at once, the cached list catches up
                    S.alerts.alerts.forEach(function (x) {
                        x['new'] = false;
                    });
                    S.alerts.unseen = 0;
                    S.acked = Date.now();
                    renderAlerts();
                }
                post(API + 'ack', {}).then(function () {
                    if (window.UniSense && window.UniSense.shieldBadge) {
                        window.UniSense.shieldBadge(0);
                    }
                });
            } else if (act === 'save-allow') {
                var ta = q('allowlist');
                save({ allowlist: ta.value.split(/[\s,]+/).filter(Boolean) }, 'allow-msg').then(function (ok) {
                    if (ok) {
                        ta.__dirty = false;
                    }
                });
            } else if (act === 'save-keys') {
                var body = {};
                ['vtkey', 'abuseipdbkey'].forEach(function (k) {
                    var v = q(k).value.trim();
                    if (v) {
                        body[k] = v === '-' ? '' : v;
                    }
                });
                if (!Object.keys(body).length) {
                    msg('keys-msg', 'Paste a key first (or "-" to remove one).', 'is-error');
                    return;
                }
                save(body, 'keys-msg').then(function (ok) {
                    if (ok) {
                        q('vtkey').value = '';
                        q('abuseipdbkey').value = '';
                    }
                });
            }
            return;
        }
        var un = e.target.closest && e.target.closest('[data-ush-unblock]');
        if (un) {
            unblock(un.getAttribute('data-ush-unblock'));
            return;
        }
        var ck = e.target.closest && e.target.closest('[data-ush-check]');
        if (ck) {
            runCheck(ck.getAttribute('data-ush-check'));
            q('check-card').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            return;
        }
        var fl = e.target.closest && e.target.closest('[data-ush-filter]');
        if (fl) {
            S.filter = fl.getAttribute('data-ush-filter');
            root.querySelectorAll('[data-ush-filter]').forEach(function (b) {
                b.classList.toggle('is-active', b === fl);
            });
            renderRecent();
        }
    }

    /* ------------------------------------------------------------- start */
    build();
    Promise.all([load(), loadHits(), loadAlerts()]).then(function () {
        poll();
        var m = /[#&]check=([0-9a-fA-F:.]+)/.exec(location.hash);
        if (m) {
            runCheck(m[1]);
            q('check-card').scrollIntoView({ block: 'start' });
        }
    });
    loadNames();
    loadCheckup();
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            load();
        }
    });
}());
