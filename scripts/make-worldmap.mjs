#!/usr/bin/env node
// Generate the Traffic Map's world map: Natural Earth 1:50m country shapes
// (world-atlas, public domain data), simplified and projected once at build
// time, so the browser only draws ready-made SVG paths.
//
//   node scripts/make-worldmap.mjs [--check]
//
// Output: misc/theme-unisense/src/opnsense/www/js/unisense/worldmap.json
//   { w, h, countries: [{ cc, d, c: [x, y], a }] }   (h follows the land's extent)
//     cc  ISO 3166-1 alpha-2 code
//     d   SVG path data in a w x h box (Natural Earth projection)
//     c   label/arc anchor: centroid of the country's largest landmass
//     a   projected area of that landmass (to draw tiny countries as dots)
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { feature } from 'topojson-client';
import { presimplify, simplify, quantile } from 'topojson-simplify';
import { geoNaturalEarth1, geoPath } from 'd3-geo';
import countries from 'i18n-iso-countries';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'misc/theme-unisense/src/opnsense/www/js/unisense/worldmap.json');
const check = process.argv.includes('--check');

const W = 1000;

// shapes without an ISO numeric code in Natural Earth
const BY_NAME = { 'Kosovo': 'XK', 'N. Cyprus': 'CY', 'Somaliland': 'SO' };

const topo = JSON.parse(readFileSync(join(root, 'node_modules/world-atlas/countries-50m.json'), 'utf8'));
const pre = presimplify(topo);
const simple = simplify(pre, quantile(pre, 0.12));
const features = feature(simple, simple.objects.countries).features
  .filter(f => f.id !== '010');   // Antarctica: no traffic, lots of space

// fit the width, then crop the height to the land (no empty bands)
const fc = { type: 'FeatureCollection', features };
const projection = geoNaturalEarth1().fitWidth(W - 8, fc);
const [[, y0], [, y1]] = geoPath(projection).bounds(fc);
projection.translate([projection.translate()[0] + 4, projection.translate()[1] - y0 + 4]);
const H = Math.ceil(y1 - y0) + 8;
const path = geoPath(projection).digits(1);

const list = [];
for (const f of features) {
  const cc = (f.id && countries.numericToAlpha2(f.id)) || BY_NAME[f.properties.name];
  if (!cc) {
    console.warn(`skipped ${f.properties.name} (${f.id})`);
    continue;
  }
  const d = path(f);
  if (!d) continue;
  // anchor on the largest polygon (France's mainland, not its overseas parts)
  const polys = f.geometry.type === 'MultiPolygon' ? f.geometry.coordinates : [f.geometry.coordinates];
  let best = null;
  let bestArea = -1;
  for (const p of polys) {
    const g = { type: 'Polygon', coordinates: p };
    const a = path.area(g);
    if (a > bestArea) {
      bestArea = a;
      best = g;
    }
  }
  const c = path.centroid(best).map(v => Math.round(v * 10) / 10);
  const prev = list.find(x => x.cc === cc);
  if (prev) {               // N. Cyprus -> CY etc.: merge into one entry
    prev.d += d;
    continue;
  }
  list.push({ cc, d, c, a: Math.round(bestArea) });
}
list.sort((a, b) => a.cc.localeCompare(b.cc));

const json = JSON.stringify({ w: W, h: H, source: 'Natural Earth 1:50m via world-atlas 2.0.2 (public domain)', countries: list });
if (check) {
  let cur = '';
  try { cur = readFileSync(out, 'utf8'); } catch (e) { /* missing */ }
  if (cur.trim() !== json) {
    console.error('worldmap.json is out of date: run node scripts/make-worldmap.mjs');
    process.exit(1);
  }
  console.log(`worldmap.json up to date (${list.length} countries)`);
} else {
  writeFileSync(out, json + '\n');
  console.log(`wrote worldmap.json: ${list.length} countries, ${(json.length / 1024).toFixed(0)} KiB`);
}
