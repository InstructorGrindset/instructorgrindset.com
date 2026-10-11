#!/usr/bin/env bash
# Refresh the third party libraries the Career Map loads from /map/vendor/.
#
# The site has no build step and nothing loads from a CDN, so the exact
# dist files are copied from the npm registry into the repository and
# committed. Bump a version below, run this script, and commit the result.
#
#   bash scripts/vendor.sh
#
# Requires node + npm (npm pack only; nothing is installed into the repo).
set -euo pipefail

MAPLIBRE_VERSION="5.24.0"
ZOD_VERSION="3.23.8"       # 3.23.x ships a single ESM file (lib/index.mjs)
CHARTJS_VERSION="4.5.1"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$REPO_ROOT/map/vendor"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch() {
  # fetch <package> <version> -> extracts to $WORK/<package>/package
  local pkg="$1" ver="$2" dir="$WORK/$1"
  mkdir -p "$dir"
  (cd "$dir" && npm pack --silent "${pkg}@${ver}" >/dev/null)
  tar -xzf "$dir"/*.tgz -C "$dir"
  echo "$dir/package"
}

copy_license() {
  # copy_license <package dir> <dest>
  local src
  for name in LICENSE LICENSE.txt LICENSE.md license; do
    if [ -f "$1/$name" ]; then src="$1/$name"; break; fi
  done
  if [ -z "${src:-}" ]; then echo "no LICENSE in $1" >&2; exit 1; fi
  cp "$src" "$2"
}

rm -rf "$VENDOR"
mkdir -p "$VENDOR/maplibre-gl" "$VENDOR/zod" "$VENDOR/chart.js"

# MapLibre GL JS: UMD build exposes window.maplibregl
P="$(fetch maplibre-gl "$MAPLIBRE_VERSION")"
cp "$P/dist/maplibre-gl.js" "$VENDOR/maplibre-gl/"
cp "$P/dist/maplibre-gl.css" "$VENDOR/maplibre-gl/"
copy_license "$P" "$VENDOR/maplibre-gl/LICENSE.txt"

# Zod: single file ES module
P="$(fetch zod "$ZOD_VERSION")"
cp "$P/lib/index.mjs" "$VENDOR/zod/zod.mjs"
copy_license "$P" "$VENDOR/zod/LICENSE"

# Chart.js: UMD build exposes window.Chart (bundles @kurkle/color)
P="$(fetch chart.js "$CHARTJS_VERSION")"
cp "$P/dist/chart.umd.min.js" "$VENDOR/chart.js/chart.umd.min.js" 2>/dev/null \
  || cp "$P/dist/chart.umd.js" "$VENDOR/chart.js/chart.umd.min.js"
copy_license "$P" "$VENDOR/chart.js/LICENSE.md"

# Source maps are not vendored; drop the references so browsers skip the fetch.
for f in "$VENDOR/maplibre-gl/maplibre-gl.js" "$VENDOR/chart.js/chart.umd.min.js"; do
  sed -i.bak -e 's#//\# sourceMappingURL=[^[:space:]]*##' "$f" && rm -f "$f.bak"
done

cat > "$VENDOR/VERSIONS.json" <<EOF
{
  "maplibre-gl": "$MAPLIBRE_VERSION",
  "zod": "$ZOD_VERSION",
  "chart.js": "$CHARTJS_VERSION"
}
EOF

echo "Vendored into $VENDOR:"
(cd "$VENDOR" && find . -type f | sort | xargs du -h)
