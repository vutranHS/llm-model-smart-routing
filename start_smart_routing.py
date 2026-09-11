#!/usr/bin/env python3
"""Start smart routing proxy using the EXISTING Claude Code custom endpoint.

Reads ~/.claude/settings.json:
  ANTHROPIC_BASE_URL   → upstream (must already be set)
  ANTHROPIC_AUTH_TOKEN → forwarded as Authorization: Bearer

Usage:
  python start_smart_routing.py
  python start_smart_routing.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = Path.home() / ".claude/settings.json"


def load_env() -> dict:
    data = json.loads(SETTINGS.read_text())
    return data.get("env") or {}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--fable", default=None)
    p.add_argument("--opus5", default=None)
    p.add_argument("--opus48", default=None)
    p.add_argument("--sonnet", default=None)
    args = p.parse_args()

    env = load_env()
    base_url = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
    token = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or ""
    if not base_url:
        print("ERROR: no ANTHROPIC_BASE_URL in ~/.claude/settings.json", file=sys.stderr)
        return 1
    if base_url.endswith("/v1"):
        upstream = base_url[: -len("/v1")]
    else:
        upstream = base_url

    # 9router catalog (probed). Cost-aware defaults.
    fable = args.fable or os.environ.get("SMART_FABLE") or "cc/claude-fable-5-1"
    opus5 = args.opus5 or os.environ.get("SMART_OPUS5") or "cc/claude-opus-5"
    opus48 = args.opus48 or os.environ.get("SMART_OPUS48") or "cc/claude-opus-4-8"
    sonnet = args.sonnet or os.environ.get("SMART_SONNET") or "cc/claude-sonnet-5"

    print("=== Smart Routing Plan ===")
    print(f"  upstream     : {upstream}")
    print(f"  auth source  : ~/.claude/settings.json  ANTHROPIC_AUTH_TOKEN ({'set' if token else 'MISSING'})")
    print(f"  proxy listen : http://{args.host}:{args.port}/v1")
    print()
    print("  software+hard              → fable   " + fable + "   ($10/$50)")
    print("  other hard                 → opus5   " + opus5 + "   ($5/$25)")
    print("  medium sw/design/research  → opus48  " + opus48 + "   ($5/$25)")
    print("  easy + medium office       → sonnet  " + sonnet + "   ($2/$10)")
    print()
    print("Claude Code config AFTER proxy is running:")
    print(f"  ANTHROPIC_BASE_URL=http://{args.host}:{args.port}/v1")
    print("  (keep ANTHROPIC_AUTH_TOKEN unchanged — proxy forwards it)")
    print()

    if args.dry_run:
        return 0

    os.environ.setdefault("ANTHROPIC_AUTH_TOKEN", token)

    cmd = [
        str(HERE / ".venv-classifier/bin/python"),
        str(HERE / "smart_proxy.py"),
        "--host", args.host,
        "--port", str(args.port),
        "--upstream", upstream,
        "--fable", fable,
        "--opus5", opus5,
        "--opus48", opus48,
        "--sonnet", sonnet,
        "--log", str(HERE / "smart_proxy.log.jsonl"),
    ]
    print("exec:", " ".join(cmd))
    os.execv(cmd[0], cmd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
