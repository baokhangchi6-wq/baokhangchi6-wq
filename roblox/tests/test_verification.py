from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from roblox_ios_dumper.cli import DEFAULT_PATTERNS, main
from roblox_ios_dumper.verification import verify_dump

from .fixtures import build_ipa


class VerificationTests(unittest.TestCase):
    def test_dump_passes_identity_and_segment_verification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ipa = root / "Roblox.ipa"
            output = root / "output"
            build_ipa(ipa)
            exit_code = main(
                [
                    "dump",
                    str(ipa),
                    "--known-fflags",
                    str(root / "missing.hpp"),
                    "--known-json-root",
                    str(root / "missing-json"),
                    "--output",
                    str(output),
                ]
            )
            self.assertEqual(exit_code, 0)
            result = verify_dump(
                output,
                input_path=ipa,
                patterns_path=DEFAULT_PATTERNS,
                minimum_flag_coverage=0.50,
            )
            self.assertTrue(result.ok, result.errors)
            self.assertTrue(result.checks["input_reloaded"])
            self.assertEqual(result.checks["fflags_decoded"], 1)

    def test_detects_manifest_identity_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ipa = root / "Roblox.ipa"
            output = root / "output"
            build_ipa(ipa)
            self.assertEqual(
                main(
                    [
                        "dump",
                        str(ipa),
                        "--known-fflags",
                        str(root / "missing.hpp"),
                        "--known-json-root",
                        str(root / "missing-json"),
                        "--output",
                        str(output),
                    ]
                ),
                0,
            )
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["input"]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            result = verify_dump(
                output,
                input_path=ipa,
                patterns_path=DEFAULT_PATTERNS,
                minimum_flag_coverage=0.50,
            )
            self.assertFalse(result.ok)
            self.assertIn("identity:sha256-mismatch", result.errors)


if __name__ == "__main__":
    unittest.main()
