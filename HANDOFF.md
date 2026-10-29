# HANDOFF iOS — Masterstrap (khởi tạo 2026-10-29) [ACTIVE]

Nền tảng: iOS (iPhone 14 Pro Max, cài bằng Sideloadly trên Windows, KHÔNG có Mac, build qua GitHub Actions `macos-15`).
Android (`C:\Users\Administrator\Desktop\Masterstrap\Android`) chỉ là tài liệu tham khảo, KHÔNG sửa.
Mục tiêu: 10/10 tính năng như Android, KHÔNG gắn cứng một phiên bản Roblox; mọi manh mối phải đủ để workflow tự dựng lại khi Roblox update.

## 1. Chiến lược theo tính năng (user chốt 2026-10-29)
| # | Tính năng | Độ khó | Cơ chế | Trạng thái |
|---|---|---|---|---|
| 1 | FPS unlock 200-500 (không cap theo Hz) | cực khó | vá nhị phân clamp + CADisplayLink range + XML FramerateCap | [ACTIVE] chưa đạt: presented ≈ 60 |
| 2 | Stretch / Shader / Wet Floor | trung bình | hook `luau_load` rồi chạy Luau (`C:\Users\Administrator\Desktop\Masterstrap\Script`) | [ACTIVE] chưa đạt: ABI gate đóng |
| 3 | Flag / Live Flag | dễ | hook `setfflag` | [ACTIVE] chưa đạt: hiện chỉ ghi direct-RVA, Live Flag không có |
| 4 | Font | cực dễ | remap asset có sẵn trong IPA, không hook | [ACTIVE] remapCount=13, chưa xác nhận bằng mắt |
| 5 | 7 offsets / Safe / Turbo | dễ | cập nhật dumper bằng LeanREA/REA; Safe/Turbo = tối ưu API ngoài, đúng sandbox iOS | [ACTIVE] 7 offsets chưa hook thật |

## 2. Phát hiện từ log cũ (Log/Roblox, 2026-10-15 03:02, build e0c11d1, Roblox 2.741.1062, deviceVerified=false)
Nguồn: seen = đọc trực tiếp receipt.
- seen: `MasterstrapAbiProbe.json` mọi role `opcode=0x00000000`. NGUYÊN NHÂN (seen): `tools/build_integrated_roblox_ipa.py:2011` gán cứng `uint32_t opcode = 0` (cố ý không đọc code memory). Đây KHÔNG phải RVA sai. `callGate` luôn `closed` (dòng 1999 gán cứng 0).
- seen: 3 role `AMBIGUOUS_RVA`: `luau_load` (aliases=4), `game_loaded` (aliases=2), `loadstring` (aliases=2). Hàm `offset_role_count(rva)` đếm số role cùng RVA.
- seen: Stretch `gateOpen=true percent=50 frameReads=0 sceneAspectCalls=904 sceneAspectReads=0 sceneInputAspect=2.16744 sceneOutputAspect=2.16744 sceneRejectedReason=3 observedDrawable=2796x1290`. Hook `UIView.frame` không chạy; hook scene-aspect bị từ chối lý do 3 (mã lý do: tra `tools/ios_stretch.c`, chưa giải mã).
- seen: FPS `requestedFps=500 policyTargetFps=500 screenMaximum=500` nhưng `hardwareMaximumFps=120`, `presentedPerSecond=59.955`, `commitsPerSecond=119.578`, `nextDrawableWaitAverageMs=12.883`, `lowPowerActual=true` (FrameReceipt) trong khi DisplayReceipt ghi `lowPower=false`. `screenMaximum` ghi sai (phải là 120 trên 14 Pro Max).
- seen: Flag: `ClientAppSettings.json` 25 key, `ConsumedReceipt.cachedFlags=20` (lệch 5, chưa tra flag nào mất). Cấu hình bị xóa sau consume đầu (`deletedFiles=5`, `reusable=false`) nên không có Live Flag.
- seen: `MasterstrapRuntimeReceipt.json` `boost.status=inactive` dù TurboProfile `game_mode_master=true`.
- seen: log `[FEATURE_10_STEALTH] ... offsetsLoaded=7/7 runtimeHooks=0` (`build_integrated_roblox_ipa.py:2783`): chỉ kiểm tra tên, KHÔNG hook thật.
- seen: commit mới nhất `185ac7d` (2026-10-15 04:13) CHƯA có log thiết bị. Log trong `Log/Roblox` là của `e0c11d1`.

