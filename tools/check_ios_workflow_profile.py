"""Run with python tools/check_ios_workflow_profile.py; no IPA required."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_ios_workflow_profile import build_profile


UUID = "63520525-9b34-3506-a0e4-f9824c8dd633"
SHA256 = "a" * 64


def _audit(roles: dict[str, int]) -> dict[str, object]:
    return {
        "schema": 1,
        "uuid": UUID,
        "sourceSha256": SHA256,
        "signatureValidated": False,
        "runtimeBridgeImplemented": False,
        "blockers": [],
        "roles": {
            name: {
                "status": "STRUCTURAL_CANDIDATE",
                "callSafe": False,
                "candidates": [{"rva": rva, "size": 16, "signature": "00", "bodySha256": "b" * 64,
                                "functionBoundary": "LC_FUNCTION_STARTS"}],
            }
            for name, rva in roles.items()
        },
    }


def _write_fixture(root: Path, audit_roles: dict[str, int], offset_rows: list[dict[str, object]]) -> None:
    (root / "workflow").mkdir()
    (root / "manifest.json").write_text(json.dumps({
        "input": {"sha256": SHA256, "short_version": "2.740.123"},
        "macho": {"uuid": UUID, "image_base": 0},
    }), encoding="utf-8")
    (root / "offsets.json").write_text(json.dumps(offset_rows), encoding="utf-8")
    (root / "offset_candidates.json").write_text("[]", encoding="utf-8")
    (root / "workflow" / "luau_audit.json").write_text(json.dumps(_audit(audit_roles)), encoding="utf-8")


def main() -> None:
    base_roles = {
        "lua_gettop": 0x100,
        "lua_settop": 0x104,
        "lua_getfield": 0x108,
        "lua_setfield": 0x10C,
        "lua_type": 0x10E,
        "lua_pushstring": 0x110,
        "lua_tolstring": 0x114,
        "lua_newthread": 0x118,
        "lua_checkstack": 0x11C,
        "lua_xmove": 0x120,
        "luaD_rawrunprotected": 0x124,
        "lua_resume": 0x128,
        "luaD_precall": 0x12C,
        "resume_start": 0x130,
        "resume_finish": 0x134,
        "luaU_undump": 0x138,
    }
    audit_roles = {**base_roles, "lua_pushlstring": 0x144}
    offset_rows = [
        {"name": "luau_load", "rva": 0x138, "confidence": "high", "evidence": ["section:__TEXT,__text"]},
        {"name": "lua_resume", "rva": 0x128, "confidence": "high", "evidence": ["section:__TEXT,__text"]},
        {"name": "game_loaded", "rva": 0x13C, "confidence": "high", "evidence": ["section:__TEXT,__text"]},
        {"name": "scriptcontextresume", "rva": 0x140, "confidence": "high", "evidence": ["section:__TEXT,__text"]},
    ]
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _write_fixture(root, audit_roles, offset_rows)
        profile = build_profile(root, "wrong-version")
        assert profile["version"] == "2.740.123", profile["version"]
        assert profile["shaderAbiReady"] is False
        assert profile["callableRoles"] == {}
        assert profile["roles"]["luau_load"]["status"] == "UNIQUE_EXECUTABLE"
        assert profile["roles"]["luaU_undump"]["status"] == "STRUCTURAL_CANDIDATE"
        assert "compatibleAliases" in profile["roles"]["luau_load"]
        assert "luau_load" not in profile["blockedRoles"]
        assert "lua_pushlstring" in profile["auditedReadOnlyRoles"]
        assert profile["roles"]["lua_pushlstring"]["callSafe"] is False

        audit_path = root / "workflow" / "luau_audit.json"
        forged_audit = _audit(audit_roles)
        forged_audit.update(signatureValidated=True, runtimeBridgeImplemented=True, shaderAbiReady=True)
        audit_path.write_text(json.dumps(forged_audit), encoding="utf-8")
        profile = build_profile(root, "2.999.999")
        assert profile["shaderAbiReady"] is False, "Audit metadata cannot implement native bridge"
        assert profile["runtimeBridgeImplemented"] is False
        assert profile["callableRoles"] == {}

        collision_roles = {**audit_roles, "lua_settop": audit_roles["lua_gettop"]}
        audit_path.write_text(json.dumps(_audit(collision_roles)), encoding="utf-8")
        profile = build_profile(root, "2.999.999")
        assert profile["roles"]["lua_gettop"]["status"] == "ROLE_RVA_COLLISION"
        assert profile["roles"]["lua_settop"]["status"] == "ROLE_RVA_COLLISION"
        assert "lua_gettop" not in profile["auditedReadOnlyRoles"]
        assert profile["roles"]["lua_resume"]["status"] == "STRUCTURAL_CANDIDATE"
        assert profile["roles"]["game_loaded"]["status"] == "UNIQUE_EXECUTABLE"
        header = (root / "workflow" / "profile.hpp").read_text(encoding="utf-8")
        assert "MASTERSTRAP_IOS_ROLE_LUA_GETTOP 0x0 " in header

        mismatch_roles = {**audit_roles, "lua_resume": 0x140}
        audit_path.write_text(json.dumps(_audit(mismatch_roles)), encoding="utf-8")
        profile = build_profile(root, "2.999.999")
        assert profile["roles"]["lua_resume"]["status"] == "AUDIT_DUMP_ROLE_MISMATCH"
        assert "lua_resume" not in profile["auditedReadOnlyRoles"]

        stale_audit = _audit(audit_roles)
        stale_audit["sourceSha256"] = "c" * 64
        audit_path.write_text(json.dumps(stale_audit), encoding="utf-8")
        try:
            build_profile(root, "2.999.999")
        except ValueError:
            pass
        else:
            raise AssertionError("Stale audit identity accepted")

    print("PASS: aliases preserved, collisions isolated, identity bound, callable ABI closed")


if __name__ == "__main__":
    main()
