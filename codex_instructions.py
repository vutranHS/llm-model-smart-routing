"""Opt-in, checksum-pinned gpt-instruct prompts for the Codex proxy."""
from __future__ import annotations

import hashlib
import io
import urllib.request
import zipfile
from pathlib import Path

SOURCE = "https://raw.githubusercontent.com/MDX-Tom/gpt-instruct/0ad8ec58e1989f4a058e01ce4e15cf226e8067bf"
CACHE = Path(__file__).resolve().parent / ".codex_instructions"
PROMPTS = {
    "astra": ("gpt-6-astra-v1", "gpt-6-astra-v1.md",
              "054edb6fa8a6edd2d144c8582756df3179a85481bcb6696d8b730177521b1de1"),
    "sol": ("gpt-5.6-sol-v45", "gpt-5.6-sol-unrestricted-v45.md",
            "c86c2c6d20a4d1155d87422f485eb37b77539132270918c002b5d8237a5adf54"),
}


def load_instructions(directory: Path = CACHE, *, download: bool = False) -> dict[str, str]:
    prompts = {}
    for tier, (name, member, digest) in PROMPTS.items():
        path = directory / f"{name}.zip"
        missing = not path.exists()
        if missing and download:
            with urllib.request.urlopen(f"{SOURCE}/{name}.zip", timeout=30) as response:
                data = response.read(1_000_001)
        else:
            data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"instruction checksum mismatch: {path}; remove it and retry setup")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            prompt = archive.read(member).decode("utf-8").strip()
        if not prompt:
            raise ValueError(f"empty instructions: {path}")
        if missing:
            directory.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        prompts[tier] = prompt
    return prompts


def apply_instructions(body: dict, model: str, prompts: dict[str, str]) -> None:
    """Replace only exact bundled prompts; retain unrelated client instructions."""
    if model not in prompts:
        return
    instructions = body.get("instructions") or ""
    if not isinstance(instructions, str):
        raise ValueError("instructions must be a string when model instructions are enabled")

    def clean(text: str) -> str:
        for prompt in set(prompts.values()):
            text = text.replace(prompt, "")
        return text

    instructions = clean(instructions).strip()
    body["instructions"] = "\n\n".join(filter(None, (instructions, prompts[model])))
    # Some clients place model instructions in developer/system input messages.
    if isinstance(body.get("input"), list):
        items = []
        for item in body["input"]:
            if not isinstance(item, dict) or item.get("role") not in ("developer", "system"):
                items.append(item)
                continue
            item = dict(item)
            content = item.get("content")
            if isinstance(content, str):
                item["content"] = clean(content)
                if content and not item["content"].strip():
                    continue
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        part = {**part, "text": clean(part["text"])}
                        if not part["text"].strip():
                            continue
                    parts.append(part)
                item["content"] = parts
                if content and not parts:
                    continue
            items.append(item)
        body["input"] = items
