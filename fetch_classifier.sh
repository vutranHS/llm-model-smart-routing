#!/usr/bin/env bash
# Fetch ONNX classifier weights into ./query-classifier/
# Weights ship as a GitHub Release asset (not in git).
set -euo pipefail

DEST="$(cd "$(dirname "$0")" && pwd)/query-classifier"
MODEL="$DEST/model_fp16.onnx"
DEFAULT_URL="https://github.com/vutranHS/llm-model-smart-routing/releases/download/classifier-v1/model_fp16.onnx"
EXPECTED_SHA="30a68d6e5e95e380b8525c7a2cab035859e779baf526ae0e7b81b4e7d5a82e10"

mkdir -p "$DEST"

if [[ -f "$MODEL" ]]; then
  echo "already present: $MODEL ($(du -h "$MODEL" | cut -f1))"
  exit 0
fi

URL="${CLASSIFIER_URL:-$DEFAULT_URL}"
echo "downloading $URL ..."
if command -v curl >/dev/null 2>&1; then
  curl -L --fail --progress-bar -o "$MODEL" "$URL"
elif command -v wget >/dev/null 2>&1; then
  wget -O "$MODEL" "$URL"
else
  echo "ERROR: need curl or wget" >&2
  exit 1
fi

if command -v shasum >/dev/null 2>&1; then
  actual=$(shasum -a 256 "$MODEL" | awk '{print $1}')
elif command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$MODEL" | awk '{print $1}')
else
  actual=""
fi

if [[ -n "$actual" && "$actual" != "$EXPECTED_SHA" ]]; then
  echo "ERROR: sha256 mismatch" >&2
  echo "  expected $EXPECTED_SHA" >&2
  echo "  actual   $actual" >&2
  rm -f "$MODEL"
  exit 1
fi

echo "ok: $MODEL ($(du -h "$MODEL" | cut -f1))${actual:+  sha256 verified}"
