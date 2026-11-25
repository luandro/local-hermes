# Local Hermes (LFM3-1.7B) — Eval & Improvement Loop

Goal: get the local, CPU-only Hermes into a **functional** state for real local
tasks (storage check/cleanup, Docker self-hosted apps from selfh.st, common ops),
using a tight **eval → plan → dev → eval** loop.

## Current state (2026-11-25)
- Model server: llama.cpp `b12686`, `LFM3-1.7B` Q8_0 + harbor-hermes LoRA, 64k ctx,
  on `127.0.0.1:8080` (alias `lfm3-harbor-hermes`). CPU-only (Intel UHD 620, 8 threads).
- Hermes v0.14.0; Telegram gateway live; STT = local Parakeet pt-BR.
- Throughput ≈ 27 tok/s prompt, ≈ 5 tok/s generation.

### Known problems (what the eval must catch)
1. **Gateway/Telegram prompt was ~20k tokens** → ~15 min/turn ("oi" stuck for hours).
   Cause: telegram platform loaded all 81 tools + full skill index. *Fix applied:
   `platform_toolsets.telegram: [terminal, file, web]`; needs verification.*
2. **No generation cap** → a reasoning model can run to the 64k context (≈ hours).
   Fix: cap `max_tokens` per request/agent.
3. **Backlog**: serial slot processing; many queued messages compound latency.
4. **Tool reliability** for the target tasks (df/cleanup, docker) unproven.

## The eval (`eval/`)
- `prompts.jsonl` — small, text-only (no audio). Tier 1 = fast model sanity
  (arith/ready/pong/cap/sum/json). Tier 2 = tool tasks (disk `df`, selfh.st apps).
- `run_eval.py` — two modes:
  - `--mode model` (default): direct `/v1/chat/completions`, small prompt, `max_tokens`
    capped. Fast inner-loop signal.
  - `--mode agent`: `hermes -z` full agent (system prompt + tools). Acceptance signal.
  - Scores by case-insensitive substring; **timeout or empty = FAIL** (the hours guard).
- Run:
  - `python3 eval/run_eval.py --tier 1 --budget 300`   (fast; budget enforced, default 300s)
  - `python3 eval/run_eval.py --tier 2 --mode agent --timeout 1200 --budget 1200`  (acceptance)
  - `python3 eval/run_eval.py --mode telegram --only oi`  (advisory: measures last real turn;
    bot API can't read the user's reply, so it never auto-passes — confirm in chat)
- Tier-2 `disk` is verified against the real `df -P /` Use%; `selfh` requires the named
  apps to overlap a freshly fetched selfh.st/apps listing (disposable Docker install = dev phase).
- Results saved to `eval/results_<mode>.json`.

## The loop
1. **EVAL** — run tier 1 (model) + tier 2 (agent). Record pass/latency/tokens.
2. **TRIAGE** — classify each FAIL: prompt-size / gen-runaway / tool / model-capability.
3. **PLAN** — pick the highest-leverage fix from the backlog below; write it as a task.
4. **DEV** — implement the smallest change (config, serve.sh, wrapper, or prompt).
5. **EVAL** — re-run; require tier-1 100% and no regression in latency. Iterate.
   Gate to proceed to tier-2 acceptance only when tier-1 is green and fast.

## Backlog (prioritized)
- [x] P1 Minimize telegram toolset (terminal/file/web) — *verify with eval.*
- [ ] P1 Cap generation (`max_tokens`) so no turn can run away for hours.
- [ ] P1 Confirm skills/memory off on the **gateway** path (not just CLI).
- [ ] P2 Reduce/avoid backlog: serialize with a clear "busy" reply or queue cap.
- [ ] P2 Prove tool tasks: `df` cleanup flow + `docker` install from selfh.st.
- [ ] P3 Speed: try Q4_K_M quant (≈2× faster prompt) if accuracy holds.
- [ ] P3 Add 2–3 more tier-2 tasks (listening ports, large files, service health).

## Acceptance — "functional" (hardened per Codex)
- Tier 1 (model): 6/6 PASS, score **final content only**, ordinary cases must NOT
  end `finish_reason=length`, each within a per-case budget (< 5 min).
- Telegram probe: a fresh-session message returns a reply in < 5 min **and** the
  effective prompt stays under budget (assert prompt tokens, not just latency).
