"""Run with python tools/check_ios_luau_audit.py; no IPA required."""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent),
                str(Path(__file__).resolve().parents[1]),
                str(Path(__file__).resolve().parents[1] / "roblox")]
from audit_ios_luau import audit
from roblox.tests.fixtures import BASE, TEXT_ADDRESS, TEXT_FILE_OFFSET, build_macho
from roblox_ios_dumper.macho import MachOImage
from roblox_ios_dumper.scanner import Arm64Scanner


image = MachOImage(build_macho(with_function_starts=True))
body_pattern = image.read_virtual(TEXT_ADDRESS + 0x110, 12).hex(" ")
result = Arm64Scanner(image).scan_offsets([
    {"name": "lua_example", "hex_pattern": body_pattern, "offset": -80}
])[0]
assert result.address == TEXT_ADDRESS + 0x100, result

raw = bytearray(build_macho(with_function_starts=True))
leaf = bytes.fromhex("08 a4 45 a9 08 01 09 cb 00 fd 44 d3 c0 03 5f d6")
raw[TEXT_FILE_OFFSET:TEXT_FILE_OFFSET + 16] = leaf
image = MachOImage(bytes(raw))
# Fixture boundaries isolate the synthetic leaf; production uses Mach-O metadata.
image.function_starts = [TEXT_ADDRESS, TEXT_ADDRESS + 16, TEXT_ADDRESS + 0x80]
report = audit(image, [])
assert report["layoutCandidates"] == {"top": 0x58, "base": 0x60, "tvalueSize": 16}
assert report["roles"]["lua_gettop"]["candidates"][0]["rva"] == TEXT_ADDRESS - BASE
assert report["shaderAbiReady"] is False
assert report["roles"]["lua_gettop"]["callSafe"] is False

raw[TEXT_FILE_OFFSET + 0x80:TEXT_FILE_OFFSET + 0x90] = leaf
image = MachOImage(bytes(raw))
image.function_starts = [TEXT_ADDRESS, TEXT_ADDRESS + 16, TEXT_ADDRESS + 0x80, TEXT_ADDRESS + 0x90]
report = audit(image, [])
assert report["roles"]["lua_gettop"]["status"] == "AMBIGUOUS"
assert report["layoutCandidates"] == {}
assert report["shaderAbiReady"] is False
print("PASS: interior pattern resolves to entry; ambiguous APIs never become callable")