## 3. Offsets iOS đã có (Offsets.hpp trong thư mục gốc, Roblox 2.741.1062 theo `last_built_roblox_ios_version.json`; ARM64) [ACTIVE cho 2.741, DEPRECATED khi đổi bản]
- setfflag = 0x479C4A8
- game_loaded = 0x6B5A10
- luau_load = 0x4246884
- task_spawn = 0x18AF128
- lua_resumecheck = 0x4229244
- lua_resume = 0x42266E0
- Probe (từ AbiProbe.json): lua_gettop 0x4219124, lua_settop 0x4219138, lua_pushvalue 0x4219504, lua_type 0x42195e4, lua_getfield 0x421b10c, lua_setfield 0x421b30c, lua_pushstring 0x421a940, lua_pushlstring 0x421a874, lua_tostring 0x421a2ec, lua_toboolean 0x4219c50, lua_next 0x421c59c, scriptcontextresume 0x18d67a4, resumewaitingscripts 0x191d128, loadstring 0x18a7a2c, enableloadmodule 0x18a75a0.
- Patch bypass signature bytecode: [ACTIVE] iOS mọi bản (2026-10-29) `patch_luau_load_verify()` trong `tools/build_integrated_roblox_ipa.py`. Cách resolve (không hard-code RVA): xref chuỗi `%s: bytecode version mismatch (expected [%d..%d], got %d)` -> hàm chứa (luau_load) -> trong 0x800 byte đầu tìm DUY NHẤT bộ ba `bl <check>` (mask 0xfc000000 == 0x94000000), `cmp w0,#1` (word `7100041f`), `b.ne` (mask 0xff00001f == 0x54000001); đổi b.ne -> `b` cùng đích (imm19 -> imm26). Không đúng 1 ứng viên = fail-closed (Release raise). Xuất `output/luau_load_patch_profile.json`. Đã kiểm trên 2.741.1062 (UUID 8066b0dc-2286-3b33-b7c3-589a33daab87): luau_load 0x4246884, gate 0x42469f8, `c1030054` -> `1e000014`, đích 0x4246a70. Chưa kiểm trên 2.744 (chờ IPA); nếu profile báo `unresolved` thì đọc `gateCandidates`/`luauLoadCandidates` rồi chỉnh mẫu.
- Log 7 offsets đổi `ACTIVE_ALL/ACTIVE_PARTIAL` -> `LOADED_NOT_HOOKED/PARTIAL_NOT_HOOKED` (đã tải offset, runtimeHooks=0).
- Fetch IPA: bỏ fallback cứng `Delta-2.741.1062.ipa`; scrape lỗi thì fail rõ thay vì tải nhầm bản cũ. Bỏ đường dẫn `2.735.1135` và tên `Masterstrap-2.741.1062.ipa` cứng trong `check_ci.py`.
- 7 Stealth Offsets (ARCHITECTURE.md, Roblox 2.740, bảng bị cắt cụt, thiếu hàng 4): RbxAbortLogger 0x170F4F8, RbxReportbytecode 0x1860204, LuauValidateheader 0x410F4B4, ValidateCaller 0x188651C, Validateandsetupcaps 0x18572E0, TelemetryBuffer 0x462A1A0. Hàng 4 (Android gọi là ScriptTimeoutCheck) CHƯA có trên iOS. [DEPRECATED cho 2.744, phải dump lại]

