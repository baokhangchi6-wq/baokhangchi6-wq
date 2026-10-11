import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from ios_stretch_profile import ANCHORS, build_stretch_profile
from .fixtures import BASE, build_macho, _segment, _section, encode_adrp, encode_add, encode_bl

class StretchProfileTests(unittest.TestCase):
    def profile(self, *, wrong_axis=False, missing_resize=False, different_allocator=False, clobbered_width=False):
        data = bytearray(build_macho(with_function_starts=True))
        struct.pack_into('<i', data, 8, 0)
        cursor = 32; commands = []
        for _ in range(struct.unpack_from('<I', data, 16)[0]):
            size = struct.unpack_from('<I', data, cursor + 4)[0]
            commands.append(bytes(data[cursor:cursor + size])); cursor += size
        commands[0] = _segment('__TEXT', BASE, 0x1000, 0, 0x1000, 5, [
            _section('__text', '__TEXT', BASE + 0x400, 0x180, 0x400, 0),
            _section('__objc_stubs', '__TEXT', BASE + 0x600, 96, 0x600, 0),
            _section('__cstring', '__TEXT', BASE + 0x700, 0x300, 0x700, 2)])
        blob = b''.join(commands); data[32:32 + len(blob)] = blob
        struct.pack_into('<I', data, 20, len(blob))
        data[0x400:0xA00] = bytes(0x600)
        for o in range(0x400, 0x580, 4): struct.pack_into('<I', data, o, 0xD503201F)
        for index, selector in enumerate(('frame', 'contentScaleFactor', 'setDrawableSize:')):
            stub = 0x600 + index * 32; name = 0x900 + index * 32; slot = 0x2080 + index * 8
            encoded = selector.encode() + b'\0'; data[name:name+len(encoded)] = encoded
            struct.pack_into('<Q', data, 0x1080 + index * 8, BASE + name)
            struct.pack_into('<5I', data, stub, encode_adrp(BASE + stub, BASE + slot, 1),
                             0xF9400021 | ((slot & 0xFFF) // 8 << 10),
                             encode_adrp(BASE + stub + 8, BASE + 0x2180, 16), 0xF940C210, 0xD61F0200)
        for index, anchor in enumerate(ANCHORS):
            if missing_resize and index: continue
            start = 0x400 + index * 0x80; string = 0x700 + index * 0x80
            encoded = anchor.encode() + b'\0'; data[string:string+len(encoded)] = encoded
            words = [encode_adrp(BASE+start,BASE+string,2), encode_add(2,2,string), 0xAA1503E0,
                     encode_bl(BASE+start+12,BASE+0x600), 0x1E604068 if wrong_axis else 0x1E604048,
                     0xAA1503E0, encode_bl(BASE+start+24,BASE+0x620), 0x1E680809,
                     0xAA1503E0, encode_bl(BASE+start+36,BASE+0x600), 0x1E604068,
                     0xAA1503E0, encode_bl(BASE+start+48,BASE+0x620), 0x1E680800,
                     0x1E790135, 0x1E790016, # FCVTZU W21,D9; FCVTZU W22,D0
                     0xAA1403E0, 0xAA1503E1, 0xAA1603E2,
                     encode_bl(BASE+start+76,BASE+(0x580 if different_allocator and index else 0x500)),
                     0x1E6302A0, 0x1E6302C1, encode_bl(BASE+start+88,BASE+0x640)]
            struct.pack_into('<'+'I'*len(words), data, start, *words)
            if clobbered_width: struct.pack_into('<I', data, start + 64, 0x52800015)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root/'source.ipa', 'w') as archive:
                archive.writestr('Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib', data)
            return build_stretch_profile(root/'source.ipa', root/'profile.json')

    def test_size_flow_and_rejection(self):
        self.assertTrue(self.profile()['verified'])
        for variant in ('wrong_axis', 'missing_resize', 'different_allocator', 'clobbered_width'):
            with self.subTest(variant=variant): self.assertFalse(self.profile(**{variant: True})['verified'])

if __name__ == '__main__': unittest.main()
