# Learnings — local Hermes (LFM3-1.7B) on a CPU ThinkPad

Running log of hard-won facts from getting this setup working. Newest at top.

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
2. **Merge LoRA → GGUF** (drop runtime --lora cache contamination) — needs ~7 GB disk.
3. **Eval-only 2-request wrapper** (tool_choice required→none) to guarantee 1 round for selfh.
4. **GGML_NATIVE=ON, BLAS=OFF** build (llamafile SGEMM beat OpenBLAS ~30% pp in community tests).
5. **Q4_0 vs Q4_K_M** A/B after LoRA merge (Q4_0 CPU_REPACK can be faster; validate JSON fidelity).
6. **Vulkan on UHD 620** controlled test — audit says DESCARTAR (UHD620 offload regressed to 1.9–3.9 tok/s).
7. **hermes-vcc-engine / llmslim** plugin (instant compression, no LLM call) for long sessions.
8. **`--load-mode none` + THP + systemd MemoryMin=2.5G** — audit: file mmap bypasses THP → ~30% pp loss.
9. **Spec-decode OFF A/B** — audit math says n-gram hurts on CPU (pp_batch/tg only 1.5–2.5×). [subagent in progress]
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