import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from ios_render_idle_profile import ANCHOR, SLEEP, build_render_idle_profile
from .fixtures import BASE, build_macho, _segment, _section, encode_adrp, encode_add, encode_bl


class RenderIdleProfileTests(unittest.TestCase):
    def profile(self, *, wrong_width=False, ambiguous=False, wrong_map=False, broken_stub=False):
        data = bytearray(build_macho(with_function_starts=True))
        struct.pack_into('<i', data, 8, 0) # thin arm64
        cursor = 32
        commands = []
        for _ in range(struct.unpack_from('<I', data, 16)[0]):
            size = struct.unpack_from('<I', data, cursor + 4)[0]
            commands.append(bytes(data[cursor:cursor + size])); cursor += size
        commands[0] = _segment('__TEXT', BASE, 0x1000, 0, 0x1000, 5, [
            _section('__text', '__TEXT', BASE + 0x400, 0x180, 0x400, 0),
            _section('__stubs', '__TEXT', BASE + 0x600, 12, 0x600, 8),
            _section('__cstring', '__TEXT', BASE + 0x700, 0x300, 0x700, 2)])
        commands[1] = _segment('__DATA', BASE + 0x2000, 0x1000, 0x1000, 0x1000, 3, [
            _section('__data', '__DATA', BASE + 0x2000, 0x100, 0x1000, 0),
            _section('__la_symbol_ptr', '__DATA', BASE + 0x2180, 8, 0x1180, 7)])
        strings = b'\0' + SLEEP + b'\0'
        commands.append(struct.pack('<6I', 2, 24, 0x1800, 1, 0x1810, len(strings)))
        dynamic = [11, 80] + [0] * 18
        dynamic[14:16] = [0x1900, 1]
        commands.append(struct.pack('<20I', *dynamic))
        blob = b''.join(commands)
        data[32:32 + len(blob)] = blob
        struct.pack_into('<II', data, 16, len(commands), len(blob))
        struct.pack_into('<IBBHQ', data, 0x1800, 1, 1, 0, 0, 0)
        data[0x1810:0x1810 + len(strings)] = strings
        struct.pack_into('<I', data, 0x1900, 0)
        data[0x700:0xA00] = bytes(0x300)
        anchor = ANCHOR.encode() + b'\0'
        data[0x700:0x700 + len(anchor)] = anchor
        for offset in range(0x400, 0x680, 4): struct.pack_into('<I', data, offset, 0xD503201F)
        struct.pack_into('<II', data, 0x400, encode_adrp(BASE + 0x400, BASE + 0x700, 0), encode_add(0, 0, 0x700))
        def guard(offset):
            instructions = [encode_adrp(BASE + offset, BASE + 0x2000, 23),
                0x394002E8 if not wrong_width else 0xB94002E8, # LDRB W8,[X23]
                0xB94006E9, # LDR W9,[X23,#4]
                0x7100051F, 0x7A410928, # CMP W8,#1; CCMP W9,#1,#8,EQ
                0x5400004B, # B.LT skips following BL
                encode_bl(BASE + offset + 24, BASE + 0x600), 0xD503201F]
            struct.pack_into('<8I', data, offset, *instructions)
        guard(0x408)
        if ambiguous: guard(0x440)
        struct.pack_into('<III', data, 0x600, encode_adrp(BASE + 0x600, BASE + 0x2180, 16),
                         0xF940C210, 0xD65F03C0 if broken_stub else 0xD61F0200)
        with tempfile.TemporaryDirectory() as directory:
            ipa = Path(directory) / 'input.ipa'
            with zipfile.ZipFile(ipa, 'w') as archive:
                archive.writestr('Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib', data)
            return build_render_idle_profile(ipa, {'FFlagMainThreadIdle': 0x2008 if wrong_map else 0x2000,
                                                  'FIntMainThreadMinIdleTimeUs': 0x2004}, Path(directory) / 'profile.json')

    def test_unique_guard_and_fail_closed_variants(self):
        self.assertTrue(self.profile()['verified'])
        for variant in ('wrong_width', 'ambiguous', 'wrong_map', 'broken_stub'):
            with self.subTest(variant=variant): self.assertFalse(self.profile(**{variant: True})['verified'])


if __name__ == '__main__': unittest.main()
