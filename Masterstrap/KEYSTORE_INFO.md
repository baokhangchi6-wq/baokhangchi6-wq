# Masterstrap Release Keystore & App Signing Info

Tài liệu này lưu trữ thông tin chữ ký số phát hành chính thức của ứng dụng **Masterstrap** để phục vụ việc tải lên và cập nhật phiên bản trên **Google Play Console**.

---

## 1. Thông Tin File Keystore

* **Đường dẫn file:** `Masterstrap/app/masterstrap-release-key.jks`
* **Định dạng:** `PKCS12` (Chuẩn bảo mật quốc tế của Oracle & Android)
* **Key Alias:** `masterstrap_key`
* **Mật khẩu Keystore/Khóa:** Không lưu trong repository. Cấp qua
  `MASTERSTRAP_STORE_PASSWORD` và `MASTERSTRAP_KEY_PASSWORD` ở môi trường
  build cục bộ an toàn.
* **Thời hạn hiệu lực:** `10.000 ngày` (Có hiệu lực đến ngày **10 Tháng 1, 2054**)
* **Chủ sở hữu (DName):** `CN=Masterstrap, OU=Mobile, O=Masterstrap, L=Global, ST=Global, C=VN`

---

## 2. Mã Băm Chứng Thực (Certificate Fingerprints)

Các mã băm này được dùng khi khai báo dịch vụ Google Play Console, Google Cloud Console, Google AdMob và Firebase:

### SHA-256 Fingerprint:
```text
30:5D:95:AD:09:6E:5B:7D:EB:7C:49:BF:8E:A4:D9:6C:3C:CD:15:D1:49:E5:6D:2B:2B:59:8C:03:42:8C:66:13
```

### SHA-1 Fingerprint:
```text
83:FC:B4:7D:80:0A:EF:E7:86:C5:53:10:3E:0E:BE:2F:7B:63:04:E2
```

---

## 3. Đường Dẫn File Bản Dựng Google Play (`.aab`)

* **File Android App Bundle:**
  `Masterstrap/app/build/outputs/bundle/release/app-release.aab`

---

## 4. Hướng Dẫn Build Cho Các Lần Cập Nhật Sau

Mỗi khi bạn muốn cập nhật phiên bản mới lên Google Play:

1. Mở file `Masterstrap/app/build.gradle.kts` và tăng số phiên bản:
   ```kotlin
   versionCode = 14 // Tăng thêm 1 số so với bản 1.3 hiện tại
   versionName = "1.4" // Tên hiển thị phiên bản tiếp theo
   ```
2. Chạy lệnh build duy nhất:
   ```powershell
   $env:JAVA_HOME="C:\jdk17\jdk-17.0.2"; $env:PATH="C:\jdk17\jdk-17.0.2\bin;$env:PATH"; .\gradlew bundleRelease
   ```
3. Lấy file `app-release.aab` mới tạo trong thư mục `Masterstrap/app/build/outputs/bundle/release/` để tải lên Google Play Console.
