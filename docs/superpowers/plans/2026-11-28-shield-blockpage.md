# 1.7.0 Shield block page and unblock requests: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One tabbed Shield page (Overview / Threats / Privacy / Requests), dismissible alerts, a block page served by the firewall, and unblock requests the admin approves or declines.

**Architecture:**
- A loopback virtual IP (default `198.18.0.80`) is the address Unbound's UniSense Privacy policy answers for blocked names.
- A stdlib Python server (`blockpage.py`, running as `www`) on that VIP serves the page over HTTP. Over HTTPS, it signs a per-host certificate with the CA the admin picked.
- Requests land in a JSON file under Shield's lock. `shield.py` lists, approves and declines them, and prunes timed domain allows.

**Tech Stack:** Python 3 stdlib (http.server, ssl, socketserver), `openssl` CLI, OPNsense MVC (PHP models: Vip, Filter, DNat, Unbound), vanilla JS/CSS, Playwright harness.

**Spec:** `docs/superpowers/specs/2026-11-28-shield-blockpage-design.md`

## Global Constraints
- OPNsense 25.7, 26.1 and 26.7 must all work. DNat (the Shield threat redirect) is 26.1+ only and silently skipped on 25.7.
- Default address is `198.18.0.80`. The request host is `unblock.<system domain>`.
- Request limits:

  | Limit | Value |
  |---|---|
  | Name | 60 characters |
  | Reason | 100 words and 800 characters |
  | Requests per client IP | 5 per hour |
  | Pending per IP | 10 |
  | Total kept | 200 |
  | Request body | 4 KB |
  | Declined requests kept | 30 days |

- Approve durations are `1h|6h|24h|1mo|forever`. They reuse Shield's `DURATIONS`; the UI shows 1h / 24h / 1 month / forever.
- No external assets on the block page. All user text is HTML-escaped.
- Everything the setup creates is recorded in `managed` and removed when switched off and on uninstall (`install.sh backend_off`, `+PRE_DEINSTALL.post`).
- Gaming: the page only answers already-blocked names and addresses, only on tcp 80/443.

## Review Focus
1. **A blocked HTTPS visit from a device without the CA, or with no CA chosen.** It must fail fast: the TLS handshake is refused or reset, with no 30-second hang. Test: `test_https_without_ca_refused_fast` (Task 2).
2. **A hostile request body** (HTML/script in name or reason, over 100 words, non-UTF-8, 1 MB POST). It must be rejected or escaped, never stored raw and never crash the server. Tests: `test_request_escaped`, `test_request_too_long`, `test_body_cap` (Task 2).
3. **Changing the address or switching off while the server runs.** The old VIP, rule and host override must be gone, and the server restarted on the new address. Real-PHP check: "address change replaces VIP" (Task 3).
4. **Approving a domain that is already in the permanent allow list, or approving twice.** No duplicate allow, and the request still shows as approved. Test: `test_approve_idempotent` (Task 1).
5. **A timed domain approval expiring.** The domain leaves the Privacy allow list and Unbound is resynced. Test: `test_domain_allow_expires_and_resyncs` (Task 1).

---

### Task 1: Requests, dismissible alerts and timed domain allows (shield.py)

**Files:**
- Modify: `misc/theme-unisense/src/opnsense/scripts/unisense/shield.py`
- Modify: `misc/theme-unisense/src/opnsense/service/conf/actions.d/actions_unisense.conf`
- Test: `tests/test-shield.py`

**Interfaces:**
- Produces (CLI, JSON out):
  - `shield.py requests`
    → `{ok, pending, requests:[{id,t,ip,device,name,site,kind,reason,status,until,why}]}`
  - `shield.py request add` (reads JSON from stdin, used by blockpage.py)
    → `{ok}` or `{ok:false,error:'rate'|'full'|'invalid'}`
  - `shield.py request approve <id> <dur>` / `request decline <id>`
  - `shield.py dismiss <device> <remote>`
- Produces (Python, imported by blockpage.py):
  - `REQUESTS_FILE` (`SHIELD_DIR/requests.json`);
  - `add_request(req: dict, now_ts: float) -> str`, which returns `'ok'|'rate'|'full'|'invalid'` and enforces every limit from Global Constraints;
  - `Locked` (existing).
- Domain approvals go in state.json `domain_allows: {domain: {until, dur, added}}`.
  - `prune_allows()` also prunes these.
  - When any are added or removed it runs `/usr/local/opnsense/scripts/unisense/setup_privacy.php sync` (overridable by env `UNISENSE_PRIVACY_SYNC`).
  - `forever` domain approvals call `privacy.py rule allow <domain>` instead.
