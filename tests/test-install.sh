#!/bin/sh
# Installer tests: run install.sh against a fake OPNsense root.
# A small PHP shim stands in for settheme.php / run_migrations.php.
# shellcheck disable=SC2016  # assertions are evaluated later, on purpose
set -eu
cd "$(dirname "$0")/.."
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
*) exit 1 ;;
esac
SHIM
	chmod +x "$W/php"
	export UNISENSE_PREFIX="$R" UNISENSE_PHP="$W/php"
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
t "manifest lists every non-theme file" '[ "$(wc -l < "$B/www/themes/unisense/.files")" -eq "$(cd misc/theme-unisense/src/opnsense && find . -type f ! -path "./www/themes/*" | wc -l)" ]'
t "theme activated" '[ "$(theme)" = unisense ]'
t "menu and acl caches cleared" '[ ! -e "$R/var/lib/php/tmp/opnsense_menu_cache.xml" ] && [ ! -e "$R/var/lib/php/tmp/opnsense_acl_cache.json" ]'
t "asset cache key refreshed" '[ "$B/www/index.php" -nt "$R/conf/config.xml" ] || [ "$(find "$B/www/index.php" -newermt 2021-01-01 | wc -l)" -eq 1 ]'
t "model migration ran" 'grep -q "migrate OPNsense/Unisense" "$R/migrations.log"'
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

echo "uninstall"
sh install.sh --local . >/dev/null
sh install.sh --uninstall --yes >/dev/null
t "theme switched back to opnsense" '[ "$(theme)" = opnsense ]'
t "theme removed" '[ ! -e "$B/www/themes/unisense" ]'
t "plugin files removed" '[ -z "$(find "$B" -path "*nisense*")" ]'
t "core files still there" '[ -d "$B/www/themes/opnsense" ] && [ -s "$B/mvc/app/models/OPNsense/Core/Core.xml" ] && [ -f "$B/www/index.php" ]'
t "uninstall twice is harmless" 'sh install.sh --uninstall --yes >/dev/null'
rm -rf "$W"

echo "release archive"
setup
sh scripts/package.sh >/dev/null
mkdir -p "$W/rel" && tar -xzf dist/unisense.tar.gz -C "$W/rel"
t "archive layout" '[ -s "$W/rel/unisense/install.sh" ] && [ -s "$W/rel/unisense/opnsense/www/themes/unisense/build/css/main.css" ]'
(cd "$W/rel/unisense" && sh install.sh --yes >/dev/null)
t "installer uses the files next to it" '[ -s "$B/www/js/unisense/networkmap.js" ] && [ "$(theme)" = unisense ]'

echo "piped and URL install"
sh install.sh --uninstall --yes >/dev/null
sh -s -- --local "$REPO_DIR" < install.sh >/dev/null
t "fetch ... | sh style works" '[ -s "$B/www/themes/unisense/build/css/main.css" ]'
sh install.sh --uninstall --yes >/dev/null
PORT=$(( 20000 + $$ % 20000 ))
( cd dist && python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 ) & SRV=$!
sleep 1
sh install.sh --url "http://127.0.0.1:$PORT/unisense.tar.gz" --yes >/dev/null
kill "$SRV" 2>/dev/null || true
t "--url install works" '[ -s "$B/mvc/app/views/OPNsense/Unisense/networkmap.volt" ] && [ "$(theme)" = unisense ]'
t "a broken URL fails cleanly" '! sh install.sh --url "http://127.0.0.1:1/nope.tar.gz" --yes >/dev/null 2>&1 && [ -s "$B/www/themes/unisense/build/css/main.css" ]'
rm -rf "$W"

echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
