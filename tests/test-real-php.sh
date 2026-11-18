#!/bin/sh
# Run the installer's PHP parts for real — settheme.php, run_migrations.php and
# the Network Map model — against an OPNsense core checkout, with PHP 8 on Linux.
#
#   tests/test-real-php.sh CORE_DIR
#
# CORE_DIR is a checkout of opnsense/core with at least src/opnsense/mvc and
# src/etc. OPNsense code uses absolute paths, so this needs root: it links
# /usr/local/opnsense and creates /conf for the duration of the test and
# refuses to run if either already exists for real.
# shellcheck disable=SC2016,SC2034  # assertions are evaluated later, on purpose
set -eu
cd "$(dirname "$0")/.."
CORE="$(cd "${1:?usage: $0 CORE_DIR}" && pwd)"
[ -f "$CORE/src/opnsense/mvc/script/load_phalcon.php" ] || { echo "$CORE is not an OPNsense core checkout"; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "needs root (uses /usr/local/opnsense and /conf)"; exit 2; }
for p in /usr/local/opnsense /conf; do
	if [ -e "$p" ] && [ ! -L "$p" ] && [ ! -f "$p/.unisense-test" ]; then
		echo "$p exists — refusing to touch a real system"; exit 2
	fi
done
command -v php >/dev/null || { echo "php is required"; exit 2; }
php -m | grep -qi '^phalcon$' || { echo "the phalcon PHP extension is required (OPNsense's MVC uses it)"; exit 2; }

PASS=0
FAIL=0
# t NAME ASSERTION [show]: with "show", print $out when the assertion fails
t() {
	if eval "$2"; then PASS=$((PASS + 1)); echo "  ok   $1"; return 0; fi
	FAIL=$((FAIL + 1)); echo "  FAIL $1"
	[ "${3:-}" != show ] || printf '%s\n' "$out" | sed 's/^/       | /'
}

S="$(mktemp -d)"
MADE=""
cleanup() {
	rm -f /usr/local/opnsense
	rm -rf /conf "$S"
	rm -f /var/lib/php/tmp/configdmodelfield.data
	for d in $MADE; do rm -rf "$d"; done
}
trap cleanup EXIT

# a minimal /usr/local/opnsense: the real mvc tree plus an empty web root
mkdir -p "$S/opnsense/www/themes/opnsense/build/css"
cp -R "$CORE/src/opnsense/mvc" "$S/opnsense/mvc"
echo '<?php' > "$S/opnsense/www/index.php"
mkdir -p /usr/local
ln -sfn "$S/opnsense" /usr/local/opnsense
mkdir -p /conf/backup
touch /conf/.unisense-test
for d in /var/lib/php/tmp /var/lib/php/cache; do
	[ -d "$d" ] || { mkdir -p "$d"; MADE="$MADE $d"; }
done
cp "$CORE/src/etc/config.xml.sample" /conf/config.xml
grep -q '<theme>' /conf/config.xml || sed -i 's:</opnsense>:  <theme>opnsense</theme>\n</opnsense>:' /conf/config.xml
cp /conf/config.xml "$S/config.orig.xml"

# php with OPNsense's include path (see /usr/local/etc/php.ini on a firewall)
cat > "$S/php" <<EOF
#!/bin/sh
exec php -d include_path=".:/usr/local/opnsense/mvc:$CORE/src/etc/inc:/usr/local/opnsense/contrib" -d error_reporting=E_ALL -d display_errors=stderr "\$@"
EOF
chmod +x "$S/php"
PHP="$S/php"
# configd stand-ins: record what the installer asks for
printf '#!/bin/sh\necho "service $*" >> "%s/calls"\n' "$S" > "$S/service"
printf '#!/bin/sh\necho "configctl $*" >> "%s/calls"\n' "$S" > "$S/configctl"
chmod +x "$S/service" "$S/configctl"
export UNISENSE_SERVICE="$S/service" UNISENSE_CONFIGCTL="$S/configctl"
# the cron model validates commands against configd's action list; core
# caches that list here until configd restarts (no socket here: never stale)
printf '%s' '{"unisense geoip update": {"description": "UniSense: update IP-to-country databases (Traffic Map)"}, "firmware poll": {"description": "Firmware update check"}}' \
    > /var/lib/php/tmp/configdmodelfield.data
SETUPCRON=/usr/local/opnsense/scripts/unisense/setup_cron.php
SETTHEME=/usr/local/opnsense/scripts/unisense/settheme.php
theme() { sed -n 's:.*<theme>\([^<]*\)</theme>.*:\1:p' /conf/config.xml; }
# the config minus the parts a save is expected to touch
stable() { sed -e '/<theme>/d' -e '/<revision>/,/<\/revision>/d' -e '/<OPNsense>/,/<\/OPNsense>/d' "$1"; }

