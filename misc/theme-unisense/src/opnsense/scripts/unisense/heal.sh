#!/bin/sh
#
# UniSense self-check, run by OPNsense after every firmware update
# (rc.syshook update) and at boot (rc.syshook start).
#
# Firmware updates do not touch files that no package owns, so UniSense
# normally survives them as it is. This makes sure of it:
#   1. every file the installer put in place is still there and not empty;
#      anything missing is restored from the copy the installer keeps in
#      /var/db/unisense/keep (the same release, so nothing changes version);
#   2. the menu and privilege caches are cleared, so the Lobby pages show up
#      under a freshly updated core;
#   3. the Shield block tables are loaded again (pf empties them at boot).
# It never changes the selected theme or your cron jobs, and does nothing
# when everything is in place.
#
# UNISENSE_PREFIX is only used by the tests.

PREFIX="${UNISENSE_PREFIX:-}"
BASE="${PREFIX}/usr/local/opnsense"
THEME_DIR="${BASE}/www/themes/unisense"
MANIFEST="${THEME_DIR}/.files"
KEEP="${PREFIX}/var/db/unisense/keep/unisense.tar.gz"
KEEP_LIST="${PREFIX}/var/db/unisense/keep/files"
CACHE_DIR="${PREFIX}/var/lib/php/tmp"
SERVICE="${UNISENSE_SERVICE:-/usr/sbin/service}"
CONFIGCTL="${UNISENSE_CONFIGCTL:-/usr/local/sbin/configctl}"

log() {
	if [ -z "$PREFIX" ] && command -v logger >/dev/null 2>&1; then
		logger -t unisense "$*"
	fi
	printf 'unisense: %s\n' "$*"
}

damaged() {
	for f in VERSION build/css/main.css build/js/theme.js build/images/default-logo.svg; do
		[ -s "${THEME_DIR}/$f" ] || return 0
	done
	[ -f "$MANIFEST" ] || return 0
	while IFS= read -r f; do
		case "$f" in ''|*..*|/*) continue ;; esac
		[ -s "${BASE}/$f" ] || return 0
	done < "$MANIFEST"
	return 1
}

restore() {
	tmp="$(mktemp -d "${TMPDIR:-/tmp}/unisense-heal.XXXXXX")" || return 1
	if ! tar -xzf "$KEEP" -C "$tmp" 2>/dev/null || [ ! -s "$tmp/www/themes/unisense/build/css/main.css" ]; then
		rm -rf "$tmp"
		log "the kept copy is unreadable; reinstall with install.sh"
		return 1
	fi
	# the theme: staged and swapped in one step, like the installer does
	rm -rf "${THEME_DIR}.new"
	cp -R "$tmp/www/themes/unisense" "${THEME_DIR}.new" && \
		rm -rf "$THEME_DIR" && mv "${THEME_DIR}.new" "$THEME_DIR"
	[ -f "$KEEP_LIST" ] && cp "$KEEP_LIST" "$MANIFEST"
	# everything else, only where it is missing or empty
	(cd "$tmp" && find . -type f ! -path './www/themes/*' ! -path '*/__pycache__/*' | sed 's:^\./::') | while IFS= read -r f; do
		if [ ! -s "${BASE}/$f" ]; then
			mkdir -p "$(dirname "${BASE}/$f")"
			cp "$tmp/$f" "${BASE}/$f"
			case "$f" in
			scripts/*.sh|scripts/*.py|scripts/*.php) chmod 755 "${BASE}/$f" ;;
			*) chmod 644 "${BASE}/$f" ;;
			esac
		fi
	done
	rm -rf "$tmp"
	if [ -x "$SERVICE" ]; then
		"$SERVICE" configd restart >/dev/null 2>&1
	fi
	return 0
}

if [ ! -d "$THEME_DIR" ] && [ ! -f "$KEEP" ]; then
	# not installed (or removed): nothing to look after
	exit 0
fi

if damaged; then
	if [ -f "$KEEP" ] && restore; then
		log "restored missing theme files ($(cat "${THEME_DIR}/VERSION" 2>/dev/null))"
	else
		log "some theme files are missing; run the installer again to repair"
	fi
fi

# new core, new menu: let OPNsense rebuild its menu and privilege caches
rm -f "${CACHE_DIR}/opnsense_menu_cache.xml" "${CACHE_DIR}/opnsense_acl_cache.json"

# Shield: pf's tables start empty after a reboot; fill them again (in the
# background, configd may still be starting)
if [ -f "${BASE}/scripts/unisense/shield.py" ] && [ -x "$CONFIGCTL" ]; then
	"$CONFIGCTL" -dq unisense shield apply >/dev/null 2>&1 || true
fi
exit 0
