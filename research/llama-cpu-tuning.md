# llama.cpp CPU tuning — make LFM3-1.7B (~1.7B) faster & more reliable on a CPU-only laptop

Scope: 8 threads, 23 GB RAM, no GPU, `llama-server` build ~b12686 (2026). Goal: 2–5x
end-to-end speedup (prompt ingest + generation) without wrecking quality. Every non-obvious
claim cites a primary source (llama.cpp docs / PRs / issues). "Expected gain" figures are
order-of-magnitude estimates *inferred* from the cited mechanism unless the source states a
number; treat them as hypotheses to A/B test, not benchmarks of your box.

Baseline being tuned (from `serve.sh`): `-m LFM3-1.7B-Q4_K_M.gguf --lora … -c 65536 -ngl 0 -t 8 -fa on`.
Two obvious wins already visible: context is **65536** (massively oversized for a 1.7B model)
and there is **no `-ub`/`-b`/`-tb` tuning, no KV quant, no mlock**.

---

## Technique | What it does | Expected gain | Tradeoff | Flag / How | Source URL

| Technique | What it does | Expected gain | Tradeoff | Flag / How | Source |
|---|---|---|---|---|---|
| **Shrink context size** | KV cache and per-token attention cost scale with `-c`. 65536 is wildly larger than a 1.7B model needs for agent turns. | Large. Cuts KV memory ~proportionally and speeds attention; removes the dominant slowdown on long contexts. 8k–16k is plenty for most agent loops. | Smaller max prompt+output window; long RAG may need chunking. | `-c 8192` (or `16384`). Default is 0 = model's trained length. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **Flash Attention** | Fused, memory-efficient attention kernel; required for KV-cache quantization to work. | Moderate; bigger at long context. Also *unlocks* `-ctk/-ctv` quant. | Negligible quality cost; `-fa auto` already on by default in b12686. Keep `-fa on`. | `-fa on` (default `auto`). | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **KV-cache quantization `-ctk q8_0 -ctv q8_0`** | Store K/V in 8-bit instead of f16. | Moderate; ~halves KV memory → less bandwidth per token, lets a big context fit in RAM without swapping. Near-lossless. | Tiny quality loss; V quant is the sensitive half. | `-ctk q8_0 -ctv q8_0` (needs `-fa on`). | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **KV-cache quant `-ctk q4_0 -ctv q4_0`** | 4-bit K/V. | Higher memory/bandwidth saving than q8. | **Substantial quality/tool-calling degradation** — docs explicitly warn against extreme KV quant. Avoid for an agent that calls tools. | `-ctk q4_0 -ctv q4_0` (only if RAM-bound and quality-tolerant). | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/function-calling.md |
| **Quant choice: Q4_K_M vs Q8_0 vs Q5** | Weight precision. Q4_K_M is the community default and the `-hf` fallback. | Q4_K_M ≈ 2x smaller/faster than Q8_0 with small quality loss; Q5_K_M is the middle ground. For a 1.7B already at Q4_K_M, going Q8_0 is *slower* — don't. | Q4_K_M: minor quality drop vs Q8. Q8: best quality, ~2x RAM & slower on CPU. | Use `Q4_K_M` (current) or `Q5_K_M` if quality is marginal; avoid Q8 on CPU. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **Thread sizing `-t` / `-tb`** | `-t` = gen threads, `-tb` (`--threads-batch`) = prompt-processing threads. | Right-sizing avoids oversubscription; `-tb` > `-t` can speed prompt ingest. On 8 threads start `-t 8 -tb 8`, then try `-tb 4` (sometimes faster for small batches). | Too many threads → contention, slower. Physical vs logical cores matter. | `-t 8 -tb 8` (tune `-tb` 4–8). | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **Batch / ubatch sizing `-b` / `-ub`** | `-b` logical batch (default 2048), `-ub` physical ubatch (default 512). Prompt processing runs in ubatches. | BLAS-accelerated prompt processing helps mainly at batch > 32; larger `-ub` can raise pp throughput. | Too-large `-ub` raises compute-buffer memory; diminishing returns on CPU. | Try `-ub 512` (default) → `-ub 1024`; keep `-b 2048`. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/build.md |
| **Build with BLAS (OpenBLAS)** | Offloads matmuls to optimized BLAS. | "Performance improvements in prompt processing using batch sizes higher than 32 … does not affect generation." Pure prompt-ingest win. | Build-time dependency; no generation benefit. | `cmake -B build -DGGML_BLAS=ON -DGGML_BLAS_VENDOR=OpenBLAS`. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/build.md |
| **Build with ZenDNN (AMD) / oneMKL (Intel)** | Vendor matmul kernels. | Vendor-specific prompt-processing acceleration. | Only for matching CPU vendor. | `-DGGML_ZENDNN=ON` (AMD EPYC) or oneMKL (Intel). | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/build.md |
| **`-lm mlock` / `mmap+mlock`** | Locks model pages in RAM (no swap/compress), mmap maps file. | More *reliable* (no page-out stalls) on a 23 GB box; mmap keeps load fast + low RSS. | mlock needs RAM ≥ model; mmap-only can page out under pressure. | `-lm mmap+mlock` for stability, or `-lm mmap` (default auto) for low memory. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **`--no-reasoning-preserve`** | Stops storing the full reasoning trace in history (keeps only last assistant msg). | Smaller prompts on multi-turn agent loops → faster prompt ingest, less KV churn. | Loses reasoning history across turns (fine for most tool loops). | `--no-reasoning-preserve`. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **`-rea off` / `--reasoning-budget 0`** | Disable thinking, or cap it. Reasoning tokens are pure latency on CPU. | Large for tool turns — removes 200–400+ thinking tokens per call. | Model won't "think"; set per-request for simple tool turns only. | `-rea off` globally, or per-request `reasoning_effort:"none"`. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **Speculative decoding — ngram (no draft model)** | Drafts tokens from an n-gram lookup of the prompt, target verifies. Needs **no second model** → ideal for CPU. | Potentially large for repetitive/structured output (code, JSON, tool args); near-zero when output is novel prose. | No gain on high-entropy text; small lookup overhead. | `--spec-type ngram-simple` (or `ngram-mod`/`ngram-cache`); tune `--spec-ngram-*-size-*`. `--spec-default` enables a default config. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **Speculative decoding — small draft model** | A tiny draft model proposes, target verifies. | 1.5–2.5x typical when draft acceptance is decent. | Costs RAM for a 2nd model + weaker acceptance on a 1.7B target; ngram usually better here. | `-md draft.gguf --spec-draft-n-max 3`. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **Prompt caching (default on) + `--cache-reuse`** | Reuses KV of a shared prompt prefix across requests. | Big for repeated system prompts / tool schemas (your 19k-token schema). | Needs identical prefix; `--cache-reuse N` reuses via KV shift. | `--cache-prompt` (default on) + `--cache-reuse 256`. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **`--no-warmup`** | Skips the empty warmup run at load. | Faster startup / reload (not steady-state). | First real request pays the warmup cost. | `--no-warmup`. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **`--numa distribute` / `isolate`** | NUMA-aware thread placement. | Helps only on multi-socket/NUMA; single-laptop likely neutral. | Negligible on a single-node laptop. | `--numa distribute`. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **`--poll 0` / `--prio`** | Controls worker wait strategy & priority. | Marginal; can trim idle latency. | Busy-polling burns CPU. | `--poll 0` (no polling) or raise `--prio`. | https://raw.githubusercontent.com/ggml-org/llama.cpp/master/tools/server/README.md |
| **SWA full vs sliding (`--swa-full`)** | Sliding-window attention shrinks KV ~5x for SWA models (Gemma-3 class). | Large KV memory save *if* the model is SWA. LFM3-1.7B is dense (non-SWA) → N/A. | Disables advanced context caching; only for SWA archs. | `--swa-full` (only relevant to SWA models). | https://github.com/ggml-org/llama.cpp/pull/13194 |

