"""Resolve UI projection and RenderJob roles; emit guarded calls in unused TEXT padding."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

COOKIE = 0x4D53524F4C455331
RENDER_ANCHOR = '[FLog::SurfaceController] SurfaceController::RenderJob::scheduleRender: No data model. Bind workspace now...'

def resolve_roles(image):
    from roblox_ios_dumper.scanner import Arm64Scanner
    from roblox_ios_dumper.arm64 import decode_bl
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    scanner = Arm64Scanner(image)
    decoder = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN); decoder.detail = True
    scanner._index_targets({a for value in ('render2D', RENDER_ANCHOR) for a in image.find_cstrings(value)})
    def body(anchor):
        starts = {scanner.resolve_function_start(r.instruction_address)[0] for r in scanner.find_string_xrefs(anchor)}
        if len(starts) != 1 or None in starts: return []
        start = next(iter(starts)); i = image.function_starts.index(start)
        if i + 1 == len(image.function_starts): return []
        end = image.function_starts[i + 1]; offset = image.virtual_to_file(start)
        return list(decoder.disasm(image.data[offset:offset + end - start], start))
    ui = body('render2D'); render = body(RENDER_ANCHOR)
    hooks = []
    for i, ins in enumerate(ui):
        if ins.mnemonic != 'bl' or i < 3: continue
        first, second, pointer = ui[i-3:i]
        if first.mnemonic != 'ucvtf' or not first.op_str.startswith('s1, w') or second.mnemonic != 'ucvtf' or not second.op_str.startswith('s0, w') or pointer.mnemonic != 'add' or not pointer.op_str.startswith('x0, sp,'): continue
        width, height = second.op_str.split(', ')[1], first.op_str.split(', ')[1]
        if not any(item.mnemonic == 'mov' and item.op_str == 'x' + width[1:] + ', x2' for item in ui[:i]): continue
        if not any(item.mnemonic == 'mov' and item.op_str == 'x' + height[1:] + ', x3' for item in ui[:i]): continue
        target = decode_bl(int.from_bytes(ins.bytes,'little'), ins.address)
        if target not in image.function_starts: continue
        j = image.function_starts.index(target)
        if j + 1 == len(image.function_starts): continue
        offset = image.virtual_to_file(target); end = image.function_starts[j+1]
        matrix = list(decoder.disasm(image.data[offset:offset + end - target], target))
        w = [item.op_str.split(', ')[0] for item in matrix if item.mnemonic == 'fmov' and item.op_str.endswith(', s0')]
        h = [item.op_str.split(', ')[0] for item in matrix if item.mnemonic == 'fmov' and item.op_str.endswith(', s1')]
        if len(w) != 1 or len(h) != 1: continue
        division = [item.op_str for item in matrix if item.mnemonic == 'fdiv']
        constants = [item.op_str for item in matrix if item.mnemonic == 'fmov']
        if 's0, s0, ' + w[0] not in division or 's5, s1, ' + h[0] not in division: continue
        if 's0, #2.00000000' not in constants or 's1, #-2.00000000' not in constants: continue
        hooks.append({'role':'ui-width','rva':image.to_rva(second.address),'before':second.bytes.hex(),
                      'functionRva':image.to_rva(ui[0].address),'matrixRva':image.to_rva(target),'slot':16})
    if len(hooks) != 1: return []
    if not render or render[0].mnemonic != 'sub' or not render[0].op_str.startswith('sp, sp, #'): return []
    returns = [item for item in render if item.mnemonic == 'ret']
    if len(returns) != 1 or returns[0].op_str: return []
    hooks.extend([
        {'role':'render-enter','rva':image.to_rva(render[0].address),'before':render[0].bytes.hex(),'slot':24},
        {'role':'render-exit','rva':image.to_rva(returns[0].address),'before':returns[0].bytes.hex(),'slot':32}])
    return hooks

def build_render_roles_profile(ipa, report):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'roblox'))
    from roblox_ios_dumper.macho import MachOImage
    with zipfile.ZipFile(ipa) as archive:
        image = MachOImage(archive.read('Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib'),'arm64')
    result = {'schema':1,'uuid':image.uuid,'sourceSha256':hashlib.sha256(image.data).hexdigest(),
              'verified':False,'deviceVerified':False,'hooks':[],'status':'UNRESOLVED_ROLES'}
    if image.architecture == 'arm64' and not image.is_encrypted and not image.slice.file_offset:
        result['hooks'] = resolve_roles(image)
        result['verified'] = len(result['hooks']) == 3
        if result['verified']: result['status'] = 'VERIFIED_UI_ORTHOGRAPHIC_WIDTH_AND_RENDER_JOB'
    report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

def encode_branch(source, target):
    distance = target - source
    if distance % 4 or not -(1<<27) <= distance < (1<<27): raise ValueError('Role branch exceeds ARM64 range')
    return 0x14000000 | ((distance >> 2) & 0x3FFFFFF)

def trampoline(start, state, hook):
    words = []
    emit = words.append
    original = int.from_bytes(bytes.fromhex(hook['before']),'little')
    if hook['role'] != 'render-exit': emit(original)
    emit(0xD10B83FF) # SUB SP,SP,#736; keep the original instruction's SP change separate
    for reg in range(0,20,2):
        second = 30 if reg == 18 else reg + 1
        emit(0xA9000000 | ((reg*8//8)<<15) | (second<<10) | (31<<5) | reg)
    for reg in range(0,32,2):
        emit(0xAD000000 | (((160+reg*16)//16)<<15) | ((reg+1)<<10) | (31<<5) | reg)
    emit(0xD53B4211) # MRS X17,NZCV
    emit(0xF90153F1) # STR X17,[SP,#672]
    emit(0xD53B4411); emit(0xF90163F1) # FPCR at 704
    emit(0xD53B4431); emit(0xF90167F1) # FPSR at 712
    pc = start + len(words)*4
    delta = ((state & ~0xFFF) - (pc & ~0xFFF)) >> 12
    if not -(1<<20) <= delta < (1<<20): raise ValueError('Role state exceeds ADRP range')
    encoded = delta & 0x1FFFFF
    emit(0x90000010 | ((encoded & 3)<<29) | ((encoded>>2)<<5))
    emit(0xF9400210 | (((state & 0xFFF) + hook['slot'])//8<<10))
    emit(0xF9015BF0) # STR X16,[SP,#688] callback readiness, survives its caller-saved registers
    test = len(words); emit(0) # CBZ X16 skips the callback while slots are unbound
    emit(0xD63F0200) # BLR X16
    if hook['role'] == 'ui-width': emit(0xBD02ABE0) # STR S0,[SP,#680]
    restore = len(words)
    words[test] = 0xB4000010 | ((restore-test)<<5)
    emit(0xF94153F1); emit(0xD51B4211) # restore NZCV
    emit(0xF94163F1); emit(0xD51B4411)
    emit(0xF94167F1); emit(0xD51B4431)
    for reg in range(0,32,2):
        emit(0xAD400000 | (((160+reg*16)//16)<<15) | ((reg+1)<<10) | (31<<5) | reg)
    if hook['role'] == 'ui-width':
        # If no callback ran, original S0 is already in the restored Q0.
        emit(0xF9415BF0) # LDR X16,[SP,#688]
        emit(0xB4000050) # CBZ X16 skips reloading S0 when callback is unbound
        emit(0xBD42ABE0) # LDR S0,[SP,#680]
    for reg in range(0,20,2):
        second = 30 if reg == 18 else reg + 1
        emit(0xA9400000 | ((reg*8//8)<<15) | (second<<10) | (31<<5) | reg)
    emit(0x910B83FF)
    if hook['role'] == 'render-exit': emit(original)
    else: emit(encode_branch(start + len(words)*4, hook['absoluteRva'] + 4))
    return struct.pack('<'+'I'*len(words),*words)

def patch_render_roles(data, profile):
    from roblox_ios_dumper.macho import MachOImage
    image = MachOImage(bytes(data),'arm64')
    if not profile.get('verified'): return data, 0
    if image.uuid != profile['uuid']: raise ValueError('Role patch UUID mismatch')
    if profile.get('sourceSha256') and hashlib.sha256(image.data).hexdigest()!=profile['sourceSha256']: raise ValueError('Role source hash mismatch')
    header_end = 32 + struct.unpack_from('<I',data,20)[0]
    text = next(s for s in image.sections if s.name == '__text' and s.size)
    cave = (header_end + 1024 + 15) & ~15
    state = (max(s.end_address for s in image.segments) + 0x3FFF) & ~0x3FFF
    offset = (len(data) + 0x3FFF) & ~0x3FFF
    command = struct.pack('<II16sQQQQiiII',0x19,72,b'__MSDATA'.ljust(16,b'\0'),state,0x4000,offset,0x4000,3,3,0,0)
    if any(data[header_end:header_end+len(command)]): raise ValueError('No padding for role segment command')
    planned=[]
    for hook in profile['hooks']:
        address = image.image_base + hook['rva']; site = image.virtual_to_file(address)
        segment=image.segment_for_va(address)
        if site is None or not segment or not segment.is_executable or bytes(data[site:site+4]).hex()!=hook['before']: raise ValueError('Role instruction changed')
        actual={**hook,'absoluteRva':address}
        blob=trampoline(image.image_base+cave,state,actual)
        if cave+len(blob)>text.file_offset or any(data[cave:cave+len(blob)]): raise ValueError('Role TEXT padding unavailable')
        planned.append((site,cave,blob,encode_branch(address,image.image_base+cave)))
        cave=(cave+len(blob)+15)&~15
    output=bytearray(data)
    for site,cave,blob,branch in planned:
        output[cave:cave+len(blob)]=blob; struct.pack_into('<I',output,site,branch)
    output[header_end:header_end+72]=command
    count=struct.unpack_from('<I',output,16)[0]
    struct.pack_into('<II',output,16,count+1,header_end-32+72)
    output.extend(bytes(offset+0x4000-len(output)))
    struct.pack_into('<Q',output,offset,COOKIE)
    profile['outputSha256']=hashlib.sha256(output).hexdigest()
    profile['stateRva']=image.to_rva(state)
    return output, image.to_rva(state)
