"""Resolve stock font assets and C import slots from the exact IPA; no fallback RVA."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
import sys
import zipfile

CATALOG = dict(zip(
    ('Minecraft', 'GothamBold', 'Cyberpunk', 'Fredoka', 'AnimeHand', 'ComicBangers', 'Typewriter', 'Creepster', 'Merriweather', 'Highway'),
    ('PressStart2P', 'Montserrat', 'Zekton', 'FredokaOne', 'IndieFlower', 'Bangers', 'SpecialElite', 'Creepster', 'Merriweather', 'HighwayGothic')))

def resolve_font_imports(image):
    data = image.data
    symbols = indirect = None
    sections = []
    cursor = 32
    for _ in range(struct.unpack_from('<I', data, 16)[0]):
        command, size = struct.unpack_from('<II', data, cursor)
        if command == 2:
            if size < 24: raise ValueError('Truncated LC_SYMTAB')
            symbols = struct.unpack_from('<6I', data, cursor)
        elif command == 11:
            if size < 80: raise ValueError('Truncated LC_DYSYMTAB')
            indirect = struct.unpack_from('<20I', data, cursor)
        elif command == 0x19:
            count = struct.unpack_from('<I', data, cursor + 64)[0]
            sections.extend(struct.unpack_from('<16s16sQQIIIIIIII', data, cursor + 72 + j * 80) for j in range(count))
        cursor += size
    if not symbols or not indirect: return []
    symoff, count, stroff, strsize = symbols[2:]
    indoff, indcount = indirect[14:16]
    if count > 1000000 or indcount > 1000000 or symoff + count * 16 > len(data) or stroff + strsize > len(data) or indoff + indcount * 4 > len(data):
        raise ValueError('Font symbol tables exceed input bounds')
    slots = []
    for section in sections:
        if section[8] & 255 not in (6, 7): continue
        address, size, first = section[2], section[3], section[9]
        segment = image.segment_for_va(address)
        if size % 8 or not segment or segment.is_executable or not segment.is_readable or not segment.max_protection & 2 or first + size // 8 > indcount:
            raise ValueError('Invalid font import pointer section')
        for j in range(size // 8):
            index = struct.unpack_from('<I', data, indoff + (first + j) * 4)[0]
            if index & 0xC0000000: continue
            if index >= count: raise ValueError('Font indirect symbol index exceeds table')
            nameoff, kind, number, _, _ = struct.unpack_from('<IBBHQ', data, symoff + index * 16)
            if nameoff >= strsize: raise ValueError('Font symbol name exceeds string table')
            end = data.find(b'\0', stroff + nameoff, stroff + strsize)
            if end < 0: raise ValueError('Unterminated font symbol name')
            name = data[stroff + nameoff:end]
            if name in (b'_fopen', b'_open') and kind == 1 and number == 0:
                slots.append({'symbol': name.decode(), 'rva': image.to_rva(address + j * 8), 'kind': 0 if name == b'_fopen' else 1})
    return slots

def build_font_profile(ipa, report):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'roblox'))
    from roblox_ios_dumper.macho import MachOImage
    with zipfile.ZipFile(ipa) as archive:
        engine = 'Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib'
        image = MachOImage(archive.read(engine), 'arm64')
        if image.is_encrypted or image.slice.file_offset or image.architecture != 'arm64':
            raise ValueError('Font import rebinding requires decrypted thin arm64, not arm64e')
        slots = resolve_font_imports(image)
        root = str(PurePosixPath(engine).parent / 'content') + '/'
        family_root = root + 'fonts/families/'
        names = set(archive.namelist())
        families = sorted(PurePosixPath(n).name for n in names if n.startswith(family_root) and
            n.count('/') == family_root.count('/') and re.fullmatch(r'[A-Za-z0-9_-]+\.json', PurePosixPath(n).name))
        available = {}
        for name, family in CATALOG.items():
            path = family_root + family + '.json'
            if path not in names: continue
            document = json.loads(archive.read(path))
            faces = document.get('faces', [])
            if not faces: continue
            local = [face.get('assetId', '') for face in faces if face.get('assetId', '').startswith('rbxasset://')]
            if not local or any('..' in PurePosixPath(asset[len('rbxasset://'):]).parts or
                not asset[len('rbxasset://'):].startswith('fonts/') or root + asset[len('rbxasset://'):] not in names for asset in local): continue
            available[name] = family + '.json'
    result = {'schema': 1, 'uuid': image.uuid, 'sourceSha256': hashlib.sha256(image.data).hexdigest(),
              'readerSlots': slots, 'stockFamilies': families, 'catalog': available,
              'sizeStepSupported': False, 'deviceVerified': False}
    report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    if not slots or 'Minecraft' not in available: raise ValueError('Font gate missing reader imports or stock Minecraft assets')
    return result
