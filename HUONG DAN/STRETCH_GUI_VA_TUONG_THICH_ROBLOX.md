# Stretch GUI và tương thích Roblox

## Backend thử nghiệm

- Framebuffer/Metal drawable, UIKit frame, projection GUI, clipping và tọa độ chạm giữ kích thước native.
- Stretch chỉ đổi aspect truyền vào perspective của camera cảnh chính. Không hook perspective của shadow, ViewportFrame hoặc matrix orthographic dùng chung.
- Resolver nhận diện `tanf(FOV/2)`, phép chia hai trường width/height và đường copy camera chính; offset, register và RVA lấy từ IPA hiện tại.
- Backend này giữ hình kéo ngang, **không giảm số pixel render**. FPS có thể giảm vì target rộng hơn. Giảm riêng pixel cảnh cần xác minh đường upscale cảnh trước GUI; không thu nhỏ cả drawable để tránh méo GUI.
- Receipt schema 4 ghi `sceneAspectCalls/Reads`, aspect đầu vào/đầu ra, `guiTarget=NATIVE`, `pixelReduction=false`. Counter không xác nhận hình ảnh hay hit-testing trên thiết bị.

## Actions khi Roblox cập nhật

1. Kiểm tra phiên bản App Store; tải IPA khớp chính xác `CFBundleShortVersionString`. Mirror dự phòng dùng phiên bản vừa phát hiện; không fallback URL/offsets của bản cũ.
2. Dump lại map flag có kiểu dữ liệu; xác minh UUID/SHA256, segment và map. Kiểm tra font assets/import, framebuffer observer, main scene aspect, RenderJob idle, acquire/null/texture/context/command buffer.
3. Mọi profile phải trùng UUID/SHA256. Phải tìm đủ FPS roles và 8/8 patch trước compile. Thiếu hoặc mơ hồ thì Actions thất bại, không advance checkpoint hay upload IPA mới.
4. Build Debug bằng macOS SDK; chạy bridge import/budget, native backend, ARM64 trampoline qua dyld và Metal texture/completion tests. Release cần gate tương tự và kiểm thử thiết bị.
5. Xuất `compatibility_profile.json`, source identity, reports và IPA. Build thành công là candidate; `deviceVerified=false` cho đến khi thử trên iPhone.

Manual Debug có thể chọn `source_version` chính xác để kiểm thử binary cũ khi nhà phân phối chưa có IPA mới. Bản này giữ đúng nhãn phiên bản và không advance checkpoint App Store; schedule/push vẫn bắt buộc nguồn khớp App Store hiện tại.

2.741 là binary thật đã kiểm tra resolver; fixtures kiểm tra relocation, thay field offsets và từ chối dữ liệu sai. Chưa có binary 2.742 để chứng minh tương thích thực tế. Nếu compiler/ABI thay đổi ngoài contract, cần bổ sung resolver; không cam kết mọi phiên bản tương lai tự hoạt động.

## Gate iPhone

So sánh 100%/75%/50%: cảnh kéo đúng; menu, joystick, chữ, clipping và thao tác chạm không lệch. Thử cold launch qua Masterstrap, warm update, mở Roblox trực tiếp, xoay màn hình, điện năng yếu bật/tắt và resize khi GPU đang bận. Flag/font/FPS cần tiếp tục hoạt động; đối chiếu frame GPU hoàn tất, không dùng riêng overlay FPS.

Bridge vẫn dùng đủ năm tài liệu qua v3 local; payload lớn dùng v2 Durable Object. Không thêm polling, request theo feature, D1 writes/reads hoặc upload log. Chi phí licence/quảng cáo tính riêng.
