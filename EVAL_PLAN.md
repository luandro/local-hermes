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
- **Iter 12 — LoRA merge A/B: MERGED model ADOPTED (2026-11-27, THIS subagent).** Tested the
  Gemini audit's claim that runtime `--lora` contaminates prefix/KV cache and skips fast kernels.
  Merged the PEFT LoRA (`harness-hub/LFM3-1.7B-hermes`) into base `LiquidAI/LFM3-1.7B` in BF16
  (`merge_and_unload`), converted to BF16 GGUF, built an imatrix (`llama-imatrix`, calib =
  terminal/JSON/pt+en), and quantized to Q4_K_M (1.10 GB). Same build, same eval
  (`--mode agent --only disk,selfh --budget 600 --timeout 600`), gateway stopped, arms sequential.
  - **runtime --lora (baseline):** disk PASS 217.8 s, selfh PASS 41.8 s, 1st-turn pp **18.84 tok/s**,
    **136** `CPU_REPACK fallback` warnings, 2/2.
  - **merged (no --lora):** disk **PASS 136.9 s**, selfh **PASS 15.9 s**, 1st-turn pp **41.03 tok/s**,
    **0** fallback warnings, 2/2.
  - **Cache reuse equal:** both arms clean LCP reuse (f_sim 0.97–1.00), turn1 3,115 tok cold,
    later 39–115 tok, no `forcing full re-processing`. **ADOPTED** — equal PASS, ~2.2× faster
    prefill, faster wall, cache unchanged. serve.sh prefers the merged GGUF, drops `--lora`
    (fallback to base+LoRA if merged absent). Full table in LEARNINGS.md "LoRA merge A/B".
