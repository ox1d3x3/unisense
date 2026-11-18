#!/usr/bin/env node
// Compile every non-partial SCSS entry in assets/stylesheets to build/css.
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
for (const file of readdirSync(src).filter(f => f.endsWith('.scss') && !f.startsWith('_')).sort()) {
  const name = basename(file, '.scss');
  const result = sass.compile(join(src, file), {
    style: 'expanded',
    loadPaths: [src],
    silenceDeprecations: ['import', 'global-builtin', 'slash-div', 'color-functions', 'if-function'],
    logger: { warn: (msg, opts) => { if (!opts.deprecation) { console.warn(`[${file}] ${msg}`); } } },
  });
  const css = banner + result.css + '\n';
  const target = join(out, `${name}.css`);
  if (check) {
    if (!existsSync(target) || readFileSync(target, 'utf8') !== css) stale.push(`${name}.css`);
  } else {
    writeFileSync(target, css);
    console.log(`built ${name}.css (${(css.length / 1024).toFixed(1)} KiB)`);
  }
}
if (check && stale.length) {
  console.error(`build/css is out of date: ${stale.join(', ')} — run "npm run build"`);
  process.exit(1);
}
