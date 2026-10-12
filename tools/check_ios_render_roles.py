"""Execute patched ARM64 instructions through macOS dyld, including unbound slots."""
import ctypes
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from ios_render_roles import patch_render_roles, COOKIE

def main():
    if sys.platform != 'darwin': raise SystemExit('Render role executable check requires Apple arm64')
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'roblox'))
    from roblox_ios_dumper.macho import MachOImage
    from capstone import Cs,CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        callback_source = r'''
float clobber(float input) {
    __asm__ volatile("mov x0,#99\nmov x1,#88\nmov x16,#77\nmov x17,#66\n"
        "movi v1.16b,#1\nmovi v2.16b,#2\ncmp x0,x1\n"
        : : : "x0","x1","x16","x17","v1","v2","cc");
    return input*2;
}
void notify(void) { clobber(1); }
'''
        (root/'callbacks.c').write_text(callback_source,encoding='utf-8')
        subprocess.run(['xcrun','clang','-O0','-dynamiclib',str(root/'callbacks.c'),'-o',str(root/'callbacks.dylib')],check=True)
        callbacks=ctypes.CDLL(str(root/'callbacks.dylib'))
        (root/'fixture.c').write_text('float fixture_ui(float x, float height) { volatile float y=x; return y; }\nunsigned fixture_job(unsigned x) { volatile unsigned y=x+3; return y; }\n__attribute__((noinline)) void* fixture_original(void*x){return (char*)x+4;}\nvoid* fixture_acquire(void*x){return fixture_original(x);}\nvoid* fixture_texture(void*x){return fixture_original(x);}\n__asm__(".text\\n.p2align 2\\n.globl _fixture_null\\n_fixture_null:\\ncbz x0,1f\\nmov x0,#7\\nret\\n1:mov x0,#0\\nret\\n");\n',encoding='utf-8')
        subprocess.run(['xcrun','clang','-O0','-dynamiclib','-Wl,-headerpad,0x4000',str(root/'fixture.c'),'-o',str(root/'original.dylib')],check=True)
        source=(root/'original.dylib').read_bytes(); image=MachOImage(source,'arm64')
        if image.architecture != 'arm64': raise AssertionError('Fixture must run as arm64')
        decoder=Cs(CS_ARCH_ARM64,CS_MODE_LITTLE_ENDIAN)
        functions=[]
        for i,start in enumerate(image.function_starts):
            end=image.function_starts[i+1] if i+1<len(image.function_starts) else next(section.end_address for section in image.executable_sections if section.address<=start<section.end_address)
            offset=image.virtual_to_file(start)
            code=list(decoder.disasm(image.data[offset:offset+end-start],start))
            functions.append(code)
        ui=next(code for code in functions if any(ins.mnemonic=='str' and ins.op_str.startswith('s0,') for ins in code))
        job=next(code for code in functions if code is not ui and code[0].mnemonic=='sub' and any(ins.mnemonic=='add' and ins.op_str.endswith(', #3') for ins in code))
        instruction=ui[0]
        assert instruction.mnemonic=='sub' and instruction.op_str.startswith('sp, sp, #')
        result=next(ins for ins in job if ins.mnemonic=='ret')
        hooks=[{'role':'ui-width','rva':image.to_rva(instruction.address),'before':instruction.bytes.hex(),'slot':16},
               {'role':'render-enter','rva':image.to_rva(job[0].address),'before':job[0].bytes.hex(),'slot':24},
               {'role':'render-exit','rva':image.to_rva(result.address),'before':result.bytes.hex(),'slot':32}]
        call_sites=[ins for code in functions for ins in code if ins.mnemonic=='bl']
        assert len(call_sites)==2
        for role,slot,ins in zip(('fps-acquire','fps-texture'),(40,56),call_sites):
            hooks.append({'role':role,'rva':image.to_rva(ins.address),'before':ins.bytes.hex(),'slot':slot,'deviceRegister':0})
        check=next(ins for code in functions for ins in code if ins.mnemonic=='cbz' and ins.op_str.startswith('x0,'))
        decoder.detail=True
        parsed=next(decoder.disasm(bytes(check.bytes),check.address))
        hooks.append({'role':'fps-null','rva':image.to_rva(check.address),'before':check.bytes.hex(),'slot':48,'failureRva':image.to_rva(parsed.operands[1].imm)})
        output,state=patch_render_roles(bytearray(source),{'verified':True,'uuid':image.uuid,'hooks':hooks})
        target=root/'patched.dylib'; target.write_bytes(output)
        subprocess.run(['codesign','--force','--sign','-',str(target)],check=True)
        library=ctypes.CDLL(str(target)); library.fixture_ui.argtypes=[ctypes.c_float,ctypes.c_float];library.fixture_ui.restype=ctypes.c_float
        library.fixture_job.argtypes=[ctypes.c_uint];library.fixture_job.restype=ctypes.c_uint
        class Info(ctypes.Structure): _fields_=[('name',ctypes.c_char_p),('base',ctypes.c_void_p),('symbol',ctypes.c_char_p),('address',ctypes.c_void_p)]
        info=Info(); libc=ctypes.CDLL(None)
        libc.dladdr.argtypes=[ctypes.c_void_p,ctypes.POINTER(Info)]
        assert libc.dladdr(ctypes.cast(library.fixture_ui,ctypes.c_void_p),ctypes.byref(info))
        slots=(ctypes.c_void_p*8).from_address(info.base+state)
        assert slots[0]==COOKIE
        assert library.fixture_ui(1398,1290)==1398 and library.fixture_job(4)==7
        calls=[0,0]
        @ctypes.CFUNCTYPE(ctypes.c_float,ctypes.c_float,ctypes.c_float)
        def width(value,height):
            assert height==1290
            return value*2
        @ctypes.CFUNCTYPE(None)
        def enter(): calls[0]+=1
        @ctypes.CFUNCTYPE(None)
        def leave(): calls[1]+=1
        slots[2]=ctypes.cast(width,ctypes.c_void_p).value
        slots[3]=ctypes.cast(enter,ctypes.c_void_p).value
        slots[4]=ctypes.cast(leave,ctypes.c_void_p).value
        for i in range(100):
            assert library.fixture_ui(1398,1290)==2796
            assert library.fixture_job(i)==i+3 # preserve X0 input and return value across callbacks
        assert calls==[100,100]
        slots[2]=ctypes.cast(callbacks.clobber,ctypes.c_void_p).value
        slots[3]=slots[4]=ctypes.cast(callbacks.notify,ctypes.c_void_p).value
        assert library.fixture_ui(1398,1290)==2796 and library.fixture_job(40)==43
        slots[2]=None
        assert library.fixture_ui(1398,1290)==1398
        library.fixture_acquire.argtypes=library.fixture_texture.argtypes=library.fixture_null.argtypes=[ctypes.c_void_p]
        library.fixture_acquire.restype=library.fixture_texture.restype=ctypes.c_void_p
        library.fixture_null.restype=ctypes.c_size_t
        assert library.fixture_acquire(256)==260 and library.fixture_texture(256)==260
        assert library.fixture_null(None)==0 and library.fixture_null(256)==7
        @ctypes.CFUNCTYPE(ctypes.c_void_p,ctypes.c_void_p)
        def acquire(value):return None
        @ctypes.CFUNCTYPE(ctypes.c_size_t,ctypes.c_void_p)
        def allowed(value):return 1
        @ctypes.CFUNCTYPE(ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p)
        def texture(value,device):return 4096
        slots[5]=ctypes.cast(acquire,ctypes.c_void_p).value
        slots[6]=ctypes.cast(allowed,ctypes.c_void_p).value
        slots[7]=ctypes.cast(texture,ctypes.c_void_p).value
        assert library.fixture_acquire(256) is None and library.fixture_texture(None)==4096
        assert library.fixture_null(None)==7
        slots[6]=None
        assert library.fixture_null(None)==0
        print('PASS ARM64 role patch: dyld load, unbound passthrough, UI float result, entry/return register preservation')

if __name__=='__main__': main()
