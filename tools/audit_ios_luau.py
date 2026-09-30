"""Resolve Luau API evidence from Mach-O; never authorize native calls.

Like Android's loader audit, function discovery and executable ABI validation
are separate. These structural matches do not verify a VM/task boundary,
protected loader, bytecode encoding, or context lifetime.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "roblox"))
from roblox_ios_dumper.arm64 import decode_bl, is_bl
from roblox_ios_dumper.ipa import load_input
from roblox_ios_dumper.macho import MachOImage
from roblox_ios_dumper.scanner import Arm64Scanner, REFERENCE_HEX_PATTERNS


def audit(image: MachOImage, offsets: list[dict]) -> dict:
    if not isinstance(offsets, list) or any(not isinstance(row, dict) for row in offsets):
        raise ValueError("Offsets must be a list of objects")
    if image.is_encrypted or not image.function_starts:
        raise ValueError("Luau audit requires decrypted code and LC_FUNCTION_STARTS")
    scanner = Arm64Scanner(image)
    starts = set(image.function_starts)
    roles: dict[str, dict] = {}

    def body(address: int) -> bytes:
        index = bisect.bisect_right(image.function_starts, address)
        if address not in starts or index == len(image.function_starts):
            return b""
        size = image.function_starts[index] - address
        return image.read_virtual(address, size) or b""

    def instructions(address: int):
        raw = body(address)
        return [(address + i, struct.unpack_from("<I", raw, i)[0])
                for i in range(0, len(raw) - 3, 4)]

    def calls(address: int) -> list[int]:
        return [decode_bl(word, pc) for pc, word in instructions(address) if is_bl(word)]

    def matches(pattern: str) -> list[int]:
        _, hits, _ = scanner.scan_hex_pattern(pattern)
        return sorted({scanner.find_function_start(hit) for hit in hits
                       if scanner.find_function_start(hit) in starts})

    def matches_any(*patterns: str) -> list[int]:
        found: set[int] = set()
        for pattern in patterns:
            found.update(matches(pattern))
        return sorted(found)

    def contains(address: int, *words: int) -> bool:
        return struct.pack("<" + "I" * len(words), *words) in body(address)

    def record(name: str, candidates: list[int], evidence: list[str]) -> int | None:
        candidates = sorted({a for a in candidates if scanner._is_code(a) and body(a)})
        rows = []
        for address in candidates:
            raw = body(address)
            rows.append({"rva": image.to_rva(address), "size": len(raw),
                         "signature": raw[:32].hex(),
                         "bodySha256": hashlib.sha256(raw).hexdigest(),
                         "calls": [image.to_rva(x) for x in calls(address)],
                         "functionBoundary": "LC_FUNCTION_STARTS"})
        roles[name] = {"status": "STRUCTURAL_CANDIDATE" if len(rows) == 1 else
                      "MISSING" if not rows else "AMBIGUOUS",
                      "callSafe": False, "candidates": rows, "evidence": evidence}
        return candidates[0] if len(candidates) == 1 else None

    # A four-instruction leaf: (L->top - L->base) / sizeof(TValue).
    gettops = []
    layout = {}
    for address in matches_any(
        "?? ?? ?? A9 08 01 09 CB 00 FD 44 D3 C0 03 5F D6",
        REFERENCE_HEX_PATTERNS["lua_gettop"],
    ):
        raw = body(address)
        if len(raw) != 16:
            continue
        word = struct.unpack_from("<I", raw)[0]
        # LDP x8,x9,[x0,#imm], unsigned positive offset.
        if word & 0xFFC07FFF != 0xA9402408:
            continue
        disp = (word >> 15) & 0x7F
        if not 0 < disp < 0x40:
            continue
        gettops.append(address)
    gettop = record("lua_gettop", gettops, ["leaf:(top-base)>>4", "TValue-size:16"])
    if gettop:
        word = struct.unpack_from("<I", body(gettop))[0]
        layout = {"top": ((word >> 15) & 0x7F) * 8,
                  "base": ((word >> 15) & 0x7F) * 8 + 8,
                  "tvalueSize": 16}

    top = layout.get("top")
    def top_write(address: int) -> bool:
        return top is not None and any(
            word & 0xFFC003E0 == 0xF9000260 and ((word >> 10) & 0xFFF) * 8 == top
            for _, word in instructions(address))  # STR Xt,[x19,#top]

    settop_candidates = matches_any(
        "48 01 0B CB 08 FD 44 D3 81 02 08 4B 48 D1 21 8B",
        REFERENCE_HEX_PATTERNS["lua_settop"],
    )
    record("lua_settop", [a for a in settop_candidates if top_write(a)
        and contains(a, 0x8B34D148, 0x91004108)
        and contains(a, 0xB81E015F)],
        ["positive-index:grow-and-clear-nil-tags", "negative-index:top+(index+1)*16",
         "stores-derived-top-offset"])

    pushstrings = matches_any(
        "41 01 00 B4 F3 03 01 AA E0 03 01 AA ?? ?? ?? 94 E2 03 00 AA E0 03 14 AA E1 03 13 AA",
        REFERENCE_HEX_PATTERNS["lua_pushstring"],
    )
    pushstring = record("lua_pushstring", [a for a in pushstrings if len(body(a)) <= 0x100
        and sum((w & 0xFC000000) == 0x14000000 for _, w in instructions(a)) == 2],
        ["null-string-path", "length-call-and-two-tail-branches"])
    pushlstrings = []
    direct_pushlstrings = matches(REFERENCE_HEX_PATTERNS["lua_pushlstring"])
    if direct_pushlstrings:
        pushlstrings.extend(direct_pushlstrings)
    interners = []
    if pushstring:
        for pc, w in instructions(pushstring):
            if w & 0xFC000000 != 0x14000000:
                continue
            target = decode_bl(w, pc)
            if target in starts and top_write(target) and contains(target, 0x528000C8, 0xB9000EC8):
                pushlstrings.append(target)
                for cpc, cw in instructions(target):
                    if is_bl(cw) and image.read_virtual(cpc + 4, 4) == struct.pack("<I", 0xF90002C0):
                        interners.append(decode_bl(cw, cpc))
    record("lua_pushlstring", pushlstrings,
        ["tail-callee-of-pushstring", "pushes-string-tag:6", "increments-top:16"])
    interner = record("luaS_newlstr", [a for a in interners if a in starts],
        ["pushlstring-callee-return-stored-as-TString"])

    field_candidates = matches_any(
        "E0 03 14 AA ?? ?? ?? 94 E2 03 00 AA E0 03 13 AA E1 03 14 AA ?? ?? ?? 94 E0 03 00 F9 C8 00 80 52 E8 0F 00 B9",
        REFERENCE_HEX_PATTERNS["lua_getfield"],
        REFERENCE_HEX_PATTERNS["lua_setfield"],
    )
    record("lua_getfield", [a for a in field_candidates if interner in calls(a)
        and top_write(a) and contains(a, 0xF9402E63, 0x910003E2)
        and contains(a, 0xB9400D00)],
        ["shared-TString-interner", "stack-local-key-tag:6", "getter-output:top",
         "pushes-one-TValue-and-returns-tag"])
    record("lua_setfield", [a for a in field_candidates if interner in calls(a)
        and top_write(a) and contains(a, 0xD1004103, 0x910003E2)
        and contains(a, 0xD1004108)],
        ["shared-TString-interner", "stack-local-key-tag:6", "setter-input:top-16", "pops-one-TValue"])

    tolstring_candidates = matches("C8 0E 40 B9 1F 19 00 71 ?? ?? ?? 54")
    record("lua_tolstring", [a for a in tolstring_candidates
        if contains(a, 0xB9401508, 0xF9000268)
        and contains(a, 0xF94002C8, 0x91006100)
        and contains(a, 0xAA0203F3)],
        ["tag:6-existing-string-path", "TString-length:u32+0x14", "TString-data:+0x18",
         "third-argument:size_t-pointer", "lua_tostring-is-a-macro-use-tolstring-with-null-length"])

    # Preserve questionable old dump entries as evidence, never silently bless them.
    dump_issues = []
    for row in offsets:
        rva = row.get("rva")
        if not isinstance(rva, int) or not str(row.get("name", "")).startswith("lua"):
            continue
        addr = image.image_base + rva
        start, source = scanner.resolve_function_start(addr)
        if start != addr:
            dump_issues.append({"role": row["name"], "rva": rva,
                               "issue": "INTERIOR_FUNCTION_ADDRESS",
                               "containingFunctionRva": image.to_rva(start) if start is not None else None,
                               "boundarySource": source})
    return {"schema": 1, "uuid": image.uuid,
            "sha256": hashlib.sha256(image.data).hexdigest(), "layoutCandidates": layout,
            "roles": roles, "dumpIssues": dump_issues,
            "shaderAbiReady": False, "runtimeBridgeImplemented": False,
            "blockers": ["protected-loader-ABI-unverified", "resume-ABI-unverified",
                         "game-VM-task-boundary-unverified", "bytecode-wire-format-unverified",
                         "context-lifetime-unverified", "native-Luau-bridge-not-implemented"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--offsets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    loaded = load_input(args.input)
    report = audit(MachOImage(loaded.binary), json.loads(args.offsets.read_text(encoding="utf-8")))
    report["sourceSha256"] = loaded.sha256
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({name: row["status"] for name, row in report["roles"].items()}))
    print("Luau callable ABI remains blocked; see", args.output)


if __name__ == "__main__":
    main()
