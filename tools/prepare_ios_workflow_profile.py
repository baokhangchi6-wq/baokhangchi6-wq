#!/usr/bin/env python3
"""Build an exact-binary iOS workflow profile for native runtime consumers.

The dumper intentionally emits candidates that are useful for inspection. This
step is the promotion gate: only unique, executable roles from the same Mach-O
UUID are eligible for a runtime profile. Ambiguous Lua roles stay blocked.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ROLE_ALIASES = {
    "luau_load": ("luau_load", "luau_bytecodeload"),
    "luau_bytecodeload": ("luau_bytecodeload", "luau_load"),
    "lua_resume": ("lua_resume",),
    "game_loaded": ("game_loaded", "ongameloaded"),
    "ongameloaded": ("ongameloaded", "game_loaded"),
    "scriptcontextresume": ("scriptcontextresume",),
}
REQUIRED_ROLES = ("luau_load", "lua_resume", "game_loaded", "scriptcontextresume")


def _load_offsets(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else []


def _role_candidates(offsets: list[dict[str, object]], role: str) -> list[dict[str, object]]:
    names = set(ROLE_ALIASES.get(role, (role,)))
    rows = [row for row in offsets if isinstance(row, dict) and row.get("name") in names and isinstance(row.get("rva"), int)]
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
    offsets = _load_offsets(output / "offsets.json")
    names_by_rva: dict[int, set[str]] = {}
    for row in offsets:
        if isinstance(row, dict) and isinstance(row.get("rva"), int) and isinstance(row.get("name"), str):
            names_by_rva.setdefault(int(row["rva"]), set()).add(str(row["name"]))
    candidates = _load_offsets(output / "offset_candidates.json")
    audit_path = output / "workflow" / "luau_audit.json"
    luau_audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.is_file() else None
    if luau_audit is not None:
        if (not isinstance(luau_audit, dict) or not macho.get("uuid") or not identity.get("sha256")
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
        roles[role] = {"status": "UNIQUE_EXECUTABLE", "rva": row["rva"], "confidence": confidence}

    promoted_rvas = [int(item["rva"]) for item in roles.values() if item.get("status") == "UNIQUE_EXECUTABLE"]
    if len(promoted_rvas) != len(set(promoted_rvas)):
        blocked.extend(role for role, item in roles.items() if item.get("status") == "UNIQUE_EXECUTABLE")
        for role, item in roles.items():
            if item.get("status") == "UNIQUE_EXECUTABLE":
                item["status"] = "ROLE_RVA_COLLISION"

    profile = {
        "schema": 1,
        "platform": "ios-arm64",
        "version": version,
        "uuid": macho.get("uuid"),
        "sha256": identity.get("sha256"),
        "imageBase": macho.get("image_base"),
        "roles": roles,
        "candidateCount": len(candidates),
        "luauDiscovery": luau_audit,
        # Role uniqueness is necessary, but not sufficient for a callable
        # Luau ABI. Signature/call-graph audit remains a separate promotion.
        "shaderAbiReady": False,
        "signatureValidated": False,
        "blockedRoles": blocked,
        "promotion": "awaiting-signature-audit" if not blocked else "fail-closed",
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
    for role in REQUIRED_ROLES:
        item = roles[role]
        header.append(f"#define MASTERSTRAP_IOS_ROLE_{role.upper()} 0x{int(item.get('rva', 0)):X}")
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
