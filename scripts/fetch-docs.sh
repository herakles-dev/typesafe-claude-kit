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

# Each page carries a leading "Documentation Index" banner that only makes sense
# for a crawler. Strip it (line 1 through the first blank line) so the local copy
# starts at the page title.
fetch_one() {
  local url="$1" base="$2" dest="$3"
  local rel="${url#"$base"/}"
  local out="$dest/$rel"
  mkdir -p "$(dirname "$out")"
  if ! curl -fsSL "$url" \
      | awk 'NR==1 && /^> ## Documentation Index/ {skip=1} skip && /^$/ {skip=0; next} !skip' \
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