echo "install (real php)"
out="$(UNISENSE_PREFIX=/ UNISENSE_PHP="$PHP" sh install.sh --local . --yes 2>&1)" || true
t "installer finishes without warnings" '! printf "%s" "$out" | grep -q -E "^ ! |Fatal|Warning|Deprecated"' show
t "theme set to unisense" '[ "$(theme)" = unisense ]'
t "revision written" 'grep -q "<description>GUI theme set to unisense</description>" /conf/config.xml'
t "backup made" '[ -n "$(ls /conf/backup)" ]'
t "nothing else in the config changed" 'stable "$S/config.orig.xml" | diff -q - "$(stable /conf/config.xml > "$S/now.xml"; echo "$S/now.xml")" >/dev/null'
t "network map config section created" 'grep -q "<unisense>" /conf/config.xml'

echo "daily IP database update (real cron model)"
t "configd restarted for the new actions" 'grep -q "service configd restart" "$S/calls"'
t "cron job saved" 'grep -q "<command>unisense geoip update</command>" /conf/config.xml && grep -q "<origin>UniSense</origin>" /conf/config.xml'
t "scheduled at 01:00" '[ "$("$PHP" "$SETUPCRON" 2>&1)" = "0 1 * * * unisense geoip update" ]' show
t "crontab regenerated" 'grep -q "configctl cron restart" "$S/calls"'
t "first download started in the background" 'grep -q "configctl -dq unisense geoip update" "$S/calls"'
t "second install keeps the existing job" '[ "$("$PHP" "$SETUPCRON" install 2>&1)" = exists ] && [ "$(grep -c "<command>unisense geoip update</command>" /conf/config.xml)" = 1 ]'

echo "settheme.php"
t "prints the current theme" '[ "$("$PHP" "$SETTHEME" 2>&1)" = unisense ]'
t "setting the same theme is a no-op" '[ "$("$PHP" "$SETTHEME" unisense 2>&1)" = unchanged ]'
t "unknown theme is refused" '! "$PHP" "$SETTHEME" nope >/dev/null 2>&1 && [ "$(theme)" = unisense ]'
t "bad theme name is refused" '! "$PHP" "$SETTHEME" "../x" >/dev/null 2>&1'
sed -i '/<theme>/d' /conf/config.xml
t "missing <theme> reads as opnsense" '[ "$("$PHP" "$SETTHEME" 2>&1)" = opnsense ]'
t "missing <theme> can be set" '[ "$("$PHP" "$SETTHEME" unisense 2>&1)" = ok ] && [ "$(theme)" = unisense ]'

echo "network map model (real BaseModel)"
cat > "$S/model.php" <<'PHP'
<?php
require_once('script/load_phalcon.php');
$m = new \OPNsense\Unisense\NetworkMap();
$check = function ($mac, $name, $icon) use ($m) {
    $d = $m->devices->device->Add();
    $d->setNodes(['mac' => $mac, 'name' => $name, 'icon' => $icon]);
    $n = count($m->performValidation());
    $m->devices->device->del($d->getAttributes()['uuid']);
    return $n === 0 ? 'valid' : 'invalid';
};
echo $check('aa:bb:cc:dd:ee:01', 'Living room TV', 'tv'), "\n";
echo $check('aa:bb:cc:dd:ee:02', '<script>', 'tv'), "\n";
echo $check('aa:bb:cc:dd:ee:03', 'NAS', 'rocket'), "\n";
echo $check('not-a-mac', 'x', 'auto'), "\n";
$d = $m->devices->device->Add();
$d->setNodes(['mac' => 'aa:bb:cc:dd:ee:04', 'name' => 'Kitchen speaker', 'icon' => 'speaker']);
$m->serializeToConfig();
\OPNsense\Core\Config::getInstance()->save();
echo "saved\n";
PHP
out="$("$PHP" "$S/model.php" 2>&1)" || true
t "model loads and validates a good device" '[ "$(printf "%s\n" "$out" | sed -n 1p)" = valid ]' show
t "HTML in a name is rejected" '[ "$(printf "%s\n" "$out" | sed -n 2p)" = invalid ]'
t "unknown icon is rejected" '[ "$(printf "%s\n" "$out" | sed -n 3p)" = invalid ]'
t "bad MAC is rejected" '[ "$(printf "%s\n" "$out" | sed -n 4p)" = invalid ]'
t "device saved to config" 'grep -q "Kitchen speaker" /conf/config.xml'

echo "uninstall (real php)"
out="$(UNISENSE_PREFIX=/ UNISENSE_PHP="$PHP" sh install.sh --uninstall --yes 2>&1)" || true
t "uninstall finishes without warnings" '! printf "%s" "$out" | grep -q -E "^ ! |Fatal|Warning|Deprecated"' show
t "theme switched back to opnsense" '[ "$(theme)" = opnsense ]'
t "device labels kept in config" 'grep -q "Kitchen speaker" /conf/config.xml'
t "cron job removed" '! grep -q "unisense geoip update" /conf/config.xml'

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
