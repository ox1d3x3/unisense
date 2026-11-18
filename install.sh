#!/bin/sh
#
# UniSense for OPNsense — installer, updater and uninstaller
# https://github.com/ox1d3x3/unisense
#
# Easiest: run this on the firewall as root (Shell, option 8):
#
#   fetch -o - https://raw.githubusercontent.com/ox1d3x3/unisense/main/install.sh | sh
#
# It installs (or updates) the theme and the Network Map page and switches the
# GUI to UniSense. Run the script in a terminal without options for a menu.
#
# Options:
#   install            install or update, and select the theme (default)
#   --no-activate      install or update without changing the selected theme
#   --uninstall        switch back to the default theme and remove everything
#   --status           show what is installed
#   --version X.Y.Z    install a specific release instead of the latest
#   --branch NAME      install the current state of a git branch (e.g. main)
#   --local DIR        install from an extracted archive or a repository clone
#   --url URL          install from a .tar.gz archive at URL
#   --yes              do not ask questions
#
# Private repository: export GITHUB_TOKEN=<token with read access> first.

set -eu

REPO="ox1d3x3/unisense"
# UNISENSE_PREFIX is only used to test the installer outside OPNsense
PREFIX="${UNISENSE_PREFIX:-}"
BASE="${PREFIX}/usr/local/opnsense"
THEME_DIR="${BASE}/www/themes/unisense"
MANIFEST="${THEME_DIR}/.files"
INDEX="${BASE}/www/index.php"
CACHE_DIR="${PREFIX}/var/lib/php/tmp"
CONFIG="${PREFIX}/conf/config.xml"
PHP="${UNISENSE_PHP:-/usr/local/bin/php}"
SETTHEME="${BASE}/scripts/unisense/settheme.php"
MIGRATE="${BASE}/mvc/script/run_migrations.php"

ACTION=""
ACTIVATE=1
ASSUME_YES=0
VERSION="latest"
BRANCH=""
LOCAL=""
URL=""
TMP=""

if [ -t 1 ]; then
	B="$(printf '\033[1m')"; G="$(printf '\033[32m')"; Y="$(printf '\033[33m')"
	R="$(printf '\033[31m')"; D="$(printf '\033[2m')"; N="$(printf '\033[0m')"
else
	B=""; G=""; Y=""; R=""; D=""; N=""
fi

say()  { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$B" "$N" "$*"; }
ok()   { printf '%s ✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s !%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

cleanup() {
	if [ -n "$TMP" ] && [ -d "$TMP" ]; then
		rm -rf "$TMP"
	fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT TERM

usage() {
	cat <<'USAGE'
UniSense for OPNsense — installer, updater and uninstaller

  fetch -o - https://raw.githubusercontent.com/ox1d3x3/unisense/main/install.sh | sh

Usage: sh install.sh [install|--uninstall|--status] [options]

  install            install or update, and select the theme (default)
  --no-activate      install or update without changing the selected theme
  --uninstall        switch back to the default theme and remove everything
  --status           show what is installed
  --version X.Y.Z    install a specific release instead of the latest
  --branch NAME      install the current state of a git branch (e.g. main)
  --local DIR        install from an extracted archive or a repository clone
  --url URL          install from a .tar.gz archive at URL
  --yes              do not ask questions

Without options in a terminal, a menu is shown.
Private repository: export GITHUB_TOKEN=<token with read access> first.
USAGE
}

# ------------------------------------------------------------------ options
while [ $# -gt 0 ]; do
	case "$1" in
	install|--install|-i) ACTION="install"; shift ;;
	uninstall|--uninstall|remove|-u) ACTION="uninstall"; shift ;;
	status|--status|-s) ACTION="status"; shift ;;
	--no-activate) ACTIVATE=0; shift ;;
	--activate) ACTIVATE=1; shift ;;
	--version|-v) [ $# -ge 2 ] || die "--version needs a value"; VERSION="${2#v}"; shift 2 ;;
	--branch|-b) [ $# -ge 2 ] || die "--branch needs a value"; BRANCH="$2"; shift 2 ;;
	--local|-l) [ $# -ge 2 ] || die "--local needs a directory"; LOCAL="$2"; shift 2 ;;
	--url) [ $# -ge 2 ] || die "--url needs a value"; URL="$2"; shift 2 ;;
	--yes|-y|--force|-f) ASSUME_YES=1; shift ;;
	--help|-h) usage; exit 0 ;;
	*) usage; die "unknown option: $1" ;;
	esac
