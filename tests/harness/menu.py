"""Build the OPNsense menu tree (like MenuSystem.php) from core Menu.xml files."""
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path


class Item:
    def __init__(self, name, depth):
        self.Id = name
        self.VisibleName = name
        self.CssClass = ''
        self.Url = ''
        self.order = 0
        self.visibility = 'all'
        self.depth = depth
        self.children = {}
        self.Selected = False
        self.IsExternal = 'N'
        self.urls = []

    def merge(self, node):
        for k, v in node.attrib.items():
            if k == 'VisibleName':
                self.VisibleName = v
            elif k == 'cssClass':
                self.CssClass = v
            elif k == 'url':
                self.Url = v
                self.urls.append(v)
            elif k == 'order':
                self.order = int(v)
            elif k == 'visibility':
                self.visibility = v
            elif k == 'IsExternal':
                self.IsExternal = v
        for child in node:
            it = self.children.get(child.tag)
            if it is None:
                it = self.children[child.tag] = Item(child.tag, self.depth + 1)
            it.merge(child)

    @property
    def Children(self):
        return [c for c in sorted(self.children.values(), key=lambda c: (c.order, c.VisibleName))
                if c.visibility != 'hidden']

    def select(self, url):
        hit = any(u == url for u in self.urls)
        for c in self.children.values():
            if c.select(url):
                hit = True
        self.Selected = hit
        return hit

    @property
    def LinkClass(self):
        css = ['list-group-item']
        if len(self.Children) >= 1 and self.depth < 3:
            if self.Selected:
                css.append('active-menu-title' if any(c.visibility == 'all' for c in self.children.values())
                           else 'active')
        else:
            if self.depth == 3:
                css.append('menu-level-3-item')
            if self.Selected:
                css.append('active')
        if self.Url:
            css.append('menu_ref_' + hashlib.md5(self.Url.encode()).hexdigest())
        return ' '.join(css) + ' '


def build_menu(core_models: Path, selected_url: str):
    root = Item('root', 0)
    files = sorted(core_models.glob('OPNsense/*/Menu/Menu.xml'))
    # Core first, like the real menu system
    files.sort(key=lambda p: 0 if p.parts[-3] == 'Core' else 1)
    for f in files:
        root.merge(ET.parse(f).getroot())
    root.select(selected_url)
    return root.Children
