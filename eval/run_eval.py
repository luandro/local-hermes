#!/usr/bin/env python3
"""Small local eval for the LFM3-1.7B-hermes setup (hardened per Codex review).

Modes:
  model    — POST each prompt to llama.cpp /v1/chat/completions (fast inner loop).
  agent    — `hermes -z "<prompt>"` full agent (system prompt + tools).
  telegram — send the prompt to the live Telegram bot and measure the reply
             (the real user path; records queue wait + reply latency).

Scoring (strict):
  - Uses FINAL content only (not reasoning_content), unless --allow-reasoning.
  - Ordinary cases FAIL on finish_reason=length (runaway/truncation guard).
  - expect_any is a case-insensitive substring; tool cases can add `expect_regex`.
  - Per-case wall-clock budget (--budget) is enforced; empty selection is an error.

Usage:
  python3 run_eval.py --tier 1 --mode model
  python3 run_eval.py --tier 2 --mode agent --timeout 1200 --budget 1200
  python3 run_eval.py --mode telegram --only oi --budget 600
"""
import argparse, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from memtrace import MemTracer, _find_llama_pid  # E00 memory-pressure instrumentation
BASE = os.environ.get("HERMES_BASE_URL", "http://127.0.0.1:8080/v1")
MODEL = os.environ.get("HERMES_MODEL", "lfm3-harbor-hermes")
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
SERVER_LOG = Path(os.environ.get("LLAMA_SERVER_LOG", "/tmp/opencode/serve.log"))


