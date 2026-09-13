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

### Optional Codex instructions

The first interactive `smartcodex` setup asks whether to use model-specific
instructions (`y/N`, default **no**). The choice is saved for subsequent starts.
Without a terminal, instructions default to off unless previously enabled or
explicitly requested. `--dry-run` never prompts, downloads, or changes settings.

```bash
smartcodex --instruct       # enable without the question
smartcodex --no-instruct    # disable without the question
# If the proxy is already running, stop it before changing this option:
stopcodex
smartcodex --instruct
```

Opting in downloads two checksum-pinned ZIPs from
[MDX-Tom/gpt-instruct](https://github.com/MDX-Tom/gpt-instruct) into
`.codex_instructions/` inside the install directory. Downloads happen only during
setup; subsequent starts can use the verified cache offline. A failed download or
checksum check aborts setup without starting the proxy or changing Codex config.
These are optional third-party prompts; review them before enabling.

After routing, the proxy uses Astra v1 only for `gpt-6-astra`, and Sol v45 only
for `gpt-5.6-sol`. Terra and Luna have no bundled instruct and keep the
client-provided instructions. If a long-context request is demoted from Astra
to Terra, it therefore also stops receiving Astra instruct.
The proxy preserves unrelated client instructions, tools, and user messages,
replacing only exact copies of these bundled prompts in `instructions` or
developer/system input messages. Custom variants are not removed automatically.
Your existing `model_instructions_file` is left untouched; disabling this option
leaves all client instructions unchanged. Restart the Codex session after setup.

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
