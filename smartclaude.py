#!/usr/bin/env python3
"""Enable smart routing: start proxy in background + point Claude Code at it.

Idempotent. Safe to run multiple times.
Original ANTHROPIC_BASE_URL is saved to .smart_routing_state.json for stopclaude.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = Path.home() / ".claude/settings.json"
STATE = HERE / ".smart_routing_state.json"
PIDFILE = HERE / ".smart_proxy.pid"
LOG = HERE / "smart_proxy.out.log"
PORT = int(os.environ.get("SMART_PROXY_PORT", "8787"))
PROXY_URL = f"http://127.0.0.1:{PORT}/v1"


def load_settings() -> dict:
    return json.loads(SETTINGS.read_text())


def save_settings(data: dict) -> None:
    SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def proxy_alive() -> int | None:
    if not PIDFILE.exists():
        return None
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


def wait_health(timeout: float = 20.0) -> bool:
    url = f"http://127.0.0.1:{PORT}/health"
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main() -> int:
    data = load_settings()
    env = data.setdefault("env", {})
    current = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")

    # 1) Save original once — never invent an endpoint
    if not STATE.exists():
        original = current  # may be empty
        STATE.write_text(json.dumps({"original_base_url": original}, indent=2) + "\n")
        if original:
            print(f"saved original BASE_URL → {original}")
        else:
            print(
                "WARNING: ANTHROPIC_BASE_URL is empty — saved empty original.\n"
                "  stopclaude will not invent a fallback; set BASE_URL manually after disable."
            )
    else:
        print(f"original BASE_URL already saved → {json.loads(STATE.read_text()).get('original_base_url')!r}")

    # 2) Start proxy if not running
    pid = proxy_alive()
    if pid:
        print(f"proxy already running pid={pid}")
    else:
        py = str(HERE / ".venv-classifier/bin/python")
        if not Path(py).exists():
            print("ERROR: .venv-classifier missing. Run: python3 -m venv .venv-classifier && "
                  "./.venv-classifier/bin/pip install onnxruntime tokenizers numpy", file=sys.stderr)
            return 1
        cmd = [py, str(HERE / "start_smart_routing.py"), "--port", str(PORT)]
        log_f = open(LOG, "a", encoding="utf-8")
        proc = subprocess.Popen(
            cmd,
            stdout=log_f,
            stderr=log_f,
            start_new_session=True,
            cwd=str(HERE),
        )
        PIDFILE.write_text(str(proc.pid))
        print(f"starting proxy pid={proc.pid} port={PORT} log={LOG}")
        if not wait_health():
            print("ERROR: proxy failed to become healthy. See log:", LOG, file=sys.stderr)
            try:
                proc.kill()
            except Exception:
                pass
            PIDFILE.unlink(missing_ok=True)
            return 1
        print("proxy healthy ✓")

    # 3) Patch settings if needed
    if current == PROXY_URL:
        print(f"settings already point at {PROXY_URL}")
    else:
        env["ANTHROPIC_BASE_URL"] = PROXY_URL
        save_settings(data)
        print(f"ANTHROPIC_BASE_URL → {PROXY_URL}")

    print()
    print("Smart routing ON.")
    print("  Restart / open a new Claude Code session to take effect.")
    print("  Disable with: stopclaude")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
