# Masterstrap: Desync và licence iOS

Desync do toggle sinh chỉ còn `DFIntS2PhysicsSenderRate=38760`. Không thay Faster Speed. Các flag cũ trong custom editor vẫn là dữ liệu người dùng; bundle cũ do toggle sinh được dọn khỏi activeFlags.

Key/ID thiết bị chuyển vào Keychain (ThisDeviceOnly); migration giữ nguyên ID đã binding. Cold launch và foreground gửi một POST `license.masterstrap.org/api/ios-licenses/validate?mode=status`. Content-Type text/plain vẫn mang JSON, tránh thêm CORS preflight. `/validate` thường vẫn dành cho activation/CHECK. Endpoint status không kích hoạt lại binding, không tăng CHECK, không ghi DB. Không polling khi toggle/slider/launch Roblox.

Status hợp lệ mới xác nhận Plus/Max của phiên app. Server not-found/disabled/expired/reset hạ quyền, xóa key nhưng giữ device ID. Lỗi mạng/429/5xx giữ key và sở thích, báo thử lại; launch VIP chưa xác nhận bị chặn. Plus timer dùng mốc thời gian server, không request theo timer. Max lifetime vẫn kiểm tra thu hồi ở lần mở/foreground tiếp theo.

Runtime log `[LICENSE]` chỉ chứa action/status/valid, không key/ID. Backend có zero writes trên status; kích hoạt, CHECK và quản trị vẫn có writes. Không cam kết tổng requests/reads/writes từ DAU.

CI chỉ build Masterstrap khi commit có `[masterstrap-only]`. Kiểm tra: `python -B tools/check_ios_license.py` thực thi JS, và trên macOS chạy WK reply/Keychain thật; bridge budget/import giữ đủ năm tài liệu. Sau build, dọn log tạm cũ khỏi hai thư mục thử nghiệm, lưu riêng để tránh trộn phiên.

Thử iPhone: mở lại Plus/Max đã lưu; Desync chỉ một flag; disable/xóa/reset key trên admin rồi mở lại app; Plus hết hạn; mất mạng giữ key và không mất các cấu hình đã lưu; phục hồi mạng xác minh đúng. Không dùng key khách hàng để test tự động.
