# 1.7.0: One Shield page, block page, unblock requests

Approved by Mahabub on 2026-11-28.

## Why
Security features are spread over two pages. When something is blocked, people on the network see a broken site with no explanation and no way to ask for it. The admin, Mahabub on X1Firewall, wants to see why something was blocked and decide.

## Goals
1. **One Shield page with tabs.**
   - Overview: status, checkup, alerts.
   - Threats: countries, lists, allowed addresses.
   - Privacy.
   - Requests.
   - The Privacy menu entry is removed. `/ui/unisense/privacy` opens the Privacy tab.
2. **Alerts.**
   - "Mark as seen" folds seen alerts into a collapsed "Earlier alerts" row.
   - Each alert has Dismiss. A dismissed alert returns only on a new hit after the dismiss.
3. **Block page.**
   - A blocked site shows "Stopped by UniSense Shield", the site, the reason (list name / "your rule"), and a Request to unblock button.
4. **Requests.**
   - Fixed address `http://unblock.<system domain>` plus the button on the block page.
   - Form fields:

     | Field | Limit |
     |---|---|
     | Name | 60 characters |
     | Website | pre-filled |
     | Reason | 100 words, 800 characters |

   - Shield → Requests lists each request with Approve (1h / 24h / 1 month / forever) or Decline.
   - A menu badge counts pending requests plus unseen alerts.
   - Timed approvals expire by themselves, for domains and addresses.

## Non-goals
- No HTTPS page without a trusted CA: those devices get the browser warning, which is accepted.
- No block page for apps or games: they just fail.
- No block page for country blocks.

## Constraints (verified)
- Unbound DNSBL policy `address` (NetworkField, IPv4) exists on 25.7, 26.1 and 26.7. The dnsbl module answers A with it and gives an empty answer to AAAA.
- Virtual IPs on `lo0` are supported on all three. `interfaces_vips_configure('lo0')` and `loopback.inc` registers `lo0` as an interface.
- The DNat model exists only on 26.1+. Redirecting Shield threat web traffic therefore applies on 26.1+ only.
- Gaming must not be affected. The page only answers already-blocked names and addresses on ports 80/443.

## Approaches considered
- **A. Dedicated loopback VIP plus a small UniSense server (chosen).**
  - Works on every version.
  - Never touches the GUI's lighttpd.
  - The address isn't in any LAN subnet, so clients route it to the firewall.
- **B. Answer with the LAN IP and share lighttpd with the GUI.** Collides with the admin GUI on 80/443, and would expose an unauthenticated page inside the admin web server. Rejected.
- **C. Port-forward 80/443 for blocked answers to 127.0.0.1.** Needs DNat, which 25.7 doesn't have. Rejected as the base; used only for Shield threat addresses on 26.1+.

## Design
- **Settings** (Shield model `blockpage` node):

  | Setting | Value |
  |---|---|
  | enabled | — |
  | address | default `198.18.0.80`, benchmark range, never routed |
  | ca | refid from System → Trust → Authorities, optional |
  | interfaces | shared with Privacy's |

- **Firewall objects** (created by setup, recorded in `managed`, removed when off or on uninstall):
  - a VIP `address/32` on `lo0`;
  - a floating pass rule from the chosen interfaces to the VIP on tcp 80 and 443, placed before Shield's rules;
  - an Unbound host override `unblock.<domain>` pointing to the VIP;
  - the Privacy policy `address` set to the VIP;
  - on 26.1+, DNat rules: tcp 80/443 from the interfaces to `unisense_threats` go to the VIP.
- **Server** `scripts/unisense/blockpage.py`:
  - Python stdlib only. Started by configd `blockpage start|stop|status` and a `rc.syshook.d/start` hook. It runs as `www` and binds only the VIP.
  - HTTP serves the page or the form.
  - HTTPS is offered only when a CA is set. An SNI callback mints a per-host certificate with the `openssl` CLI, signed by the CA and cached in `/var/unisense/blockpage/certs`. The CA key is copied there with mode 0600, owner `www`.
  - Reason lookup:
    - domain: the latest blocked entry in Unbound stats for the client and domain, otherwise "your rule";
    - redirected threat address: the original destination from `pfctl -ss`, then Shield's `Lists.find`.
  - Limits:
    - 5 requests per client IP per hour;
    - 10 pending per IP;
    - 200 total;
    - body 4 KB.
  - Text is escaped, and there are no external assets.
- **Storage:** requests go in `/var/unisense/requests.json` under the shared lock:

  ```
  {id, t, ip, device name, name, site, kind domain|address, reason, status pending|approved|declined, until}
  ```

  Declined requests are kept for 30 days.
- **Approving:**
  - a domain becomes a Privacy allow with an expiry, stored in state, pruned by the 15-minute Shield cron, which resyncs Privacy;
  - an address uses the existing Shield `allow`.
- **UI:**
  - `shield.volt` gets tabs and pulls in the Privacy dashboard (`privacy.js` mounts into the tab).
  - A Requests tab.
  - A Block page card under Privacy: enable, address, CA select, test link.

## Left to the builder
Exact layout of the tabs and cards, which follows the existing Shield and Privacy styles, and the wording of the block page.
