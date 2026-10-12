"""Resolve UI projection and RenderJob roles; emit guarded calls in unused TEXT padding."""
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

COOKIE = 0x4D53524F4C455331
RENDER_ANCHOR = '[FLog::SurfaceController] SurfaceController::RenderJob::scheduleRender: No data model. Bind workspace now...'

def resolve_offscreen_roles(image, scanner, decoder):
    from roblox_ios_dumper.arm64 import decode_address_reference, decode_bl
    stubs={}
    for section in image.sections:
        if section.name!='__objc_stubs': continue
        for offset in range(section.file_offset,section.file_offset+section.size-19,4):
            address=section.address+offset-section.file_offset
            a,b,c,d,e=struct.unpack_from('<5I',image.data,offset)
            ref=decode_address_reference(address,a,b)
            if not ref or a&31!=1 or b&0xFFC003FF!=0xF9400021 or e!=0xD61F0200: continue
            pointer=image.read_u64(ref.target_address)
            if pointer is None: continue
            file=image.virtual_to_file(pointer)
            if file is None: continue
            end=image.data.find(b'\0',file,file+40)
            if end<0:continue
            name=image.data[file:end]
            if name in (b'nextDrawable',b'texture',b'commit'): stubs.setdefault(name,[]).append(address)
    submit={scanner.resolve_function_start(r.instruction_address)[0] for r in scanner.find_string_xrefs('submitFrame')}
    buffer_paths=[]
    if len(submit)==1 and None not in submit:
        entry=next(iter(submit));idx=image.function_starts.index(entry)
        if idx+1<len(image.function_starts):
            finish=image.function_starts[idx+1];file=image.virtual_to_file(entry)
            submit_code=list(decoder.disasm(image.data[file:file+finish-entry],entry))
            roots=[ins.reg_name(ins.operands[0].reg) for ins in submit_code[:20] if ins.mnemonic=='mov' and ins.op_str.endswith(', x0')]
            if len(roots)==1:
                for k,ins in enumerate(submit_code[:-2]):
                    if ins.mnemonic!='ldr' or ins.reg_name(ins.operands[1].mem.base)!=roots[0]:continue
                    flag,buffer=submit_code[k+1:k+3]
                    if flag.mnemonic!='ldr' or not flag.op_str.startswith('w') or flag.operands[1].mem.base!=ins.operands[0].reg:continue
                    if buffer.mnemonic!='ldr' or not buffer.op_str.startswith('x') or buffer.operands[1].mem.base!=ins.operands[0].reg:continue
                    register=buffer.reg_name(buffer.operands[0].reg)
                    committed=False
                    for n,call in enumerate(submit_code[1:],1):
                        if call.mnemonic!='bl' or submit_code[n-1].mnemonic!='mov' or submit_code[n-1].op_str!='x1, '+register:continue
                        target=decode_bl(int.from_bytes(call.bytes,'little'),call.address)
                        if target not in image.function_starts:continue
                        ti=image.function_starts.index(target)
                        if ti+1>=len(image.function_starts):continue
                        tf=image.virtual_to_file(target);te=image.function_starts[ti+1]
                        helper=list(decoder.disasm(image.data[tf:tf+te-target],target))
                        aliases=[x.op_str.split(', ')[0] for x in helper[:10] if x.mnemonic=='mov' and x.op_str.endswith(', x1')]
                        if len(aliases)!=1:continue
                        if any(x.mnemonic=='bl' and decode_bl(int.from_bytes(x.bytes,'little'),x.address) in stubs.get(b'commit',[]) and
                               n0>0 and helper[n0-1].mnemonic=='mov' and helper[n0-1].op_str=='x0, '+aliases[0] for n0,x in enumerate(helper)):committed=True
                    if committed:buffer_paths.append((ins.operands[1].mem.disp,buffer.operands[1].mem.disp))
    if len(buffer_paths)!=1:return []
    starts={scanner.resolve_function_start(r.instruction_address)[0] for r in scanner.find_string_xrefs('acquireFramebuffer')}
    matches=[]
    for start in starts:
        if start is None:continue
        index=image.function_starts.index(start)
        if index+1>=len(image.function_starts):continue
        end=image.function_starts[index+1];off=image.virtual_to_file(start)
        code=list(decoder.disasm(image.data[off:off+end-start],start))
        for i,call in enumerate(code):
            if call.mnemonic!='bl' or decode_bl(int.from_bytes(call.bytes,'little'),call.address) not in stubs.get(b'nextDrawable',[]):continue
            if i<1 or i+2>=len(code):continue
            load=code[i-1];retain=code[i+1];store=code[i+2]
            if load.mnemonic!='ldr' or not load.op_str.startswith('x0, [x') or retain.mnemonic!='bl' or store.mnemonic!='str' or not store.op_str.startswith('x0, [x'):continue
            later=[]
            for j in range(i+3,len(code)-3):
                access,branch,framebuffer,texture=code[j:j+4]
                if access.mnemonic!='ldr' or not access.op_str.startswith('x0, [') or access.operands[1].mem.disp!=store.operands[1].mem.disp:continue
                window=code[i-2] if i>=2 else None;reloaded=code[j-1]
                if not window or window.mnemonic!='ldr' or window.reg_name(window.operands[1].mem.base)!='sp':continue
                if reloaded.mnemonic!='ldr' or reloaded.operands[0].reg!=access.operands[1].mem.base or window.operands[0].reg!=store.operands[1].mem.base:continue
                if window.operands[1].mem.base!=reloaded.operands[1].mem.base or window.operands[1].mem.disp!=reloaded.operands[1].mem.disp:continue
                if branch.mnemonic!='cbz' or not branch.op_str.startswith('x0, '):continue
                if framebuffer.mnemonic!='ldr' or texture.mnemonic!='bl' or decode_bl(int.from_bytes(texture.bytes,'little'),texture.address) not in stubs.get(b'texture',[]):continue
                if j+7>=len(code):continue
                following=code[j+4:j+8]
                if following[0].mnemonic!='mov' or not following[0].op_str.endswith(', x0'):continue
                texture_reg=following[0].op_str.split(', ')[0]
                if following[1].mnemonic!='mov' or not following[1].op_str.startswith('x0, '):continue
                if following[2].mnemonic!='bl':continue
                after=code[j+7:j+11]
                if len(after)!=4 or after[0].mnemonic!='ldr' or after[0].op_str!='x8, [x0]' or after[1].op_str!='x0, [x8]' or after[2].op_str!='x1, '+texture_reg or after[3].mnemonic!='bl':continue
                bind=decode_bl(int.from_bytes(after[3].bytes,'little'),after[3].address)
                bind_off=image.virtual_to_file(bind)
                if bind_off is None:continue
                binding=list(decoder.disasm(image.data[bind_off:bind_off+8],bind))
                if len(binding)!=2 or binding[0].mnemonic!='str' or not binding[0].op_str.startswith('x1, [x0, #') or binding[1].mnemonic!='ret':continue
                fail=branch.operands[1].imm
                failure=[ins for ins in code if fail<=ins.address<fail+28]
                if not any(ins.mnemonic=='mov' and ins.op_str=='w0, #0' for ins in failure):continue
                later.append((branch,texture,fail))
            async_guards=[]
            for k in range(4,i):
                branch=code[k]
                if branch.mnemonic!='b.ne' or not call.address-32<=branch.operands[0].imm<=call.address:continue
                a,b,load,compare=code[k-4:k]
                ref=decode_address_reference(a.address,int.from_bytes(a.bytes,'little'),int.from_bytes(b.bytes,'little'))
                if ref and load.mnemonic=='ldrb' and load.op_str=='w8, [x8]' and compare.mnemonic=='cmp' and compare.op_str=='w8, #1':
                    segment=image.segment_for_va(ref.target_address)
                    if segment and segment.is_writable:async_guards.append(image.to_rva(ref.target_address))
            if len(later)==1 and len(async_guards)==1:
                branch,texture,fail=later[0]
                matches.append([
                    {'role':'fps-acquire','rva':image.to_rva(call.address),'before':call.bytes.hex(),'slot':40,'asyncFlagRva':async_guards[0]},
                    {'role':'fps-null','rva':image.to_rva(branch.address),'before':branch.bytes.hex(),'failureRva':image.to_rva(fail),'slot':48},
                    {'role':'fps-texture','rva':image.to_rva(texture.address),'before':texture.bytes.hex(),'slot':56,
                     'deviceRegister':next((int(ins.op_str.split(', ')[0][1:]) for ins in code[:20] if ins.mnemonic=='mov' and ins.op_str.endswith(', x0')),0),
                     'contextOffset':buffer_paths[0][0],'commandBufferOffset':buffer_paths[0][1]}])
    return matches[0] if len(matches)==1 else []

