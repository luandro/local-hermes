# Small-model agents on CPU — techniques to make LFM3-1.7B + LoRA reliable & fast

Context: Hermes Agent (NousResearch) + LFM3-1.7B + LoRA, `llama.cpp` CPU-only. Three
problems: (1) reasoning model emits answer in `reasoning_content` and hits token cap with
empty `content`; (2) multi-round web tool tasks ~4 min/round; (3) tool-schema bloat (81 tools
≈ 19k tokens) slows prompt ingest.

All claims cite a primary source. Numbers are as reported by the source, on its hardware.

## Technique | Problem it solves | How | Expected impact | Source URL

### Reasoning runaway / empty content

| Technique | Problem it solves | How | Expected impact | Source |
|---|---|---|---|---|
| Raise `max_tokens` above the reasoning budget | Empty `content`, `finish_reason=length` | Thinking models spend 200–400+ tokens on internal `` before any visible output; a small cap is consumed entirely by reasoning. Set 512–2048. | Eliminates empty-content failures; the single most common "model does nothing" cause. | https://github.com/ggml-org/llama.cpp/discussions/20931 |
| `--reasoning-budget N` (server) | Unbounded thinking | `-1` unrestricted, `0` immediate end (disable thinking), `N>0` caps thinking tokens; inject `--reasoning-budget-message` when exhausted. | Hard cap on reasoning tokens → predictable latency; `0` removes thinking entirely for tool turns. | https://github.com/ggml-org/llama.cpp/blob/HEAD/tools/server/README.md |
| `--reasoning-budget 0` / `enable_thinking:false` | Reasoning on simple tool turns | Disable thinking per request via `chat_template_kwargs:{"enable_thinking":false}` or `reasoning_effort:"none"`. | Removes the reasoning block for tool-call turns; direct answer in `content`. | https://github.com/ggml-org/llama.cpp/pull/13771 |
| `--reasoning-format` + `--jinja` | Answer misfiled into `reasoning_content` | `--reasoning-format deepseek` (default with `--jinja`) routes thoughts to `message.reasoning_content`, leaving `content` clean. | Clean separation; client reads `content` for the final answer. | https://github.com/ggml-org/llama.cpp/blob/HEAD/tools/server/README.md |
| `/v1/chat/completions/control` `reasoning_end` | Reasoning won't stop | Start request with `reasoning_control:true`, then POST `action:"reasoning_end"` to force the model out of thinking into the final answer mid-stream. | Real-time cap on runaway reasoning without pre-allocating a huge budget. | https://github.com/ggml-org/llama.cpp/blob/HEAD/tools/server/README.md |
| Score final content only; fail on `finish_reason=length` | Silent empty answers | Eval harness must treat `length` as failure and score `content` not `reasoning_content`. | Turns the bug into a detectable test failure (already adopted in local eval). | https://github.com/ggml-org/llama.cpp/discussions/20931 |

### Structured / grammar-constrained decoding

