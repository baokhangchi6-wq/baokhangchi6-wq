"""Profile exact libc++ wait imports and BL caller functions; never guess engine ABI."""
import hashlib
import json
from bisect import bisect_right
from pathlib import Path
import struct
import sys
import zipfile
from ios_font_profile import resolve_import_slots

WAIT_SYMBOLS = {
    b'__ZNSt3__118condition_variable4waitERNS_11unique_lockINS_5mutexEEE': 0,
    b'__ZNSt3__118condition_variable15__do_timed_waitERNS_11unique_lockINS_5mutexEEENS_6chrono10time_pointINS5_12system_clockENS5_8durationIxNS_5ratioILl1ELl1000000000EEEEEEE': 1,
}

def build_pacing_profile(ipa, report):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'roblox'))
    from roblox_ios_dumper.macho import MachOImage
    from roblox_ios_dumper.arm64 import decode_address_reference, decode_bl
    with zipfile.ZipFile(ipa) as archive:
        image = MachOImage(archive.read('Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib'), 'arm64')
    if image.is_encrypted or image.slice.file_offset or image.architecture != 'arm64':
        raise ValueError('Pacing profile requires decrypted thin arm64')
    slots = resolve_import_slots(image, WAIT_SYMBOLS)
    slot_addresses = {image.image_base + slot['rva'] for slot in slots}
    stubs = set()
    for section in image.sections:
        if section.name != '__stubs': continue
        for offset in range(section.file_offset, section.file_offset + section.size - 7, 4):
            address = section.address + offset - section.file_offset
            first, second = struct.unpack_from('<II', image.data, offset)
            ref = decode_address_reference(address, first, second)
            if ref and ref.target_address in slot_addresses: stubs.add(address)
    callers = []
    for section in image.executable_sections:
        for offset in range(section.file_offset, section.file_offset + section.size - 3, 4):
            address = section.address + offset - section.file_offset
            word = struct.unpack_from('<I', image.data, offset)[0]
            if word & 0xFC000000 != 0x94000000 or decode_bl(word, address) not in stubs: continue
            index = bisect_right(image.function_starts, address) - 1
            if index < 0 or index + 1 >= len(image.function_starts): continue
            callers.append({'callRva': image.to_rva(address), 'startRva': image.to_rva(image.function_starts[index]),
                            'endRva': image.to_rva(image.function_starts[index + 1])})
    result = {'schema': 1, 'uuid': image.uuid, 'sourceSha256': hashlib.sha256(image.data).hexdigest(),
              'readerSlots': slots, 'callers': callers, 'mode': 'observe-only-preserve-wait', 'engineFpsVerified': False}
    report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result
