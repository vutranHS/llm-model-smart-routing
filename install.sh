#!/usr/bin/env bash
# Smart Routing installer for Claude Code + Codex CLI (macOS)
# Usage: ./install.sh [--prefix ~/.smart-routing] [--no-zshrc]
set -euo pipefail

PREFIX="${SMART_ROUTING_HOME:-$HOME/.smart-routing}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
SKIP_ZSHRC=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --no-zshrc) SKIP_ZSHRC=1; shift ;;
    -h|--help)
      echo "Usage: ./install.sh [--prefix DIR] [--no-zshrc]"
      echo "  --prefix DIR   install location (default: ~/.smart-routing)"
      echo "  --no-zshrc     do not modify ~/.zshrc"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

echo "=== Smart Routing Install ==="
echo "  source : $SRC_DIR"
echo "  prefix : $PREFIX"

# 1) Copy files
mkdir -p "$PREFIX"
rsync -a --exclude '.venv-classifier' --exclude '*.log' --exclude '*.log.jsonl' \
  --exclude '.smart_*' --exclude '.codex_*' --exclude '__pycache__' \
  "$SRC_DIR/" "$PREFIX/"
chmod +x "$PREFIX"/*.py "$PREFIX"/install.sh "$PREFIX"/uninstall.sh 2>/dev/null || true

# 2) Python venv
PY="${PYTHON_BIN:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Install Python 3.11+ first." >&2
  exit 1
fi
echo "using $($PY -V)"
if [[ ! -x "$PREFIX/.venv-classifier/bin/python" ]]; then
  echo "creating venv..."
  "$PY" -m venv "$PREFIX/.venv-classifier"
fi
"$PREFIX/.venv-classifier/bin/pip" install -q --upgrade pip
"$PREFIX/.venv-classifier/bin/pip" install -q onnxruntime tokenizers numpy
echo "deps ok: onnxruntime, tokenizers, numpy"

# 3) Smoke test classifier
"$PREFIX/.venv-classifier/bin/python" - <<PY
import sys
sys.path.insert(0, "$PREFIX")
from classify import QueryClassifier, DEFAULT_DIR
print("classifier dir:", DEFAULT_DIR)
clf = QueryClassifier()
r = clf.classify(["Fix this Python bug"])[0]
print("smoke:", r["scene"], r["difficulty"])
PY

# 4) zshrc aliases
ZSHRC="$HOME/.zshrc"
MARK_BEGIN="# >>> smart-routing >>>"
MARK_END="# <<< smart-routing <<<"
if [[ "$SKIP_ZSHRC" -eq 1 ]]; then
  echo "skip zshrc"
else
  # remove old block if any
  if [[ -f "$ZSHRC" ]] && grep -q "$MARK_BEGIN" "$ZSHRC"; then
    # portable sed in-place (macOS needs '')
    sed -i '' "/$MARK_BEGIN/,/$MARK_END/d" "$ZSHRC"
  fi
  cat >> "$ZSHRC" <<EOF

$MARK_BEGIN
# Installed by smart-routing install.sh
SMART_ROUTING_DIR="$PREFIX"
smartclaude() { "\$SMART_ROUTING_DIR/.venv-classifier/bin/python" "\$SMART_ROUTING_DIR/smartclaude.py" "\$@"; }
stopclaude()  { "\$SMART_ROUTING_DIR/.venv-classifier/bin/python" "\$SMART_ROUTING_DIR/stopclaude.py" "\$@"; }
smartcodex()  { "\$SMART_ROUTING_DIR/.venv-classifier/bin/python" "\$SMART_ROUTING_DIR/smartcodex.py" "\$@"; }
stopcodex()   { "\$SMART_ROUTING_DIR/.venv-classifier/bin/python" "\$SMART_ROUTING_DIR/stopcodex.py" "\$@"; }
$MARK_END
EOF
  echo "aliases added to ~/.zshrc"
  echo "  smartclaude / stopclaude"
  echo "  smartcodex  / stopcodex"
fi

echo
echo "=== Done ==="
echo "Run:  source ~/.zshrc"
echo "Then: smartclaude   # or smartcodex"
echo "Docs: $PREFIX/README.md"
