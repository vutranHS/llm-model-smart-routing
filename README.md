# Smart Model Routing

Local ONNX classifier tự chọn model theo **scene + difficulty** (và **token count** với Codex), rewrite request trước khi gọi upstream.

```
Claude Code (8787)  /  Codex CLI (8788)
        │
        ▼
Local proxy
        │  classify ~7ms (offline)
        │  rewrite model (+ reasoning.effort với Codex)
        ▼
Upstream của bạn (đọc từ config — không hardcode)
```

> Endpoint gốc **không hardcode**. `smartclaude`/`smartcodex` backup URL từ config vào state file; `stop*` chỉ restore từ state. Thiếu state = không đoán.

---

## Cài mới

```bash
git clone https://github.com/vutranHS/llm-model-smart-routing.git
cd llm-model-smart-routing
./fetch_classifier.sh          # copy/download ONNX ~150MB (không nằm trong git)
./install.sh
source ~/.zshrc
```

`install.sh` copy về `~/.smart-routing/`, tạo venv, thêm alias `smartclaude` / `stopclaude` / `smartcodex` / `stopcodex`.

---

## BẬT / TẮT

```bash
smartclaude    # Claude Code → proxy :8787
stopclaude     # revert ANTHROPIC_BASE_URL + kill proxy

smartcodex     # Codex CLI → proxy :8788
stopcodex      # revert config.toml + kill proxy
```

Sau khi bật/tắt, **restart session** tương ứng.

State (ghi lúc bật lần đầu):
- `.smart_routing_state.json` — original `ANTHROPIC_BASE_URL`
- `.codex_smart_state.json` — original provider + `base_url`

---

## Yêu cầu

- Python 3.11+
- `onnxruntime`, `tokenizers`, `numpy` (venv `.venv-classifier/`)
- Claude Code / Codex đã trỏ custom endpoint hợp lệ
- **ONNX weights** `query-classifier/model_fp16.onnx` (~150MB) — không nằm trong git; lấy qua Release

```bash
./fetch_classifier.sh
# tải từ GitHub Release classifier-v1 (sha256 verify)
# override URL: CLASSIFIER_URL='https://...' ./fetch_classifier.sh

python3 -m venv .venv-classifier
./.venv-classifier/bin/pip install onnxruntime tokenizers numpy
```

Weights: [Release classifier-v1](https://github.com/vutranHS/llm-model-smart-routing/releases/tag/classifier-v1)

---

## Routing rules

### Claude Code (port 8787)

| Rule | Model | Giá in/out |
|---|---|---|
| software + hard | `cc/claude-fable-5-1` | $10/$50 |
| hard khác | `cc/claude-opus-5` | $5/$25 |
| medium software/design/research | `cc/claude-opus-4-8` | $5/$25 |
| easy + medium office | `cc/claude-sonnet-5` | $2/$10 |
| fallback / lỗi classify | `cc/claude-sonnet-5` | |

### Codex CLI (port 8788)

| Rule | Model | Giá | Effort |
|---|---|---|---|
| software + hard | `gpt-6-astra` | $10/$50 | high |
| hard khác | `gpt-5.6-sol` | $4/$20 | high |
| medium tech | `gpt-5.6-terra` | $2/$12 | medium |
| easy / office | `gpt-5.6-luna` | $0.2/$1.2 | low |

**Token guard (Codex):** ước tính input >200K → demote tier (né cliff 272K = 2× giá).  
>100K + tier luna/terra → bump effort lên high.

Classifier labels:
- **scene**: `office` · `software` · `design` · `media` · `research` · `other`
- **difficulty**: `easy` · `medium` · `hard`

Override model id (9router catalog máy bạn có thể khác prefix):
```bash
smartclaude  # hoặc chạy smart_proxy.py --fable/--opus5/--opus48/--sonnet
smartcodex   # hoặc codex_smart_proxy.py --astra/--sol/--terra/--luna
```

---

## Config bị sửa khi bật

**Claude** `~/.claude/settings.json`:
```
ANTHROPIC_BASE_URL → http://127.0.0.1:8787/v1
```
Token giữ nguyên; proxy forward `Authorization`.

**Codex** `~/.codex/config.toml`:
```
model_provider = "smart"
[model_providers.smart]
base_url = "http://127.0.0.1:8788/v1"
env_key = <giống provider cũ>
wire_api = "responses"
```

---

## Verify

```bash
# health
curl -s http://127.0.0.1:8787/health
curl -s http://127.0.0.1:8788/health

# log route
tail -f smart_proxy.log.jsonl
tail -f codex_smart_proxy.log.jsonl

# classifier standalone
./.venv-classifier/bin/python classify.py \
  "Fix this Python TypeError in FastAPI" \
  "Implement a distributed rate limiter with Redis, production-grade"
```

Log dòng `route` có: `scene`, `difficulty`, `routed_model`, `effort` (Codex), `est_tokens` (Codex).

---

## Files

| File | Vai trò |
|---|---|
| `install.sh` / `uninstall.sh` | (bundle) cài / gỡ |
| `smartclaude.py` / `stopclaude.py` | bật/tắt Claude |
| `smartcodex.py` / `stopcodex.py` | bật/tắt Codex |
| `smart_proxy.py` | proxy Anthropic Messages |
| `codex_smart_proxy.py` | proxy OpenAI Responses + effort |
| `classify.py` | ONNX wrapper |
| `query-classifier/` | model + tokenizer |

---

## Troubleshooting

| Triệu chứng | Fix |
|---|---|
| connection refused | proxy chưa start → `smartclaude` / `smartcodex` |
| 401 | token sai/hết hạn — check auth trong settings/config |
| model not found | model id không match catalog upstream |
| Route luôn tier rẻ | classifier fail → log `"reason": "classifier_error"` |
| stop* warning “no state” | thiếu state file — set BASE_URL / provider tay |
| Claude `WebFetch_ide` | do IDE bridge — `claude --no-ide`, không phải proxy |
| Port 8787/8788 chiếm | `lsof -ti :8787` rồi kill |

---

## Lưu ý

- Proxy phải chạy **trước** khi start session.
- Classifier **offline**, không gửi text ra ngoài.
- Chỉ rewrite `model` (+ `reasoning.effort` với Codex); body/header/stream pass-through.
- `stop*` / `uninstall.sh` **không hardcode** endpoint — chỉ restore từ state.
- Model ids (`cc/claude-…`, `gpt-…`) phụ thuộc catalog upstream của bạn; override nếu khác.
