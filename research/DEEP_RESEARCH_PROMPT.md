# Deep-research prompt — minimize latency of a CPU-only 1.7B agent (Hermes + llama.cpp)

> Hand this file to a deep-research agent (one with web search + fetch). Goal: a cited,
> actionable report on cutting **prompt-ingest and per-turn latency** for a small local agent.

## Context (trust these facts)
- Hardware: CPU-only ThinkPad, Intel UHD 620, **8 threads, 23 GB RAM, no GPU**, swap often full.
- Model: **LFM3-1.7B + `harness-hub/LFM3-1.7B-hermes` LoRA**, served by **llama.cpp `llama-server`
  b12686** (CPU), Q4_K_M, `-fa on`, 64k context, alias `lfm3-harbor-hermes` on 127.0.0.1:8080.
- Harness: **Hermes Agent v0.14.0** (NousResearch). Telegram gateway + `hermes -z` CLI.
- Measured: prompt ingest ~25 tok/s, generation ~5 tok/s (Q8) / ~2.5× faster on Q4.
- Prompt is ~4k tokens (minimal toolset: terminal+web; skills index ~3k; memory off).
- **Hermes hard-refuses context < 64,000** unless `model.context_length` is set in config.yaml.
- Symptom to fix: agent turns take 100 s (single tool round) to 11 min (multi-round). We want
  the **lowest possible prompt time** without breaking tool-calling quality.

## Questions to answer (cite primary sources: llama.cpp docs/PRs, Hermes source, benchmarks)
1. **Context-size override:** does setting `model.context_length: 16384` in Hermes config + `-c 16384`
   in llama.cpp actually reduce per-token attention/KV cost for a 4k prompt, and what breaks if the
   prompt later exceeds it? Quantify expected speedup (KV/attention vs context).
2. **KV-cache quant:** `-ctk q8_0 -ctv q8_0` — real speed/quality deltas on CPU; is q8_0 safe for
   tool-calling? Any 2026 llama.cpp changes.
3. **Prompt/prefix caching in llama.cpp server:** exact flags (`--cache-reuse`, `-cram`/`--cache-ram`,
   `--cache-prompt`), how Hermes' per-turn system prompt interacts with caching, and how to keep the
   tool-schema prefix resident. Expected TTFT reduction.
4. **Reasoning control:** `--reasoning-budget`, `--reasoning-format`, `reasoning_control`/`reasoning_end`,
   per-request `enable_thinking:false` — how to make the model emit final `content` fast on tool turns
   without a global quality hit. Does Hermes expose per-turn reasoning control?
5. **Prompt-ingest acceleration:** BLAS build (OpenBLAS) impact, `-t/-tb/-ub` tuning for an 8-thread
   laptop, `--no-warmup`, mmap/mlock. Which give real pp gains on CPU.
6. **Speculative decoding:** ngram (`--spec-type ngram-simple`) tuning for structured/tool output —
   when it helps vs hurts at 1.7B; recommended `--spec-ngram-*` sizes.
7. **Tool-schema / prompt shrinkage in Hermes:** how to further cut the system prompt (skills index,
   tool schemas, per-platform toolsets), Tool RAG, and whether Hermes supports a "lite" prompt mode.
8. **Anything else** that measurably cuts end-to-end turn latency on CPU for a 1.7B agent (batching,
   `--cont-batching`, slot count, `--parallel`, model/quant choice, draft models).

## Required output
A markdown report with a ranked table: **Technique | Expected latency win | Risk/quality | Exact
flag/config | Source URL**. End with a **"Top 10 to apply now, in order"** list and call out anything
that is a *pessimization* to avoid. Prefer measured numbers; label inferences. Do not include secrets.