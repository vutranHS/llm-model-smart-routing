#!/usr/bin/env python3
"""Standalone query classifier (ONNX).

Predicts:
  scene:      office | software | design | media | research | other
  difficulty: easy | medium | hard

Weights: query-classifier/model_fp16.onnx
  Run ./fetch_classifier.sh if missing (GitHub Release classifier-v1).

Usage:
  python classify.py "fix this python bug in my repo"
  python classify.py --json "draft a PPT about Q3 results"
  python classify.py -f prompts.txt
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

# Weights live next to this script (./query-classifier/). No external fallback.
DEFAULT_DIR = Path(__file__).resolve().parent / "query-classifier"

SCENES = ["office", "software", "design", "media", "research", "other"]
DIFFICULTIES = ["easy", "medium", "hard"]


def pick_model(scene: str, difficulty: str) -> str:
    """Heuristic label only — real routing lives in smart_proxy / codex_smart_proxy."""
    if difficulty == "hard":
        return "mimo-x-pro-preview"
    if difficulty == "medium":
        return "mimo-x-pro-preview" if scene in ("software", "design", "research") else "mimo-x-flash-preview"
    return "mimo-x-flash-preview"


class QueryClassifier:
    def __init__(self, model_dir: Path = DEFAULT_DIR, max_length: int = 256):
        self.model_dir = Path(model_dir)
        self.max_length = max_length
        self.tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer.json"))
        # Match the preprocessing used to validate the exported model.
        self.tokenizer.no_truncation()
        self.session = ort.InferenceSession(
            str(self.model_dir / "model_fp16.onnx"),
            providers=["CPUExecutionProvider"],
        )

    def classify(self, texts: list[str]) -> list[dict]:
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return []
        encodings = [
            self.tokenizer.encode(unicodedata.normalize("NFC", text))
            for text in texts
        ]
        token_rows = []
        for encoding in encodings:
            ids = encoding.ids
            if len(ids) > self.max_length:
                head = self.max_length // 2
                tail = self.max_length - head
                ids = ids[:head] + ids[-tail:]
            token_rows.append(ids)
        max_len = max(len(ids) for ids in token_rows)
        input_ids = np.zeros((len(token_rows), max_len), dtype=np.int64)
        attention_mask = np.zeros((len(token_rows), max_len), dtype=np.int64)
        for i, ids in enumerate(token_rows):
            input_ids[i, :len(ids)] = ids
            attention_mask[i, :len(ids)] = 1

        scene_probs, diff_probs = self.session.run(
            ["scene_probs", "difficulty_probs"],
            {"input_ids": input_ids, "attention_mask": attention_mask},
        )

        out = []
        for i, text in enumerate(texts):
            sp = scene_probs[i]
            dp = diff_probs[i]
            scene_idx = int(sp.argmax())
            diff_idx = int(dp.argmax())
            scene = SCENES[scene_idx]
            difficulty = DIFFICULTIES[diff_idx]
            out.append(
                {
                    "text": text,
                    "scene": scene,
                    "scene_prob": float(sp[scene_idx]),
                    "scene_all": {SCENES[j]: round(float(sp[j]), 4) for j in range(len(SCENES))},
                    "difficulty": difficulty,
                    "difficulty_prob": float(dp[diff_idx]),
                    "difficulty_all": {
                        DIFFICULTIES[j]: round(float(dp[j]), 4) for j in range(len(DIFFICULTIES))
                    },
                    "suggested_model": pick_model(scene, difficulty),
                }
            )
        return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Local ONNX query classifier (scene + difficulty)")
    p.add_argument("text", nargs="*", help="Text(s) to classify")
    p.add_argument("-f", "--file", help="Read lines from file")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--dir", default=str(DEFAULT_DIR), help="Classifier asset dir")
    args = p.parse_args(argv)

    texts = list(args.text or [])
    if args.file:
        texts.extend(
            line.strip()
            for line in Path(args.file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    if not texts:
        p.error("provide text args or -f file")

    clf = QueryClassifier(Path(args.dir))
    results = clf.classify(texts)

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    for r in results:
        print(f"text: {r['text']}")
        print(
            f"  scene:      {r['scene']:10} ({r['scene_prob']:.3f})  "
            + " ".join(f"{k}={v:.2f}" for k, v in r["scene_all"].items())
        )
        print(
            f"  difficulty: {r['difficulty']:10} ({r['difficulty_prob']:.3f})  "
            + " ".join(f"{k}={v:.2f}" for k, v in r["difficulty_all"].items())
        )
        print(f"  suggest:    {r['suggested_model']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
