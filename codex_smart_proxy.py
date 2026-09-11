#!/usr/bin/env python3
"""Smart model-routing proxy for Codex CLI (OpenAI Responses API).

Codex → this proxy → your OpenAI-compatible upstream via POST /v1/responses.

Rewrites:
  - body.model           (scene + difficulty + token budget)
  - body.reasoning.effort (low | medium | high | xhigh)

Pricing (short-context, per 1M in/out) — official OpenAI:
  gpt-6-astra   $10 / $50   flagship
  gpt-5.6-sol   $4  / $20   strong
  gpt-5.6-terra $2  / $12   mid
  gpt-5.6-luna  $0.2/$1.2   cheap
  ALL: input >272K → 2x input, 1.5x output on FULL request.

Token rule: if estimated input > SAFE_TOKENS (default 200k), drop a tier
to stay under the 272K pricing cliff.

Usage:
  python codex_smart_proxy.py --port 8788 --upstream https://YOUR-ENDPOINT
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import QueryClassifier  # noqa: E402

# No default upstream — resolve from --upstream, CODEX_UPSTREAM, state, or codex config
SAFE_TOKENS = 200_000          # stay under 272K pricing cliff
LONG_CTX_PRICING = 272_000

# tier order cheap → expensive
TIERS = ["luna", "terra", "sol", "astra"]

# (scene_set, difficulty) → (tier, effort)
# effort: none | low | medium | high | xhigh
BASE_RULES = [
    # software hard → astra high (the only place astra is worth $10/$50)
    ({"software"}, {"hard"}, "astra", "high"),
    # other hard → sol high
    ({"design", "research", "office", "media", "other"}, {"hard"}, "sol", "high"),
    # medium tech → terra medium
    ({"software", "design", "research"}, {"medium"}, "terra", "medium"),
    # medium office / easy → luna low
    ({"office", "media", "other"}, {"medium"}, "luna", "low"),
    ({"office", "media", "design", "research", "software", "other"}, {"easy"}, "luna", "low"),
]


def pick_base(scene: str, difficulty: str) -> tuple[str, str]:
    for scenes, diffs, tier, effort in BASE_RULES:
        if scene in scenes and difficulty in diffs:
            return tier, effort
    return "luna", "low"


def demote_for_tokens(tier: str, est_tokens: int) -> tuple[str, str]:
    """If input is huge, drop a tier so 2x-price still cheaper than astra 1x."""
    if est_tokens <= SAFE_TOKENS:
        return tier, ""
    idx = TIERS.index(tier) if tier in TIERS else 0
    # long context: never sit on astra; drop at least one tier
    new_idx = min(idx, 1) if idx >= 2 else idx
    # extra demote if extremely long
    if est_tokens > 500_000:
        new_idx = min(new_idx, 0)  # luna
    elif est_tokens > 350_000:
        new_idx = min(new_idx, 1)  # terra
    return TIERS[new_idx], f"demoted_for_tokens:{est_tokens}"


def estimate_tokens(body: dict) -> int:
    """Rough token estimate from Responses API payload (chars/4)."""
    total_chars = 0

    def walk(obj):
        nonlocal total_chars
        if isinstance(obj, str):
            total_chars += len(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    # input can be str or list of items
    walk(body.get("input"))
    walk(body.get("instructions"))
    # tools definitions also eat context
    for t in body.get("tools") or []:
        if isinstance(t, dict):
            walk(t.get("name") or "")
            walk(json.dumps(t.get("parameters") or t.get("function") or {}))
    # ~4 chars/token English, ~2 for CJK-heavy — use 3.5 as compromise
    return max(1, int(total_chars / 3.5))


def extract_prompt_text(body: dict) -> str:
    """Pull last user text for the classifier."""
    inp = body.get("input")
    if isinstance(inp, str):
        return inp
    if isinstance(inp, list):
        for item in reversed(inp):
            if not isinstance(item, dict):
                continue
            role = item.get("role") or item.get("type")
            if role in ("user", "message"):
                content = item.get("content") or item.get("text") or ""
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = []
                    for c in content:
                        if isinstance(c, dict):
                            parts.append(c.get("text") or "")
                        elif isinstance(c, str):
                            parts.append(c)
                    t = "\n".join(parts).strip()
                    if t:
                        return t
        # fallback first stringy item
        for item in inp:
            if isinstance(item, dict) and item.get("role") == "user":
                c = item.get("content")
                if isinstance(c, str):
                    return c
    return body.get("instructions") or ""


class Proxy:
    def __init__(self, *, classifier: QueryClassifier, upstream: str, models: dict,
                 log_path: Path | None, safe_tokens: int, log_prompts: bool = False):
        self.clf = classifier
        self.upstream = upstream.rstrip("/")
        self.models = models  # tier → model id
        self.safe_tokens = safe_tokens
        self.log_path = log_path
        self.log_prompts = log_prompts
        self._log_f = open(log_path, "a", encoding="utf-8") if log_path else None

    def log(self, event: dict) -> None:
        event["ts"] = time.time()
        line = json.dumps(event, ensure_ascii=False)
        print(line, file=sys.stderr, flush=True)
        if self._log_f:
            self._log_f.write(line + "\n")
            self._log_f.flush()

    def route(self, body: dict) -> tuple[str, str, dict]:
        text = extract_prompt_text(body)
        est = estimate_tokens(body)
        info: dict = {"est_tokens": est}
        if self.log_prompts:
            info["text_preview"] = text[:120]

        if not text.strip():
            info["reason"] = "empty"
            return self.models["luna"], "low", info

        try:
            r = self.clf.classify([text])[0]
        except Exception as exc:
            info["reason"] = f"classifier_error:{exc}"
            return self.models["luna"], "low", info

        tier, effort = pick_base(r["scene"], r["difficulty"])
        info.update({
            "scene": r["scene"],
            "scene_prob": round(r["scene_prob"], 3),
            "difficulty": r["difficulty"],
            "difficulty_prob": round(r["difficulty_prob"], 3),
            "base_tier": tier,
            "base_effort": effort,
        })

        # token-aware demotion
        new_tier, demote_note = demote_for_tokens(tier, est)
        if demote_note:
            info["demote"] = demote_note
        tier = new_tier

        # long context + cheap tier → bump effort (luna-max pattern)
        if est > 100_000 and tier in ("luna", "terra") and effort in ("low", "medium"):
            effort = "high"
            info["effort_bump"] = "long_ctx_luna_high"

        model = self.models.get(tier, self.models["luna"])
        info["tier"] = tier
        info["effort"] = effort
        return model, effort, info


class Handler(BaseHTTPRequestHandler):
    proxy: Proxy

    def log_message(self, *a):
        pass

    def _send_json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _norm_path(self) -> str:
        path = self.path.split("?", 1)[0]
        if path.startswith("/v1/v1/"):
            path = path[len("/v1"):]
        return path

    def _forward_headers(self, accept: str | None = None) -> dict:
        headers = {}
        for key in self.headers.keys():
            kl = key.lower()
            if kl in ("host", "content-length", "connection", "transfer-encoding", "accept-encoding"):
                continue
            v = self.headers.get(key)
            if v:
                headers[key] = v
        headers.setdefault("Content-Type", "application/json")
        if accept:
            headers["Accept"] = accept
        return headers

    def do_GET(self):
        path = self._norm_path()
        if path in ("/", "/health", "/healthz"):
            self._send_json(200, {"ok": True, "service": "codex-smart-proxy"})
            return
        if path.startswith("/v1/"):
            qs = self.path.split("?", 1)
            url = f"{self.proxy.upstream}{path}"
            if len(qs) > 1:
                url += "?" + qs[1]
            req = urllib.request.Request(url, headers=self._forward_headers(), method="GET")
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    body = r.read()
                    self.send_response(r.status)
                    self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
            except urllib.error.HTTPError as e:
                err = e.read()
                self.send_response(e.code)
                self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(err)))
                self.end_headers()
                self.wfile.write(err)
            except Exception as exc:
                self._send_json(502, {"error": {"message": str(exc)}})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        path = self._norm_path()
        self.proxy.log({"event": "http", "method": "POST", "path": self.path,
                        "ua": (self.headers.get("user-agent") or "")[:80]})

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        # Only /v1/responses gets rewrite; other /v1/* pass through
        if not path.startswith("/v1/responses"):
            if path.startswith("/v1/"):
                qs = self.path.split("?", 1)
                url = f"{self.proxy.upstream}{path}"
                if len(qs) > 1:
                    url += "?" + qs[1]
                req = urllib.request.Request(url, data=raw if raw else None,
                                             headers=self._forward_headers(), method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=120) as r:
                        body = r.read()
                        self.send_response(r.status)
                        self.send_header("Content-Type", r.headers.get("Content-Type", "application/json"))
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                except urllib.error.HTTPError as e:
                    err = e.read()
                    self.send_response(e.code)
                    self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(err)))
                    self.end_headers()
                    self.wfile.write(err)
                except Exception as exc:
                    self._send_json(502, {"error": {"message": str(exc)}})
                return
            self._send_json(404, {"error": {"message": f"unknown path {self.path}"}})
            return

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._send_json(400, {"error": {"message": f"bad json: {exc}"}})
            return

        t0 = time.time()
        real_model, effort, info = self.proxy.route(body)
        body = dict(body)
        original_model = body.get("model")
        body["model"] = real_model

        # Rewrite reasoning.effort (Responses API)
        reasoning = dict(body.get("reasoning") or {})
        original_effort = reasoning.get("effort")
        reasoning["effort"] = effort
        body["reasoning"] = reasoning

        info["original_model"] = original_model
        info["original_effort"] = original_effort
        info["routed_model"] = real_model
        info["stream"] = bool(body.get("stream"))
        info["classify_ms"] = round((time.time() - t0) * 1000, 1)
        self.proxy.log({"event": "route", **info})

        qs = self.path.split("?", 1)
        upstream_url = f"{self.proxy.upstream}/v1/responses"
        if len(qs) > 1:
            upstream_url += "?" + qs[1]

        headers = self._forward_headers(
            accept="text/event-stream" if body.get("stream") else "application/json"
        )
        req = urllib.request.Request(upstream_url, data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        streaming = bool(body.get("stream"))
        try:
            upstream = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            err_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
            self.proxy.log({"event": "upstream_error", "status": e.code,
                            "body_bytes": len(err_body)})
            return
        except Exception as exc:
            self._send_json(502, {"error": {"message": str(exc)}})
            return

        self.send_response(upstream.status)
        self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
        self.send_header("X-Smart-Route", real_model)
        self.send_header("X-Smart-Tier", str(info.get("tier", "")))
        self.send_header("X-Smart-Effort", effort)
        self.send_header("X-Smart-Tokens", str(info.get("est_tokens", "")))
        self.send_header("X-Smart-Scene", str(info.get("scene", "")))
        self.send_header("X-Smart-Difficulty", str(info.get("difficulty", "")))

        if streaming:
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    chunk = upstream.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                upstream.close()
            self.proxy.log({"event": "stream_done", "ms": round((time.time() - t0) * 1000, 1)})
            return

        data = upstream.read()
        upstream.close()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.proxy.log({"event": "done", "ms": round((time.time() - t0) * 1000, 1), "bytes": len(data)})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Codex smart routing proxy")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8788)
    p.add_argument("--upstream", default=None,
                   help="OpenAI-compatible base, no trailing /v1 (required if no CODEX_UPSTREAM)")
    p.add_argument("--astra", default="gpt-6-astra")
    p.add_argument("--sol", default="gpt-5.6-sol")
    p.add_argument("--terra", default="gpt-5.6-terra")
    p.add_argument("--luna", default="gpt-5.6-luna")
    p.add_argument("--safe-tokens", type=int, default=SAFE_TOKENS)
    p.add_argument("--classifier-dir", default=None)
    p.add_argument("--log", default=str(Path(__file__).resolve().parent / "codex_smart_proxy.log.jsonl"))
    p.add_argument("--log-prompts", action="store_true",
                   help="Include a 120-character prompt preview in local logs")
    args = p.parse_args(argv)

    upstream = args.upstream or os.environ.get("CODEX_UPSTREAM")
    if not upstream:
        # read original from state or current codex 9router provider — never invent
        state = Path(__file__).resolve().parent / ".codex_smart_state.json"
        try:
            if state.exists():
                u = (json.loads(state.read_text()).get("original_base_url") or "").rstrip("/")
                if u.endswith("/v1"):
                    u = u[: -len("/v1")]
                upstream = u or None
        except Exception:
            upstream = None
    if not upstream:
        cfg = Path.home() / ".codex/config.toml"
        try:
            import re as _re
            text = cfg.read_text()
            m = _re.search(r'\[model_providers\.[^\]]+\][^[]*?base_url\s*=\s*"([^"]+)"', text, _re.S)
            if m:
                u = m.group(1).rstrip("/")
                if u.endswith("/v1"):
                    u = u[: -len("/v1")]
                if "127.0.0.1" not in u and "localhost" not in u:
                    upstream = u
        except Exception:
            pass
    if not upstream:
        print("ERROR: --upstream required (or CODEX_UPSTREAM / state file / codex config)",
              file=sys.stderr)
        return 1
    args.upstream = upstream

    from classify import DEFAULT_DIR
    clf_dir = Path(args.classifier_dir) if args.classifier_dir else DEFAULT_DIR
    if not (clf_dir / "model_fp16.onnx").exists():
        print(f"classifier not found at {clf_dir}", file=sys.stderr)
        return 1

    print(f"loading classifier from {clf_dir} ...", file=sys.stderr)
    clf = QueryClassifier(clf_dir)
    models = {"astra": args.astra, "sol": args.sol, "terra": args.terra, "luna": args.luna}
    proxy = Proxy(classifier=clf, upstream=args.upstream, models=models,
                  log_path=Path(args.log), safe_tokens=args.safe_tokens,
                  log_prompts=args.log_prompts)

    class H(Handler):
        pass
    H.proxy = proxy
    server = ThreadingHTTPServer((args.host, args.port), H)
    print(f"codex smart proxy on http://{args.host}:{args.port}/v1", file=sys.stderr)
    print(f"  upstream = {args.upstream}", file=sys.stderr)
    print(f"  astra={args.astra}  sol={args.sol}  terra={args.terra}  luna={args.luna}", file=sys.stderr)
    print(f"  safe_tokens={args.safe_tokens}  (pricing cliff {LONG_CTX_PRICING})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
