# Kiểm thử iOS parity v2 — chưa xác nhận 8/8

Ngày rà soát: 2026-09-08. Chỉ thay đổi dự án IOS, không chỉnh runtime Android.

## Kết luận từ mã nguồn và log hiện có

Log trong `log ios thật/` thuộc runtime cũ: có direct-RVA FastFlag readback và
bridge import. Không có bằng chứng font/shader/stretch đã thực thi. Việc có đủ
file JSON không chứng minh có backend áp dụng. License và bridge là hạ tầng,
không cộng thay cho các nhóm tính năng đồ họa/hiệu năng bên dưới.

| Nhóm | Bằng chứng/trạng thái hiện tại | Điều còn thiếu để chấm đạt |
|---|---|---|
| FastFlags | Bản trước có readback trực tiếp bộ nhớ. V2 giữ backend này, sửa độ rộng ghi theo bảng kiểu, kiểm tra UUID | So sánh bật/tắt từng flag trong game. Chưa có registry SetFFlag/GetFastFlag tương đương Android |
| Font | Backend Luau đang khóa | ABI, task/UI context, lifecycle và readback font trên iPhone |
| Shader + sàn ướt | Backend Luau/lifecycle đang khóa | ABI, kế hoạch áp dụng, property/material readback; thử sàn ướt riêng |
| Stretch | Chưa có backend camera được kiểm chứng | Camera/projection ABI iOS, kiểm tra hình ảnh và touch mapping |
| FPS | V2 hỗ trợ cả API FPS cũ và frame-rate range, áp dụng lại cho display link đã thấy | Đo FPS trong game, thao tác, chuyển game, foreground/background; không suy FPS từ giá trị yêu cầu |
| Rendering mode | Chưa có runtime consumer xác nhận Metal performance/quality | Phải xác minh tác động từng mode; không coi OpenGL/Vulkan của Android là có trên iOS |
| Safe mode | Theo dõi và tôn trọng low-power/thermal; không can thiệp kernel | Chưa có RAM/network/power policy tương đương Android |
| Turbo mode | Keep-display có readback; phần hiệu năng do hệ điều hành quản lý | Không có API ép xung CPU/GPU hay Android power hints trên iOS |

Chưa có cơ sở chấm 8/8. Một nhóm chỉ được tính đạt khi có bằng chứng chạy thật;
`READY`, `REQUESTED`, `LOADED`, kiểm thử tĩnh hoặc build thành công không đủ.

## Thay đổi của v2

- FPS: xử lý `preferredFramesPerSecond` và `preferredFrameRateRange` (khi có),
  theo dõi display link bằng tham chiếu yếu và áp dụng sau khi profile đến muộn.
- Tất cả thao tác UIKit/policy thực hiện trên main queue qua snapshot cấu hình.
- Hook được cài độc lập, tránh cài lại một phần rồi gọi đệ quy chính hook đó.
- Khi tắt policy, phục hồi yêu cầu native đã lưu; keep-display chỉ phục hồi
  trạng thái mà Masterstrap đã thay đổi, không ép tắt idle timer của Roblox.
- Theo dõi thông báo low-power, thermal và foreground. Policy không vượt
  maximumFramesPerSecond; giới hạn yêu cầu tới 60 ở low-power/thermal serious,
  tới 30 ở thermal critical. iOS vẫn có quyền giảm thêm.
- Log có giới hạn số bản ghi; không tạo vòng ghi log từng frame hoặc display
  link riêng chỉ để tạo số FPS cao.
- Profile thiếu trường không dùng lại giá trị từ request trước.
- FastFlag dùng độ rộng bool/int từ bảng địa chỉ thay vì kiểu của giá trị JSON.
- Build sử dụng dump cục bộ khớp SHA-256/UUID IPA; không lấy offsets mạng cho một
  IPA khác. Runtime kiểm tra UUID trước khi ghi flags. UUID đúng không mở khóa
  các lời gọi engine chưa được xác minh ABI.
- IPA thử nghiệm không nhúng baseline flags hoặc ClientAppSettings có sẵn.

