import importlib.util
import inspect
from pathlib import Path
import plistlib
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def load_build_module(file_name: str, module_name: str):
    path = ROOT / "tools" / file_name
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AppBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roblox_builder = load_build_module(
            "build_integrated_roblox_ipa.py", "masterstrap_roblox_builder"
        )
        cls.app_builder = load_build_module(
            "build_masterstrap_app_ipa.py", "masterstrap_app_builder"
        )

    def test_registers_global_scheme_once(self):
        plist = {
            "CFBundleIdentifier": "com.roblox.robloxmobile",
            "CFBundleURLTypes": [],
        }
        scheme = self.roblox_builder.register_bridge_url_scheme(plist)
        self.roblox_builder.register_bridge_url_scheme(plist)

        self.assertEqual("masterstrap-rbx-bridge-v1", scheme)
        entries = [
            entry
            for entry in plist["CFBundleURLTypes"]
            if scheme in entry.get("CFBundleURLSchemes", [])
        ]
        self.assertEqual(1, len(entries))

    def test_registers_vng_scheme(self):
        plist = {"CFBundleIdentifier": "com.vng.roblox"}
        scheme = self.roblox_builder.register_bridge_url_scheme(plist)
        self.assertEqual("masterstrap-rbx-vng-bridge-v1", scheme)

    def test_roblox_plist_enables_high_refresh_presentation(self):
        plist = {}
        self.roblox_builder.configure_ios_runtime_plist(plist)
        self.assertIs(True, plist["CADisableMinimumFrameDurationOnPhone"])
        self.assertIs(True, plist["CADisableMinimumFrameDuration"])

    def test_sanitize_and_configure_roblox_plist_restores_global_bundle_and_purges_delta(self):
        plist = {
            "CFBundleIdentifier": "com.delta.client",
            "CFBundleDisplayName": "Delta",
            "CFBundleName": "Delta",
            "CFBundleURLTypes": [
                {
                    "CFBundleTypeRole": "Editor",
                    "CFBundleURLName": "delta-scheme",
                    "CFBundleURLSchemes": ["delta", "deltaclient"],
                }
            ],
        }
        scheme = self.roblox_builder.sanitize_and_configure_roblox_plist(plist)
        self.assertEqual("masterstrap-rbx-bridge-v1", scheme)
        self.assertEqual("com.roblox.robloxmobile", plist["CFBundleIdentifier"])
        self.assertEqual("Masterstrap", plist["CFBundleDisplayName"])
        self.assertEqual("Masterstrap", plist["CFBundleName"])
        self.assertIs(True, plist["UIFileSharingEnabled"])
        self.assertIs(True, plist["LSSupportsOpeningDocumentsInPlace"])
        self.assertIs(True, plist["CADisableMinimumFrameDurationOnPhone"])

        all_schemes = []
        for entry in plist["CFBundleURLTypes"]:
            for s in entry.get("CFBundleURLSchemes", []):
                all_schemes.append(s)

        self.assertNotIn("delta", all_schemes)
        self.assertNotIn("deltaclient", all_schemes)
        self.assertIn("roblox", all_schemes)
        self.assertIn("robloxmobile", all_schemes)
        self.assertIn("masterstrap-rbx-bridge-v1", all_schemes)

    def test_sanitize_and_configure_roblox_plist_handles_vng(self):
        plist = {
            "CFBundleIdentifier": "com.vng.roblox",
            "CFBundleName": "Roblox VNG",
        }
        scheme = self.roblox_builder.sanitize_and_configure_roblox_plist(plist)
        self.assertEqual("masterstrap-rbx-vng-bridge-v1", scheme)
        self.assertEqual("com.vng.roblox", plist["CFBundleIdentifier"])

        all_schemes = []
        for entry in plist["CFBundleURLTypes"]:
            for s in entry.get("CFBundleURLSchemes", []):
                all_schemes.append(s)

        self.assertIn("roblox-vng", all_schemes)
        self.assertIn("robloxmobile", all_schemes)
        self.assertIn("roblox", all_schemes)
        self.assertIn("masterstrap-rbx-vng-bridge-v1", all_schemes)

    def test_both_native_sides_use_server_bridge_v2_with_v1_fallback(self):
        app_source = self.app_builder.generate_native_app_objc_source()
        hook_source = self.roblox_builder.generate_native_hook_source(
            [], [], 0, [], {}
        )

        for scheme in (
            "masterstrap-rbx-bridge-v1",
            "masterstrap-rbx-vng-bridge-v1",
        ):
            self.assertIn(scheme, app_source)
            self.assertIn(scheme, hook_source)
        self.assertIn("serialize_bridge_payload", app_source)
        self.assertIn("request_bridge_v2_ticket", app_source)
        self.assertIn("https://bridge.masterstrap.org/v2/launch-configs", app_source)
        self.assertIn("v=2&request=%s&ticket=%s", app_source)
        self.assertIn("import_masterstrap_bridge_url", hook_source)
        self.assertIn("bridge_consume_v2", hook_source)
        self.assertIn(
            "https://bridge.masterstrap.org/v2/launch-configs/consume",
            hook_source,
        )
        self.assertIn("UIApplicationLaunchOptionsURLKey", hook_source)
        self.assertIn("application:openURL:options:", hook_source)
        self.assertIn("request=%s&payload=", app_source)
        self.assertIn("masterstrap_app.log", app_source)
        self.assertIn("masterstrap_bridge.log", hook_source)
        self.assertIn("masterstrap_ios.log", hook_source)
        self.assertIn("MasterstrapBridgeReceipt.json", hook_source)
        self.assertIn("MasterstrapRuntimeReceipt.json", hook_source)
        self.assertIn("receivedAtUnix", hook_source)
        for profile_key, file_name in (
            ("shaderProfile", "MasterstrapShaderProfile.json"),
            ("fontProfile", "MasterstrapFontProfile.json"),
        ):
            self.assertIn(profile_key, hook_source)
            self.assertIn(file_name, hook_source)

    def test_ticket_is_not_written_to_bridge_logs(self):
        app_source = self.app_builder.generate_native_app_objc_source()
        hook_source = self.roblox_builder.generate_native_hook_source([], [], 0, [], {})

        self.assertNotIn('ticket=%s", bridge_v2_ticket', app_source)
        self.assertNotIn("ticket=%s", hook_source)

    def test_cold_launch_consumes_before_original_delegate(self):
        hook_source = self.roblox_builder.generate_native_hook_source([], [], 0, [], {})
        callback = hook_source.split("static bool bridge_did_finish", 1)[1].split(
            "static bool bridge_open_url", 1
        )[0]
        self.assertLess(
            callback.index("import_masterstrap_bridge_url(url)"),
            callback.index("g_original_did_finish"),
        )

    def test_dynamic_parser_accepts_flexible_boolean_and_quoted_integer_values(self):
        hook_source = self.roblox_builder.generate_native_hook_source([], [], 0, [], {})

        self.assertIn("parse_dynamic_scalar_text", hook_source)
        for alias in (
            '"true"',
            '"yes"',
            '"on"',
            '"enabled"',
            '"false"',
            '"no"',
            '"off"',
            '"disabled"',
        ):
            self.assertIn(alias, hook_source)
        self.assertIn("dynamic_text_equals_ci", hook_source)
        self.assertIn("atoll(value)", hook_source)
        self.assertIn("[DYNAMIC_JSON_UNSUPPORTED]", hook_source)

    def test_runtime_log_reports_actual_backend_capabilities(self):
        hook_source = self.roblox_builder.generate_native_hook_source([], [], 0, [], {})

        self.assertIn('kFastFlagBackend = "direct-rva-write-v1"', hook_source)
        self.assertIn('kEngineSetFFlagBackend = "not-installed"', hook_source)
        self.assertIn('kEngineGetFastFlagBackend = "not-installed"', hook_source)
        self.assertIn('kFpsRuntimeBackend = "cadisplaylink-metal-present-policy-v5"', hook_source)
        self.assertIn('kFontRuntimeBackend = "native-asset-vfs-v1"', hook_source)
        self.assertIn('kShaderRuntimeBackend = "native-metal-lighting-v1"', hook_source)
        self.assertIn('kStretchRuntimeBackend = "metal-layer-drawable-v1"', hook_source)
        self.assertIn("FastFlag Apply Backend: %s", hook_source)
        self.assertIn("[CAPABILITY][FPS]", hook_source)
        self.assertIn("[CAPABILITY][FONT]", hook_source)
        self.assertIn("[CAPABILITY][SHADER]", hook_source)
        self.assertIn("[CAPABILITY][STRETCH]", hook_source)
        self.assertIn("[CAPABILITY][RENDERER]", hook_source)
        self.assertIn("[READBACK][FPS]", hook_source)
        self.assertIn("[LIFECYCLE]", hook_source)

    def test_runtime_abi_probe_is_read_only_and_fail_closed(self):
        hook_source = self.roblox_builder.generate_native_hook_source([], [], 0, [], {})

        self.assertIn("MasterstrapAbiProbe.json", hook_source)
        self.assertIn("macho_rva_is_executable", hook_source)
        self.assertIn("[ABI_PROBE][SUMMARY]", hook_source)
        self.assertIn("callGate=CLOSED", hook_source)
        self.assertIn('\\"callSafe\\":false', hook_source)
        self.assertIn("function-signatures-unverified", hook_source)
        self.assertNotIn("g_abi_probe_call_gate_open = 1", hook_source)

    def test_launch_configuration_is_authorized_once_then_destroyed(self):
        hook_source = self.roblox_builder.generate_native_hook_source([], [], 0, [], {})

        self.assertIn("MasterstrapLaunchAuthorization.json", hook_source)
        self.assertIn("MasterstrapConsumedReceipt.json", hook_source)
        self.assertIn("singleUse\\\":true", hook_source)
        self.assertIn("BLOCK_DIRECT_LAUNCH", hook_source)
        self.assertIn("direct_or_stale_launch_blocked", hook_source)
        self.assertIn("cold_launch_applied", hook_source)
        self.assertIn("warm_bridge_applied", hook_source)
        self.assertIn("apply_cached_dynamic_flags", hook_source)
        persistence = hook_source.split("// Persistence uses the in-memory cache only", 1)[1]
        self.assertNotIn("load_dynamic_config_files", persistence)
        self.assertIn("for (int loop = 0; launch_authorized && loop < 60; loop++)", persistence)

    def test_app_bridge_transports_all_runtime_profiles(self):
        app_source = self.app_builder.generate_native_app_objc_source()
        for profile_key, file_name in (
            ("shaderProfile", "MasterstrapShaderProfile.json"),
            ("fontProfile", "MasterstrapFontProfile.json"),
        ):
            self.assertIn(profile_key, app_source)
            self.assertIn(file_name, app_source)
        for profile in ("FASTFLAGS", "TURBO", "GLOBAL", "SHADER", "FONT"):
            self.assertIn(f"[PROFILE][{profile}] status=SAVED", app_source)

    def test_ios_ui_uses_metal_and_builds_separate_profiles(self):
        html = self.app_builder.configure_ios_runtime_html(
            self.app_builder.HTML_TEMPLATE_RAW
        )
        self.assertIn('value="metal_performance"', html)
        self.assertIn('value="metal_quality"', html)
        self.assertNotIn('<option value="opengl">', html)
        self.assertNotIn('<option value="vulkan">', html)
        self.assertIn('id="display-font-family"', html)
        self.assertIn('id="display-font-size-step"', html)
        self.assertIn("const shaderProfile = JSON.stringify", html)
        self.assertIn("const fontProfile = JSON.stringify", html)
        self.assertIn("shaderProfile: shaderProfile", html)
        self.assertIn("fontProfile: fontProfile", html)
        self.assertIn("function prepareFlagsForRoblox", html)
        self.assertIn('"FFlagDisablePostFx"', html)
        self.assertIn('delete clean[key]', html)
        self.assertIn("[PROFILE][REQUEST]", html)
        self.assertFalse(any(line != line.rstrip() for line in html.splitlines()))

    def test_levelplay_buttons_match_android_gate_policy(self):
        app_source = self.app_builder.generate_native_app_objc_source()
        html = self.app_builder.configure_ios_runtime_html(
            self.app_builder.HTML_TEMPLATE_RAW
        )
        bridge_source = (ROOT / "tools" / "ios_levelplay_bridge.m").read_text(
            encoding="utf-8"
        )

        self.assertIn("requestLevelPlayAd", app_source)
        self.assertIn("setLevelPlayBannerVisible", app_source)
        self.assertIn("MSLevelPlayConfigure", app_source)
        self.assertIn('request("rewarded", "roblox_launch", false)', html)
        self.assertIn('request("rewarded", "game_preset:" + presetId, false)', html)
        self.assertIn('request("rewarded", "desync", false)', html)
        self.assertIn('request("rewarded", "game_mode_master", false)', html)
        self.assertIn('request("interstitial", "fastflag_editor", true)', html)
        self.assertIn("interstitialCooldownMs = 5 * 60 * 1000", html)
        self.assertIn("needsFpsReward", html)
        self.assertIn("MasterstrapAds.banner(true)", html)
        self.assertIn("window.SettingsPersistence", html)
        self.assertIn("saveToggle", html)
        self.assertIn("restoreAllUI", html)
        self.assertIn("syncConfigToNative", html)
        self.assertIn("MSRewardEarned ? @\"rewarded\" : @\"failed\"", bridge_source)
        self.assertIn("interstitial_unavailable", bridge_source)
        self.assertIn("MSPrivacyChoiceSavedKey", bridge_source)
        self.assertIn("[LPMPrivacySettings setGDPRConsent:gdprConsent]", bridge_source)
        self.assertIn("[LPMPrivacySettings setCCPA:ccpaOptOut]", bridge_source)
        self.assertIn("LevelPlay init deferred: awaiting explicit ad privacy choice", bridge_source)
        self.assertIn("MSInitializationStarted", bridge_source)
        self.assertIn("MSTryShowPending", bridge_source)
        self.assertIn("MSPrepareAdObjects", bridge_source)
        self.assertIn("preloading rewarded ad unit", bridge_source)
        self.assertIn("interstitial_fallback_completed", bridge_source)
        self.assertIn("rewarded_and_interstitial_unavailable", bridge_source)
        self.assertIn("[adUnitId isEqualToString:MSRewardedAdUnitId]", bridge_source)
        self.assertIn("PrivacyInfo.xcprivacy", self.app_builder.build_masterstrap_ipa.__code__.co_consts)

    def test_standalone_app_packages_swift_runtime_and_unity_resources(self):
        compile_source = inspect.getsource(self.app_builder.compile_native_executable)
        package_source = inspect.getsource(self.app_builder.build_masterstrap_ipa)
        ios_html = self.app_builder.configure_ios_runtime_html(
            self.app_builder.HTML_TEMPLATE_RAW
        )
        runtime_embedder = (
            ROOT / "tools" / "embed_swift_runtime_in_ipa.sh"
        ).read_text(encoding="utf-8")

        self.assertIn("@executable_path/Frameworks", compile_source)
        self.assertIn("omsdk-v1.js", package_source)
        self.assertIn("omid-session-client-v1.js", package_source)
        self.assertIn("merge_privacy_manifests", package_source)
        self.assertIn("rawText.split(String.fromCharCode(10))", ios_html)
        self.assertNotIn('rawText.split("\n");', ios_html)
        self.assertIn("swift-stdlib-tool failed", runtime_embedder)
        self.assertIn("@rpath\\/libswift", runtime_embedder)
        self.assertIn("libswift_Concurrency.dylib", runtime_embedder)
        self.assertIn("libswiftCore.dylib", runtime_embedder)
        self.assertIn("swift-5.5/iphoneos", runtime_embedder)
        self.assertIn('--destination "$frameworks_dir"', runtime_embedder)

    def test_sdk_privacy_manifests_are_merged_without_losing_reasons(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = []
            for index, reason in enumerate(("CA92.1", "1C8F.1")):
                path = Path(temp_dir) / f"PrivacyInfo-{index}.xcprivacy"
                path.write_bytes(
                    plistlib.dumps(
                        {
                            "NSPrivacyTracking": index == 1,
                            "NSPrivacyAccessedAPITypes": [
                                {
                                    "NSPrivacyAccessedAPIType": (
                                        "NSPrivacyAccessedAPICategoryUserDefaults"
                                    ),
                                    "NSPrivacyAccessedAPITypeReasons": [reason],
                                }
                            ],
                        }
                    )
                )
                paths.append(path)

            merged = plistlib.loads(
                self.app_builder.merge_privacy_manifests(paths)
            )

        self.assertTrue(merged["NSPrivacyTracking"])
        self.assertEqual(
            ["CA92.1", "1C8F.1"],
            merged["NSPrivacyAccessedAPITypes"][0][
                "NSPrivacyAccessedAPITypeReasons"
            ],
        )


if __name__ == "__main__":
    unittest.main()
