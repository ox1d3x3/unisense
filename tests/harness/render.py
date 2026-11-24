#!/usr/bin/env python3
"""Render real OPNsense core Volt pages with the UniSense theme into static HTML.

Usage: render.py --core /path/to/opnsense/core --out site/
"""
import os
import argparse
import json
import re
from pathlib import Path

from volt import make_env, get_form, get_form_grid
from menu import build_menu

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1] / 'misc/theme-unisense/src/opnsense'
THEME = PLUGIN / 'www/themes/unisense'
THEME_NAME = os.environ.get('UNISENSE_HARNESS_THEME', 'unisense')  # 'opnsense' renders the stock theme (perf baseline)


def php_list(src, func):
    m = re.search(r'function %s\(\)\s*\{(.*?)\n    \}' % func, src, re.S)
    if not m:
        return []  # older cores include dashboard assets from the view itself
    return re.findall(r"^\s*(?:\$result\[\] = )?'([^']+)'", m.group(1), re.M)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--core', required=True)
    ap.add_argument('--out', default=str(HERE / 'site'))
    ap.add_argument('--all', action='store_true', help='also render every core UI page into pages/auto/ (layout audit)')
    args = ap.parse_args()
    core = Path(args.core)
    app = core / 'src/opnsense/mvc/app'
    out = Path(args.out)
    (out / 'pages').mkdir(parents=True, exist_ok=True)

    base_src = (app / 'controllers/OPNsense/Base/ControllerBase.php').read_text()
    dash_src = (app / 'controllers/OPNsense/Core/DashboardController.php').read_text()
    js_files = php_list(base_src, 'templateJSIncludes')
    css_files = php_list(base_src, 'templateCSSIncludes')
    dash_js = js_files + php_list(dash_src, 'templateJSIncludes')
    dash_css = css_files + php_list(dash_src, 'templateCSSIncludes')

    import volt
    volt.LEGACY_FORMS = "fields['sections']" not in (app / 'views/layout_partials/base_form.volt').read_text()
    env = make_env([app / 'views', PLUGIN / 'mvc/app/views', HERE / 'templates'], THEME, core / 'src/opnsense/www')
    env.globals['core_has'] = lambda u: (core / 'src/opnsense/www' / u.lstrip('/')).exists()
    ctrl = app / 'controllers/OPNsense'

    pages = {
        'dashboard': dict(view='OPNsense/Core/dashboard', url='/ui/core/dashboard', title='Dashboard',
                          js=dash_js, css=dash_css, vars={}),
        'cron': dict(view='OPNsense/Cron/index', url='/ui/cron', title='System: Settings: Cron',
                     vars={'formDialogEdit': get_form(ctrl / 'Cron/forms/dialogEdit.xml'),
                           'formGridJobs': get_form_grid(ctrl / 'Cron/forms/dialogEdit.xml', 'grid_jobs')}),
        'syslog': dict(view='OPNsense/Syslog/index', url='/ui/syslog', title='System: Settings: Logging',
                       vars={'localForm': get_form(ctrl / 'Syslog/forms/local.xml'),
                             'formDialogDestination': get_form(ctrl / 'Syslog/forms/dialogDestination.xml'),
                             'formGridDestination': get_form_grid(ctrl / 'Syslog/forms/dialogDestination.xml')}),
        'networkmap': dict(view='OPNsense/Unisense/networkmap', url='/ui/unisense/networkmap',
                           title='Lobby: Network Map', vars={}),
        'trafficmap': dict(view='OPNsense/Unisense/trafficmap', url='/ui/unisense/trafficmap',
                           title='Lobby: Traffic Map', vars={}),
        'shield': dict(view='OPNsense/Unisense/shield', url='/ui/unisense/shield',
                       title='Lobby: Shield', vars={}),
        'monit': dict(view='OPNsense/Monit/index', url='/ui/monit', title='Services: Monit: Settings',
                      vars={'formGeneralSettings': get_form(ctrl / 'Monit/forms/general.xml'),
                            'formDialogEditAlert': get_form(ctrl / 'Monit/forms/alerts.xml'),
                            'formDialogEditService': get_form(ctrl / 'Monit/forms/services.xml'),
                            'formDialogEditTest': get_form(ctrl / 'Monit/forms/tests.xml')}),
    }

    for name, p in pages.items():
        menu = build_menu(app / 'models', p['url'], [PLUGIN / 'mvc/app/models'])
        ctx = dict(
            ui_theme=THEME_NAME, langcode='en', headTitle=p['title'], title=p['title'],
            system_hostname='opnsense', system_domain='home.arpa', session_username='root',
            css_files=p.get('css', css_files), javascript_files=p.get('js', js_files),
            menuSystem=menu, menuFavorites=json.dumps([]), menuSelectedUrl=p['url'],
            menuSelectedIsFavorite=False, product_website='https://opnsense.org/', product_name='OPNsense',
            product_copyright_years='2014-2026', product_copyright_url='https://www.deciso.com/',
            product_copyright_owner='Deciso B.V.', csrf_token='test',
        )
        ctx.update(p['vars'])
        content = env.get_template(p['view']).render(**ctx)
        ctx['content'] = lambda c=content: c
        html = env.get_template('layouts/default').render(**ctx)
        (out / 'pages' / f'{name}.html').write_text(html)
        print('rendered', name)

    if args.all:
        render_all(env, app, out / 'pages' / 'auto', css_files, js_files, dash_css, dash_js)

    # hand-written legacy (PHP) page and login page templates
    for name in ['legacy', 'login']:
        menu = build_menu(app / 'models', '/system_general.php', [PLUGIN / 'mvc/app/models'])
        tpl = env.get_template(f'{name}.html')
        html = tpl.render(menuSystem=menu, ui_theme=THEME_NAME)
        (out / 'pages' / f'{name}.html').write_text(html)
        print('rendered', name)


