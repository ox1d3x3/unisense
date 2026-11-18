<div align="center">

<img src="misc/theme-unisense/src/opnsense/www/themes/unisense/build/images/icon-logo.svg" width="84" alt="UniSense logo">

# UniSense

**A clean, controller-style theme for the OPNsense web GUI.**

Dark navigation rail · card layout · quiet tables · light, dark and auto modes · six accent colours · zero external requests.

[![CI](https://github.com/ox1d3x3/unisense/actions/workflows/ci.yml/badge.svg)](https://github.com/ox1d3x3/unisense/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ox1d3x3/unisense?color=006fff)](https://github.com/ox1d3x3/unisense/releases/latest)
[![License](https://img.shields.io/github/license/ox1d3x3/unisense?color=blue)](./LICENSE)
![OPNsense](https://img.shields.io/badge/OPNsense-25.7%20%7C%2026.1%20%7C%2026.7-d94f00)

[Screenshots](#screenshots) · [Install](#installation) · [Use](#using-the-theme) · [FAQ](#faq) · [Build](#building-from-source) · [Releases](https://github.com/ox1d3x3/unisense/releases/latest)

<br>

<img src="docs/screenshots/dashboard-dark.png" width="100%" alt="UniSense dashboard in dark mode">

</div>

### Quick install

Run on the firewall as root (**Shell**, option 8 on the console menu or over SSH):

```sh
fetch -o /tmp/unisense-install.sh https://github.com/ox1d3x3/unisense/releases/latest/download/install.sh && sh /tmp/unisense-install.sh
```

Then select **System → Settings → General → Theme → `unisense`** and press **Save**.

---

## Screenshots

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/dashboard-light.png" alt="Dashboard in light mode"></td>
    <td width="50%"><img src="docs/screenshots/sidebar-collapsed.png" alt="Collapsed icon rail with fly-out menus"></td>
  </tr>
  <tr>
    <td align="center"><sub><b>Light mode</b></sub></td>
    <td align="center"><sub><b>Collapsed icon rail with fly-out menus</b></sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/grid-light.png" alt="A data grid page"></td>
    <td width="50%"><img src="docs/screenshots/dialog-light.png" alt="An edit dialog"></td>
  </tr>
  <tr>
    <td align="center"><sub><b>Data grids</b></sub></td>
    <td align="center"><sub><b>Edit dialogs</b></sub></td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/legacy-dark.png" alt="A classic settings page in dark mode"></td>
    <td width="50%"><img src="docs/screenshots/login-dark.png" alt="Sign-in page"></td>
  </tr>
  <tr>
    <td align="center"><sub><b>Classic (PHP) pages styled too</b></sub></td>
    <td align="center"><sub><b>Sign-in page</b></sub></td>
  </tr>
  <tr>
    <td width="50%" align="center"><img src="docs/screenshots/mobile-dashboard.png" width="45%" alt="Dashboard on a phone"> <img src="docs/screenshots/mobile-menu.png" width="45%" alt="Menu on a phone"></td>
    <td width="50%"><img src="docs/screenshots/appearance.png" alt="Appearance menu"></td>
  </tr>
  <tr>
    <td align="center"><sub><b>Phones and tablets</b></sub></td>
    <td align="center"><sub><b>Appearance menu: mode and accent</b></sub></td>
  </tr>
</table>

---

## Features

**Look**
- Dark navigation rail in both modes, with an accent marker on the current page and a guide line for open sections
- Pages laid out as cards; dividers instead of zebra stripes; small uppercase column headings
- Rounded inputs, selects, tokens and buttons with a soft focus ring; native checkboxes and radios in the accent colour
- Status shown as soft chips (labels, alerts, gateway and service states)

**Modes and colours**
- **Light**, **Dark** or **System** (follows your OS, live) — switched from the moon/sun button in the top bar, no reload
- Six accent colours: Blue, Teal, Green, Purple, Pink, Orange
- Your choice is remembered per browser; with JavaScript off the theme still follows the OS

**Coverage**
- Every MVC page: forms, tabs, data grids (Tabulator), edit dialogs, dropdowns, tooltips, popovers, the status dialog
- Dashboard widgets as cards; gauges and the CPU graph follow the accent colour and recolour when you switch modes
- Classic PHP pages (bare inputs, `opnsense_standard_table_form` tables), the date picker, tokenizer, jqTree and nv.d3 charts
- Collapsed sidebar (core's icon rail with hover fly-outs) and the phone drawer
- Sign-in page

**Safe by design**
- CSS and one small, defensive script only — no core files are modified, no templates are overridden
- All colours are CSS custom properties: one stylesheet serves light and dark; a lint step fails the build if any hard-coded colour slips in
- No fonts, images or scripts are loaded from the internet

---

## Installation

### Installer (recommended)

```sh
fetch -o /tmp/unisense-install.sh https://github.com/ox1d3x3/unisense/releases/latest/download/install.sh
sh /tmp/unisense-install.sh
```

The installer downloads the latest release, checks it is complete, and copies it to `/usr/local/opnsense/www/themes/unisense` in one step (a failed download never leaves half a theme behind). Running it again updates the theme.

| Command | What it does |
| --- | --- |
| `sh install.sh` | install or update to the latest release |
| `sh install.sh --version 1.0.0` | install a specific release |
| `sh install.sh --local DIR` | install from an extracted release or a clone of this repository |
| `sh install.sh --status` | show the installed version and whether it is active |
| `sh install.sh --uninstall` | remove the theme (switch to another theme first) |

### Manual install

1. Download `unisense.tar.gz` from the [latest release](https://github.com/ox1d3x3/unisense/releases/latest) and copy it to the firewall.
2. Extract it and run the bundled installer:
   ```sh
   tar -xzf unisense.tar.gz && sh install.sh
   ```
   or copy the `unisense` directory to `/usr/local/opnsense/www/themes/` yourself and run `touch /usr/local/opnsense/www/index.php` so browsers fetch the new files.

### As an OPNsense plugin package

`misc/theme-unisense` follows the layout of the [opnsense/plugins](https://github.com/opnsense/plugins) repository. To build `os-theme-unisense` on a FreeBSD/OPNsense build machine, copy that directory into `plugins/misc/` and run `make package` there.

---

## Using the theme

1. **System → Settings → General → Theme** → choose **unisense** → **Save**.
2. Pick light, dark or system mode and an accent colour from the **sun/moon button** in the top bar (on phones it sits next to the menu button).

Each browser keeps its own choice. To reset it, choose **System** and **Blue**, or run `localStorage.clear()` in the browser console on the firewall's GUI.

### Uninstalling

Select another theme first (for example **opnsense**) and save, then run `sh install.sh --uninstall`.

---

## FAQ

<details>
<summary><b>Does it survive OPNsense updates?</b></summary>

Yes. The theme lives in its own directory, which firmware updates do not touch. If a future OPNsense release changes its page layout, update UniSense with `sh install.sh`.
</details>

<details>
<summary><b>Which OPNsense versions are supported?</b></summary>

UniSense is tested against the GUI markup of OPNsense **25.7, 26.1 and 26.7** (see [Testing](#testing)). It only adds CSS and a small script, so it does not affect how the firewall works — if something looks off after an update, switch back to the **opnsense** theme and [open an issue](https://github.com/ox1d3x3/unisense/issues).
</details>

<details>
<summary><b>Which browsers?</b></summary>

Current Chrome, Edge, Firefox and Safari (desktop and mobile). The theme uses `color-mix()` and `:has()`, available in all of them since 2023.
</details>

<details>
<summary><b>The page still looks like the old theme after updating.</b></summary>

The installer refreshes OPNsense's cache key so browsers fetch new files. If you copied files by hand, run `touch /usr/local/opnsense/www/index.php`, or force-reload the page (Ctrl+F5 / Cmd+Shift+R).
</details>

<details>
<summary><b>Some charts use orange/blue/green.</b></summary>

Dashboard gauges and the CPU graph follow your accent colour. Multi-series charts (traffic, interface statistics, firewall rules) keep OPNsense's categorical palette so their series stay distinguishable.
</details>

<details>
<summary><b>Can I change the logo or colours?</b></summary>

Logos are in `build/images/` (`default-logo.svg`, `icon-logo.svg`, `favicon.png`). Colours are design tokens in `assets/stylesheets/config/_tokens.scss` — change them and run `npm run build`.
</details>

---

## Building from source

Requirements: Node.js 18 or newer.

```sh
npm ci
npm run build      # compile assets/stylesheets -> build/css
npm run lint       # parse check, colour-token check, url() check, theme.js syntax
npm test           # both
sh scripts/package.sh   # dist/unisense.tar.gz + install.sh + SHA256SUMS
```

```
misc/theme-unisense/                       plugin directory (opnsense/plugins layout)
└─ src/opnsense/www/themes/unisense/
   ├─ assets/stylesheets/                  SCSS sources
   │  ├─ config/_tokens.scss               light / dark / common design tokens
   │  ├─ config/_bootstrap-vars.scss       Bootstrap 3 variables mapped to tokens
   │  ├─ config/_compat.scss               darken()/rgba()… shims that work on CSS variables
   │  ├─ core/_layout.scss                 upstream OPNsense layout rules (tokenised)
   │  ├─ unisense/                         the UniSense design layer
   │  └─ main.scss, dashboard.scss, …      one entry per stylesheet OPNsense loads
   └─ build/                               what OPNsense serves (css, js/theme.js, images)
```

### Testing

`tests/harness` renders **real OPNsense core templates** (the layout, menu, forms, grids and dialogs from a checkout of [opnsense/core](https://github.com/opnsense/core)) with the theme applied, serves them with canned API responses, and drives them in Chromium:

```sh
pip install playwright jinja2 pillow && playwright install chromium
git clone --depth 1 -b stable/26.7 https://github.com/opnsense/core /tmp/core
cd tests/harness
python3 render.py   --core /tmp/core      # build the pages
python3 shoot.py    --core /tmp/core      # screenshots, light/dark, desktop/phone; fails on JS errors or missing files
python3 interact.py --core /tmp/core      # appearance menu, accents, charts, selects, dialogs, drawer, tablet widths
```

CI runs all of this against OPNsense 25.7, 26.1 and 26.7 on every push and uploads the screenshots as an artifact.

---

## Contributing

Issues and pull requests are welcome. For visual bugs, please include the OPNsense version, the page (menu path), light or dark mode, your browser, and a screenshot.

## Changelog

Release notes are on the [Releases](https://github.com/ox1d3x3/unisense/releases) page.

---

<div align="center">

Made by [ox1d3x3](https://github.com/ox1d3x3) · Licensed under [BSD 2-Clause](./LICENSE) · Sibling of [UniWRT](https://github.com/ox1d3x3/uniwrt-luci) for OpenWrt

UniSense is an independent project and is not affiliated with or endorsed by Deciso B.V., the OPNsense project or Ubiquiti Inc.

If UniSense is useful to you, a ⭐ helps others find it.

</div>
