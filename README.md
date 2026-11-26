# local-hermes

Run a small local agent (**LFM3-1.7B + Harbor/Hermes LoRA**) with the
[Hermes Agent](https://github.com/NousResearch/hermes-agent) harness, fully local
(CPU or GPU), with a local pt-BR STT (Parakeet) and a small eval harness.

Tested on a CPU-only ThinkPad (Intel, 8 threads, 23 GB). Adaptive to Linux
x86_64 / aarch64 (RPi 5) and macOS (Apple Silicon), CPU or CUDA/Metal.

## Quick start (one command on a fresh machine)

```sh
git clone <this-repo> && cd local-hermes
./setup.sh            # installs deps, builds llama.cpp, downloads models, wires Hermes
./serve.sh            # starts the model server on 127.0.0.1:8080
# in another shell:
hermes chat -q "oi"
```

`setup.sh` is idempotent. It may ask for `sudo` to install system packages
(`cmake`, `build-essential`, `libopenblas-dev`, `python3-venv`, `ffmpeg`).

## What's inside

| Path | Purpose |
|---|---|
| `setup.sh` | Full bootstrap (deps → llama.cpp → models → STT → Hermes config). |
| `serve.sh` | Adaptive llama.cpp launcher (CPU/GPU, x64/ARM/macOS). |
| `eval/run_eval.py`, `eval/prompts.jsonl` | Small text eval (model/agent/telegram modes). |
| `stt/transcribe.sh` | Local pt-BR STT wrapper (Parakeet ONNX, Hermes `local_command`). |
| `research/*.md` | Research reports + a deep-research prompt. |
| `LEARNINGS.md`, `EVAL_PLAN.md` | Hard-won facts and the eval→improve loop log. |

## Models (downloaded by setup into `models/`)
- Base: `LiquidAI/LFM3-1.7B-GGUF` → `LFM3-1.7B-Q4_K_M.gguf` (~1.1 GB)
- Adapter: `harness-hub/LFM3-1.7B-hermes-GGUF` → `...-lora-F16.gguf` (~63 MB)

## Usage

```sh
./serve.sh                          # server on :8080 (alias lfm3-harbor-hermes)
hermes chat -q "check disk usage"   # full agent (terminal/web tools)
python3 eval/run_eval.py --tier 1 --mode model --pause-gateway   # quick eval
python3 eval/run_eval.py --tier 2 --mode agent --only disk,httpd --pause-gateway
```

## Local STT (pt-BR)
`setup.sh` creates `stt-venv` with `onnx-asr`. Wire Hermes (also done by setup):
```sh
# ~/.hermes/.env
HERMES_LOCAL_STT_COMMAND=/path/to/local-hermes/stt/transcribe.sh {input_path} {output_dir}
HERMES_LOCAL_STT_LANGUAGE=pt
# ~/.hermes/config.yaml  ->  stt: { enabled: true, provider: local_command, local: { language: pt } }
```

## Revert Hermes config
`setup.sh` backs up `~/.hermes/config.yaml` to `config.yaml.bak.local-hermes-*`.
Restore it to undo the wiring.

## Performance notes
CPU-only ~20–25 tok/s prompt, ~5 tok/s gen. Single-round tool tasks ≈ 150 s;
multi-round tasks need free RAM (swap thrash tanks pp to ~5 tok/s). Key wins are in
`LEARNINGS.md` (hybrid-checkpoint cache fix, thread split, reasoning budget). Hermes
requires ≥64k model context.

## Roadmap / contributing
See `LEARNINGS.md` → "PENDING improvements" for the next experiments (LoRA merge,
native build, Q4_0 A/B, Vulkan, eval round-gating). PRs welcome — keep changes
documented in `LEARNINGS.md`/`EVAL_PLAN.md` and cite sources in `research/`.

## License
Code: MIT (see `LICENSE`). Models follow their upstream licenses (LFM: lfm1.0; Parakeet: cc-by-4.0).