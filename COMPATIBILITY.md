# Contract tương thích Roblox iOS

Mục tiêu: khi Roblox dịch địa chỉ hoặc đổi GPU class, Actions tự sinh profile
từ binary mới. Không chọn RVA/ABI của bản trước, không tuyên bố mọi bản tương
lai giữ nguyên contract.

## Backend và gate

| Tính năng | Contract tự xác minh mỗi build/runtime | Khi contract đổi |
|---|---|---|
| Flag | UUID/hash binary; registration/storage map có loại và bounds | Không ghi slot không được xác minh; báo MISS/UNSUPPORTED |
| FPS | Patch signature duy nhất trong executable/function bounds; Metal device/queue/buffer thực, selector và ABI | Runtime giữ original IMP khi ABI sai; Release chặn patch thiếu/mơ hồ |
| Font family | Import symbol table của đúng binary; catalog JSON/TTF stock, canonical path, fallback | Chặn build thiếu reader/Minecraft; runtime quay về reader gốc |
| Stretch | Chưa bật: cần layer/viewport scope, kích thước gốc, restore/touch | Giữ disabled tới khi gate riêng đạt |

Metal discovery xử lý từng protocol độc lập và quan sát device sau khi driver
lazy-load. Queue factory bắt object cụ thể; không khóa vào tên class AGX hoặc
version Roblox. Probe không commit GPU work, không quét mọi class mỗi frame.
Original IMP giữ theo class/selector; native test phải bao gồm inherited/super
calls, device tạo muộn, ABI rejection và timed-present passthrough/override.

## Chuỗi Actions cho bản mới

1. Xác minh input decrypted, architecture, UUID/hash; dump/verify profile cùng input.
2. Resolve storage, FPS patches và font import/assets; không dùng header CDN/RVA cũ.
3. Build Debug bằng SDK Apple; chạy transport, budget và native backend tests.
4. Xuất identity, manifest, FPS/font profiles và rejection/coverage diagnostics.
5. Device gate: cold/warm/direct, login/join/rejoin, power, flag removal, font Default,
   FPS metrics, stretch/touch khi được bật; test tổ hợp trước Release.
6. Checkpoint chỉ ghi build candidate, `device_verified=false`; không thay nghiệm thu.

Test fixtures phải có missing/ambiguous/bounds/UUID/ABI mismatch, không chỉ happy path.
Thay đổi compiler/ABI/semantics ngoài resolver vẫn cần cập nhật resolver và test;
CI phải phát hiện/chặn, không đoán địa chỉ để giữ workflow xanh.

## Kế hoạch còn lại

- FPS: xác nhận queue/commit/completion/GPU coverage trên iPhone; truy vết đúng
  engine step/nguồn overlay, rồi sửa pacing được chứng minh. Display/present rate
  không được đổi nhãn thành engine FPS; `engineFps=null` tới khi có bộ đo đúng.
- Pacing probe: resolve libc++ wait import và BL caller/function bounds từ binary;
  observer C++ giữ nguyên lock/deadline/exception, không bỏ fence wait. Caller không
  nhận diện không được gán nhãn engine step. Layer phải ghi supported/readback,
  không tuyên bố VSync đã tắt chỉ từ yêu cầu setter.
- Stretch: backend riêng sau FPS gate; lưu kích thước gốc mỗi layer, restore 100%,
  portrait/landscape, touch. Không nhân tỷ lệ lên kích thước đã stretch.
- Font sizeStep: contract riêng; family assets remap không chứng minh đổi cỡ chữ.
- Mọi tính năng mới dùng snapshot năm tài liệu hiện có. Không thêm request riêng
  từng feature, polling bridge hoặc D1 diagnostics; licence vẫn xác minh server.