| Technique | Problem it solves | How | Expected impact | Source |
|---|---|---|---|---|
| GBNF grammar / `response_format` json_schema | Malformed tool args, wrong tool name | At each token the grammar sets invalid tokens to −∞; output is guaranteed to parse. `json_schema`→GBNF auto-converted per request. | Malformed-JSON / wrong-tool-name failure modes drop to ~zero; "3B beat 70B on BFCL under XGrammar." | https://github.com/ggml-org/llama.cpp/blob/HEAD/grammars/README.md |
| Force-tool grammar on first hop only (`required_first_hop`) | Small model skipping the tool | `tool_choice:"required"` + grammar on hop 0, then release to `"auto"` so the final answer can land. | Canonical SLM loop-break; forces a structured call, then allows prose. | https://edgevox.nrl.ai/documentation/tool-calling |
| Lazy grammars (`grammar_lazy` + `grammar_triggers`) | Grammar strangling free-text reasoning | Grammar stays inactive until a trigger token appears, then constrains only the structured tail. Server builds it automatically from the chat template when `--jinja` + `tools`. | Lets the model think in prose then emit a valid call; avoids forcing reasoning into JSON. | https://codeloopback.dev/articles/llm-structured-output |
| **Constraint tax** awareness — free-text reasoning prefix, not global lock | Grammar suppressing reasoning | Hard schema-locking can suppress reasoning (Qwen2.5-1.5B 91.5%→48% under a global lock). Adopt constrained JSON *with* a reasoning prefix. | Recovers reasoning quality lost to over-constraining. | https://posttrainllm.com/docs/learn/small-model-tool-calling-playbook/ |
| Keep schemas small; bounded final field | Grammar size / runaway final string | Every optional field enlarges the grammar; an unbounded trailing `string` runs to `max_tokens`. Split large extractions; set `maxLength` or put bounded fields last. | Smaller grammar = faster masking; prevents the last field eating the token budget. | https://docs.liquid.ai/deployment/on-device/llama-cpp/structured-output |
| `llguidance` backend (`-DLLAMA_LLGUIDANCE=ON`) | Grammar masking CPU cost | ~10× faster grammar masking than the default sampler, same API. | Lower per-token overhead when grammars are active. | https://edgevox.nrl.ai/documentation/tool-calling |

### Tool-schema minimization

| Technique | Problem it solves | How | Expected impact | Source |
|---|---|---|---|---|
| Tool RAG (retrieve only relevant tools) | 81 tools ≈ 19k tokens | Retrieve ~4 relevant tools per query instead of all. TinyAgent: prompt 2762→1397 tokens (~2× cut) with *higher* accuracy via a DeBERTa classifier (recall 0.998). | ~2× prompt reduction; accuracy up. Directly attacks the 19k-token ingest cost. | https://arxiv.org/abs/2409.00608 |
| Cap tool count (~20) | Hallucinated tool names | qwen3-1.7B with 107 tools "struggled most of the time"; with 20 tools it got 19/20 correct. | Large reliability gain from fewer, well-named tools. | https://github.com/speakeasy-api/developer-docs/blob/main/mcp/tool-design/less-is-more.mdx |
| Schema-in-Prompt (SIP) vs Functional Tokens | Generalization vs latency tradeoff | FTs are compact but can't do unseen functions; SIP generalizes. SIP costs +1,785–1,905 prompt tokens and 26–98× time-to-first-call. | Choose per need: FT for fixed toolset speed; SIP when tools vary. Quantifies the cost of schema bloat. | https://arxiv.org/html/2609.09476v1 |
| Toolsets / per-platform tool filtering | All-tools prompt bloat | Restrict tools per surface (e.g. telegram: [terminal,file,web]). Local LEARNINGS confirms tool schemas dominate prompt cost. | Removes the dominant prompt cost; already applied locally. | https://github.com/ggml-org/llama.cpp (local LEARNINGS.md) |

### Cutting agent rounds

