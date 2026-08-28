# Masterstrap Mobile (Frontend Shell Edition)

> ⚠️ **THÔNG BÁO QUAN TRỌNG VỀ KIẾN TRÚC DỰ ÁN:**  
> **Đây là dự án Vỏ (Frontend Launcher & Configurator), không chứa tính năng cốt lõi hoặc mã can thiệp bộ nhớ.**  
> Nếu anh/chị cần nghiên cứu, sửa đổi hoặc biên dịch lõi Native Hook C++ (`libmasterstrap.so`), thuật toán SmartSlot FastFlag và quy trình đóng gói Roblox Patched, vui lòng truy cập và làm việc tại thư mục mã nguồn chính:  
> 📂 **[`C:\Users\Administrator\Desktop\Chevstrap-main\.idea\app`](file:///C:/Users/Administrator/Desktop/Chevstrap-main/.idea/app)**

---

## 📱 1. Giới thiệu Tổng quan

**Masterstrap Mobile (Frontend Shell)** là ứng dụng giao diện độc lập được thiết kế chuẩn tỉ lệ màn hình dọc 9:16 với phong cách **Modern White Minimalist Theme** (chủ đề màu trắng tinh tế, sắc nét). 

Ứng dụng được thiết kế nhằm mục đích **100% tuân thủ chính sách nhà phát triển của Google Play Store**:
- Không sử dụng quyền Root / Shizuku.
- Không chứa mã nhị phân can thiệp bộ nhớ (`ptrace`, `mprotect`, Native C++ Hook).
- Đóng vai trò là một **Trình quản lý Cấu hình Game (FastFlag & Preset Configurator)** chuyên nghiệp, an toàn tuyệt đối.

---

## 🏗️ 2. Mô hình Hoạt động 2 Tầng (Shell & Core Engine)

Masterstrap hoạt động theo mô hình tách biệt tương tự như cách **Bloxstrap** hoạt động trên Windows:

```text
┌──────────────────────────────────────────────────────────────────┐
│ 1. MASTERSTRAP VỎ (Frontend Shell - Google Play Safe)            │
│    - Giao diện 9:16 Light Theme hiện đại.                        │
│    - Quản lý FastFlags qua DataGrid, Toggle cờ có sẵn, Game Mode.│
│    - Xuất file cấu hình văn bản tiêu chuẩn:                      │
│      ├── ClientAppSettings.json                                 │
│      └── GlobalBasicSettings_13.xml                              │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                     (Lưu file cấu hình vào máy)
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│ 2. ROBLOX PATCHED (Core Engine - Tự động nạp cấu hình)           │
│    - Nhúng sẵn lõi `libmasterstrap.so` (biên dịch từ NDK).       │
│    - Khi khởi động: Tự động đọc cấu hình từ 2 file JSON & XML.   │
│    - Kích hoạt mở khóa FPS, tối ưu hóa đồ họa trực tiếp vào Game.│
└──────────────────────────────────────────────────────────────────┘
```

---

## 📁 3. Vị trí Lưu trữ Cấu hình Tiêu chuẩn

Khi người dùng thao tác bấm nút **Save** hoặc **Save and Launch**, ứng dụng sẽ tự động đồng bộ và xuất 2 tệp cấu hình vào các đường dẫn sau để **Roblox Patched** nhận diện ngay lập tức:

| Tệp Cấu hình | Mục đích | Đường dẫn Lưu trữ Chuẩn |
|---|---|---|
| **`ClientAppSettings.json`** | Chứa toàn bộ FastFlags tùy chỉnh, FPS unlocker, cờ tối ưu đồ họa | `/sdcard/Masterstrap/ClientAppSettings.json`<br>`/sdcard/Android/data/com.roblox.client/files/ClientSettings/ClientAppSettings.json`<br>`/sdcard/Android/data/com.roblox.client.vnggames/files/ClientSettings/ClientAppSettings.json` |
| **`GlobalBasicSettings_13.xml`** | Cấu hình tham số engine, thiết lập render và camera | `/sdcard/Masterstrap/GlobalBasicSettings_13.xml`<br>`/sdcard/Android/data/com.roblox.client/files/GlobalBasicSettings_13.xml`<br>`/sdcard/Android/data/com.roblox.client.vnggames/files/GlobalBasicSettings_13.xml` |

---

## ✨ 4. Tính năng Giao diện Chính

1. **Tab 1: FastFlag DataGrid Manager:**
   - Hiển thị danh sách cờ dưới dạng bảng dữ liệu trực quan (Flag Name, Type: Boolean/Integer/String, Value).
   - Hỗ trợ thanh tìm kiếm thời gian thực, thêm cờ mới, chỉnh sửa giá trị và xóa cờ.
   - Hỗ trợ Import / Export danh sách cờ từ tệp `.json`.

2. **Tab 2: Essential Toggles (Công tắc Cờ Nhanh):**
   - **FPS Unlocker:** Lựa chọn nhanh các mốc FPS: `60`, `120`, `144`, `240`, `Uncapped (Vô hạn)`.
   - **Lighting Technology:** Chuyển đổi Future Lighting (Phase 3), ShadowMap, Voxel.
   - **Graphics Optimization:** Giảm tải texture, tắt bóng đổ chi tiết, tối ưu hiệu năng cho máy yếu.
   - **Network & Fast Loading:** Bật nạp trước asset bản đồ, giảm giật ping.

3. **Tab 3: Game Mode (Chế độ Chơi Game Chuyên Nghiệp):**
   - Khóa tỉ lệ hiển thị và tối ưu độ nhạy cảm ứng.
   - Cấu hình mức tiêu thụ bộ nhớ RAM tối ưu cho game nặng.

4. **Thanh 3 Nút Hành Động Cố Định (Bottom Action Bar):**
   - 🔴 **Close:** Đóng hoặc thu nhỏ ứng dụng.
   - 💾 **Save:** Lưu toàn bộ thiết lập ra các tệp JSON và XML.
   - 🚀 **Save & Launch:** Lưu thiết lập và tự động mở ứng dụng Roblox (Global hoặc VNG) ngay lập tức.

---

## 🌍 5. Hỗ trợ Đa Ngôn ngữ (Localization)

Giao diện được bản địa hóa sang hơn 20 ngôn ngữ phổ biến trên thế giới (Việt Nam, Anh, Trung Quốc, Tây Ban Nha, Nhật Bản, Hàn Quốc, Nga, Pháp, Đức, Thái Lan, Indonesia, v.v.) nằm tại thư mục tài nguyên `res/values-*/strings.xml`.

---

## 🔗 6. Liên kết Dự án Liên quan

- **Mã nguồn Lõi C++ NDK & APK Patched:** [`C:\Users\Administrator\Desktop\Chevstrap-main\.idea\app`](file:///C:/Users/Administrator/Desktop/Chevstrap-main/.idea/app)
- **Hạ tầng Tự động hóa Offsets 24/7:** [`C:\Users\Administrator\Desktop\Website`](file:///C:/Users/Administrator/Desktop/Website)
- **Máy chủ Dữ liệu Trực tuyến:** `https://masterstrap.org/Data/Android/`
