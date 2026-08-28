from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from roblox_ios_dumper.ipa import load_input
from roblox_ios_dumper.macho import MachOImage

from .fixtures import BASE, CSTRING_ADDRESS, DATA_ADDRESS, build_ipa, build_macho


class MachOTests(unittest.TestCase):
    def test_parses_arm64e_segments_and_uuid(self) -> None:
        image = MachOImage(build_macho())
        self.assertEqual(image.architecture, "arm64e")
        self.assertEqual(image.image_base, BASE)
        self.assertEqual(image.uuid, "12345678-1234-5678-1234-567812345678")
        self.assertFalse(image.is_encrypted)
        self.assertEqual(image.virtual_to_file(CSTRING_ADDRESS), 0x700)
        self.assertEqual(image.file_to_virtual(0x1040), DATA_ADDRESS + 0x40)
        self.assertTrue(image.segment_for_va(DATA_ADDRESS + 0x40).is_writable)

    def test_detects_encryption(self) -> None:
        self.assertTrue(MachOImage(build_macho(encrypted=True)).is_encrypted)

    def test_loads_ipa_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Roblox.ipa"
            build_ipa(path)
            loaded = load_input(path)
            self.assertEqual(loaded.bundle_identifier, "com.roblox.client")
            self.assertEqual(loaded.short_version, "2.735.1138")
            self.assertEqual(loaded.binary_path, "Payload/Roblox.app/Roblox")
            self.assertEqual(MachOImage(loaded.binary).architecture, "arm64e")


if __name__ == "__main__":
    unittest.main()