def _env():
    env = {}
    for line in (HERMES_HOME / ".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def call_model(prompt, max_tokens, timeout, allow_reasoning):
    body = json.dumps({"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                       "max_tokens": max_tokens, "temperature": 0}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
    except Exception as e:
        return {"ok": False, "error": str(e), "latency": time.time() - t0}
    dt = time.time() - t0
    ch = d["choices"][0]
    msg = ch["message"]
    content = msg.get("content") or ""
    if allow_reasoning:
        content += "\n" + (msg.get("reasoning_content") or "")
    u = d.get("usage", {})
    return {"ok": True, "text": content.strip(), "latency": dt, "finish": ch.get("finish_reason"),
            "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens")}


def call_agent(prompt, timeout, usage_path=None):
    t0 = time.time()
    cmd = ["hermes", "-z", prompt]
    tmp_usage = None
    if usage_path is None:
        # one-shot: write usage to a temp file so we can account tokens per hop
        import tempfile
        fd, tmp_usage = tempfile.mkstemp(suffix=".usage.json", prefix="hermes_eval_")
        os.close(fd)
        usage_path = tmp_usage
        cmd += ["--usage-file", usage_path]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        usage = None
        try:
            usage = json.loads(Path(usage_path).read_text())
        except Exception:
            usage = None
        return {"ok": p.returncode == 0, "text": (p.stdout or p.stderr).strip(),
                "latency": time.time() - t0, "finish": f"exit={p.returncode}",
                "usage": usage}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "latency": time.time() - t0}
    finally:
        if tmp_usage:
            try:
                os.unlink(tmp_usage)
            except OSError:
                pass


def _last_prompt_tokens(server_log, since):
    """Best-effort: total prompt tokens of the most recent turn from llama.cpp log."""
    try:
        with open(server_log, "r", errors="ignore") as f:
            lines = f.readlines()[-400:]
    except OSError:
        return None
    total = None
    for ln in lines:
        m = re.search(r"prompt processing, n_tokens =\s*(\d+), progress =\s*([0-9.]+)", ln)
        if m:
            n, p = int(m.group(1)), float(m.group(2))
            if p > 0:
                total = round(n / p)
    return total


def call_telegram(prompt, timeout):
    """Measure the LAST real Telegram turn from logs (user triggers by sending a msg).
    Enforces a numeric prompt-token budget. Does NOT send (bot self-messages don't
    trigger the agent, and we must correlate to a real user turn)."""
    gw = HERMES_HOME / "logs" / "gateway.log"
    srv = SERVER_LOG
    ptok = _last_prompt_tokens(srv, 0)
    # latency: last inbound->response-ready pair for telegram
    lat = None
    try:
        lines = gw.read_text(errors="ignore").splitlines()
        ready_ts = inbound_ts = None
        for ln in reversed(lines):
            if ready_ts is None and "response ready: platform=telegram" in ln:
                ready_ts = ln[:19]
            elif ready_ts is not None and "inbound message: platform=telegram" in ln:
                inbound_ts = ln[:19]; break
        if ready_ts and inbound_ts:
            import datetime as dt
            fmt = "%Y-%m-%d %H:%M:%S"
            lat = (dt.datetime.strptime(ready_ts, fmt) - dt.datetime.strptime(inbound_ts, fmt)).total_seconds()
    except Exception:
        pass
    return {"ok": False, "error": "advisory: bot API can't read the user's reply; verify in chat",
            "text": f"last telegram turn: ptok={ptok} latency={lat}s", "finish": "manual",
            "latency": lat or 0.0, "prompt_tokens": ptok,
            "note": "user-triggered; confirm reply text in Telegram"}


def _fetch_text(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-eval"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "ignore").lower()
    except Exception:
        return ""


def _root_use_pct():
    try:
        out = subprocess.run(["df", "-P", "/"], capture_output=True, text=True, timeout=10).stdout
        for ln in out.splitlines()[1:]:
            parts = ln.split()
            for p in parts:
                if p.endswith("%"):
                    return p
    except Exception:
        pass
    return None


def score(case, res, allow_reasoning):
    if not res.get("ok"):
        return False, res.get("error", "not ok")
    if res.get("finish") == "length" and not case.get("tools"):
        return False, "length(truncated)"
    text = res.get("text") or ""
    if case.get("verify_df"):
        real = _root_use_pct()
        if not real:
            return False, "df-unavailable"
        real_i = int(real.rstrip("%"))
        found = [int(x) for x in re.findall(r"(\d+)%", text)]
        ok = real_i in found
        return (ok, f"df-eq({real_i})" if ok else f"df-mismatch(real={real_i},got={found})")
    if case.get("verify_httpd"):
        # Must be the run-owned container, not any listener.
        running = False
        try:
            insp = subprocess.run(
                ["docker", "inspect", "-f",
                 "{{.State.Running}}|{{.Config.Image}}|{{range $p,$b := .NetworkSettings.Ports}}{{$p}}={{$b}} {{end}}",
                 "hermes-httpd"], capture_output=True, text=True, timeout=15)
            out = insp.stdout
            running = ("true" in out) and ("nginx" in out.lower()) and ("8099" in out)
        except Exception:
            running = False
        # Direct loopback probe, no proxies, no redirect following.
        code = None
        try:
            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *a, **k):
                    return None
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
            req = urllib.request.Request("http://127.0.0.1:8099/", method="GET")
            with opener.open(req, timeout=10) as r:
                code = r.getcode()
        except Exception as e:
            code = str(e)
        ok = running and (code == 200)
        return (ok, f"httpd(run={running},http={code})")
    if case.get("verify_selfh"):
        page = _fetch_text("https://r2.selfh.st/directory/software.json")
        if not page:
            return False, "selfh-fetch-failed"
        try:
            data = json.loads(page)
            names = [e.get("name", "") for e in data.get("entries", []) if e.get("name")]
        except Exception:
            return False, "selfh-parse-fail"
        low = text.lower()
        matched = {n for n in names if n.lower() in low}
        ok = len(matched) >= 3
        return (ok, f"selfh-apps={len(matched)}" if ok else f"selfh<3apps({len(matched)})")
    if case.get("expect_json") is not None:
        try:
            obj = json.loads(text.strip())
            ok = all(obj.get(k) == v for k, v in case["expect_json"].items())
            return ok, ("json-ok" if ok else "json-mismatch")
        except Exception:
            return False, "json-parse-fail"
    if case.get("expect_exact"):
        ci = case.get("ci")
        cand = [e.lower() for e in case["expect_exact"]] if ci else case["expect_exact"]
        val = text.strip().lower() if ci else text.strip()
        return (val in cand, "exact" if val in cand else "no-exact")
    if case.get("expect_nonempty"):
        return (len(text.strip()) > 0, "nonempty" if text.strip() else "empty")
    if case.get("expect_regex"):
        return (bool(re.search(case["expect_regex"], text, re.I)), "regex")
    hay = text.lower()
    ok = any(e.lower() in hay for e in case.get("expect_any", []))
    return ok, ("match" if ok else "no-match")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="all", choices=["1", "2", "all"])
    ap.add_argument("--mode", default="model", choices=["model", "agent", "telegram"])
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--budget", type=int, default=300, help="per-case wall-clock budget (s); 0 disables")
    ap.add_argument("--allow-reasoning", action="store_true")
    ap.add_argument("--max-prompt-tokens", type=int, default=8000,
                    help="fail any turn whose prompt exceeds this (runaway/oversize guard)")
    ap.add_argument("--only", default="")
    ap.add_argument("--pause-gateway", action="store_true",
                    help="stop the Telegram gateway during the run and restart it after (clean signal)")
    ap.add_argument("--no-memtrace", action="store_true",
                    help="disable per-case memory-pressure tracing (PSI/swap/majflt)")
    args = ap.parse_args()

    cases = [json.loads(l) for l in (HERE / "prompts.jsonl").read_text().splitlines() if l.strip()]
    if args.tier != "all":
        cases = [c for c in cases if str(c["tier"]) == args.tier]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only.split(",")]
    if not cases:
        print("ERROR: empty selection", file=sys.stderr); sys.exit(2)

    if args.pause_gateway:
        try:
            subprocess.run(["hermes", "gateway", "stop"], capture_output=True, text=True, timeout=180)
        except Exception as e:
            print(f"warn: gateway stop timed out/failed ({e}); continuing")
        time.sleep(2)
    results, npass = [], 0
    llama_pid = None if args.no_memtrace else _find_llama_pid()
    if not args.no_memtrace and not llama_pid:
        print("note: no llama-server pid found; memory trace disabled", file=sys.stderr)
    print(f"{'id':8} {'t':2} {'res':5} {'lat(s)':>7} {'ptok':>5} {'ctok':>5} {'swapin':>6}  note")
    print("-" * 80)
    try:
        for c in cases:
            to = min(args.timeout, args.budget) if args.budget else args.timeout
            mt = MemTracer(llama_pid) if llama_pid else None
            if mt:
                mt.__enter__()
            try:
                if args.mode == "agent":
                    res = call_agent(c["prompt"], to)
                elif args.mode == "telegram":
                    res = call_telegram(c["prompt"], to)
                else:
                    res = call_model(c["prompt"], c["max_tokens"], to, args.allow_reasoning)
            finally:
                mem = mt.stop() if mt else None
            # Agent mode: pull token counts from the --usage-file report.
            if args.mode == "agent" and res.get("usage"):
                u = res["usage"]
                # oneshot.py schema: input_tokens/output_tokens/cache_read_tokens/
                # cache_write_tokens/reasoning_tokens/total_tokens/api_calls
                res["prompt_tokens"] = u.get("input_tokens")
                res["completion_tokens"] = u.get("output_tokens")
                res["cache_read_tokens"] = u.get("cache_read_tokens")
                res["cache_write_tokens"] = u.get("cache_write_tokens")
                res["reasoning_tokens"] = u.get("reasoning_tokens")
                res["api_calls"] = u.get("api_calls")
            ok, why = score(c, res, args.allow_reasoning)
            pt = res.get("prompt_tokens")
            if pt is not None and pt > args.max_prompt_tokens:
                ok, why = False, f"prompt>{args.max_prompt_tokens}({pt})"
            if args.budget and res.get("latency", 0) > args.budget:
                ok, why = False, "over-budget"
            npass += ok
            txt = (res.get("text") or "").replace("\n", " ")[:34]
            swpin = (mem or {}).get("pswpin_delta")
            print(f"{c['id']:8} {c['tier']:<2} {('PASS' if ok else 'FAIL'):5} {res.get('latency',0):7.1f} "
                  f"{str(res.get('prompt_tokens','')):>5} {str(res.get('completion_tokens','')):>5} "
                  f"{str(swpin if swpin is not None else ''):>6}  {why} | {txt}")
            rec = {"id": c["id"], "pass": ok, "why": why, **res}
            if mem:
                rec["mem"] = mem
            results.append(rec)
            (HERE / f"results_{args.mode}.json").write_text(json.dumps(results, indent=2))
    finally:
        if args.pause_gateway:
            try:
                subprocess.run(["hermes", "gateway", "start"], capture_output=True, text=True, timeout=180)
            except Exception as e:
                print(f"warn: gateway restart failed ({e}); run 'hermes gateway start'")

    print("-" * 80)
    # E00 summary: aggregate memory pressure across the run (null-safe).
    if not args.no_memtrace and results:
        swpins = [r["mem"]["pswpin_delta"] for r in results if r.get("mem") and r["mem"].get("pswpin_delta") is not None]
        majflts = [r["mem"]["pgmajfault_delta"] for r in results if r.get("mem") and r["mem"].get("pgmajfault_delta") is not None]
        memin = [r["mem"]["mem_available_min_kb"] for r in results if r.get("mem") and r["mem"].get("mem_available_min_kb") is not None]
        if swpins:
            print(f"MEMPRESSURE  swapin_total={sum(swpins)}  majflt_total={sum(majflts) if majflts else '?'}  "
                  f"avail_min_kb={min(memin) if memin else '?'}")
    print(f"TOTAL {npass}/{len(cases)} pass  ({args.mode}, tier={args.tier})")
    sys.exit(0 if npass == len(cases) else 1)


if __name__ == "__main__":
    main()