from __future__ import annotations

import unittest

from roblox_ios_dumper.macho import MachOImage
from roblox_ios_dumper.scanner import Arm64Scanner
from roblox_ios_dumper.arm64 import store_kind

from .fixtures import BASE, TEXT_ADDRESS, build_macho


class ScannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.image = MachOImage(build_macho())
        self.scanner = Arm64Scanner(self.image)

    def test_resolves_string_xref_offset(self) -> None:
        results = self.scanner.scan_offsets(
            [
                {
                    "name": "print",
                    "string": "Current identity is %d",
                    "xref_index": 0,
                    "max_function_scan": 4096,
                }
            ]
        )
        self.assertEqual(results[0].address, TEXT_ADDRESS + 0x100)
        self.assertEqual(results[0].rva, 0x500)
        self.assertEqual(results[0].confidence, "medium")

    def test_decodes_registration_call_and_writable_storage(self) -> None:
        flags, summary = self.scanner.scan_fast_flags()
        self.assertEqual(summary["registration_calls"], 2)
        flag = next(item for item in flags if item.name == "FFlagDebugDisplayFPS")
        self.assertEqual(flag.rva, 0x2040)
        self.assertTrue(flag.writable)
        self.assertEqual(flag.segment, "__DATA")
        self.assertEqual(flag.confidence, "high")

    def test_recognizes_compact_boolean_state_store(self) -> None:
        self.assertEqual(store_kind(0x79000109), "bool16")

    def test_prefers_lc_function_starts_over_prologue_guessing(self) -> None:
        scanner = Arm64Scanner(MachOImage(build_macho(with_function_starts=True)))
        address, source = scanner.resolve_function_start(TEXT_ADDRESS + 0x118)
        self.assertEqual(address, TEXT_ADDRESS + 0x100)
        self.assertEqual(source, "lc-function-starts")

    def test_relaxed_hex_scan_reports_candidate_without_promoting_it(self) -> None:
        start = TEXT_ADDRESS + 0x100
        raw = self.image.read_virtual(start, 24)
        self.assertIsNotNone(raw)
        tokens = [f"{byte:02X}" for byte in raw]
        tokens[0] = "AA" if tokens[0] != "AA" else "BB"
        tokens[4] = "??"
        candidates = self.scanner.scan_relaxed_hex_candidates(
            "changed_pattern", " ".join(tokens)
        )
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["address"], start)
        self.assertFalse(candidates[0]["promoted"])
        self.assertGreater(candidates[0]["score"], 0.90)


if __name__ == "__main__":
    unittest.main()