## 4. Android tham chiếu (seen, đọc không sửa)
- Android pin ABI theo Build ID + chữ ký 32 byte cho từng role: `Android\.idea\app\src\main\jni\masterstrap_shader_abi.hpp` (kLuaLoad, kTaskSpawn, kLuaResume, kUgcGameLoaded... mỗi role có `k<Role>Signature` 32 byte) và `profiles\<version>\<lane>\offsets.hpp`. Hướng iOS: resolve bằng chữ ký byte, không tin RVA cũ.
- Android 7 offsets (2.744.1081 arm64): RbxAbortLogger 0x2b87fd8, RbxReportbytecode 0x42b3ee0, LuauValidateheader 0x2603edc, ScriptTimeoutCheck 0x2608188, ValidateCaller 0x2607d00, Validateandsetupcaps 0x1ef68fc. KHÔNG dùng RVA Android cho Mach-O.

## 5. Ràng buộc kỹ thuật iOS (inferred, cần xác minh trên máy)
- Sideload không có JIT/Dobby: hook = vá tĩnh trong `RobloxLib` lúc đóng gói (nhánh vào TEXT padding trỏ tới callback trong segment RW riêng của `libmasterstrap.dylib`), runtime không ghi executable memory. `ARCHITECTURE.md` mục "Role callbacks" và `COMPATIBILITY.md` mô tả cùng hướng.
- `libmasterstrap.dylib` là dylib DUY NHẤT được thêm. Purge `libgloop`, `delta.dylib`.
- Bridge: ticket cloud 60s dùng một lần; payload nhỏ protocol 3 (ngưỡng 2.048 ký tự), lớn protocol 2 (`rules.md` mục 4-6).

## 6. xtool (tra cứu 2026-10-29)
- Chạy Linux/WSL/macOS, KHÔNG Windows native. Máy có WSL2 Ubuntu (đang Stopped).
- Build SwiftPM -> iOS app, ký và cài bằng Apple ID. Cần Darwin SDK từ Xcode.xip.
- unknown: ký lại IPA có sẵn; link `.xcframework`/static framework (LevelPlay); target Obj-C/C.
- `build_masterstrap_app_ipa.py` link LevelPlay 9.6.0 bằng `xcrun clang -force_load` + Swift runtime từ Xcode 26: không phải SwiftPM.
- Hướng thử: lấy Darwin SDK bằng xtool trong WSL, chạy script Python hiện có với clang+lld trỏ SDK đó; ký bằng công cụ Windows (Sideloadly/zsign). Thử Roblox patched trước, `com.masterstrap.app` sau (LevelPlay).

## 7. Việc tiếp theo (đã được user đồng ý bước 1)
1. Cần IPA Roblox iOS 2.744 decrypted (local chỉ có `roblox/extracted/RobloxLib` của 2.741 và `output/Masterstrap-2.741.1062.ipa`). Nguồn tự động: `tools/fetch_roblox_ios_ipa.py` (Delta, `https://bstshrt.com/u/delta-ios`) hoặc secret `IOS_IPA_URL`.
2. Build lại từ commit mới nhất, xóa log cũ (`Log/Roblox`, `Log/Masterstrap` đã archive), user chạy trên iPhone (tắt Low Power Mode) rồi gửi `masterstrap_ios.log`, `masterstrap_bridge.log` và receipt mới.
3. Sửa trước (rủi ro thấp): ghi đúng `screenMaximum`; thống nhất `lowPower`; log 7 offsets ghi rõ "runtimeHooks=0 / chưa hook".
4. Probe ABI đọc opcode thật (đọc từ file ở build time, không đọc runtime) để phân biệt ambiguous.
5. Hook `luau_load` -> Luau stretch/shader/wet floor; hook `setfflag` -> Flag + Live Flag.
6. Tự động hóa: workflow phải resolve role bằng chữ ký, fail-closed khi mơ hồ, và xuất profile theo phiên bản. Mỗi tính năng đạt thì ghi vào mục này (version, RVA nguyên văn, chữ ký, trạng thái).

