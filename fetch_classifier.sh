#!/usr/bin/env bash
# Copy ONNX classifier weights into ./query-classifier/
# GitHub 100MB/file limit — model_fp16.onnx (~150MB) is not in the repo.
set -euo pipefail

DEST="$(cd "$(dirname "$0")" && pwd)/query-classifier"
MODEL="$DEST/model_fp16.onnx"

if [[ -f "$MODEL" ]]; then
  echo "already present: $MODEL ($(du -h "$MODEL" | cut -f1))"
  exit 0
fi

# 1) MiMo Desktop install (macOS)
MIMO="$HOME/Library/Application Support/Xiaomi MiMo AI/on-demand-assets/query-classifier/model_fp16.onnx"
if [[ -f "$MIMO" ]]; then
  echo "copying from MiMo Desktop..."
  cp "$MIMO" "$MODEL"
  echo "ok: $MODEL"
  exit 0
fi

# 2) Optional download URL (set CLASSIFIER_URL)
if [[ -n "${CLASSIFIER_URL:-}" ]]; then
  echo "downloading from CLASSIFIER_URL..."
  curl -L --fail -o "$MODEL" "$CLASSIFIER_URL"
  echo "ok: $MODEL"
  exit 0
fi

cat >&2 <<EOF
model_fp16.onnx not found.

Place it at:
  $MODEL

Sources:
  1) Copy from MiMo Desktop (if installed):
     "$MIMO"
  2) Download yourself and set:
     CLASSIFIER_URL='https://...' ./fetch_classifier.sh

Manifest sha256 (fp16): 30a68d6e5e95e380b8525c7a2cab035859e779baf526ae0e7b81b4e7d5a82e10
EOF
exit 1
