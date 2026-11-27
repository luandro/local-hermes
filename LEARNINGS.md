# Learnings — local Hermes (LFM3-1.7B) on a CPU ThinkPad

Running log of hard-won facts from getting this setup working. Newest at top.

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
5. **Q4_0 vs Q4_K_M** A/B after LoRA merge (Q4_0 CPU_REPACK can be faster; validate JSON fidelity).
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