- Tier 2 (agent): `disk` output matches the real root `Use%` from a successful `df`
  (not a bare `%`); `selfh` names apps from a **fetched** listing; then one pinned
  Docker app installs in a disposable env with a health check.
- A real Telegram "oi" returns a reply in < 5 min.

## Iteration log
- **Iter 0 — baseline (model, tier1):** 4/7 (arith/ready/pong over-budget with empty tokens
  = gateway slot contention at start; later cases fast). Triage: contention, not capability.
- **Iter 1 — dev: eval isolation (`--pause-gateway`, fault-tolerant).** Re-eval tier1: **7/7 PASS**
  (7–54 s), gateway auto-restarted. Signal now clean.
- **Iter 2 — tier2 agent (cli toolset = terminal+web):**
  - `disk`: **PASS** (256 s) — agent ran `df -h /`, reported root Use% 94%, verified == real.
  - `selfh`: **FAIL** (over-budget 1200 s). Not a hang — server log shows many model
    rounds (7.7k-token prompts, ~4 min each); multi-round web task too slow on CPU.
- **Iter 3 — dev: Q4_K_M speed.** `disk` PASS **99 s** (was 256 s on Q8 → ~2.5×). `selfh`
  still over-budget (multi-round web on dynamic page). Context 16k attempt reverted (Hermes
  requires ≥64k).
- **Iter 4 — dev: research-backed tuning + selfh static catalog.** serve.sh: `-ctk q8_0 -ctv q8_0`
  (KV quant), `-tb 8`, `--no-reasoning-preserve`, `--cache-reuse 256`, `--spec-type ngram-simple`.
  selfh task repointed to `https://r2.selfh.st/directory/software.json` (static, no-auth catalog,
  ~1,700 apps); verify_selfh parses real app names. **Result: disk PASS 103 s, selfh PASS 646 s
  (single-round terminal curl; multi-round web was too slow).** Tier-2 green.
- **Iter 5 — BLAS build + httpd task + Codex hardening.** Built llama.cpp b12686 with
  system OpenBLAS (`~/models/llama-build/build/bin`), swapped into serve.sh. **BLAS gave no
  meaningful pp gain** (~20 tok/s vs ~25; helps only at batch>32). Added `httpd` tier-2 task
  (docker nginx on 127.0.0.1:8099, verify container+curl) → **PASS** (container Up, HTTP 200).
  Codex (gpt-6.2-sol) hardened eval (httpd run-owned container, no-redirect probe) and flags
  (dropped no-ops `-tb`/`--cache-reuse`/`--spec-draft-n-max`). tier1 7/7, tier2 disk+httpd green.
- **Research (5 subagents → research/*.md):** llama CPU tuning, small-model agents, selfh sources,
  Hermes prompt-time, llama pp/spec. Key: quant weights & BLAS aren't the CPU lever at 1.7B;
  prompt length / tool count / rounds / caching are; `--cache-reuse` is a no-op on LFM3's hybrid
  layers; context reduction blocked by Hermes' 64k guard (override ignored on this build).

## Codex consultation (gpt-6.2-sol, low, read-only) — folded in
Verdict: useful smoke test, insufficient acceptance test. Blockers adopted:
1. **Test the Telegram path**, not just `hermes -z`/model — add a telegram probe that
   records queue wait + effective prompt tokens + reply latency; assert the prompt budget.
2. **Verify generation stops** — score final content only; fail ordinary cases on
   `finish_reason=length`; cap generation on the gateway; bound turn time + tool rounds.
3. **Require tool-success evidence** — match `df` Use%; verify a fetched Docker listing,
   then a health-checked pinned install; cleanup must preserve files until approval.
4. **Gates enforce their own criteria** — exact/parsed assertions, reject empty selection,
   per-case latency budgets, re-run the affected layer after each change.
5. Keep per-request token counts + per-turn totals across tool rounds (where time went).

## Loop (unchanged, with Codex gates)
EVAL (tier1 model + telegram probe + tier2 agent) → TRIAGE (prompt-size / runaway /
tool / capability) → PLAN (top backlog item) → DEV (smallest change) → EVAL (tier1 100%
& no latency regression before tier2). Full loop doc above.