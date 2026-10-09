from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

from . import __version__
from .ipa import load_input
from .macho import MachOError, MachOImage
from .scanner import Arm64Scanner
from .writers import (
    write_fflags_header,
    write_fflags_map_header,
    write_json,
    write_log,
    write_offsets_header,
)
from .verification import verify_dump


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ROOT.parent
DEFAULT_PATTERNS = PROJECT_ROOT / "patterns.ios.json"
DEFAULT_KNOWN_FFLAGS = ROOT / "output" / "FFlags.hpp"
DEFAULT_KNOWN_JSON_ROOT = (
    PROJECT_ROOT / "Masterstrap" / "app" / "src" / "main" / "assets" / "game_presets"
)


def _load_patterns(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MachOError(f"Unable to read patterns file {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("platform") != "ios":
        raise MachOError("Patterns file must declare platform=ios")
    patterns = payload.get("patterns")
    if not isinstance(patterns, list):
        raise MachOError("Patterns file has no patterns array")
    result: list[dict[str, object]] = []
    names: set[str] = set()
    for item in patterns:
        if not isinstance(item, dict):
            raise MachOError("Each patterns entry must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise MachOError(f"Invalid or duplicate pattern name: {name!r}")
        names.add(name)
        result.append(item)
    return result


def _load_known_flag_names(path: Path | None) -> set[str]:
    if path is None or not path.is_file():
        return set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MachOError(f"Unable to read known FFlags map {path}: {exc}") from exc
    return set(
        re.findall(
            r"\b(?:constexpr|constinit|inline\s+constexpr)\s+"
            r"(?:std::)?(?:u?intptr_t|uintptr_t|auto)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=",
            text,
        )
    )


def _load_known_json_names(root: Path | None) -> set[str]:
    if root is None or not root.is_dir():
        return set()
    result: set[str] = set()
    prefix = re.compile(r"^(?:FFlag|DFFlag|SFFlag|FInt|DFInt|SFInt|FString|DFString|SFString)[A-Za-z0-9_]+$")

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and prefix.fullmatch(key):
                    result.add(key)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for path in root.rglob("*.json"):
        try:
            visit(json.loads(path.read_text(encoding="utf-8", errors="replace")))
        except (OSError, json.JSONDecodeError):
            continue
    return result


def _default_output(loaded_version: str | None, macho_uuid: str | None) -> Path:
    version = loaded_version or "unknown-version"
    identity = macho_uuid or "no-uuid"
    return ROOT / "output" / version / identity


def _version_tag(version: str | None) -> str:
    digits = re.sub(r"[^0-9A-Za-z]", "", version or "Unknown")
    if not digits or digits[0].isdigit() is False:
        digits = "V" + digits
    return digits + "IOS"


def _print_inspection(loaded, image: MachOImage) -> None:
    print(f"source:       {loaded.source_path}")
    print(f"binary:       {loaded.binary_path}")
    print(f"bundle:       {loaded.bundle_identifier or 'unknown'}")
    print(f"version:      {loaded.short_version or 'unknown'} ({loaded.build_version or 'unknown'})")
    print(f"architecture: {image.architecture} ({image.slice.container})")
    print(f"uuid:         {image.uuid or 'missing'}")
    print(f"sha256:       {loaded.sha256}")
    print(f"image base:   0x{image.image_base:X}")
    print(f"encrypted:    {'yes' if image.is_encrypted else 'no'}")
    print("segments:")
    for segment in image.segments:
        rights = "".join(
            [
                "r" if segment.is_readable else "-",
                "w" if segment.is_writable else "-",
                "x" if segment.is_executable else "-",
            ]
        )
        print(
            f"  {segment.name:16} vm=0x{segment.vm_address:X} "
            f"size=0x{segment.vm_size:X} file=0x{segment.file_offset:X}+0x{segment.file_size:X} {rights}"
        )


def _manifest(loaded, image: MachOImage, patterns_path: Path, status: str) -> dict[str, object]:
    return {
        "schema": 1,
        "tool": {
            "name": "masterstrap-roblox-ios-dumper",
            "version": __version__,
            "mode": "debug-evidence",
        },
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": status,
        "input": loaded.bundle_dict(),
        "patterns": str(patterns_path.resolve()),
        "macho": image.identity_dict(),
    }


def run_dump(args: argparse.Namespace) -> int:
    loaded = load_input(args.input, args.member)
    image = MachOImage(loaded.binary, args.arch)
    _print_inspection(loaded, image)
    if image.is_encrypted and not args.allow_encrypted:
        print(
            "ERROR: Mach-O reports cryptid != 0. Static code scanning would produce "
            "unreliable results. Use a lawfully obtained analyzable binary or pass "
            "--allow-encrypted for metadata-only output.",
            file=sys.stderr,
        )
        output = args.output or _default_output(loaded.short_version, image.uuid)
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "manifest.json", _manifest(loaded, image, args.patterns, "encrypted-input"))
        return 2

    patterns = _load_patterns(args.patterns)
    known_names = _load_known_flag_names(args.known_fflags)
    known_names.update(_load_known_json_names(args.known_json_root))
    scanner = Arm64Scanner(image, known_names)
    offsets = scanner.scan_offsets(patterns)
    flags, flag_scan = scanner.scan_fast_flags()

    status = "metadata-only-encrypted" if image.is_encrypted else "complete"
    manifest = _manifest(loaded, image, args.patterns, status)
    manifest["summary"] = {
        "offsets_resolved": sum(item.rva is not None for item in offsets),
        "offsets_missing": sum(item.rva is None for item in offsets),
        "registration_calls": flag_scan.get("registration_calls", 0),
        "fflags_decoded": len(flags),
        "fflags_high_confidence": sum(item.confidence == "high" for item in flags),
        "known_flag_names": len(known_names),
        "offset_candidates": len(scanner.offset_candidates),
    }

    output = args.output or _default_output(loaded.short_version, image.uuid)
    output.mkdir(parents=True, exist_ok=True)
    identity = f"uuid={image.uuid or 'missing'} sha256={loaded.sha256}"
    write_json(output / "manifest.json", manifest)
    write_json(output / "offsets.json", [item.json_dict() for item in offsets])
    write_json(output / "fflags.json", [item.json_dict() for item in flags])
    write_json(output / "flag_scan.json", flag_scan)
    write_json(output / "offset_candidates.json", scanner.offset_candidates)
    write_offsets_header(output / "Offsets.hpp", offsets, identity)
    write_fflags_header(output / "FFlags.hpp", flags, identity)
    write_fflags_map_header(
        output / "fflags_map.hpp",
        flags,
        identity,
        _version_tag(loaded.short_version),
    )
    write_log(output / "dump.log", manifest, offsets, flags, flag_scan)

    print()
    print(f"offsets: {manifest['summary']['offsets_resolved']}/{len(offsets)}")
    print(
        f"FFlags: {len(flags)}/{flag_scan.get('registration_calls', 0)} "
        "registration calls decoded"
    )
    print(f"offset candidates: {len(scanner.offset_candidates)} (not promoted)")
    print(f"output:  {output.resolve()}")

    required_missing = [
        item.name
        for item, pattern in zip(offsets, patterns)
        if bool(pattern.get("required")) and item.rva is None
    ]
    if required_missing:
        print(
            "WARNING: required offsets missing: " + ", ".join(required_missing),
            file=sys.stderr,
        )
        return 3
    return 0


def run_inspect(args: argparse.Namespace) -> int:
    loaded = load_input(args.input, args.member)
    image = MachOImage(loaded.binary, args.arch)
    _print_inspection(loaded, image)
    return 0


def run_verify(args: argparse.Namespace) -> int:
    result = verify_dump(
        args.output,
        input_path=args.input,
        member=args.member,
        patterns_path=args.patterns,
        minimum_flag_coverage=args.min_flag_coverage,
    )
    report_path = args.report or args.output / "verification.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(report_path, result.json_dict())
    print(f"verification: {'PASS' if result.ok else 'FAIL'}")
    print(
        f"offsets: {result.checks.get('offsets_resolved', 0)}/"
        f"{result.checks.get('offsets_total', 0)}"
    )
    print(
        f"FFlags: {result.checks.get('fflags_decoded', 0)}/"
        f"{result.checks.get('registration_calls', 0)} "
        f"coverage={float(result.checks.get('flag_coverage', 0.0)):.2%}"
    )
    visible_warnings = result.warnings if args.verbose else result.warnings[:10]
    for warning in visible_warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if len(visible_warnings) < len(result.warnings):
        print(
            f"WARNING: {len(result.warnings) - len(visible_warnings)} more warnings; "
            "use --verbose or inspect verification.json",
            file=sys.stderr,
        )
    for error in result.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    print(f"report: {report_path.resolve()}")
    return 0 if result.ok else 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="roblox-ios-dumper",
        description="Inspect and statically dump evidence-backed RVAs from a Roblox iOS Mach-O/IPA",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    inspect = subcommands.add_parser("inspect", help="Inspect IPA/Mach-O identity and layout")
    inspect.add_argument("input", type=Path)
    inspect.add_argument("--member", help="Explicit Mach-O member path inside the IPA")
    inspect.add_argument("--arch", choices=("auto", "arm64", "arm64e"), default="auto")
    inspect.set_defaults(handler=run_inspect)

    dump = subcommands.add_parser("dump", help="Dump Offsets/FFlags with evidence reports")
    dump.add_argument("input", type=Path)
    dump.add_argument("--member", help="Explicit Mach-O member path inside the IPA")
    dump.add_argument("--arch", choices=("auto", "arm64", "arm64e"), default="auto")
    dump.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS)
    dump.add_argument(
        "--known-fflags",
        type=Path,
        default=DEFAULT_KNOWN_FFLAGS if DEFAULT_KNOWN_FFLAGS.is_file() else None,
        help="Optional Android/previous-version FFlags.hpp used only to recover full type prefixes",
    )
    dump.add_argument(
        "--known-json-root",
        type=Path,
        default=DEFAULT_KNOWN_JSON_ROOT if DEFAULT_KNOWN_JSON_ROOT.is_dir() else None,
        help="Optional preset JSON directory used to recover authoritative full flag prefixes",
    )
    dump.add_argument("-o", "--output", type=Path)
    dump.add_argument(
        "--allow-encrypted",
        action="store_true",
        help="Emit metadata/evidence for encrypted input; results are not trusted",
    )
    dump.add_argument("--verbose", action="store_true", help="Print verbose output")
    dump.set_defaults(handler=run_dump)

    verify = subcommands.add_parser(
        "verify",
        help="Verify dump identity, segment permissions, evidence, coverage, and headers",
    )
    verify.add_argument("output", type=Path, help="Directory produced by the dump command")
    verify.add_argument("--input", type=Path, help="Reload the IPA/Mach-O and compare UUID/SHA/layout")
    verify.add_argument("--member", help="Explicit Mach-O member path inside the IPA")
    verify.add_argument("--patterns", type=Path, default=DEFAULT_PATTERNS)
    verify.add_argument(
        "--min-flag-coverage",
        type=float,
        default=0.70,
        help="Minimum decoded registration-call ratio (default: 0.70)",
    )
    verify.add_argument("--report", type=Path)
    verify.add_argument("--verbose", action="store_true", help="Print every verification warning")
    verify.set_defaults(handler=run_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except MachOError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
