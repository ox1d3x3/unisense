# Third-party data and assets

UniSense itself is licensed under the BSD 2-Clause License (see `LICENSE`).
The Traffic Map uses the following third-party works.

## Shipped with UniSense

| Work | Where | Licence |
|---|---|---|
| [FlagKit](https://github.com/madebybowtie/FlagKit) flag icons, © 2016 Bowtie AB | `www/css/unisense/flags/` (metadata stripped) | MIT — `flags/LICENSE-FlagKit.txt` |
| [Natural Earth](https://www.naturalearthdata.com) 1:50m admin-0 countries, via [world-atlas](https://github.com/topojson/world-atlas) 2.0.2 | `www/js/unisense/worldmap.json` (simplified and projected by `scripts/make-worldmap.mjs`) | Public domain (world-atlas: ISC) |

Build-time tools (not shipped): topojson-client, topojson-simplify and d3-geo (ISC), i18n-iso-countries (MIT).

## Downloaded to the firewall (not part of the repository)

| Work | Used for | Licence |
|---|---|---|
| [DB-IP IP to Country Lite](https://db-ip.com/db/download/ip-to-country-lite) | the country an IP address is located in | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — attribution "IP Geolocation by DB-IP" is shown on the Traffic Map page |
| [iptoasn.com](https://iptoasn.com) `ip2asn-combined` | network (AS number / organisation) of an IP address | Public domain (PDDL) |
