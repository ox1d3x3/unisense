#!/bin/sh
# Static checks for the OPNsense side of the plugin: PHP syntax, XML well-formedness,
# model masks compile, and the Volt view only uses functions core provides.
set -eu
cd "$(dirname "$0")/.."
TREE=misc/theme-unisense/src/opnsense
fail=0
find "$TREE" -name '*.php' > /tmp/unisense-php.txt
while IFS= read -r f; do
	php -l "$f" >/dev/null || { echo "✗ php syntax: $f"; fail=1; }
done < /tmp/unisense-php.txt
find "$TREE" -name '*.xml' > /tmp/unisense-xml.txt
while IFS= read -r f; do
	python3 -c 'import sys, xml.etree.ElementTree as E; E.parse(sys.argv[1])' "$f" || { echo "✗ xml: $f"; fail=1; }
done < /tmp/unisense-xml.txt
# every <Mask> in the model must be a valid PCRE
python3 - "$TREE/mvc/app/models/OPNsense/Unisense/NetworkMap.xml" <<'PY' > /tmp/unisense-masks.txt
import sys, xml.etree.ElementTree as E
for m in E.parse(sys.argv[1]).iter('Mask'):
    print(m.text)
PY
while IFS= read -r mask; do
	# shellcheck disable=SC2016
	php -r 'exit(@preg_match($argv[1], "") === false ? 1 : 0);' "$mask" || { echo "✗ invalid mask: $mask"; fail=1; }
done < /tmp/unisense-masks.txt
# Volt: only functions/filters available in OPNsense 25.7+
if grep -nE '\{\{[^}]*\b(escape_js|url\(|static_url|assets)\b' "$TREE"/mvc/app/views/OPNsense/Unisense/*.volt; then
	echo "✗ unsupported volt helper"; fail=1
fi
# helper scripts keep their shebangs (configd and the installer run them directly)
for f in settheme.php setup_cron.php; do
	head -n 1 "$TREE/scripts/unisense/$f" | grep -qx '#!/usr/local/bin/php' || { echo "✗ $f shebang"; fail=1; }
done
head -n 1 "$TREE/scripts/unisense/trafficmap.py" | grep -qx '#!/usr/local/bin/python3' || { echo "✗ trafficmap.py shebang"; fail=1; }
python3 -m py_compile "$TREE/scripts/unisense/trafficmap.py" || { echo "✗ trafficmap.py syntax"; fail=1; }
rm -rf "$TREE/scripts/unisense/__pycache__"
# configd actions: every command must exist in the plugin, flows must stay quiet (no log line per poll)
python3 - "$TREE" <<'PY' || fail=1
import configparser, os, sys
tree = sys.argv[1]
c = configparser.ConfigParser(delimiters=(':',), interpolation=None)
c.read(os.path.join(tree, 'service/conf/actions.d/actions_unisense.conf'))
ok = True
for sec in c.sections():
    cmd = c[sec]['command'].split()[0]
    rel = cmd.replace('/usr/local/opnsense/', '')
    if not os.path.exists(os.path.join(tree, rel)):
        print('✗ configd action %s: %s is not in the plugin' % (sec, cmd)); ok = False
if 'message' in c['trafficmap.flows']:
    print('✗ trafficmap.flows must not log a message (it runs every 3 s)'); ok = False
if 'description' not in c['geoip.update']:
    print('✗ geoip.update needs a description to be selectable in cron'); ok = False
sys.exit(0 if ok else 1)
PY
[ "$fail" -eq 0 ] && echo "plugin checks: OK"
exit "$fail"
