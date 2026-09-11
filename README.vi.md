# Định tuyến mô hình thông minh

[English](README.md)

Bộ phân loại ONNX chạy cục bộ chọn mô hình cho Claude Code và Codex CLI dựa trên
nhóm tác vụ, độ khó và kích thước ngữ cảnh ước tính. Proxy thay đổi model và
`reasoning.effort` của Codex trước khi chuyển tiếp yêu cầu đến upstream.

## Yêu cầu

- macOS, Python 3.11+, `rsync`, và `curl` hoặc `wget`
- Claude Code hoặc Codex đã có custom endpoint tương thích
- `query-classifier/model_fp16.onnx` từ release `classifier-v1`

## Cài đặt

```bash
git clone https://github.com/vutranHS/llm-model-smart-routing.git
cd llm-model-smart-routing
./fetch_classifier.sh
./install.sh
source ~/.zshrc
```

Nếu dùng full bundle, hãy kiểm tra checksum trước khi giải nén:

```bash
curl -L -o sr.tar.gz https://github.com/vutranHS/llm-model-smart-routing/releases/download/classifier-v1/llm-model-smart-routing-full-v1.tar.gz
echo "4b6cd693bf499ef5e58c04872a1eb26bf101f0582d3215aeafdf713b6a54aced  sr.tar.gz" | shasum -a 256 -c -
tar -xzf sr.tar.gz
```

## Sử dụng

```bash
smartclaude
stopclaude
smartcodex
stopcodex
```

Hãy khởi động lại phiên CLI sau khi bật hoặc tắt. Claude dùng proxy loopback
`127.0.0.1:8787`; Codex dùng port `8788`. Endpoint gốc và environment key
của Codex provider được giữ nguyên.

Mặc định log không chứa nội dung prompt hoặc body lỗi từ upstream. Chỉ thêm
`--log-prompts` khi chủ động muốn ghi preview 120 ký tự trên máy cục bộ.

## Kiểm tra

```bash
curl -s http://127.0.0.1:8787/health
curl -s http://127.0.0.1:8788/health
python3 -m unittest discover -s tests -v
```

Metric trong manifest là kết quả xác thực model, không phải cam kết về chất
lượng định tuyến hoặc mức tiết kiệm chi phí đầu-cuối.
