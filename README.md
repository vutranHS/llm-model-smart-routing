# Smart Model Routing

[Vietnamese documentation](README.vi.md)

A local ONNX classifier routes Claude Code and Codex CLI requests by scene,
difficulty, and estimated context size. The proxy rewrites the model and, for
Codex, `reasoning.effort`, then forwards the request to your configured upstream.

## Requirements

- macOS, Python 3.11+, `rsync`, and `curl` or `wget`
- Claude Code or Codex configured with a compatible custom endpoint
- `query-classifier/model_fp16.onnx` from the `classifier-v1` release

## Install

```bash
git clone https://github.com/vutranHS/llm-model-smart-routing.git
cd llm-model-smart-routing
./fetch_classifier.sh
./install.sh
source ~/.zshrc
```

For the full bundle, verify its checksum before extracting:

```bash
curl -L -o sr.tar.gz https://github.com/vutranHS/llm-model-smart-routing/releases/download/classifier-v1/llm-model-smart-routing-full-v1.tar.gz
echo "4b6cd693bf499ef5e58c04872a1eb26bf101f0582d3215aeafdf713b6a54aced  sr.tar.gz" | shasum -a 256 -c -
tar -xzf sr.tar.gz
```

## Usage

```bash
smartclaude
stopclaude
smartcodex
stopcodex
```

Restart the corresponding CLI session after enabling or disabling routing.
Claude uses the loopback proxy at `127.0.0.1:8787`; Codex uses port `8788`.
The original endpoints and Codex provider environment key are preserved.

Prompt text and upstream error bodies are not logged by default. Pass
`--log-prompts` only when a local 120-character preview is explicitly wanted.

## Verify

```bash
curl -s http://127.0.0.1:8787/health
curl -s http://127.0.0.1:8788/health
python3 -m unittest discover -s tests -v
```

The release manifest reports source-checkpoint accuracy of 0.7381, scene
macro-F1 of 0.7997, and difficulty macro-F1 of 0.7657. These model metrics are
not an end-to-end guarantee of routing quality or cost savings.
