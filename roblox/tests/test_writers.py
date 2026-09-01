from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from roblox_ios_dumper.scanner import FastFlagResult
from roblox_ios_dumper.writers import write_fflags_map_header


def _flag(name: str, prefix: str, suffix: str, rva: int) -> FastFlagResult:
    return FastFlagResult(
        name=name,
        prefix=prefix,
        suffix=suffix,
        address=0x100000000 + rva,
        rva=rva,
        segment="__DATA",
        writable=True,
        confidence="high",
        call_site=0x100001000,
        register_function=0x100002000,
        name_address=0x100003000,
        evidence=("storage-write:bool@0x100001000",),
    )


class WriterTests(unittest.TestCase):
    def test_fflags_map_preserves_full_typed_names_with_same_suffix(self) -> None:
        flags = [
            _flag("FFlagSharedSuffix", "FFlag", "SharedSuffix", 0x2000),
            _flag("FStringSharedSuffix", "FString", "SharedSuffix", 0x3000),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fflags_map.hpp"
            write_fflags_map_header(path, flags, "test", "TestIOS")
            text = path.read_text(encoding="utf-8")
        self.assertIn('"FFlagSharedSuffix", 0x2000, "bool"', text)
        self.assertIn('"FStringSharedSuffix", 0x3000, "pointer64"', text)
        self.assertNotIn('{"SharedSuffix",', text)


if __name__ == "__main__":
    unittest.main()
