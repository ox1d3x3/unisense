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
# the helper script must keep its shebang
head -n 1 "$TREE/scripts/unisense/settheme.php" | grep -qx '#!/usr/local/bin/php' || { echo "✗ settheme.php shebang"; fail=1; }
[ "$fail" -eq 0 ] && echo "plugin checks: OK"
exit "$fail"
