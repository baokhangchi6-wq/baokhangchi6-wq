#!/usr/bin/env python3
"""Verify iOS shader payload matches the checked-in Android package contract."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import zipfile

from build_integrated_roblox_ipa import (
    ANDROID_SHADER_SELECTOR_BY_PACKAGE,
    LUAU_SHADER_PAYLOAD,
    lz4_decompress_block,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "resources" / "shader" / "android_shader_manifest.json"
SKY_NAMES = tuple(f"sky512_{face}.tex" for face in ("bk", "dn", "ft", "lf", "rt", "up"))


def check_payload(expect_disabled: bool = False) -> dict[str, int | str]:
    if expect_disabled:
        if "MASTERSTRAP_SKYBOX_APPLIED_LOCAL" not in LUAU_SHADER_PAYLOAD:
            raise AssertionError("shader payload template markers missing")
        return {"shader": "disabled", "payload_template_bytes": len(LUAU_SHADER_PAYLOAD.encode("utf-8"))}
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


def _rbxm_has_marker(data: bytes, marker: bytes) -> bool:
    """Read RBXM PROP chunks; Source payload is LZ4-compressed in shipped files."""
    pos = 32
    while pos + 16 <= len(data):
        name = data[pos:pos + 4]
        compressed_len, uncompressed_len, _reserved = struct.unpack(
            "<III", data[pos + 4:pos + 16]
        )
        payload_len = compressed_len or uncompressed_len
        end = pos + 16 + payload_len
        if end > len(data):
            return False
        if name == b"PROP":
            payload = data[pos + 16:end]
            try:
                raw = (
                    lz4_decompress_block(payload, uncompressed_len)
                    if compressed_len else payload
                )
            except ValueError:
                raw = b""
            if marker in raw:
                return True
        pos = end
        if name == b"END\x00":
            break
    return False


def check_ipa(path: Path, expect_disabled: bool = False, input_ipa: Path | None = None) -> dict[str, int | str]:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        sky_members = [
            name for name in names
            if "RobloxLib.framework/ios/textures/sky/" in name and Path(name).name in SKY_NAMES
        ]
        if expect_disabled:
            if input_ipa is None:
                raise AssertionError("original input IPA required to verify crash isolation")
            marker = b"MASTERSTRAP_SKYBOX_APPLIED_LOCAL"
            if any(_rbxm_has_marker(archive.read(name), marker) for name in names if name.endswith(".rbxm")):
                raise AssertionError("disabled IPA contains shader readback payload")
            with zipfile.ZipFile(input_ipa) as original:
                resources = [name for name in original.namelist() if "RobloxLib.framework/" in name
                             and (name.endswith(".rbxm") or "/ios/textures/sky/" in name and Path(name).name in SKY_NAMES)]
                for name in resources:
                    if name not in names or archive.read(name) != original.read(name):
                        raise AssertionError(f"crash isolation changed original resource: {name}")
            profiles = [name for name in names if name.endswith("/MasterstrapShaderProfile.json")]
            if not profiles:
                raise AssertionError("disabled shader profile missing")
            for name in profiles:
                profile = json.loads(archive.read(name))
                if profile.get("enabled") is not False or profile.get("package") != "none" or profile.get("wetFloor") is not False:
                    raise AssertionError(f"shader profile still active: {name}")
            for name in names:
                if name.endswith("/ClientAppSettings.json") and "FIntRenderShadowHugeRadius" in json.loads(archive.read(name)):
                    raise AssertionError(f"shader selector remains in config: {name}")
            return {"shader": "disabled", "original_resources_verified": len(resources), "rbxm_payloads": 0}
        if len(set(Path(name).name for name in sky_members)) != 6:
            raise AssertionError("IPA missing one or more six sky faces")
        if any(not archive.read(name).startswith(b"\xABKTX 11\xBB\r\n\x1A\n") for name in sky_members):
            raise AssertionError("IPA sky asset is not KTX1")
        rbxm_sources = 0
        marker = b"MASTERSTRAP_SKYBOX_APPLIED_LOCAL"
        for name in names:
            if not name.endswith(".rbxm"):
                continue
            if _rbxm_has_marker(archive.read(name), marker):
                rbxm_sources += 1
        if rbxm_sources == 0:
            raise AssertionError("IPA contains no injected shader readback payload")
    return {"sky_faces": 6, "rbxm_payloads": rbxm_sources}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ipa", type=Path)
    parser.add_argument("--expect-disabled", action="store_true")
    parser.add_argument("--input-ipa", type=Path)
    args = parser.parse_args()
    result = {"payload": check_payload(expect_disabled=args.expect_disabled)}
    if args.ipa:
        result["ipa"] = check_ipa(args.ipa, expect_disabled=args.expect_disabled, input_ipa=args.input_ipa)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