Tài liệu API: [Apple preferredFrameRateRange](https://developer.apple.com/documentation/quartzcore/cadisplaylink/preferredframeraterange).

## Bộ file thử nghiệm

Thư mục: `output/ios-parity-v2/`. Đây là bản kiểm thử, chưa phải bản 8/8.

- `Masterstrap-Roblox-Patched.ipa`: cần ký lại trước khi cài.
- `Masterstrap.ipa`: launcher đi kèm, cũng cần ký lại. Bản build Windows không
  có official LevelPlay framework nếu không cung cấp framework từ macOS/Xcode;
  không coi quảng cáo đã hoạt động chỉ vì có UI.
- `profile/`: dump và bằng chứng identity của Roblox 2.735.1135.

Không thay bản `output/` cũ. Không thay offsets dựa trên Android. Nếu Roblox đã
  bắt buộc cập nhật 2.735.1135 thì cần IPA đầu vào mới, dump và kiểm chứng lại.

## Cách thử trên iPhone

1. Ghi model iPhone, phiên bản iOS, Roblox và trạng thái Low Power Mode.
2. Mở Roblox trực tiếp: phải không nhập cấu hình cũ. Giữ log phiên này.
3. Trong Masterstrap chỉ bật DisplayFPS, yêu cầu 120 FPS/refresh, Save and Launch.
4. Kiểm tra đứng yên và liên tục xoay camera/di chuyển trong cùng một game.
   Màn hình 60 Hz không được chấm lỗi vì không hiển thị 120 Hz.
5. Bật/tắt Low Power Mode, background/foreground, chuyển game rồi lặp lại.
6. Gửi cấu hình mới tắt refresh/đổi về mặc định; xác minh không dùng profile cũ.
7. Kiểm thử riêng font, shader/sàn ướt, stretch khi backend được triển khai sau.
   Hiện các nhóm đó vẫn báo BLOCKED; không coi đây là do mạng hoặc tải chậm.

Thu từ Documents của Roblox (giữ nguyên tên, không gửi token/license):

- `masterstrap_ios.log`
- `masterstrap_bridge.log`
- `MasterstrapRuntimeReceipt.json`
- `MasterstrapDisplayReceipt.json`
- `MasterstrapAbiProbe.json`
- `MasterstrapConsumedReceipt.json`

Từ Masterstrap: `masterstrap_app.log`.

Đọc log theo thứ tự:

1. `[IDENTITY] UUID status=MATCH`: binary khớp map; chưa chứng minh ABI hàm.
2. `[SESSION][AUTH] ... ALLOW_ONCE` và receipt consume: phiên có cấu hình hợp lệ.
3. `[CAPABILITY][FPS] range=1`: có API frame-rate range.
4. `[FPS][COVERAGE] trackedLinks>0`: đã thấy display link. `WAITING_FOR_LINK`
   không được coi là áp dụng thành công; engine có thể dùng đường khác.
5. `[READBACK][FPS]`: giá trị API đọc lại, **không phải FPS trình bày thực tế**.
6. `lowPower`, `thermalState`, `screenMaximum`: giải thích giới hạn hệ thống.
7. `presentedFps=null`, `runtimeVerified=false`: cố ý giữ như vậy tới khi có
   phép đo frame presentation đáng tin cậy trên thiết bị.

## Việc cần làm tiếp để tiến tới parity

Ưu tiên xác minh runtime FPS/flags của bản v2. Sau đó phân tích ABI Luau/task
context iOS từ đúng Mach-O, trước tiên cho font rồi shader/sàn ướt; camera/stretch
là hợp đồng riêng. Mỗi hook phải có signature/ABI/lifecycle và crash diagnostics.
Không bật đồng thời nhiều lời gọi dựa trên tên offset hoặc opcode prologue.

Nếu mục tiêu là “giống từng API Android”, Safe/Turbo và lựa chọn renderer cần
định nghĩa tương đương phù hợp iOS; không thể cam kết chỉ port tên toggle là đủ.
