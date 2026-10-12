import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from ios_render_roles import build_render_roles_profile, RENDER_ANCHOR, patch_render_roles, resolve_scene_aspect
from unittest.mock import patch
from types import SimpleNamespace
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
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
        self.assertEqual([h['role'] for h in profile['hooks']],['render-enter','render-exit'])
        self.assertFalse(profile['stretchVerified'])
        self.assertTrue(self.profile(self.source(wrong_projection=True))['verified']) # GUI matrix no longer a hook dependency
        self.assertFalse(self.profile(self.source(second_return=True))['verified'])
        source[0x404]^=1
        with self.assertRaisesRegex(ValueError,'source hash'): patch_render_roles(source,profile)

    def scene(self, *, shift=0, fields=0x140, camera=0x160, wrong_height=False, duplicate=False, clobber=False):
        data=bytearray(0x700); base=BASE+shift
        def words(offset, values): struct.pack_into('<'+'I'*len(values),data,offset,*values)
        words(0x80,[encode_adrp(base+0x80,base+0x1000,16),0xF9400210,0xD61F0200])
        words(0x100,[0xD10483FF,0x1E20402B,0x1E2C1001,0x1E210800,
                     encode_bl(base+0x110,base+0x80),0x1E2E1001,0x1E201828,0x1E2B1900,0xD65F03C0])
        for offset in (0x200,0x300) if duplicate else (0x200,):
            code=[0xAA0003F3,0xBD400260|((fields//4)<<10),0x1E2E1001,0x1E216800,
                  0xBD400262|(((fields+(8 if wrong_height else 4))//4)<<10),0x1E216841,0x1E211808,
                  encode_add(0,19,camera),0x1E204020 if clobber else 0x1E204101,
                  encode_bl(base+offset+36,base+0x100),encode_add(0,19,0x4a0),encode_add(1,19,camera),
                  0x528066A2,encode_bl(base+offset+52,base+0x500),0xD65F03C0]
            words(offset,code)
        image=SimpleNamespace(data=bytes(data),image_base=base,function_starts=[base+x for x in (0x100,0x200,0x300,0x400,0x500,0x600)],
            sections=[SimpleNamespace(name='__stubs',address=base+0x80,file_offset=0x80,size=12),
                      SimpleNamespace(name='__text',address=base+0x100,file_offset=0x100,size=0x500)],
            virtual_to_file=lambda address:address-base,to_rva=lambda address:address-base)
        decoder=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN);decoder.detail=True
        with patch('ios_font_profile.resolve_import_slots',return_value=[{'rva':0x1000}]):
            return resolve_scene_aspect(image,decoder)

    def test_scene_scope_relocation_and_rejection(self):
        for variant in ({},{'shift':0x8000,'fields':0x240,'camera':0x260}):
            hooks=self.scene(**variant)
            self.assertEqual(len(hooks),1)
            self.assertEqual(hooks[0]['rva'],0x220)
            self.assertEqual(hooks[0]['widthOffset'],variant.get('fields',0x140))
        for variant in ('wrong_height','duplicate','clobber'):
            with self.subTest(variant=variant): self.assertEqual(self.scene(**{variant:True}),[])

if __name__=='__main__': unittest.main()
