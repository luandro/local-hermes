#!/usr/bin/env bash
# Hermes local_command STT wrapper — Parakeet TDT 0.6B v3 (pt-BR, ONNX int8).
# Usage: transcribe.sh <input_wav> <output_dir>  -> writes <output_dir>/transcript.txt
set -euo pipefail
IN="$1"; OUT="$2"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/stt-venv/bin/python"
MODEL="${PARAKEET_MODEL:-alefiury/parakeet-tdt-0.6b-v3-ptBR-TAGARELA-onnx}"
"$PY" - "$IN" "$OUT" "$MODEL" <<'PYEOF'
import sys, onnx_asr
inp, outdir, model_id = sys.argv[1], sys.argv[2], sys.argv[3]
model = onnx_asr.load_model(model_id, quantization="int8")
res = model.recognize(inp, language="pt")
text = (getattr(res, "text", None) or str(res)).strip()
open(outdir + "/transcript.txt", "w", encoding="utf-8").write(text)
PYEOF