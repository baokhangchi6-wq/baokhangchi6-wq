from __future__ import annotations

import plistlib
import struct
import uuid
import zipfile
from pathlib import Path


BASE = 0x100000000
TEXT_FILE_OFFSET = 0x400
TEXT_ADDRESS = BASE + TEXT_FILE_OFFSET
CSTRING_FILE_OFFSET = 0x700
CSTRING_ADDRESS = BASE + CSTRING_FILE_OFFSET
DATA_ADDRESS = BASE + 0x2000
DATA_FILE_OFFSET = 0x1000


def _fixed(value: str) -> bytes:
    return value.encode("ascii").ljust(16, b"\0")


def _section(
    section: str,
    segment: str,
    address: int,
    size: int,
    file_offset: int,
    flags: int,
) -> bytes:
    return struct.pack(
        "<16s16sQQIIIIIIII",
        _fixed(section),
        _fixed(segment),
        address,
        size,
        file_offset,
        2,
        0,
        0,
        flags,
        0,
        0,
        0,
    )


def _segment(
    name: str,
    vm_address: int,
    vm_size: int,
    file_offset: int,
    file_size: int,
    protections: int,
    sections: list[bytes],
) -> bytes:
    command_size = 72 + sum(len(item) for item in sections)
    header = struct.pack(
        "<II16sQQQQiiII",
        0x19,
        command_size,
        _fixed(name),
        vm_address,
        vm_size,
        file_offset,
        file_size,
        protections,
        protections,
        len(sections),
        0,
    )
    return header + b"".join(sections)


def encode_adrp(pc: int, target: int, register: int) -> int:
    page_delta = (target & ~0xFFF) - (pc & ~0xFFF)
    immediate = page_delta >> 12
    if immediate < 0:
        immediate += 1 << 21
    immediate &= (1 << 21) - 1
    imm_low = immediate & 0x3
    imm_high = immediate >> 2
    return 0x90000000 | (imm_low << 29) | (imm_high << 5) | register


def encode_add(register: int, base_register: int, immediate: int) -> int:
    assert 0 <= immediate < 0x1000
    return 0x91000000 | (immediate << 10) | (base_register << 5) | register


def encode_bl(pc: int, target: int) -> int:
    delta = target - pc
    assert delta % 4 == 0
    return 0x94000000 | ((delta >> 2) & 0x03FFFFFF)


def build_macho(*, encrypted: bool = False) -> bytes:
    text_sections = [
        _section("__text", "__TEXT", TEXT_ADDRESS, 0x280, TEXT_FILE_OFFSET, 0),
        _section("__cstring", "__TEXT", CSTRING_ADDRESS, 0x300, CSTRING_FILE_OFFSET, 0x2),
    ]
    data_sections = [
        _section("__data", "__DATA", DATA_ADDRESS, 0x200, DATA_FILE_OFFSET, 0),
    ]
    commands = [
        _segment("__TEXT", BASE, 0x1000, 0, 0x1000, 5, text_sections),
        _segment("__DATA", DATA_ADDRESS, 0x1000, DATA_FILE_OFFSET, 0x1000, 3, data_sections),
        struct.pack("<II16s", 0x1B, 24, uuid.UUID("12345678-1234-5678-1234-567812345678").bytes),
        struct.pack("<IIIII", 0x2C, 24, 0x400, 0x200, 1 if encrypted else 0) + struct.pack("<I", 0),
        struct.pack("<IIIIII", 0x32, 24, 2, 0x00100000, 0x00110000, 0),
    ]
    command_blob = b"".join(commands)
    header = struct.pack(
        "<IiiIIIII",
        0xFEEDFACF,
        0x0100000C,
        2,
        2,
        len(commands),
        len(command_blob),
        0,
        0,
    )
    data = bytearray(0x2000)
    data[: len(header)] = header
    data[len(header) : len(header) + len(command_blob)] = command_blob

    strings = {
        "EnableLoadModule": CSTRING_ADDRESS,
        "FFlagDebugDisplayFPS": CSTRING_ADDRESS + 0x30,
        "Current identity is %d": CSTRING_ADDRESS + 0x70,
    }
    for value, address in strings.items():
        offset = address - BASE
        encoded = value.encode("ascii") + b"\0"
        data[offset : offset + len(encoded)] = encoded

    register_function = TEXT_ADDRESS + 0x180
    # Function containing the EnableLoadModule registration anchor.
    struct.pack_into("<I", data, TEXT_FILE_OFFSET, 0xD10083FF)
    anchor_pc = TEXT_ADDRESS + 0x20
    anchor_offset = TEXT_FILE_OFFSET + 0x20
    struct.pack_into("<I", data, anchor_offset, encode_adrp(anchor_pc, strings["EnableLoadModule"], 0))
    struct.pack_into("<I", data, anchor_offset + 4, encode_add(0, 0, strings["EnableLoadModule"] & 0xFFF))
    struct.pack_into("<I", data, anchor_offset + 8, encode_bl(anchor_pc + 8, register_function))

    # A second function registers FFlagDebugDisplayFPS and references writable storage.
    second_start = TEXT_ADDRESS + 0x80
    second_offset = TEXT_FILE_OFFSET + 0x80
    struct.pack_into("<I", data, second_offset, 0xD10083FF)
    name_pc = second_start + 0x10
    struct.pack_into("<I", data, second_offset + 0x10, encode_adrp(name_pc, strings["FFlagDebugDisplayFPS"], 1))
    struct.pack_into("<I", data, second_offset + 0x14, encode_add(1, 1, strings["FFlagDebugDisplayFPS"] & 0xFFF))
    storage = DATA_ADDRESS + 0x40
    storage_pc = second_start + 0x18
    struct.pack_into("<I", data, second_offset + 0x18, encode_adrp(storage_pc, storage, 0))
    struct.pack_into("<I", data, second_offset + 0x1C, encode_add(0, 0, storage & 0xFFF))
    struct.pack_into("<I", data, second_offset + 0x20, 0x3900001F)  # STRB WZR,[X0]
    call_pc = second_start + 0x24
    struct.pack_into("<I", data, second_offset + 0x24, encode_bl(call_pc, register_function))

    # An offset anchor in its own function.
    third_start = TEXT_ADDRESS + 0x100
    third_offset = TEXT_FILE_OFFSET + 0x100
    struct.pack_into("<I", data, third_offset, 0xD10083FF)
    print_pc = third_start + 0x10
    struct.pack_into("<I", data, third_offset + 0x10, encode_adrp(print_pc, strings["Current identity is %d"], 2))
    struct.pack_into("<I", data, third_offset + 0x14, encode_add(2, 2, strings["Current identity is %d"] & 0xFFF))
    return bytes(data)


def build_ipa(path: Path, *, encrypted: bool = False) -> None:
    info = {
        "CFBundleExecutable": "Roblox",
        "CFBundleIdentifier": "com.roblox.client",
        "CFBundleShortVersionString": "2.735.1138",
        "CFBundleVersion": "1138",
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("Payload/Roblox.app/Info.plist", plistlib.dumps(info))
        archive.writestr("Payload/Roblox.app/Roblox", build_macho(encrypted=encrypted))