---

## Notes on the biggest levers for *this* box

1. **Context is the silent killer.** `-c 65536` on a 1.7B CPU model means every token's
   attention and the whole KV allocation are sized for 64k. Dropping to 8k–16k is the single
   easiest large win and also makes `-ctk/-ctv q8_0` KV fit comfortably in 23 GB.
2. **Prompt ingest dominates agent latency** (huge tool schemas, multi-round). Attack it with
   a BLAS build + `-ub`/`-tb` tuning + prefix caching + `--no-reasoning-preserve`, not with
   generation tricks.
3. **Speculative decoding (ngram) is the main "free" generation multiplier** because it needs
   no draft model — but only pays off on structured/repetitive output.
4. **Don't go to Q8 weights on CPU** — you already have Q4_K_M; Q8 is ~2x slower for a 1.7B.
   Spend the memory budget on KV quant + smaller context instead.

## Top 5 to try first

1. **`-c 8192` (or 16384)** — shrink the oversized 65536 context. Biggest single win.
2. **`-ctk q8_0 -ctv q8_0 -fa on`** — halve KV memory/bandwidth, near-lossless.
3. **BLAS build + `-t 8 -tb 8 -ub 512`** — speed prompt processing (ingest), the real bottleneck.
4. **`--spec-type ngram-simple`** — free generation speedup on structured/tool output.
5. **`--no-reasoning-preserve` + `-rea off` for tool turns + `--cache-reuse 256`** — cut
   per-turn prompt size and skip reasoning latency on the agent loop.