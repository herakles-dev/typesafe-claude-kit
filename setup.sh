#!/usr/bin/env bash
# typesafe-claude-kit — first-time setup
#
# Usage: ./setup.sh [--copy] [--force] [--no-docs] [--help]
#
#   --copy      copy agents/skill into place instead of symlinking
#   --force     overwrite existing targets in ~/.claude/agents and ~/.claude/skills
#   --no-docs   skip mirroring docs.typesafe.ai into reference/
#   --help      print this message and exit
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_AGENTS_DIR="${CLAUDE_AGENTS_DIR:-$HOME/.claude/agents}"
CLAUDE_SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"

COPY=0
FORCE=0
NO_DOCS=0

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

for arg in "$@"; do
  case "$arg" in
    --copy) COPY=1 ;;
    --force) FORCE=1 ;;
    --no-docs) NO_DOCS=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "setup.sh: unknown option: $arg" >&2; usage; exit 1 ;;
  esac
done

echo "=== typesafe-claude-kit setup ==="

# --- Prerequisites --------------------------------------------------------------------------

command -v python3 >/dev/null 2>&1 || { echo "Error: python3 is required but not installed." >&2; exit 1; }

PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 10) else 0)')
if [ "$PY_OK" != "1" ]; then
  echo "Error: python3 >= 3.10 is required (found $(python3 --version))." >&2
  exit 1
fi

command -v curl >/dev/null 2>&1 || { echo "Error: curl is required but not installed." >&2; exit 1; }

# --- Dependencies ----------------------------------------------------------------------------

if [ -f "$ROOT/requirements.txt" ] && grep -qE '^[^#[:space:]]' "$ROOT/requirements.txt"; then
  if python3 -c "import requests" >/dev/null 2>&1; then
    echo "Python dependencies already satisfied (requests importable)."
  else
    echo "Installing Python dependencies..."
    pip_err="$(mktemp)"
    if ! python3 -m pip install --user -r "$ROOT/requirements.txt" 2>"$pip_err"; then
      cat "$pip_err" >&2
      echo "" >&2
      echo "Warning: could not install dependencies automatically (see above -- often a" >&2
      echo "PEP 668 'externally managed environment' on Debian/Ubuntu). Install manually," >&2
      echo "e.g. in a virtualenv:" >&2
      echo "  python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt" >&2
      echo "Continuing setup without it; tests/run_all.py will fail until it's installed." >&2
    fi
    rm -f "$pip_err"
  fi
else
  echo "No third-party Python dependencies to install."
fi

# --- Agents + skill ----------------------------------------------------------------------

link_or_copy() {
  local src="$1" dest="$2"
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    if [ "$FORCE" -eq 1 ]; then
      rm -rf "$dest"
    else
      echo "  skip (exists): $dest"
      return 0
    fi
  fi
  mkdir -p "$(dirname "$dest")"
  if [ "$COPY" -eq 1 ]; then
    cp -r "$src" "$dest"
    echo "  copied: $dest"
  else
    ln -s "$src" "$dest"
    echo "  linked: $dest -> $src"
  fi
}

echo "Installing agents into $CLAUDE_AGENTS_DIR ..."
mkdir -p "$CLAUDE_AGENTS_DIR"
shopt -s nullglob
agent_files=("$ROOT"/agents/typesafe-*.md)
shopt -u nullglob
if [ "${#agent_files[@]}" -eq 0 ]; then
  echo "  warning: no agents/typesafe-*.md found" >&2
else
  for f in "${agent_files[@]}"; do
    link_or_copy "$f" "$CLAUDE_AGENTS_DIR/$(basename "$f")"
  done
fi

echo "Installing the /typesafe skill into $CLAUDE_SKILLS_DIR ..."
link_or_copy "$ROOT/skills/typesafe" "$CLAUDE_SKILLS_DIR/typesafe"

# --- Environment -----------------------------------------------------------------------------

if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "Created .env from .env.example — set TYPESAFE_API_KEY in it before running anything live."
else
  echo ".env already exists, leaving it alone."
fi

# --- Docs mirror -----------------------------------------------------------------------------

if [ "$NO_DOCS" -eq 1 ]; then
  echo "Skipping docs mirror (--no-docs)."
else
  echo "Fetching the TypeSafe docs mirror into reference/ ..."
  "$ROOT/scripts/fetch-docs.sh" || echo "  warning: fetch-docs.sh failed; run it manually later." >&2
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Edit .env and set TYPESAFE_API_KEY (get one at https://typesafe.ai)"
echo "  2. Run the tests:  python3 tests/run_all.py --list"
echo "  3. Read AGENTS.md, then knowledge/MASTERY.md"
echo "  4. Using Claude Code? Just open this directory — CLAUDE.md has the rest of the context."
