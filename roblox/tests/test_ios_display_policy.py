import importlib.util
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DisplayPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "ios_builder_policy_test", ROOT / "tools/build_integrated_roblox_ipa.py"
        )
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)
        cls.source = cls.builder.generate_native_hook_source([], [], 0, [], {})

    def test_both_display_apis_and_existing_links_are_handled(self):
        for marker in ("setPreferredFramesPerSecond:", "setPreferredFrameRateRange:",
                       "addToRunLoop:forMode:", "weakObjectsHashTable", "profile-loaded"):
            self.assertIn(marker, self.source)
        self.assertIn('sizeof(MSFrameRateRange) == 12', self.source)

    def test_partial_install_cannot_capture_itself(self):
        for pointer in ("set_fps", "set_range", "create", "add"):
            self.assertIn(f"&& !g_original_display_link_{pointer}", self.source)

    def test_process_policy_uses_main_queue_snapshot(self):
        self.assertIn("dispatch_async_f(&_dispatch_main_q, snapshot, ms_apply_process_main)", self.source)
        self.assertIn("g_ms_policy = *snapshot;", self.source)
        self.assertIn("free(snapshot);", self.source)
        self.assertIn("g_ms_policy.loaded &&", self.source)

    def test_power_notifications_and_restoration_are_present(self):
        for marker in ("NSProcessInfoPowerStateDidChangeNotification",
                       "NSProcessInfoThermalStateDidChangeNotification",
                       "UIApplicationDidBecomeActiveNotification", "g_ms_idle_original",
                       "RESTORED_NATIVE_REQUEST", "ms_capture_link_request(self)"):
            self.assertIn(marker, self.source)

    def test_logs_do_not_claim_presented_fps_or_complete_boost(self):
        self.assertIn("presentedFps=UNMEASURED", self.source)
        self.assertIn("WAITING_FOR_LINK", self.source)
        self.assertIn("cpuOverclock=UNSUPPORTED", self.source)
        self.assertIn("ramCleanup=UNSUPPORTED", self.source)
        self.assertIn("g_display_link_rewrite_count < 16", self.source)

    def test_missing_profile_cannot_reuse_previous_request(self):
        loader = self.source.split("static void load_and_apply_runtime_profiles", 1)[1].split(
            "static void bridge_log", 1
        )[0]
        self.assertLess(loader.index("g_target_fps = 10000"), loader.index("read_profile_file"))
        self.assertIn("g_font_size_step = 0", loader)
        self.assertIn('"none"', loader)

    def test_runtime_identity_is_pinned_to_exact_engine_uuid(self):
        source = self.builder.generate_native_hook_source(
            [], [], 0, [], {}, expected_uuid="293724c9-edb3-312d-a863-2e5a7275f85d"
        )
        self.assertIn("kEngineIdentityPinned = true", source)
        self.assertIn("41,55,36,201,237,179,49,45,168,99,46,90,114,117,248,93", source)
        worker = source.split("static void* worker_thread", 1)[1]
        self.assertLess(worker.index("engine_identity_matches(base)"), worker.index("complete_runtime_boot(base"))
        self.assertIn("kEngineIdentityPinned = false", self.source)

    def test_ipa_profile_failure_is_not_replaced_with_remote_map(self):
        import sys
        sys.path.insert(0, str(ROOT / "roblox"))
        from roblox_ios_dumper.verification import VerificationResult
        failed = VerificationResult(errors=["sha256-mismatch"])
        with mock.patch("roblox_ios_dumper.verification.verify_dump", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "sha256-mismatch"):
                self.builder.verify_ipa_profile(Path("wrong.ipa"), Path("profile/FFlags.hpp"))

    def test_dumper_layout_does_not_silently_return_none(self):
        with mock.patch.object(self.builder.subprocess, "run", side_effect=RuntimeError("dump-failed")):
            with self.assertRaisesRegex(RuntimeError, "dump-failed"):
                self.builder.run_local_dumper(Path("test.ipa"), Path("out"))

    def test_storage_width_is_not_derived_from_json_value(self):
        self.assertIn("entry.kind = flag->kind;", self.source)
        self.assertNotIn("entry.kind = (flag->kind == 0) ? 0 : val_kind", self.source)

    def test_profile_booleans_reuse_flexible_scalar_parser(self):
        body = self.source.split("static bool profile_read_bool", 1)[1].split("static int profile_read_int", 1)[0]
        self.assertIn("parse_dynamic_scalar_text(token", body)
        self.assertIn("number != 0 && number != 1", body)

    def test_fps_patch_rejects_ambiguous_and_non_code_matches(self):
        import os
        import tempfile
        import json
        from tests.fixtures import build_macho
        signature = bytes.fromhex("1fc10371091e805209b1891a")
        with mock.patch.dict(os.environ, {"MASTERSTRAP_BUILD_TYPE": "Debug"}), tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "fps.json"
            data = bytearray(build_macho(with_function_starts=True))
            data[0x420:0x42c] = signature
            data[0x1000:0x100c] = signature  # writable data must not count as executable evidence
            result = self.builder.patch_robloxlib_fps_clamps(data, report)
            self.assertEqual(result[0x428:0x42c], bytes.fromhex("e903082a"))
            self.assertEqual(result[0x1000:0x100c], signature)
            self.assertEqual(json.loads(report.read_text())["patches"][0]["matches"], 1)
            data = bytearray(build_macho(with_function_starts=True))
            data[0x420:0x42c] = data[0x4a0:0x4ac] = signature
            original = bytes(data)
            self.builder.patch_robloxlib_fps_clamps(data, report)
            self.assertEqual(bytes(data), original)
            self.assertEqual(json.loads(report.read_text())["patches"][0]["matches"], 2)
            with mock.patch.dict(os.environ, {"MASTERSTRAP_BUILD_TYPE": "Release"}):
                with self.assertRaisesRegex(RuntimeError, "binary gate"):
                    self.builder.patch_robloxlib_fps_clamps(data, report)
            data = bytearray(build_macho(with_function_starts=True))
            data[0x47c:0x488] = signature  # spans two LC_FUNCTION_STARTS entries
            original = bytes(data)
            self.builder.patch_robloxlib_fps_clamps(data, report)
            self.assertEqual(bytes(data), original)

    def test_fps_patch_is_idempotent_for_branch_signature(self):
        import os
        from tests.fixtures import build_macho
        data = bytearray(build_macho(with_function_starts=True))
        data[0x420:0x42c] = bytes.fromhex("627a40f90820601ecd2b0054")
        with mock.patch.dict(os.environ, {"MASTERSTRAP_BUILD_TYPE": "Debug"}):
            self.builder.patch_robloxlib_fps_clamps(data)
            first = bytes(data)
            self.builder.patch_robloxlib_fps_clamps(data)
            self.assertEqual(bytes(data), first)


if __name__ == "__main__":
    unittest.main()
