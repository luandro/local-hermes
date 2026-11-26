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
MODEL_DIR="${HERMES_MODELS_DIR:-}"
if [ -z "$MODEL_DIR" ]; then
  for d in "$DIR/models" "$HOME/models/lfm3-hermes"; do
    [ -f "$d/LFM3-1.7B-Q4_K_M.gguf" ] && MODEL_DIR="$d" && break
  done
fi
BASE="${BASE:-$MODEL_DIR/LFM3-1.7B-Q4_K_M.gguf}"
LORA="${LORA:-$MODEL_DIR/LFM3-1.7B-harbor-hermes-lora-F16.gguf}"

# --- locate llama-server ---
find_bin() {
  for c in \
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
[ -f "$LORA" ] || { echo "ERROR: LoRA not found: $LORA (run ./setup.sh)" >&2; exit 1; }

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
  THREAD_ARGS=(-t "$NPROC" -ub 1024)
  KV_ARGS=(-ctk q8_0 -ctv q8_0)            # KV quant (needs -fa on)
fi

echo "platform=$OS/$ARCH threads=$NPROC bin=$BIN"
echo "model=$BASE"
exec "$BIN" \
  -m "$BASE" --lora "$LORA" \
  -c "$CTX" --host 127.0.0.1 --port "$PORT" --alias "$ALIAS" \
  "${GPU_ARGS[@]}" "${THREAD_ARGS[@]}" -fa on "${KV_ARGS[@]}" \
  --no-reasoning-preserve -cram 4096 --parallel 1 \
  --spec-type ngram-simple --spec-ngram-simple-size-m 32