- **Iter 11 — disk-eval flake root cause + fix (2026-11-27, THIS subagent).** The `disk` eval
  intermittently ballooned to 12–20k tokens over multiple rounds and FAILED at 600 s, while warm
  single-round runs PASS in ~17–48 s. **Root cause: the `disk` prompt was open-ended** ("Check disk
  usage… run df -h /… report Use%"); a 1.7B reasoning model sometimes loops across rounds, and with
  `agent.max_turns: 8` + `compression.threshold: 0.5` (=32k, never fires at 12–20k) nothing bounded
  the accumulation. `selfh` was already reliable because its prompt forces a single round.
  - **Baseline evidence:** 13 warm `disk` runs all PASS at exactly 2 turns (turn1 = 4 tok warm /
    3,115 cold, turn2 = 80 tok), 16.9–48.7 s. Balloon is a multi-round tail event (didn't reproduce
    warm); the one cold FAIL was gateway slot contention (auto-restart), not a balloon.
  - **Fix (minimal, = selfh pattern):** reword `disk` prompt in `eval/prompts.jsonl` to *"Call the
    terminal tool immediately and run exactly this command once. Report the Use% value for the root
    filesystem and stop.\n\ndf -h /"*. No config/serve.sh change. Single source of truth = prompts.jsonl.
  - **Verify:** `disk` **PASS 3/3** — 172.5 s (cold, 3,123-tok turn1), 30.1 s, 24.5 s; all 2 turns,
    turn2 = 80 tok, bounded. `selfh` re-check **PASS 41.3 s** (no regression). **KEPT.**
    Backup: `~/.hermes/config.yaml.bak.diskfix-20261127_071323`.
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
- **Iter 8 — prompt-size minimization (2026-11-27, THIS subagent).** The pi benchmark proved
  prompt SIZE is the dominant CPU lever. Measured with `hermes prompt-size` + the eval's
  first-turn `n_tokens` from `serve.log`. Baseline (plugins=[omh,herdr-agent-state,orca-status,
  portfolio-manager], cli=[connections,terminal,web]): first-turn prompt **15,815 tok**, CLI tool
  schemas 8,922 B (6 tools). A/B (one change class at a time):
  - **A. `plugins.enabled: []`** → system 7,748→6,046 B, tool schemas 8,922→6,848 B.
  - **B. `platform_toolsets.cli: [terminal]`** (drop connections+web; eval needs only terminal)
    → CLI tool schemas **4,612 B (4 tools)**, first-turn prompt **4,175 tok (-74%)**.
  - **Result (warm cache): disk PASS 39 s (was 58), selfh PASS 35 s (was 41), 2/2.**
    Cold-cache first run disk 238 s (4,175-tok prefill at ~20 tok/s, no cache reuse) — a
    one-time artifact, not a regression. **KEPT.** omh's 3-tool/2.2 KB block left in place
    (user relies on omh; small vs the 74% win). Repro: `config/lean-config.yaml`, applied by
    `setup.sh`. Backup: `~/.hermes/config.yaml.bak.promptshrink-20261127_032953`.
- **Iter 9 — spec-decode OFF A/B (2026-11-27, THIS subagent).** Tested the Gemini audit's claim that
  n-gram spec should be OFF on this CPU (pp_batch/tg only 1.5–2.5×). Same build, same eval
  (`--mode agent --only disk,selfh --budget 600 --timeout 600`), gateway stopped, arms sequential.
  - **spec ON** (ngram-simple n8 m6 hits2): disk **199.8 s**, selfh **34.5 s**, 2/2; pp 20.99 tok/s,
    tg 9.87–12.45 tok/s.
  - **spec OFF** (line removed): disk **181.1 s**, selfh **39.1 s**, 2/2; pp 20.05 tok/s,
    tg 9.46–12.42 tok/s.
  - **KEPT spec OFF.** Faster on the substantial task (disk −19 s), equal-within-noise on the trivial
    one; pp/tg identical within noise. Audit confirmed (neutral-to-better on a 1.7B). Full table in
    LEARNINGS.md "Spec-decode A/B".
- **Iter 10 — load-mode mmap vs none / THP A/B (2026-11-27, THIS subagent).** Tested the Gemini audit's
  "THP vs mmap" claim (file mmap → 4 KiB pages → ~30% pp loss; `none` → anon/THP fixes it). Same build,
  same eval (`--mode agent --only disk,selfh --budget 600 --timeout 600`), gateway stopped, arms sequential.
  THP state `[madvise]`. **mmap:** disk FAIL 600 s (12,209-tok prompt), selfh PASS 395.6 s, pp 22.85 tok/s
  (1st turn), AnonHugePages 0. **none:** disk FAIL 600 s, selfh FAIL 600 s (0/2), pp ~21–25 tok/s,
  AnonHugePages **0 kB** throughout (THP never engaged — no madvise in llama.cpp). **REVERTED to mmap.**
  `none` is not faster (pp identical within noise), THP inert here, and adds swap-eviction risk for no gain.
  Full table in LEARNINGS.md "Load-mode A/B".
- **Iter 11 — native build A/B: GGML_NATIVE + BLAS=OFF (2026-11-27, THIS subagent).** Tested the Gemini
  audit's "~30% pp" claim for a native build with the embedded llamafile SGEMM vs the OpenBLAS build.
  Built a second binary in `build-native` (original `build/` intact), `-DGGML_NATIVE=ON -DGGML_BLAS=OFF`.
  Verified march in `ggml-cpu.dir/flags.make`: native → `-march=native` (baseline → `-march=x86-64`; the
  x86-64 in CMakeConfigureLog.yaml is a detection artifact, not a build flag). 3 paired
  `llama-bench -p 2048 -n 128 -t 4 -ngl 0` runs/arm. **Median pp 51.84 (native) vs 43.54 (BLAS) = +19.1%,
  tg 17.04 vs 16.65 = +2.3%; native won 3/3 on both. ADOPTED** — `setup.sh` `build_llama()` now uses
  `-DGGML_NATIVE=ON -DGGML_BLAS=OFF` (Metal kept on macOS). `serve.sh` left on `build/` (live binary
  unchanged). Full table in LEARNINGS.md "Native build A/B".
- **Iter 12 — thread/batch sweep: current `-t 4 -tb 8 -b 2048 -ub 512` VALIDATED, KEPT (2026-11-27, THIS
  subagent).** `llama-bench` sweep of `-t`/`-b`/`-ub` (native binary, merged Q4_K_M, `-ngl 0`). Host was
  swap-thrashing (other apps, not killable) so absolute numbers are contention-depressed (baseline
  unreproducible: pp 26→16.6 vs clean 51.8) — only within-run A/B directions are trustworthy. Results:
  `-t` t8 pathological (tg 0.18), t4 best tg / t6 +11% pp −18% tg → keep t4; `-b` b1024 looked better at
  p512 but **reversed at real p2048** (b2048 +20% pp / +62% tg) → keep b2048; `-ub` ub512 best both → keep
  ub512. `-tb` is server-only, not benchable. **No serve.sh change; re-run on an idle host for clean
  numbers.** Full table in LEARNINGS.md "Thread/batch sweep".

## Iter 13 — ik_llama.cpp backend eval: DEAD-END for Q4_K_M, KEEP mainline (2026-11-27, THIS subagent)
Gemini audit's PRINCIPAL prefill candidate (`ik_llama.cpp`, PR #2782 LFM3 + #2472 AVX2 Q4_0_R8 GEMM;
community i5-8500T +97% pp / +19% tg). Built commit c9643ae in `/home/luandro/models/ik-build`
(Release, GGML_NATIVE=ON, IQK GEMM+FA). **Loads LFM3** (arch `lfm3`, merged Q4_K_M). A/B identical
config (`-p 2048 -n 128 -t 4 -ngl 0`, 2×, median): **pp 54.2 vs mainline 52.9 (+2.5%, noise);
tg 13.9 vs 16.1 (−14% regression).** No prefill jump because the +97% is **Q4_0/QAT-specific** (ik's
Q4_0_R8 repack path); our **Q4_K_M** doesn't engage it. Flags: has `--jinja`/`--reasoning-budget`/
`--ctx-checkpoints`/`-fa`/`-cram`/`--lora`; **missing `--cache-prompt`, `--load-mode`,
`--checkpoint-min-step`** (all used by serve.sh). **Verdict: KEEP mainline** — fails the ≥15% pp bar,
regresses decode, and would need serve.sh edits anyway. ik-build kept outside repo for a future Q4_0
trial; mainline builds untouched. Full table in LEARNINGS.md "ik_llama.cpp evaluation".

