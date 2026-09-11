#!/usr/bin/env python3
"""Enable Codex smart routing: start proxy + point Codex config at it.

Reads ~/.codex/config.toml, backs up original provider base_url.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = Path.home() / ".codex/config.toml"
STATE = HERE / ".codex_smart_state.json"
PIDFILE = HERE / ".codex_smart_proxy.pid"
LOG = HERE / ".codex_smart_proxy.out.log"
PORT = int(os.environ.get("CODEX_SMART_PORT", "8788"))
PROXY_URL = f"http://127.0.0.1:{PORT}/v1"


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
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def provider_setting(text: str, provider: str, key: str) -> str:
    """Read a quoted setting from a model provider section."""
    match = re.search(
        r'\[model_providers\.' + re.escape(provider) + r'\][^[]*?'
        + re.escape(key) + r'\s*=\s*"([^"]+)"', text, re.S,
    )
    return match.group(1) if match else ""


def patch_config() -> None:
    text = CONFIG.read_text()
    # Save original once — read whatever provider/base_url is active now
    if not STATE.exists():
        pm = re.search(r'^model_provider\s*=\s*"([^"]+)"', text, re.M)
        provider = pm.group(1) if pm else "9router"
        if provider == "smart":
            provider = "9router"  # already switched; keep previous convention
        original = provider_setting(text, provider, "base_url")
        env_key = provider_setting(text, provider, "env_key")
        if not env_key:
            raise ValueError(f"model provider {provider!r} has no env_key")
        STATE.write_text(json.dumps(
            {"original_base_url": original, "original_provider": provider,
             "original_env_key": env_key},
            indent=2,
        ) + "\n")
        print(f"saved original provider={provider} base_url={original or '(unset)'}")

    state = json.loads(STATE.read_text())
    env_key = state.get("original_env_key") or provider_setting(
        text, state.get("original_provider") or "", "env_key"
    )
    if not env_key:
        raise ValueError("cannot determine env_key for the original Codex provider")

    # Ensure [model_providers.smart] exists and model_provider = "smart"
    if "[model_providers.smart]" not in text:
        block = f'''
[model_providers.smart]
name = "Smart Router"
base_url = "{PROXY_URL}"
env_key = "{env_key}"
wire_api = "responses"
stream_idle_timeout_ms = 500000
'''
        text = text.rstrip() + "\n" + block

    # rewrite base_url inside [model_providers.smart]
    text = re.sub(
        r'(\[model_providers\.smart\][^[]*?base_url\s*=\s*")[^"]+(")',
        rf'\g<1>{PROXY_URL}\g<2>',
        text,
        count=1,
        flags=re.S,
    )

    text = re.sub(
        r'(\[model_providers\.smart\][^[]*?env_key\s*=\s*")[^"]+(")',
        rf'\g<1>{env_key}\g<2>', text, count=1, flags=re.S,
    )

    # switch model_provider
    if re.search(r'^model_provider\s*=', text, re.M):
        text = re.sub(r'^model_provider\s*=\s*".*"', 'model_provider = "smart"', text, count=1, flags=re.M)
    else:
        text = 'model_provider = "smart"\n' + text

    CONFIG.write_text(text)
    print(f"config.toml → model_provider=smart  base_url={PROXY_URL}")


def main() -> int:
    global PORT, PROXY_URL
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    PORT = args.port
    PROXY_URL = f"http://127.0.0.1:{PORT}/v1"

    print("=== Codex Smart Routing Plan ===")
    print(f"  proxy listen : {PROXY_URL}")
    print(f"  config       : {CONFIG}")
    print(f"  software+hard     → gpt-6-astra   $10/$50  effort=high")
    print(f"  other hard        → gpt-5.6-sol   $4/$20   effort=high")
    print(f"  medium tech       → gpt-5.6-terra $2/$12   effort=medium")
    print(f"  easy / office     → gpt-5.6-luna  $0.2/$1.2 effort=low")
    print(f"  >200000 tokens     → demote tier (avoid the 272K pricing cliff)")
    print()
    if args.dry_run:
        return 0

    # Resolve upstream BEFORE starting proxy (from state or current config)
    text = CONFIG.read_text() if CONFIG.exists() else ""
    original_url = None
    if STATE.exists():
        try:
            original_url = (json.loads(STATE.read_text()).get("original_base_url") or "").rstrip("/") or None
        except Exception:
            original_url = None
    if not original_url:
        pm = re.search(r'^model_provider\s*=\s*"([^"]+)"', text, re.M)
        provider = pm.group(1) if pm else None
        if provider and provider != "smart":
            m = re.search(
                r'\[model_providers\.' + re.escape(provider) + r'\][^[]*?base_url\s*=\s*"([^"]+)"',
                text, re.S,
            )
            if m:
                original_url = m.group(1).rstrip("/")
    if original_url and original_url.endswith("/v1"):
        original_url = original_url[: -len("/v1")]
    if not original_url:
        print("ERROR: cannot resolve upstream from ~/.codex/config.toml — set base_url first",
              file=sys.stderr)
        return 1
    print(f"upstream = {original_url}")

    pid = proxy_alive()
    if pid:
        print(f"proxy already running pid={pid}")
    else:
        py = str(HERE / ".venv-classifier/bin/python")
        cmd = [py, str(HERE / "codex_smart_proxy.py"), "--port", str(PORT), "--upstream", original_url]
        log_f = open(LOG, "a", encoding="utf-8")
        proc = subprocess.Popen(cmd, stdout=log_f, stderr=log_f, start_new_session=True, cwd=str(HERE))
        PIDFILE.write_text(str(proc.pid))
        print(f"starting proxy pid={proc.pid}")
        if not wait_health():
            print("ERROR: proxy not healthy, see", LOG, file=sys.stderr)
            try:
                proc.kill()
            except Exception:
                pass
            PIDFILE.unlink(missing_ok=True)
            return 1
        print("proxy healthy ✓")

    patch_config()
    print()
    print("Codex smart routing ON. Restart Codex session to take effect.")
    print("Disable with: stopcodex")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