## 8. Build Actions đầu tiên sau resolver (2026-10-29) [ACTIVE]
- Repo chạy Actions: remote `sub` = `https://github.com/baokhangchi6-wq/baokhangchi6-wq.git` (public, không giới hạn phút). `origin` = `Masterstrap/IOS-source` chỉ làm checkpoint, workflow đã disable. Push cả 2, chỉ `sub` chạy.
- Run `40798451635` (workflow_dispatch, commit `d7783a8`) success. Artifact `Masterstrap-Roblox-2.743.1134-d7783a8f07320f7708b65e001760be7419c062b9-run-255` (Delta hiện chỉ có 2.743.1134, chưa có 2.744).
- Nền tảng iOS, Roblox 2.743.1134 (CFBundleVersion 2.743.1134). RobloxLib gốc sha256 `49ed0244bee98fb4f0d8feac3c93e253188412172bc15f2555dcd02405857377`, UUID `8066b0dc-2286-3b33-b7c3-589a33daab87`.
- seen: 2.741.1062 gốc sha256 `8f7c2772c8c2fbd9b5e61dc28b732e0078400fa7b98113f5d0a447ca24b55152`, CÙNG UUID. Khác sha nhưng cùng UUID và 112/112 offset giống hệt 2.741 (2.743 thêm 2: fps_displaytiming_pacing 0x409D268, fps_devicemtl_present_timing 0x40A43C8). inferred: 2.741 -> 2.743 không đổi code engine, nên resolver CHƯA được kiểm trên engine khác.
- luau_load_patch_profile.json: `resolved-signature-site`, luau_load 0x4246884, gate 0x42469f8, `c1030054` -> `1e000014`.
- fps_patch_profile.json: resolved 9/9 (RVA = file offset): GetDefaultFramerateCap clamp fileOffset 74859440 `09b1891a`->`e903082a`; TaskScheduler dt fileOffset 9680192 `6bb18c1a`->`1f2003d5`; frametime calc 74870372 `e832881a`->`e803172a`; getTargetFps fallback 74859448 `88078052`->`08e28452`; AdaptiveSync schedule 67752316 `16c59f1a`->`16e28452`; refresh clamp 67752412 `b632961a`->`1f2003d5`; DisplayTiming 67752552 `a812961a8002231ea102231e0201231e`->`e803162a8002231ec102231e0201231e`; DeviceMtl presentDrawable 67781576 `cd2b0054`->`5e010014`; in-game refresh 67752396 `15c49f1a`->`f503162a`. fpsTargetSlot 0x67e1a70. runtimeEffect: unverified-requires-device-trace.
- compatibility_profile: STRUCTURAL_GATES_PASSED, deviceVerified=false. Chưa có log thiết bị 2.743.

## 9. Đọc log thiết bị đầu tiên của build d7783a8 (Roblox 2.743.1134, iPhone 14 Pro Max) — 2026-10-29 [ACTIVE]
Nguồn: `Log/Roblox/` các file mtime 2026-10-29 08:16 (`MasterstrapBuildReceipt.json` commit `d7783a8f07320f7708b65e001760be7419c062b9`, runId `40798451635`). File mtime 2026-10-15 trong cùng thư mục là log CŨ của e0c11d1, KHÔNG dùng so sánh. Roblox 2.743 trên máy là bản cũ nên server không cho vào Home/game: log chỉ phủ giai đoạn khởi động + bridge, KHÔNG có dữ liệu in-game (trackedLinks=0, frameReads=0).
Trạng thái nguồn IPA lúc ghi: App Store US = `2.744.11082` (phát hành 2026-10-26T16:16:18Z, minimumOsVersion 14.0); Delta (`https://bstshrt.com/u/delta-ios`, finalUrl `https://deltaexecutor.filesadda.com/Delta%20v2.743.1136.ipa`) = `2.743.1136`, CHƯA có 2.744. Việc phải làm khi Delta ra 2.744: chạy workflow trên `sub` (workflow_dispatch, `source_version` trống hoặc `2.744.<build>`), mở `luau_load_patch_profile.json` đầu tiên (phải `resolved-signature-site`), `fps_patch_profile.json` (resolved 9/9), so `Offsets.hpp` với mục 3/8; cài lên máy rồi mới lấy log mới. Lưu ý: cron `17 */3 * * *` trên `sub` sẽ tự build lại bản Delta (2.743) vì checkpoint `last_built_roblox_ios_version.json` còn 2.741.1062 và bước advance checkpoint chỉ chạy khi ipa_version == store_version.

