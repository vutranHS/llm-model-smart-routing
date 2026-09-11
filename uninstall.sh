#!/usr/bin/env bash
# Smart Routing uninstaller (macOS)
# Usage: ./uninstall.sh [--prefix ~/.smart-routing] [--keep-files] [--keep-zshrc]
set -euo pipefail

PREFIX="${SMART_ROUTING_HOME:-$HOME/.smart-routing}"
KEEP_FILES=0
KEEP_ZSHRC=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --keep-files) KEEP_FILES=1; shift ;;
    --keep-zshrc) KEEP_ZSHRC=1; shift ;;
    -h|--help)
      echo "Usage: ./uninstall.sh [--prefix DIR] [--keep-files] [--keep-zshrc]"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

echo "=== Smart Routing Uninstall ==="
echo "  prefix: $PREFIX"

# 1) Stop proxies if scripts exist
if [[ -x "$PREFIX/.venv-classifier/bin/python" ]]; then
  "$PREFIX/.venv-classifier/bin/python" "$PREFIX/stopclaude.py" 2>/dev/null || true
  "$PREFIX/.venv-classifier/bin/python" "$PREFIX/stopcodex.py" 2>/dev/null || true
fi
# kill by port just in case
for port in 8787 8788; do
  pids=$(lsof -ti tcp:$port -sTCP:LISTEN 2>/dev/null || true)
  if [[ -n "${pids:-}" ]]; then
    echo "killing listener on :$port ($pids)"
    kill $pids 2>/dev/null || true
  fi
done

# 2) Restore Claude Code BASE_URL if still pointing at proxy
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
if [[ -f "$CLAUDE_SETTINGS" ]] && command -v python3 >/dev/null 2>&1; then
  SMART_ROUTING_HOME="$PREFIX" python3 - <<'PY' || true
import json, os
from pathlib import Path
p = Path.home()/".claude/settings.json"
state = Path(os.environ.get("SMART_ROUTING_HOME", Path.home()/".smart-routing"))/".smart_routing_state.json"
try:
    d = json.loads(p.read_text())
    env = d.setdefault("env", {})
    base = env.get("ANTHROPIC_BASE_URL") or ""
    if "127.0.0.1:8787" not in base and "localhost:8787" not in base:
        print("Claude BASE_URL not on proxy — leave as-is")
    else:
        original = None
        if state.exists():
            try:
                original = json.loads(state.read_text()).get("original_base_url")
            except Exception:
                original = None
        if original:
            env["ANTHROPIC_BASE_URL"] = original
            p.write_text(json.dumps(d, indent=2, ensure_ascii=False)+"\n")
            print(f"restored Claude ANTHROPIC_BASE_URL → {original}")
        else:
            print("WARNING: Claude BASE_URL on proxy but no state file — set manually in ~/.claude/settings.json")
except Exception as e:
    print(f"skip Claude restore: {e}")
PY
fi

# 3) Restore Codex config if still on smart
CODEX_CONFIG="$HOME/.codex/config.toml"
if [[ -f "$CODEX_CONFIG" ]] && command -v python3 >/dev/null 2>&1; then
  SMART_ROUTING_HOME="$PREFIX" python3 - <<'PY' || true
import json, os, re
from pathlib import Path
p = Path.home()/".codex/config.toml"
state = Path(os.environ.get("SMART_ROUTING_HOME", Path.home()/".smart-routing"))/".codex_smart_state.json"
text = p.read_text()
if not re.search(r'^model_provider\s*=\s*"smart"', text, re.M):
    print("Codex provider not smart — leave as-is")
else:
    provider = "9router"
    original = None
    if state.exists():
        try:
            st = json.loads(state.read_text())
            provider = st.get("original_provider") or provider
            original = st.get("original_base_url")
        except Exception:
            pass
    else:
        print("WARNING: no codex state file — assuming original_provider=9router, leaving base_url untouched")
    text = re.sub(r'^model_provider\s*=\s*"smart"', f'model_provider = "{provider}"', text, count=1, flags=re.M)
    if original:
        text = re.sub(
            r'(\[model_providers\.' + re.escape(provider) + r'\][^[]*?base_url\s*=\s*")[^"]+(")',
            rf'\g<1>{original}\g<2>',
            text, count=1, flags=re.S,
        )
        print(f"restored Codex model_provider={provider} base_url={original}")
    else:
        print(f"restored Codex model_provider={provider} (base_url unchanged)")
    p.write_text(text)
PY
fi

# 4) Remove zshrc block
ZSHRC="$HOME/.zshrc"
MARK_BEGIN="# >>> smart-routing >>>"
MARK_END="# <<< smart-routing <<<"
if [[ "$KEEP_ZSHRC" -eq 0 && -f "$ZSHRC" ]] && grep -q "$MARK_BEGIN" "$ZSHRC"; then
  sed -i '' "/$MARK_BEGIN/,/$MARK_END/d" "$ZSHRC"
  echo "removed aliases from ~/.zshrc"
fi

# 5) Remove files
if [[ "$KEEP_FILES" -eq 0 ]]; then
  if [[ -d "$PREFIX" ]]; then
    echo "removing $PREFIX"
    rm -rf "$PREFIX"
  fi
else
  echo "kept files at $PREFIX"
fi

echo
echo "=== Done ==="
echo "Run: source ~/.zshrc"
echo "Open new Claude Code / Codex sessions."
