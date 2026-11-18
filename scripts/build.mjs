#!/usr/bin/env node
// Compile every non-partial SCSS entry in assets/stylesheets to build/css.
// and stamp the version from VERSION into build/js/theme.js (footer credit).
// Usage: node scripts/build.mjs [--check]   (--check: fail if build/ is stale)
import * as sass from 'sass';
import { readdirSync, readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { join, dirname, basename } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const theme = join(root, 'misc/theme-unisense/src/opnsense/www/themes/unisense');
const src = join(theme, 'assets/stylesheets');
const out = join(theme, 'build/css');
const check = process.argv.includes('--check');

const banner = '/* UniSense for OPNsense — generated from assets/stylesheets, do not edit. */\n';
mkdirSync(out, { recursive: true });

let stale = [];

// version stamp in theme.js
const version = readFileSync(join(theme, 'VERSION'), 'utf8').trim();
const themeJs = join(theme, 'build/js/theme.js');
const js = readFileSync(themeJs, 'utf8');
const stamped = js.replace(/var VERSION = '[^']*';/, `var VERSION = '${version}';`);
if (!/var VERSION = '[^']*';/.test(js)) {
  console.error('build/js/theme.js has no "var VERSION = \'...\';" line');
  process.exit(1);
}
if (check) {
  if (stamped !== js) stale.push('js/theme.js (version)');
} else if (stamped !== js) {
  writeFileSync(themeJs, stamped);
  console.log(`stamped theme.js with ${version}`);
}
for (const file of readdirSync(src).filter(f => f.endsWith('.scss') && !f.startsWith('_')).sort()) {
  const name = basename(file, '.scss');
  const result = sass.compile(join(src, file), {
    style: 'compressed',   // smaller download and faster parse on every page load
    loadPaths: [src],
    silenceDeprecations: ['import', 'global-builtin', 'slash-div', 'color-functions', 'if-function'],
    logger: { warn: (msg, opts) => { if (!opts.deprecation) { console.warn(`[${file}] ${msg}`); } } },
  });
  // @charset must come first. Sass's compressed output starts with a UTF-8
  // BOM instead; behind the banner that BOM would glue onto the first
  // selector (":root") and the browser would drop all design tokens.
  const body = result.css.replace(/^\uFEFF/, '').replace(/^@charset "UTF-8";\s*/, '');
  const css = '@charset "UTF-8";\n' + banner + body + '\n';
  if (css.indexOf('\uFEFF') !== -1) {
    console.error(`[${file}] unexpected byte-order mark in output`);
    process.exit(1);
  }
  const target = join(out, `${name}.css`);
  if (check) {
    if (!existsSync(target) || readFileSync(target, 'utf8') !== css) stale.push(`${name}.css`);
  } else {
    writeFileSync(target, css);
    console.log(`built ${name}.css (${(css.length / 1024).toFixed(1)} KiB)`);
  }
}
if (check && stale.length) {
  console.error(`build output is out of date: ${stale.join(', ')} — run "npm run build"`);
  process.exit(1);
}
