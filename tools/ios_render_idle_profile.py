"""Admit only the known RenderJob minimum-idle guard from this exact binary."""
import hashlib
import json
from bisect import bisect_right
from pathlib import Path
import struct
import sys
import zipfile
from ios_font_profile import resolve_import_slots

ANCHOR = '[FLog::SurfaceController] SurfaceController::RenderJob::scheduleRender: No data model. Bind workspace now...'
SLEEP = b'__ZNSt3__111this_thread9sleep_forERKNS_6chrono8durationIxNS_5ratioILl1ELl1000000000EEEEE'

def build_render_idle_profile(ipa, flags, report):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'roblox'))
    from roblox_ios_dumper.macho import MachOImage
    from roblox_ios_dumper.scanner import Arm64Scanner
    from roblox_ios_dumper.arm64 import decode_address_reference, decode_bl
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
    from capstone.arm64 import ARM64_OP_REG, ARM64_OP_IMM, ARM64_OP_MEM
    with zipfile.ZipFile(ipa) as archive:
        image = MachOImage(archive.read('Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib'), 'arm64')
    flag = flags.get('FFlagMainThreadIdle', 0)
    minimum = flags.get('FIntMainThreadMinIdleTimeUs', 0)
    result = {'schema': 1, 'uuid': image.uuid, 'sourceSha256': hashlib.sha256(image.data).hexdigest(),
              'verified': False, 'flagRva': flag, 'minimumRva': minimum, 'status': 'UNRESOLVED', 'deviceVerified': False}
    valid_storage = all((segment := image.segment_for_va(image.image_base + rva)) and
                        segment.is_readable and segment.is_writable and not segment.is_executable and
                        image.image_base + rva + width <= segment.end_address
                        for rva, width in ((flag, 1), (minimum, 4)))
    if flag and minimum and minimum % 4 == 0 and valid_storage and image.architecture == 'arm64' and not image.is_encrypted:
        scanner = Arm64Scanner(image)
        starts = set()
        for ref in scanner.find_string_xrefs(ANCHOR):
            index = bisect_right(image.function_starts, ref.instruction_address) - 1
            if index >= 0: starts.add(image.function_starts[index])
        slots = resolve_import_slots(image, {SLEEP: 0})
        targets = {image.image_base + s['rva'] for s in slots}
        stubs = set()
        for section in image.sections:
            if section.name != '__stubs': continue
            for offset in range(section.file_offset, section.file_offset + section.size - 11, 4):
                address = section.address + offset - section.file_offset
                a, b, c = struct.unpack_from('<III', image.data, offset)
                ref = decode_address_reference(address, a, b)
                if ref and ref.target_address in targets and a & 31 == 16 and b & 0xFFC003FF == 0xF9400210 and c == 0xD61F0200:
                    stubs.add(address) # ADRP X16; LDR X16,[X16,...]; BR X16
        if len(starts) == 1:
            start = next(iter(starts)); index = bisect_right(image.function_starts, start)
            end = image.function_starts[index] if index < len(image.function_starts) else start
            offset = image.virtual_to_file(start)
            decoder = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN); decoder.detail = True
            instructions = list(decoder.disasm(image.data[offset:offset + end - start], start))
            calls = [i.address for i in instructions if i.mnemonic == 'bl' and decode_bl(int.from_bytes(i.bytes, 'little'), i.address) in stubs]
            bases = {}; loads = {}; matches = []
            for i, instruction in enumerate(instructions):
                operands = instruction.operands
                new_load = None
                if instruction.mnemonic == 'adrp' and len(operands) == 2:
                    name = instruction.reg_name(operands[0].reg)
                    bases[name] = operands[1].imm; loads.pop('w' + name[1:], None)
                    continue
                if instruction.mnemonic in ('ldrb', 'ldr') and len(operands) == 2 and operands[1].type == ARM64_OP_MEM:
                    name = instruction.reg_name(operands[0].reg)
                    base = bases.get(instruction.reg_name(operands[1].mem.base))
                    if base is not None:
                        address = base + operands[1].mem.disp
                        if instruction.mnemonic == 'ldrb' and address == image.image_base + flag: new_load = (name, 'bool', instruction.address)
                        if instruction.mnemonic == 'ldr' and name.startswith('w') and address == image.image_base + minimum: new_load = (name, 'int', instruction.address)
                if instruction.mnemonic == 'cmp' and len(operands) == 2 and operands[0].type == ARM64_OP_REG and operands[1].type == ARM64_OP_IMM and operands[1].imm == 1 and i + 2 < len(instructions):
                    boolean = loads.get(instruction.reg_name(operands[0].reg))
                    conditional, branch = instructions[i + 1:i + 3]
                    ops = conditional.operands
                    if boolean and boolean[0] == 'bool' and instruction.address - boolean[1] <= 24 and conditional.mnemonic == 'ccmp' and conditional.op_str.endswith(', eq') and len(ops) == 3 and ops[0].type == ARM64_OP_REG and ops[1].type == ARM64_OP_IMM and ops[1].imm == 1 and ops[2].type == ARM64_OP_IMM and ops[2].imm == 8:
                        integer = loads.get(conditional.reg_name(ops[0].reg))
                        if integer and integer[0] == 'int' and instruction.address - integer[1] <= 24 and branch.mnemonic == 'b.lt' and branch.operands and branch.operands[0].type == ARM64_OP_IMM:
                            skip = branch.operands[0].imm
                            inside = [c for c in calls if branch.address < c < skip <= end]
                            if len(inside) == 1: matches.append((instruction.address, inside[0], skip))
                # Caller-saved base registers cannot survive a call; other writes invalidate their facts.
                _, written = instruction.regs_access()
                for register in written:
                    name = instruction.reg_name(register)
                    bases.pop('x' + name[1:] if name.startswith('w') else name, None)
                    loads.pop('w' + name[1:] if name.startswith('x') else name, None)
                if new_load: loads[new_load[0]] = new_load[1:]
                if instruction.mnemonic in ('bl', 'blr'):
                    for register in range(19): bases.pop('x' + str(register), None); loads.pop('w' + str(register), None)
            if len(matches) == 1:
                guard, sleep, skip = matches[0]
                result.update(verified=True, status='VERIFIED_RENDERJOB_MINIMUM_IDLE_GUARD', functionRva=image.to_rva(start), guardRva=image.to_rva(guard), sleepCallRva=image.to_rva(sleep), skipRva=image.to_rva(skip))
    report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result