- Alerts:
  - state `dismissed: {"device|remote": ts}`;
  - `cmd_alerts` drops an alert whose `last <= dismissed ts`;
  - `unseen` and `status.alerts` ignore dismissed alerts.
- `status` adds `requests_pending: int` for the menu badge.

- [ ] **Step 1: Write failing tests in `tests/test-shield.py`:**
  - `test_request_add_and_list`: valid request → `'ok'`; `requests` shows status `pending`.
  - `test_request_limits`: the 6th request from one IP within an hour → `'rate'`; reason of 101 words → `'invalid'`; name of 61 characters → `'invalid'`.
  - `test_request_escaped`: stored as given (escaping is the renderer's job); a control character is stripped.
  - `test_approve_domain_timed`: approve `1h` → `domain_allows[site].until ≈ now+3600`; the sync command is called once (fake script writes a marker).
  - `test_approve_idempotent`: a second approve does no second sync and keeps status `approved`.
  - `test_domain_allow_expires_and_resyncs`: `until` in the past → after `apply`, the entry is gone and sync ran.
  - `test_approve_address_uses_allow`: kind `address` → the entry appears in `allows`.
  - `test_decline_and_retention`: declined → `status: declined`; a declined request older than 30 days is pruned.
  - `test_dismiss_alert`: dismiss hides the alert; a later hit with a newer `last` brings it back as new.

- [ ] **Step 2:** Run `python3 tests/test-shield.py`. The new tests fail.
- [ ] **Step 3: Implement.**
  - Write `add_request`, `cmd_requests`, `cmd_request`, `cmd_dismiss` and extend `prune_allows`, under the existing `Locked`.
  - Request ids are `secrets.token_hex(6)`.
  - `device` is resolved from the Network Map name of the IP, if available, as `alerts` already does.
  - configd actions (`parameters:%s %s %s`):
    - `shield.requests`: no message (it is polled);
    - `shield.request`;
    - `shield.dismiss`.
- [ ] **Step 4:** Run `python3 tests/test-shield.py`. Everything passes.
- [ ] **Step 5:** Commit: `Shield: unblock requests, dismissible alerts, timed domain allows`.

### Task 2: Block page server (blockpage.py)

**Files:**
- Create: `misc/theme-unisense/src/opnsense/scripts/unisense/blockpage.py`
- Create: `misc/theme-unisense/src/opnsense/etc/rc.syshook.d/start/60-unisense-blockpage`
- Modify: `actions_unisense.conf` (`blockpage.start|stop|restart|status`)
- Test: `tests/test-blockpage.py`

**Interfaces:**
- Consumes: `shield.add_request`, `shield.REQUESTS_FILE`, `shield.Lists`, `privacy.py` stats access (latest blocked entry for a domain).
- Config: `/var/unisense/blockpage/conf.json` `{address, domain, ca_crt, ca_key, enabled}`, written by setup (Task 3). Env `UNISENSE_BLOCKPAGE_CONF` overrides it for tests.
- CLI: `blockpage.py serve [--port-http N --port-https N]`, `start`, `stop`, `status` → `{ok, running, pid, address}`. Pidfile `/var/run/unisense-blockpage.pid`; runs via `daemon -u www`.
- Routes:
  - `GET /` with a Host other than `unblock.<domain>` → block page for that host;
  - `GET /` with Host `unblock.<domain>` → request form;
  - `POST /request` → thank-you page or an error page;
  - `GET /favicon.ico` → 204.
- Reason function: `why(host: str, client: str) -> {'kind':'domain'|'address','list':str}`.
  - Domain: the latest blocked stats row for host and client.
  - Address: original destination from `pfctl -ss` for client → VIP, then `Lists.find`.
  - Otherwise `{'list':'your rule'}`.
- HTTPS:
  - only when `ca_key` is set;
  - the SNI callback mints `certs/<host>.pem` via `openssl req -new` plus `openssl x509 -req -CA ... -days 397` with SAN set to the host, using one shared leaf key, cached;
  - host names are validated against `^[a-z0-9.-]{1,253}$` before any subprocess call.
  - Without a CA, port 443 is not listened on, so connections are refused straight away.
- Copy (exact):
  - title "Stopped by UniSense Shield";
  - line "<b>{host}</b> is blocked because {reason}.";
  - button "Request to unblock";
  - form labels "Your name", "Website", "Why do you need it? (100 words max)";
  - thanks "Sent. The network admin will look at it soon."

- [ ] **Step 1: Write failing tests in `tests/test-blockpage.py`.** Each test starts the server on 127.0.0.1 with free ports, a temporary Shield dir and a test CA made with openssl in setUp.
  - `test_block_page_http`: Host `ads.example` → 200; the body contains "Stopped by UniSense Shield" and `ads.example`.
  - `test_form_on_unblock_host`: Host `unblock.lan.internal` → the form.
  - `test_request_post`: posting a valid form → thanks; `requests.json` has 1 pending.
  - `test_request_escaped`: name `<script>x</script>` → the admin listing and the page never contain a raw `<script>`.
  - `test_request_too_long`: 101-word reason → error page; nothing stored.
  - `test_body_cap`: a 1 MB POST → 413; the server is still alive afterwards.
  - `test_https_minted`: TLS with SNI `ads.example` → cert SAN = `ads.example`, verifies against the test CA.
  - `test_https_without_ca_refused_fast`: no CA → connecting to the https port is refused in under 1 s.
  - `test_bad_sni`: SNI `../../x` → handshake fails; no file is written outside `certs/`.
- [ ] **Step 2:** Run `python3 tests/test-blockpage.py`. Everything fails (no module).
- [ ] **Step 3: Implement `blockpage.py`.**
  - `ThreadingHTTPServer` per port with `daemon_threads`.
  - Page CSS inline, matching UniSense dark.
  - `html.escape` on every value.
  - The syshook runs `blockpage.py start` when `conf.json` has `enabled`.
- [ ] **Step 4:** Run `python3 tests/test-blockpage.py`. Everything passes.
- [ ] **Step 5:** Commit: `Block page server with unblock requests`.

### Task 3: Setup: VIP, rule, host override, policy address, threat redirect

**Files:**
- Modify: `misc/theme-unisense/src/opnsense/mvc/app/models/OPNsense/Unisense/Shield.xml` (node `blockpage`: `enabled` Boolean default 0, `address` NetworkField ipv4 default `198.18.0.80`, `ca` Text, `managed` Text)
- Modify: `Privacy.php` (policy `address` = blockpage address when enabled, else empty)
- Create: `misc/theme-unisense/src/opnsense/mvc/app/models/OPNsense/Unisense/BlockPage.php` (helper class used by `Shield::syncAndSave`)
- Modify: `Shield.php` (`syncAndSave` calls `BlockPage::sync($remove, $errors)`)
- Modify: `install.sh`, `+PRE_DEINSTALL.post`, `scripts/check-plugin.sh`
- Test: `tests/test-real-php.sh` (new "Block page" section on all three cores), `tests/test-install.sh`

**Interfaces:**
- `BlockPage::sync(bool $remove, array &$errors): array`, returning `{vip, filter, nat, unbound, conf}` change flags.
  - VIP: `Interfaces\Vip` item, mode `ipalias`, interface `lo0`, subnet `<address>`, subnet_bits 32, descr `UniSense block page`.
  - Floating pass rule: interfaces = Privacy interfaces, dst `<address>/32`, tcp, ports `80,443` (via a port alias `unisense_web`), quick, sequence 5.
  - Unbound host override: hostname `unblock`, domain = system domain, server = address.
  - DNat (`class_exists`): tcp from Privacy interfaces to alias `unisense_threats` on 80/443 → `<address>`, same port.
  - Writes `conf.json`, extracting the CA crt/prv from `config->ca` by refid. The key is written mode 0600, owner `www`.
  - Every uuid is recorded in `managed` as `kind:uuid`.
- After saving, Shield's apply calls `interface vip reconfigure` (configd) when vip changed, `filter reload`, `unbound restart` when unbound changed, then `blockpage restart` or `stop`.
- API: `ShieldController::getAction` adds `blockpage` settings and `cas: [{refid, descr}]`.

- [ ] **Step 1: Add real-PHP checks to `tests/test-real-php.sh`.** Seed `interfaces/lo0` and a CA in the test config.
  - "block page on" → VIP on lo0 with `198.18.0.80/32`; pf renders a pass rule to `198.18.0.80` port 80,443; host override `unblock.<domain>` exists; Privacy policy address = `198.18.0.80`; DNat rule present on 26.1/26.7 and absent on 25.7.
  - "address change replaces VIP" → only one VIP, `198.18.0.81`, and the old uuid is gone.
  - "block page off" → VIP, rule, override and DNat are gone; the policy address is empty.
  - The uninstall check also confirms they are gone.
- [ ] **Step 2:** Run `sh tests/test-real-php.sh /home/claude/realcore/26.7`. The new checks fail.
- [ ] **Step 3:** Implement `BlockPage.php`, the model fields, the Privacy address hook, the apply actions, and removal in install/uninstall. check-plugin: shebang and py_compile for `blockpage.py`, and `shield.requests` has no message.
- [ ] **Step 4:** Run real-PHP on 25.7, 26.1 and 26.7, plus `sh tests/test-install.sh` and `sh scripts/check-plugin.sh`. Everything passes.
- [ ] **Step 5:** Commit: `Block page: loopback address, rule, unblock host, threat redirect`.

### Task 4: API and the tabbed Shield page

**Files:**
- Modify: `Api/ShieldController.php` (`requestsAction`, `requestAction` POST `{id, action: approve|decline, dur}`, `dismissAction` POST `{device, remote}`, `blockpageAction` GET status)
- Modify: `views/OPNsense/Unisense/shield.volt`, `www/js/unisense/shield.js`, `www/css/unisense/shield.css`
- Modify: `privacy.volt` → redirect stub to `/ui/unisense/shield#privacy`; `privacy.js` exports `UnisensePrivacy.mount(el)` instead of self-starting on `#upv`
- Modify: `Menu.xml` (remove the Privacy entry), `ACL.xml` (the Shield page ACL also covers `api/unisense/privacy/*`; the Privacy ACL is kept)
- Modify: `www/themes/unisense/build/js` menu badge source = `alerts + requests_pending`
- Test: `tests/harness/server.py`, `interact.py`, `render.py`, `shoot.py`

**Interfaces:**
- Tabs:
  - buttons `[data-ush-tab="overview|threats|privacy|requests"]`;
  - panes `[data-ush-pane=...]`;
  - the hash keeps the tab, and the Privacy pane mounts lazily the first time it opens.
- Requests table:
  - row `[data-ush-req=id]`;
  - approve menu reuses `allowBtn` with durations 1h / 24h / 1mo / forever;
  - Decline button `[data-ush-req-decline]`;
  - the tab label shows a count badge when requests are pending.
- Alerts:
  - seen alerts go in `<details class="ush-earlier">` "Earlier alerts (n)";
  - each alert has a Dismiss button `[data-ush-action="dismiss"]`.
- Block page card (Privacy tab):
  - switch `blockpage.enabled`;
  - address input;
  - CA select (first option "None: HTTP only");
  - test link `http://unblock.<domain>`;
  - status line from `blockpageAction`.

- [ ] **Step 1: Add harness tests in `interact.py`.** Mock server state: 2 pending requests, 3 alerts with 1 seen.
  - Tabs switch, and the hash restores the tab after reload.
  - `/ui/unisense/privacy` lands on the Privacy tab.
  - Approve 24h posts `{action:'approve', dur:'24h'}` and the row shows approved.
  - Decline posts `decline`.
  - Dismiss removes the alert row.
  - "Earlier alerts (1)" is collapsed.
  - The menu badge = 2 + unseen alerts.
  - The block page card saves `blockpage.enabled` and `ca`.
  - No console errors; the tables fit their cards at 1440, 800 and 390 px.
- [ ] **Step 2:** Run `python interact.py --core /home/claude/ref/core-26.7`. The new checks fail.
- [ ] **Step 3:** Implement the controller, volt, JS, CSS, menu and ACL.
- [ ] **Step 4:** Run interact and shoot on 25.7, 26.1 and 26.7, plus `npm run lint`. 0 failures.
- [ ] **Step 5:** Commit: `Shield: tabs with Privacy and Requests, dismissible alerts`.

### Task 5: Docs, release 1.7.0

**Files:** `README.md` (Shield section rewritten around tabs; block page and requests; HTTPS/CA note; FAQ "Why does my phone show a certificate warning?"), `.github/release-notes/1.7.0.md`, `misc/theme-unisense/pkg-descr`, `VERSION`, `Makefile`, `.github/workflows/ci.yml` (add `tests/test-blockpage.py`), `docs/screenshots` (shield tabs, requests, the block page itself via a harness route), `tests/harness/docs_shots.py`.

- [ ] **Step 1:** Write the docs. Bump to 1.7.0. Run `npm run build`.
- [ ] **Step 2:** Run every suite: lint, check-plugin, shellcheck, test-install, test-trafficmap, test-shield, test-privacy, test-blockpage, pyflakes, real-PHP ×3, interact ×3. All green.
- [ ] **Step 3:** Commit, push, and watch CI. The v1.7.0 release should have its 3 assets.
