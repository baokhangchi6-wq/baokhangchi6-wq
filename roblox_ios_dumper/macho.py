from __future__ import annotations

import struct
import uuid as uuidlib
from dataclasses import dataclass, field
from typing import Iterable


CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_MASK = 0x00FFFFFF
CPU_SUBTYPE_ARM64E = 2

LC_SEGMENT_64 = 0x19
LC_UUID = 0x1B
LC_ENCRYPTION_INFO = 0x21
LC_ENCRYPTION_INFO_64 = 0x2C
LC_MAIN = 0x80000028
LC_BUILD_VERSION = 0x32

VM_PROT_READ = 0x1
VM_PROT_WRITE = 0x2
VM_PROT_EXECUTE = 0x4
SECTION_TYPE = 0x000000FF
S_CSTRING_LITERALS = 0x2
S_ZEROFILL = 0x1
S_GB_ZEROFILL = 0xC
S_THREAD_LOCAL_ZEROFILL = 0x12


class MachOError(ValueError):
    pass


@dataclass(frozen=True)
class SliceSelection:
    file_offset: int
    size: int
    cpu_type: int
    cpu_subtype: int
    architecture: str
    container: str


@dataclass(frozen=True)
class MachOSection:
    segment_name: str
    name: str
    address: int
    size: int
    file_offset: int
    align: int
    flags: int

    @property
    def end_address(self) -> int:
        return self.address + self.size

    @property
    def section_type(self) -> int:
        return self.flags & SECTION_TYPE

    @property
    def is_zero_fill(self) -> bool:
        return self.section_type in {
            S_ZEROFILL,
            S_GB_ZEROFILL,
            S_THREAD_LOCAL_ZEROFILL,
        }


@dataclass
class MachOSegment:
    name: str
    vm_address: int
    vm_size: int
    file_offset: int
    file_size: int
    max_protection: int
    initial_protection: int
    flags: int
    sections: list[MachOSection] = field(default_factory=list)

    @property
    def end_address(self) -> int:
        return self.vm_address + self.vm_size

    @property
    def is_readable(self) -> bool:
        return bool(self.initial_protection & VM_PROT_READ)

    @property
    def is_writable(self) -> bool:
        return bool(self.initial_protection & VM_PROT_WRITE)

    @property
    def is_executable(self) -> bool:
        return bool(self.initial_protection & VM_PROT_EXECUTE)


@dataclass(frozen=True)
class EncryptionInfo:
    file_offset: int
    size: int
    crypt_id: int

    @property
    def encrypted(self) -> bool:
        return self.crypt_id != 0


@dataclass(frozen=True)
class BuildVersion:
    platform: int
    minimum_os: str
    sdk: str


def _fixed_name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")


def _version_word(value: int) -> str:
    return f"{(value >> 16) & 0xFFFF}.{(value >> 8) & 0xFF}.{value & 0xFF}"


def _architecture(cpu_type: int, cpu_subtype: int) -> str:
    if cpu_type != CPU_TYPE_ARM64:
        return f"cpu-{cpu_type:#x}-{cpu_subtype & CPU_SUBTYPE_MASK:#x}"
    if (cpu_subtype & CPU_SUBTYPE_MASK) == CPU_SUBTYPE_ARM64E:
        return "arm64e"
    return "arm64"


