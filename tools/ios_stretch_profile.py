"""Resolve the shared frame -> pixel size -> framebuffer/drawable contract."""
import hashlib
import json
import struct
import sys
from pathlib import Path
import zipfile

ANCHORS = ('[FLog::Graphics] Metal: creating framebuffer %dx%d',
           '[FLog::Graphics] Metal: resizing main framebuffer to %dx%d')

def build_stretch_profile(ipa, report):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'roblox'))
    from roblox_ios_dumper.macho import MachOImage
    from roblox_ios_dumper.scanner import Arm64Scanner
    from roblox_ios_dumper.arm64 import decode_address_reference, decode_bl
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    with zipfile.ZipFile(ipa) as archive:
        image = MachOImage(archive.read('Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib'), 'arm64')
    result = {'schema': 1, 'uuid': image.uuid, 'sourceSha256': hashlib.sha256(image.data).hexdigest(),
              'verified': False, 'deviceVerified': False, 'status': 'UNRESOLVED_FRAMEBUFFER_SIZE_FLOW', 'roles': []}
    if image.architecture != 'arm64' or image.is_encrypted or image.slice.file_offset:
        report.write_text(json.dumps(result, indent=2), encoding='utf-8'); return result
    stubs = {}
    for section in image.sections:
        if section.name != '__objc_stubs': continue
        for offset in range(section.file_offset, section.file_offset + section.size - 19, 4):
            address = section.address + offset - section.file_offset
            a, b, c, d, e = struct.unpack_from('<5I', image.data, offset)
            ref = decode_address_reference(address, a, b)
            dispatch = decode_address_reference(address + 8, c, d)
            if not ref or not dispatch or a & 31 != 1 or b & 0xFFC003FF != 0xF9400021 or c & 31 != 16 or d & 0xFFC003FF != 0xF9400210 or e != 0xD61F0200: continue
            pointer = image.read_u64(ref.target_address)
            if pointer is None: continue
            string_offset = image.virtual_to_file(pointer)
            if string_offset is None: continue
            end = image.data.find(b'\0', string_offset, string_offset + 80)
            if end < 0: continue
            name = image.data[string_offset:end]
            if name in (b'frame', b'contentScaleFactor', b'setDrawableSize:'):
                stubs.setdefault(name.decode(), []).append(address)
    scanner = Arm64Scanner(image)
    scanner._index_targets({address for anchor in ANCHORS for address in image.find_cstrings(anchor)})
    decoder = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN); decoder.detail = True
    for anchor in ANCHORS:
        starts = {scanner.resolve_function_start(r.instruction_address)[0] for r in scanner.find_string_xrefs(anchor)}
        if len(starts) != 1 or None in starts: break
        start = next(iter(starts)); index = image.function_starts.index(start)
        if index + 1 == len(image.function_starts): break
        end = image.function_starts[index + 1]; offset = image.virtual_to_file(start)
        code = list(decoder.disasm(image.data[offset:offset + end - start], start))
        if not code or code[-1].address + 4 != end: break
        calls = [(i, decode_bl(int.from_bytes(ins.bytes, 'little'), ins.address))
                 for i, ins in enumerate(code) if ins.mnemonic in ('bl', 'b')]
        frame = [i for i, target in calls if target in stubs.get('frame', [])]
        scale = [i for i, target in calls if target in stubs.get('contentScaleFactor', [])]
        drawable = [i for i, target in calls if target in stubs.get('setDrawableSize:', [])]
        if len(frame) != 2 or len(scale) != 2 or len(drawable) != 1: break
        i, j = frame
        if not (i < scale[0] < j < scale[1] < drawable[0]): break
        if any(ins.mnemonic.startswith(('b.', 'cb', 'tb')) or ins.mnemonic == 'b'
               for ins in code[i:scale[1]]): break
        # Match dimensions by dataflow; compiler may insert unrelated instructions.
        def unique_between(start, stop, predicate):
            matches = [k for k in range(start, stop) if predicate(code[k])]
            return matches[0] if len(matches) == 1 else None
        receiver = code[i - 1].op_str if code[i - 1].mnemonic == 'mov' else None
        if not receiver or not receiver.startswith('x0, x'): break
        if any(unique_between(lo, hi, lambda ins: ins.mnemonic == 'mov' and ins.op_str == receiver) is None
               for lo, hi in ((i + 1, scale[0]), (scale[0] + 1, j), (j + 1, scale[1]))): break
        width_move = unique_between(i + 1, scale[0], lambda ins: ins.mnemonic == 'fmov' and ins.op_str.endswith(', d2'))
        height_move = unique_between(j + 1, scale[1], lambda ins: ins.mnemonic == 'fmov' and ins.op_str.endswith(', d3'))
        if width_move is None or height_move is None: break
        temp = code[width_move].op_str.split(', ')[0]
        if code[height_move].op_str.split(', ')[0] != temp: break
        width_multiply = unique_between(scale[0] + 1, j, lambda ins: ins.mnemonic == 'fmul' and ins.op_str.endswith(', d0, ' + temp))
        height_multiply = unique_between(scale[1] + 1, scale[1] + 10, lambda ins: ins.mnemonic == 'fmul' and ins.op_str == 'd0, d0, ' + temp)
        if width_multiply is None or height_multiply is None: break
        width = code[width_multiply].op_str.split(', ')[0]
        conversions = [k for k in range(height_multiply + 1, min(height_multiply + 10, len(code)))
                       if code[k].mnemonic == 'fcvtzu']
        if len(conversions) != 2: break
        wreg, hreg = (code[k].op_str.split(', ')[0] for k in conversions)
        if not wreg.startswith('w') or not hreg.startswith('w'): break
        if wreg == hreg or not all(19 <= int(reg[1:]) <= 28 for reg in (wreg, hreg)): break
        if code[conversions[0]].op_str != wreg + ', ' + width or code[conversions[1]].op_str != hreg + ', d0': break
        setter = drawable[0]
        size_loads = [k for k in range(conversions[1] + 1, setter - 1) if code[k].mnemonic == code[k + 1].mnemonic == 'ucvtf' and
                   code[k].op_str == 'd0, ' + wreg and code[k + 1].op_str == 'd1, ' + hreg
                   ]
        if len(size_loads) != 1: break
        allocations = [(code[k].address, target) for k, target in calls if k >= 3 and
                       code[k - 2].mnemonic == code[k - 1].mnemonic == 'mov' and
                       code[k - 2].op_str == 'x1, x' + wreg[1:] and code[k - 1].op_str == 'x2, x' + hreg[1:]]
        if len(allocations) != 1: break
        allocator_segment = image.segment_for_va(allocations[0][1])
        if not allocator_segment or not allocator_segment.is_executable: break
        # Early-return epilogues may restore these registers but cannot reach either size use.
        parents = {}
        for k, ins in enumerate(code):
            successors = [] if ins.mnemonic in ('ret', 'br', 'b') else [ins.address + 4]
            if ins.mnemonic == 'b' or ins.mnemonic.startswith('b.') or ins.mnemonic in ('cbz', 'cbnz', 'tbz', 'tbnz'):
                successors.append(ins.operands[-1].imm)
            for address in successors: parents.setdefault(address, []).append(k)
        pending = [code[size_loads[0]].address, allocations[0][0]]; relevant = set()
        while pending:
            for k in parents.get(pending.pop(), []):
                if k <= conversions[1] or k in relevant: continue
                relevant.add(k); pending.append(code[k].address)
        if any(ins.reg_name(reg).lstrip('xw') in (wreg[1:], hreg[1:])
               for k in relevant for ins in [code[k]] for reg in ins.regs_access()[1]): break
        result['roles'].append({'anchor': anchor, 'functionRva': image.to_rva(start),
                                'frameReturnRva': image.to_rva(code[i].address + 4),
                                'drawableCallRva': image.to_rva(code[setter].address),
                                'allocatorRva': image.to_rva(allocations[0][1])})
    roles = result['roles']
    if len(roles) == 2 and roles[0]['functionRva'] != roles[1]['functionRva'] and roles[0]['allocatorRva'] == roles[1]['allocatorRva']:
        result.update(verified=True, status='VERIFIED_FRAMEBUFFER_AND_DRAWABLE_SIZE_FLOW')
    report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result