done

if [ -z "$PREFIX" ] && [ "$(id -u)" != "0" ]; then
	die "run this as root (OPNsense console: option 8, Shell)"
fi
[ -d "${BASE}/www/themes" ] || die "${BASE}/www/themes not found — is this an OPNsense system?"

# ----------------------------------------------------------------- helpers
installed_version() {
	if [ -f "${THEME_DIR}/VERSION" ]; then
		cat "${THEME_DIR}/VERSION"
	elif [ -d "$THEME_DIR" ]; then
		echo "unknown"
	else
		echo ""
	fi
}

current_theme() {
	if [ -x "$PHP" ] && [ -f "$SETTHEME" ]; then
		"$PHP" "$SETTHEME" 2>/dev/null && return 0
	fi
	# fallback for when the helper is not installed yet
	if [ -f "$CONFIG" ]; then
		sed -n 's:.*<theme>\([^<]*\)</theme>.*:\1:p' "$CONFIG" | head -n 1
	fi
}

set_theme() {
	if [ ! -x "$PHP" ] || [ ! -f "$SETTHEME" ]; then
		warn "cannot change the theme automatically; select it in System > Settings > General"
		return 1
	fi
	out="$("$PHP" "$SETTHEME" "$1" 2>&1)" || { warn "could not select theme $1: $out"; return 1; }
	return 0
}

refresh_gui() {
	# menu / ACL caches (new pages and privileges) and the asset cache key
	rm -f "${CACHE_DIR}/opnsense_menu_cache.xml" "${CACHE_DIR}/opnsense_acl_cache.json"
	if [ -f "$INDEX" ]; then
		touch "$INDEX"
	fi
}

download() {
	# download URL FILE
	if [ -n "${GITHUB_TOKEN:-}" ]; then
		command -v curl >/dev/null 2>&1 || die "GITHUB_TOKEN needs curl"
		curl -fsSL --retry 2 --max-time 120 -H "Authorization: Bearer ${GITHUB_TOKEN}" \
		    -H "Accept: application/octet-stream" -o "$2" "$1"
	elif command -v fetch >/dev/null 2>&1; then
		fetch -q -T 60 -o "$2" "$1"
	elif command -v curl >/dev/null 2>&1; then
		curl -fsSL --retry 2 --max-time 120 -o "$2" "$1"
	else
		die "neither fetch nor curl is available"
	fi
}

confirm() {
	# confirm QUESTION — default yes; non-interactive runs answer yes
	if [ "$ASSUME_YES" -eq 1 ] || [ ! -t 0 ]; then
		return 0
	fi
	printf '%s [Y/n] ' "$1"
	read -r answer || answer=""
	case "$answer" in
	n|N|no|NO|No) return 1 ;;
	*) return 0 ;;
	esac
}

# locate the "opnsense" tree (mirror of /usr/local/opnsense) inside DIR;
# with a second argument "shallow" only the usual layouts are checked
find_tree() {
	for c in "$1" "$1/opnsense" "$1/unisense/opnsense" "$1/misc/theme-unisense/src/opnsense"; do
		if [ -f "$c/www/themes/unisense/build/css/main.css" ]; then
			(cd "$c" && pwd)
			return 0
		fi
	done
	[ "${2:-}" != "shallow" ] || return 1
	hit="$(find "$1" -maxdepth 7 -type f -path '*/www/themes/unisense/build/css/main.css' 2>/dev/null | head -n 1)"
	if [ -n "$hit" ]; then
		(cd "$(dirname "$hit")/../../../../.." && pwd)
		return 0
	fi
	return 1
}

validate_tree() {
	for f in www/themes/unisense/build/css/main.css www/themes/unisense/build/js/theme.js \
	    www/themes/unisense/build/images/default-logo.svg www/themes/unisense/build/images/favicon.png \
	    www/themes/unisense/VERSION www/js/unisense/networkmap.js www/css/unisense/networkmap.css \
	    mvc/app/models/OPNsense/Unisense/NetworkMap.xml mvc/app/views/OPNsense/Unisense/networkmap.volt \
	    scripts/unisense/settheme.php; do
		[ -s "$1/$f" ] || die "the download is incomplete ($f is missing)"
	done
}

