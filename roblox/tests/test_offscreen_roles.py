import struct
import unittest
from . import test_render_roles as fixture_roles
from .fixtures import BASE,_segment,_section,_uleb128,encode_adrp,encode_add,encode_bl
from ios_render_roles import RENDER_ANCHOR

def ldr(rt,rn,offset):return 0xF9400000|(offset//8<<10)|(rn<<5)|rt
def str64(rt,rn,offset):return 0xF9000000|(offset//8<<10)|(rn<<5)|rt

class OffscreenRoleTests(unittest.TestCase):
    profile=fixture_roles.RenderRoleTests.profile
    def source(self,**options):
        data=fixture_roles.RenderRoleTests().source(**options)
        data.extend(bytes(0x3000-len(data)))
        cursor=32;commands=[]
        for _ in range(struct.unpack_from('<I',data,16)[0]):
            size=struct.unpack_from('<I',data,cursor+4)[0];commands.append(bytes(data[cursor:cursor+size]));cursor+=size
        commands[0]=_segment('__TEXT',BASE,0x2000,0,0x2000,5,[
            _section('__text','__TEXT',BASE+0x400,0x780,0x400,0),
            _section('__objc_stubs','__TEXT',BASE+0xc00,0x60,0xc00,0),
            _section('__cstring','__TEXT',BASE+0x1000,0x400,0x1000,2)])
        commands[1]=_segment('__DATA',BASE+0x3000,0x1000,0x2000,0x1000,3,[
            _section('__data','__DATA',BASE+0x3000,0x200,0x2000,0)])
        starts=[0x400,0x480,0x500,0x580,0x900,0xa00,0xb00,0xc00]
        encoded=b'';previous=0
        for value in starts:encoded+=_uleb128(value-previous);previous=value
        encoded+=b'\0';data[0x2f00:0x2f00+len(encoded)]=encoded
        commands[-1]=struct.pack('<IIII',0x26,16,0x2f00,len(encoded))
        blob=b''.join(commands);data[32:32+len(blob)]=blob;struct.pack_into('<I',data,20,len(blob))
        data[0x1000:0x1400]=bytes(0x400)
        for address,name in ((0x1000,'nextDrawable'),(0x1020,'texture'),(0x1040,'commit'),
                             (0x1080,'render2D'),(0x1100,RENDER_ANCHOR),(0x1200,'acquireFramebuffer'),(0x1240,'submitFrame')):
            value=name.encode()+b'\0';data[address:address+len(value)]=value
        struct.pack_into('<II',data,0x400,encode_adrp(BASE+0x400,BASE+0x1080,8),encode_add(8,8,0x80))
        struct.pack_into('<II',data,0x484,encode_adrp(BASE+0x484,BASE+0x1100,8),encode_add(8,8,0x100))
        for offset in range(0x580,0xb80,4):struct.pack_into('<I',data,offset,0xD503201F)
        for i,name in enumerate((0x1000,0x1020,0x1040)):
            struct.pack_into('<Q',data,0x2000+i*8,BASE+name)
            stub=0xc00+i*32
            struct.pack_into('<5I',data,stub,encode_adrp(BASE+stub,BASE+0x3000+i*8,1),
                             ldr(1,1,i*8),encode_adrp(BASE+stub+8,BASE+0x3080,16),ldr(16,16,0x80),0xD61F0200)
        start=0x580
        words=[0xAA0003F3,encode_adrp(BASE+start+4,BASE+0x1200,2),encode_add(2,2,0x200),
               encode_adrp(BASE+start+12,BASE+0x3080,8),encode_add(8,8,0x80),0x39400108,0x7100051F,
               0x54000021,ldr(22,31,0x108),ldr(0,22,0x18),encode_bl(BASE+start+40,BASE+0xc00),
               encode_bl(BASE+start+44,BASE+0xc80),str64(0,22,0x20),ldr(8,31,0x108),ldr(0,8,0x20),
               0xB4000180,ldr(20,8,8),encode_bl(BASE+start+68,BASE+0xc20),0xAA0003F3,0xAA1403E0,
               encode_bl(BASE+start+80,BASE+0xa80),ldr(8,0,0),ldr(0,8,0),0xAA1303E1,
               encode_bl(BASE+start+96,BASE+0xb00),0x52800020,0xD65F03C0,0x52800000,0xD65F03C0]
        struct.pack_into('<'+'I'*len(words),data,start,*words)
        submit=[0xAA0003F3,encode_adrp(BASE+0x904,BASE+0x1240,8),encode_add(8,8,0x240),
                ldr(8,19,0x1a8),0xB9407517,ldr(20,8,0x28),0xAA1403E1,encode_bl(BASE+0x91c,BASE+0xa00),0xD65F03C0]
        struct.pack_into('<9I',data,0x900,*submit)
        struct.pack_into('<4I',data,0xa00,0xAA0103F5,0xAA1503E0,encode_bl(BASE+0xa08,BASE+0xc40),0xD65F03C0)
        struct.pack_into('<2I',data,0xb00,str64(1,0,0x68),0xD65F03C0)
        return data
    def test_offscreen_bind_and_commit_semantics(self):
        source=self.source();p=self.profile(source)
        self.assertTrue(p['fpsVerified'])
        self.assertEqual(p['hooks'][-1]['contextOffset'],0x1a8)
        self.assertEqual(p['hooks'][-1]['commandBufferOffset'],0x28)
        for offset,value in ((0xb00,str64(2,0,0x68)),(0xa08,0xD503201F),(0x5b0,str64(0,22,0x28)),(0x59c,0x14000001)):
            variant=bytearray(source);struct.pack_into('<I',variant,offset,value)
            with self.subTest(offset=offset):self.assertFalse(self.profile(variant)['fpsVerified'])
