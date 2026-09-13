import copy
import hashlib
import io
import json
import sys
import tempfile
import types
import unittest
import zipfile
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.modules.setdefault("onnxruntime", types.SimpleNamespace(InferenceSession=object))
sys.modules.setdefault("tokenizers", types.SimpleNamespace(Tokenizer=object))

import codex_instructions
import codex_smart_proxy
import smartcodex
import stopcodex

PROMPTS = {"gpt-6-astra": "ASTRA PROMPT", "gpt-5.6-sol": "SOL PROMPT"}
CONFIG = '''model_provider = "custom"
model_instructions_file = "./my-custom.md"

[model_providers.custom]
base_url = "https://example.test/v1"
env_key = "CUSTOM_API_KEY"
'''


class InstructionTests(unittest.TestCase):
    def test_replaces_only_bundled_prompts_and_preserves_client_data(self):
        original = {
            "instructions": "CORE\n\nASTRA PROMPT",
            "input": [
                {"role": "developer", "content": "POLICY\nSOL PROMPT"},
                {"role": "system", "content": [{"type": "input_text", "text": "ASTRA PROMPT"}]},
                {"role": "user", "content": "SOL PROMPT"},
                {"type": "function_call_output", "call_id": "1", "output": "ASTRA PROMPT"},
            ],
            "tools": [{"type": "function", "name": "read_file"}],
            "previous_response_id": "resp_123",
        }
        body = copy.deepcopy(original)
        codex_instructions.apply_instructions(body, "gpt-5.6-sol", PROMPTS)
        self.assertEqual(body["instructions"], "CORE\n\nSOL PROMPT")
        self.assertEqual(body["input"][0]["content"], "POLICY\n")
        self.assertEqual(body["input"][1:], original["input"][2:])
        self.assertEqual(body["tools"], original["tools"])
        self.assertEqual(body["previous_response_id"], "resp_123")
        again = copy.deepcopy(body)
        codex_instructions.apply_instructions(body, "gpt-5.6-sol", PROMPTS)
        self.assertEqual(body, again)
        codex_instructions.apply_instructions(body, "gpt-6-astra", PROMPTS)
        self.assertEqual(body["instructions"], "CORE\n\nASTRA PROMPT")

    def test_disabled_and_unknown_model_leave_body_unchanged(self):
        for model, prompts in (("gpt-6-astra", {}), ("custom-model", PROMPTS)):
            body = {"instructions": "ASTRA PROMPT", "input": "hello"}
            original = copy.deepcopy(body)
            codex_instructions.apply_instructions(body, model, prompts)
            self.assertEqual(body, original)

    def test_invalid_instruction_type_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be a string"):
            codex_instructions.apply_instructions({"instructions": ["bad"]}, "gpt-6-astra", PROMPTS)

    def test_download_checksum_cache_and_offline_reuse(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("prompt.md", "TEST PROMPT")
        data = buf.getvalue()
        pins = {tier: (tier, "prompt.md", hashlib.sha256(data).hexdigest()) for tier in ("astra", "sol")}
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(codex_instructions, "PROMPTS", pins), \
                patch.object(codex_instructions.urllib.request, "urlopen", side_effect=lambda *a, **kw: io.BytesIO(data)) as fetch:
            directory = Path(tmp)
            prompts = codex_instructions.load_instructions(directory, download=True)
            self.assertEqual(fetch.call_count, 2)
            self.assertNotIn("luna", prompts)
            self.assertEqual(codex_instructions.load_instructions(directory), prompts)
            self.assertEqual(fetch.call_count, 2)
            (directory / "astra.zip").write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                codex_instructions.load_instructions(directory)

    def test_bad_download_is_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(codex_instructions.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                codex_instructions.load_instructions(Path(tmp), download=True)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def setup_run(self, args, *, answer="", tty=True, saved=None, running=None, download_error=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        tmp = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        config = tmp / "config.toml"
        state = tmp / "state.json"
        config.write_text(CONFIG)
        if saved is not None:
            state.write_text(json.dumps({"use_instruct": saved, "original_provider": "custom",
                                         "original_env_key": "CUSTOM_API_KEY",
                                         "original_base_url": "https://example.test/v1"}))
        for name, value in (("HERE", tmp), ("CONFIG", config), ("STATE", state),
                            ("PIDFILE", tmp / "proxy.pid"), ("LOG", tmp / "proxy.log")):
            stack.enter_context(patch.object(smartcodex, name, value))
        stack.enter_context(patch.object(sys, "argv", ["smartcodex", *args]))
        stack.enter_context(patch.object(sys.stdin, "isatty", return_value=tty))
        ask = stack.enter_context(patch("builtins.input", return_value=answer))
        stack.enter_context(patch.object(smartcodex, "proxy_alive", return_value=running))
        stack.enter_context(patch.object(smartcodex, "wait_health", return_value=True))
        start = stack.enter_context(patch.object(smartcodex.subprocess, "Popen"))
        start.return_value.pid = 123
        load = stack.enter_context(patch.object(smartcodex, "load_instructions", side_effect=download_error))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = smartcodex.main()
        return result, ask, start, load, config, state

    def test_first_setup_asks_and_saves_yes_or_no(self):
        for answer, enabled in (("y", True), ("n", False), ("", False)):
            with self.subTest(answer=answer):
                result, ask, start, load, config, state = self.setup_run([], answer=answer)
                self.assertEqual(result, 0)
                ask.assert_called_once()
                self.assertEqual(load.called, enabled)
                self.assertEqual("--instruct" in start.call_args.args[0], enabled)
                self.assertEqual(json.loads(state.read_text())["use_instruct"], enabled)
                self.assertIn('model_instructions_file = "./my-custom.md"', config.read_text())

    def test_flags_saved_choice_and_noninteractive_default(self):
        for args, saved, tty, enabled in ((["--instruct"], None, True, True),
                                         (["--no-instruct"], True, True, False),
                                         ([], True, True, True), ([], False, True, False),
                                         ([], None, False, False)):
            with self.subTest(args=args, saved=saved, tty=tty):
                result, ask, start, load, _, state = self.setup_run(args, saved=saved, tty=tty)
                self.assertEqual(result, 0)
                ask.assert_not_called()
                self.assertEqual(load.called, enabled)
                self.assertEqual("--instruct" in start.call_args.args[0], enabled)
                self.assertEqual(json.loads(state.read_text())["use_instruct"], enabled)

    def test_dry_run_has_no_side_effects(self):
        result, ask, start, load, config, state = self.setup_run(["--instruct", "--dry-run"])
        self.assertEqual(result, 0)
        ask.assert_not_called()
        start.assert_not_called()
        load.assert_not_called()
        self.assertEqual(config.read_text(), CONFIG)
        self.assertFalse(state.exists())

    def test_running_mode_change_requires_stop_without_writing(self):
        result, _, start, load, config, state = self.setup_run(["--instruct"], saved=False, running=123)
        self.assertEqual(result, 1)
        start.assert_not_called()
        load.assert_not_called()
        self.assertEqual(config.read_text(), CONFIG)
        self.assertFalse(json.loads(state.read_text())["use_instruct"])

    def test_download_failure_does_not_start_or_change_config(self):
        result, _, start, _, config, state = self.setup_run(["--instruct"], download_error=OSError("offline"))
        self.assertEqual(result, 1)
        start.assert_not_called()
        self.assertEqual(config.read_text(), CONFIG)
        self.assertFalse(state.exists())

    def test_stop_preserves_original_instruction_setting(self):
        _, _, _, _, config, state = self.setup_run(["--instruct"])
        with patch.object(stopcodex, "CONFIG", config), patch.object(stopcodex, "STATE", state), \
                patch.object(stopcodex, "kill_proxy"), redirect_stdout(io.StringIO()):
            self.assertEqual(stopcodex.main(), 0)
        self.assertIn('model_provider = "custom"', config.read_text())
        self.assertIn('model_instructions_file = "./my-custom.md"', config.read_text())

    def test_recovers_upstream_when_smart_is_already_active(self):
        config = CONFIG + '''
[model_providers.smart]
base_url = "http://127.0.0.1:8788/v1"
env_key = "CUSTOM_API_KEY"
'''
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            config_path, state_path = tmp / "config.toml", tmp / "state.json"
            config_path.write_text(config)
            with patch.object(smartcodex, "CONFIG", config_path), \
                    patch.object(smartcodex, "STATE", state_path), \
                    patch.object(smartcodex, "PIDFILE", tmp / "proxy.pid"), \
                    patch.object(smartcodex, "LOG", tmp / "proxy.log"), \
                    patch.object(smartcodex, "HERE", tmp), \
                    patch.object(smartcodex, "proxy_alive", return_value=None), \
                    patch.object(smartcodex, "wait_health", return_value=True), \
                    patch.object(smartcodex.subprocess, "Popen") as start, \
                    patch.object(sys, "argv", ["smartcodex", "--no-instruct"]), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                start.return_value.pid = 123
                self.assertEqual(smartcodex.main(), 0)
            self.assertIn("https://example.test", start.call_args.args[0])
            state = json.loads(state_path.read_text())
            self.assertEqual(state["original_provider"], "custom")
            self.assertEqual(state["original_base_url"], "https://example.test/v1")

    def test_http_handler_uses_final_model_for_stream_and_nonstream(self):
        class Classifier:
            scene, difficulty = "software", "hard"

            def classify(self, texts):
                return [{"scene": self.scene, "difficulty": self.difficulty,
                         "scene_prob": 1.0, "difficulty_prob": 1.0}]

        clf = Classifier()
        proxy = codex_smart_proxy.Proxy(classifier=clf, upstream="https://example.test",
                                       models={"astra": "gpt-6-astra", "sol": "gpt-5.6-sol",
                                               "terra": "gpt-5.6-terra", "luna": "gpt-5.6-luna"},
                                       log_path=None, safe_tokens=200_000, instructions=PROMPTS)
        for scene, difficulty, size, expected in (("software", "hard", 1, "gpt-6-astra"),
                                                   ("research", "hard", 1, "gpt-5.6-sol"),
                                                   ("software", "medium", 1, "gpt-5.6-terra"),
                                                   ("office", "easy", 1, "gpt-5.6-luna"),
                                                   ("software", "hard", 800_000, "gpt-5.6-terra")):
            clf.scene, clf.difficulty = scene, difficulty
            for stream in (False, True):
                body = {"model": "gpt-5.6-sol", "input": "x" * size,
                        "instructions": "CORE\nASTRA PROMPT", "stream": stream}
                handler = object.__new__(codex_smart_proxy.Handler)
                handler.proxy = proxy
                handler.path = "/v1/responses"
                raw = json.dumps(body).encode()
                handler.headers = {"Content-Length": str(len(raw))}
                handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
                handler.send_response = lambda *args: None
                handler.send_header = lambda *args: None
                handler.end_headers = lambda: None
                upstream = io.BytesIO(b"data: [DONE]\n\n" if stream else b"{}")
                upstream.status, upstream.headers = 200, {"Content-Type": "text/event-stream" if stream else "application/json"}
                with patch.object(codex_smart_proxy.urllib.request, "urlopen", return_value=upstream) as send, \
                        patch.object(proxy, "log"):
                    handler.do_POST()
                forwarded = json.loads(send.call_args.args[0].data)
                self.assertEqual(forwarded["model"], expected)
                if expected in PROMPTS:
                    self.assertEqual(forwarded["instructions"], "CORE\n\n" + PROMPTS[expected])
                else:
                    self.assertEqual(forwarded["instructions"], "CORE\nASTRA PROMPT")
                self.assertEqual(forwarded["stream"], stream)
                self.assertTrue(handler.wfile.getvalue())


if __name__ == "__main__":
    unittest.main()