### Đã hoạt động (seen trong log)
- Bridge: `protocol=3`, `Document writes: client=1 turbo=1 global=1 shader=1 font=1`, ticket `singleUse=1 ttlSeconds=180`, cold + warm launch, duplicate request bị bỏ đúng (`request=60768-1793236567-2`).
- UUID gate: `UUID status=MATCH expected=8066b0dc-2286-3b33-b7c3-589a33daab87`, RobloxLib base `0x10aea8000`, `Verified Engine Offsets Count: 114`, mọi OFFSET_MAP `executable=1`.
- Flag (direct-rva-write-v1): `Total Flags Requested: 49`, `Verified Flags Patched: 43`, `dynamicApplied=20`, `cachedFlags=22`, `userFlags=20`; 6 flag không có trong FFlags map: FFlagTaskSchedulerLimitTargetFpsToScreenRefreshRate, FFlagTaskSchedulerLimitTargetFpsTo2402, FFlagDebugDisableVsync, FFlagAutomaticDRSUseGpuTime, FFlagAnimationLodBoneEnabled, FFlagAdaptiveMeshCacheSizeForAndroid. `Engine SetFFlag Registry: not-installed` => chưa hook `setfflag` (0x479C4A8), chưa có Live Flag. Config bị xóa sau consume (`deletedFiles=5`, `reusable=false`).
- FPS: hook `WKDisplayLinkHandler displayLinkFired: abi=VERIFIED status=INSTALLED`, `preferredFrameRateRange min=60.0 max=120.0 preferred=120.0 target=120 status=MATCH`; GlobalBasicSettings_13.xml `FramerateCap=2000`. Giới hạn: `effective=120` (phần mềm tự kẹp theo Hz màn hình) — mục tiêu 200-500 FPS chưa đạt vì `presentedFps=UNMEASURED`, chưa vào game; 9 bản vá nhị phân clamp chưa có trace thiết bị.
- Font: `OBSERVED_ASSET_REMAP`, `importsInstalled=2`, `remapCount=3` (2.741 là 13), `[FONT][ASSET] remapped=1 sample=1..3 scope=ROBLOXLIB_STOCK_FAMILY`, `sizeStatus=UNSUPPORTED_KEEP_ORIGINAL`, `visualVerified=false`.
- Safe/Turbo: `[CAPABILITY][SAFE] status=ACTIVE thermalGuard=SYSTEM_RESPECTED zoneRelief=ACTIVE ramCleanup=UNSUPPORTED networkAcceleration=UNSUPPORTED`; `[CAPABILITY][TURBO] status=ACTIVE qosClass=USER_INTERACTIVE`. `[PROFILE][TURBO] plan=FREE -> GAME_MODE_REQUIRES_PLUS_MAX disabled` là chặn theo gói, không phải lỗi.
- 7 offsets: log mới `[FEATURE_10_STEALTH] status=LOADED_NOT_HOOKED offsetsLoaded=7/7 runtimeHooks=0` (sửa tên đã hiệu lực). `scripttimeoutcheck` đã có: RVA 0x1937630 (hàng 4 còn thiếu trước đây).

