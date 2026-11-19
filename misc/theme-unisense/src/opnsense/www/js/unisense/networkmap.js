/*
 * UniSense for OPNsense — Lobby: Network Map
 * (c) ox1d3x3 — BSD 2-Clause License
 *
 * Builds a live topology (internet -> firewall -> networks -> devices) from
 * core APIs only:
 *   /api/interfaces/overview/interfaces_info   interfaces, addresses, gateways
 *   /api/routes/gateway/status                 gateway health
 *   /api/diagnostics/interface/get_arp         who is online (IPv4)
 *   /api/kea/leases4/search                    hostnames (Kea, optional)
 *   /api/dnsmasq/leases/search                 hostnames (Dnsmasq, optional)
 *   /api/dhcpv4/leases/search_lease            hostnames (ISC DHCP, optional)
 *   /api/diagnostics/traffic/top/<ifs>         live per-host traffic (optional)
 *   /api/unisense/networkmap/*                 custom device names and icons
 *
 * Optional sources are read with fetch() so a missing service or plugin never
 * triggers the GUI's global ajax error dialog. Every value from the network
 * (hostnames, labels, vendors) is HTML-escaped before it is rendered.
 */
(function () {
    'use strict';

    var root = document.getElementById('usn');
    if (!root) {
        return;
    }

    /* ------------------------------------------------------------ strings */
    var T = {};
    Object.keys(root.dataset).forEach(function (k) {
        if (k.indexOf('t') === 0 && k.length > 1) {
            var key = k.charAt(1).toLowerCase() + k.slice(2);
            T[key] = root.dataset[k];
        }
    });
    function t(key, fallback) {
        return T[key] || fallback;
    }

    /* -------------------------------------------------------------- icons */
    var ICONS = {
        laptop: 'fa-laptop',
        desktop: 'fa-desktop',
        mobile: 'fa-mobile-screen-button',
        tablet: 'fa-tablet-screen-button',
        tv: 'fa-tv',
        game: 'fa-gamepad',
        server: 'fa-server',
        nas: 'fa-hard-drive',
        printer: 'fa-print',
        camera: 'fa-video',
        ap: 'fa-wifi',
        switch: 'fa-network-wired',
        router: 'fa-route',
        iot: 'fa-lightbulb',
        speaker: 'fa-volume-high',
        watch: 'fa-clock',
        car: 'fa-car',
        other: 'fa-circle-question'
    };
    var ICON_LABELS = {
        laptop: 'Laptop', desktop: 'Desktop', mobile: 'Phone', tablet: 'Tablet', tv: 'TV / media',
        game: 'Console', server: 'Server', nas: 'NAS', printer: 'Printer', camera: 'Camera',
        ap: 'Access point', switch: 'Switch', router: 'Router', iot: 'Smart home', speaker: 'Speaker',
        watch: 'Wearable', car: 'Car', other: 'Other'
    };

    /* first match wins; tested against "hostname vendor" (lower case) */
    var GUESS = [
        [/iphone|android|pixel|galaxy|oneplus|redmi|xiaomi.*(phone|mi)|huawei-p|oppo|vivo|motorola|moto[- ]/, 'mobile'],
        [/ipad|tab[- ]?s\d|kindle|fire[- ]?tab|surface go/, 'tablet'],
        [/watch|fitbit|garmin/, 'watch'],
        [/macbook|laptop|thinkpad|notebook|xps|zenbook|surface/, 'laptop'],
        [/imac|mac[- ]?mini|mac[- ]?studio|desktop|-pc\b|\bpc\b|workstation/, 'desktop'],
        [/xbox|playstation|ps[45]\b|nintendo|switch-?oled|steamdeck|steam-deck|sony interactive/, 'game'],
        [/apple[- ]?tv|appletv|roku|chromecast|shield|firetv|fire[- ]?tv|bravia|webos|lg[- ]?tv|samsung[- ]?tv|tizen|\btv\b|hisense|tcl/, 'tv'],
        [/sonos|homepod|echo|alexa|nest[- ]?(mini|audio|hub)|bose|speaker/, 'speaker'],
        [/printer|laserjet|officejet|deskjet|epson|brother|canon|lexmark|kyocera|xerox|ricoh/, 'printer'],
        [/synology|qnap|\bnas\b|truenas|unraid|asustor|terramaster|zimaos|casaos/, 'nas'],
        [/cam\b|camera|hikvision|dahua|reolink|amcrest|axis comm|wyze|eufy|arlo|doorbell|nvr|frigate/, 'camera'],
        [/\bap\b|access[- ]?point|unifi|ubiquiti|aruba|ruckus|eap\d|deco|omada|mikrotik/, 'ap'],
        [/switch|netgear|tp-?link|d-?link|zyxel|cisco/, 'switch'],
        [/router|openwrt|gl-?inet|fritz|asus-?rt/, 'router'],
        [/espressif|tuya|shelly|sonoff|tasmota|hue|philips lighting|lifx|wiz|nest|ring|ecobee|smart|esp32|esp8266|home[- ]?assistant|zigbee|tado/, 'iot'],
        [/proxmox|esxi|vmware|server|srv|docker|kube|raspberry|pi-?hole|nuc/, 'server'],
        [/tesla|car\b/, 'car'],
        [/apple/, 'laptop']
    ];

    function guessIcon(dev) {
        var hay = ((dev.hostname || '') + ' ' + (dev.vendor || '')).toLowerCase();
        for (var i = 0; i < GUESS.length; i++) {
            if (GUESS[i][0].test(hay)) {
                return GUESS[i][1];
            }
        }
        return 'other';
    }

    /* ------------------------------------------------------------ helpers */
    function esc(v) {
        return String(v === undefined || v === null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function normMac(m) {
        m = String(m || '').trim().toLowerCase().replace(/-/g, ':');
        return /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/.test(m) ? m : '';
    }

    function ipSortKey(ip) {
        var p = String(ip || '').split('.');
        if (p.length !== 4) {
            return Number.MAX_SAFE_INTEGER;
        }
        return ((+p[0] * 256 + +p[1]) * 256 + +p[2]) * 256 + +p[3];
    }

    function fmtBits(bits) {
        bits = +bits || 0;
        var units = ['b/s', 'Kb/s', 'Mb/s', 'Gb/s'];
        var i = 0;
        while (bits >= 1000 && i < units.length - 1) {
            bits /= 1000;
            i++;
        }
        return (i === 0 ? Math.round(bits) : bits.toFixed(bits >= 100 ? 0 : 1)) + ' ' + units[i];
    }

    /* compact form for tiles: 42.5M, 820K */
    function fmtBitsShort(bits) {
        bits = +bits || 0;
        var units = ['', 'K', 'M', 'G'];
        var i = 0;
        while (bits >= 1000 && i < units.length - 1) {
            bits /= 1000;
            i++;
        }
        return (i === 0 ? Math.round(bits) : bits.toFixed(bits >= 100 ? 0 : 1)) + units[i];
    }

    /*
     * Write markup only when it changed: periodic refreshes then cost nothing
     * when the network is unchanged, and hover/focus stay where they are.
     */
    function setHtml(el, html) {
        if (el && el.__usnHtml !== html) {
            el.innerHTML = html;
            el.__usnHtml = html;
            return true;
        }
        return false;
    }

    function rows(data) {
        if (!data) {
            return [];
        }
        if (Array.isArray(data)) {
            return data;
        }
        if (Array.isArray(data.rows)) {
            return data.rows;
        }
        if (Array.isArray(data.items)) {
            return data.items;
        }
        return [];
    }

    function fetchJson(url, timeoutMs) {
        var ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
        var timer = ctrl ? setTimeout(function () { ctrl.abort(); }, timeoutMs || 20000) : null;
        return fetch(url, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
            signal: ctrl ? ctrl.signal : undefined
        }).then(function (r) {
            if (!r.ok) {
                return null;
            }
            return r.json().catch(function () { return null; });
        }).catch(function () {
            return null;
        }).then(function (v) {
            if (timer) {
                clearTimeout(timer);
            }
            return v;
        });
    }

    function postJson(url, payload) {
        return new Promise(function (resolve) {
            $.ajax({
                url: url,
                type: 'POST',
                dataType: 'json',
                contentType: 'application/json; charset=utf-8',
                data: JSON.stringify(payload || {}),
                global: false,
                success: function (data) { resolve(data || {}); },
                error: function (xhr) { resolve((xhr && xhr.responseJSON) || { result: 'failed' }); }
            });
        });
    }

    /* -------------------------------------------------------------- state */
    var S = {
        interfaces: [],     // assigned, enabled interfaces
        gateways: [],
        devices: {},        // mac -> device
        labels: {},         // mac -> {uuid, name, icon}
        system: null,
        rates: {},          // ip -> {in, out}
        filter: 'all',
        query: '',
        showOffline: true,
        live: true,
        loaded: false,
        failed: false,
        updated: null
    };

    try {
        var pref = JSON.parse(window.localStorage.getItem('unisense.netmap') || '{}');
        if (typeof pref.showOffline === 'boolean') {
            S.showOffline = pref.showOffline;
        }
        if (typeof pref.live === 'boolean') {
            S.live = pref.live;
        }
    } catch (e) { /* storage unavailable */ }

    function savePrefs() {
        try {
            window.localStorage.setItem('unisense.netmap', JSON.stringify({ showOffline: S.showOffline, live: S.live }));
        } catch (e) { /* ignore */ }
    }

    function isUplink(ifc) {
        var lt = String(ifc.link_type || '').toLowerCase();
        return (Array.isArray(ifc.gateways) && ifc.gateways.length > 0) ||
            ['dhcp', 'pppoe', 'pptp', 'l2tp', 'ppp'].indexOf(lt) !== -1 ||
            ifc.identifier === 'wan';
    }

    /* optional DHCP services: only ask the ones present in this GUI's menu */
    function hasMenu(path) {
        return !!document.querySelector('#mainmenu a[href^="' + path + '"]');
    }

    /* --------------------------------------------------------------- load */
    function load() {
        return Promise.all([
            fetchJson('/api/interfaces/overview/interfaces_info'),
            fetchJson('/api/routes/gateway/status'),
            fetchJson('/api/diagnostics/interface/get_arp', 30000),
            hasMenu('/ui/kea/') ? fetchJson('/api/kea/leases4/search') : Promise.resolve(null),
            hasMenu('/ui/dnsmasq/') ? fetchJson('/api/dnsmasq/leases/search') : Promise.resolve(null),
            hasMenu('/ui/dhcpv4/leases') ? fetchJson('/api/dhcpv4/leases/search_lease') : Promise.resolve(null),
            fetchJson('/api/unisense/networkmap/search_device'),
            S.system ? Promise.resolve(S.system) : fetchJson('/api/diagnostics/system/system_information')
        ]).then(function (res) {
            var ifRows = rows(res[0]);
            if (!res[0] || (!ifRows.length && !rows(res[2]).length)) {
                S.failed = !S.loaded;
            } else {
                S.failed = false;
            }

            /* interfaces */
            var ifaces = [];
            var byDevice = {};
            ifRows.forEach(function (r) {
                if (!r || !r.identifier || r.enabled === false) {
                    return;
                }
                var ifc = {
                    id: String(r.identifier),
                    name: String(r.description || r.identifier).trim(),
                    device: String(r.device || r.ifname || ''),
                    status: String(r.status || ''),
                    addr4: String(r.addr4 || ''),
                    addr6: String(r.addr6 || ''),
                    mac: normMac(r.macaddr),
                    link_type: r.link_type,
                    gateways: Array.isArray(r.gateways) ? r.gateways : [],
                    identifier: String(r.identifier),
                    vlan: r.vlan_tag || null
                };
                /* loopback (lo0, 127.0.0.1) never has clients: leave it off the map */
                if (/^lo\d*$/.test(ifc.device) || /^127\./.test(ifc.addr4)) {
                    return;
                }
                ifc.uplink = isUplink(ifc);
                ifaces.push(ifc);
                if (ifc.device) {
                    byDevice[ifc.device] = ifc;
                }
            });
            S.interfaces = ifaces;
            S.gateways = rows(res[1]).filter(function (g) { return g && g.name; });

            var ownMacs = {};
            ifaces.forEach(function (i) {
                if (i.mac) {
                    ownMacs[i.mac] = true;
                }
            });

            /* devices: ARP = online */
            var devices = {};
            function devFor(mac) {
                if (!devices[mac]) {
                    devices[mac] = {
                        mac: mac, ips: [], hostname: '', vendor: '', ifname: '', ifid: '',
                        online: false, lease: false
                    };
                }
                return devices[mac];
            }
            function addIp(d, ip) {
                if (ip && d.ips.indexOf(ip) === -1) {
                    d.ips.push(ip);
                }
            }

            rows(res[2]).forEach(function (a) {
                var mac = normMac(a && a.mac);
                if (!mac || ownMacs[mac] || a.permanent === true || a.expired === true) {
                    return;
                }
                var d = devFor(mac);
                d.online = true;
                addIp(d, a.ip);
                d.hostname = d.hostname || String(a.hostname || '');
                d.vendor = d.vendor || String(a.manufacturer || '');
                if (a.intf && byDevice[a.intf]) {
                    d.ifid = byDevice[a.intf].id;
                }
                d.ifname = d.ifname || String(a.intf_description || a.intf || '');
            });

            /* leases: names, and devices that are currently offline */
            function applyLease(l, macKey, ipKey, hostKey, ifKey, ifDescrKey, vendorKey) {
                var mac = normMac(l && l[macKey]);
                if (!mac || ownMacs[mac]) {
                    return;
                }
                var known = !!devices[mac];
                var d = devFor(mac);
                d.lease = true;
                if (!known) {
                    addIp(d, l[ipKey]);
                }
                if (l[hostKey] && !d.hostname) {
                    d.hostname = String(l[hostKey]).replace(/\.$/, '');
                }
                if (vendorKey && l[vendorKey] && !d.vendor) {
                    d.vendor = String(l[vendorKey]);
                }
                if (!d.ifid) {
                    var dev = l[ifKey] && byDevice[l[ifKey]];
                    if (dev) {
                        d.ifid = dev.id;
                    } else if (l[ifDescrKey]) {
                        var hit = ifaces.filter(function (i) { return i.name === l[ifDescrKey]; })[0];
                        if (hit) {
                            d.ifid = hit.id;
                        }
                    }
                }
                d.ifname = d.ifname || String(l[ifDescrKey] || '');
            }
            rows(res[3]).forEach(function (l) { applyLease(l, 'hwaddr', 'address', 'hostname', 'if', 'if_descr', 'mac_info'); });
            rows(res[4]).forEach(function (l) { applyLease(l, 'hwaddr', 'address', 'hostname', 'if', 'if_descr', 'mac_info'); });
            rows(res[5]).forEach(function (l) { applyLease(l, 'mac', 'address', 'hostname', 'if', 'if_descr', 'man'); });

            /* assign by subnet when ARP/lease did not tell us the interface */
            Object.keys(devices).forEach(function (mac) {
                var d = devices[mac];
                if (!d.ifid && d.ips.length) {
                    var hit = ifaces.filter(function (i) { return inSubnet(d.ips[0], i.addr4); })[0];
                    if (hit) {
                        d.ifid = hit.id;
                    }
                }
                d.ips.sort(function (a, b) { return ipSortKey(a) - ipSortKey(b); });
            });
            S.devices = devices;

            /* labels */
            if (res[6]) {
                var labels = {};
                rows(res[6]).forEach(function (r) {
                    var mac = normMac(r && r.mac);
                    if (mac) {
                        labels[mac] = { uuid: r.uuid, name: String(r.name || ''), icon: String(r.icon || '') };
                    }
                });
                S.labels = labels;
            }

            if (res[7] && typeof res[7] === 'object') {
                S.system = res[7];
            }
            S.loaded = true;
            S.updated = new Date();
        });
    }

    function inSubnet(ip, cidr) {
        var m = /^(\d+\.\d+\.\d+\.\d+)\/(\d+)$/.exec(String(cidr || ''));
        if (!m || !/^\d+\.\d+\.\d+\.\d+$/.test(ip)) {
            return false;
        }
        var bits = +m[2];
        if (bits <= 0) {
            return true;
        }
        var mask = bits >= 32 ? 0xffffffff : (~((1 << (32 - bits)) - 1)) >>> 0;
        return ((ipSortKey(ip) & mask) >>> 0) === ((ipSortKey(m[1]) & mask) >>> 0);
    }

    /* ------------------------------------------------------- live traffic */
    var trafficTimer = null;
    var trafficBusy = false;

    function lanIds() {
        return S.interfaces.filter(function (i) { return !i.uplink && i.status === 'up'; })
            .map(function (i) { return i.id; });
    }

    function pollTraffic() {
        clearTimeout(trafficTimer);
        trafficTimer = null;
        if (!S.live || trafficBusy) {
            return;
        }
        var ids = lanIds();
        if (!ids.length || document.hidden) {
            trafficTimer = setTimeout(pollTraffic, 5000);
            return;
        }
        trafficBusy = true;
        fetchJson('/api/diagnostics/traffic/top/' + ids.map(encodeURIComponent).join(','), 30000).then(function (data) {
            var rates = {};
            if (data && typeof data === 'object') {
                Object.keys(data).forEach(function (intf) {
                    var recs = (data[intf] && data[intf].records) || [];
                    recs.forEach(function (r) {
                        if (r && r.address) {
                            var cur = rates[r.address] || { in: 0, out: 0 };
                            cur.in += +r.rate_bits_in || 0;
                            cur.out += +r.rate_bits_out || 0;
                            rates[r.address] = cur;
                        }
                    });
                });
            }
            S.rates = rates;
            updateRates();
            pulseLive();
        }).then(function () {
            trafficBusy = false;
            if (S.live) {
                trafficTimer = setTimeout(pollTraffic, 4000);
            }
        });
    }

    function pulseLive() {
        var dot = root.querySelector('.usn-live-dot');
        var reduce = false;
        try {
            reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (e) { /* ignore */ }
        if (dot && typeof dot.animate === 'function' && !reduce) {
            dot.animate([{ opacity: 0.2 }, { opacity: 1 }], { duration: 700, easing: 'ease-out' });
        }
    }

    function deviceRate(d) {
        var r = { in: 0, out: 0 };
        d.ips.forEach(function (ip) {
            var x = S.rates[ip];
            if (x) {
                r.in += x.in;
                r.out += x.out;
            }
        });
        return r;
    }

    function rateHtml(r, short) {
        if (!r || (!r.in && !r.out)) {
            return '';
        }
        var f = short ? fmtBitsShort : fmtBits;
        return '<span class="usn-rate" title="' + esc(fmtBits(r.in) + ' / ' + fmtBits(r.out)) + '">' +
            '<span class="usn-rate-dl"><i class="fa fa-arrow-down"></i>' + esc(f(r.in)) + '</span> ' +
            '<span class="usn-rate-ul"><i class="fa fa-arrow-up"></i>' + esc(f(r.out)) + '</span></span>';
    }

    function updateRates() {
        var total = { in: 0, out: 0 };
        root.querySelectorAll('[data-mac]').forEach(function (el) {
            var d = S.devices[el.getAttribute('data-mac')];
            var slot = el.querySelector('.usn-tile-rate');
            if (!d || !slot) {
                return;
            }
            var r = deviceRate(d);
            setHtml(slot, S.live ? rateHtml(r, true) : '');
            el.classList.toggle('is-active', !!S.live && d.online && (r.in + r.out) > 16000);
        });
        Object.keys(S.devices).forEach(function (mac) {
            var r = deviceRate(S.devices[mac]);
            total.in += r.in;
            total.out += r.out;
        });
        var tot = root.querySelector('[data-usn-total]');
        if (tot) {
            setHtml(tot, S.live ? ('<span class="usn-nw">' + esc(fmtBits(total.in)) + '</span> <small>/</small> <span class="usn-nw">' + esc(fmtBits(total.out)) + '</span>') : '&mdash;');
        }
        var perNet = {};
        var other = { in: 0, out: 0 };
        var lan = {};
        S.interfaces.forEach(function (i) {
            if (!i.uplink) {
                lan[i.id] = true;
            }
        });
        Object.keys(S.devices).forEach(function (mac) {
            var d = S.devices[mac];
            var x = deviceRate(d);
            var bucket = lan[d.ifid] ? (perNet[d.ifid] = perNet[d.ifid] || { in: 0, out: 0 }) : other;
            bucket.in += x.in;
            bucket.out += x.out;
        });
        root.querySelectorAll('[data-usn-net-rate]').forEach(function (el) {
            setHtml(el, S.live ? rateHtml(perNet[el.getAttribute('data-usn-net-rate')] || { in: 0, out: 0 }) : '');
        });
        var fwRate = root.querySelector('[data-usn-total-rate]');
        if (fwRate) {
            setHtml(fwRate, S.live ? rateHtml(total) : '');
        }
        updateFlows(total, perNet, other);
        root.classList.toggle('usn-live', !!S.live);
    }

    /* ------------------------------------------------------------- render */
    function label(d) {
        var l = S.labels[d.mac];
        return (l && l.name) || d.hostname || d.vendor || d.ips[0] || d.mac;
    }

    function iconKey(d) {
        var l = S.labels[d.mac];
        if (l && l.icon && l.icon !== 'auto' && ICONS[l.icon]) {
            return l.icon;
        }
        return guessIcon(d);
    }

    function matches(d) {
        if (!S.showOffline && !d.online) {
            return false;
        }
        if (S.filter !== 'all' && d.ifid !== S.filter) {
            return false;
        }
        if (!S.query) {
            return true;
        }
        var hay = [label(d), d.hostname, d.vendor, d.mac, d.ips.join(' '), d.ifname].join(' ').toLowerCase();
        return S.query.split(/\s+/).every(function (w) { return hay.indexOf(w) !== -1; });
    }

    function gatewayChip(g) {
        var st = String(g.status_translated || g.status || '').toLowerCase();
        var raw = String(g.status || '').toLowerCase();
        var cls = 'ok';
        if (raw === 'down' || raw === 'force_down' || st.indexOf('offline') !== -1) {
            cls = 'crit';
        } else if (raw !== 'none' && raw !== 'online' && st !== 'online') {
            cls = 'warn';
        }
        return '<span class="usn-chip usn-chip-' + cls + '">' + esc(g.status_translated || g.status || '') + '</span>';
    }

    function renderShell() {
        root.innerHTML =
            '<div class="usn-toolbar">' +
            '  <div class="usn-search"><i class="fa fa-magnifying-glass" aria-hidden="true"></i>' +
            '    <input type="search" class="form-control" data-usn="query" placeholder="' + esc(t('search', 'Search')) + '" aria-label="' + esc(t('search', 'Search')) + '"></div>' +
            '  <div class="usn-filters" data-usn="filters" role="tablist"></div>' +
            '  <div class="usn-tools">' +
            '    <label class="usn-switch"><input type="checkbox" data-usn="offline"> <span>' + esc(t('showOffline', 'Show offline')) + '</span></label>' +
            '    <label class="usn-switch"><input type="checkbox" data-usn="live"> <span>' + esc(t('live', 'Live traffic')) + '</span></label>' +
            '    <button type="button" class="btn btn-default btn-sm" data-usn="refresh" title="' + esc(t('refresh', 'Refresh')) + '" aria-label="' + esc(t('refresh', 'Refresh')) + '"><i class="fa fa-arrows-rotate"></i></button>' +
            '  </div>' +
            '</div>' +
            '<div class="usn-stats" data-usn="stats"></div>' +
            '<div class="usn-map" data-usn="map"></div>';

        var q = root.querySelector('[data-usn="query"]');
        q.addEventListener('input', function () {
            S.query = q.value.trim().toLowerCase();
            renderMap();
        });
        var off = root.querySelector('[data-usn="offline"]');
        off.checked = S.showOffline;
        off.addEventListener('change', function () {
            S.showOffline = off.checked;
            savePrefs();
            renderAll();
        });
        var live = root.querySelector('[data-usn="live"]');
        live.checked = S.live;
        live.addEventListener('change', function () {
            S.live = live.checked;
            savePrefs();
            if (S.live) {
                pollTraffic();
            } else {
                clearTimeout(trafficTimer);
                trafficTimer = null;
                S.rates = {};
            }
            updateRates();
        });
        root.querySelector('[data-usn="refresh"]').addEventListener('click', function () {
            refresh(true);
        });
        root.querySelector('[data-usn="filters"]').addEventListener('click', function (e) {
            var b = e.target.closest ? e.target.closest('button[data-filter]') : null;
            if (b) {
                S.filter = b.getAttribute('data-filter');
                renderAll();
            }
        });
        root.querySelector('[data-usn="map"]').addEventListener('click', function (e) {
            var hitTile = e.target.closest ? e.target.closest('[data-mac]') : null;
            if (hitTile) {
                openDevice(hitTile.getAttribute('data-mac'));
            }
        });
        root.querySelector('[data-usn="map"]').addEventListener('keydown', function (e) {
            if ((e.key === 'Enter' || e.key === ' ') && e.target.hasAttribute && e.target.hasAttribute('data-mac')) {
                e.preventDefault();
                openDevice(e.target.getAttribute('data-mac'));
            }
        });
    }

    function renderFilters() {
        var box = root.querySelector('[data-usn="filters"]');
        if (!box) {
            return;
        }
        var lans = S.interfaces.filter(function (i) { return !i.uplink; });
        if (S.filter !== 'all' && !S.interfaces.some(function (i) { return i.id === S.filter; })) {
            S.filter = 'all';
        }
        var html = '<button type="button" role="tab" data-filter="all" class="' + (S.filter === 'all' ? 'is-active' : '') +
            '" aria-selected="' + (S.filter === 'all') + '">' + esc(t('all', 'All')) + '</button>';
        lans.forEach(function (i) {
            var on = S.filter === i.id;
            html += '<button type="button" role="tab" data-filter="' + esc(i.id) + '" class="' + (on ? 'is-active' : '') +
                '" aria-selected="' + on + '">' + esc(i.name) + '</button>';
        });
        setHtml(box, html);
    }

    function renderStats() {
        var box = root.querySelector('[data-usn="stats"]');
        if (!box) {
            return;
        }
        var macs = Object.keys(S.devices);
        var online = macs.filter(function (m) { return S.devices[m].online; }).length;
        var lans = S.interfaces.filter(function (i) { return !i.uplink; });
        var gwOk = S.gateways.filter(function (g) {
            var s = String(g.status || '').toLowerCase();
            return s === 'none' || s === 'online';
        }).length;
        setHtml(box,
            stat('fa-laptop', t('devices', 'Devices'), online + ' <small>' + esc(t('online', 'online').toLowerCase()) + '</small>',
                (macs.length - online) + ' ' + esc(t('offline', 'offline').toLowerCase())) +
            stat('fa-sitemap', t('networks', 'Networks'), String(lans.length),
                lans.map(function (i) { return esc(i.name); }).join(' · ')) +
            stat('fa-globe', t('gateways', 'Gateways'), S.gateways.length ? (gwOk + ' <small>/ ' + S.gateways.length + '</small>') : '&mdash;',
                S.gateways.map(function (g) { return esc(g.name); }).join(' · ')) +
            stat('fa-gauge-high', t('traffic', 'Traffic'), '<span data-usn-total>&mdash;</span>',
                '<i class="fa fa-arrow-down"></i> / <i class="fa fa-arrow-up"></i>' +
                (S.updated ? ' · ' + esc(t('updated', 'Updated')) + ' ' + esc(S.updated.toLocaleTimeString()) : ''), 'usn-stat-traffic'));
        updateRates();
    }

    function stat(icon, title, value, sub, cls) {
        return '<div class="usn-stat' + (cls ? ' ' + cls : '') + '"><div class="usn-stat-icon"><i class="fa ' + icon + '" aria-hidden="true"></i></div>' +
            '<div class="usn-stat-body"><div class="usn-stat-title">' + esc(title) +
            (cls === 'usn-stat-traffic' ? '<span class="usn-live-dot" aria-hidden="true"></span>' : '') + '</div>' +
            '<div class="usn-stat-value">' + value + '</div><div class="usn-stat-sub">' + (sub || '&nbsp;') + '</div></div></div>';
    }

    /*
     * Connectors are small SVGs laid over the board, one per link, each on its
     * own compositor layer so a moving link repaints only its own few pixels:
     *   Internet -> firewall, firewall -> each network card (a curve), and
     *   network card -> its device lane.
     * Live data flow is a "string" of dots along each link: download dots
     * (accent) travel towards the devices, upload dots (green) travel back.
     * The dots move by animating stroke-dashoffset with the Web Animations
     * API; the traffic rate sets the speed through updatePlaybackRate(), so a
     * speed change never makes the string jump. Idle links stay a plain line.
     */
    var DOT_GAP = 10;          /* px from dot to dot along a link */

    function flowSpeed(bps) {
        if (!(bps > 8000)) {
            return 0;
        }
        /* 10 kbit/s ~0.75x, 1 Mbit/s ~2.25x, 100 Mbit/s ~3.75x */
        return Math.max(0.5, Math.min(4, Math.log10(bps / 1000) * 0.75));
    }

    function reduceMotion() {
        try {
            return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        } catch (e) {
            return false;
        }
    }

    /* fwd: dots travel along the path (towards the devices) or back */
    function drivePath(path, bps, fwd) {
        var speed = S.live ? flowSpeed(bps) : 0;
        path.classList.toggle('is-on', speed > 0);
        if (reduceMotion() || typeof path.animate !== 'function') {
            return;
        }
        if (!path.__usnAnim) {
            var off = fwd ? [0, -DOT_GAP] : [0, DOT_GAP];
            path.__usnAnim = path.animate([{ strokeDashoffset: off[0] }, { strokeDashoffset: off[1] }],
                { duration: 520, iterations: Infinity });
            path.__usnAnim.pause();
        }
        var a = path.__usnAnim;
        if (speed > 0) {
            if (typeof a.updatePlaybackRate === 'function') {
                a.updatePlaybackRate(speed);
            } else {
                a.playbackRate = speed;
            }
            if (a.playState !== 'running') {
                a.play();
            }
        } else if (a.playState === 'running') {
            a.pause();
        }
    }

    function updateFlows(total, perNet, other) {
        root.querySelectorAll('[data-usn-wire]').forEach(function (w) {
            var key = w.getAttribute('data-usn-wire');
            var r = key === 'other' ? other :
                key.indexOf('net:') === 0 ? (perNet[key.slice(4)] || { in: 0, out: 0 }) : total;
            var p = w.querySelectorAll('.usn-link-flow');
            if (p.length === 2) {
                drivePath(p[0], r.in, true);
                drivePath(p[1], r.out, false);
            }
            w.classList.toggle('is-busy', !!S.live && (r.in + r.out) > 8000);
        });
    }

    /* draw the connectors from where the cards actually are */
    function layoutLinks() {
        var board = root.querySelector('[data-usn-board]');
        var layer = board && board.querySelector('.usn-links');
        if (!layer) {
            return;
        }
        var b = board.getBoundingClientRect();
        var box = function (el) {
            var r = el.getBoundingClientRect();
            return { l: r.left - b.left, t: r.top - b.top, r: r.right - b.left, b: r.bottom - b.top, w: r.width, h: r.height };
        };
        var links = [];
        var inet = board.querySelector('.usn-node-internet');
        var fw = board.querySelector('.usn-node-fw');
        if (inet && fw) {
            var i = box(inet), f = box(fw);
            /* narrow boards stack (CSS container query): networks under the core */
            var rowsEl = board.querySelector('.usn-rows');
            var stacked = rowsEl ? box(rowsEl).t >= Math.max(i.b, f.b) - 1 : true;
            if (f.t >= i.b - 1) {
                links.push(['wan', i.l + i.w / 2, i.b, f.l + f.w / 2, f.t]);
            } else {
                links.push(['wan', i.r, i.t + i.h / 2, f.l, f.t + f.h / 2]);
            }
            if (!stacked) {
                board.querySelectorAll('[data-usn-row]').forEach(function (row) {
                    var key = row.getAttribute('data-usn-row');
                    var card = box(row.querySelector('.usn-netcard'));
                    var lane = box(row.querySelector('.usn-lane'));
                    var cy = card.t + card.h / 2;
                    links.push([key, f.r, f.t + f.h / 2, card.l, cy, true]);
                    links.push([key, card.r, cy, lane.l, cy]);
                });
            }
        }
        var sig = JSON.stringify(links.map(function (x) {
            return x.map(function (v) { return typeof v === 'number' ? Math.round(v) : v; });
        }));
        if (layer.__usnSig === sig) {
            return;
        }
        layer.__usnSig = sig;
        var pad = 6;
        layer.innerHTML = links.map(function (k) {
            var x1 = k[1], y1 = k[2], x2 = k[3], y2 = k[4];
            var left = Math.min(x1, x2) - pad, top = Math.min(y1, y2) - pad;
            var w = Math.max(1, Math.abs(x2 - x1)) + pad * 2, h = Math.max(1, Math.abs(y2 - y1)) + pad * 2;
            var ax = x1 - left, ay = y1 - top, bx = x2 - left, by = y2 - top;
            var d = k[5] ? 'M' + ax + ' ' + ay + 'C' + (ax + (bx - ax) * 0.55) + ' ' + ay + ' ' + (bx - (bx - ax) * 0.55) + ' ' + by + ' ' + bx + ' ' + by :
                'M' + ax + ' ' + ay + 'L' + bx + ' ' + by;
            return '<svg class="usn-link" data-usn-wire="' + esc(k[0]) + '" width="' + w.toFixed(1) + '" height="' + h.toFixed(1) +
                '" viewBox="0 0 ' + w.toFixed(1) + ' ' + h.toFixed(1) + '" style="left:' + left.toFixed(1) + 'px;top:' + top.toFixed(1) + 'px">' +
                '<path class="usn-link-base" d="' + d + '"/><path class="usn-link-flow usn-link-dl" d="' + d + '"/>' +
                '<path class="usn-link-flow usn-link-ul" d="' + d + '"/></svg>';
        }).join('');
        updateRates();
    }

    function tile(d) {
        var k = iconKey(d);
        var meta = [d.ips[0] || '', d.vendor || ''].filter(Boolean).map(esc).join(' · ');
        return '<div class="usn-tile' + (d.online ? '' : ' is-offline') + '" data-mac="' + esc(d.mac) + '" role="button" tabindex="0"' +
            ' title="' + esc(label(d)) + '">' +
            '<span class="usn-tile-icon"><i class="fa ' + ICONS[k] + '" aria-hidden="true"></i></span>' +
            '<span class="usn-tile-text"><span class="usn-tile-name"><span class="usn-tile-label">' + esc(label(d)) + '</span>' +
            '<span class="usn-live-ind' + (d.online ? ' is-on' : '') + '" role="img" aria-label="' +
            esc(d.online ? t('online', 'Online') : t('offline', 'Offline')) + '">' +
            (d.online ? '<span class="usn-ripple" aria-hidden="true"></span>' : '') + '<span class="usn-dot' + (d.online ? ' is-on' : '') + '"></span></span></span>' +
            '<span class="usn-tile-meta">' + (meta || esc(d.mac)) + '</span></span>' +
            '<span class="usn-tile-rate"></span>' +
            '</div>';
    }

    function renderMap() {
        var map = root.querySelector('[data-usn="map"]');
        if (!map) {
            return;
        }
        if (S.failed) {
            setHtml(map, '<div class="alert alert-warning">' + esc(t('error', 'Could not load network data.')) + '</div>');
            return;
        }

        /* left column: Internet above the firewall */
        var uplinks = S.interfaces.filter(function (i) { return i.uplink; });
        var sys = S.system || {};
        var host = String(sys.name || '').trim() || 'OPNsense';
        var ver = Array.isArray(sys.versions) ? String(sys.versions[0] || '') : '';

        var html = '<div class="usn-board" data-usn-board><div class="usn-links" aria-hidden="true"></div><div class="usn-core">';
        html += '<div class="usn-node usn-node-internet"><span class="usn-node-icon"><i class="fa fa-globe" aria-hidden="true"></i></span>' +
            '<b class="usn-node-name">' + esc(t('internet', 'Internet')) + '</b>';
        if (S.gateways.length) {
            html += '<span class="usn-gws">' + S.gateways.map(function (g) {
                var delay = g.delay && g.delay !== '~' ? ' · ' + esc(g.delay) : '';
                return '<span class="usn-gw"><span class="usn-gw-name">' + esc(g.name) + ' ' + gatewayChip(g) + '</span>' +
                    '<small>' + esc(g.address && g.address !== '~' ? g.address : '') + delay + '</small></span>';
            }).join('') + '</span>';
        }
        html += '</div>';
        html += '<div class="usn-node usn-node-fw"><span class="usn-node-icon"><i class="fa fa-shield-halved" aria-hidden="true"></i></span>' +
            '<b class="usn-node-name">' + esc(host) + '</b><small class="usn-node-sub">' + esc(ver || t('firewall', 'Firewall')) + '</small>';
        if (uplinks.length) {
            html += '<span class="usn-wans">' + uplinks.map(function (u) {
                return '<span class="usn-wan"><span class="usn-dot ' + (u.status === 'up' ? 'is-on' : '') + '"></span>' +
                    esc(u.name) + ' <small>' + esc(u.addr4 || u.addr6 || u.status) + '</small></span>';
            }).join('') + '</span>';
        }
        html += '<span class="usn-node-rate" data-usn-total-rate></span></div></div>';

        /* right: one row per network — its card, then its devices */
        var nets = S.interfaces.filter(function (i) { return !i.uplink && (S.filter === 'all' || i.id === S.filter); });
        var macs = Object.keys(S.devices).filter(function (m) { return matches(S.devices[m]); });
        /* devices on an uplink (e.g. the ISP router) or on no known network */
        var lanSet = {};
        S.interfaces.forEach(function (i) {
            if (!i.uplink) {
                lanSet[i.id] = true;
            }
        });
        var unassigned = macs.filter(function (m) { return !lanSet[S.devices[m].ifid]; });

        function row(key, icon, name, sub, list, down, rateKey) {
            var online = list.filter(function (d) { return d.online; }).length;
            var r = '<div class="usn-row' + (down ? ' is-down' : '') + '" data-usn-row="' + esc(key) + '">' +
                '<section class="usn-netcard"><span class="usn-net-icon"><i class="fa ' + icon + '" aria-hidden="true"></i></span>' +
                '<span class="usn-net-title"><b>' + esc(name) + '</b><small>' + sub + '</small>' +
                '<span class="usn-net-meta"><span class="usn-chip">' + online + ' / ' + list.length + '</span>' +
                (rateKey ? '<span class="usn-net-rate" data-usn-net-rate="' + esc(rateKey) + '"></span>' : '') + '</span></span></section>' +
                '<div class="usn-lane' + (list.length ? '' : ' is-empty') + '">';
            r += list.length ? list.map(tile).join('') :
                '<span class="usn-empty">' + esc(S.query ? t('noMatch', 'No devices match your search.') : t('noDevices', 'No devices')) + '</span>';
            return r + '</div></div>';
        }

        html += '<div class="usn-rows">';
        var shown = 0;
        nets.forEach(function (n) {
            var list = macs.filter(function (m) { return S.devices[m].ifid === n.id; }).map(function (m) { return S.devices[m]; });
            list.sort(function (a, b) {
                if (a.online !== b.online) {
                    return a.online ? -1 : 1;
                }
                return ipSortKey(a.ips[0]) - ipSortKey(b.ips[0]) || label(a).localeCompare(label(b));
            });
            shown += list.length;
            html += row('net:' + n.id, 'fa-network-wired', n.name,
                esc(n.addr4 || n.addr6 || n.device) + (n.vlan ? ' · VLAN ' + esc(n.vlan) : ''), list, n.status !== 'up', n.id);
        });
        if (unassigned.length && S.filter === 'all') {
            shown += unassigned.length;
            html += row('other', 'fa-ethernet', t('upstream', 'Other'), esc(t('upstreamSub', 'outside your networks')),
                unassigned.map(function (m) { return S.devices[m]; }), false, '');
        }
        html += '</div></div>';
        if (!nets.length && !shown) {
            html += '<div class="usn-empty">' + esc(t('noDevices', 'No devices found.')) + '</div>';
        }
        var focused = document.activeElement && map.contains(document.activeElement) ?
            document.activeElement.getAttribute('data-mac') : null;
        if (setHtml(map, html)) {
            if (focused) {
                var again = map.querySelector('[data-mac="' + focused.replace(/"/g, '') + '"]');
                if (again) {
                    again.focus({ preventScroll: true });
                }
            }
            var board = map.querySelector('[data-usn-board]');
            if (board && window.ResizeObserver) {
                S.ro = S.ro || new ResizeObserver(function () {
                    layoutLinks();
                });
                S.ro.disconnect();
                S.ro.observe(board);
            }
            layoutLinks();
        }
        updateRates();
    }

    function renderAll() {
        renderFilters();
        renderStats();
        renderMap();
    }

    /* -------------------------------------------------------- device dialog */
    function openDevice(mac) {
        var d = S.devices[mac];
        if (!d) {
            return;
        }
        var l = S.labels[mac] || null;
        var current = (l && l.icon && ICONS[l.icon]) ? l.icon : 'auto';
        var guess = guessIcon(d);
        var r = deviceRate(d);
        var net = S.interfaces.filter(function (i) { return i.id === d.ifid; })[0];

        var info = [
            [t('status', 'Status'), '<span class="usn-chip usn-chip-' + (d.online ? 'ok' : 'neutral') + '">' + esc(d.online ? t('online', 'Online') : t('offline', 'Offline')) + '</span>'],
            [t('ip', 'IP address'), d.ips.length ? d.ips.map(esc).join('<br>') : '&mdash;'],
            [t('mac', 'MAC address'), '<code>' + esc(d.mac) + '</code>'],
            [t('vendor', 'Vendor'), esc(d.vendor) || '&mdash;'],
            [t('hostname', 'Hostname'), esc(d.hostname) || '&mdash;'],
            [t('network', 'Network'), esc(net ? net.name : d.ifname) || '&mdash;']
        ];
        if (S.live && (r.in || r.out)) {
            info.push([t('traffic', 'Traffic'), rateHtml(r)]);
        }

        var icons = '<button type="button" class="usn-icon-opt" data-icon="auto" aria-pressed="' + (current === 'auto') + '" title="Auto">' +
            '<i class="fa ' + ICONS[guess] + '"></i><span>Auto</span></button>';
        Object.keys(ICONS).forEach(function (k) {
            icons += '<button type="button" class="usn-icon-opt" data-icon="' + k + '" aria-pressed="' + (current === k) + '" title="' + esc(ICON_LABELS[k]) + '">' +
                '<i class="fa ' + ICONS[k] + '"></i><span>' + esc(ICON_LABELS[k]) + '</span></button>';
        });

        var $body = $('<div class="usn-dialog"></div>');
        $body.html(
            '<table class="table table-condensed usn-info"><tbody>' +
            info.map(function (row) { return '<tr><th>' + esc(row[0]) + '</th><td>' + row[1] + '</td></tr>'; }).join('') +
            '</tbody></table>' +
            '<div class="form-group"><label for="usn-name">' + esc(t('name', 'Name')) + '</label>' +
            '<input id="usn-name" type="text" class="form-control" maxlength="64" autocomplete="off"></div>' +
            '<div class="form-group"><label>' + esc(t('icon', 'Icon')) + '</label><div class="usn-icon-grid">' + icons + '</div></div>' +
            '<div class="usn-dialog-msg text-danger" role="alert"></div>'
        );
        var $name = $body.find('#usn-name');
        $name.val(l && l.name ? l.name : '');
        $name.attr('placeholder', d.hostname || d.vendor || d.ips[0] || d.mac);
        var chosen = current;
        $body.on('click', '.usn-icon-opt', function () {
            chosen = $(this).attr('data-icon');
            $body.find('.usn-icon-opt').attr('aria-pressed', 'false');
            $(this).attr('aria-pressed', 'true');
        });

        var buttons = [];
        if (l) {
            buttons.push({
                label: t('reset', 'Reset'),
                cssClass: 'btn-default pull-left',
                action: function (dlg) {
                    save(d.mac, '', 'auto', dlg, $body);
                }
            });
        }
        buttons.push({
            label: t('cancel', 'Cancel'),
            cssClass: 'btn-default',
            action: function (dlg) { dlg.close(); }
        });
        buttons.push({
            label: t('save', 'Save'),
            cssClass: 'btn-primary',
            hotkey: 13,
            action: function (dlg) {
                save(d.mac, String($name.val() || '').trim(), chosen, dlg, $body);
            }
        });

        var title = label(d);
        if (typeof BootstrapDialog === 'undefined') {
            return;
        }
        BootstrapDialog.show({
            title: $('<span></span>').text(title),
            message: $body,
            type: BootstrapDialog.TYPE_DEFAULT,
            cssClass: 'usn-modal',
            buttons: buttons,
            onshown: function () { $name.trigger('focus'); }
        });
    }

    function save(mac, name, icon, dlg, $body) {
        var $msg = $body.find('.usn-dialog-msg');
        $msg.text('');
        if (name.length > 64 || /[<>\x00-\x1f\x7f]/.test(name)) {
            $msg.text('Use up to 64 characters (no < or >).');
            return;
        }
        if (icon === 'auto') {
            icon = '';
        }
        var l = S.labels[mac];
        var req;
        if (!name && !icon) {
            req = l ? postJson('/api/unisense/networkmap/del_device/' + encodeURIComponent(l.uuid), {}) :
                Promise.resolve({ result: 'deleted' });
        } else if (l) {
            req = postJson('/api/unisense/networkmap/set_device/' + encodeURIComponent(l.uuid), { device: { mac: mac, name: name, icon: icon } });
        } else {
            req = postJson('/api/unisense/networkmap/add_device', { device: { mac: mac, name: name, icon: icon } });
        }
        dlg.enableButtons(false);
        req.then(function (res) {
            dlg.enableButtons(true);
            if (res && (res.result === 'saved' || res.result === 'deleted')) {
                if (!name && !icon) {
                    delete S.labels[mac];
                } else {
                    S.labels[mac] = { uuid: res.uuid || (l && l.uuid) || '', name: name, icon: icon };
                }
                dlg.close();
                renderAll();
                /* sync uuids and validation state with the server */
                fetchJson('/api/unisense/networkmap/search_device').then(function (data) {
                    if (data) {
                        var labels = {};
                        rows(data).forEach(function (r) {
                            var m = normMac(r && r.mac);
                            if (m) {
                                labels[m] = { uuid: r.uuid, name: String(r.name || ''), icon: String(r.icon || '') };
                            }
                        });
                        S.labels = labels;
                        renderAll();
                    }
                });
                return;
            }
            var msg = t('saveError', 'Could not save the device label.');
            if (res && res.validations && typeof res.validations === 'object') {
                var v = Object.keys(res.validations).map(function (k) { return res.validations[k]; });
                if (v.length) {
                    msg = v.join(' ');
                }
            } else if (res && res.errorMessage) {
                msg = res.errorMessage;
            }
            $msg.text(msg);
        });
    }

    /* ---------------------------------------------------------- lifecycle */
    var refreshTimer = null;
    var refreshing = false;

    function refresh(manual) {
        if (refreshing) {
            return;
        }
        refreshing = true;
        clearTimeout(refreshTimer);
        var btn = root.querySelector('[data-usn="refresh"] i');
        if (btn && manual) {
            btn.classList.add('fa-spin');
        }
        load().catch(function () {
            S.failed = !S.loaded;
        }).then(function () {
            refreshing = false;
            if (btn) {
                btn.classList.remove('fa-spin');
            }
            renderAll();
            if (S.live && !trafficTimer && !trafficBusy) {
                pollTraffic();
            }
            refreshTimer = setTimeout(function () { refresh(false); }, 30000);
        });
    }

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            refresh(false);
            if (S.live) {
                pollTraffic();
            }
        }
    });

    function start() {
        renderShell();
        refresh(false);   /* starts live traffic once the interfaces are known */
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }

    /* exposed for tests */
    window.UniSenseNetworkMap = { guessIcon: guessIcon, inSubnet: inSubnet, state: S };
})();
