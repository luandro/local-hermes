# local-hermes research: small decision models (routing) + deterministic-task shortcuts

Research-only survey. No code/config touched. Host swap-thrashed → no benchmarks run; every number below is **as reported by the cited primary source, on that source's hardware**, not measured here. "UNVERIFIED" = not confirmed from a primary source.

---

## QUESTION A — Is a small decision model worth it vs the rules router?

### A.1 What Laya-multilingual actually is (primary source)

From the model card (https://huggingface.co/convaiinnovations/laya-multilingual) and family card (https://huggingface.co/convaiinnovations/laya):

| Property | Value | Source |
|---|---|---|
| Backbone | mmBERT-base, 307M, 22 layers, hidden 768, 256k vocab | laya-multilingual card |
| Total params | **322M** (+ decision head: 2 transformer layers, option-marker scorer, act/escalate head) | card |
| Context | 1024 default, up to 8192 with `max_len=8192` | card |
| License | Apache-2.0 (weights + `laya` pkg). Continuous-learning `vegaml-learn` is **PolyForm Noncommercial** | card |
| Languages | 100+ (PT-BR explicitly supported; router sends accent-stripped Latin + BR support text here) | card |
| Headline latency | **32.8 ms / 1 question on a T4 GPU** | card "Speed" table |

**Critical reality check on latency.** The 32.8 ms is a **Tesla T4 GPU**. The card's own production table says `Router(preload=True)` = **"32.8 ms (GPU) / 193–464 ms (CPU)"**. Independent CPU measurements:

- i9-14900HX FP32 eager: **~400 ms / question**; ONNX Runtime **276 ms** (https://debarshidas.dev/writing/laya-decision-latency).
- Nekko (Rust/ONNX) Linux CPU fp32, Ryzen 9 9950X3D: **71 ms (1 q) / 560 ms (10 q)** (https://nekkoagent.com/docs/decision-models).
- Community ONNX i3-1215U (8 vCPU): FP32 1.6 GB **~438 ms**, INT8 403 MB **~171 ms** (https://github.com/samiul000/laya-cpp).
- Community ONNX 4-vCPU cloud box, batch 1: fp32 **~406 ms** single-thread / ~145 ms 4-thread; int8 ~15% faster (https://huggingface.co/yehor-oleksiuk/laya-typed-decisions-onnx).

The ThinkPad i7-8650U (4c/8t, 15 W, AVX2+FMA, no AVX-512/BF16) is **slower per core than every CPU above**. Realistic single-question latency here is **≥400 ms, plausibly 0.5–1 s**, *per routing decision* — and that is before the swap pressure of holding the weights.

### A.2 RAM floor

- FP32 weights: 322M × 4 B ≈ **1.29 GB** (card's ONNX FP32 export measured 1.6 GB incl. graph; yehor fp32 ~1.3–1.7 GB "just for weights").
- FP16: ~646 MB (multilingual, per Nekko).
- INT8 weight-only: ~403 MB (laya-cpp) but **multilingual INT8 is 929 MB — larger than fp16** because the 256k embedding Gather stays fp32, and it **flipped one answer** on a 15-option near-tie (Nekko release notes; laya docs issue #790).
- Laya's own docs: **"INT8 trades real accuracy … do not use it where the calibrated probability or confidence matters"** (https://github.com/NandhaKishorM/laya docs/reference/agent.md). Per-tensor INT8 agreement ~67% (EN) / ~83% (multilingual); per-channel collapses to ~32%/~40%.

On a 23 GB host that **swap-thrashes under the 1.7B model already resident**, adding a resident ~1.3 GB (FP32) decision model is net-negative for the stated goal (reduce memory pressure). INT8's ~0.5–0.9 GB is better but **fails the "don't trust the probability" requirement** that a high-stakes abstention gate depends on.

### A.3 Zero-shot accuracy — the dealbreaker

The card is unusually honest:

- **Ships uncalibrated / over-confident.** Mean confidence 0.75–0.83 vs much lower accuracy; refit temperature per (type, option-count) moves ECE 0.314→0.106 (multilingual). Raw confidences must NOT be thresholded.
- **Near chance on typed-decisions zero-shot: 0.342** (random 0.318, majority 0.461). "Laya is a fast base to specialise, not a zero-shot decision engine."
- MASSIVE intent, 51 langs, 20-way: macro accuracy **0.366** (multilingual). XNLI 14 non-English langs 0.731.
- **`noul` (the binary primitive we'd want) is documented as unreliable** — can return P(true)≈0.5 on clearly positive input (issue #156); `act_probability` carries "no usable signal" (AUROC 0.30, issue #185). The recommended workaround is a 2-option `choice` with neutral keys — i.e. you must re-engineer the question and **still validate on your own data**.
- The good numbers (typed-decisions 0.766) belong to a checkpoint **fine-tuned on that benchmark's own training split**. We have no labelled PT-BR/EN storage-intent dataset → we'd be in the near-chance zero-shot regime until we build one and fine-tune (Kaggle 2×T4 notebook).

**Conclusion A.3:** For a *binary, high-stakes* decision, Laya zero-shot is **not safe** (near-chance + overconfident + unreliable noul). It only becomes viable after (a) building a labelled dataset, (b) fine-tuning, (c) fitting calibration — at which point a far cheaper classifier likely matches it.

### A.4 Lighter alternatives (primary sources)

| Option | RAM | CPU latency (single short string) | PT-BR | Notes / source |
|---|---|---|---|---|
| **Rules (current)** | ~0 | ~µs (regex) | yes (hand-listed) | 100% deterministic, auditable, fail-open. No calibration needed. |
| **TF-IDF + LogisticRegression** (scikit-learn) | model = vectorizer vocab + coefs, typically **single-digit MB** for a few k features; char_wb n-grams handle PT-BR accents | **sub-ms** (sparse dot product) | yes via char n-grams / PT stopwords | LogReg has canonical logit link → **well-calibrated by default**; wrap in `CalibratedClassifierCV(method="sigmoid")` for a guarantee. https://scikit-learn.org/stable/modules/calibration.html |
| **fastText supervised** | **KB–few MB** (even <1 MB after quantization) | **~µs** (bag of n-grams + linear) | yes (train on PT data) | Paper: 2 orders of magnitude smaller than original fastText w/ slight acc loss. https://arxiv.org/abs/1612.03651 . Needs a labelled set. |
| **MiniLM (all-MiniLM-L6-v2 / multilingual-MiniLM)** | ~22M (EN) / ~118M (multilingual) params → **~90 MB / ~470 MB** fp32; ONNX int8 ~¼ | **~5–20 ms** CPU single sentence (UNVERIFIED exact on 8th-gen; order-of-magnitude from sentence-transformers docs) | multilingual variant yes | Embedding + tiny head; needs labelled data; heavier than TF-IDF for a binary task. https://sbert.net |
| **Laya-multilingual** | **~1.3 GB FP32** / ~0.5–0.9 GB INT8 | **≥400 ms** (likely 0.5–1 s on i7-8650U) | yes | Near-chance zero-shot, overconfident, unreliable noul. Only after fine-tune+calibration. |

### A.5 The safety / calibration approach (this is the real answer)

The decision is **asymmetric**: a false "block" on a write request = silent capability loss (worst outcome); a false "fall-through" = we merely pay for one normal LLM turn (mild). So the gate must be tuned for **≥98% recall on the critical class (write/conditional intent must NOT be blocked)**, accepting lower coverage.

Primary-source mechanism — **selective prediction / reject option with a guaranteed-risk threshold**:

- **SGR (Selection with Guaranteed Risk), Geifman & El-Yaniv 2017**: learns a threshold τ such that the error rate on *answered* cases is bounded ≤ r* with high probability 1−δ, maximizing coverage. Exactly the "set risk, maximize coverage" framing we need. Survey: https://dl.acm.org/doi/full/10.1145/3727633 ; SelectiveNet: http://proceedings.mlr.press/v97/geifman19a/geifman19a.pdf
- **Post-hoc calibration** to make the threshold meaningful: Platt/sigmoid or isotonic via `CalibratedClassifierCV`, or temperature scaling (doesn't change argmax, only confidence). https://scikit-learn.org/stable/modules/calibration.html
- **Laya's own guidance** matches: calibrate per (type, option-count); **"Gate on `route`, never on a confidence number"**; use `abstain` (default risk 10%) + conformal `prediction_set` (2+ options ⇒ route to review). This is a clean abstention primitive *if* calibrated — but see A.3, it isn't calibrated out of the box.

**Practical safe design:** any small model must emit a calibrated P(write-intent); **block only if P(read-only) ≥ τ_high AND P(write) ≤ (1−0.98)**; everything else falls through. Choose τ on a held-out set to hit ≥98% critical recall (SGR), then verify ≥95% route accuracy. A model that can't clear both on *our* data is rejected.

### A.6 Evidence of small models for intent routing / tool-shortlisting

- **TinyAgent (arXiv 2409.00608, BAIR)**: fine-tuned **DeBERTa-v3-small** as a 16-way tool classifier; tool recall **0.998**, prompt 2762→1397 tokens (~2× cut), *higher* task success (80.06% vs 78.89% 1.1B). Direct precedent that a **small encoder beats rules/RAG for tool selection**. https://arxiv.org/html/2409.00608v3
- **Tool representation estimation (arXiv 2409.02141)**: DeBERTa-V3 MLC head for tool retrieval. https://arxiv.org/abs/2409.02141
- **LLMCompiler / Gorilla / ToolLLM**: use small/encoder components for planning & retrieval (general literature; not fetched in depth here — flagged as background, not a specific number).
- **PT-BR small-model intent classification**: broad literature exists but **no specific primary number fetched** — UNVERIFIED. Treat as "likely fine with TF-IDF/fastText char n-grams" but must be measured on our data.

### A.7 VERDICT (Question A)

**Rules win for now — but a TF-IDF+LogReg (or fastText) classifier is the right *upgrade path*, not Laya.**

Ranked:

1. **Rules (current W1)** — best RAM (0), latency (µs), auditability, fail-open. Its weakness is coverage/brittleness on paraphrase, not safety. **Keep as the safety floor.**
2. **TF-IDF + LogisticRegression (char_wb n-grams, PT-BR+EN)** — best accuracy-vs-cost *if* rules miss a measurable slice. MB-scale RAM, sub-ms, **well-calibrated by default**, trivial `CalibratedClassifierCV` + SGR threshold for the 98% guarantee. **This is the recommended small-model experiment.**
3. **fastText supervised** — even smaller/faster; needs labelled data; slightly lower accuracy ceiling. Good fallback if sklearn vectorizer RAM is a concern (it won't be).
4. **MiniLM multilingual + head** — only if linear models underfit; ~0.5 GB RAM competes with the swap budget; 5–20 ms CPU.
5. **Laya-multilingual** — **not worth it here.** ~1.3 GB FP32 (or accuracy-unsafe INT8), ≥400 ms–1 s CPU on this host, near-chance zero-shot, overconfident, unreliable `noul`. Only justified after we have a labelled set *and* fine-tune *and* calibrate — by which point #2 is cheaper and sufficient. **Do not adopt Laya for this binary gate.**

Honest bottom line: the plan's own sequencing (R1 rules/BM25 baseline → E10 Laya **only if** traces prove an ambiguous slice rules can't fill) is correct. Add **TF-IDF+LogReg as the R1.5 step before Laya**.

---

## QUESTION B — Other deterministic-task opportunities

Safety test applied: **(1) no side effects, (2) provably read-only, (3) partial-intent trap must fall through.** "Safe?" = safe to auto-answer from a deterministic scan with the same guard.

| Candidate | Exact read-only command | Safe (no side effects)? | Partial-intent trap (must fall through) | Priority |
|---|---|---|---|---|
| **Compose lint/validate** | `docker compose config -q` (validate only, no print); `docker compose config` (render) | **Yes** — parse/resolve/render only, no engine call. `-q` = validate only. | "validate **and fix**", "config **then up**", "lint **and redeploy**" → fall through. `up --dry-run` is **NOT** in the `up` option list (UNVERIFIED as a real flag) — do **not** rely on it; use `config -q`. | **High** |
| **JSON syntax** | `jq empty file.json` (exit 0 = valid) | **Yes** | "validate **and reformat/fix**", "pretty-print **and save**" → fall through (`jq` without `empty` may write with `>`). | **High** |
| **YAML syntax** | `yamllint file.yaml` (read-only linter) | **Yes** (yamllint only reads) | "lint **and auto-fix**" → fall through. | High |
| **systemctl status** | `systemctl status UNIT --no-pager` | **Yes** (read-only) | "status **and restart/enable**" → fall through. | High |
| **journalctl** | `journalctl -u UNIT --no-pager -n 50` | **Mostly** — reads journal; `--no-pager` avoids pager. May seal/rotate only on write ops, not plain read. Treat as read-only. | "logs **and clear/vacuum**" (`journalctl --vacuum`) → fall through. | Med |
| **uptime / free / uname** | `uptime`; `free -h`; `uname -a` | **Yes** (pure read) | "free **and drop caches**", "uname **and update**" → fall through. | Med (bundle with df) |
| **docker ps / images** | `docker ps --format '{{json .}}'`; `docker images` | **Yes** (read-only subset per Docker safety refs) | "ps **and stop/prune**", "images **and rmi**" → fall through. | High |
| **docker stats snapshot** | `docker stats --no-stream` | **Yes** — `--no-stream` pulls first result only, no stream | "stats **and restart**", "stats **and kill**" → fall through. | Med |
| **docker system df** | `docker system df` (`-v` detail) | **Yes** (read-only; pairs with our df/du) | "df **and prune**" → fall through. | **High** (extends W1) |
| **git status / log / diff / remote** | `git --no-optional-locks status --porcelain`; `git log`; `git diff`; `git remote -v` | **Conditional** — plain `git status` **rewrites `.git/index`** (takes `index.lock`). **`--no-optional-locks` (or `GIT_OPTIONAL_LOCKS=0`) makes it read-only.** `git diff` ignores the flag (still may refresh) — prefer `git --no-optional-locks diff` cautiously or `git diff --no-index`. | "status **and commit/add**", "diff **and apply/checkout**" → fall through. | High (with the lock caveat) |
| **ss / netstat** | `ss -ltn` (listening TCP) | **Yes** (read-only) | "ss **and kill/restart service**" → fall through. | Med |
| **curl HEAD probe** | `curl -sS -I --max-time 5 URL` | **Mostly** — `-I` = HEAD, no body; **but** some endpoints have side effects on HEAD (rare) and it is a network egress. Treat as read-only *probe* only. | "curl **and POST/put/delete**", "check **and fix/restart**" → fall through. | Med |
| **find / rg / wc** | `rg --files`; `find … -maxdepth`; `wc -l` | **Yes** (read-only) | "find **and delete/move**", "rg **and replace**" → fall through. | Med |
| **version / package list** | `--version`; `pip list`; `apt list --installed`; `npm ls` | **Yes** (read-only) | "list **and install/upgrade/remove**" → fall through. | Med |
| **terraform validate** | `terraform validate` (needs `terraform init -backend=false`) | **Yes** — docs: "safe to run automatically". `init` writes `.terraform/` though → validate the *config*, not init. | "validate **and apply/plan-destroy**" → fall through. | Med |
| **terraform plan** | `terraform plan -input=false` (no `-out`) | **Mostly** — "does not carry out changes"; but it **does an in-memory refresh** (remote API reads) and may write `.terraform`. Speculative plan is not persisted. | "plan **and apply**", "plan **and destroy**" → fall through. **Never** auto-run `-out` or `apply`. | Low-Med |
| **ansible inventory** | `ansible-inventory --list` | **Yes** (read-only) | "inventory **and run playbook**" → fall through. | Low |
| **Prometheus/health check** | `curl -fsS --max-time 5 http://host/healthz` | **Yes** (GET; `-f` fails on 4xx/5xx) | "check health **and restart/redeploy**" → fall through. | Med |

Notes: `docker compose up --dry-run` appears in blog posts but is **absent from the official `up` options table** fetched — treat as UNVERIFIED; use `docker compose config -q` for validation instead. `terraform plan` is "read-only for infra" but **not side-effect-free for local state/`.terraform`** and does remote reads — gate carefully.

---

## Other deterministic-shortcut mechanisms (beyond `pre_llm_call` block)

Verified against Hermes Event Hooks docs (https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks):

1. **`pre_tool_call` hook (Directive/control).** Fires before every tool; can `{"action":"block","message":...}` (short-circuits tool, returns text to model), `{"action":"approve",...}` (human gate), or `{"action":"modify","args":{...}}` (rewrite args). **Fail-closed on timeout/raise** (blocks the tool). → Use to **auto-answer/skip a deterministic tool** (e.g. intercept `terminal` running `df` and substitute cached scan) or to **rewrite** a command into the read-only variant (e.g. inject `--no-optional-locks` into `git status`). Highly feasible on v0.23.4.
2. **`transform_tool_result` / `transform_terminal_output` (Transform).** Replace a tool result string after the fact → serve a **cached/deterministic answer** for a repeated read-only query (tool-result cache). Feasible.
3. **Tool-result cache (memoization).** No native Hermes cache found in docs (UNVERIFIED). Implement as a plugin keyed on `(tool_name, normalized_args)` with a TTL, returning via `transform_tool_result` or short-circuiting in `pre_tool_call`. Safe only for provably read-only tools.
4. **GBNF / grammar-constrained first hop.** llama.cpp supports `grammar`/`json_schema` and `tool_choice` + grammar on hop 0 then release (sources already in `research/small-model-agents.md`: https://github.com/ggml-org/llama.cpp/blob/HEAD/grammars/README.md ). Forces a *structured* tool call deterministically — but this **still costs one model prefill**; it does not give the zero-inference win that `pre_llm_call` block does. Use for *reliability*, not *speed*.
5. **Hermes Tool Search / progressive disclosure.** Not separately documented in the fetched hooks pages (UNVERIFIED as a distinct feature). The TinyAgent Tool-RAG pattern (DeBERTa classifier, recall 0.998) is the proven way to shortlist tools and cut prompt tokens — a *different* win (prompt size) than W1's *zero-inference* win.
6. **Local OpenAI-compatible proxy.** Our `tool_choice` proxy already failed (added a round). Other proxy jobs that *don't* add a round: **(a) deterministic short-circuit** — answer known read-only intents from the proxy before forwarding (same idea as W1 but at the HTTP layer, model-agnostic); **(b) response cache** keyed on request hash for identical read-only prompts; **(c) request dedup/coalescing**. LiteLLM/llama.cpp-server proxy docs are the reference implementations (UNVERIFIED specifics). The proxy must fail-open to the real server on any miss.

---

## Top 3 recommendations (ranked by value/risk)

1. **Extend W1 with `docker system df` + `df`/`free`/`uptime` bundling (highest value, lowest risk).**
   *Why:* pure read-only, same guard, zero new model, directly cuts the disk/status task class that already costs 25–137 s.
   *Next experiment:* add `docker system df` (and `free -h`, `uptime`) to `scan_storage()` behind the existing partial-intent guard; extend the unit-test corpus with "df **and prune**", "status **and restart**" traps; re-run `test_storage_status.py`.

2. **Build a labelled PT-BR/EN intent set + TF-IDF/LogReg classifier as R1.5 (best accuracy upgrade, still cheap).**
   *Why:* rules are brittle on paraphrase; TF-IDF+LogReg is MB-RAM, sub-ms, well-calibrated, and can be tuned via SGR to ≥98% critical recall. Beats Laya on every axis for this binary task.
   *Next experiment:* harvest the E00 traces + synthetic paraphrases into a labelled set; train `TfidfVectorizer(analyzer="char_wb", ngram_range=(2,5))` + `LogisticRegression`; wrap in `CalibratedClassifierCV(method="sigmoid")`; pick τ by SGR for ≥98% write-recall; require ≥95% route accuracy before replacing/augmenting rules. **Do not** reach for Laya unless this underfits.

3. **Add a `pre_tool_call` read-only rewriter (medium value, medium risk, high leverage).**
   *Why:* auto-injects `--no-optional-locks` for `git status`, `--no-stream` for `docker stats`, `-I`/`--max-time` for curl, and blocks the write-variant — making the *model's own* tool calls safe and cheap without a separate classifier. Fail-closed semantics are a hazard → must be conservative.
   *Next experiment:* a `pre_tool_call` plugin that `modify`s argv for a whitelist of read-only tools (git/docker/curl) and `block`s obvious write variants, with the same partial-intent fall-through; unit-test the modify/block matrix; verify it never touches non-whitelisted tools.

---

## Sources (primary)

- Laya multilingual card: https://huggingface.co/convaiinnovations/laya-multilingual
- Laya family card (speed, calibration, fine-tune, honest limits): https://huggingface.co/convaiinnovations/laya
- Laya ONNX/INT8 export + accuracy caveats: https://github.com/NandhaKishorM/laya (docs/reference/agent.md), https://github.com/NandhaKishorM/laya/blob/main/docs/evals.md
- Laya CPU latency (i9-14900HX, ONNX): https://debarshidas.dev/writing/laya-decision-latency
- Laya C++/ONNX INT8 (i3-1215U): https://github.com/samiul000/laya-cpp
- Laya ONNX int8 (4-vCPU): https://huggingface.co/yehor-oleksiuk/laya-typed-decisions-onnx
- Nekko Laya runtime (Ryzen CPU, int8 flips): https://nekkoagent.com/docs/decision-models
- fastText.zip (compression): https://arxiv.org/abs/1612.03651
- scikit-learn calibration (Platt/isotonic/temperature, CalibratedClassifierCV): https://scikit-learn.org/stable/modules/calibration.html
- Selective prediction / SGR / SelectiveNet: https://dl.acm.org/doi/full/10.1145/3727633 ; http://proceedings.mlr.press/v97/geifman19a/geifman19a.pdf
- TinyAgent Tool-RAG (DeBERTa recall 0.998): https://arxiv.org/html/2409.00608v3 ; https://bair.berkeley.edu/blog/2024/05/29/tiny-agent/
- Tool representation estimation: https://arxiv.org/abs/2409.02141
- Hermes Event Hooks (pre_llm_call / pre_tool_call / transform): https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks
- Hermes Plugins: https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins
- docker compose config: https://docs.docker.com/reference/cli/docker/compose/config/
- docker compose up (no --dry-run in options): https://docs.docker.com/reference/cli/docker/compose/up/
- docker stats --no-stream: https://docs.docker.com/reference/cli/docker/container/stats/
- docker system df: https://docs.docker.com/reference/cli/docker/system/df/
- terraform plan (in-memory refresh, not persisted): https://developer.hashicorp.com/terraform/cli/commands/plan
- terraform validate (safe to auto-run): https://developer.hashicorp.com/terraform/cli/commands/validate
- terraform refresh (modifies state): https://developer.hashicorp.com/terraform/cli/commands/refresh
- git status writes index / --no-optional-locks: https://git-scm.com/docs/git-status/2.17.0 ; https://blog.chaosyn.com/en/posts/git-status-no-optional-locks/ ; Linus thread https://public-inbox.org/git/Pine.LNX.4.64.0702100913020.8424@woody.linux-foundation.org/