def resolve_roles(image):
    from roblox_ios_dumper.scanner import Arm64Scanner
    from roblox_ios_dumper.arm64 import decode_bl
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    scanner = Arm64Scanner(image)
    decoder = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN); decoder.detail = True
    scanner._index_targets({a for value in ('render2D', RENDER_ANCHOR, 'acquireFramebuffer','submitFrame') for a in image.find_cstrings(value)})
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
        if not matrix or matrix[0].mnemonic!='sub' or not matrix[0].op_str.startswith('sp, sp, #'): continue
        hooks.append({'role':'ui-width','rva':image.to_rva(matrix[0].address),'before':matrix[0].bytes.hex(),
                      'functionRva':image.to_rva(ui[0].address),'matrixRva':image.to_rva(target),'slot':16})
    if len(hooks) != 1: return []
    if not render or render[0].mnemonic != 'sub' or not render[0].op_str.startswith('sp, sp, #'): return []
    returns = [item for item in render if item.mnemonic == 'ret']
    if len(returns) != 1 or returns[0].op_str: return []
    hooks.extend([
        {'role':'render-enter','rva':image.to_rva(render[0].address),'before':render[0].bytes.hex(),'slot':24},
        {'role':'render-exit','rva':image.to_rva(returns[0].address),'before':returns[0].bytes.hex(),'slot':32}])
    return hooks+resolve_offscreen_roles(image,scanner,decoder)

