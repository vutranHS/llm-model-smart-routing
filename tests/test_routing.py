import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault("onnxruntime", types.SimpleNamespace(InferenceSession=object))
sys.modules.setdefault("tokenizers", types.SimpleNamespace(Tokenizer=object))

import codex_smart_proxy
import smart_proxy
import smartcodex


class FakeClassifier:
    def classify(self, texts):
        return [{"scene": "software", "scene_prob": 0.9,
                 "difficulty": "hard", "difficulty_prob": 0.8}]


class RoutingTests(unittest.TestCase):
    def test_prompt_preview_is_opt_in(self):
        proxy = smart_proxy.Proxy(
            classifier=FakeClassifier(), upstream="https://example.test",
            upstream_key=None, fable="fable", opus5="opus5", opus48="opus48",
            sonnet="sonnet", log_path=None,
        )
        _, info = proxy.classify_and_route({
            "messages": [{"role": "user", "content": "a sensitive prompt"}]
        })
        self.assertNotIn("text_preview", info)

    def test_codex_prompt_preview_is_opt_in(self):
        proxy = codex_smart_proxy.Proxy(
            classifier=FakeClassifier(), upstream="https://example.test",
            models={"astra": "a", "sol": "s", "terra": "t", "luna": "l"},
            log_path=None, safe_tokens=200_000,
        )
        _, _, info = proxy.route({"input": "a sensitive prompt"})
        self.assertNotIn("text_preview", info)

    def test_codex_preserves_original_env_key(self):
        config = """model_provider = "custom"

[model_providers.custom]
base_url = "https://example.test/v1"
env_key = "CUSTOM_API_KEY"
wire_api = "responses"
"""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.toml"
            state_path = Path(tmp) / "state.json"
            config_path.write_text(config)
            with patch.object(smartcodex, "CONFIG", config_path), \
                    patch.object(smartcodex, "STATE", state_path):
                smartcodex.patch_config()
                smartcodex.patch_config()
            updated = config_path.read_text()
            state = json.loads(state_path.read_text())
            self.assertIn('env_key = "CUSTOM_API_KEY"', updated)
            self.assertEqual(state["original_env_key"], "CUSTOM_API_KEY")
            self.assertEqual(updated.count("[model_providers.smart]"), 1)

    def test_codex_fallback_uses_terra_medium(self):
        class BrokenClassifier:
            def classify(self, texts):
                raise RuntimeError("classifier unavailable")

        proxy = codex_smart_proxy.Proxy(
            classifier=BrokenClassifier(), upstream="https://example.test",
            models={"astra": "a", "sol": "s", "terra": "t"}, log_path=None, safe_tokens=200_000,
        )
        model, effort, _ = proxy.route({"input": "route this"})
        self.assertEqual((model, effort), ("t", "medium"))


if __name__ == "__main__":
    unittest.main()
