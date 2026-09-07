# Bridge Masterstrap → Roblox patched trên iOS

## Mục tiêu

Bridge cho phép `Masterstrap.ipa` chuyển cấu hình launch sang Roblox patched mà
không đọc chéo thư mục, không cần App Group và không yêu cầu hai ứng dụng dùng
chung Team ID.

Endpoint production: `https://bridge.masterstrap.org`.

## Luồng chính — protocol 2

1. Frontend gửi `clientAppSettings`, `turboProfile`, `globalSettings` và
   `targetChannel` cho native Masterstrap.
2. Masterstrap gửi cấu hình qua HTTPS bằng `POST /v2/launch-configs`.
3. Server trả về request ID và ticket ngẫu nhiên dùng một lần, hết hạn sau 60 giây.
4. Masterstrap mở Roblox bằng URL ngắn:
   - Global: `masterstrap-rbx-bridge-v1://apply?v=2&request=...&ticket=...`
   - VNG: `masterstrap-rbx-vng-bridge-v1://apply?v=2&request=...&ticket=...`
5. Roblox patched nhận URL ở cả cold launch và warm launch, rồi gửi ticket bằng
   `POST /v2/launch-configs/consume`.
6. Roblox kiểm tra protocol, request ID, channel và kiểu payload trước khi ghi cấu
   hình vào sandbox của chính Roblox.
7. Ở cold launch, bước consume và ghi file hoàn tất trước khi gọi delegate khởi
   động gốc, để engine đọc đúng cấu hình ngay trong phiên hiện tại.

Ticket không được ghi vào log, không được lưu dạng thô trên server và chỉ consume
được một lần. Server chỉ lưu SHA-256 của ticket. API không bật CORS và không có
trang hay nút công khai dẫn tới endpoint.

## Fallback — protocol 1

Nếu Masterstrap không kết nối được API lúc Save and Launch, app tự dùng bridge v1
cũ: JSON được Base64 và chuyển trực tiếp qua custom URL. Điều này giúp launch vẫn
hoạt động khi server hoặc mạng tạm thời không sẵn sàng. Protocol 1 chỉ là fallback;
protocol 2 là luồng mặc định.

## File được tạo trong sandbox Roblox

- `Documents/ClientAppSettings.json`
- `Documents/MasterstrapTurboProfile.json`
- `Documents/GlobalBasicSettings_13.xml`
- `Documents/MasterstrapBridgeReceipt.json`

Receipt thành công chứa protocol thực tế đã dùng:

```json
{"protocol":2,"status":"imported","request":"...","receivedAtUnix":0,"client":true,"turbo":true,"global":true}
```

## Log chẩn đoán

- Masterstrap: `Documents/masterstrap_app.log`
- Roblox bridge: `Documents/masterstrap_bridge.log`
- Roblox runtime: `Documents/masterstrap_ios.log`

Log có request ID, channel, HTTP status và kết quả ghi file, nhưng không ghi ticket
hoặc nội dung cấu hình. Log Masterstrap/bridge xoay ở 512 KiB; log runtime xoay ở
1 MiB.

Các mốc thành công cần thấy:

```text
Bridge API upload complete: request=... protocol=2 channel=...
Bridge API consume complete: request=... channel=...
Import complete: request=... protocol=2
```

## Ký, cài đặt và kiểm chứng

- Ký lại toàn bộ nested framework/dylib của Roblox trước khi ký app ngoài cùng.
- Mỗi bundle ID cần provisioning phù hợp; hai IPA có thể dùng Team ID khác nhau.
- Cài cả hai IPA đã ký và mở Roblox patched ít nhất một lần.
- Mở Masterstrap, đổi một FastFlag dễ quan sát rồi chọn Save and Launch.
- Đối chiếu cùng request ID trong hai log và trong
  `MasterstrapBridgeReceipt.json`.

Không đưa license, token tài khoản, cookie hoặc dữ liệu đăng nhập vào payload.