## Iter 14 — Q4_0 vs Q4_K_M A/B: Q4_0 fidelity-SAFE but NOT faster, KEEP Q4_K_M (2026-11-27, THIS subagent)
Highest-value remaining lever: Q4_0 CPU_REPACK (mainline `Q4_0_4_4`) + ik_llama `Q4_0_R8` AVX2 fast
path (community 8th-gen +97% pp, Q4_0-only). Re-merged BF16 (base+adapter re-downloaded from HF,
peak ~5.8 GiB avail, no OOM), converted bf16 (3.3 G), quantized Q4_0 with existing imatrix
(995.94 MiB, 4.83 BPW). Bench `-p 2048 -n 128 -t 4 -ngl 0`, 2× each, median (host swap-thrashed —
absolute numbers contention-depressed, only directions trustworthy):
- **mainline Q4_K_M:** pp 42.78, tg 15.77. **mainline Q4_0:** pp 40.95 (−4%), tg 15.85 (tie) → not faster.
- **ik Q4_K_M:** pp 48.21, tg 11.61. **ik Q4_0:** pp 50.75 (+5.3%, high variance 56.8/44.7, NOT +97%),
  tg 13.67 (below mainline). R8 path engages (ik Q4_0 > mainline Q4_0 pp) but marginal + noisy + decode penalty.
- **FIDELITY GATE PASS:** Q4_0 eval `--mode agent --only disk,selfh --budget 600` (gateway stopped,
  clean merged Q4_0, `lora_args=[none]`, 0 CPU_REPACK warns): **disk PASS 120.1 s (df-eq 89),
  selfh PASS 16.6 s (3 apps) — 2/2.** Q4_0 does not hurt tool-arg/JSON accuracy.
- **Verdict: KEEP Q4_K_M** — adopt Q4_0 only if faster AND fidelity-ok; it is fidelity-ok but not
  faster, so no reason to trade Q4_K_M's Q6_K JSON protection. Q4_0 GGUF removed; ~10 GB BF16
  intermediates cleaned; repro artifacts kept. serve.sh unchanged; ik-build kept for future Q4_0/QAD.
  Full table in LEARNINGS.md "Q4_0 vs Q4_K_M A/B".

