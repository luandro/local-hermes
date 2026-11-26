# Fewer rounds + faster generation — LFM3-1.7B + Hermes v0.14.0 + LoRA on CPU (llama.cpp b12686)

Scope: 4-core / 8-thread CPU, ~5 tok/s decode, ~22 tok/s prefill, 8-round web task timing out.
Prefix reprocessing is already fixed (checkpoints). This file targets **round count × generation**.

Labels: **[M] measured** = a primary source reports the number on real hardware; **[I] inferred** =
analytical conclusion from the cited architecture/flag semantics, not a published benchmark on this box.
Every non-obvious claim carries a URL.

---

## 1. Forcing fewer rounds

| Technique | Problem it solves | How / exact flag | Expected impact | Risk | Source URL |
|---|---|---|---|---|---|
| `tool_choice:"required"` on the server | Model answers in prose instead of calling a tool on hop 0 | `POST /v1/chat/completions` body `"tool_choice":"required"`; Anthropic path `{"type":"any"}`. vLLM honours it via structured outputs. | [M] Guarantees ≥1 tool call when the template is supported. | [M] **Silently ignored** on templates with `supports_preserve_reasoning:true` (generates to `max_tokens`, `finish_reason:length`). [M] **HTTP 500** "empty grammar stack" on XML-style tool-call models served with `--mmproj`. LFM3 writes XML `<tool_call><function=…>` — treat as **high risk**. | https://github.com/ggml-org/llama.cpp/issues/27217 ; https://github.com/ggml-org/llama.cpp/issues/26737 ; https://docs.vllm.ai/en/stable/features/tool_calling |
| GBNF grammar forcing exactly one call | Same, but deterministic and template-independent | Build a grammar whose tool-call rule is `repeat(..., min=1, max=1)` (llama.cpp's own gemma4 parser does `min = required?1:0, max = parallel?-1:1`). Post via `grammar` / `--grammar-file`. | [M] One structured call then the grammar forces a stop → 1 tool round. | [I] LFM3 XML tool syntax must be encoded exactly; a wrong literal bricks generation. Loses the "answer after tool result" hop unless you drop the grammar on hop 1. | https://github.com/ggml-org/llama.cpp/blob/master/common/parsers/gemma4.cpp |
| Hermes `agent.tool_use_enforcement` | Model narrates ("I would run…") instead of calling | `agent: tool_use_enforcement: true` (or `[ "lfm3", "liquid" ]`). Injects system-prompt guidance only. | [M] Reduces "describes action, emits no call" turns. | Prompt-side only — **not a hard constraint**; a 1.7B can still ignore it. Default `"auto"` excludes LFM3 (not in the gpt/qwen/… list), so **you must set it explicitly**. | https://hermes-agent.nousresearch.com/docs/user-guide/configuration |
| Hermes `agent.max_turns` | Runaway 8-round loops | `agent: max_turns: 20` (positive int). Default is **unlimited** since v0.14.0. | [M] Caps a turn; at exhaustion Hermes injects one wrap-up + a single grace call. | [M] Caps caused **silent mid-task truncation**, which is why the default was removed. A cap makes the timeout *cleaner*, not faster. | https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/config_defaults.py |
| Hermes `prefill_messages_file` | No per-turn tool_choice exists → prime the first hop | Ephemeral `{role,content}` list injected at the **start of every API call** (never persisted). Put an assistant prefill that opens a tool call. | [I] Closest thing to "force first hop" without a proxy. | Injected on **every** call, not just hop 0 → can loop. Use a short, task-shaped prefill. | https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/config_defaults.py |
| Prompt phrasing "one command then answer" | Small model over-calls / over-thinks | System prompt: "Run **exactly one** tool command, then reply with the final answer. Do not call a second tool." + `tool_use_enforcement:true`. | [I] Reliable 1–2 round behaviour on 1.7B when the tool set is small. | [I] Brittle if the task genuinely needs 2 commands; model may still emit 0 calls (needs `required`/grammar backstop). | https://hermes-agent.nousresearch.com/docs/user-guide/configuration |
| **Does Hermes v0.14.0 support per-turn `tool_choice` / "required first hop"?** | — | **No.** `_build_api_kwargs()` does not set `tool_choice` at all (open bug #741). Config exposes no `tool_choice` key. | — | To get `required` on hop 0 only you need a **local proxy** that injects `"tool_choice":"required"` on the first request and `"auto"` after, or a GBNF grammar. | https://github.com/NousResearch/hermes-agent/issues/741 |

## 2. Faster generation on CPU at 1.7B

| Technique | Problem it solves | How / exact flag | Expected impact | Risk | Source URL |
|---|---|---|---|---|---|
| Accept the bandwidth floor | Expecting 5 tok/s to be "fixable" by flags | decode tok/s ≈ effective BW ÷ bytes-per-token. 1.7B Q4_K_M ≈ 1.1–1.4 GB → 5 tok/s ⇒ ~5–7 GB/s effective. | [M] Explains why `-t`, `-ub`, `-fa`, BLAS, KV-type all move decode <5%. | None — this is physics. Only a smaller quant / smaller model / more memory channels raises it. | https://learn.arm.com/learning-paths/cross-platform/llm-decode-roofline/how-decode-uses-memory ; https://www.bigiron.cc/guides/predicting-local-llm-speed-from-memory-bandwidth-tested-on-three-cpus |
| `-t 4` (not 8) on a 4-core | HT siblings contend on a bandwidth-bound kernel | `-t 4 -tb 8` (4 decode threads = physical cores; 8 batch threads for prefill). | [M] 4 threads ≈ 8 on pp, **slightly better tg**; decode peaks at physical cores then regresses. | [M] >physical cores lowers tg. | https://github.com/ggml-org/llama.cpp/pull/26348 ; https://multigrid.ai/learn/llamacpp-threads-flag |
| ngram spec on **tool/JSON** output | Decode is the wall on structured calls | `--spec-type ngram-simple` (or `ngram-map-k`). Structured field names repeat → high accept. | [M] ~1.3–2× tg on repetitive/structured output; ~0 on diverse prose. | [M] **Pessimization** on low-repetition text (draft cost > accepts). | https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md |
| `--spec-draft-n-max` is a **no-op** for n-gram | Wasted tuning | Don't set it for ngram-simple/map-k/mod. | [M] Tested 2/4/6/8/12 → identical speed & acceptance. | None. | https://github.com/solarkyle/qwen38-flashnext-16gb |
| Prefer `ngram-map-k` / `k4v` over `ngram-simple`/`ngram-mod` | Wrong variant choice | `--spec-type ngram-map-k` (accept 92%) or `ngram-map-k4v` (95.5%). | [M] +45% on code vs none; ngram-mod is the **weakest** of the five. | [M] Acceptance ≠ throughput (k4v accepts more but is slower than cache). | https://github.com/solarkyle/qwen38-flashnext-16gb |
| ngram size-n / size-m / min-hits | Over/under-matching | Defaults n=12, m=48, hits=1. For tool/JSON keep n≈8–12, m≈16–32, hits 1–2. | [I] Smaller m raises acceptance on short structured spans; too-large m tanks it. | [M] Wrong m → low accept → slowdown. | https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md |
| KV quant is ~free at short context, ~half at 64K | Assuming q8_0 helps/hurts decode | `-ctk q8_0 -ctv q8_0`. LFM3 is tri-hybrid (13 conv + 5 SWA-256 + 6 global) → tiny KV. | [M] ~2% cost empty, ~0% at 4K, **~50% at 64K** (GPU). On CPU q8_0 can be *slower* than f16. | [M] Quality: near-lossless on Qwen, **Gemma-sensitive**; LFM3 untested → risk. At 4K it buys ~nothing. | https://sotaaz.com/post/llamacpp-kv-cache-quantization-bench-en ; https://inboxpraveen.github.io/blogs/blog-1.html |
| 2026 CPU decode kernels (VNNI/AMX) | Slow generic Q4_K matmul | Build `-DGGML_NATIVE=ON` on a CPU with AVX512-VNNI; AMX helps **prefill only**. | [M] AVX512-VNNI Q4_K dot: **1.4–2.1×** decode; AMX: 1.47× **pp**, ~1.0× tg. | [M] Zero benefit on a 4-core AVX2 laptop (no VNNI/AMX). | https://github.com/ggml-org/llama.cpp/pull/26348 ; https://pseedr.com/stack/llamacpp-optimizes-intel-amx-for-147x-faster-prompt-processing |
| `-np 1` (single slot) | Parallel slots split prefill | `--parallel 1`. | [M] `-np 4` cut a cold 5.8K prefill by **−92%** (prefill is partitioned, not parallelized). | [M] N>1 only helps concurrency. | https://github.com/ggml-org/llama.cpp/discussions/30333 |

## 3. Reasoning control — quality/speed tradeoff on a 1.7B

| Technique | Problem it solves | How / exact flag | Expected impact | Risk | Source URL |
|---|---|---|---|---|---|
| **Do NOT disable reasoning** (`none` / `--reasoning-budget 0`) | Thinking eats the token budget | `reasoning_effort:"none"` or `--reasoning-budget 0`. | [M] **Tanks tool-calling accuracy**: Qwen3-1.7B BFCL Live 72.8 (Think) → 65.0 (No-Think); Non-Live 84.0 → 81.1. | [M] LFM3 docs: `enable_thinking=False` "not recommended… may still emit reasoning or give inconsistent answers." | https://arxiv.org/html/2608.22472 ; https://docs.liquid.ai/lfm/key-concepts/thinking |
| Soft budget + message (best speed/quality) | Over-thinking without accuracy loss | `--reasoning-budget 192 --reasoning-budget-message "…budget exceeded, make the tool call or answer now."` | [M] HumanEval 94%→88% (no-reasoning)→**89%** (budget 1000 + message). **Without the message it craters to 79%.** | [M] Active budget sampler **disables backend sampling** → ~5% (CUDA) to ~30% (Vulkan) per-token tax; on **CPU** the tax is small (no GPU→CPU transfer). | https://github.com/ggml-org/llama.cpp/pull/20297 ; https://www.lftw.dev/guides/llama-cpp-reasoning-budget |
| `reasoning_effort: low` instead of `none` | Want speed, keep accuracy | Hermes `agent: reasoning_effort: low` (or `minimal`). | [I] Keeps a short think block; preserves most tool accuracy while cutting tokens. | [I] LFM3 has no `reasoning_effort` levels (n/a) — maps to enable_thinking on/off, so use the budget instead. | https://hermes-agent.nousresearch.com/docs/user-guide/configuration |
| Keep reasoning **on** for tool turns | Net accuracy | Leave thinking enabled; cap with a soft budget. | [M] Reasoning is worth +7–8 pts on 1.7B tool calling; the cost is tokens, not correctness. | — | https://arxiv.org/html/2608.22472 |

## 4. Merging the LoRA into GGUF

| Technique | Problem it solves | How / exact flag | Expected impact | Risk | Source URL |
|---|---|---|---|---|---|
| Runtime `--lora` **contaminates the prefix/KV cache** | Your prefix-checkpoint fix is silently defeated | Merge instead of `--lora`. Runtime path: `W·x + B(A·x)` per graph eval, implies `--no-mmap`. | [M] RAM prompt cache does **not** record adapter identity → KV computed under adapter A reused for B (10/10 wrong on b11514). `--cache-ram 0` or `cache_prompt:false` fixes it but **kills prefix reuse**. | [M] Silent wrong output, no log; `cache_n` shows reuse but not under which adapter. | https://github.com/ggml-org/llama.cpp/issues/30129 ; https://github.com/ggml-org/llama.cpp/issues/26207 |
| Runtime `--lora` slows prefill | PP regression | Merge → single set of weights. | [I]/[M] Community report of up to **5× PP loss** with `--lora` on Strix Halo; no clean CPU 1.7B number published. | [I] 32M adapter on 1.7B is small; the dominant cost is the cache-correctness risk, not the matmul. | https://www.reddit.com/r/LocalLLaMA/comments/1xbekzx/fast_lora_in_llamacpp/ |
| Merge → convert → quantize (worth it) | Both above | `merge_and_unload()` → `convert_hf_to_gguf.py --outtype bf16` → `llama-quantize … Q4_K_M`. | [I] Removes per-token LoRA overhead, restores mmap + safe prefix cache. One artifact, no adapter/base skew. | [M] **Never merge over a Q4 GGUF** — quantize from bf16/f16 only; re-quantizing a quant destroys quality. | https://llmconfigurator.com/en/guides/convert-model-to-gguf ; https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md |
| Adapter-only GGUF (`convert_lora_to_gguf.py`) | Avoid full merge | Only for plain LoRA/QLoRA; `--outtype f16`. | [M] Works with a quantized base (no dequant). | [M] DoRA / modified-emb / added-token adapters **must** be merged; converted DoRA can differ from trained. | https://multigrid.ai/learn/lora-adapter-in-llamacpp ; https://shattered.io/merge-lora-adapter-gguf-llama-cpp-ollama-12-steps |

## 5. Other end-to-end round/latency cuts

| Technique | Problem it solves | How / exact flag | Expected impact | Risk | Source URL |
|---|---|---|---|---|---|
| **Shrink the tool schema** (deferred tools / tool search) | 81 tools ≈ 19k-token prompt; small models degrade with big tool lists | LFM3 `defer_loading=True` + a `search` tool → definitions load on demand. | [M] Small models "degrade when the prompt carries dozens of tool definitions." Fewer tokens + fewer wrong calls. | [I] Adds a search round if the model must discover a tool. | https://docs.liquid.ai/lfm/models/lfm3-1.7b ; https://huggingface.co/LiquidAI/LFM3-1.7B |
| `-np 1` + stable prefix + `--cache-prompt` | Re-prefill each round | Keep single slot; put volatile text last. | [M] TTFT near-zero on revisits; `-np>1` partitions prefill (−92%). | — | https://github.com/ggml-org/llama.cpp/discussions/15530 ; https://multigrid.ai/learn/llamacpp-prompt-caching |
| `--load-mode mlock` | Cold-read page faults stall first token | `--load-mode mlock` (needs RAM ≥ model). | [I] Avoids first-token stalls if swap is in use. | [M] Can OOM if RAM < model. | https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md |
| Lower `max_tokens` + LFM3 sampling preset | Runaway / empty content | `--temp 0.1 --top-k 50 --repeat-penalty 1.1`; cap `max_tokens` 512–1024. | [M] LFM3-recommended preset; caps runaway. | [M] Too-low cap → `finish_reason:length`, empty `content`. | https://huggingface.co/LiquidAI/LFM3-1.7B-GGUF |
| One "do the task" tool instead of many | Round count is the real cost | Collapse the web flow into one composite tool. | [I] 8 rounds → 2 (one call + answer) is the single biggest win. | [I] Loses granularity/observability. | https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop |
| Spec decode is **not bit-identical** under grammar/JSON | Silent tool-arg drift | Verify with a divergence canary; keep target-only for exact endpoints. | [M] 1.2% tool-arg mismatch per call with a draft model; compounds over a 5-call session (~6%). | [M] Under JSON schemas **84.9% of drafts are rejected** unless grammar-masked. | https://dev.to/marcuswwchen/speculative-decoding-shifted-our-output-distribution-and-evals-missed-it-4dci ; https://yuvrajsinghbhadoria2.github.io/grammar-spec-decoding |

---

## Top 8 to apply now (ranked by real latency/round impact)

1. **Merge the 32M LoRA into the GGUF** (bf16 → Q4_K_M) and drop `--lora`. Fixes the prefix/KV-cache
   contamination (#30129/#26207) that silently defeats your checkpoint work, restores mmap, and removes
   per-token LoRA overhead. **[I] + correctness, [M] cache bug.** — https://github.com/ggml-org/llama.cpp/issues/30129
2. **Cut the tool schema** (defer_loading / tool search, or one composite "do-task" tool). Attacks round
   count and prompt size together — the two multipliers. **[M] small models degrade with big tool lists.** —
   https://docs.liquid.ai/lfm/models/lfm3-1.7b
3. **Force hop-0 with a GBNF grammar (`min=1,max=1`) or a tiny proxy injecting `tool_choice:"required"`
   on the first request only.** Hermes v0.14.0 has **no** per-turn tool_choice (#741); `required` on the
   server is unreliable on LFM3's XML template (#27217/#26737). **[M] flags, [I] LFM3 risk.** —
   https://github.com/NousResearch/hermes-agent/issues/741
4. **Keep reasoning ON but soft-cap it**: `--reasoning-budget 192` **with** `--reasoning-budget-message`.
   Disabling (`0`/`none`) costs ~7–8 BFCL pts on 1.7B; the message is the difference between 89% and 79%. **[M]** —
   https://github.com/ggml-org/llama.cpp/pull/20297
5. **Switch ngram variant to `ngram-map-k`** (accept 92%) and drop `--spec-draft-n-max` (no-op). ~1.3–2× tg
   on tool/JSON, pessimization on prose. **[M]** — https://github.com/solarkyle/qwen38-flashnext-16gb
6. **`-t 4 -tb 8`, `--parallel 1`, `--load-mode mlock`.** 4 decode threads = physical cores; single slot
   avoids the −92% prefill partition; mlock kills page-fault stalls. **[M]** —
   https://github.com/ggml-org/llama.cpp/pull/26348 ; https://github.com/ggml-org/llama.cpp/discussions/30333
7. **Drop `-ctk q8_0 -ctv q8_0`** (or leave f16). At 4K it buys ~0 and can be slower on CPU; LFM3's tri-hybrid
   KV is tiny. Re-add only if you push past ~32K. **[M]** — https://sotaaz.com/post/llamacpp-kv-cache-quantization-bench-en
8. **Prompt: "Run exactly one tool command, then answer."** + `tool_use_enforcement: true` (LFM3 is not in
   the `auto` list). Cheap, no infra. **[I]** — https://hermes-agent.nousresearch.com/docs/user-guide/configuration

### Do-not-do
- Don't set `agent.max_turns` low to "fix" the timeout — it truncates silently; it makes failure clean, not fast.
- Don't expect any decode flag to beat ~5 tok/s — it's the memory-bandwidth floor for ~1.2 GB/token.
- Don't merge/quantize from an already-quantized GGUF.