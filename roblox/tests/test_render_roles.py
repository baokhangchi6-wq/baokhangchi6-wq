import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from ios_render_roles import build_render_roles_profile, RENDER_ANCHOR, patch_render_roles
from .fixtures import BASE, build_macho, encode_adrp, encode_add, encode_bl

class RenderRoleTests(unittest.TestCase):
    def source(self, wrong_projection=False, second_return=False):
        data=bytearray(build_macho(with_function_starts=True))
        struct.pack_into('<i',data,8,0)
        data[0x700:0xA00]=bytes(0x300)
        for offset in range(0x400,0x680,4): struct.pack_into('<I',data,offset,0xD503201F)
        for offset,name in ((0x700,'render2D'),(0x780,RENDER_ANCHOR)):
            encoded=name.encode()+b'\0';data[offset:offset+len(encoded)]=encoded
        ui=[encode_adrp(BASE+0x400,BASE+0x700,8),encode_add(8,8,0x700),0xAA0203F7,0xAA0303F8,
            0x1E230301,0x1E2302E0,0x910203E0,encode_bl(BASE+0x41c,BASE+0x500)]
        struct.pack_into('<8I',data,0x400,*ui)
        struct.pack_into('<5I',data,0x480,0xD10503FF,encode_adrp(BASE+0x484,BASE+0x780,8),
                         encode_add(8,8,0x780),0x910503FF,0xD65F03C0)
        if second_return: struct.pack_into('<I',data,0x498,0xD65F03C0)
        matrix=[0xD10483FF,0x1E204028,0x1E204009,0x1E201000,0x1E291800,
                0x1E301001,0x1E281825,0xD65F03C0]
        if wrong_projection: matrix[4]=0x1E281800
        struct.pack_into('<8I',data,0x500,*matrix)
        return data
    def profile(self,data):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with zipfile.ZipFile(root/'fixture.ipa','w') as archive:
                archive.writestr('Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib',data)
            return build_render_roles_profile(root/'fixture.ipa',root/'profile.json')
    def test_roles_and_negative_gates(self):
        source=self.source(); profile=self.profile(source)
        self.assertTrue(profile['verified'])
        self.assertEqual([h['role'] for h in profile['hooks']],['ui-width','render-enter','render-exit'])
        self.assertFalse(self.profile(self.source(wrong_projection=True))['verified'])
        self.assertFalse(self.profile(self.source(second_return=True))['verified'])
        source[0x404]^=1
        with self.assertRaisesRegex(ValueError,'source hash'): patch_render_roles(source,profile)

if __name__=='__main__': unittest.main()
