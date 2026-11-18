#!/usr/bin/env node
// UniSense stylesheet lint.
//
// 1. every compiled CSS file parses;
// 2. no colour literal escapes the token system: outside the :root token
//    blocks, colour values must use var(--us-*) (literal colours would not
//    follow light/dark mode). A short, reviewed allow-list covers intentional
//    exceptions (print styles, white text on solid accent buttons, ...);
// 3. every var(--us-*) used is defined;
// 4. url() references point to files that exist (or core paths / data URIs);
// 5. theme.js parses.
//
// The shipped CSS is compressed; checks 2-4 run on an expanded compile of the
// same sources so the allow-list stays readable. The shipped files must parse
// and must not contain a byte-order mark.
import postcss from 'postcss';
import * as sass from 'sass';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const theme = join(root, 'misc/theme-unisense/src/opnsense/www/themes/unisense');
const cssDir = join(theme, 'build/css');

const COLOR_PROPS = /^(color|background|background-color|border|border-(top|right|bottom|left)(-color)?|border-color|outline(-color)?|box-shadow|text-shadow|fill|stroke|caret-color|column-rule(-color)?|text-decoration-color)$/;
const COLOR_LITERAL = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(|\b(white|black|red|green|blue|gray|grey|silver|yellow|orange|lightcoral)\b/;

// selector or value fragments that are intentionally literal
const ALLOW = [
  { file: /.*/, sel: /@media print|^\s*\*,\s*\*:before|^\s*\*,\s*\*::before/ },            // Bootstrap print reset
  { file: /main\.css/, sel: /^(mark|\.mark)(, \.mark)?$/ },                                                // Bootstrap <mark>
  { file: /main\.css/, sel: /^\.bg-primary|a\.list-group-item-(success|info|warning|danger)\.active/, val: /#fff/ },
  { file: /main\.css/, sel: /^kbd kbd$/ },
  { file: /main\.css/, sel: /^\.carousel/ },                                                   // unused Bootstrap carousel
  { file: /main\.css/, sel: /^\.(btn-success|btn-danger|btn-warning)/, val: /#ffffff|#1fa74d|#1c1e21/ }, // solid buttons
  { file: /main\.css/, sel: /^\.label(?!-opnsense)|^a\.label/, val: /#ffffff/ },
  { file: /main\.css/, sel: /^\.label-(success|warning)/, val: /#1fa74d|#d48806/ },
  { file: /main\.css/, sel: /^\.progress-bar/, val: /#ffffff|rgba\(255, 255, 255, 0\.15\)/ },
  { file: /main\.css/, sel: /^\.modal-backdrop/, val: /#000/ },
  { file: /main\.css/, sel: /^\.btn-group \.dropdown-toggle|^\.btn-group\.open \.dropdown-toggle|^\.form-control:focus|^\.btn:active|^\.btn\.active|^\.open > \.dropdown-toggle|^\.navbar-form|^\.nav-tabs > li > a|^\.dropdown-menu|^\.popover|^\.modal-content|^\.list-group-item/, val: /rgba\(0, 0, 0, 0\.\d+\)/ }, // Bootstrap base shadows, overridden later
  { file: /main\.css/, sel: /tokenize > \.tokens-container > \.token\.pending-delete/, val: /#ffffff/ },
  { file: /main\.css/, sel: /\.navbar-header \.us-appearance/, val: /#fff/ },
  { file: /main\.css/, sel: /#navigation\.page-side/, val: /rgba\(255, 255, 255, 0?\.(14|16|24)\)/ },
  { file: /main\.css/, sel: /\[data-state=/, val: /./ },
  { file: /main\.css/, sel: /\.input-group-btn|\.btn-group > \.btn/, val: /rgba/ },
  { file: /bootstrap-select\.css/, sel: /btn-(danger|info|dark|primary|secondary|success)|li\.active small/, val: /rgba\(255, 255, 255, 0\.5\)/ },
  { file: /dns-overview\.css/, sel: /^\.query-/, val: /./ },                                   // categorical chips
];

let errors = 0;
const fail = (f, msg) => { errors++; console.error(`✗ ${f}: ${msg}`); };

const defined = new Set(['--us-swatch']); // set inline by theme.js on accent swatches
const used = new Map();

const scssDir = join(theme, 'assets/stylesheets');
for (const file of readdirSync(cssDir).filter(f => f.endsWith('.css'))) {
  const shipped = readFileSync(join(cssDir, file), 'utf8');
  if (shipped.includes('\uFEFF')) fail(file, 'contains a byte-order mark (would break the first rule)');
  try {
    postcss.parse(shipped, { from: file });
  } catch (e) {
    fail(file, `parse error: ${e.message}`);
    continue;
  }
  const scss = join(scssDir, file.replace(/\.css$/, '.scss'));
  if (!existsSync(scss)) { fail(file, 'no matching source in assets/stylesheets'); continue; }
  const css = sass.compile(scss, {
    style: 'expanded',
    loadPaths: [scssDir],
    silenceDeprecations: ['import', 'global-builtin', 'slash-div', 'color-functions', 'if-function'],
    logger: { warn: () => {} },
  }).css;
  const ast = postcss.parse(css, { from: file });
  ast.walkDecls(decl => {
    const rule = decl.parent;
    const sel = rule && rule.selector ? rule.selector : '';
    const inPrint = (() => { let p = decl.parent; while (p) { if (p.type === 'atrule' && /print/.test(p.params)) return true; p = p.parent; } return false; })();

    if (decl.prop.startsWith('--')) {
      defined.add(decl.prop);
      return; // token definitions may use literals
    }
    for (const m of decl.value.matchAll(/var\((--us-[\w-]+)/g)) {
      if (!used.has(m[1])) used.set(m[1], `${file} ${sel}`);
    }
    for (const m of decl.value.matchAll(/url\((['"]?)([^'")]+)\1\)/g)) {
      const u = m[2];
      // legacy pages and the sign-in page send "default-src 'self'": data: images are blocked there
      if (u.startsWith('data:')) { fail(file, `data: URL in ${sel} (blocked by OPNsense's Content-Security-Policy on legacy pages; ship a file)`); continue; }
      if (u.startsWith('/ui/themes/opnsense/') || u.startsWith('/ui/assets/') || u.startsWith('/ui/css/')) continue;
      const local = u.startsWith('/ui/themes/unisense/') ? join(theme, u.replace('/ui/themes/unisense/', '')) : join(cssDir, u);
      if (!existsSync(local.split(/[?#]/)[0])) fail(file, `missing url(${u}) in ${sel}`);
    }
    if (inPrint || !COLOR_PROPS.test(decl.prop) || !COLOR_LITERAL.test(decl.value)) return;
    // allow literals that only appear inside color-mix() alongside a token
    const stripped = decl.value.replace(/color-mix\([^()]*(\([^()]*\)[^()]*)*\)/g, 'TOKEN');
    if (!COLOR_LITERAL.test(stripped)) return;
    // translucent black/white shadows and highlights read the same in both modes
    if (/shadow$/.test(decl.prop) && !/#[0-9a-fA-F]{3,8}\b|\b(white|black)\b/.test(stripped)
        && [...stripped.matchAll(/rgba\(([^)]*)\)/g)].every(m => /^\s*(0|255),\s*\1,\s*\1,/.test(m[1]))) return;
    if (ALLOW.some(a => a.file.test(file) && a.sel.test(sel) && (!a.val || a.val.test(decl.value)))) return;
    fail(file, `literal colour "${decl.prop}: ${decl.value}" in "${sel.replace(/\s+/g, ' ').slice(0, 120)}"`);
  });
}

// plugin stylesheets (Network Map): must parse; tokens there carry fallbacks on purpose
const pluginCss = join(root, 'misc/theme-unisense/src/opnsense/www/css');
for (const dir of readdirSync(pluginCss, { withFileTypes: true }).filter(d => d.isDirectory())) {
  for (const file of readdirSync(join(pluginCss, dir.name)).filter(f => f.endsWith('.css'))) {
    const text = readFileSync(join(pluginCss, dir.name, file), 'utf8');
    if (/url\(\s*['"]?data:/.test(text)) fail(`${dir.name}/${file}`, 'data: URL (blocked by Content-Security-Policy on legacy pages)');
    try {
      postcss.parse(text, { from: file });
    } catch (e) {
      fail(`${dir.name}/${file}`, `parse error: ${e.message}`);
    }
  }
}

for (const [v, where] of used) {
  if (!defined.has(v)) fail('tokens', `${v} is used (${where}) but never defined`);
}

for (const js of [join(theme, 'build/js/theme.js'), join(root, 'misc/theme-unisense/src/opnsense/www/js/unisense/networkmap.js')]) {
  try {
    new vm.Script(readFileSync(js, 'utf8'), { filename: js });
  } catch (e) {
    fail(js, e.message);
  }
}

if (errors) {
  console.error(`\n${errors} problem(s)`);
  process.exit(1);
}
console.log('lint: OK');
