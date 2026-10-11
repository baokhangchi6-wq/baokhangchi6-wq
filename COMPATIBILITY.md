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
| Stretch resolution | Anchor create/resize framebuffer; hai lần đọc frame/scale; cùng width/height cho allocator và drawable; UUID + CGRect ABI | Không sửa frame nếu thiếu/mơ hồ; Debug chờ device gate; Release chặn role thiếu |

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
- RenderJob minimum-idle: mỗi build xác minh anchor `scheduleRender`, storage bool/int32,
  chuỗi load/compare/branch và đúng import `sleep_for`. FPS >60 tắt riêng
  `FFlagMainThreadIdle`; flag người dùng đặt rõ được ưu tiên. FPS ≤60 hoặc snapshot
  không còn yêu cầu sẽ restore giá trị trước Masterstrap. Không đổi minimumIdleUs,
  không bỏ fence/GPU wait. Runtime kiểm tra UUID; Release chặn role chưa xác minh,
  Debug giữ policy tắt khi không resolve được. Profile không thay device gate.
- Stretch resolution: thay width trả về chỉ ở lần đọc `UIView.frame` đầu tiên của
  hai hàm tạo/resize framebuffer được xác minh. Mỗi lần lấy frame gốc mới, không
  sửa layout/touch hoặc co riêng drawable khác kích thước framebuffer. Portrait,
  direct launch và 100% dùng frame gốc. Snapshot mới đổi policy; engine resize
  lần kế tiếp tạo lại framebuffer đồng bộ. Receipt đo số lần đúng callsite; cần
  test hình ảnh, restore, orientation/touch trên iPhone. Không tuyên bố camera
  projection/FOV đã thay đổi chỉ vì resolution thay đổi. Bản compiler đổi luồng
  tính kích thước phải bị từ chối, không dùng RVA cũ.
- GUI và joystick: build #142 xác nhận framebuffer 3D/UI cùng bị thu chiều ngang.
  Resolver đã nhận lệnh xen giữa khi dataflow width/height vẫn giữ, nhưng không
  dùng patch "projection" cũ: site đó thuộc `FastCluster::updateInstanceData`.
  Cần tách scene target khỏi `Pass2d/PlayerGui` và `Pass2d/CoreGui`, giữ final
  drawable/UI ở kích thước gốc, xác minh touch và restore trên thiết bị. Nếu
  compiler đổi graph pass/attachment, gate phải đóng, không lấy tên pass làm ABI.
- Bản thử UI projection: resolver xác minh `render2D` truyền width/height tới
  hàm chiếu trực giao `2/width`, `-2/height`. Callback chỉ khôi phục width logic
  khi input khớp frame đã co × contentScaleFactor; texture cảnh vẫn giữ kích
  thước hiện tại. Đây là gate khác với native-resolution UI target/composite;
  cần nghiệm thu hình dạng, vị trí chạm và độ nét UI riêng trên iPhone.
- Role callbacks được đặt trong segment RW riêng trước LINKEDIT; nhánh trong
  TEXT padding chỉ được viết lúc đóng gói. Runtime kiểm tra UUID, segment và
  cookie, không ghi executable memory. Gate macOS arm64 ký/nạp dylib thực và
  kiểm tra register, SIMD, NZCV/FPCR/FPSR, baseline khi callback chưa bind.
- `MasterstrapRenderJobReceipt.json` đo trọn `scheduleRender` và drawable wait
  nằm trên cùng thread/job. Chưa gọi counter scheduling này là engine FPS;
  nếu jobsWithDrawables=0 phải truy tiếp thread handoff, không gán nhãn giả.
- Font sizeStep: contract riêng; family assets remap không chứng minh đổi cỡ chữ.
- Mọi tính năng mới dùng snapshot năm tài liệu hiện có. Không thêm request riêng
  từng feature, polling bridge hoặc D1 diagnostics; licence vẫn xác minh server.
