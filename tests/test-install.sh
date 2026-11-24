#!/bin/sh
# Installer tests: run install.sh against a fake OPNsense root.
# A small PHP shim stands in for settheme.php / run_migrations.php.
# shellcheck disable=SC2016,SC2034  # assertions are evaluated later, on purpose
set -eu
cd "$(dirname "$0")/.."
unset GITHUB_TOKEN GH_TOKEN || true
REPO_DIR="$(pwd)"
PASS=0
FAIL=0

t() { if eval "$2"; then PASS=$((PASS + 1)); echo "  ok   $1"; else FAIL=$((FAIL + 1)); echo "  FAIL $1"; fi; }

setup() {
	W="$(mktemp -d)"
	R="$W/root"
	mkdir -p "$R/usr/local/opnsense/www/themes/opnsense/build/css" "$R/usr/local/opnsense/mvc/script" \
	    "$R/conf" "$R/var/lib/php/tmp" "$R/usr/local/opnsense/mvc/app/models/OPNsense/Core"
	echo core > "$R/usr/local/opnsense/mvc/app/models/OPNsense/Core/Core.xml"
	echo '<?php' > "$R/usr/local/opnsense/www/index.php"
	touch -t 202001010000 "$R/usr/local/opnsense/www/index.php"
	echo '<?php' > "$R/usr/local/opnsense/mvc/script/run_migrations.php"
	echo x > "$R/var/lib/php/tmp/opnsense_menu_cache.xml"
	echo x > "$R/var/lib/php/tmp/opnsense_acl_cache.json"
	printf '<opnsense>\n  <theme>opnsense</theme>\n</opnsense>\n' > "$R/conf/config.xml"
	cat > "$W/php" <<'SHIM'
#!/bin/sh
# php shim: emulate settheme.php and run_migrations.php against $UNISENSE_PREFIX/conf/config.xml
cfg="$UNISENSE_PREFIX/conf/config.xml"
case "$1" in
*settheme.php)
	if [ -z "${2:-}" ]; then sed -n 's:.*<theme>\([^<]*\)</theme>.*:\1:p' "$cfg"; exit 0; fi
	[ -d "$UNISENSE_PREFIX/usr/local/opnsense/www/themes/$2" ] || { echo "theme not found: $2" >&2; exit 1; }
	sed -i "s:<theme>[^<]*</theme>:<theme>$2</theme>:" "$cfg"; echo ok ;;
*run_migrations.php) echo "migrate $2" >> "$UNISENSE_PREFIX/migrations.log" ;;
*setup_cron.php)
	case "${2:-}" in
	install) if grep -q unisense-cron "$cfg"; then echo exists; else sed -i "s:</opnsense>:<unisense-cron/></opnsense>:" "$cfg"; echo ok; fi ;;
	remove) sed -i "s:<unisense-cron/>::" "$cfg"; echo ok ;;
	esac ;;
*setup_shield.php)
	case "${2:-}" in
	remove) if grep -q unisense-shield "$cfg"; then sed -i "s:<unisense-shield/>::" "$cfg"; echo changed; else echo none; fi ;;
	esac ;;
*) exit 1 ;;
esac
SHIM
	chmod +x "$W/php"
	printf '#!/bin/sh\necho "$(basename "$0") $*" >> "%s/calls"\n' "$R" > "$W/service"
	cp "$W/service" "$W/configctl"
	chmod +x "$W/service" "$W/configctl"
	export UNISENSE_PREFIX="$R" UNISENSE_PHP="$W/php" UNISENSE_SERVICE="$W/service" UNISENSE_CONFIGCTL="$W/configctl"
	B="$R/usr/local/opnsense"
}
theme() { sed -n 's:.*<theme>\([^<]*\)</theme>.*:\1:p' "$R/conf/config.xml"; }

