#!/bin/sh
#
# UniSense — theme installer for OPNsense
# https://github.com/ox1d3x3/unisense
#
# Usage (as root on the firewall):
#   sh install.sh                    install or update to the latest release
#   sh install.sh --version 1.0.0    install a specific release
#   sh install.sh --local DIR        install from an extracted release / checkout
#   sh install.sh --uninstall        remove the theme
#   sh install.sh --status           show what is installed
#
# The theme is copied to /usr/local/opnsense/www/themes/unisense. Nothing else
# on the system is modified, except that index.php is touched so browsers
# pick up the new stylesheets (the same thing OPNsense does on updates).

set -eu

REPO="ox1d3x3/unisense"
# UNISENSE_PREFIX is only for testing the installer outside OPNsense
PREFIX="${UNISENSE_PREFIX:-}"
THEMES="${PREFIX}/usr/local/opnsense/www/themes"
DEST="${THEMES}/unisense"
INDEX="${PREFIX}/usr/local/opnsense/www/index.php"
CONFIG="${PREFIX}/conf/config.xml"

ACTION="install"
VERSION="latest"
LOCAL=""
FORCE=0

say()  { printf '%s\n' "$*"; }
info() { printf '==> %s\n' "$*"; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
	sed -n '5,13p' "$0" 2>/dev/null | sed 's/^# \{0,1\}//'
	exit "${1:-0}"
}

while [ $# -gt 0 ]; do
	case "$1" in
	--version|-v) [ $# -ge 2 ] || die "--version needs a value"; VERSION="${2#v}"; shift 2 ;;
	--local|-l)   [ $# -ge 2 ] || die "--local needs a directory"; LOCAL="$2"; shift 2 ;;
	--uninstall|-u) ACTION="uninstall"; shift ;;
	--status|-s)  ACTION="status"; shift ;;
	--force|-f)   FORCE=1; shift ;;
	--help|-h)    usage 0 ;;
	*) say "unknown option: $1"; usage 1 ;;
	esac
done

if [ -z "$PREFIX" ] && [ "$(id -u)" != "0" ]; then
	die "run this as root (e.g. from the OPNsense shell, option 8)"
fi
[ -d "$THEMES" ] || die "$THEMES not found — is this an OPNsense system?"

theme_in_use() {
	[ -f "$CONFIG" ] && grep -q '<theme>unisense</theme>' "$CONFIG"
}

refresh_cache() {
	if [ -f "$INDEX" ]; then
		touch "$INDEX"
	fi
}

installed_version() {
	if [ -f "$DEST/VERSION" ]; then
		cat "$DEST/VERSION"
	elif [ -d "$DEST" ]; then
		echo "unknown"
	else
		echo "not installed"
	fi
}

download() {
	# download URL FILE — FreeBSD fetch(1) is always present on OPNsense
	if command -v fetch >/dev/null 2>&1; then
		fetch -q -T 30 -o "$2" "$1"
	elif command -v curl >/dev/null 2>&1; then
		curl -fsSL --max-time 60 -o "$2" "$1"
	else
		die "neither fetch nor curl is available"
	fi
}

validate() {
	# validate DIR — a theme directory must contain the core files
	for f in build/css/main.css build/js/theme.js build/images/default-logo.svg \
	    build/images/icon-logo.svg build/images/favicon.png VERSION; do
		[ -s "$1/$f" ] || die "incomplete theme: $1/$f is missing"
	done
}

do_status() {
	say "UniSense: $(installed_version)"
	if theme_in_use; then
		say "Active:   yes (System > Settings > General > Theme)"
	else
		say "Active:   no"
	fi
}

do_install() {
	TMP="$(mktemp -d "${TMPDIR:-/tmp}/unisense.XXXXXX")"
	trap 'rm -rf "$TMP"' EXIT INT TERM

	if [ -z "$LOCAL" ]; then
		# running from an extracted release archive: use the files next to us
		HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
		if [ -n "$HERE" ] && [ -f "$HERE/unisense/build/css/main.css" ]; then
			LOCAL="$HERE/unisense"
		fi
	fi

	if [ -n "$LOCAL" ]; then
		if [ -f "$LOCAL/misc/theme-unisense/src/opnsense/www/themes/unisense/build/css/main.css" ]; then
			LOCAL="$LOCAL/misc/theme-unisense/src/opnsense/www/themes/unisense"
		elif [ -f "$LOCAL/unisense/build/css/main.css" ]; then
			LOCAL="$LOCAL/unisense"
		fi
		[ -d "$LOCAL" ] || die "$LOCAL is not a directory"
		SRC="$LOCAL"
		info "Installing from $SRC"
	else
		if [ "$VERSION" = "latest" ]; then
			URL="https://github.com/${REPO}/releases/latest/download/unisense.tar.gz"
		else
			URL="https://github.com/${REPO}/releases/download/v${VERSION}/unisense.tar.gz"
		fi
		info "Downloading $URL"
		download "$URL" "$TMP/unisense.tar.gz" || die "download failed"
		tar -xzf "$TMP/unisense.tar.gz" -C "$TMP" || die "archive is damaged"
		SRC="$TMP/unisense"
	fi

	validate "$SRC"
	NEW="$(cat "$SRC/VERSION")"
	OLD="$(installed_version)"

	# stage next to the target, then swap, so a failure never leaves half a theme
	rm -rf "${DEST}.new" "${DEST}.old"
	cp -R "$SRC" "${DEST}.new"
	if [ -z "$PREFIX" ]; then
		chown -R 0:0 "${DEST}.new"
	fi
	find "${DEST}.new" -type d -exec chmod 755 {} +
	find "${DEST}.new" -type f -exec chmod 644 {} +
	if [ -d "$DEST" ]; then
		mv "$DEST" "${DEST}.old"
	fi
	mv "${DEST}.new" "$DEST"
	rm -rf "${DEST}.old"
	refresh_cache

	if [ "$OLD" = "not installed" ]; then
		info "UniSense $NEW installed."
	elif [ "$OLD" = "$NEW" ]; then
		info "UniSense $NEW reinstalled."
	else
		info "UniSense updated: $OLD -> $NEW"
	fi
	if theme_in_use; then
		say "Reload the GUI in your browser to see the changes."
	else
		say "Enable it in System > Settings > General > Theme: unisense, then Save."
	fi
}

do_uninstall() {
	[ -d "$DEST" ] || { info "UniSense is not installed."; return 0; }
	if theme_in_use && [ "$FORCE" -ne 1 ]; then
		say "UniSense is the active GUI theme. Select another theme first in"
		say "System > Settings > General > Theme (e.g. opnsense) and Save, then"
		say "run this again — or use --force to remove it anyway."
		exit 1
	fi
	rm -rf "$DEST" "${DEST}.new" "${DEST}.old"
	refresh_cache
	info "UniSense removed."
}

case "$ACTION" in
install)   do_install ;;
uninstall) do_uninstall ;;
status)    do_status ;;
esac