def select_macho_slice(data: bytes, preferred_arch: str = "auto") -> tuple[bytes, SliceSelection]:
    if len(data) < 4:
        raise MachOError("Input is too small to contain a Mach-O header")

    magic = data[:4]
    thin_magics = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}
    if magic in thin_magics:
        endian = "<" if magic == b"\xcf\xfa\xed\xfe" else ">"
        if len(data) < 12:
            raise MachOError("Truncated Mach-O header")
        cpu_type, cpu_subtype = struct.unpack_from(endian + "ii", data, 4)
        selection = SliceSelection(
            file_offset=0,
            size=len(data),
            cpu_type=cpu_type,
            cpu_subtype=cpu_subtype,
            architecture=_architecture(cpu_type, cpu_subtype),
            container="thin",
        )
        return data, selection

    fat_layouts = {
        b"\xca\xfe\xba\xbe": (">", False),
        b"\xbe\xba\xfe\xca": ("<", False),
        b"\xca\xfe\xba\xbf": (">", True),
        b"\xbf\xba\xfe\xca": ("<", True),
    }
    layout = fat_layouts.get(magic)
    if layout is None:
        raise MachOError(
            "Unsupported binary magic; expected Mach-O 64-bit or Universal Mach-O"
        )

    endian, is_64 = layout
    if len(data) < 8:
        raise MachOError("Truncated Universal Mach-O header")
    arch_count = struct.unpack_from(endian + "I", data, 4)[0]
    if arch_count == 0 or arch_count > 128:
        raise MachOError(f"Invalid Universal Mach-O architecture count: {arch_count}")

    cursor = 8
    candidates: list[SliceSelection] = []
    for _ in range(arch_count):
        entry_size = 32 if is_64 else 20
        if cursor + entry_size > len(data):
            raise MachOError("Truncated Universal Mach-O architecture table")
        if is_64:
            cpu_type, cpu_subtype, offset, size, _align, _reserved = struct.unpack_from(
                endian + "iiQQII", data, cursor
            )
        else:
            cpu_type, cpu_subtype, offset, size, _align = struct.unpack_from(
                endian + "iiIII", data, cursor
            )
        cursor += entry_size
        if offset > len(data) or size > len(data) - offset:
            raise MachOError("Universal Mach-O slice points outside the input")
        candidates.append(
            SliceSelection(
                file_offset=offset,
                size=size,
                cpu_type=cpu_type,
                cpu_subtype=cpu_subtype,
                architecture=_architecture(cpu_type, cpu_subtype),
                container="fat64" if is_64 else "fat",
            )
        )

    arm_candidates = [item for item in candidates if item.cpu_type == CPU_TYPE_ARM64]
    if not arm_candidates:
        found = ", ".join(item.architecture for item in candidates)
        raise MachOError(f"Universal Mach-O has no ARM64 slice (found: {found})")

    if preferred_arch not in {"auto", "arm64", "arm64e"}:
        raise MachOError(f"Unsupported preferred architecture: {preferred_arch}")
    if preferred_arch == "auto":
        selected = next(
            (item for item in arm_candidates if item.architecture == "arm64e"),
            arm_candidates[0],
        )
    else:
        selected = next(
            (item for item in arm_candidates if item.architecture == preferred_arch),
            None,
        )
        if selected is None:
            found = ", ".join(item.architecture for item in arm_candidates)
            raise MachOError(
                f"Requested {preferred_arch} slice is unavailable (found: {found})"
            )
    return (
        data[selected.file_offset : selected.file_offset + selected.size],
        selected,
    )