echo "local install"
setup
sh install.sh --local . >/dev/null
t "theme files installed" '[ -s "$B/www/themes/unisense/build/css/main.css" ]'
t "network map view installed" '[ -s "$B/mvc/app/views/OPNsense/Unisense/networkmap.volt" ]'
t "network map api installed" '[ -s "$B/mvc/app/controllers/OPNsense/Unisense/Api/NetworkmapController.php" ]'
t "menu + acl installed" '[ -s "$B/mvc/app/models/OPNsense/Unisense/Menu/Menu.xml" ] && [ -s "$B/mvc/app/models/OPNsense/Unisense/ACL/ACL.xml" ]'
t "js + css installed" '[ -s "$B/www/js/unisense/networkmap.js" ] && [ -s "$B/www/css/unisense/networkmap.css" ]'
t "helper is executable" '[ -x "$B/scripts/unisense/settheme.php" ]'
t "manifest lists every non-theme file" '[ "$(wc -l < "$B/www/themes/unisense/.files")" -eq "$(cd misc/theme-unisense/src/opnsense && find . -type f ! -path "./www/themes/*" ! -path "*/__pycache__/*" ! -name "*.pyc" | wc -l)" ]'
t "theme activated" '[ "$(theme)" = unisense ]'
t "menu and acl caches cleared" '[ ! -e "$R/var/lib/php/tmp/opnsense_menu_cache.xml" ] && [ ! -e "$R/var/lib/php/tmp/opnsense_acl_cache.json" ]'
t "asset cache key refreshed" '[ "$B/www/index.php" -nt "$R/conf/config.xml" ] || [ "$(find "$B/www/index.php" -newermt 2021-01-01 | wc -l)" -eq 1 ]'
t "model migration ran" 'grep -q "migrate OPNsense/Unisense" "$R/migrations.log"'
t "traffic map installed (page, api, backend, flags)" '[ -s "$B/mvc/app/views/OPNsense/Unisense/trafficmap.volt" ] && [ -s "$B/mvc/app/controllers/OPNsense/Unisense/Api/TrafficmapController.php" ] && [ -x "$B/scripts/unisense/trafficmap.py" ] && [ -s "$B/www/css/unisense/flags/AU.svg" ] && [ -s "$B/www/js/unisense/worldmap.json" ]'
t "configd actions installed and configd restarted" '[ -s "$B/service/conf/actions.d/actions_unisense.conf" ] && grep -q "service configd restart" "$R/calls"'
t "daily update scheduled, crontab reloaded" 'grep -q unisense-cron "$R/conf/config.xml" && grep -q "configctl cron restart" "$R/calls"'
t "first database download started" 'grep -q "configctl -dq unisense geoip update" "$R/calls"'
t "core files untouched" '[ "$(cat "$B/mvc/app/models/OPNsense/Core/Core.xml")" = core ] && [ -d "$B/www/themes/opnsense" ]'
t "no staging leftovers" '[ ! -e "$B/www/themes/unisense.new" ] && [ ! -e "$B/www/themes/unisense.old" ] && [ -z "$(find "$B" -name "*.unisense-new")" ]'
t "status reports version" 'sh install.sh --status | grep -q "Installed:    $(cat misc/theme-unisense/src/opnsense/www/themes/unisense/VERSION)"'

echo "update"
echo "www/js/unisense/obsolete.js" >> "$B/www/themes/unisense/.files"; echo x > "$B/www/js/unisense/obsolete.js"
sh install.sh --local . >/dev/null
t "reinstall keeps everything" '[ -s "$B/www/js/unisense/networkmap.js" ] && [ "$(theme)" = unisense ]'
t "files no longer shipped are removed" '[ ! -e "$B/www/js/unisense/obsolete.js" ]'

echo "no-activate"
sed -i 's:<theme>unisense</theme>:<theme>opnsense</theme>:' "$R/conf/config.xml"
sh install.sh --local . --no-activate >/dev/null
t "--no-activate leaves the theme alone" '[ "$(theme)" = opnsense ]'

echo "survives firmware updates (self-repair)"
H="$B/scripts/unisense/heal.sh"
t "repair copy kept" '[ -s "$R/var/db/unisense/keep/unisense.tar.gz" ] && [ -s "$R/var/db/unisense/keep/files" ]'
t "runs after firmware updates and at boot" '[ -x "$R/usr/local/etc/rc.syshook.d/update/50-unisense" ] && [ -x "$R/usr/local/etc/rc.syshook.d/start/50-unisense" ] && grep -q heal.sh "$R/usr/local/etc/rc.syshook.d/update/50-unisense"'
t "nothing to do when intact" 'out="$(sh "$H")" && [ -z "$out" ]'
rm -rf "$B/www/themes/unisense" "$B/www/js/unisense/networkmap.js"
: > "$B/www/css/unisense/trafficmap.css"
touch "$R/var/lib/php/tmp/opnsense_menu_cache.xml"
t "repairs a removed theme and missing files" 'sh "$H" | grep -q "restored" && [ -s "$B/www/themes/unisense/build/css/main.css" ] && [ -s "$B/www/js/unisense/networkmap.js" ] && [ -s "$B/www/css/unisense/trafficmap.css" ] && [ -s "$B/www/themes/unisense/.files" ]'
t "repair keeps the selected theme" '[ "$(theme)" = opnsense ]'
t "menu cache cleared for the new core" '[ ! -e "$R/var/lib/php/tmp/opnsense_menu_cache.xml" ]'
t "quiet again once repaired" 'out="$(sh "$H")" && [ -z "$out" ]'
t "Shield tables reloaded at boot (pf starts them empty)" 'grep -q "configctl -dq unisense shield apply" "$R/calls"'
t "status shows self-repair" 'sh install.sh --status | grep -q "Self-repair"'

