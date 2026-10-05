#!/usr/bin/env python3
"""Runnable regression check for packaged shader isolation and RBXM decoding."""
import json
from pathlib import Path
import struct
import tempfile
import zipfile

from build_integrated_roblox_ipa import DIAGNOSTIC_ONLY_OFFSETS, generate_native_hook_source, inject_rbxm_script
from verify_ios_android_shader_parity import _rbxm_has_marker, check_ipa


def main() -> None:
    prefix = "Payload/Roblox.app/Frameworks/RobloxLib.framework/"
    model_path = prefix + "content/AnimateDependencies.rbxm"
    host = b"local Module = {}\n" + b"-- original animation module\n" * 3 + b"return Module\n"
    prop = struct.pack("<II", 1, 6) + b"Source\x01" + struct.pack("<I", len(host)) + host
    model = bytes(32) + b"PROP" + struct.pack("<III", 0, len(prop), 0) + prop
    profile_path = "Payload/Roblox.app/MasterstrapShaderProfile.json"
    disabled = json.dumps({"enabled": False, "package": "none", "wetFloor": False}).encode()
    stock = {model_path: model, prefix + "ios/textures/sky/sky512_bk.tex": b"stock sky"}

    def write_archive(path: Path, entries: dict[str, bytes]) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)

    marker = b"MASTERSTRAP_SKYBOX_APPLIED_LOCAL"
    patched, changed = inject_rbxm_script(model, 'warn("MASTERSTRAP_SKYBOX_APPLIED_LOCAL")')
    assert changed and _rbxm_has_marker(patched, marker)
    assert not _rbxm_has_marker(model, marker)
    with tempfile.TemporaryDirectory() as directory:
        original, output = Path(directory) / "input.ipa", Path(directory) / "output.ipa"
        write_archive(original, stock)
        clean = {**stock, profile_path: disabled}
        write_archive(output, clean)
        assert check_ipa(output, True, original)["original_resources_verified"] == 2
        for change in (
            {model_path: patched},
            {prefix + "ios/textures/sky/sky512_bk.tex": b"custom sky"},
            {profile_path: b'{"enabled":true,"package":"cyberpunk","wetFloor":true}'},
            {"Payload/Roblox.app/ClientSettings/ClientAppSettings.json": b'{"FIntRenderShadowHugeRadius":3009}'},
        ):
            write_archive(output, {**clean, **change})
            try:
                check_ipa(output, True, original)
            except AssertionError:
                pass
            else:
                raise AssertionError(f"isolation gate accepted changed resource: {tuple(change)}")
    source = generate_native_hook_source([], [], 0,
        [(name, 0x100) for name in DIAGNOSTIC_ONLY_OFFSETS], {},
        shader_enabled=False, stretch_enabled=False)
    assert "kShaderEnabled = false" in source
    assert "kStretchEnabled = false" in source
    assert "tiny_hook" not in source
    assert "kOffsetsCount = 0" in source
    assert "runtimeHooks=0" in source
    print("PASS: pristine assets accepted; shader payload, sky mutation and active config rejected")


if __name__ == "__main__":
    main()