FORM_RX = re.compile(r"""(?:\$this->view->(\w+)\s*=|['"](\w+)['"]\s*=>)\s*\$this->getForm(Grid)?\(\s*['"]([\w-]+)['"](?:\s*,\s*['"]([\w-]+)['"])?""")
PICK_RX = re.compile(r"""->pick\(\s*['"]([^'"]+)['"]""")
ACTION_RX = re.compile(r"public function (\w+)Action\s*\(")


def discover(app):
    """every UI controller action with its view and forms (like ControllerBase::getForm)"""
    ctrl = app / 'controllers/OPNsense'
    found = []
    for php in sorted(ctrl.rglob('*Controller.php')):
        rel = php.relative_to(ctrl)
        if 'Api' in rel.parts or rel.parts[0] in ('Base',):
            continue
        src = php.read_text(errors='replace')
        module_dir = php.parent
        starts = [(m.start(), m.group(1)) for m in ACTION_RX.finditer(src)]
        for i, (pos, action) in enumerate(starts):
            body = src[pos:starts[i + 1][0] if i + 1 < len(starts) else len(src)]
            pick = PICK_RX.search(body)
            if not pick:
                continue
            forms = {}
            for m in FORM_RX.finditer(body):
                var = m.group(1) or m.group(2)
                xml = module_dir / 'forms' / (m.group(4) + '.xml')
                if not xml.exists():
                    continue
                try:
                    forms[var] = get_form_grid(xml, m.group(5)) if m.group(3) else get_form(xml)
                except Exception:
                    pass
            name = (str(rel.parent).replace('/', '-') + '-' + php.stem.replace('Controller', '') + '-' + action).lower()
            url = '/ui/' + '/'.join(str(rel.parent).lower().split('/')) + ('' if action == 'index' else '/' + action)
            found.append((name, pick.group(1), url, forms))
    return found


def render_all(env, app, out, css_files, js_files, dash_css, dash_js):
    out.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    for name, view, url, forms in discover(app):
        try:
            menu = build_menu(app / 'models', url, [PLUGIN / 'mvc/app/models'])
            ctx = dict(
                ui_theme=THEME_NAME, langcode='en', headTitle=name, title=name,
                system_hostname='opnsense', system_domain='home.arpa', session_username='root',
                css_files=css_files, javascript_files=js_files,
                menuSystem=menu, menuFavorites=json.dumps([]), menuSelectedUrl=url,
                menuSelectedIsFavorite=False, product_website='https://opnsense.org/', product_name='OPNsense',
                product_copyright_years='2014-2026', product_copyright_url='https://www.deciso.com/',
                product_copyright_owner='Deciso B.V.', csrf_token='test',
            )
            ctx.update(forms)
            content = env.get_template(view).render(**ctx)
            ctx['content'] = lambda c=content: c
            html = env.get_template('layouts/default').render(**ctx)
            (out / f'{name}.html').write_text(html)
            ok += 1
        except Exception as e:
            failed += 1
            print('  skipped %s (%s: %s)' % (name, type(e).__name__, str(e)[:80]))
    print('rendered %d core pages into %s (%d skipped)' % (ok, out, failed))


if __name__ == '__main__':
    main()
