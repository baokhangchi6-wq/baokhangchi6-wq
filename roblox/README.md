# Masterstrap Roblox iOS static dumper

This directory contains the evidence-backed Mach-O/ARM64 dumper used to produce
version-specific Roblox iOS offsets and FastFlag storage maps without launching
Roblox.

## Dump

Run from this directory:

```powershell
python -m roblox_ios_dumper dump Roblox.ipa
```

The default pattern file is `../patterns.ios.json`. Output is isolated by Roblox
version and Mach-O UUID:

```text
output/<version>/<uuid>/
├── manifest.json
├── Offsets.hpp
├── offsets.json
├── offset_candidates.json
├── FFlags.hpp
├── fflags.json
├── fflags_map.hpp
├── flag_scan.json
└── dump.log
```

`Offsets.hpp` contains only resolved offsets. Relaxed AOB matches are written to
`offset_candidates.json` with `promoted=false`; they must not be compiled into a
runtime hook until a developer confirms their semantics.

`fflags_map.hpp` uses complete typed names such as `FFlagName`, `FIntName`, and
`FStringName`. Prefixes are not removed, so flags with the same suffix cannot
overwrite each other in the generated map.

## Verify

Always reload the original IPA while verifying so UUID, SHA-256, architecture,
image base, segment permissions and generated headers are checked together:

```powershell
python -m roblox_ios_dumper verify `
  output/<version>/<uuid> `
  --input Roblox.ipa
```

Verification checks:

- resolved function RVAs are executable and carry evidence;
- FastFlag RVAs are writable and have direct-store evidence;
- `Offsets.hpp`, `FFlags.hpp`, and `fflags_map.hpp` exactly match their JSON;
- candidate RVAs are executable, evidence-backed, and never auto-promoted;
- registration-call coverage meets the configured threshold;
- the input IPA identity matches the dump manifest.

Static verification proves that an RVA is consistent with the analyzed binary.
It does not prove that changing a flag produces a particular in-game effect.

## Current reference result

For Roblox iOS `2.735.1135`, Mach-O UUID
`293724c9-edb3-312d-a863-2e5a7275f85d`:

- offsets resolved: `95/177`;
- FastFlags decoded: `13,261/15,325` registration calls (`86.53%`);
- full-name map entries: `13,261`;
- verification with the original IPA: `PASS`.

The `LC_FUNCTION_STARTS` parser recovered two additional high-confidence
function boundaries compared with the previous dump:

- `eventsinit = 0x1E40A0`;
- `luaopenbase = 0x17A79C8`.

