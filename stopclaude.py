#!/usr/bin/env python3
"""Disable smart routing: revert Claude Code BASE_URL + stop proxy.

Restores the original BASE_URL saved by smartclaude.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = Path.home() / ".claude/settings.json"
STATE = HERE / ".smart_routing_state.json"
PIDFILE = HERE / ".smart_proxy.pid"
PORT = int(os.environ.get("SMART_PROXY_PORT", "8787"))


def is_proxy_process(pid: int) -> bool:
    """Avoid terminating an unrelated process after PID reuse."""
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="], text=True
        )
        return any(name in command for name in ("smart_proxy.py", "start_smart_routing.py"))
    except (OSError, subprocess.SubprocessError):
        return False


def load_settings() -> dict:
    return json.loads(SETTINGS.read_text())


def save_settings(data: dict) -> None:
    SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def kill_proxy() -> None:
    # by pidfile
    if PIDFILE.exists():
        try:
            pid = int(PIDFILE.read_text().strip())
            if not is_proxy_process(pid):
                print(f"refusing to stop unrelated or missing pid={pid}")
                PIDFILE.unlink(missing_ok=True)
                return
            os.kill(pid, signal.SIGTERM)
            print(f"sent SIGTERM to proxy pid={pid}")
        except ProcessLookupError:
            print(f"proxy pid already gone")
        except Exception as exc:
            print(f"kill via pidfile failed: {exc}")
        PIDFILE.unlink(missing_ok=True)


def main() -> int:
    # 1) Revert settings — only if we know the original
    original = None
    if STATE.exists():
        try:
            original = json.loads(STATE.read_text()).get("original_base_url")
        except Exception:
            original = None

    data = load_settings()
    env = data.setdefault("env", {})
    current = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    on_proxy = "127.0.0.1:8787" in current or "localhost:8787" in current

    if not on_proxy:
        print(f"BASE_URL not on proxy ({current or 'unset'}) — nothing to revert")
    elif original:
        env["ANTHROPIC_BASE_URL"] = original
        save_settings(data)
        print(f"ANTHROPIC_BASE_URL restored → {original}")
    else:
        print(
            "WARNING: BASE_URL points at proxy but no state file "
            f"({STATE.name}). Not guessing an endpoint.\n"
            "  Set ANTHROPIC_BASE_URL manually in ~/.claude/settings.json\n"
            "  (state is written by smartclaude on first enable)."
        )

    # 2) Stop proxy
    kill_proxy()

    print()
    print("Smart routing OFF.")
    print("  Restart / open a new Claude Code session to take effect.")
    print("  Re-enable with: smartclaude")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
