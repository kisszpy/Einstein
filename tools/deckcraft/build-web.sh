#!/usr/bin/env bash
# Build the forked DeckCraft web bundle (with Simplified Chinese faces) end to end.
#
#   tools/deckcraft/build-web.sh          # fast path: reuses cargo cache
#   tools/deckcraft/build-web.sh clean    # cargo clean first
#
# Why this exists: upstream's web build ships no Han face, so every Chinese character renders as
# .notdef. See FORK.md. The script encapsulates the three things trunk alone cannot do here:
#   1. point cargo at the craft-fonts checkout (CRAFT_FONTS_DIR),
#   2. build with fat LTO (the stock profile is thin LTO / 16 units; fat LTO cuts the wasm by ~20%),
#   3. run wasm-opt ourselves, because trunk insists on downloading its own binaryen and that
#      download times out in this environment.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
WEB="$ROOT/apps/deckcraft-web"
OUT="$ROOT/dist/web"
CRAFT_FONTS="${CRAFT_FONTS_DIR:-$ROOT/../craft-fonts}"
BINARIEN="${BINARIEN:-$(ls -d "$LOCALAPPDATA/Temp/be/binaryen-version_131" 2>/dev/null || true)}"

need() { command -v "$1" >/dev/null || { echo "missing tool: $1" >&2; exit 1; }; }
need cargo
need trunk
need python

if [ ! -f "$CRAFT_FONTS/fonts/cjk-manifest.txt" ]; then
  echo "craft-fonts checkout with the fork's cjk-manifest.txt not found at: $CRAFT_FONTS" >&2
  echo "clone it and run tools/build-han-subset.py first (see FORK.md)" >&2
  exit 1
fi

if [ "${1:-}" = "clean" ]; then
  (cd "$ROOT" && cargo clean)
fi

echo "== trunk build (fat LTO) =="
(cd "$WEB" && CARGO_PROFILE_RELEASE_LTO=fat CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1 \
  CRAFT_FONTS_DIR="$(cygpath -m "$CRAFT_FONTS" 2>/dev/null || echo "$CRAFT_FONTS")" \
  trunk build --release)

WASM="$(ls "$OUT"/*_bg.wasm 2>/dev/null | head -1 || true)"
[ -n "$WASM" ] || { echo "no wasm produced" >&2; exit 1; }

if [ -n "$BINARIEN" ] && [ -x "$BINARIEN/bin/wasm-opt.exe" ]; then
  echo "== wasm-opt -Oz in place =="
  BEFORE=$(du -h "$WASM" | cut -f1)
  OPT="$BINARIEN/bin/wasm-opt.exe"
  "$OPT" -Oz --strip-debug --strip-producers "$WASM" -o "$WASM.opt" \
    && mv "$WASM.opt" "$WASM"
  echo "  $BEFORE -> $(du -h "$WASM" | cut -f1)"
else
  echo "binaryen not found (BINARIEN=/path/to/binaryen-version_131); skipping wasm-opt" >&2
fi

echo
echo "== verify the embedded faces (must list DeckCraft Han Sans twice) =="
grep -ro 'family: "DeckCraft Han Sans"' "$ROOT/target/wasm32-unknown-unknown/release/build" 2>/dev/null | sort | uniq -c | head -3

echo
echo "bundle ready: $OUT"
