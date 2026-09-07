# Đối chiếu backend Roblox Patched Android và iOS

## Mục tiêu

Tài liệu này là hợp đồng kỹ thuật để đưa Roblox Patched iOS đạt mức tính năng tương đương Android mà không dùng offset Android cho Mach-O iOS.

## Trạng thái đã xác minh

| Lớp | Android | iOS hiện tại | Kết luận |
|---|---|---|---|
| Nhận cấu hình từ Masterstrap | Có, qua bridge và tệp trong sandbox Roblox | Có, bridge API v2 và fallback URL; đã ghi bốn tệp vào sandbox Roblox | Đã có nền tảng giao tiếp |
| Parse FastFlag | JSON typed, chuẩn hóa bool/int/string | Bool/int và chuỗi bool/int linh hoạt | Parser iOS đã được sửa; cần log thiết bị thật |
| `SetFFlag` registry | Có, gọi wrapper Roblox đã kiểm chứng ABI và đọc lại slot | Chưa có; hàm mang tên `SetFastFlag` hiện chỉ ghi trực tiếp RVA | Chưa tương đương |
| `GetFastFlag` hook | Có, là lớp phụ sau khi `SetFFlag` khỏe | Chưa có hook thật | Chưa tương đương |
| Direct-memory fallback | Có, chỉ cho từng flag chưa được registry xác nhận | Có, hiện là đường áp dụng chính cho bool/int | Có nhưng thứ tự ưu tiên chưa tương đương |
| Offset/signature guard | Kiểm tra segment ELF, chữ ký wrapper, registry pointer và readback | Mới chỉ log opcode; chưa có guard runtime đầy đủ | Phải bổ sung trước khi gọi hàm engine |
| Font trong game | Có, dùng kế hoạch Luau chung với shader backend | Chỉ nhận tệp cấu hình; chưa có consumer runtime | Chưa có backend |
| Shader package | Có, Luau bytecode + task/game lifecycle hooks với ABI pin theo build | Chỉ nhận cấu hình; chưa có Luau/lifecycle backend | Chưa có backend |
| Stretch resolution | Có, camera/view-projection hook và surface policy | Chỉ nhận phần trăm trong turbo profile | Chưa có backend |
| Reapply qua game kế tiếp | Có lifecycle và cơ chế reapply có giới hạn | Chỉ ghi lại RVA theo timer 30 giây | Chưa tương đương |

## Quy tắc triển khai

1. Không tái sử dụng RVA Android trong iOS. Android là ELF `libroblox.so`; iOS là Mach-O `RobloxLib`.
2. Mọi hook engine iOS phải có đủ: UUID/SHA-256 đúng build, RVA nằm trong `__TEXT`, chữ ký byte khớp, ABI gọi đã được xác nhận và log readback.
3. `SetFFlag` registry là đường chính; `GetFastFlag` và direct-memory chỉ là lớp phụ/fallback như Android.
4. Không bật font/shader/stretch nếu thiếu một offset hoặc field offset trong hợp đồng ABI tương ứng. Khi thiếu phải fail-closed và ghi lý do vào log, không thử gọi mù.
5. Mỗi lần Roblox cập nhật phải dump lại Mach-O và chạy static verification trước khi đóng gói.

## Thứ tự hoàn thiện

1. Xác minh parser mới trên thiết bị thật và thu `masterstrap_ios.log`.
2. Mở rộng dumper để tìm và xác minh wrapper `SetFFlag`, getter bool/int và lifecycle offsets.
3. Thêm runtime offset shield và typed readback; chỉ sau đó mới bật registry setter/getter.
4. Port shader/font bằng bộ ABI Luau riêng của iOS.
5. Dump camera function cùng các field offsets iOS rồi port stretch projection.
6. Thêm test tĩnh, build IPA debug, kiểm tra log qua nhiều lần vào/thoát game.

## Tiêu chí “đầy đủ như APK”

Không được gọi là parity nếu chỉ tạo đủ tệp JSON. Parity chỉ đạt khi log thiết bị thật chứng minh registry setter/getter, font, shader, stretch và lifecycle reapply đều được consumer runtime iOS thực thi, đồng thời không có crash hoặc vòng ghi liên tục gây tụt FPS.