fetch_source() {
	TMP="$(mktemp -d "${TMPDIR:-/tmp}/unisense.XXXXXX")"
	if [ -n "$LOCAL" ]; then
		[ -d "$LOCAL" ] || die "$LOCAL is not a directory"
		SRC="$(find_tree "$LOCAL")" || die "no UniSense files found in $LOCAL"
		return 0
	fi

	# running from an extracted release: use the files next to this script
	case "$0" in
	*/install.sh|install.sh)
		here="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
		if [ -n "$here" ] && [ -z "$BRANCH$URL" ] && [ "$VERSION" = "latest" ] && SRC="$(find_tree "$here" shallow)"; then
			step "Using the files next to the installer"
			return 0
		fi
		;;
	esac

	if [ -n "$URL" ]; then
		step "Downloading $URL"
		download "$URL" "$TMP/src.tar.gz" || die "download failed: $URL"
	elif [ -n "${GITHUB_TOKEN:-}" ]; then
		ref="${BRANCH:-main}"
		[ "$VERSION" = "latest" ] || ref="v${VERSION}"
		url="https://api.github.com/repos/${REPO}/tarball/${ref}"
		step "Downloading ${REPO}@${ref} (authenticated)"
		download "$url" "$TMP/src.tar.gz" || die "download failed — check GITHUB_TOKEN and the repository name"
	elif [ -n "$BRANCH" ]; then
		url="https://codeload.github.com/${REPO}/tar.gz/refs/heads/${BRANCH}"
		step "Downloading branch ${BRANCH}"
		download "$url" "$TMP/src.tar.gz" || die "download failed: $url"
	else
		if [ "$VERSION" = "latest" ]; then
			url="https://github.com/${REPO}/releases/latest/download/unisense.tar.gz"
		else
			url="https://github.com/${REPO}/releases/download/v${VERSION}/unisense.tar.gz"
		fi
		step "Downloading UniSense ${VERSION}"
		if ! download "$url" "$TMP/src.tar.gz" 2>/dev/null; then
			[ "$VERSION" = "latest" ] || die "release v${VERSION} not found"
			warn "no published release yet — installing the main branch"
			download "https://codeload.github.com/${REPO}/tar.gz/refs/heads/main" "$TMP/src.tar.gz" ||
			    die "download failed (is the repository private? then export GITHUB_TOKEN)"
		fi
	fi
	mkdir -p "$TMP/x"
	tar -xzf "$TMP/src.tar.gz" -C "$TMP/x" || die "the downloaded archive is damaged"
	SRC="$(find_tree "$TMP/x")" || die "the downloaded archive does not contain UniSense"
}

# --------------------------------------------------------------- actions
do_status() {
	v="$(installed_version)"
	say "${B}UniSense${N}"
	if [ -n "$v" ]; then
		say "  Installed:    $v"
	else
		say "  Installed:    no"
	fi
	say "  GUI theme:    $(current_theme)"
	if [ -f "${BASE}/mvc/app/views/OPNsense/Unisense/networkmap.volt" ]; then
		say "  Network Map:  Lobby > Network Map"
	else
		say "  Network Map:  not installed"
	fi
}

