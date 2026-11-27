#!/usr/bin/env bash
# Adaptive launcher for LFM3-1.7B + Harbor/Hermes LoRA via llama.cpp.
# Works on Linux x86_64 / aarch64 (RPi) and macOS (Metal). CPU or GPU.
# Models are looked up in: $HERMES_MODELS_DIR, <repo>/models, ~/models/lfm3-hermes.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8080}"
CTX="${CTX:-65536}"
ALIAS="${ALIAS:-lfm3-harbor-hermes}"

# --- locate models ---
# Prefer the merged (LoRA baked in) Q4_K_M GGUF: no runtime --lora, no CPU_REPACK
# fallback (136 warnings on the runtime-LoRA path), ~2.2x faster prefill on CPU.
# Fall back to base + runtime LoRA if the merged GGUF is not present.
MODEL_DIR="${HERMES_MODELS_DIR:-}"
if [ -z "$MODEL_DIR" ]; then
  for d in "$DIR/models" "$HOME/models/lfm3-hermes"; do
    [ -f "$d/LFM3-1.7B-Q4_K_M.gguf" ] && MODEL_DIR="$d" && break
  done
fi
MERGED="$MODEL_DIR/LFM3-1.7B-hermes-merged-Q4_K_M.gguf"
BASE="${BASE:-$MODEL_DIR/LFM3-1.7B-Q4_K_M.gguf}"
LORA="${LORA:-$MODEL_DIR/LFM3-1.7B-harbor-hermes-lora-F16.gguf}"
LORA_ARGS=()
if [ -z "${BASE_OVERRIDE:-}" ] && [ -f "$MERGED" ]; then
  BASE="$MERGED"                            # merged: single GGUF, LoRA baked in
else
  LORA_ARGS=(--lora "$LORA")                # fallback: runtime LoRA
fi

# --- locate llama-server ---
find_bin() {
  for c in \
    "$HOME/models/llama-build/build-native/bin/llama-server" \
    "$DIR/../llama-build/build-native/bin/llama-server" \
    "$DIR/../llama-build/build/bin/llama-server" \
    "$HOME/models/llama-build/build/bin/llama-server" \
    "$HOME/.local/llama.cpp-bin"/*/llama-server \
    "$(command -v llama-server || true)"; do
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}
BIN="${LLAMA_SERVER:-$(find_bin || true)}"
# LLAMA_SERVER may be a directory or a binary; normalize to the binary path.
if [ -d "$BIN" ]; then BIN="$BIN/llama-server"; fi
[ -x "$BIN" ] || { echo "ERROR: llama-server not found. Run ./setup.sh first." >&2; exit 1; }
[ -f "$BASE" ] || { echo "ERROR: base model not found: $BASE (run ./setup.sh)" >&2; exit 1; }
if [ ${#LORA_ARGS[@]} -gt 0 ]; then
  [ -f "$LORA" ] || { echo "ERROR: LoRA not found: $LORA (run ./setup.sh)" >&2; exit 1; }
fi

# --- platform flags ---
OS="$(uname -s)"; ARCH="$(uname -m)"
NPROC="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)"
GPU_ARGS=(); KV_ARGS=(); THREAD_ARGS=(-t "$NPROC")
if [ "$OS" = "Darwin" ]; then
  GPU_ARGS=(-ngl 99)                       # Metal on Apple Silicon
elif command -v nvidia-smi >/dev/null 2>&1; then
  GPU_ARGS=(-ngl 99)                       # CUDA if an NVIDIA GPU is present
else
  GPU_ARGS=(-ngl 0)                        # CPU
  # 4 physical cores for generation (bandwidth); 8 threads for prompt ingest (compute).
  THREAD_ARGS=(-t 4 -tb 8 -b 2048 -ub 512)
fi

echo "platform=$OS/$ARCH threads=$NPROC bin=$BIN"
echo "model=$BASE lora_args=[${LORA_ARGS[*]:-none}]"
exec "$BIN" \
  -m "$BASE" "${LORA_ARGS[@]}" \
  -c "$CTX" --host 127.0.0.1 --port "$PORT" --alias "$ALIAS" \
  "${GPU_ARGS[@]}" "${THREAD_ARGS[@]}" -fa on "${KV_ARGS[@]}" \
  --load-mode mmap \
  --cache-prompt --ctx-checkpoints 16 --checkpoint-min-step 256 -cram 1024 \
  --parallel 1 \
  --reasoning-format deepseek --reasoning-budget 96