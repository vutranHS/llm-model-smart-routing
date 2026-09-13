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

### Tùy chọn instruct cho Codex

Lần đầu chạy `smartcodex` trong terminal, setup hỏi có dùng instruct theo model
không (`y/N`, mặc định **không**). Lựa chọn được lưu cho những lần chạy sau.
Nếu không có terminal tương tác, mặc định tắt trừ khi đã bật trước đó hoặc truyền
cờ rõ ràng. `--dry-run` không hỏi, không tải file và không thay đổi cấu hình.

```bash
smartcodex --instruct       # bật, không cần trả lời câu hỏi
smartcodex --no-instruct    # tắt, không cần trả lời câu hỏi
# Nếu proxy đang chạy, dừng trước khi đổi lựa chọn:
stopcodex
smartcodex --instruct
```

Khi chọn bật, setup tải hai ZIP đã ghim phiên bản và SHA256 từ
[MDX-Tom/gpt-instruct](https://github.com/MDX-Tom/gpt-instruct), lưu trong
`.codex_instructions/` tại thư mục cài đặt. Chỉ setup tải file; những lần sau có
thể dùng cache đã kiểm tra mà không cần mạng. Lỗi tải hoặc sai checksum sẽ dừng
setup, không khởi động proxy hay sửa cấu hình Codex. Đây là prompt bên thứ ba,
nên xem nội dung trước khi bật.

Sau khi chốt model, proxy chỉ dùng Astra v1 cho `gpt-6-astra` và Sol v45 cho
`gpt-5.6-sol`. Terra và Luna không có instruct đi kèm nên giữ nguyên instruction
từ client. Vì vậy nếu request dài bị hạ từ Astra xuống Terra thì cũng không còn
nhận Astra instruct.
Proxy giữ nguyên instruction khác, tools và tin nhắn người dùng; chỉ thay các
bản sao khớp chính xác của hai prompt này trong `instructions` hoặc tin nhắn
developer/system. Bản prompt tự chỉnh sửa không bị xóa tự động.
Không sửa `model_instructions_file` hiện có; khi tắt tùy chọn này, instruction
từ client được giữ nguyên. Hãy mở lại phiên Codex sau setup.

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