class MachOImage:
    """Bounds-checked parser for the selected ARM64 slice of a Mach-O binary."""

    def __init__(self, container_data: bytes, preferred_arch: str = "auto") -> None:
        self.container_size = len(container_data)
        self.data, self.slice = select_macho_slice(container_data, preferred_arch)
        self.endian = self._detect_endian()
        self.cpu_type = 0
        self.cpu_subtype = 0
        self.file_type = 0
        self.flags = 0
        self.uuid: str | None = None
        self.build_version: BuildVersion | None = None
        self.encryption: list[EncryptionInfo] = []
        self.entry_file_offset: int | None = None
        self.segments: list[MachOSegment] = []
        self._parse()
        if self.cpu_type != CPU_TYPE_ARM64:
            raise MachOError(
                f"Selected Mach-O slice is not ARM64: cpu_type={self.cpu_type:#x}"
            )
        text = next((seg for seg in self.segments if seg.name == "__TEXT"), None)
        if text is None:
            raise MachOError("Mach-O has no __TEXT segment")
        self.image_base = text.vm_address

    def _detect_endian(self) -> str:
        magic = self.data[:4]
        if magic == b"\xcf\xfa\xed\xfe":
            return "<"
        if magic == b"\xfe\xed\xfa\xcf":
            return ">"
        raise MachOError("Selected slice is not a 64-bit Mach-O")

    def _parse(self) -> None:
        header_size = 32
        if len(self.data) < header_size:
            raise MachOError("Truncated Mach-O 64-bit header")
        (
            _magic,
            self.cpu_type,
            self.cpu_subtype,
            self.file_type,
            command_count,
            command_bytes,
            self.flags,
            _reserved,
        ) = struct.unpack_from(self.endian + "IiiIIIII", self.data, 0)
        if command_count > 100_000:
            raise MachOError(f"Unreasonable load command count: {command_count}")
        command_end = header_size + command_bytes
        if command_end > len(self.data):
            raise MachOError("Mach-O load commands extend past the selected slice")

        cursor = header_size
        for _ in range(command_count):
            if cursor + 8 > command_end:
                raise MachOError("Truncated Mach-O load command")
            command, command_size = struct.unpack_from(self.endian + "II", self.data, cursor)
            if command_size < 8 or cursor + command_size > command_end:
                raise MachOError(
                    f"Invalid Mach-O load command size {command_size} at {cursor:#x}"
                )
            if command == LC_SEGMENT_64:
                self._parse_segment(cursor, command_size)
            elif command == LC_UUID:
                if command_size >= 24:
                    self.uuid = str(uuidlib.UUID(bytes=bytes(self.data[cursor + 8 : cursor + 24])))
            elif command in {LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64}:
                if command_size >= 20:
                    crypt_offset, crypt_size, crypt_id = struct.unpack_from(
                        self.endian + "III", self.data, cursor + 8
                    )
                    self.encryption.append(
                        EncryptionInfo(crypt_offset, crypt_size, crypt_id)
                    )
            elif command == LC_BUILD_VERSION:
                if command_size >= 24:
                    platform, minimum_os, sdk, _tool_count = struct.unpack_from(
                        self.endian + "IIII", self.data, cursor + 8
                    )
                    self.build_version = BuildVersion(
                        platform=platform,
                        minimum_os=_version_word(minimum_os),
                        sdk=_version_word(sdk),
                    )
            elif command == LC_MAIN:
                if command_size >= 24:
                    self.entry_file_offset = struct.unpack_from(
                        self.endian + "Q", self.data, cursor + 8
                    )[0]
            cursor += command_size

        if cursor != command_end:
            raise MachOError("Mach-O load command size accounting mismatch")
        if not self.segments:
            raise MachOError("Mach-O contains no LC_SEGMENT_64 command")

    def _parse_segment(self, cursor: int, command_size: int) -> None:
        if command_size < 72:
            raise MachOError("Truncated LC_SEGMENT_64 command")
        (
            raw_name,
            vm_address,
            vm_size,
            file_offset,
            file_size,
            max_protection,
            initial_protection,
            section_count,
            flags,
        ) = struct.unpack_from(self.endian + "16sQQQQiiII", self.data, cursor + 8)
        expected_size = 72 + section_count * 80
        if expected_size > command_size:
            raise MachOError("LC_SEGMENT_64 section table exceeds command size")
        if file_size and (file_offset > len(self.data) or file_size > len(self.data) - file_offset):
            raise MachOError(
                f"Segment {_fixed_name(raw_name)} file range exceeds selected slice"
            )

        segment = MachOSegment(
            name=_fixed_name(raw_name),
            vm_address=vm_address,
            vm_size=vm_size,
            file_offset=file_offset,
            file_size=file_size,
            max_protection=max_protection,
            initial_protection=initial_protection,
            flags=flags,
        )
        section_cursor = cursor + 72
        for _ in range(section_count):
            (
                raw_section_name,
                raw_segment_name,
                address,
                size,
                file_off,
                align,
                _relocation_offset,
                _relocation_count,
                section_flags,
                _reserved1,
                _reserved2,
                _reserved3,
            ) = struct.unpack_from(
                self.endian + "16s16sQQIIIIIIII", self.data, section_cursor
            )
            section_cursor += 80
            section = MachOSection(
                segment_name=_fixed_name(raw_segment_name),
                name=_fixed_name(raw_section_name),
                address=address,
                size=size,
                file_offset=file_off,
                align=align,
                flags=section_flags,
            )
            if not section.is_zero_fill and size:
                if file_off > len(self.data) or size > len(self.data) - file_off:
                    raise MachOError(
                        f"Section {section.segment_name},{section.name} exceeds selected slice"
                    )
            segment.sections.append(section)
        self.segments.append(segment)

    @property
    def architecture(self) -> str:
        return _architecture(self.cpu_type, self.cpu_subtype)

    @property
    def is_encrypted(self) -> bool:
        return any(info.encrypted for info in self.encryption)

    @property
    def sections(self) -> list[MachOSection]:
        return [section for segment in self.segments for section in segment.sections]

    @property
    def executable_sections(self) -> list[MachOSection]:
        preferred = [
            section
            for segment in self.segments
            if segment.is_executable
            for section in segment.sections
            if section.size and not section.is_zero_fill and section.name == "__text"
        ]
        if preferred:
            return preferred
        return [
            section
            for segment in self.segments
            if segment.is_executable
            for section in segment.sections
            if section.size and not section.is_zero_fill
        ]

    @property
    def searchable_string_sections(self) -> list[MachOSection]:
        preferred_names = {
            "__cstring",
            "__const",
            "__objc_methname",
            "__objc_classname",
        }
        return [
            section
            for section in self.sections
            if not section.is_zero_fill
            and section.size
            and (
                section.section_type == S_CSTRING_LITERALS
                or section.name in preferred_names
            )
        ]

    def segment_for_va(self, address: int) -> MachOSegment | None:
        return next(
            (
                segment
                for segment in self.segments
                if segment.vm_address <= address < segment.end_address
            ),
            None,
        )

    def section_for_va(self, address: int) -> MachOSection | None:
        return next(
            (
                section
                for section in self.sections
                if section.address <= address < section.end_address
            ),
            None,
        )

    def virtual_to_file(self, address: int) -> int | None:
        segment = self.segment_for_va(address)
        if segment is None:
            return None
        delta = address - segment.vm_address
        if delta >= segment.file_size:
            return None
        offset = segment.file_offset + delta
        return offset if 0 <= offset < len(self.data) else None

    def file_to_virtual(self, offset: int) -> int | None:
        for segment in self.segments:
            if segment.file_offset <= offset < segment.file_offset + segment.file_size:
                return segment.vm_address + (offset - segment.file_offset)
        return None

    def to_rva(self, address: int) -> int:
        if address < self.image_base:
            raise MachOError(f"Address {address:#x} precedes image base {self.image_base:#x}")
        return address - self.image_base

    def read_virtual(self, address: int, size: int) -> bytes | None:
        if size < 0:
            return None
        offset = self.virtual_to_file(address)
        if offset is None or size > len(self.data) - offset:
            return None
        segment = self.segment_for_va(address)
        if segment is None or address + size > segment.vm_address + segment.file_size:
            return None
        return self.data[offset : offset + size]

    def read_u32(self, address: int) -> int | None:
        raw = self.read_virtual(address, 4)
        return struct.unpack(self.endian + "I", raw)[0] if raw is not None else None

    def read_u64(self, address: int) -> int | None:
        raw = self.read_virtual(address, 8)
        return struct.unpack(self.endian + "Q", raw)[0] if raw is not None else None

    def read_ascii_identifier(self, address: int, maximum: int = 200) -> str | None:
        offset = self.virtual_to_file(address)
        if offset is None:
            return None
        end = min(len(self.data), offset + maximum + 1)
        raw = self.data[offset:end]
        terminator = raw.find(b"\0")
        if terminator < 2 or terminator > maximum:
            return None
        value = raw[:terminator]
        if not ((65 <= value[0] <= 90) or (97 <= value[0] <= 122)):
            return None
        if any(
            not (
                48 <= byte <= 57
                or 65 <= byte <= 90
                or 97 <= byte <= 122
                or byte == 95
            )
            for byte in value
        ):
            return None
        return value.decode("ascii")

    def find_cstrings(self, text: str) -> list[int]:
        needle = text.encode("utf-8")
        hits: list[int] = []
        sections = self.searchable_string_sections or self.sections
        for section in sections:
            if section.is_zero_fill or not section.size:
                continue
            start = section.file_offset
            end = start + section.size
            cursor = start
            while cursor < end:
                found = self.data.find(needle, cursor, end)
                if found < 0:
                    break
                after = found + len(needle)
                before_ok = found == start or self.data[found - 1] == 0
                after_ok = after == end or self.data[after] == 0
                if before_ok and after_ok:
                    address = self.file_to_virtual(found)
                    if address is not None:
                        hits.append(address)
                cursor = found + 1
        return sorted(set(hits))

    def iter_section_words(self, sections: Iterable[MachOSection]) -> Iterable[tuple[int, int]]:
        if self.endian != "<":
            raise MachOError("ARM64 instruction scanning requires little-endian Mach-O")
        for section in sections:
            usable = section.size - (section.size % 4)
            for relative in range(0, usable, 4):
                word = struct.unpack_from("<I", self.data, section.file_offset + relative)[0]
                yield section.address + relative, word

    def identity_dict(self) -> dict[str, object]:
        return {
            "container": self.slice.container,
            "slice_file_offset": self.slice.file_offset,
            "slice_size": self.slice.size,
            "architecture": self.architecture,
            "cpu_type": self.cpu_type,
            "cpu_subtype": self.cpu_subtype,
            "uuid": self.uuid,
            "image_base": self.image_base,
            "encrypted": self.is_encrypted,
            "encryption": [
                {
                    "file_offset": item.file_offset,
                    "size": item.size,
                    "crypt_id": item.crypt_id,
                }
                for item in self.encryption
            ],
            "build_version": (
                {
                    "platform": self.build_version.platform,
                    "minimum_os": self.build_version.minimum_os,
                    "sdk": self.build_version.sdk,
                }
                if self.build_version
                else None
            ),
            "segments": [
                {
                    "name": segment.name,
                    "vm_address": segment.vm_address,
                    "vm_size": segment.vm_size,
                    "file_offset": segment.file_offset,
                    "file_size": segment.file_size,
                    "initial_protection": segment.initial_protection,
                    "sections": [
                        {
                            "segment": section.segment_name,
                            "name": section.name,
                            "address": section.address,
                            "size": section.size,
                            "file_offset": section.file_offset,
                            "flags": section.flags,
                        }
                        for section in segment.sections
                    ],
                }
                for segment in self.segments
            ],
        }
