#!/usr/bin/env python3
"""Build an exact-binary iOS workflow profile for native runtime consumers.

The dumper intentionally emits candidates that are useful for inspection. This
step is the promotion gate: only unique, executable roles from the same Mach-O
UUID are eligible for read-only runtime inspection. Callable ABI stays blocked.
"""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path


ROLE_ALIASES = {
    # One Luau loader commonly contains validation and error formatting in
    # the same function. These aliases are compatible evidence, not separate
    # callable entry points.
    "luau_load": ("luau_load", "luau_bytecodeload", "luau_validateheader", "luau_pushstringerror"),
    "luau_load_wrapper": ("luau_load_wrapper",),
    "luau_bytecodeload": ("luau_bytecodeload", "luau_load", "luau_validateheader", "luau_pushstringerror"),
    "lua_resume": ("lua_resume",),
    "game_loaded": ("game_loaded", "ongameloaded"),
    "ongameloaded": ("ongameloaded", "game_loaded"),
    "scriptcontextresume": ("scriptcontextresume",),
}
REQUIRED_ROLES = ("luau_load", "lua_resume", "game_loaded", "scriptcontextresume")
AUDIT_REQUIRED_ROLES = (
    "lua_gettop", "lua_settop", "lua_getfield", "lua_setfield",
    "lua_pushstring", "lua_tolstring", "lua_newthread", "lua_checkstack",
    "lua_xmove", "luaD_rawrunprotected", "lua_resume",
    "resume_start", "resume_finish", "luaU_undump",
)