| Technique | Problem it solves | How | Expected impact | Source |
|---|---|---|---|---|
| Parallel tool calls | ~4 min/round, sequential stacking | Model emits multiple independent calls in one turn; host fans out concurrently (bounded). | "At least 2× faster, often more"; GAP cut turns 21–33% and time 21–32% on multi-hop QA. | https://www.augmentcode.com/changelog/parallel-tool-calls |
| Parallel tool calls (framework support) | Sequential dispatch | smolagents `ToolCallingAgent` and LM Studio `max_parallel_tool_calls` run multiple calls per response. | Fewer round-trips; wall-clock = slowest call not sum. | https://github.com/huggingface/smolagents/pull/1412 |
| Dependency-graph planning (GAP) | Too many ReAct rounds | Decompose into a dependency graph; independent sub-tasks run in parallel per level. Qwen2.5-3B: 1.78 vs 2.27 turns; 168s vs 248s. | Fewer turns, shorter responses, lower latency. | https://arxiv.org/html/2510.25320 |
| Speculative *tool* calling | Round-trip gaps | A tiny speculative model predicts the tool call and pre-executes cheap stateless tools (web search, file read) while the main model generates. | Hundreds of tok/s throughput gain in agent serving; best for cheap stateless tools. | https://arxiv.org/html/2512.15834v1 |
| Loop detection / schema-retry / echoed-payload sanitiser | Wasted rounds on loops & bad args | Fingerprint (tool,args); on 2nd identical call inject "already called", 3rd hard-break; one schema-retry per tool; sanitise echoed JSON. | Converts crashes/loops into clean exits; Hammer-0.5B infinite loop fixed. | https://edgevox.nrl.ai/documentation/reports/slm-tool-calling-benchmark |
| Explicit "MUST call the tool" directive | Model answers from memory | System suffix: "You MUST call the matching tool for live data. Do NOT answer from memory." | Lifts BFCL irrelevance/hallucination sub-scores for small models. | https://edgevox.nrl.ai/documentation/reports/slm-tool-calling-benchmark |
| `agent.max_turns` / `gateway_timeout` bounds | Runaway multi-round cost | Cap turns (500→8) and timeout (1800→600). | Bounds worst-case wall-clock; already applied locally. | https://github.com/ggml-org/llama.cpp (local LEARNINGS.md) |

### KV / prompt caching

| Technique | Problem it solves | How | Expected impact | Source |
|---|---|---|---|---|
| Prompt caching (`cache_prompt`, default on) | Re-ingesting shared prefix each turn | Reuse KV of the common prefix; only the differing suffix is prefilled. | 91.7% TTFT reduction on long prompts (186ms→15ms warm) on CPU. | https://tiffena.me/blog/ai-infrastructure/benchmark-cpu-only-llm-inference-llama.cpp-server-caching-batching-concurrency/ |
| `--cache-reuse N` (e.g. 256) | Shared chunk not at the front | Reuse cached KV slices of ≥N tokens even when not a pure prefix, via KV shifting. | "Difference between constant reprefill and near-instant follow-ups." | https://7minai.com/how-to-fix-llama-server-kv-cache-reuse/ |
| `-cram` / `--cache-ram` raise (default 8192 MiB) | Cache eviction on long contexts | Raise the host-RAM prompt-cache cap (e.g. 20480) so long agentic prefixes stay resident. | "Massive speedups" for long multiturn; more RAM, no VRAM cost. | https://www.reddit.com/r/LocalLLaMA/comments/1wp5668/ |
| Keep volatile tokens (timestamp) out of the prefix | Cache thrash | Put date/time/session-id at the *end*; one early differing token invalidates the whole prefix. | qwen3:4b responses 5s→<2s in one report. | https://community.home-assistant.io/t/improve-local-llm-performance-with-llama-cpp-and-custom-conversation/935476 |
| Cache the repeated tool-schema system prompt | Schema re-processing | Prefix-KV-cache the (minimized) tool-schema system prompt — "biggest speed win locally." | Avoids re-ingesting the schema every round. | https://posttrainllm.com/docs/learn/small-model-tool-calling-playbook/ |

### CPU-specific inference (2026)

