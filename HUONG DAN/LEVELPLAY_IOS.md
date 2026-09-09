# LevelPlay trên Masterstrap iOS

## Phạm vi quảng cáo

Chính sách iOS được giữ đồng nhất với Android:

- Gói Free: `Save & Launch`, game preset và FPS cao yêu cầu rewarded hoàn tất.
- Gói Plus: 120/144 FPS được dùng trực tiếp; 240/300 FPS yêu cầu rewarded.
- Gói Max: không có quảng cáo bắt buộc.
- FastFlag Editor: interstitial cho Free, nhưng fail-open khi không có inventory.
- Settings: banner chỉ hiển thị cho Free và bị hủy khi đóng Settings.

Rewarded luôn fail-closed. Callback đóng quảng cáo không được xem là phần thưởng; chỉ callback
`didRewardAdWithAdInfo:reward:` mới cho phép hành động tiếp tục.

## GitHub Actions secrets bắt buộc

Tạo đúng bốn repository secrets sau bằng thông tin của ứng dụng **iOS** trong LevelPlay:

- `IOS_LEVELPLAY_APP_KEY`: `28072d93d`
- `IOS_LEVELPLAY_REWARDED_ID`: `Rewarded_iOS`
- `IOS_LEVELPLAY_BANNER_ID`: `Banner_iOS`
- `IOS_LEVELPLAY_INTERSTITIAL_ID`: `Interstitial_iOS`

Không dùng lại ID Android. Mã nguồn và workflow đã có fallback mặc định các ID trên, tự động nhúng `SKAdNetworkItems` của ironSource & Unity Ads vào `Info.plist`.

Workflow tải SDK chính thức LevelPlay 9.6.0, Unity Ads Adapter 5.11.0 và Unity Ads iOS SDK 4.20.0 trực tiếp trong runner macOS, liên kết bằng Xcode và chép
`PrivacyInfo.xcprivacy` vào bundle. SDK/xcframework không được commit vào Git để tránh tăng dung lượng.
Workflow dùng runner `macos-26` vì LevelPlay 9.6 yêu cầu Xcode 26.

Trước lần khởi tạo SDK đầu tiên, ứng dụng bắt buộc người dùng chọn quảng cáo cá nhân hóa hoặc
không cá nhân hóa. Lựa chọn GDPR/CCPA được lưu cục bộ và truyền qua `LPMPrivacySettings` trước
`LevelPlay init`; ứng dụng không tự suy đoán sự đồng ý.

## Cấu hình Roblox dùng một lần

Roblox patched chỉ nhận cấu hình của phiên được mở qua Masterstrap. Sau khi nhận và áp dụng lần đầu:

- các JSON/XML cấu hình và giấy phép phiên bị xóa;
- một receipt không chứa FastFlag nhạy cảm được giữ lại để chẩn đoán;
- persistence 30 giây chỉ dùng bản sao trong bộ nhớ;
- mở Roblox patched trực tiếp sẽ không dùng lại cấu hình của phiên trước.

Các log cần kiểm tra trên thiết bị gồm `[ADS]`, `[SESSION][AUTH]`, `[SESSION][PURGE]` và
`[SESSION][CONSUME]`.
