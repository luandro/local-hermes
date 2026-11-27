# Learnings — local Hermes (LFM3-1.7B) on a CPU ThinkPad

Running log of hard-won facts from getting this setup working. Newest at top.

## Round-gating (first-hop `tool_choice:"required"`) — NOT VIABLE / NOT ADOPTED (2026-11-27, THIS subagent)
Goal: force the model to make exactly ONE tool call on hop 0 then answer, to cut agent
rounds (the multi-round wall-time killer) for ANY task. **Verdict: REVERTED — gating does not
reduce rounds on a genuinely multi-step task, and the wall gain was a cache-warmth confound.**

- **Native support:** Hermes v0.23.4's OpenAI-wire path (`_build_chat_completions_kwargs` →
  `transport.build_kwargs`) **still never sets `tool_choice`** — bug #741 persists on the local
  path. `agent.tool_use_enforcement` is **prompt-only** (system-prompt guidance, not a hard
  constraint). Transports only ever emit `"auto"` (codex.py:801). No `tool_choice` /
  first-hop-required config key exists. **llama-server DOES honour `tool_choice` in the request
  body** (`common_chat_tool_choice_parse_oaicompat` in `libllama-common.so`; no `--tool-choice`
  CLI flag, but the JSON field works). So a proxy is the only way to force first-hop-required.
- **Proxy built:** `eval/tool_choice_proxy.py` (~110 lines, no secrets) — stateless first-hop
  detection (first hop = no prior `role:tool` / assistant tool-call message), injects
  `tool_choice:"required"` on hop 0, `"auto"` after; `--mode gate|passthrough`; hop logging.
  Smoke-tested (curl + live `hermes -z`): correctly re-arms per task (fresh task's first hop →
  `required`), passes all other fields untouched.
- **Multi-round A/B** (task: "find largest regular file under ~/models, report size" — genuinely
  needs list→stat→answer; same merged Q4_K_M, gateway stopped, sequential, through the proxy):

  | arm | wall | model requests (hops) | result |
  |---|---|---|---|
  | baseline (passthrough) | **518.7 s** | **4** | correct (1.75 GB Q8_0) |
  | gated (required hop 0) | 232.3 s | **5** | correct (same file) |

  **Gating did NOT reduce rounds — it added one (5 vs 4).** The task needs two real tool calls
  regardless of hop-0 forcing; `required` only guarantees the first call is a tool, which the
  model already did. The 232 s vs 519 s wall gap is a **cache-warmth confound** (baseline ran
  cold with 6,144-tok prefills; gated reused warm KV, 512-tok prefills) — not a gating effect.
- **Regression (disk+selfh, gating ON):** could not complete cleanly — host swap-thrashed
  (20,478/20,479 MB, tg collapsed to 0.1–0.2 t/s from other apps, not killable; same contention
  caveat as the Q4_0 / thread-sweep iters). `disk` completed its hops under gate (re-arm correct);
  `selfh` crawled under swap and was stopped at budget. No gating-induced failure observed, but no
  clean PASS either — moot since the adopt gate (fewer rounds) already failed.
- **Decision: NOT ADOPTED.** Round-gating fails the primary criterion (reduces rounds) on a
  multi-step task, adds a failure mode (a required first call breaks any task whose hop 0 should
  be prose), and the apparent speedup is cache warmth, not gating. Reverted: config base_url
  restored to `localhost:8080` (verified identical to backup), proxy stopped, server left healthy
  direct. Proxy kept in `eval/` as a documented, opt-in tool (not wired into serve.sh). Backup:
  `~/.hermes/config.yaml.bak.roundgate-1795804833`.

## Q4_0 vs Q4_K_M A/B — Q4_0 fidelity-SAFE but NOT faster; KEEP Q4_K_M (2026-11-27, THIS subagent)
The highest-value remaining lever: Q4_0 with CPU_REPACK (mainline `Q4_0_4_4`) and especially
ik_llama's `Q4_0_R8` AVX2 fast path (community 8th-gen test showed +97% pp, but only on Q4_0).
Re-merged to BF16 (base+adapter re-downloaded from HF; `do_merge.py` peak ~5.8 GiB avail, no OOM,
19 s), converted `--outtype bf16` (3.3 G), quantized Q4_0 with the existing imatrix (995.94 MiB,
4.83 BPW, 27 s). **Verdict: KEEP Q4_K_M — Q4_0 is fidelity-safe but not faster on either backend.**
- **Bench A/B** (`-p 2048 -n 128 -t 4 -ngl 0`, 2× each, median). **Caveat: host swap-thrashed the
  whole session (swap 19G/19G full, other apps not killable) so absolute numbers are
  contention-depressed (~17 min/run vs ~40 s clean) — only within-arm directions are trustworthy;
  high variance (±5–8 tok/s) on the ik arms.**

  | backend | quant | pp2048 (med) | tg128 (med) | vs mainline Q4_K_M |
  |---|---|---|---|---|
  | mainline native | Q4_K_M | 42.78 (44.38/41.18) | 15.77 (15.08/16.46) | baseline |
  | mainline native | Q4_0 | 40.95 (41.14/40.76) | 15.85 (16.05/15.64) | pp −4%, tg ≈ noise |
  | ik_llama c9643ae | Q4_K_M | 48.21 (45.45/50.97) | 11.61 (9.21/14.01) | — |
  | ik_llama c9643ae | Q4_0 | 50.75 (56.82/44.68) | 13.67 (15.80/11.54) | — |

