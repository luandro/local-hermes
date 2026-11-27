#!/usr/bin/env python3
"""Eval-only round-gating proxy (fallback for Hermes bug #741).

Hermes v0.23.4's OpenAI-wire path never sets `tool_choice` (no per-turn /
first-hop enforcement; `tool_use_enforcement` is prompt-only). llama-server
DOES honour `tool_choice` in the request body (common_chat_tool_choice_parse_oaicompat).
This tiny proxy sits between Hermes and llama-server and forces exactly one
tool call on the FIRST hop of a task, then reverts to auto so the model can
answer after seeing the tool result.

First-hop detection is stateless: a request is the first hop iff none of its
messages are a tool result (`role == "tool"`) or an assistant tool-call turn.
The first request of any task carries only system + user messages; once the
model calls a tool, the next request carries the tool result -> auto. This
survives separate `hermes -z` invocations with no session bookkeeping.

Usage:
  python3 eval/tool_choice_proxy.py --listen 8081 --upstream http://127.0.0.1:8080
  # then point Hermes base_url at http://127.0.0.1:8081/v1

No secrets. Read-only wrt the model; only mutates the `tool_choice` field.
"""
import argparse
import json
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = 8081
UPSTREAM = "http://127.0.0.1:8080"
MODE = "gate"  # "gate" = required on first hop; "passthrough" = never touch tool_choice
HOP = 0


def _is_first_hop(messages):
    """First hop = no prior tool result and no assistant tool-call turn."""
    for m in messages or []:
        if m.get("role") == "tool":
            return False
        if m.get("role") == "assistant" and m.get("tool_calls"):
            return False
    return True


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # quiet
        pass

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        # Only rewrite chat/completions; pass everything else through untouched.
        if "chat/completions" in self.path and raw:
            try:
                body = json.loads(raw)
                if isinstance(body, dict) and body.get("messages"):
                    global HOP
                    HOP += 1
                    if MODE == "gate":
                        body["tool_choice"] = "required" if _is_first_hop(body["messages"]) else "auto"
                    print(f"[proxy] hop={HOP} first_hop={_is_first_hop(body['messages'])} "
                          f"tool_choice={body.get('tool_choice')}", flush=True)
                    raw = json.dumps(body).encode()
            except Exception:
                pass  # leave body as-is on any parse error
        url = UPSTREAM + self.path
        req = urllib.request.Request(url, data=raw if raw else None, method=method)
        for k, v in self.headers.items():
            if k.lower() in ("host", "content-length", "connection"):
                continue
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                self.send_response(r.status)
                ctype = r.headers.get("Content-Type", "application/json")
                self.send_header("Content-Type", ctype)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = r.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
        except Exception as e:
            msg = json.dumps({"error": {"message": f"proxy upstream error: {e}"}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        self._proxy("GET")


def main():
    global LISTEN, UPSTREAM, MODE
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=LISTEN)
    ap.add_argument("--upstream", default=UPSTREAM)
    ap.add_argument("--mode", choices=["gate", "passthrough"], default="gate",
                    help="gate=force required on first hop; passthrough=never modify (baseline)")
    args = ap.parse_args()
    LISTEN, UPSTREAM, MODE = args.listen, args.upstream, args.mode
    srv = ThreadingHTTPServer(("127.0.0.1", LISTEN), Handler)
    print(f"tool_choice_proxy on 127.0.0.1:{LISTEN} -> {UPSTREAM} mode={MODE}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()