### Chưa hoạt động / cần sửa (seen)
- ABI probe: mọi role `opcode=0x00000000` (hard-code), `callGate=CLOSED reason=function-signatures-unverified`, `AMBIGUOUS_RVA`: luau_load (aliases=4, trùng luau_bytecodeload/luau_pushstringerror/luau_validateheader cùng RVA 0x4246884), game_loaded (aliases=2, trùng ongameloaded 0x6B5A10), loadstring (aliases=2, trùng luaL_sandboxthread 0x18A7A2C).
- Shader: `[CAPABILITY][SHADER] backend=luau-vm-avatar-engine status=BLOCKED reason=address-probe-only-luau-lifecycle-unverified abiPlausible=19/19`; `MasterstrapRuntimeReceipt.json shader.status=blocked_abi_lifecycle`. Nhưng `[FEATURE_06_SHADER] status=ACTIVE` và `[FEATURE_05_RENDERER] status=ACTIVE` ở ma trận 10/10 là DƯƠNG TÍNH GIẢ (cổng chỉ xét EngineBase+HookInstalled). Cần sửa ma trận ghi theo trạng thái thật.
- Stretch: `[CAPABILITY][STRETCH] backend=luau-cframe-renderstepped hookInstalled=0 binaryVerified=1`; `[FEATURE_08_STRETCH] status=ACTIVE` cũng dương tính giả. Receipt toàn 0 (chưa vào scene): `sceneAspectCalls=0 sceneAspectReads=0 sceneRejectedReason=0 frameReads=0`. Chưa thể đánh giá `sceneRejectedReason=3` của log cũ.
- `screenMaximum=2000` (receipt) sai: `readback max=120`. Chưa sửa.
- Lệch hai nguồn: Display receipt `lowPower=false`/`lowPower=0` — chưa thấy Frame receipt mới (không vào game).
- `boost.status=inactive` (Turbo tắt do gói FREE).
- iPhone cài 2.743 không vào được Home vì Roblox bắt cập nhật => chỉ test được khi có IPA 2.744.

### Đề xuất tiếp theo (chưa làm)
1. Sửa trước (không cần thiết bị): ma trận 10/10 ghi đúng trạng thái; `screenMaximum` lấy `maximumFramesPerSecond` thật; ABI probe đọc opcode từ file lúc build; tách role trùng RVA (luau_load vs validateheader, game_loaded vs ongameloaded, loadstring vs sandboxthread).
2. Khi có IPA 2.744: cài, vào game, lấy log mới (xóa/lưu `Log/Roblox` cũ trước), mới đánh giá FPS/Stretch/Shader/Live Flag.

## 10. Sửa cron chống build lặp (2026-10-29) [ACTIVE]
- Nguyên nhân (seen): gate cũ so App Store `2.744.11082` với checkpoint `2.741.1062` => luôn build; Delta chỉ có `2.743.1136` nên checkpoint (chỉ tiến khi ipa_version == store_version) không bao giờ tiến => build lại mỗi tick (runs #231-#242).
- Sửa: job `gate` chạy `ubuntu-latest` (sparse checkout `tools` + checkpoint, không pip, không macOS). Cron `17 */6 * * *`: `tools/fetch_roblox_ios_ipa.py --probe` lấy version từ URL Delta (`version_from_url`), rồi `tools/check_roblox_ios_version.py --source-version <ver>` so với `last_built_roblox_ios_version.json`. Job `build` (macos-15) chỉ chạy khi `should_build=true`. push/workflow_dispatch bỏ qua gate (dispatch theo `force_rebuild`).
- Checkpoint chỉ tiến trên run `schedule` sau khi build ra IPA, tiến tới version nguồn Delta (không phải App Store); `write_checkpoint` không bao giờ lùi version. Checkpoint đặt tay = `2.743.1136` (đã build 2.743.1134 thủ công, bản Delta hiện tại).
- Khi Delta ra `2.744.x`: cron tự build 1 lần rồi checkpoint tiến; không build lặp.
- Tải thẳng App Store (inferred, chưa kiểm bằng thực nghiệm): IPA App Store mã hóa FairPlay; pipeline cần Mach-O đã giải mã (`image.is_encrypted` chặn). Chỉ iTunes Lookup (version) dùng được trong CI. ipatool/Apple ID tải được IPA nhưng vẫn mã hóa, giải mã cần thiết bị jailbreak. Nên giữ Delta hoặc secret `IOS_IPA_URL`.
