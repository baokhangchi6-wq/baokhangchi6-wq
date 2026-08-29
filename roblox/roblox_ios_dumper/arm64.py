from __future__ import annotations

from dataclasses import dataclass


def is_adrp(instruction: int) -> bool:
    return (instruction & 0x9F000000) == 0x90000000


def is_adr(instruction: int) -> bool:
    return (instruction & 0x9F000000) == 0x10000000


def is_add_immediate(instruction: int) -> bool:
    return (instruction & 0xFF000000) == 0x91000000


def is_ldr_unsigned_64(instruction: int) -> bool:
    return (instruction & 0xFFC00000) == 0xF9400000


def is_ldr_literal_64(instruction: int) -> bool:
    return (instruction & 0xFF000000) == 0x58000000


def is_bl(instruction: int) -> bool:
    return (instruction & 0xFC000000) == 0x94000000


def destination_register(instruction: int) -> int:
    return instruction & 0x1F


def base_register(instruction: int) -> int:
    return (instruction >> 5) & 0x1F


def store_kind(instruction: int) -> str | None:
    masked = instruction & 0xFFC00000
    if masked == 0x39000000:
        return "bool"
    # Newer iOS FastFlag registration sites also initialize the compact flag
    # state with STRH. It is still a boolean family registration; treating it
    # as an unknown store makes the scanner accidentally reuse the preceding
    # flag's byte storage.
    if masked == 0x79000000:
        return "bool16"
    if masked == 0xB9000000:
        return "int32"
    if masked == 0xF9000000:
        return "pointer64"
    return None


def decode_adrp(instruction: int, pc: int) -> int:
    imm_hi = (instruction >> 5) & 0x7FFFF
    imm_low = (instruction >> 29) & 0x3
    immediate = (imm_hi << 2) | imm_low
    if immediate & (1 << 20):
        immediate -= 1 << 21
    return (pc & ~0xFFF) + (immediate << 12)


def decode_adr(instruction: int, pc: int) -> int:
    imm_hi = (instruction >> 5) & 0x7FFFF
    imm_low = (instruction >> 29) & 0x3
    immediate = (imm_hi << 2) | imm_low
    if immediate & (1 << 20):
        immediate -= 1 << 21
    return pc + immediate


def decode_add_immediate(instruction: int) -> int:
    immediate = (instruction >> 10) & 0xFFF
    return immediate << 12 if ((instruction >> 22) & 1) else immediate


def decode_ldr_unsigned_64(instruction: int) -> int:
    return ((instruction >> 10) & 0xFFF) << 3


def decode_ldr_literal_64(instruction: int, pc: int) -> int:
    immediate = (instruction >> 5) & 0x7FFFF
    if immediate & (1 << 18):
        immediate -= 1 << 19
    return pc + (immediate << 2)


def decode_bl(instruction: int, pc: int) -> int:
    immediate = instruction & 0x03FFFFFF
    if immediate & (1 << 25):
        immediate -= 1 << 26
    return pc + (immediate << 2)


def is_probable_function_prologue(instruction: int) -> bool:
    # STP pair using pre-indexed SP, SUB SP,SP,#imm, or PACIASP.
    return (
        (instruction & 0xFFC003E0) == 0xA98003E0
        or (instruction & 0xFF8003FF) == 0xD10003FF
        or instruction in {0xD503233F, 0xD503237F}
    )


@dataclass(frozen=True)
class AddressReference:
    instruction_address: int
    target_address: int
    register: int
    kind: str
    instruction_count: int


def decode_address_reference(
    pc: int, first: int, second: int | None = None
) -> AddressReference | None:
    if is_adr(first):
        return AddressReference(
            instruction_address=pc,
            target_address=decode_adr(first, pc),
            register=destination_register(first),
            kind="adr",
            instruction_count=1,
        )
    if is_ldr_literal_64(first):
        return AddressReference(
            instruction_address=pc,
            target_address=decode_ldr_literal_64(first, pc),
            register=destination_register(first),
            kind="ldr-literal-slot",
            instruction_count=1,
        )
    if not is_adrp(first) or second is None:
        return None
    register = destination_register(first)
    if is_add_immediate(second) and base_register(second) == register:
        return AddressReference(
            instruction_address=pc,
            target_address=decode_adrp(first, pc) + decode_add_immediate(second),
            register=destination_register(second),
            kind="adrp+add",
            instruction_count=2,
        )
    if is_ldr_unsigned_64(second) and base_register(second) == register:
        return AddressReference(
            instruction_address=pc,
            target_address=decode_adrp(first, pc) + decode_ldr_unsigned_64(second),
            register=destination_register(second),
            kind="adrp+ldr-slot",
            instruction_count=2,
        )
    return None