def build_render_roles_profile(ipa, report):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'roblox'))
    from roblox_ios_dumper.macho import MachOImage
    with zipfile.ZipFile(ipa) as archive:
        image = MachOImage(archive.read('Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib'),'arm64')
    result = {'schema':1,'uuid':image.uuid,'sourceSha256':hashlib.sha256(image.data).hexdigest(),
              'verified':False,'deviceVerified':False,'hooks':[],'status':'UNRESOLVED_ROLES'}
    if image.architecture == 'arm64' and not image.is_encrypted and not image.slice.file_offset:
        result['hooks'] = resolve_roles(image)
        result['verified'] = len(result['hooks']) in (3,6)
        result['fpsVerified'] = len(result['hooks'])==6
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
    replacement=hook['role'].startswith('fps-')
    if hook['role'] != 'render-exit' and not replacement: emit(original)
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
    if hook['role']=='fps-null':emit(0xF90157E0) # default predicate: original X0 != NULL
    pc = start + len(words)*4
    delta = ((state & ~0xFFF) - (pc & ~0xFFF)) >> 12
    if not -(1<<20) <= delta < (1<<20): raise ValueError('Role state exceeds ADRP range')
    encoded = delta & 0x1FFFFF
    emit(0x90000010 | ((encoded & 3)<<29) | ((encoded>>2)<<5))
    emit(0xF9400210 | (((state & 0xFFF) + hook['slot'])//8<<10))
    emit(0xF9015BF0) # STR X16,[SP,#688] callback readiness, survives its caller-saved registers
    test = len(words); emit(0) # CBZ X16 skips the callback while slots are unbound
    if hook['role']=='fps-texture':emit(0xAA0003E1 | (hook['deviceRegister']<<16)) # MOV X1, verified DeviceMtl register
    emit(0xD63F0200) # BLR X16
    if hook['role'] == 'ui-width': emit(0xBD02ABE0) # STR S0,[SP,#680]
    if replacement:emit(0xF90157E0) # callback return X0
    bypass=None
    if hook['role'] in ('fps-acquire','fps-texture'):
        bypass=len(words);emit(0)
        fallback=len(words)
        from roblox_ios_dumper.arm64 import decode_bl
        target=decode_bl(original,hook['absoluteRva'])
        emit(encode_branch(start+len(words)*4,target)|0x80000000) # BL original ObjC stub
        emit(0xF90157E0)
    else:fallback=len(words)
    restore = len(words)
    words[test] = 0xB4000010 | ((fallback-test)<<5)
    if bypass is not None:words[bypass]=encode_branch(start+bypass*4,start+restore*4)
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
    if hook['role'] in ('fps-acquire','fps-texture'):
        emit(0xF94157E0) # replacement id result
        emit(0xF90003E0) # overwrite saved X0 with callback result
    for reg in range(0,20,2):
        second = 30 if reg == 18 else reg + 1
        emit(0xA9400000 | ((reg*8//8)<<15) | (second<<10) | (31<<5) | reg)
    if hook['role']=='fps-null':
        emit(0xF94157F0)
        conditional=len(words);emit(0)
        emit(0xA94847F0);emit(0x910B83FF)
        emit(encode_branch(start+len(words)*4,hook['absoluteRva']+4))
        failure=len(words)
        words[conditional]=0xB4000010|((failure-conditional)<<5)
        emit(0xA94847F0);emit(0x910B83FF)
        emit(encode_branch(start+len(words)*4,hook['absoluteFailure']))
    else:
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
    linkedit = next(s for s in image.segments if s.name == '__LINKEDIT')
    if linkedit.file_offset % 0x4000 or linkedit.vm_address % 0x4000 or linkedit.sections:
        raise ValueError('Role state requires aligned sectionless LINKEDIT')
    state = linkedit.vm_address; offset = linkedit.file_offset
    command = struct.pack('<II16sQQQQiiII',0x19,72,b'__MSDATA'.ljust(16,b'\0'),state,0x4000,offset,0x4000,3,3,0,0)
    if any(data[header_end:header_end+len(command)]): raise ValueError('No padding for role segment command')
    planned=[]
    for hook in profile['hooks']:
        address = image.image_base + hook['rva']; site = image.virtual_to_file(address)
        segment=image.segment_for_va(address)
        if site is None or not segment or not segment.is_executable or bytes(data[site:site+4]).hex()!=hook['before']: raise ValueError('Role instruction changed')
        actual={**hook,'absoluteRva':address,'absoluteFailure':image.image_base+hook.get('failureRva',0)}
        blob=trampoline(image.image_base+cave,state,actual)
        if cave+len(blob)>text.file_offset or any(data[cave:cave+len(blob)]): raise ValueError('Role TEXT padding unavailable')
        planned.append((site,cave,blob,encode_branch(address,image.image_base+cave)))
        cave=(cave+len(blob)+15)&~15
    output=bytearray(data)
    for site,cave,blob,branch in planned:
        output[cave:cave+len(blob)]=blob; struct.pack_into('<I',output,site,branch)
    # Keep LINKEDIT last. Insert a file/VM page before it and relocate its file-offset commands.
    output[offset:offset]=bytes(0x4000)
    commands=[]; cursor=32; count=struct.unpack_from('<I',data,16)[0]
    def relocate(blob, field, width=4):
        fmt='<Q' if width==8 else '<I'
        value=struct.unpack_from(fmt,blob,field)[0]
        if value >= offset and value: struct.pack_into(fmt,blob,field,value+0x4000)
    for _ in range(count):
        kind,size=struct.unpack_from('<II',data,cursor)
        blob=bytearray(data[cursor:cursor+size]);cursor+=size
        if kind==0x19 and bytes(blob[8:24]).rstrip(b'\0')==b'__LINKEDIT':
            commands.append(command)
            struct.pack_into('<Q',blob,24,linkedit.vm_address+0x4000)
            relocate(blob,40,8)
        elif kind==2:
            relocate(blob,8);relocate(blob,16)
        elif kind==11:
            for field in (32,40,48,56,64,72): relocate(blob,field)
        elif kind in (0x22,0x80000022):
            for field in (8,16,24,32,40): relocate(blob,field)
        elif kind in (0x1d,0x1e,0x26,0x29,0x2b,0x2e,0x80000033,0x80000034,0x16):
            relocate(blob,8)
        elif kind==0x31: relocate(blob,24,8)
        commands.append(blob)
    encoded=b''.join(commands)
    output[32:32+len(encoded)]=encoded
    struct.pack_into('<II',output,16,count+1,len(encoded))
    struct.pack_into('<Q',output,offset,COOKIE)
    profile['outputSha256']=hashlib.sha256(output).hexdigest()
    profile['stateRva']=image.to_rva(state)
    return output, image.to_rva(state)