def _load_offsets(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else []


def _role_candidates(offsets: list[dict[str, object]], role: str) -> list[dict[str, object]]:
    names = set(ROLE_ALIASES.get(role, (role,)))
    rows = [row for row in offsets if isinstance(row, dict) and row.get("name") in names
            and type(row.get("rva")) is int and row["rva"] >= 0 and row["rva"] % 4 == 0]
    # Same RVA under two aliases is one evidence point, not two independent hits.
    unique: dict[int, dict[str, object]] = {}
    for row in rows:
        unique.setdefault(int(row["rva"]), row)
    return list(unique.values())


def build_profile(output: Path, version: str) -> dict[str, object]:
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    macho = manifest.get("macho", {}) if isinstance(manifest, dict) else {}
    identity = manifest.get("input", {}) if isinstance(manifest, dict) else {}
    if not isinstance(macho, dict) or not isinstance(identity, dict):
        raise ValueError("Invalid dump identity")
    try:
        uuid.UUID(str(macho.get("uuid", "")))
        source_hash = identity.get("sha256")
        if not isinstance(source_hash, str) or len(source_hash) != 64 or len(bytes.fromhex(source_hash)) != 32:
            raise ValueError("Invalid SHA256")
    except (ValueError, TypeError) as exc:
        raise ValueError("Dump UUID/SHA256 required") from exc
    version = identity.get("short_version") or identity.get("build_version") or version
    if not isinstance(version, str) or not version or any(char not in "0123456789." for char in version):
        raise ValueError("Invalid IPA version")
    offsets = _load_offsets(output / "offsets.json")
    names_by_rva: dict[int, set[str]] = {}
    for row in offsets:
        if (isinstance(row, dict) and type(row.get("rva")) is int and row["rva"] >= 0
                and row["rva"] % 4 == 0 and isinstance(row.get("name"), str)):
            names_by_rva.setdefault(int(row["rva"]), set()).add(str(row["name"]))
    candidates = _load_offsets(output / "offset_candidates.json")
    audit_path = output / "workflow" / "luau_audit.json"
    luau_audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.is_file() else None
    if luau_audit is not None:
        if (not isinstance(luau_audit, dict) or luau_audit.get("schema") != 1
                or luau_audit.get("uuid") != macho["uuid"]
                or luau_audit.get("sourceSha256") != identity["sha256"]):
            raise ValueError("Luau audit does not match dump UUID/SHA256")
    roles: dict[str, dict[str, object]] = {}
    blocked: list[str] = []
    for role in REQUIRED_ROLES:
        rows = _role_candidates(offsets, role)
        if len(rows) != 1:
            blocked.append(role)
            roles[role] = {"status": "AMBIGUOUS_OR_MISSING", "candidates": [row.get("rva") for row in rows]}
            continue
        row = rows[0]
        aliases = names_by_rva.get(int(row["rva"]), set())
        allowed_aliases = set(ROLE_ALIASES.get(role, (role,)))
        unexpected_aliases = sorted(aliases - allowed_aliases)
        if unexpected_aliases:
            blocked.append(role)
            roles[role] = {
                "status": "RVA_ROLE_COLLISION",
                "rva": row["rva"],
                "aliases": sorted(aliases),
                "unexpectedAliases": unexpected_aliases,
            }
            continue
        confidence = str(row.get("confidence", ""))
        executable = "__TEXT" in " ".join(str(x) for x in row.get("evidence", []))
        if confidence not in {"high", "medium"} or not executable:
            blocked.append(role)
            roles[role] = {"status": "WEAK_EVIDENCE", "rva": row.get("rva"), "confidence": confidence}
            continue
        roles[role] = {"status": "UNIQUE_EXECUTABLE", "rva": row["rva"], "confidence": confidence,
                       "callSafe": False}

    audit_roles = luau_audit.get("roles", {}) if isinstance(luau_audit, dict) else {}
    if not isinstance(audit_roles, dict) or any(
            not isinstance(role, str) or not role.isidentifier() for role in audit_roles):
        raise ValueError("Invalid Luau audit roles")
    for role in dict.fromkeys((*AUDIT_REQUIRED_ROLES, *audit_roles)):
        audited = audit_roles.get(role, {}) if isinstance(audit_roles, dict) else {}
        candidates_for_role = audited.get("candidates", []) if isinstance(audited, dict) else []
        if (not isinstance(audited, dict) or not isinstance(candidates_for_role, list)
                or audited.get("status") != "STRUCTURAL_CANDIDATE" or len(candidates_for_role) != 1):
            if role in AUDIT_REQUIRED_ROLES:
                blocked.append(role)
            roles[role] = {
                "status": str(audited.get("status") or "AUDIT_MISSING") if isinstance(audited, dict) else "AUDIT_INVALID",
                "candidates": [row.get("rva") for row in candidates_for_role if isinstance(row, dict)]
                              if isinstance(candidates_for_role, list) else [],
            }
            continue
        candidate = candidates_for_role[0]
        rva = candidate.get("rva") if isinstance(candidate, dict) else None
        if type(rva) is not int or rva < 0 or rva % 4:
            if role in AUDIT_REQUIRED_ROLES:
                blocked.append(role)
            roles[role] = {"status": "AUDIT_INVALID_RVA"}
            continue
        dumped_role = roles.get(role, {})
        if dumped_role.get("status") == "UNIQUE_EXECUTABLE" and dumped_role.get("rva") != rva:
            blocked.append(role)
            roles[role] = {"status": "AUDIT_DUMP_ROLE_MISMATCH", "dumpRva": dumped_role["rva"],
                           "auditRva": rva, "callSafe": False}
            continue
        roles[role] = {
            "status": "STRUCTURAL_CANDIDATE",
            "rva": rva,
            "confidence": "audit-structural",
            "signature": candidate.get("signature"),
            "bodySha256": candidate.get("bodySha256"),
            "size": candidate.get("size"),
            "functionBoundary": candidate.get("functionBoundary"),
            "callSafe": False,
        }

    read_only_statuses = {"UNIQUE_EXECUTABLE", "STRUCTURAL_CANDIDATE"}
    roles_by_rva: dict[int, list[str]] = {}
    for role, item in roles.items():
        if item.get("status") in read_only_statuses:
            roles_by_rva.setdefault(int(item["rva"]), []).append(role)
    loader_aliases = {*ROLE_ALIASES["luau_load"], "luaU_undump"}
    for shared_roles in roles_by_rva.values():
        if len(shared_roles) > 1:
            if set(shared_roles) <= loader_aliases:
                for role in shared_roles:
                    roles[role]["compatibleAliases"] = shared_roles
                continue
            for role in shared_roles:
                blocked.append(role)
                item = roles[role]
                item["status"] = "ROLE_RVA_COLLISION"
                item["collidingRoles"] = shared_roles

    audit_signature = bool(isinstance(luau_audit, dict) and luau_audit.get("signatureValidated") is True)
    # ponytail: discovery proves no callable bridge; add a verified bridge contract before promotion.
    audit_bridge = False
    shader_ready = False
    audit_blockers = []
    if not audit_signature:
        audit_blockers.append("luau-signature-or-resume-chain-unverified")
    if not audit_bridge:
        audit_blockers.append("native-luau-bridge-not-implemented")
    blocked.extend(item for item in audit_blockers if item not in blocked)
    if isinstance(luau_audit, dict):
        blocked.extend(item for item in luau_audit.get("blockers", []) if isinstance(item, str))
    blocked = list(dict.fromkeys(blocked))

    required_blocked = set(REQUIRED_ROLES) | set(AUDIT_REQUIRED_ROLES)
    profile = {
        "schema": 1,
        "platform": "ios-arm64",
        "version": version,
        "uuid": macho.get("uuid"),
        "sha256": identity.get("sha256"),
        "imageBase": macho.get("image_base"),
        "roles": roles,
        "auditedReadOnlyRoles": {role: item for role, item in roles.items()
                                if item.get("status") in read_only_statuses},
        "callableRoles": {},
        "candidateCount": len(candidates),
        "luauDiscovery": luau_audit,
        # Audit must prove exact function boundaries and the resume chain
        # before native shader payloads can cross into Luau.
        "shaderAbiReady": shader_ready,
        "signatureValidated": audit_signature,
        "runtimeBridgeImplemented": audit_bridge,
        "blockedRoles": blocked,
        "promotion": "awaiting-runtime-bridge" if audit_signature and not any(
            item in required_blocked for item in blocked) else "fail-closed",
    }
    workflow = output / "workflow"
    workflow.mkdir(parents=True, exist_ok=True)
    (workflow / "profile.json").write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    uuid_text = str(profile.get("uuid") or "")
    compact_uuid = uuid_text.replace("-", "")
    uuid_bytes = [f"0x{int(compact_uuid[index:index + 2], 16):02x}" for index in range(0, 32, 2)] if len(compact_uuid) == 32 else []
    header = [
        "// Generated by tools/prepare_ios_workflow_profile.py; exact IPA only.",
        "#pragma once",
        "#include <stdint.h>",
        f"#define MASTERSTRAP_IOS_WORKFLOW_VERSION \"{version}\"",
        f"#define MASTERSTRAP_IOS_WORKFLOW_UUID \"{uuid_text}\"",
        f"#define MASTERSTRAP_IOS_SHADER_ABI_READY {int(profile['shaderAbiReady'])}",
        f"static const uint8_t kMasterstrapIosWorkflowUuid[16] = {{{', '.join(uuid_bytes or ['0'] * 16)}}};",
    ]
    for role in dict.fromkeys((*REQUIRED_ROLES, *AUDIT_REQUIRED_ROLES, *audit_roles)):
        item = roles[role]
        rva = item.get("rva", 0) if item.get("status") in read_only_statuses else 0
        header.append(f"#define MASTERSTRAP_IOS_ROLE_{role.upper()} 0x{int(rva):X} // read-only; not callable")
    (workflow / "profile.hpp").write_text("\n".join(header) + "\n", encoding="utf-8")
    return profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    profile = build_profile(args.output.resolve(), args.version)
    print(json.dumps({"shaderAbiReady": profile["shaderAbiReady"], "blockedRoles": profile["blockedRoles"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
