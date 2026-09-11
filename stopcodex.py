#!/usr/bin/env python3
"""Disable Codex smart routing: restore config.toml + kill proxy."""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path.home() / ".codex/config.toml"
STATE = HERE / ".codex_smart_state.json"
PIDFILE = HERE / ".codex_smart_proxy.pid"
PORT = int(os.environ.get("CODEX_SMART_PORT", "8788"))


def is_proxy_process(pid: int) -> bool:
    """Avoid terminating an unrelated process after PID reuse."""
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="], text=True
        )
        return any(name in command for name in ("codex_smart_proxy.py",))
    except (OSError, subprocess.SubprocessError):
        return False


def kill_proxy() -> None:
    if PIDFILE.exists():
        try:
            pid = int(PIDFILE.read_text().strip())
            if not is_proxy_process(pid):
                print(f"refusing to stop unrelated or missing pid={pid}")
                PIDFILE.unlink(missing_ok=True)
                return
            os.kill(pid, signal.SIGTERM)
            print(f"SIGTERM proxy pid={pid}")
        except ProcessLookupError:
            print("proxy already gone")
        except Exception as exc:
            print(f"kill failed: {exc}")
        PIDFILE.unlink(missing_ok=True)


def main() -> int:
    original_url = None
    original_provider = None
    if STATE.exists():
        try:
            st = json.loads(STATE.read_text())
            original_url = st.get("original_base_url")
            original_provider = st.get("original_provider")
        except Exception:
            pass

    if not CONFIG.exists():
        print(f"no config at {CONFIG}")
        kill_proxy()
        return 0

    text = CONFIG.read_text()
    on_smart = bool(re.search(r'^model_provider\s*=\s*"smart"', text, re.M))

    if not on_smart:
        print("model_provider is not smart — config unchanged")
    else:
        provider = original_provider or "9router"
        text = re.sub(
            r'^model_provider\s*=\s*"smart"',
            f'model_provider = "{provider}"',
            text, count=1, flags=re.M,
        )
        if original_url:
            text = re.sub(
                r'(\[model_providers\.' + re.escape(provider) + r'\][^[]*?base_url\s*=\s*")[^"]+(")',
                rf'\g<1>{original_url}\g<2>',
                text, count=1, flags=re.S,
            )
            print(f"config.toml → model_provider={provider}  base_url={original_url}")
        else:
            print(
                f"config.toml → model_provider={provider} "
                "(no state file — left existing base_url untouched)"
            )
        CONFIG.write_text(text)
        if not original_provider:
            print("WARNING: no state file; assumed original_provider=9router")

    kill_proxy()
    print()
    print("Codex smart routing OFF. Restart Codex session.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