echo "uninstall"
cp "$H" "$W/heal-copy.sh"
sh install.sh --local . >/dev/null
mkdir -p "$R/var/db/unisense" "$B/scripts/unisense/__pycache__" && echo "{}" > "$R/var/db/unisense/meta.json"
echo x > "$B/scripts/unisense/__pycache__/trafficmap.cpython-311.pyc"
sed -i "s:</opnsense>:<unisense-shield/></opnsense>:" "$R/conf/config.xml"
: > "$R/calls"
err="$(sh install.sh --uninstall --yes 2>&1 >/dev/null)"
t "uninstall without warnings" '[ -z "$err" ]'
t "Shield tables emptied, rules removed, firewall reloaded" 'grep -q "configctl unisense shield clear" "$R/calls" && ! grep -q unisense-shield "$R/conf/config.xml" && grep -q "configctl filter reload" "$R/calls"'
t "theme switched back to opnsense" '[ "$(theme)" = opnsense ]'
t "theme removed" '[ ! -e "$B/www/themes/unisense" ]'
t "plugin files removed" '[ -z "$(find "$B" -path "*nisense*")" ]'
t "core files still there" '[ -d "$B/www/themes/opnsense" ] && [ -s "$B/mvc/app/models/OPNsense/Core/Core.xml" ] && [ -f "$B/www/index.php" ]'
t "daily update job removed" '! grep -q unisense-cron "$R/conf/config.xml"'
t "IP databases removed" '[ ! -e "$R/var/db/unisense" ]'
t "firmware-update hooks removed" '[ ! -e "$R/usr/local/etc/rc.syshook.d/update/50-unisense" ] && [ ! -e "$R/usr/local/etc/rc.syshook.d/start/50-unisense" ]'
t "self-check is a no-op once removed" 'sh "$W/heal-copy.sh"' 
t "uninstall twice is harmless" 'sh install.sh --uninstall --yes >/dev/null'
rm -rf "$W"

echo "release archive"
setup
sh scripts/package.sh >/dev/null
mkdir -p "$W/rel" && tar -xzf dist/unisense.tar.gz -C "$W/rel"
t "archive layout" '[ -s "$W/rel/unisense/install.sh" ] && [ -s "$W/rel/unisense/opnsense/www/themes/unisense/build/css/main.css" ]'
(cd "$W/rel/unisense" && sh install.sh --yes >/dev/null)
t "installer uses the files next to it" '[ -s "$B/www/js/unisense/networkmap.js" ] && [ "$(theme)" = unisense ]'

echo "GitHub downloads (mock server with GitHub's URL layout)"
sh install.sh --uninstall --yes >/dev/null
WEB="$W/web"
mkdir -p "$WEB/codeload/refs/heads" "$WEB/releases" "$W/gh/unisense-main" "$W/gh/ox1d3x3-unisense-0123abc"
# GitHub source tarballs: the whole repository inside "<repo>-<ref>/"
git ls-files -co --exclude-standard | while IFS= read -r f; do
	[ -f "$f" ] || continue
	for d in unisense-main ox1d3x3-unisense-0123abc; do
		mkdir -p "$W/gh/$d/$(dirname "$f")" && cp -p "$f" "$W/gh/$d/$f"
	done
done
tar -czf "$WEB/codeload/refs/heads/main" -C "$W/gh" unisense-main
tar -czf "$WEB/api-tarball.tar.gz" -C "$W/gh" ox1d3x3-unisense-0123abc
PORT=$(( 20000 + $$ % 20000 ))
( cd "$WEB" && exec python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 ) & SRV=$!
trap 'kill "$SRV" 2>/dev/null || true' EXIT
sleep 1
export UNISENSE_RELEASES="http://127.0.0.1:$PORT/releases" UNISENSE_CODELOAD="http://127.0.0.1:$PORT/codeload"