| Technique | Problem it solves | How | Expected impact | Source |
|---|---|---|---|---|
| Speculative decoding — **skip for 1–3B targets** | Slow generation | Draft model only pays off when draft ≫ faster (5–10× size ratio). 0.5B draft for 1.5B target was 1.5–2.5× *slower* on CPU. | Avoid a pessimization; not worth it at 1.7B. Use only with a large target. | https://aifrontierpost.com/articles/speculative-decoding-llamacpp-tutorial/ |
| N-gram / MTP speculation | Repetitive output (reasoning, code) | `--spec-type ngram-mod` / `draft-mtp` draft from history; no separate model. | +3.5% avg, +14% coding/long-gen; MTP+ngram +73% on a 35B MoE. | https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md |
| Quantization is NOT the speed lever | Chasing tok/s via quant | At 1–3B decode is memory-bandwidth-bound; Q3→Q6 changes tok/s by single digits. Real knobs: prompt length, `max_tokens`, Flash Attention, `n_ctx`. | Redirect effort from quant to prompt/round reduction. | https://edgevox.nrl.ai/documentation/reports/slm-tool-calling-benchmark |
| Flash Attention (`-fa on`) + 8 threads | CPU throughput | Already enabled locally; standard CPU acceleration. | Baseline acceleration; keep on. | https://github.com/ggml-org/llama.cpp/blob/HEAD/tools/server/README.md |
| Warmup request before timing | Cold-cache 67% apparent slowdown | One "say hello" forces mmap pages into RAM before measurement/serving. | Removes a 67% cold-start artifact; methodology fix. | https://blog.cuttle.af/optimizing-local-llms-complete-benchmark/ |
| MoE / NUMA-aware serving | CPU scaling | MoE 2.4× faster than dense at same RAM; NUMA-node-as-device 2.84× decode scaling on multi-socket. | Big CPU wins if hardware allows (not on a single ThinkPad). | https://blog.cuttle.af/optimizing-local-llms-complete-benchmark/ |

### Tool-call format

| Technique | Problem it solves | How | Expected impact | Source |
|---|---|---|---|---|
| Hermes / ChatML `<tool_call>{…}</tool_call>` | Parser coverage | Native format for Hermes 2/3, Qwen2.5/3; recovered by `hermes`/`qwen25` detectors; llama.cpp autoparser handles it via `--jinja`. | Widely supported; matches the Hermes LoRA's native format. | https://edgevox.nrl.ai/documentation/reports/slm-tool-calling-benchmark |
| Tool-call specialists (if model swap allowed) | Weak native tool accuracy | granite-4.0-1b (BFCL ~50, Apache-2.0), xLAM-2, ToolACE-2, Functionary lead small-model BFCL. | Better reliability than a generalist 1.7B at same size. | https://edgevox.nrl.ai/documentation/reports/slm-tool-calling-benchmark |
| 4-bit hurts FC disproportionately | Silent accuracy loss | Tool selection + arg fidelity degrade faster than general tasks at Q4; prefer 8-bit/AWQ/QAT and BFCL-validate. | Consider Q8 for the tool-caller if accuracy is the bottleneck (trade vs speed). | https://posttrainllm.com/docs/learn/small-model-tool-calling-playbook/ |

## Top 5 to try first

1. **Fix the reasoning cap first (biggest, cheapest win):** raise `max_tokens` to ≥512 (eval
   already uses 400) and add `--reasoning-budget 0`/`enable_thinking:false` for tool turns so
   the answer lands in `content`. Add `reasoning_control`+`reasoning_end` as a safety valve.
   (discussion/20931, server README, PR#13771)
2. **Cache the minimized tool-schema prefix + turn on `--cache-reuse 256` and raise `-cram`:**
   91.7% TTFT cut on long prompts; schema prefix cached once instead of re-ingested per round.
   (tiffena, 7minai, reddit cram, posttrainllm)
3. **Tool RAG / hard-cap the toolset (~20 tools):** cut the 19k-token schema to ~1.4k and stop
   hallucinated tool names; qwen3-1.7B went 107→20 tools for 19/20 correct.
   (TinyAgent arxiv 2409.00608, speakeasy less-is-more)
4. **Force a structured first hop with a GBNF/tool grammar, then release (`required_first_hop`)
   + lazy grammar:** guarantees a valid call on hop 0, kills malformed-JSON/wrong-name, and
   avoids the constraint tax by letting reasoning run as free text first.
   (edgevox tool-calling, codeloopback, posttrainllm)
5. **Cut rounds with parallel tool calls + loop/schema-retry hardening:** emit independent
   calls in one turn (≥2× faster), fingerprint loops, one schema-retry per tool. Skip
   speculative decoding at 1.7B (it's a pessimization on CPU).
   (augmentcode, smolagents PR#1412, edgevox benchmark, aifrontierpost)