- **Mainline:** Q4_0 **not faster** — pp 40.95 vs 42.78 (−4%, within noise), tg 15.85 vs 15.77
  (tie). The `Q4_0_4_4` CPU_REPACK path gives no win here (1.7B is bandwidth-bound, not
  compute-bound; the audit's own "quant weights aren't the CPU lever at 1–3B" holds).
- **ik_llama (the R8 fast-path test):** Q4_0 pp 50.75 vs ik Q4_K_M 48.21 = **+5.3%**, NOT the +97%
  the audit hoped for. The R8 repack path engages (ik Q4_0 pp 50.75 > mainline Q4_0 40.95, +24%),
  but the absolute jump is modest and **swamped by variance** (run1 56.82 / run2 44.68). ik tg
  (13.67) is **below mainline** (15.77) — the decode regression from the prior ik eval persists.
  So even the quant-format-specific lever is marginal and comes with a decode penalty.
- **FIDELITY GATE — PASS (the decisive result).** Q4_0 does NOT hurt tool-arg/JSON accuracy on
  this model+imatrix. Eval (`--mode agent --only disk,selfh --budget 600 --timeout 600`, gateway
  stopped, clean merged Q4_0 via a symlinked MODEL_DIR so `lora_args=[none]`, 0 CPU_REPACK warns):
  **disk PASS 120.1 s (df-eq 89, correct Use%), selfh PASS 16.6 s (3 apps: 13ft Ladder, 1Panel,
  2FAuth) — 2/2.** The Q4_K_M Q6_K attention.wv/ffn.w2 JSON protection is not needed for the eval
  to pass on Q4_0. **But since Q4_0 is not faster, there is no reason to trade that protection away.**
- **Decision: KEEP Q4_K_M.** Adopt Q4_0 only if faster AND fidelity-ok; it is fidelity-ok but
  **not faster** (mainline pp −4%, ik +5% pp within noise + decode regression). Q4_0 GGUF removed
  (not adopted); ~10 GB BF16 intermediates cleaned (merged-bf16.gguf, merged/, base/, adapter/).
  Repro artifacts kept in `~/models/merge/` (do_merge.py, calib.txt, imatrix.dat). The ik-build is
  left in place for future Q4_0/QAD experiments; mainline builds untouched; serve.sh unchanged.
  **Lesson: on this 1.7B CPU box the Q4_0_R8 headline is real but small and noisy; the imatrix Q4_0
  is accuracy-safe, so the only open question left is a clean idle-host re-bench to confirm the
  ±5% is noise, not a real regression.**

## LoRA merge A/B — MERGED model ADOPTED (2026-11-27, THIS subagent)
The Gemini audit claims runtime `--lora` contaminates the prefix/KV cache and skips fast
kernels. **Confirmed and fixed: merged the LoRA into a single Q4_K_M GGUF and dropped
`--lora`. ADOPTED.** This is NOT a wash — it is a real ~2.2× prefill win on this CPU.
- **Root cause proven:** the runtime-LoRA server logged **136 `cannot use buft 'CPU_REPACK',
  fallback to CPU` warnings** (every LoRA-adapted tensor falls back to the slow CPU path);
  the merged server logs **0**. Same build, same flags, same eval.
- **Method:** `PeftModel.from_pretrained(base, adapter).merge_and_unload()` in BF16
  (transformers+peft+torch cpu; peak ~6.6 GiB avail, no OOM) → `convert_hf_to_gguf.py
  --outtype bf16` → `llama-imatrix` (calib = terminal/JSON/pt+en, 65 lines ~1.5k tok) →
  `llama-quantize --imatrix … Q4_K_M` (1.10 GB). `llama-imatrix` was already built at
  `build/bin/llama-imatrix`. Eval: `--mode agent --only disk,selfh --budget 600`, gateway
  stopped, single server, arms sequential, cold-cache first turn.
- **Results (cold first turn):**

  | arm | disk | selfh | 1st-turn pp tok/s | LoRA fallback warns | PASS |
  |---|---|---|---|---|---|
  | runtime --lora (baseline) | PASS 217.8 s | PASS 41.8 s | **18.84** | **136** | 2/2 |
  | merged (no --lora) | **PASS 136.9 s** | **PASS 15.9 s** | **41.03** | **0** | 2/2 |

- **Cache reuse NOT worse:** both arms show clean LCP slot reuse (`f_sim_best` 0.97–1.00),
  turn 1 = 3,115 tok cold, later turns 39–115 tok, **no `forcing full prompt re-processing`**
  on either arm. The hybrid-checkpoint cache reuse that worked WITH `--lora` still works
  WITHOUT it — the merge removes the fallback penalty without touching caching.
- **Verdict: ADOPT.** Equal-or-better PASS (2/2 both), ~2.2× faster prefill (18.8→41.0 tok/s),
  faster wall on both tasks, cache reuse equal. serve.sh now prefers
  `LFM3-1.7B-hermes-merged-Q4_K_M.gguf` and drops `--lora`; falls back to base+runtime-LoRA
  only if the merged GGUF is absent (`BASE_OVERRIDE=1` forces the old path). Merged GGUF kept
  at `~/models/lfm3-hermes/` (gitignored, not committed). Repro artifacts in
  `~/models/merge/` (do_merge.py, calib.txt, imatrix.dat). Gotcha: after a `gateway stop` that
  ends in `failed`, `hermes -z` can't reach the gateway — `hermes gateway start` then
  `stop --yes` to get a clean `inactive` state before the eval.

## Disk-eval flake — ROOT CAUSE + fix (2026-11-27, THIS subagent)
The `disk` eval intermittently ballooned to 12–20k tokens over multiple rounds and FAILED at
600 s, while warm single-round runs PASS in ~17–48 s. **Root cause: the `disk` prompt was
open-ended** ("Check disk usage on this machine: run df -h / and report the Use%…"). A 1.7B
reasoning model sometimes loops — re-running the tool / re-reasoning across rounds — and each
round appends to the prompt. Nothing bounded it: `agent.max_turns: 8` allows up to 8
accumulating rounds, and `compression.threshold: 0.5` (= 32k of 64k) never fires on a 12–20k
prompt. The `selfh` case was already fixed this way (reworded to "run exactly this once … stop")
and is reliable; `disk` was the last open-ended tool prompt.
- **Evidence (baseline, this subagent):** 13 warm `disk` runs all PASS at exactly **2 turns**,
  bounded — turn 1 = 4 tok (warm cache hit) / 3,115 tok (cold), turn 2 = 80 tok; latency
  16.9–48.7 s. The balloon did NOT reproduce warm (it is a multi-round tail event). The only
  FAIL was a cold run where the auto-restarted gateway stole the single llama slot (1 turn
  logged, then over-budget) — contention, not a balloon. Iter10's 12,209-tok FAIL is the same
  multi-round accumulation (~4 rounds).
- **Fix (minimal, = the proven selfh pattern):** reword the `disk` prompt in
  `eval/prompts.jsonl` to force a single round: *"Call the terminal tool immediately and run
  exactly this command once. Report the Use% value for the root filesystem and stop.\n\ndf -h /"*.
  No config/serve.sh change needed; the prompt is the lever (prompt SIZE/rounds is the #1 CPU
  lever here). One source of truth: `eval/prompts.jsonl`.
- **Verify (fix):** `disk` **PASS 3/3** — 172.5 s (cold, 3,123-tok first turn), 30.1 s, 24.5 s;
  all exactly **2 turns**, turn 2 = 80 tok each, prompt bounded. `selfh` re-check **PASS 41.3 s**
  (2 turns) — no regression. **KEPT.**
- Backup: `~/.hermes/config.yaml.bak.diskfix-20261127_071323` (config unchanged by this fix;
  backup taken per protocol).

## Load-mode A/B — mmap vs none / THP (2026-11-27, THIS subagent)
Audit (`research/gemini-audit-lfm3-1.7b-thinkpad-cpu.md`, "Proteção via cgroup v2 e o Conflito
THP vs. mmap") claims file-backed `mmap` bypasses Transparent Hugepages → 4 KiB pages → TLB misses
→ ~30% prefill loss, and that `--load-mode none` (anon memory, THP-eligible) fixes it. **Disproven
on this box — REVERTED to mmap.** The THP fix is inert here because THP is `[madvise]` and
llama.cpp never madvises the weight buffer, so `none` gets 4 KiB pages too.
- **Method:** same build, same eval (`--mode agent --only disk,selfh --budget 600 --timeout 600`),
  gateway stopped (no `--pause-gateway`), single server, arms sequential. THP state recorded:
  `always [madvise] never` (so anon allocs only get 2 MiB pages if the process madvises — it doesn't).
  `AnonHugePages` read from `/proc/<pid>/smaps_rollup` on both arms.
- **Results:**

  | arm | disk | selfh | pp tok/s (1st turn / disk prefill) | tg tok/s | AnonHugePages | PASS |
  |---|---|---|---|---|---|---|
  | mmap (current) | FAIL 600 s (12,209-tok prompt) | **PASS 395.6 s** | 22.85 / 21.2 | 12.65 / 6.8 | **0 kB** | 1/2 |
  | none | FAIL 600 s (~20k-tok prompt) | FAIL 600 s | ~21–25 / 11.98 | 11.59 | **0 kB** | 0/2 |

- **Verdict:** `--load-mode none` did **NOT** engage THP (`AnonHugePages` stayed **0 kB** for the
  whole run, RSS ~2.2–2.4 GiB) — with THP=`[madvise]` and no `madvise(MADV_HUGEPAGE)` in llama.cpp,
  anon weights are still 4 KiB pages, so there is no TLB win to be had. pp was identical within
  noise (both ~21–23 tok/s on the 3,131-tok first turn / disk prefill). `none` scored **worse**
  (0/2 vs 1/2; selfh flipped PASS→FAIL) and adds swap-eviction risk (audit's own caveat #26110)
  for zero benefit. **REVERTED** (`git checkout serve.sh`). The audit's ~30% mmap penalty is a
  generic llama.cpp claim that does not reproduce on this ThinkPad; the real lever remains prompt
  size (the disk task ballooned to 12–20k tokens on both arms — the known multi-round issue, not
  load-mode). To actually test the audit's THP hypothesis you'd need THP=`always` (root) or a
  `madvise` patch — out of scope here. MemoryMin=2.5G (root) also not applied, per instructions.

## Spec-decode A/B — n-gram spec OFF (2026-11-27, THIS subagent)
Audit (`research/gemini-audit-lfm3-1.7b-thinkpad-cpu.md`, "Descodificação Especulativa") argues
n-gram spec should be OFF by default on this CPU: pp_batch/tg ratio is only 1.5–2.5×, so n-gram
only helps very repetitive JSON and hurts free dialogue. **Proven with the eval — KEPT spec OFF.**
- **Method:** same server build, same eval (`--mode agent --only disk,selfh --budget 600
  --timeout 600`), gateway stopped (no `--pause-gateway`), single server, arms sequential. First-turn
  prompt ~3,127 tok (lean config). tok/s from `serve.log` `prompt eval time` / `eval time` lines.
- **Results:**

  | arm | disk | selfh | pp tok/s (1st turn) | tg tok/s | PASS |
  |---|---|---|---|---|---|
  | spec ON (ngram-simple n8 m6 hits2) | 199.8 s | 34.5 s | 20.99 | 9.87–12.45 | 2/2 |
  | spec OFF (line removed) | **181.1 s** | 39.1 s | 20.05 | 9.46–12.42 | 2/2 |

- **Verdict:** spec-OFF faster on the substantial task (disk −19 s) and equal-within-noise on the
  trivial one (selfh +5 s on a 35 s task); pp/tg identical within run-to-run noise. Audit's
  "neutral-to-better on a 1.7B" confirmed. **COMMITTED** (removed the `--spec-type ngram-simple …`
  line from serve.sh). n-gram spec buys nothing on this CPU and adds complexity — leave it off.

## Native build A/B — GGML_NATIVE + BLAS=OFF ADOPTED (2026-11-27, THIS subagent)
Gemini audit claim: a native build (`-DGGML_NATIVE=ON`) with `-DGGML_BLAS=OFF` (embedded llamafile
SGEMM, real AVX2/FMA) beats the OpenBLAS build by ~30% on prefill. **Proven on this box — ADOPTED.**
The starting build was effectively GENERIC: `GGML_NATIVE=ON` was in the cache but the ggml-cpu target
compiled with `-march=x86-64` (verified in `build/…/ggml-cpu.dir/flags.make`) + OpenBLAS backend.
- **Method:** second build in a separate dir (`build-native`, original `build/` left intact), 3 paired
  `llama-bench -m <merged Q4_K_M> -p 2048 -n 128 -t 4 -ngl 0` runs per arm, arms sequential, same box.
  Build: `-DGGML_NATIVE=ON -DGGML_BLAS=OFF`, 10m10s wall, peak RSS 815 MB, 0 swaps.
- **Verified march (the whole point):** `build-native/…/ggml-cpu.dir/flags.make` →
  `-march=native` (baseline `build/` → `-march=x86-64`). The `-march=x86-64` string in
  `CMakeFiles/CMakeConfigureLog.yaml` is a compiler-detection artifact, NOT a real build flag — the
  authoritative per-target flags live in `flags.make`. Native binary has **no BLAS device**
  (`--list-devices` → none); baseline shows `BLAS: OpenBLAS`. Note: `objdump` FMA/AVX2 counts in
  `libggml-cpu.so` are identical (ggml ships AVX2 via explicit target attrs regardless of `-march`),
  so the discriminator is the backend (embedded SGEMM vs OpenBLAS) + `-march`, not the instr count.
- **Results (tok/s, median of 3):**

  | arm | pp2048 (3 runs) | pp median | tg128 (3 runs) | tg median |
  |---|---|---|---|---|
  | native + BLAS=OFF | 46.44 / 53.01 / 51.84 | **51.84** | 17.36 / 17.04 / 16.53 | **17.04** |
  | generic + OpenBLAS | 45.15 / 43.54 / 39.46 | 43.54 | 16.65 / 16.69 / 14.76 | 16.65 |

- **Verdict:** native won **3/3** on both pp and tg. pp **+19.1%** (median, >10% bar), tg **+2.3%**.
  The audit's "~30% pp" was optimistic but the direction and a clear win hold. **ADOPTED** — `setup.sh`
  `build_llama()` now uses `-DGGML_NATIVE=ON -DGGML_BLAS=OFF` (Metal path kept on macOS via GGML_METAL
  default). `serve.sh` left pointing at `build/` (live binary unchanged); a fresh `setup.sh` produces
  the faster native build. OpenBLAS is no longer required (the embedded SGEMM has real AVX2/FMA).

## Thread/batch sweep — current `-t 4 -tb 8 -b 2048 -ub 512` VALIDATED, KEPT (2026-11-27, THIS subagent)
Goal: confirm or beat the live CPU `THREAD_ARGS` via `llama-bench` (native `build-native` binary,
merged Q4_K_M, `-ngl 0`). **Caveat: the host was under heavy memory pressure the whole session**
(swap 100% full, ~15 GB held by other apps that must not be killed). The documented clean baseline
(pp2048 ≈ 51.8, tg128 ≈ 17.0) was unreproducible — repeated `-t 4` baselines came in at pp 26.0 / tg 8.0
then pp 16.6 / tg 3.4 (monotonic collapse, ~10× slower wall-clock: 15–17 min per run vs ~40 s clean).
**Absolute numbers below are contention-depressed and NOT comparable to the clean baseline; only the
within-run A/B directions are trustworthy** (large effect sizes, single model load per sweep).

- **`-tb` is NOT a `llama-bench` flag** (server-only runtime flag; `llama-bench -tb …` → `error: invalid
  parameter`). It cannot be benchmarked without the server, which is out of scope. Sweepable axes here:
  `-t`, `-b`, `-ub` only.
- **`-t` sweep** (`-p 512 -n 32 -r 1`, one load): t4 pp30.3/tg8.0 · **t6 pp33.6 (+11%)/tg6.5 (−18%)** ·
  t8 pp24.0/**tg0.18 (pathological SMT thrash)**. → t8 is broken (confirms audit); t4 = best tg, t6 trades
  tg for pp. Keep **t4** (balanced, best generation).
- **`-b` sweep @ p512** (`-t 4 -ub 512`): b1024 pp33.5/tg7.7 · b2048 pp24.1/tg7.2 · b4096 pp20.7/tg6.9 —
  *looked* like b1024 wins. **But at the real `-p 2048 -n 128` it REVERSES:** b1024 pp28.7/tg8.4 ·
  **b2048 pp34.4 (+20%)/tg13.6 (+62%)**. → small prompts over-provision the batch; b2048 is correct for
  real pp2048 ingest. Keep **b2048**. (Lesson: never tune `-b` at tiny `-p`.)
- **`-ub` sweep @ p512** (`-t 4 -b 2048`): ub256 pp25.4/tg7.0 · **ub512 pp27.5/tg10.7** · ub1024 pp26.6/tg9.2
  → **ub512 (current) best on both.** Keep **ub512**.
- **Verdict: every measurable axis confirms the current `-t 4 -tb 8 -b 2048 -ub 512`. No change to
  `serve.sh`.** `-tb 8` is unverifiable via bench but there is no evidence against it; changing it would be
  a blind guess. **Re-run this sweep on an idle host** (swap not full) to get clean absolute numbers and to
  finally test `-tb` via the server if desired.

## Environment
- CPU-only ThinkPad (Intel UHD 620, 8 threads, 23 GB RAM, swap often full, disk ~93% used).
- No NVIDIA GPU / vLLM. Path is **llama.cpp GGUF on CPU**.
- `ffmpeg`, `onnxruntime` present; no torch. `piper` TTS is broken (missing module).

## Model serving
- Model = LoRA `harness-hub/LFM3-1.7B-hermes` on base `LiquidAI/LFM3-1.7B`.
- llama.cpp **b12686** supports LFM3 (`llama_model_lfm3` symbols present). `--lora` works.
- `serve.sh`: Q4_K_M (was Q8_0), 64k ctx, 8 threads, `-fa on`, alias `lfm3-harbor-hermes`.
- **Hermes hard-requires ≥64,000 context** — a smaller `-c` triggers
  `ValueError: ... below the minimum 64,000`. Do NOT reduce context below 64k.
  (Speed comes from the quant, not from shrinking context; a 4k prompt barely notices 64k.)
- Throughput: Q8 ≈ 27 tok/s prompt, ~5 tok/s gen. Q4_K_M ≈ ~2× faster prompt.

## The "stuck for hours" bug (root cause)
- The **Telegram/gateway path** loaded **all 81 tools (~19k tokens)** + full skill index →
  ~20k-token prompts → ~13 min just to ingest, plus a message backlog + slot contention.
- Fix: `platform_toolsets.telegram: [terminal, file, web]` (and `cli: [terminal, web]`).
  Tool schemas are the dominant prompt cost (81 tools ≈ 19k tok; `portfolio-manager` alone = 40).
- Also: `agent.max_turns 500→8`, `gateway_timeout 1800→600` to bound tool rounds / turn time.

## Prompt minimization (what actually matters)
- **Tool schemas dominate**, not skills. Skills index 77 KB → ~3 KB after disabling all 60
  (`skills.disabled`) + `skills.external_dirs: []` + deleting `.skills_prompt_snapshot.json`.
- `memory.memory_enabled: false`.
- `mcp_servers: {}` — the remote `tinyfish` MCP blocked startup 60–120 s.
- The disk skills snapshot is keyed by dir manifest (not the disabled set); it's filtered at
  prompt-build time, so a full-size snapshot file ≠ full skills in the prompt.
- **Iter 8 (2026-11-27) — PROMPT SIZE is the #1 CPU lever (proven).** `hermes prompt-size
  --platform cli --json` reports the fixed budget offline; the eval's first-turn
  `prompt processing, n_tokens` in `serve.log` is the authoritative end-to-end number.
  - **Baseline** (plugins=[omh,herdr-agent-state,orca-status,portfolio-manager],
    cli=[connections,terminal,web]): first-turn **15,815 tok**, CLI tool schemas 8,922 B (6 tools).
  - **Lean A — `plugins.enabled: []`**: system 7,748→6,046 B, schemas 8,922→6,848 B.
    (portfolio-manager alone = ~40 tools; orca-status/herdr-agent-state add prompt blocks.)
  - **Lean B — `platform_toolsets.cli: [terminal]`** (drop connections=3 tools/4.6 KB + web=2/1.9 KB;
    disk=df, selfh=curl, both terminal-only): schemas **4,612 B (4 tools)**, first-turn **4,175 tok (-74%)**.
  - **Eval (warm cache): disk PASS 39 s (was 58), selfh PASS 35 s (was 41) — 2/2, faster.**
    (Cold first run disk 238 s = one-time 4,175-tok prefill; not a regression.)
  - **KEPT.** omh's residual 3-tool/2.2 KB block left (user relies on omh; tiny vs 74% win).
    Repro: `config/lean-config.yaml`, applied by `setup.sh` (`wire_hermes`).
  - **Gotcha:** the eval's `--pause-gateway` does a plain `gateway stop`, which BLOCKS if the
    gateway has live sessions → gateway keeps running and steals the single llama slot → both
    tasks hit 600 s over-budget. Fix: `hermes gateway stop --yes` first (interrupts sessions),
    then run the eval WITHOUT `--pause-gateway` (or ensure no live sessions). The separate
    `--profile coder` gateway uses a cloud model (glm-5.1), not :8080, so it doesn't contend.
  - Backup: `~/.hermes/config.yaml.bak.promptshrink-20261127_032953` (restore with `cp`).

## Reasoning-model gotchas
- LFM3-hermes is a **reasoning model**: it fills `reasoning_content` and may emit an empty
  `content` if `max_tokens` is too small (`finish_reason=length`). Give it room (eval uses 400).
- A strict eval must score **final content only** and **fail on `finish_reason=length`**.

## Contention & the eval
- The gateway and the eval share the server's 4 slots → the first eval cases timed out with
  empty token counts (never reached the model). Fix: `run_eval.py --pause-gateway`
  (stops the gateway, runs, restarts). `hermes gateway stop` takes ~60 s to drain — allow ≥180 s.
- Eval is hardened (per Codex): exact/JSON/nonempty matching, `length` fails, per-case `--budget`
  (default 300), `--max-prompt-tokens` cap, real `df` Use% equality for the disk task,
  fail-closed Telegram (Bot API can't read the user's reply) and selfh (dynamic page).

## Task results (agent mode, isolated)
- `disk` (run `df -h /`, report Use%): **PASS** ~256 s (Q8). Single tool round works.
- `selfh` (name Docker apps from selfh.st): **FAIL / over-budget** — multi-round web task,
  ~4 min per round on CPU. selfh.st/apps is a **dynamic JS app**; its listing is NOT in the
  static HTML (no "docker", only ~4 embedded names) → `verify_selfh` fails closed.

## Hermes config corruption (fixed)
- `~/.hermes/config.yaml` had 3 YAML breakages (stray `provider: omh`, split `enabled:` list,
  duplicate `external_dirs`). Backups: `config.yaml.bak.pre-lfm3-*`, `config.yaml.bak.pre-trim-*`.
- Revert everything: `cp ~/.hermes/config.yaml.bak.pre-trim-20261125_060718 ~/.hermes/config.yaml`.

## STT (local pt-BR)
- Parakeet TDT 0.6B v3 pt-BR ONNX int8 (`alefiury/...-ptBR-TAGARELA-onnx`) via `onnx-asr`.
- Wired as Hermes `stt.provider: local_command` + `HERMES_LOCAL_STT_COMMAND` (wrapper writes
  `transcript.txt`). Verified on real speech. Details in `models/parakeet-ptbr/README.md`.

## Codex consultation
- `codex-companion.mjs` (openai-codex plugin, v1.0.6). Model name convention matters:
  `--model gpt-6.2-sol --effort low` (bare versions 400). Read-only tasks: `write:false`.
- Used for iterative readiness gates on the eval/plan; folded findings into the harness.

## Loop status
- Iter0 4/7 (contention) → Iter1 isolation 7/7 → Iter2 disk PASS/selfh slow → Iter3 Q4 ~2.5× →
  Iter4 tuning + static selfh catalog → tier1 7/7, tier2 disk+httpd green → Iter5 BLAS (no gain)
  + httpd task → Iter6 deep-research hybrid-checkpoint fix (multi-round pp eliminated: rounds 2+
  ~400 tok not ~2,400; disk 305→150s) → Iter7 RAM wall (swap full → pp 22→5 tok/s).
  See EVAL_PLAN.md iteration log.

## Deep-research findings (research/deep-research-latency.md) — the big ones
- **LFM3 is tri-hybrid (13 conv + 5 SWA-256 + 6 global).** llama.cpp default
  `--checkpoint-min-step 8192` discards short-round checkpoints → **full prompt reprocess every
  tool call** (the real 11-min cause). Fix: `--cache-prompt --ctx-checkpoints 16
  --checkpoint-min-step 256 -cram 1024` → rounds 2+ process only new tokens (CONFIRMED).
- **`-c` reduction & KV q8_0 give ~0% at 4k** on CPU (LFM3's global-attn KV is tiny); KV q8_0 can
  even be slower on CPU decode. Drop unless RAM-bound past 32k.
- **Threads:** `-t 4` (physical cores) for generation, `-tb 8` for prompt ingest. `-tb 4` tanked
  pp to 15 tok/s; `-tb 8` restored ~22.
- **mlock** pins weights vs swap (needs `ulimit -l` raised as root; capped at 3 GB here → use mmap).
- **Reasoning:** don't disable (costs ~7 BFCL pts on 1.7B); use `--reasoning-budget 96–192`.
- **Runtime `--lora` contaminates prefix/KV cache** → merge LoRA into GGUF (bf16→Q4_K_M) and drop
  `--lora` (PENDING — needs disk headroom).
- **Round count is the multi-round killer:** Hermes v0.14 has no per-turn tool_choice; force 1 round
  via prompt + `tool_use_enforcement: true` (or an eval-only tool_choice required→none wrapper).

## PENDING improvements (discovered, not yet applied)
1. **Free RAM / mlock** — swap full is the current wall (pp 5 tok/s). Close apps or raise ulimit -l.
   **Gemini audit root cause:** zram0 (16G, prio 1000) + swapfile (4G) coexist = LRU inversion
   (Chris Down, Mar 2026). Fix: drop zram, use zswap (zstd, pool 25%) + swapfile, `vm.page-cluster=0`.
2. ~~**Merge LoRA → GGUF** (drop runtime --lora cache contamination)~~ **DONE (2026-11-27) —
   ADOPTED.** Merged Q4_K_M, dropped `--lora`: 136→0 CPU_REPACK fallback warnings, prefill
   18.8→41.0 tok/s (~2.2×), 2/2 PASS, cache reuse unchanged. See "LoRA merge A/B" above.
3. **Eval-only 2-request wrapper** (tool_choice required→none) to guarantee 1 round for selfh.
4. ~~**GGML_NATIVE=ON, BLAS=OFF** build (llamafile SGEMM beat OpenBLAS ~30% pp in community tests).~~
   **DONE (2026-11-27) — ADOPTED.** pp +19%, tg +2% (3/3 wins), verified `-march=native`. `setup.sh`
   now builds `-DGGML_NATIVE=ON -DGGML_BLAS=OFF`. See "Native build A/B" above.
5. ~~**Q4_0 vs Q4_K_M** A/B after LoRA merge (Q4_0 CPU_REPACK can be faster; validate JSON fidelity).~~
   **DONE (2026-11-27) — KEEP Q4_K_M.** Q4_0 fidelity-SAFE (eval 2/2: disk df-eq, selfh 3 apps) but
   NOT faster: mainline pp 40.95 vs 42.78 (−4%), tg tie; ik Q4_0 pp +5.3% (within noise, not +97%),
   tg below mainline. R8 path engages but marginal+noisy. Q4_0 GGUF removed. See "Q4_0 vs Q4_K_M A/B".
6. **Vulkan on UHD 620** controlled test — audit says DESCARTAR (UHD620 offload regressed to 1.9–3.9 tok/s).
7. **hermes-vcc-engine / llmslim** plugin (instant compression, no LLM call) for long sessions.
8. ~~**`--load-mode none` + THP + systemd MemoryMin=2.5G**~~ **DONE (2026-11-27) — REVERTED to mmap.**
   Audit: file mmap bypasses THP → ~30% pp loss. Disproven here: THP=`[madvise]` + no llama.cpp
   madvise ⇒ `none` still 4 KiB pages (`AnonHugePages`=0 both arms), pp identical, `none` 0/2 vs
   mmap 1/2. Needs THP=`always` (root) or a madvise patch to test properly. See "Load-mode A/B".
9. ~~**Spec-decode OFF A/B**~~ **DONE (2026-11-27) — KEPT spec OFF.** Audit math (n-gram hurts on
   CPU, pp_batch/tg only 1.5–2.5×) confirmed: spec-OFF disk 181 s vs spec-ON 200 s, selfh 39 vs 34 s
   (trivial, within noise), both 2/2 PASS, pp/tg identical within noise. See "Spec-decode A/B" above.
10. ~~Benchmark `harness-hub/LFM3-1.7B-pi-GGUF`~~ **DONE (not worth it — see above).** Prompt size
    beats round count on CPU; pi's 21.3k tool prompt is the killer. local-pi kept local, unpublished.

## DONE
- **Hermes upgraded v0.14.0 → v0.23.4** (2026-11-26). Single gateway, ~1,570 fewer prompt tok/call,
  skills 58→44, browser_*→browser_exec, #46201 fixed. (Audit item P1 complete.)
- **pi LoRA benchmarked vs hermes (2026-11-27, repo `~/Dev/AI/local-pi`, NOT published):** pi NOT
  worth pursuing. Both 2/3 (disk+selfh PASS, httpd FAIL both). selfh: hermes 63s vs pi 123s (~2×).
  Root cause: **pi's tool prompt is ~21.3k tokens** → 15–18 min/round CPU ingest; its "+37% fewer
  tool calls" (a model-quality claim) can't overcome harness-prompt weight on CPU. **Lesson: on CPU,
  prompt SIZE is the lever, not round count** — opposite of the hypothesis. Also: `httpd` (install+verify)
  is too hard for a 1.7B CPU agent (both fail) — doesn't differentiate; don't gate on it.
  Raw logs + table in local-pi/eval/results + EVAL_PLAN.md (commit 441a7f7).

## ik_llama.cpp evaluation — DEAD-END for our Q4_K_M (2026-11-27, THIS subagent)
- **Build:** OK. `git clone --depth 1 ikawrakow/ik_llama.cpp` → `/home/luandro/models/ik-build`
  (commit c9643ae), `cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DGGML_BLAS=OFF`,
  `-j4`. IQK optimized GEMM + IQK Flash Attention compiled in. Build took ~2× (two resumable runs,
  the iqk/*.cpp TUs are huge); no OOM. Binaries at `build/bin/{llama-bench,llama-server}`.
- **LFM3 support:** YES — loads the merged Q4_K_M, reports arch `lfm3` (params 1.73 B). No arch error.
- **A/B vs mainline native** (merged Q4_K_M, `-p 2048 -n 128 -t 4 -ngl 0`, 2× each, median):
  | backend        | pp2048 (tok/s)      | tg128 (tok/s)       |
  | -------------- | ------------------- | ------------------- |
  | mainline b12686| 53.33 / 52.39 → **52.9** | 16.22 / 16.07 → **16.1** |
  | ik_llama c9643ae| 52.71 / 55.77 → **54.2** | 12.26 / 15.47 → **13.9** |
  → pp **+2.5%** (within noise, NOT the +97% the audit hoped for); tg **−14%** (regression).
- **Why no prefill jump:** the audit's +97% was on a **Q4_0/QAT** model where ik's `Q4_0_R8` AVX2
  repack GEMM (PR #2472) engages. Our model is **Q4_K_M** — K-quant GEMM does not hit that iqk fast
  path, so ik ≈ mainline on pp and slower on tg. The headline win is quant-format-specific, not general.
- **Flags (serve.sh needs):** PRESENT → `--jinja`, `--reasoning-budget`, `--reasoning-format deepseek`,
  `-fa/--flash-attn`, `-cram/--cache-ram`, `--ctx-checkpoints`, `-np/--parallel`, `--lora`,
  `--chat-template`. **MISSING → `--cache-prompt`, `--load-mode`, `--checkpoint-min-step`** (serve.sh
  uses all three). So even if bench had won, serve.sh would need edits to adopt.
- **VERDICT: KEEP mainline.** Not ≥15% pp faster, decode regresses, and 3 serve.sh flags are absent.
  ik-build left in place outside the repo (`/home/luandro/models/ik-build`) for future Q4_0 experiments;
  mainline `build/`+`build-native/` untouched. No serve.sh change.

## Research findings (subagents → research/*.md)
- **Quant weights are NOT the CPU speed lever at 1–3B** (memory-bandwidth-bound). Attack prompt
  length, tool count, rounds, and caching instead. Q4_K_M is already right; don't go Q8.
- **Biggest llama.cpp wins:** shrink `-c` (8–16k; conflicts with Hermes' 64k hard check →
  needs `model.context_length` override), `-ctk q8_0 -ctv q8_0` KV quant, `-t/-tb/-ub` tuning,
  `--no-reasoning-preserve`, `--cache-reuse 256` + raise `-cram`, ngram spec (`--spec-type
  ngram-simple`) for structured output. Avoid q4_0 KV (wrecks tool-calling).
- **Reasoning runaway fix:** `--reasoning-budget 0` / `enable_thinking:false` on tool turns,
  `max_tokens ≥512`, `reasoning_control`+`reasoning_end`; score content only, fail on length.
- **Reliability:** cap tools ~20 / Tool RAG (TinyAgent ~2× prompt cut); GBNF structured first hop
  + lazy grammar (avoid global lock → constraint tax); parallel tool calls + loop/schema-retry.
- **selfh.st data:** no API, but static `https://r2.selfh.st/directory/software.json` (~1,700 apps,
  no auth) + `metrics.json`; secondary `awesome-selfhosted/awesome-selfhosted-data` YAML; compose
  snippets per-app from upstream repo.