## Iter 15 — round-gating (first-hop tool_choice:required): NOT VIABLE, REVERTED (2026-11-27, THIS subagent)
Last lever: force exactly ONE tool call on hop 0 then answer, to cut rounds for ANY task.
- **Native support:** Hermes v0.23.4 OpenAI-wire path still never sets `tool_choice` (#741 persists);
  `tool_use_enforcement` is prompt-only; transports only emit `"auto"`. **llama-server DOES honour
  `tool_choice` in the JSON body** (`common_chat_tool_choice_parse_oaicompat`), no CLI flag. → proxy
  is the only way. Built `eval/tool_choice_proxy.py` (stateless first-hop detect, gate/passthrough).
- **Multi-round A/B** ("largest file under ~/models, report size" = list→stat→answer): baseline
  **518.7 s / 4 requests**, gated **232.3 s / 5 requests**, both correct. **Gating added a round,
  didn't cut one** — the task needs 2 real tool calls regardless; the wall gap is cache warmth
  (baseline cold 6,144-tok prefills vs gated warm 512-tok), not gating.
- **Regression (disk+selfh, gate ON):** host swap-thrashed (20.5/20.5 GB, tg 0.1–0.2 t/s, other
  apps), selfh crawled → stopped at budget. disk re-armed correctly; no gating failure seen but no
  clean PASS. Moot — adopt gate (fewer rounds) already failed.
- **Verdict: NOT ADOPTED / REVERTED.** Fails the reduce-rounds criterion, adds a failure mode
  (required first call breaks prose-first tasks), speedup is a confound. Config restored (identical
  to backup `~/.hermes/config.yaml.bak.roundgate-1795804833`), proxy stopped, server healthy direct.
  Proxy kept in `eval/` as opt-in, not wired into serve.sh. Full table in LEARNINGS.md.

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
## E00 — instrumentation + honest eval (2026-11-27)
Plan v2 (Codex-debated) ranks this #1: no further A/B is trustworthy without it. Done:
- **Agent-mode token accounting** via `hermes --usage-file` (input/output/cache/reasoning/total/api_calls).
  The `--max-prompt-tokens` guard now fires for agent runs (was dead — pt was always None).
- **Per-request memory-pressure trace** (`eval/memtrace.py`): PSI memory+io, swap-in/out, major faults,
  MemAvailable, llama RSS — before/after each case, null-safe. New `swapin` column + `MEMPRESSURE` line.
- **Validated under the swap wall:** 1 trivial case → swapin=278, majflt=6475, avail_min=3.9GB,
  75s latency. Confirms the trace captures the paging that dominates wall-time on this host.
- **Blocker (host, not code):** swap 19G/19G full, ~4GiB free (non-killable apps). Evals time out on
  latency. Re-run for clean absolutes on an idle host / after zswap reboot. Instrumentation is valid now.
- **Next (gated on a clean host):** W1 typed storage workflow + pre-LLM rules route; R1 rules/BM25
  baseline; E10 Laya fine-tune only if E00 traces prove an ambiguous slice rules can't fill.

## W1 — storage-status pre-LLM plugin (2026-11-27)
- Deterministic `pre_llm_call` route: unambiguous read-only disk questions answered with a
  verified `df`+`du` scan, zero model calls (block before inference). Partial-intent guard:
  any write/conditional/ambiguous intent falls through to Hermes. Unit tests ALL PASS.
- Plugin enabled in live config (backup taken); e2e `hermes -z` blocked by host swap wall
  (swap 19G/19G full). Re-run on idle host: readonly → api_calls=0; "clean it up" → api_calls>0.
- Next: R1 rules/BM25 baseline; E10 Laya only if E00 traces prove an ambiguous slice rules miss.

## W1.2 — senior-review fixes (codex gpt-6.2-sol, SHIP after 6 rounds)
- Rewrote the partial-intent classifier to a strict whole-request allowlist (anchored fullmatch
  + (family,regex) tuples) after codex found the substring-admission blocker. Per-probe dispatch
  (df/free/uptime/docker only as requested) + output validation (df 6-fields+mount '/', memory
  exact Mem: all size-like, docker SIZE column, uptime 3 numeric loads). All codex blocker +
  adversarial cases are regression tests -> None. Full suite ALL PASS. Live e2e pending reboot.
- Process note: always consult codex (gpt-6.2-sol) for plan+review this session; two seniors only
  on complex/high-risk tasks (per user). seniors skill updated.
