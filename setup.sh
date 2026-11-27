#!/usr/bin/env bash
# One-command bootstrap for local-hermes on a fresh machine.
# Adaptive: Linux x86_64 / aarch64 (RPi 5) and macOS (Apple Silicon). CPU or GPU.
# Installs deps, builds llama.cpp, downloads the GGUF models, sets up local STT,
# and wires ~/.hermes/config.yaml (with a backup). Idempotent: re-running is safe.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS="$DIR/models"
LLAMA_SRC="$HOME/models/llama-build"
TAG="${LLAMA_TAG:-b12686}"
BASE_REPO="LiquidAI/LFM3-1.7B-GGUF"
LORA_REPO="harness-hub/LFM3-1.7B-hermes-GGUF"
BASE_FILE="LFM3-1.7B-Q4_K_M.gguf"
LORA_FILE="LFM3-1.7B-harbor-hermes-lora-F16.gguf"
OS="$(uname -s)"; ARCH="$(uname -m)"
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

log(){ printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }

# ---------- 1. system dependencies ----------
install_deps(){
  log "installing system dependencies ($OS/$ARCH)"
  if command -v apt-get >/dev/null; then
    $SUDO apt-get update -y
    $SUDO apt-get install -y git curl cmake build-essential libopenblas-dev python3 python3-venv python3-pip ffmpeg
  elif command -v dnf >/dev/null; then
    $SUDO dnf install -y git curl cmake gcc gcc-c++ make openblas-devel python3 python3-pip ffmpeg
  elif command -v pacman >/dev/null; then
    $SUDO pacman -Sy --noconfirm git curl cmake base-devel openblas python python-pip ffmpeg
  elif [ "$OS" = "Darwin" ]; then
    command -v brew >/dev/null || { echo "install Homebrew first: https://brew.sh" >&2; exit 1; }
    brew install cmake openblas python@3 ffmpeg
  else
    warn "unknown package manager; install: git curl cmake build-essential libopenblas-dev python3-venv ffmpeg"
  fi
}
command -v cmake >/dev/null || install_deps

# ---------- 2. build llama.cpp (native CPU, embedded SGEMM; Metal on macOS) ----------
build_llama(){
  if [ -x "$LLAMA_SRC/build/bin/llama-server" ]; then log "llama.cpp already built"; return; fi
  log "building llama.cpp $TAG"
  mkdir -p "$LLAMA_SRC"; cd "$LLAMA_SRC"
  if [ ! -f src.tar.gz ]; then
    curl -sL -o src.tar.gz "https://github.com/ggml-org/llama.cpp/archive/refs/tags/$TAG.tar.gz"
  fi
  rm -rf ./*; tar -xzf src.tar.gz --strip-components=1
  # Native CPU build (AVX2/FMA on x86_64, NEON on aarch64) with the embedded
  # llamafile SGEMM beats OpenBLAS on this CPU: pp2048 +19%, tg +2% (3/3 wins,
  # llama-bench -p 2048 -n 128 -t 4, 2026-11-27). GGML_NATIVE honours -march=native
  # on Linux x64/ARM; on macOS GGML_METAL defaults ON so the Metal path is kept.
  # OpenBLAS is no longer needed (BLAS=OFF); the embedded SGEMM has real AVX2/FMA.
  cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DGGML_BLAS=OFF
  cmake --build build --config Release -j"$(nproc 2>/dev/null || sysctl -n hw.ncpu)"
}

# ---------- 3. models ----------
fetch(){ # url dest
  if [ -f "$2" ] && [ "$(stat -c%s "$2" 2>/dev/null || stat -f%z "$2")" -gt 1000000 ]; then log "have $(basename "$2")"; return; fi
  log "downloading $(basename "$2")"; curl -L --http1.1 -C - --retry 10 --retry-delay 5 --retry-all-errors -o "$2" "$1"
}
get_models(){
  mkdir -p "$MODELS"
  fetch "https://huggingface.co/$BASE_REPO/resolve/main/$BASE_FILE" "$MODELS/$BASE_FILE"
  fetch "https://huggingface.co/$LORA_REPO/resolve/main/$LORA_FILE" "$MODELS/$LORA_FILE"
}

# ---------- 4. local STT (Parakeet pt-BR, optional) ----------
setup_stt(){
  local STT="$DIR/stt-venv"
  if [ -d "$STT" ]; then log "STT venv exists"; return; fi
  log "creating STT venv (onnx-asr)"
  python3 -m venv "$STT" || { warn "python3-venv missing; skipping STT"; return; }
  "$STT/bin/pip" install -q "onnx-asr[cpu,hub]" || warn "onnx-asr install failed; STT disabled"
}

# ---------- 5. wire Hermes config ----------
wire_hermes(){
  local CFG="$HOME/.hermes/config.yaml"
  [ -f "$CFG" ] || { warn "no $CFG; skipping Hermes wiring (create it with 'hermes setup')"; return; }
  cp "$CFG" "$CFG.bak.local-hermes-$(date +%Y%m%d_%H%M%S)"
  HERMES_CFG="$CFG" python3 - <<'PY'
import os, sys
try:
    import yaml
except Exception:
    print("[warn] PyYAML not available; skipping config merge (wire manually per README)"); sys.exit(0)
p=os.environ["HERMES_CFG"]; c=yaml.safe_load(open(p)) or {}
m=c.setdefault("model",{})
m.update({"default":"lfm3-harbor-hermes","provider":"custom",
          "base_url":"http://127.0.0.1:8080/v1","api_key":"dummy","api_mode":"chat_completions"})
c["platform_toolsets"]={"cli":["terminal"],"telegram":["terminal","file","web"]}
# Lean prompt: no plugins (portfolio-manager alone = 40 tools). On CPU, prompt
# SIZE dominates wall-time; the eval path (disk/selfh) needs only `terminal`.
c.setdefault("plugins",{})["enabled"]=[]
stt=c.setdefault("stt",{}); stt.update({"enabled":True,"provider":"local_command",
    "local":{"model":"parakeet-ptbr","language":"pt"}})
yaml.safe_dump(c, open(p,"w"), sort_keys=False, allow_unicode=True)
print("[ok] wired model + toolsets + stt into", p)
PY
}

log "platform: $OS/$ARCH"
build_llama
get_models
setup_stt
wire_hermes
log "done. Start the server:  ./serve.sh   (then: hermes chat -q \"oi\")"
log "models in $MODELS ; llama-server in $LLAMA_SRC/build/bin"