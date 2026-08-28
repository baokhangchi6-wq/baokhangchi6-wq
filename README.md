# Masterstrap Roblox iOS Dumper

Công cụ này phân tích tĩnh IPA hoặc Mach-O Roblox iOS ARM64/ARM64e, chọn đúng
engine Mach-O, rồi xuất RVA có bằng chứng cho `Offsets.hpp` và `FFlags.hpp`.
Pipeline iOS độc lập với dumper ELF Android trong `tools/`.

## Phạm vi

- Đọc IPA/ZIP, `Info.plist`, thin/fat Mach-O, segment, section, UUID và trạng thái mã hóa.
- Ưu tiên `RobloxLib.framework/RobloxLib` khi launcher chính không chứa engine.
- Tìm offset từ exact cstring + ARM64 xref + function prologue.
- Tìm FastFlag từ hàm đăng ký, call site, tên/suffix, kiểu lệnh ghi trực tiếp và storage writable.
- Chỉ xuất RVA tương đối với image base. Không xuất địa chỉ runtime đã cộng ASLR slide.
- Không giải mã IPA và không vượt cơ chế bảo vệ của App Store.

IPA, binary và thư mục `output/` đều bị `.gitignore`; chỉ mã nguồn, test và workflow
được đưa vào repository.

## Chạy ở chế độ Debug/evidence

Từ thư mục `roblox/IOS`:

```powershell
python -m roblox_ios_dumper inspect .\Roblox.ipa
python -m roblox_ios_dumper dump .\Roblox.ipa -o .\output\debug
python -m roblox_ios_dumper verify .\output\debug --input .\Roblox.ipa
```

Khi IPA chứa nhiều Mach-O, có thể khóa member để tái lập chính xác:

```powershell
python -m roblox_ios_dumper dump .\Roblox.ipa `
  --member Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib `
  -o .\output\debug
```

## Ý nghĩa kiểm chứng

Lệnh `verify` thất bại nếu phát hiện một trong các lỗi quan trọng:

- SHA-256, LC_UUID, kiến trúc hoặc image base không khớp input.
- Offset hàm không nằm trong segment executable.
- FastFlag storage không nằm trong segment writable.
- `address != image_base + RVA`.
- Thiếu bằng chứng lệnh ghi trực tiếp hoặc prefix kiểu không hợp lệ.
- `Offsets.hpp`/`FFlags.hpp` khác báo cáo JSON.
- Tỷ lệ giải mã call site thấp hơn ngưỡng (mặc định 70%).

`PASS` là bằng chứng tĩnh mạnh rằng kết quả thuộc đúng binary và đúng loại vùng nhớ;
nó không chứng minh consumer Roblox đã đọc giá trị đó ở runtime. Xác nhận runtime cần
internal diagnostic build trên thiết bị iOS được phép kiểm thử: log LC_UUID, ASLR
slide, địa chỉ `loaded_base + RVA`, quyền mapping và readback đúng kích thước kiểu.
Một smoke test nên dùng flag hiển thị vô hại, so sánh trạng thái trước/sau và không
đưa IPA hay dữ liệu tài khoản vào GitHub Actions.

## GitHub Actions hỗ trợ gì

Workflow `ios-dumper-validation.yml` chạy parser/scanner/verifier trên fixture Mach-O
tổng hợp và tải báo cáo kiểm chứng làm artifact. Nó phát hiện regression thuật toán,
sai segment/RVA, sai header và sai identity. GitHub-hosted runner không có IPA riêng
của người dùng hoặc thiết bị iOS nên không thể xác nhận tác dụng runtime. Với binary
nội bộ hợp pháp, có thể chạy cùng lệnh trên self-hosted runner và giữ IPA ngoài repo.

## Kết quả thử nghiệm hiện tại

Với fixture thật đang giữ cục bộ (không commit), dumper đã:

- Chọn `RobloxLib.framework/RobloxLib` ARM64 thay cho launcher nhỏ.
- Xác nhận input không mã hóa, đồng nhất UUID/SHA-256.
- Giải mã 13.261/15.325 call site đăng ký (86,55%).
- Xác định `FFlagDebugDisplayFPS` và `DFIntTaskSchedulerTargetFps` trong `__DATA`
  bằng lệnh ghi trực tiếp.

Các cảnh báo shared-storage là candidate cần rà soát; không nên xem chúng như xác
nhận runtime. Flag không có call site/bằng chứng đủ mạnh sẽ không được tự bịa địa chỉ.
