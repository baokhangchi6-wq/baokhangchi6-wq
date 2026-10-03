#!/usr/bin/env python3
"""Verify iOS shader payload matches the checked-in Android package contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import zipfile

from build_integrated_roblox_ipa import (
    ANDROID_SHADER_SELECTOR_BY_PACKAGE,
    LUAU_SHADER_PAYLOAD,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "resources" / "shader" / "android_shader_manifest.json"
SKY_NAMES = tuple(f"sky512_{face}.tex" for face in ("bk", "dn", "ft", "lf", "rt", "up"))


def check_payload() -> dict[str, int]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise AssertionError("shader manifest packages missing")
    if set(packages) != set(ANDROID_SHADER_SELECTOR_BY_PACKAGE):
        raise AssertionError("shader package set differs from Android")
    for package, profile in packages.items():
        sky = profile.get("sky", [])
        if len(sky) < 6 or any(not isinstance(value, str) for value in sky[:6]):
            raise AssertionError(f"{package}: six sky IDs required")
        for value in sky[:6]:
            if value not in LUAU_SHADER_PAYLOAD:
                raise AssertionError(f"{package}: missing sky ID {value}")
        for index in (6, 7):
            if len(sky) > index and sky[index] is not None and str(sky[index]) not in LUAU_SHADER_PAYLOAD:
                raise AssertionError(f"{package}: missing optional sky asset {sky[index]}")
        for section in ("lighting", "atmosphere", "correction", "bloom", "rays"):
            if section not in LUAU_SHADER_PAYLOAD:
                raise AssertionError(f"{package}: missing {section} table")
    for marker in (
        "MASTERSTRAP_SKYBOX_APPLIED_LOCAL",
        "MASTERSTRAP_WETFLOOR_READBACK",
        "MASTERSTRAP_SHADER_APPLIED_LOCAL",
    ):
        if marker not in LUAU_SHADER_PAYLOAD:
            raise AssertionError(f"runtime marker missing: {marker}")
    return {"packages": len(packages), "payload_bytes": len(LUAU_SHADER_PAYLOAD.encode("utf-8"))}


def check_ipa(path: Path) -> dict[str, int]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        sky_members = [
            name for name in names
            if "RobloxLib.framework/ios/textures/sky/" in name and Path(name).name in SKY_NAMES
        ]
        if len(set(Path(name).name for name in sky_members)) != 6:
            raise AssertionError("IPA missing one or more six sky faces")
        if any(not archive.read(name).startswith(b"\xABKTX 11\xBB\r\n\x1A\n") for name in sky_members):
            raise AssertionError("IPA sky asset is not KTX1")
        rbxm_sources = 0
        for name in names:
            if not name.endswith(".rbxm"):
                continue
            if b"MASTERSTRAP_SKYBOX_APPLIED_LOCAL" in archive.read(name):
                rbxm_sources += 1
        if rbxm_sources == 0:
            raise AssertionError("IPA contains no injected shader readback payload")
    return {"sky_faces": 6, "rbxm_payloads": rbxm_sources}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipa", type=Path)
    args = parser.parse_args()
    result = {"payload": check_payload()}
    if args.ipa:
        result["ipa"] = check_ipa(args.ipa)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