do_install() {
	fetch_source
	validate_tree "$SRC"
	new="$(cat "$SRC/www/themes/unisense/VERSION")"
	old="$(installed_version)"

	step "Installing UniSense $new"

	# 1. theme: staged next to the target and swapped in one step
	rm -rf "${THEME_DIR}.new" "${THEME_DIR}.old"
	cp -R "$SRC/www/themes/unisense" "${THEME_DIR}.new"

	# 2. everything else (Network Map page, API, model, menu, helper script)
	list="${THEME_DIR}.new/.files"
	: > "$list"
	(cd "$SRC" && find . -type f ! -path './www/themes/*' | sed 's:^\./::' | sort) > "$TMP/files"
	while IFS= read -r f; do
		mkdir -p "$(dirname "${BASE}/$f")"
		cp "$SRC/$f" "${BASE}/$f.unisense-new"
		mv -f "${BASE}/$f.unisense-new" "${BASE}/$f"
		printf '%s\n' "$f" >> "$list"
	done < "$TMP/files"

	# files from an older version that are no longer shipped
	if [ -f "$MANIFEST" ]; then
		while IFS= read -r f; do
			case "$f" in ''|*..*|/*) continue ;; esac
			grep -qxF "$f" "$list" || rm -f "${BASE}/$f"
		done < "$MANIFEST"
	fi

	if [ -z "$PREFIX" ]; then
		chown -R 0:0 "${THEME_DIR}.new"
		while IFS= read -r f; do chown 0:0 "${BASE}/$f"; done < "$list"
	fi
	find "${THEME_DIR}.new" -type d -exec chmod 755 {} +
	find "${THEME_DIR}.new" -type f -exec chmod 644 {} +
	while IFS= read -r f; do chmod 644 "${BASE}/$f"; done < "$list"
	chmod 755 "$SETTHEME"

	if [ -d "$THEME_DIR" ]; then
		mv "$THEME_DIR" "${THEME_DIR}.old"
	fi
	mv "${THEME_DIR}.new" "$THEME_DIR"
	rm -rf "${THEME_DIR}.old"

	# 3. register the model version (what an OPNsense plugin package does)
	if [ -x "$PHP" ] && [ -f "$MIGRATE" ]; then
		"$PHP" "$MIGRATE" OPNsense/Unisense >/dev/null 2>&1 || warn "model migration reported a problem (harmless on first install)"
	fi
	refresh_gui

	if [ -z "$old" ]; then
		ok "UniSense $new installed"
	elif [ "$old" = "$new" ]; then
		ok "UniSense $new reinstalled"
	else
		ok "UniSense updated: $old -> $new"
	fi
	ok "Network Map added: Lobby > Network Map"

	if [ "$ACTIVATE" -eq 1 ]; then
		if [ "$(current_theme)" = "unisense" ]; then
			ok "UniSense is the active theme"
		elif set_theme unisense; then
			ok "GUI theme switched to UniSense"
		fi
	else
		say "   Select it in System > Settings > General > Theme when you are ready."
	fi
	say ""
	say "   Reload the web GUI in your browser (Ctrl+F5) to see it."
}

do_uninstall() {
	if [ ! -d "$THEME_DIR" ] && [ ! -f "$SETTHEME" ]; then
		ok "UniSense is not installed"
		return 0
	fi
	confirm "Remove UniSense and its Network Map page?" || { say "Nothing changed."; return 0; }

	if [ "$(current_theme)" = "unisense" ]; then
		if set_theme opnsense; then
			ok "GUI theme switched back to opnsense"
		else
			die "could not switch the GUI theme; select another theme first (System > Settings > General)"
		fi
	fi

	if [ -f "$MANIFEST" ]; then
		while IFS= read -r f; do
			case "$f" in ''|*..*|/*) continue ;; esac
			rm -f "${BASE}/$f"
		done < "$MANIFEST"
	fi
	# remove the directories we created, if they are now empty
	for d in mvc/app/views/OPNsense/Unisense mvc/app/models/OPNsense/Unisense/ACL \
	    mvc/app/models/OPNsense/Unisense/Menu mvc/app/models/OPNsense/Unisense \
	    mvc/app/controllers/OPNsense/Unisense/Api mvc/app/controllers/OPNsense/Unisense \
	    www/js/unisense www/css/unisense scripts/unisense; do
		rmdir "${BASE}/$d" 2>/dev/null || true
	done
	rm -rf "$THEME_DIR" "${THEME_DIR}.new" "${THEME_DIR}.old"
	refresh_gui
	ok "UniSense removed (saved device names stay in the configuration for a reinstall)"
}

menu() {
	v="$(installed_version)"
	say ""
	say "  ${B}UniSense for OPNsense${N}  ${D}${v:+installed: $v}${N}"
	say ""
	say "  1) Install or update, and use the UniSense theme"
	say "  2) Install or update only"
	say "  3) Status"
	say "  4) Uninstall"
	say "  q) Quit"
	say ""
	printf '  Choose [1]: '
	read -r choice || choice="q"
	case "${choice:-1}" in
	1) ACTIVATE=1; do_install ;;
	2) ACTIVATE=0; do_install ;;
	3) do_status ;;
	4) do_uninstall ;;
	*) say "Nothing changed." ;;
	esac
}

# ------------------------------------------------------------------- main
if [ -z "$ACTION" ]; then
	if [ -t 0 ] && [ -t 1 ] && [ -z "$LOCAL$BRANCH$URL" ] && [ "$VERSION" = "latest" ] && [ "$ASSUME_YES" -eq 0 ]; then
		menu
		exit 0
	fi
	ACTION="install"
fi

case "$ACTION" in
install) do_install ;;
uninstall) do_uninstall ;;
status) do_status ;;
esac
