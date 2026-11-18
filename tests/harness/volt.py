"""Minimal Volt -> Jinja2 shim, sufficient to render OPNsense core layouts and
layout partials for visual theme testing. Not a general Volt implementation."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import jinja2


def _volt_array_to_dict(src: str) -> str:
    # Volt associative array literal ['a': x, 'b': 'y'] -> Jinja dict {'a': x, 'b': 'y'}
    out, i = [], 0
    while i < len(src):
        if src[i] == '[':
            depth, j = 0, i
            while j < len(src):
                if src[j] == '[':
                    depth += 1
                elif src[j] == ']':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            inner = src[i + 1:j]
            if re.match(r"\s*'[^']*'\s*:", inner):
                out.append('{' + _volt_array_to_dict(inner) + '}')
            else:
                out.append('[' + _volt_array_to_dict(inner) + ']')
            i = j + 1
        else:
            out.append(src[i])
            i += 1
    return ''.join(out)


def preprocess(text: str) -> str:
    text = re.sub(r'\{%\s*elseif\b', '{% elif', text)
    text = re.sub(r'\{%-?\s*for\s+(\w+)\s*,\s*(\w+)\s+in\s+(.+?)\s*-?%\}',
                  lambda m: '{%% for %s, %s in _items(%s) %%}' % (m.group(1), m.group(2), m.group(3)), text)
    # ternary: {{ a ? 'x' : 'y' }}
    text = re.sub(r"\{\{\s*([^{}?]+?)\s*\?\s*('[^']*')\s*:\s*('[^']*')\s*\}\}", r"{{ \2 if (\1) else \3 }}", text)
    # the one embedded PHP statement in form_input_tr.volt
    text = re.sub(r"<\?php\s*\$this_style\s*=\s*explode\(' ',\s*\$style\s*\?\?\s*''\);\s*\?>",
                  "{% set this_style = (style or '')|string|split_ws %}", text)
    # Volt array literals inside expressions
    text = re.sub(r'\{\{(.*?)\}\}', lambda m: '{{' + _volt_array_to_dict(m.group(1)) + '}}', text, flags=re.S)
    text = re.sub(r'\{%(.*?)%\}', lambda m: '{%' + _volt_array_to_dict(m.group(1)) + '%}', text, flags=re.S)
    # Volt: "x is defined" same; "not in" same; |e same.
    return text


class Lang:
    def _(self, s):
        return s


class VoltLoader(jinja2.BaseLoader):
    def __init__(self, roots):
        self.roots = [Path(r) for r in roots]

    def get_source(self, environment, template):
        name = template if template.endswith('.volt') or template.endswith('.html') else template + '.volt'
        for root in self.roots:
            p = root / name
            if p.exists():
                src = p.read_text(encoding='utf-8')
                if name.endswith('.volt'):
                    src = preprocess(src)
                return src, str(p), lambda: True
        raise jinja2.TemplateNotFound(template)


def make_env(view_roots, theme_root, core_www):
    env = jinja2.Environment(loader=VoltLoader(view_roots), undefined=jinja2.ChainableUndefined,
                             finalize=lambda v: '' if v is False or v is None else v,
                             autoescape=False, extensions=['jinja2.ext.do', 'jinja2.ext.loopcontrols'])

    def _items(x):
        if isinstance(x, dict):
            return list(x.items())
        if isinstance(x, (list, tuple)):
            return list(enumerate(x))
        return []

    @jinja2.pass_context
    def partial(ctx, name, params=None):
        data = dict(ctx.get_all())
        if isinstance(params, dict):
            data.update(params)
        return env.get_template(name).render(**data)

    def theme_file_or_default(url, theme):
        url = url if url.startswith('/') else '/' + url
        if (Path(theme_root) / ('build' + url)).exists():
            return f'/ui/themes/{theme}/build{url}'
        if (Path(core_www) / url.lstrip('/')).exists():
            return '/ui' + url
        return url

    def file_exists(path):
        prefix = '/usr/local/opnsense/www/themes/unisense/'
        if path.startswith(prefix):
            return (Path(theme_root) / path[len(prefix):]).exists()
        return False

    class _Acl:
        def isPageAccessible(self, user, url):
            return True

    class _Session:
        def get(self, key):
            return 'root'

    env.globals.update(acl=_Acl(), session=_Session(), _items=_items, partial=partial, theme_file_or_default=theme_file_or_default,
                       file_exists=file_exists, cache_safe=lambda u: u, lang=Lang())
    env.filters['split_ws'] = lambda s: s.split(' ')
    return env


# ------------------------------------------------------------------ forms

def _parse_form_node(node):
    result = {'sections': [{'children': [], 'type': False}], 'advanced': False, 'help': False}
    for child in node:
        key = child.tag
        if key == 'tab':
            result.setdefault('tabs', [])
            tab = {'tab_id': child.get('id'), 'tab_descr': child.get('description')}
            tab.update(_parse_form_node(child))
            result['tabs'].append(tab)
        elif key == 'subtab':
            continue
        elif key == 'field':
            field = _parse_form_node(child)
            for f in ['advanced', 'help', 'hint', 'style', 'maxheight', 'width', 'allownew', 'readonly', 'type',
                      'collapse', 'static']:
                if f not in field:
                    field[f] = False
                elif field[f] and f in ('advanced', 'help', 'collapse', 'static'):
                    result[f] = True
            if field['type'] == 'header':
                field['children'] = []
                result['sections'].append(field)
            else:
                result['sections'][-1]['children'].append(field)
        elif key == 'grid_view':
            continue
        else:
            result[key] = (child.text or '').strip()
    return result


def _parse_form_node_legacy(node):
    # OPNsense <= 26.1: a flat list of fields
    result = []
    for child in node:
        if child.tag == 'field':
            result.append({c.tag: (c.text or '').strip() for c in child if c.tag != 'grid_view'})
    return result


LEGACY_FORMS = False


def get_form(xml_path):
    root = ET.parse(xml_path).getroot()
    return _parse_form_node_legacy(root) if LEGACY_FORMS else _parse_form_node(root)


def get_form_grid(xml_path, grid_id=None):
    root = ET.parse(xml_path).getroot()
    data, idx = {}, 0
    for node in root:
        if node.tag != 'field' or not (node.findtext('id') or '').strip():
            continue
        rec = {'column-id': '', 'label': '', 'visible': 'true', 'sortable': 'true', 'identifier': 'false',
               'type': 'string'}
        rec['label'] = (node.findtext('label') or '').strip()
        rec['column-id'] = node.findtext('id').strip().split('.')[-1]
        seq = '9999999'
        gv = node.find('grid_view')
        skip = False
        if gv is not None:
            for item in gv:
                val = (item.text or '').strip()
                if item.tag == 'ignore' and val != 'false':
                    skip = True
                elif item.tag == 'sequence':
                    seq = val
                else:
                    rec[item.tag] = val
        if skip:
            continue
        data['%010d.%03d' % (int(seq), idx)] = rec
        idx += 1
    data['%010d.%03d' % (0, 0)] = {'column-id': 'uuid', 'label': 'ID', 'type': 'string', 'identifier': 'true',
                                   'visible': 'false'}
    base = grid_id or Path(xml_path).stem
    return {'edit_dialog_id': 'dialog_' + base, 'fields': [data[k] for k in sorted(data)], 'table_id': base}
