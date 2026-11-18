#!/bin/sh
# Build the release archive dist/unisense.tar.gz:
#   unisense/opnsense/   files for /usr/local/opnsense (theme, Network Map, helper)
#   unisense/install.sh  installer / updater / uninstaller
#   unisense/LICENSE, unisense/README.md
set -eu
cd "$(dirname "$0")/.."
TREE=misc/theme-unisense/src/opnsense
node scripts/build.mjs --check
node scripts/lint.mjs
rm -rf dist && mkdir -p dist/stage/unisense
cp -R "$TREE" dist/stage/unisense/opnsense
cp install.sh LICENSE README.md dist/stage/unisense/
( cd dist/stage && tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime="@${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct 2>/dev/null || date +%s)}" \
    -czf ../unisense.tar.gz unisense )
cp install.sh dist/install.sh
rm -rf dist/stage
( cd dist && sha256sum unisense.tar.gz install.sh > SHA256SUMS )
echo "dist/unisense.tar.gz ($(cat "$TREE/www/themes/unisense/VERSION"))"
