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
  Iter4 tuning + static selfh catalog → **tier1 7/7, tier2 disk PASS (103s) + selfh PASS (646s)**.
  Functional for single-round local tasks. Multi-round web browsing remains too slow on CPU.
  See EVAL_PLAN.md iteration log.

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