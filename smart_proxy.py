#!/usr/bin/env python3
"""Smart model-routing proxy for Claude Code (Anthropic Messages API).

Claude Code → this proxy → Anthropic (or any OpenAI-compatible that serves /v1/messages).

Flow:
  1. Receive POST /v1/messages
  2. Extract last user text
  3. Classify with local ONNX (scene + difficulty)
  4. Pick real model: opus / sonnet / haiku
  5. Rewrite body.model, forward, stream SSE back unchanged

Usage:
  python smart_proxy.py --port 8787 --upstream https://YOUR-ENDPOINT
  # or rely on ANTHROPIC_BASE_URL in ~/.claude/settings.json

  # Claude Code:
  #   ANTHROPIC_BASE_URL=http://127.0.0.1:8787/v1
  #   keep ANTHROPIC_AUTH_TOKEN as-is (proxy forwards it)

Model mapping (cost-aware, Anthropic official positioning):

  Pricing (API, per MTok in/out):
    Fable 5.1   $10/$50   long-horizon agentic / hardest reasoning
    Opus 5      $5/$25    complex agentic coding & enterprise
    Opus 4.8    $5/$25    legacy Opus (same price as Opus 5)
    Sonnet 5    $2/$10    speed + intelligence default

  Rules:
    software + hard                         → fable   (chỉ chỗ này worth $10/$50)
    any scene + hard (không phải software)  → opus5
    software|design|research + medium       → opus48
    office|media|other + medium             → sonnet
    easy (any scene)                        → sonnet
    fallback                                → sonnet
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Reuse the ported classifier
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import QueryClassifier  # noqa: E402

# No default upstream — resolve from --upstream or ~/.claude/settings.json

# (scene_set, difficulty) → alias; resolved via --fable/--opus5/--opus48/--sonnet
RULES = [
    ({"software"}, {"hard"}, "fable"),
    ({"design", "research", "office", "media", "other"}, {"hard"}, "opus5"),
    ({"software", "design", "research"}, {"medium"}, "opus48"),
    ({"office", "media", "other"}, {"medium"}, "sonnet"),
    ({"office", "media", "design", "research", "software", "other"}, {"easy"}, "sonnet"),
]


def pick_alias(scene: str, difficulty: str) -> str:
    for scenes, diffs, alias in RULES:
        if scene in scenes and difficulty in diffs:
            return alias
    return "sonnet"


def extract_text(messages: list) -> str:
    """Concatenate text blocks from the last user message (enough for classification)."""
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text") or "")
                elif isinstance(block, str):
                    parts.append(block)
            text = "\n".join(parts).strip()
            if text:
                return text
    # fallback: first user message any
    for msg in messages or []:
        if msg.get("role") == "user":
            c = msg.get("content")
            return c if isinstance(c, str) else json.dumps(c)[:2000]
    return ""


class Proxy:
    def __init__(self, *, classifier: QueryClassifier, upstream: str, upstream_key: str | None,
                 fable: str, opus5: str, opus48: str, sonnet: str, log_path: Path | None):
        self.clf = classifier
        self.upstream = upstream.rstrip("/")
        self.upstream_key = upstream_key
        self.names = {"fable": fable, "opus5": opus5, "opus48": opus48, "sonnet": sonnet}
        self.log_path = log_path
        self._log_f = open(log_path, "a", encoding="utf-8") if log_path else None

    def log(self, event: dict) -> None:
        event["ts"] = time.time()
        line = json.dumps(event, ensure_ascii=False)
        print(line, file=sys.stderr, flush=True)
        if self._log_f:
            self._log_f.write(line + "\n")
            self._log_f.flush()

    def classify_and_route(self, body: dict) -> tuple[str, dict]:
        text = extract_text(body.get("messages") or [])
        # truncate to classifier max (256 tokens ≈ ~800 chars safe)
        snippet = text[:800]
        info = {"text_preview": snippet[:120]}
        if not snippet.strip():
            info["reason"] = "empty"
            return self.names["sonnet"], info
        try:
            r = self.clf.classify([snippet])[0]
        except Exception as exc:
            info["reason"] = f"classifier_error:{exc}"
            return self.names["sonnet"], info
        alias = pick_alias(r["scene"], r["difficulty"])
        info.update(
            {
                "scene": r["scene"],
                "scene_prob": round(r["scene_prob"], 3),
                "difficulty": r["difficulty"],
                "difficulty_prob": round(r["difficulty_prob"], 3),
                "alias": alias,
            }
        )
        return self.names[alias], info


class Handler(BaseHTTPRequestHandler):
    proxy: Proxy  # set on server

    def log_message(self, fmt, *args):  # silence default access log
        pass

    def _send_json(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _log_req(self, method: str, path: str) -> None:
        self.proxy.log({
            "event": "http",
            "method": method,
            "path": path,
            "auth": "bearer" if (self.headers.get("authorization") or "").lower().startswith("bearer") else (
                "x-api-key" if self.headers.get("x-api-key") else "none"
            ),
            "ua": (self.headers.get("user-agent") or "")[:80],
        })

    def _upstream_headers(self, *, accept: str | None = None) -> dict:
        headers = {
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "anthropic-version": self.headers.get("anthropic-version", "2023-06-01"),
        }
        if accept:
            headers["Accept"] = accept
        # Forward every header Claude Code / SDKs typically send
        for h in self.headers.items():
            key = h[0] if isinstance(h, tuple) else h
            kl = key.lower()
            if kl in (
                "host", "content-length", "connection", "transfer-encoding",
                "accept-encoding",  # let urllib handle
            ):
                continue
            v = self.headers.get(key)
            if v:
                headers[key] = v
        if self.proxy.upstream_key:
            headers["x-api-key"] = self.proxy.upstream_key
            headers.pop("authorization", None)
            headers.pop("Authorization", None)
        return headers

    def _relay(self, upstream_resp, *, streaming: bool, t0: float) -> None:
        status = upstream_resp.status
        ctype = upstream_resp.headers.get("Content-Type", "application/json")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if streaming:
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    chunk = upstream_resp.read(8192)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                upstream_resp.close()
            self.proxy.log({"event": "stream_done", "ms": round((time.time() - t0) * 1000, 1)})
            return
        data = upstream_resp.read()
        upstream_resp.close()
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.proxy.log({"event": "done", "ms": round((time.time() - t0) * 1000, 1), "bytes": len(data)})

    def _norm_path(self) -> str:
        """Path without query; collapse Claude Code's double /v1/v1/."""
        path = self.path.split("?", 1)[0]
        if path.startswith("/v1/v1/"):
            path = path[len("/v1"):]
        return path

    def do_GET(self):
        self._log_req("GET", self.path)
        path = self._norm_path()
        if path in ("/", "/health", "/healthz"):
            self._send_json(200, {"ok": True, "service": "mimo-smart-proxy"})
            return
        # Pass-through GET to upstream (e.g. /v1/models)
        if path.startswith("/v1/"):
            qs = self.path.split("?", 1)
            url = f"{self.proxy.upstream}{path}"
            if len(qs) > 1:
                url += "?" + qs[1]
            req = urllib.request.Request(url, headers=self._upstream_headers(), method="GET")
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
                self._send_json(502, {"error": {"type": "api_error", "message": str(exc)}})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self):
        self._log_req("POST", self.path)
        path = self._norm_path()

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        # Only /v1/messages gets model rewriting; other /v1/* pass through
        if not path.startswith("/v1/messages"):
            if path.startswith("/v1/"):
                qs = self.path.split("?", 1)
                url = f"{self.proxy.upstream}{path}"
                if len(qs) > 1:
                    url += "?" + qs[1]
                req = urllib.request.Request(
                    url, data=raw if raw else None, headers=self._upstream_headers(), method="POST"
                )
                try:
                    with urllib.request.urlopen(req, timeout=60) as r:
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
                    self._send_json(502, {"error": {"type": "api_error", "message": str(exc)}})
                return
            self._send_json(404, {"error": {"type": "not_found_error", "message": f"unknown path {self.path}"}})
            return

        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._send_json(400, {"error": {"type": "invalid_request_error", "message": f"bad json: {exc}"}})
            return

        t0 = time.time()
        real_model, info = self.proxy.classify_and_route(body)
        body = dict(body)
        original_model = body.get("model")
        body["model"] = real_model

        # Log tool names only (no payloads) to debug WebFetch_ide issues
        tool_names = []
        for t in (body.get("tools") or []):
            if isinstance(t, dict):
                tool_names.append(t.get("name") or "?")
        info["tools"] = tool_names
        info["n_tools"] = len(tool_names)
        info["has_ide"] = any((n or "").endswith("_ide") for n in tool_names)

        headers = self._upstream_headers(
            accept="text/event-stream" if body.get("stream") else "application/json"
        )

        self.proxy.log(
            {
                "event": "route",
                "path": self.path,
                "original_model": original_model,
                "routed_model": real_model,
                "stream": bool(body.get("stream")),
                "classify_ms": round((time.time() - t0) * 1000, 1),
                **info,
            }
        )

        qs = self.path.split("?", 1)
        upstream_url = f"{self.proxy.upstream}/v1/messages"
        if len(qs) > 1:
            upstream_url += "?" + qs[1]
        req = urllib.request.Request(
            upstream_url,
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )

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
            self.proxy.log({
                "event": "upstream_error",
                "status": e.code,
                "original_model": original_model,
                "routed_model": real_model,
                "body": err_body[:400].decode("utf-8", "replace"),
            })
            return
        except Exception as exc:
            self._send_json(502, {"error": {"type": "api_error", "message": str(exc)}})
            return

        # Relay + debug headers
        status = upstream.status
        ctype = upstream.headers.get("Content-Type", "application/json")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("X-Smart-Route", real_model)
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
    p = argparse.ArgumentParser(description="Smart model-routing proxy for Claude Code")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--upstream", default=None,
                   help="Anthropic-compatible base, no trailing /v1 (required if no ANTHROPIC_BASE_URL)")
    p.add_argument("--upstream-key", default=None, help="If set, override x-api-key to upstream")
    p.add_argument("--fable", default="claude-fable-5-1", help="software+hard only ($10/$50)")
    p.add_argument("--opus5", default="claude-opus-5", help="other hard ($5/$25)")
    p.add_argument("--opus48", default="claude-opus-4-8", help="medium software/design/research ($5/$25)")
    p.add_argument("--sonnet", default="claude-sonnet-5", help="easy + medium office ($2/$10)")
    p.add_argument("--classifier-dir", default=None, help="Query classifier asset dir")
    p.add_argument("--log", default=str(Path(__file__).resolve().parent / "smart_proxy.log.jsonl"))
    args = p.parse_args(argv)

    upstream = args.upstream
    if not upstream:
        # read from Claude settings; do not invent an endpoint
        settings = Path.home() / ".claude/settings.json"
        try:
            env = json.loads(settings.read_text()).get("env") or {}
            base = (env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")
            if base.endswith("/v1"):
                base = base[: -len("/v1")]
            upstream = base or None
        except Exception:
            upstream = None
    if not upstream:
        print("ERROR: --upstream required (or set ANTHROPIC_BASE_URL in ~/.claude/settings.json)",
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
    proxy = Proxy(
        classifier=clf,
        upstream=args.upstream,
        upstream_key=args.upstream_key,
        fable=args.fable,
        opus5=args.opus5,
        opus48=args.opus48,
        sonnet=args.sonnet,
        log_path=Path(args.log),
    )

    class H(Handler):
        pass

    H.proxy = proxy
    server = ThreadingHTTPServer((args.host, args.port), H)
    print(f"smart proxy on http://{args.host}:{args.port}/v1", file=sys.stderr)
    print(f"  upstream = {args.upstream}", file=sys.stderr)
    print(f"  fable={args.fable}  opus5={args.opus5}  opus48={args.opus48}  sonnet={args.sonnet}", file=sys.stderr)
    print(f"  log = {args.log}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
