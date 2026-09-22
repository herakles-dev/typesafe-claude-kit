#!/usr/bin/env bash
# Fetch the public TypeSafe documentation into reference/ so the agents and the
# /typesafe skill can read it locally instead of fetching page by page.
#
# reference/ and llms.txt are gitignored: this repository does not redistribute
# TypeSafe's docs. The pages are downloaded from docs.typesafe.ai onto your
# machine, and re-running the script refreshes them.
#
# Usage:
#   scripts/fetch-docs.sh                 # -> ./reference/ and ./llms.txt
#   scripts/fetch-docs.sh DEST [INDEX]    # custom destination dir / index path
set -euo pipefail

BASE="https://docs.typesafe.ai"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$ROOT/reference}"
INDEX="${2:-$(dirname "$DEST")/llms.txt}"

command -v curl >/dev/null || { echo "fetch-docs: curl is required" >&2; exit 1; }

mkdir -p "$DEST"
curl -fsSL "$BASE/llms.txt" -o "$INDEX"

mapfile -t URLS < <(grep -oE "$BASE/[^) ]+\.md" "$INDEX" | sort -u)
if [ "${#URLS[@]}" -eq 0 ]; then
  echo "fetch-docs: no page URLs found in $INDEX" >&2
  exit 1
fi
echo "fetch-docs: ${#URLS[@]} pages -> $DEST"

# Each page is served for a crawler, not a reader: a leading "Documentation
# Index" banner, and embedded MDX components (`export function ...` blocks of
# React/JavaScript that render interactive examples on the website). Neither
# helps an agent reading the file, so strip both and collapse the blank-line
# runs they leave behind. The page then starts at its title.
fetch_one() {
  local url="$1" base="$2" dest="$3"
  local rel="${url#"$base"/}"
  local out="$dest/$rel"
  mkdir -p "$(dirname "$out")"
  if ! curl -fsSL "$url" \
      | awk '
          NR==1 && /^> ## Documentation Index/ { banner=1 }
          banner && /^$/                        { banner=0; next }
          banner                                { next }
          /^export (function|const|default|class) / { blk=1; next }
          blk && /^\}[;]?[[:space:]]*$/          { blk=0; next }
          blk                                   { next }
          /^[[:space:]]*$/                      { if (printed) pend=1; next }
          { if (pend) print ""; pend=0; print; printed=1 }
        ' \
      > "$out.tmp"; then
    rm -f "$out.tmp"
    echo "fetch-docs: FAILED $url" >&2
    return 1
  fi
  mv "$out.tmp" "$out"
}
export -f fetch_one

printf '%s\n' "${URLS[@]}" | xargs -P 8 -I{} bash -c 'fetch_one "$1" "$2" "$3"' _ {} "$BASE" "$DEST"

echo "fetch-docs: done. $(find "$DEST" -name '*.md' | wc -l) files in $DEST"
