# Quy tắc tối ưu bridge iOS

1. Giảm request/reads/writes phải đi cùng nhận đủ cấu hình. `openURL=1` hoặc build thành công chưa xác nhận import thành công.
2. Giữ đủ flag JSON, FPS/stretch profile, XML, font và shader profile. Không bỏ profile, cắt payload hoặc đổi giá trị để vừa URL.
3. Save/toggle/idle lưu local; không upload, polling bridge hoặc tự CHECK licence theo timer/slider.
4. Payload nhỏ dùng URL local protocol 3, nén zlib có giới hạn. Giữ ngưỡng encoded payload đã kiểm tra 2.048 ký tự; payload lớn dùng protocol 2. Không tăng ngưỡng mù.
5. Một launch intent chỉ có một chuỗi quảng cáo/animation/publish/openURL. Callback trùng không import/consume lần nữa. Thất bại phải báo lỗi, không mở Roblox với cấu hình thiếu.
6. Ticket cloud mới lưu trong Durable Object, consume nguyên tử một lần, TTL 60 giây. Không thêm D1 writes cho rate counter, log hay cleanup từng request. Ticket D1 cũ chỉ còn consume/cleanup tương thích.
7. Đo chi phí Worker/Durable Object riêng. `0 D1 writes` không đồng nghĩa miễn phí; DAU không tự xác định số launch hoặc CHECK/ngày.
8. Licence giữ kiểm tra key/thiết bị/hết hạn/khóa từ server. Không giảm reads bằng cấp quyền mới từ local cache; không xóa key đã lưu vì lỗi tạm 429/5xx.
9. Log chỉ ghi request ID, protocol, kích thước, trạng thái từng profile và backend. Không ghi ticket, licence key hoặc toàn payload.
10. Chạy kiểm tra budget và `python -B tools/check_ios_bridge_import.py` trên macOS trước bàn giao. Kiểm tra URL parse, giải nén, nội dung đủ năm file, receipt, callback trùng, input lỗi và giới hạn 512 KiB; không chỉ kiểm tra chuỗi trong mã sinh.
11. Build Debug bằng SDK Apple để thử trên iPhone; Release khi phát hành. Task bridge không tự bật các backend font/stretch đang cô lập hoặc thay offsets/FPS ngoài phạm vi đã xác nhận.
12. Tuân thủ quy trình xác nhận, chẩn đoán, build, bản dịch và quyền push do chủ dự án cung cấp. Không push/deploy ngoài quyền đã được xác nhận.