out="$(sh < install.sh 2>&1)" || true
t "fetch ... | sh with no release falls back to main" 'printf "%s" "$out" | grep -q "installing the main branch" && [ -s "$B/www/js/unisense/networkmap.js" ] && [ "$(theme)" = unisense ]'
sh install.sh --uninstall --yes >/dev/null
sh install.sh --branch main --yes >/dev/null
t "--branch main (GitHub tarball layout)" '[ -s "$B/mvc/app/views/OPNsense/Unisense/networkmap.volt" ]'
sh install.sh --uninstall --yes >/dev/null
sh install.sh --url "http://127.0.0.1:$PORT/api-tarball.tar.gz" --yes >/dev/null
t "GitHub API tarball layout" '[ -s "$B/www/themes/unisense/build/css/main.css" ] && [ "$(theme)" = unisense ]'

V="$(cat misc/theme-unisense/src/opnsense/www/themes/unisense/VERSION)"
mkdir -p "$WEB/releases/latest/download" "$WEB/releases/download/v$V"
cp dist/unisense.tar.gz "$WEB/releases/latest/download/"
cp dist/unisense.tar.gz "$WEB/releases/download/v$V/"
sh install.sh --uninstall --yes >/dev/null
out="$(sh < install.sh 2>&1)" || true
t "fetch ... | sh uses the published release" '! printf "%s" "$out" | grep -q "main branch" && [ -s "$B/www/js/unisense/networkmap.js" ] && [ "$(theme)" = unisense ]'
sh install.sh --version "$V" --yes >/dev/null
t "--version installs that release" '[ "$(cat "$B/www/themes/unisense/VERSION")" = "$V" ]'
t "--version of a missing release fails cleanly" '! sh install.sh --version 9.9.9 --yes >/dev/null 2>&1 && [ -s "$B/www/themes/unisense/build/css/main.css" ]'

if command -v curl >/dev/null 2>&1; then
	mkdir -p "$WEB/api/tarball" "$WEB/api/releases"
	cp "$WEB/api-tarball.tar.gz" "$WEB/api/tarball/main"
	cp "$WEB/api-tarball.tar.gz" "$WEB/api/tarball/v$V"
	printf '{\n  "url": "x",\n  "tag_name": "v%s",\n  "name": "UniSense"\n}\n' "$V" > "$WEB/api/releases/latest"
	sh install.sh --uninstall --yes >/dev/null
	out="$(GITHUB_TOKEN=test UNISENSE_GH_API="http://127.0.0.1:$PORT/api" sh -s -- --yes < install.sh 2>&1)" || true
	t "GITHUB_TOKEN install picks the latest release tag" 'printf "%s" "$out" | grep -q "@v$V" && [ -s "$B/www/js/unisense/networkmap.js" ]'
	rm -f "$WEB/api/releases/latest"
	out="$(GITHUB_TOKEN=test UNISENSE_GH_API="http://127.0.0.1:$PORT/api" sh -s -- --yes < install.sh 2>&1)" || true
	t "GITHUB_TOKEN install falls back to main" 'printf "%s" "$out" | grep -q "@main" && [ "$(theme)" = unisense ]'
fi

sh install.sh --uninstall --yes >/dev/null
sh -s -- --local "$REPO_DIR" < install.sh >/dev/null
t "sh -s -- --local works" '[ -s "$B/www/themes/unisense/build/css/main.css" ]'
sh install.sh --uninstall --yes >/dev/null
cp dist/unisense.tar.gz "$WEB/"
sh install.sh --url "http://127.0.0.1:$PORT/unisense.tar.gz" --yes >/dev/null
t "--url install works" '[ -s "$B/mvc/app/views/OPNsense/Unisense/networkmap.volt" ] && [ "$(theme)" = unisense ]'
t "a broken URL fails cleanly" '! sh install.sh --url "http://127.0.0.1:1/nope.tar.gz" --yes >/dev/null 2>&1 && [ -s "$B/www/themes/unisense/build/css/main.css" ]'
kill "$SRV" 2>/dev/null || true
trap - EXIT
unset UNISENSE_RELEASES UNISENSE_CODELOAD
rm -rf "$W"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
