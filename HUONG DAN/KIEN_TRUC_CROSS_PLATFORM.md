# Báo Cáo Phân Tích Kiến Trúc Cross-Platform (iOS, Android, macOS, Linux, Windows)

Tài liệu so sánh chi tiết cơ chế phân tích RVA, hook bộ nhớ, và nạp FastFlags giữa các hệ điều hành khác nhau.

---

## 1. BẢNG SO SÁNH CƠ CHẾ KỸ THUẬT THEO NỀN TẢNG

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 ROBLOX ENGINE (C++ / Luau)                             │
│                         Quản lý FastFlag Tập Trung (Global Registry)                   │
├─────────────────────┬──────────────────────┬─────────────────────┬─────────────────────┤
│      iOS / iPadOS   │       Android        │        macOS        │    Linux (Sober)    │
│  (ARM64 / Mach-O)   │   (ARM64-v8a / ELF)  │  (ARM64 & x86_64)   │   (x86_64 / ELF)    │
├─────────────────────┼──────────────────────┼─────────────────────┼─────────────────────┤
│ Mach-O Header       │ ELF .dynamic / phdr  │ Mach-O Header       │ ELF /proc/self/maps │
│ _dyld_image_count   │ dlopen / dlsym       │ _dyld_image_count   │ dlopen / dlsym      │
│ mprotect (POSIX)    │ mprotect (POSIX)     │ mprotect (POSIX)    │ mprotect (POSIX)    │
│ LC_LOAD_DYLIB       │ DT_NEEDED / Ptrace   │ LC_LOAD_DYLIB       │ LD_PRELOAD / Hook   │
└─────────────────────┴──────────────────────┴─────────────────────┴─────────────────────┘
```

---

## 2. CHI TIẾT TỪNG NỀN TẢNG

### 1. iOS / iPadOS (ARM64)
* **Binary Target**: `Roblox.app/Frameworks/RobloxLib.framework/RobloxLib` hoặc `Roblox.app/Roblox`.
* **Cơ chế Base Address**: Sử dụng `_dyld_get_image_header(index)` quét tên `RobloxLib`.
* **Cơ chế Hook**: Nhúng `libmasterstrap.dylib` qua lệnh nạp `LC_LOAD_DYLIB` vào Mach-O header.
* **Cơ chế FastFlag**: Can thiệp trực tiếp vào RVA bộ nhớ RAM kết hợp nạp động `ClientAppSettings.json` từ thư mục Documents.

### 2. Android (ARM64-v8a / ARM32)
* **Binary Target**: `libroblox.so` hoặc `libRoblox.so`.
* **Cơ chế Base Address**: Quét bản đồ bộ nhớ `/proc/self/maps` hoặc gọi `dlopen("libroblox.so", RTLD_NOLOAD)`.
* **Cơ chế Hook**: Zygisk module / Xposed / can thiệp trực tiếp qua dylib-loader (`libclient.so`).
* **Cơ chế FastFlag**: `ConfigExporter` ghi file cấu hình vào `/sdcard/Download/Masterstrap/ClientAppSettings.json` và thư mục `Android/data/com.roblox.client/files/ClientSettings/`.

### 3. macOS (Apple Silicon ARM64 & Intel x86_64)
* **Binary Target**: `RobloxPlayer.app/Contents/MacOS/RobloxPlayer`.
* **Cơ chế Base Address**: `_dyld_get_image_header(0)`.
* **Cơ chế Hook**: `DYLD_INSERT_LIBRARIES` hoặc inject `LC_LOAD_DYLIB`.
* **Cơ chế FastFlag**: `ClientAppSettings.json` đặt tại `~/Library/Application Support/Roblox/ClientSettings/`.
* **Đặc điểm**: Hoạt động tương tự 100% như trên iOS vì cùng chia sẻ nhân Darwin, định dạng Mach-O và API `_dyld`.

### 4. Linux (x86_64)
* **Kịch bản A (Sober / Flatpak)**:
  * Chạy trực tiếp nhân Roblox mobile ELF trên nền tảng Linux x86_64/ARM64.
  * Phương pháp dump và kiểm tra offset tương thích 100% với chuẩn ELF Linux.
* **Kịch bản B (Vinegar / Proton / Wine)**:
  * Chạy Roblox Windows `RobloxPlayerBeta.exe`.
  * Roblox Windows có hệ thống Hyperion (Byfron) bảo vệ RAM, do đó phương pháp can thiệp RAM bằng hook có thể bị chặn.
  * Tuy nhiên, cơ chế FastFlag qua file `ClientAppSettings.json` đặt tại `%localappdata%/Roblox/ClientSettings/` hoàn toàn **hợp lệ 100%** và không bị Byfron can thiệp.

---

## 3. KẾT LUẬN CHUNG

Phương pháp kiểm tra Offsets + FastFlags qua `FFlags.hpp` / `Offsets.hpp` và xác thực qua file log mà Masterstrap đang sử dụng là **chuẩn mực kỹ thuật phổ quát (Universal Technique)**. 

Quy trình này có thể tái sử dụng ngay lập tức trên cả **Android**, **macOS** và **Linux** mà không cần thay đổi cấu trúc dữ liệu cốt lõi.

