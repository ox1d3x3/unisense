#!/bin/sh
# Build the release archive dist/unisense.tar.gz:
#   unisense/      the theme directory (copied to /usr/local/opnsense/www/themes/)
#   install.sh     installer / updater / uninstaller
#   LICENSE, README.md
set -eu
cd "$(dirname "$0")/.."
THEME=misc/theme-unisense/src/opnsense/www/themes/unisense
node scripts/build.mjs --check
node scripts/lint.mjs
rm -rf dist && mkdir -p dist/stage
cp -R "$THEME" dist/stage/unisense
cp install.sh LICENSE README.md dist/stage/
# reproducible-ish archive: sorted, fixed owner, no macOS metadata
( cd dist/stage && tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime="@${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}" \
    -czf ../unisense.tar.gz unisense install.sh LICENSE README.md )
cp install.sh dist/install.sh
rm -rf dist/stage
( cd dist && sha256sum unisense.tar.gz install.sh > SHA256SUMS )
echo "dist/unisense.tar.gz ($(cat "$THEME/VERSION"))"
