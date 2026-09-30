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

IOS_EXACT_READONLY_ROLES = {
    # UUID-scoped contracts recovered from exact ARM64 disassembly. These are
    # read-only evidence until loader wire format and VM lifetime are proven.
    "luau_load_wrapper": 0x410F3B0,
}


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

    def offset_candidates(*names: str) -> list[int]:
        wanted = set(names)
        found: set[int] = set()
        for row in offsets:
            if not isinstance(row, dict) or row.get("name") not in wanted:
                continue
            rva = row.get("rva")
            if not isinstance(rva, int):
                continue
            address = image.image_base + rva
            start, _source = scanner.resolve_function_start(address)
            if start in starts and body(start):
                found.add(start)
        return sorted(found)

    def called_targets(address: int) -> list[int]:
        return sorted({target for target in calls(address) if target in starts})

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

    # Prefer iOS-specific structural signatures over the historical offset
    # label, which may point at coroutine auxresume rather than public resume.
    resume = record("lua_resume", matches_any(REFERENCE_HEX_PATTERNS["lua_resume"]),
                    ["iOS-resume-prologue", "exact-function-boundary", "public-resume-contract"])
    newthread = record("lua_newthread", matches_any(REFERENCE_HEX_PATTERNS["lua_newthread"]),
                       ["reference-pattern", "exact-function-boundary"])
    record("lua_checkstack", matches_any(REFERENCE_HEX_PATTERNS["lua_checkstack"]),
           ["stack-growth-prologue", "exact-function-boundary", "read-only-ABI-evidence"])
    record("lua_xmove", matches_any(REFERENCE_HEX_PATTERNS["lua_xmove"]),
           ["TValue-copy-prologue", "exact-function-boundary", "read-only-ABI-evidence"])
    record("luaD_rawrunprotected", matches_any(REFERENCE_HEX_PATTERNS["luaD_rawrunprotected"]),
           ["protected-callback-prologue", "exact-function-boundary", "read-only-ABI-evidence"])
    record("lua_type", offset_candidates("lua_type"),
           ["typed-stack-read-offset", "exact-function-boundary", "read-only-ABI-evidence"])
    precall = record("luaD_precall", matches_any(REFERENCE_HEX_PATTERNS["luaD_precall"]),
                     ["reference-pattern", "call-stack-entry"])
    undump_candidates = offset_candidates(
        "luau_load", "luau_bytecodeload", "luau_validateheader", "luau_pushstringerror"
    )
    undump_candidates.extend(matches_any(REFERENCE_HEX_PATTERNS["luaU_undump"],
                                          REFERENCE_HEX_PATTERNS["luau_bytecodeload"]))
    exact_roles = IOS_EXACT_READONLY_ROLES if image.uuid == "63520525-9b34-3506-a0e4-f9824c8dd633" else {}
    record("luau_load_wrapper",
           [image.image_base + exact_roles["luau_load_wrapper"]] if "luau_load_wrapper" in exact_roles else [],
           ["UUID-scoped-protected-loader-wrapper", "exact-function-boundary", "string-object-ABI-still-pending"])
    undump = record("luaU_undump", undump_candidates,
                    ["protected-loader-reference", "exact-function-boundary"])
    execute_dispatch = record(
        "luaV_execute_dispatch",
        matches_any(REFERENCE_HEX_PATTERNS["luaV_execute_dispatch"]),
        ["interpreter-dispatch-reference", "exact-function-boundary"],
    )

    resume_targets = called_targets(resume) if resume else []
    # resume_start and resume_finish are static helpers. Their position is
    # compiler-dependent, so require them to be distinct internal callees of
    # the exact lua_resume body; never guess from a fixed RVA.
    chain_targets = [a for a in resume_targets if a != precall and a != undump]
    start_candidates = [chain_targets[0]] if len(chain_targets) >= 2 else []
    finish_candidates = [chain_targets[-1]] if len(chain_targets) >= 2 else []
    resume_start = record("resume_start", matches_any(REFERENCE_HEX_PATTERNS["resume_start"]),
                          ["unique-internal-callee-of-lua_resume", "resume-boundary"])
    resume_finish = record("resume_finish", matches_any(REFERENCE_HEX_PATTERNS["resume_finish"]),
                           ["unique-internal-callee-of-lua_resume", "post-resume-boundary"])

    required_roles = (
        "lua_gettop", "lua_settop", "lua_type", "lua_pushstring", "lua_tolstring",
        "lua_newthread", "lua_checkstack", "lua_xmove", "luaD_rawrunprotected",
        "lua_resume", "resume_start", "resume_finish", "luaU_undump",
        "luau_load_wrapper",
    )
    unique_roles = all(roles.get(name, {}).get("status") == "STRUCTURAL_CANDIDATE"
                       for name in required_roles)
    distinct_rvas = [roles[name]["candidates"][0]["rva"] for name in required_roles
                     if roles.get(name, {}).get("status") == "STRUCTURAL_CANDIDATE"]
    signature_validated = (
        unique_roles
        and len(distinct_rvas) == len(set(distinct_rvas))
        and len(chain_targets) >= 2
        and resume_start is not None
        and resume_finish is not None
        and resume_start != resume_finish
    )

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
            "signatureEvidence": {
                "requiredRoles": list(required_roles),
                "resumeCallTargets": [image.to_rva(a) for a in chain_targets],
                "resumeChainDistinct": bool(resume_start is not None and resume_finish is not None and resume_start != resume_finish),
                "layout": layout,
            },
            # A callable signature is necessary, but the native bridge and VM
            # lifetime proof are separate gates. Keep shader ABI closed here.
            "shaderAbiReady": False,
            "signatureValidated": bool(signature_validated),
            "runtimeBridgeImplemented": False,
            "blockers": ["protected-loader-ABI-unverified", "resume-ABI-unverified",
                         "game-VM-task-boundary-unverified", "bytecode-wire-format-unverified",
                         "context-lifetime-unverified", "native-Luau-bridge-not-implemented"]
            if not signature_validated else
            ["game-VM-task-boundary-unverified", "context-lifetime-unverified",
             "native-Luau-bridge-not-implemented"]}


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
