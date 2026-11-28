/*
 * UniSense for OPNsense — Lobby: Shield, tabs and unblock requests
 * (c) Ox1d3x3 — BSD 2-Clause License
 *
 * Shield's page has three tabs: Overview (shield.js), Privacy (privacy.js,
 * started the first time its tab opens) and Requests: what people asked for
 * from the block page, to approve for a while or for good, or decline.
 *
 * Data: /api/unisense/shield/requests, /api/unisense/shield/request.
 */
(function () {
    'use strict';

    var root = document.getElementById('usr');
    var tabs = document.querySelectorAll('[data-ush-tab]');
    if (!root || !tabs.length) {
        return;
    }
    var API = '/api/unisense/shield/';
    var POLL_MS = 30000;
    var DURS = [['1h', '1 hour'], ['24h', '24 hours'], ['1mo', '1 month'], ['forever', 'Always']];
    var S = { reqs: null, timer: null, busy: {} };

    function t(k, d) {
        var v = root.getAttribute('data-t-' + k);
        return v || d;
    }

    function esc(v) {
        return String(v === undefined || v === null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function ago(ts) {
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

    function left(until) {
        var s = until - Date.now() / 1000;
        if (s <= 0) {
            return 'ended';
        }
        return s < 5400 ? Math.max(1, Math.round(s / 60)) + ' min left' : s < 129600 ? Math.round(s / 3600) + ' h left' : Math.round(s / 86400) + ' days left';
    }

    function fetchJson(url) {
        return fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } }).then(function (r) {
            return r.ok ? r.json().catch(function () { return null; }) : null;
        }).catch(function () { return null; });
    }

    function post(url, body) {
        return new Promise(function (resolve) {
            if (!window.jQuery) {
                resolve(null);
                return;
            }
            window.jQuery.ajax({
                url: url, type: 'POST', dataType: 'json', data: JSON.stringify(body || {}),
                contentType: 'application/json; charset=utf-8', global: false, timeout: 180000,
                success: function (d) { resolve(d); },
                error: function () { resolve(null); }
            });
        });
    }

    /* ------------------------------------------------------------- tabs */
    function show(name, push) {
        var found = false;
        tabs.forEach(function (b) {
            var on = b.getAttribute('data-ush-tab') === name;
            found = found || on;
            b.setAttribute('aria-selected', on ? 'true' : 'false');
            b.classList.toggle('is-active', on);
        });
        if (!found) {
            return show('overview', push);
        }
        document.querySelectorAll('[data-ush-pane]').forEach(function (p) {
            p.hidden = p.getAttribute('data-ush-pane') !== name;
        });
        if (name === 'privacy' && window.UniSense && window.UniSense.privacyStart) {
            window.UniSense.privacyStart();
        }
        if (name === 'requests') {
            load();
        }
        if (push) {
            history.replaceState(null, '', name === 'overview' ? location.pathname : '#' + name);
        }
        window.dispatchEvent(new Event('resize'));       // charts and tables in a pane that was hidden
    }

    tabs.forEach(function (b) {
        b.addEventListener('click', function () {
            show(b.getAttribute('data-ush-tab'), true);
        });
    });
    window.addEventListener('hashchange', function () {
        show(location.hash.slice(1) || 'overview', false);
    });

    /* ------------------------------------------------------------- requests */
    function badge(n) {
        var el = document.querySelector('[data-ush-req-badge]');
        if (el) {
            el.hidden = !n;
            el.textContent = n || '';
        }
    }

    function status(r) {
        if (r.status === 'approved') {
            return '<span class="usr-st is-ok"><i class="fa fa-check"></i> ' + (r.dur === 'forever' ? 'always' : esc(left(r.until))) + '</span>';
        }
        if (r.status === 'declined') {
            return '<span class="usr-st is-off"><i class="fa fa-xmark"></i> declined</span>';
        }
        if (r.status === 'expired') {
            return '<span class="usr-st is-off"><i class="fa fa-clock"></i> ended</span>';
        }
        return '<span class="usr-act"><span class="usr-approve"><button type="button" class="btn btn-primary btn-xs" data-usr-menu="' + esc(r.id) + '" aria-haspopup="menu">' +
            '<i class="fa fa-check"></i> ' + esc(t('approve', 'Approve')) + ' <i class="fa fa-caret-down"></i></button>' +
            '<span class="usr-menu" role="menu" hidden>' + DURS.map(function (d) {
                return '<button type="button" role="menuitem" data-usr-approve="' + esc(r.id) + '" data-dur="' + d[0] + '">' + d[1] + '</button>';
            }).join('') + '</span></span>' +
            '<button type="button" class="btn btn-default btn-xs" data-usr-decline="' + esc(r.id) + '"><i class="fa fa-xmark"></i> ' + esc(t('decline', 'Decline')) + '</button></span>';
    }

    function render() {
        var d = S.reqs;
        var head = '<div class="utm-card ush-card"><div class="utm-card-head"><b><i class="fa fa-inbox"></i> ' + esc(t('title', 'Unblock requests')) + '</b></div>' +
            '<p class="ush-help">' + esc(t('help', '')) + '</p>';
        if (!d) {
            root.innerHTML = head + '<div class="utm-empty">…</div></div>';
            return;
        }
        if (!d.requests.length) {
            root.innerHTML = head + '<div class="utm-empty">' + esc(t('none', 'No requests yet.')) + '</div></div>';
            return;
        }
        var names = (window.UniSense && window.UniSense.deviceNames) || {};
        root.innerHTML = head + '<div class="utm-table-wrap"><table class="utm-table ush-table usr-table"><thead><tr>' +
            '<th>Asked</th><th>Who</th><th>Website</th><th>Why</th><th></th></tr></thead><tbody>' +
            d.requests.map(function (r) {
                return '<tr data-usr-req="' + esc(r.id) + '" class="' + (r.status === 'pending' ? 'is-new' : '') + '">' +
                    '<td class="ush-nowrap utm-muted">' + esc(ago(r.t)) + '</td>' +
                    '<td><span class="utm-dev"><b>' + esc(r.name) + '</b><small>' + esc(names[r.ip] ? names[r.ip] + ' · ' + r.ip : r.ip) + '</small></span></td>' +
                    '<td><code class="usr-site">' + esc(r.site) + '</code></td>' +
                    '<td class="usr-why">' + esc(r.reason) + '</td>' +
                    '<td class="ush-c-act">' + status(r) + '</td></tr>';
            }).join('') + '</tbody></table></div><div class="ush-msg" data-usr="msg"></div></div>';
    }

    function msg(text, cls) {
        var el = root.querySelector('[data-usr="msg"]');
        if (el) {
            el.textContent = text || '';
            el.className = 'ush-msg ' + (cls || '');
        }
    }

    function load() {
        return fetchJson(API + 'requests').then(function (d) {
            if (d && d.ok) {
                S.reqs = d;
                badge(d.pending);
                if (!root.closest('[hidden]')) {
                    render();
                }
            } else if (!S.reqs) {
                render();
            }
        });
    }

    function act(id, action, dur) {
        if (S.busy[id]) {
            return;
        }
        S.busy[id] = true;
        msg(action === 'approve' ? 'Approving…' : 'Declining…');
        post(API + 'request', { id: id, action: action, duration: dur || '' }).then(function (r) {
            S.busy[id] = false;
            msg(r && r.ok ? (action === 'approve' ? 'Approved.' : 'Declined.') : (r && r.error) || 'Could not save.', r && r.ok ? 'is-ok' : 'is-error');
            load();
        });
    }

    function closeMenus() {
        root.querySelectorAll('.usr-menu').forEach(function (m) {
            m.hidden = true;
        });
    }

    root.addEventListener('click', function (e) {
        var c = function (sel) {
            return e.target.closest && e.target.closest(sel);
        };
        var m = c('[data-usr-menu]');
        if (m) {
            var menu = m.parentNode.querySelector('.usr-menu');
            var open = menu.hidden;
            closeMenus();
            menu.hidden = !open;
            return;
        }
        var a = c('[data-usr-approve]');
        if (a) {
            closeMenus();
            act(a.getAttribute('data-usr-approve'), 'approve', a.getAttribute('data-dur'));
            return;
        }
        var d = c('[data-usr-decline]');
        if (d) {
            act(d.getAttribute('data-usr-decline'), 'decline');
            return;
        }
        closeMenus();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            closeMenus();
        }
    });

    function poll() {
        clearTimeout(S.timer);
        S.timer = setTimeout(function () {
            if (!document.hidden) {
                load();
            }
            poll();
        }, POLL_MS);
    }

    show(location.hash.slice(1) || 'overview', false);
    load().then(poll);
}());
