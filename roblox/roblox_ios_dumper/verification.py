from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .ipa import load_input
from .macho import MachOError, MachOImage


VALID_PREFIXES = {
    "FFlag",
    "DFFlag",
    "SFFlag",
    "FInt",
    "DFInt",
    "SFInt",
    "FString",
    "DFString",
    "SFString",
}


@dataclass
class VerificationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def json_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": self.checks,
        }


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MachOError(f"Unable to read verification input {path}: {exc}") from exc


def _typed_prefix(name: str) -> str | None:
    for prefix in sorted(VALID_PREFIXES, key=len, reverse=True):
        if name.startswith(prefix) and len(name) > len(prefix):
            return prefix
    return None


def _segment_for_rva(
    segments: list[dict[str, object]], image_base: int, rva: int
) -> dict[str, object] | None:
    address = image_base + rva
    for segment in segments:
        start = segment.get("vm_address")
        size = segment.get("vm_size")
        if isinstance(start, int) and isinstance(size, int) and start <= address < start + size:
            return segment
    return None


def _header_values(path: Path) -> dict[str, int]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MachOError(f"Unable to read generated header {path}: {exc}") from exc
    return {
        name: int(value, 16)
        for name, value in re.findall(
            r"inline\s+constexpr\s+std::uintptr_t\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*0x([0-9A-Fa-f]+)",
            text,
        )
    }


def _map_values(path: Path) -> tuple[dict[str, int], list[str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise MachOError(f"Unable to read generated map {path}: {exc}") from exc
    values: dict[str, int] = {}
    duplicates: list[str] = []
    for name, value in re.findall(
        r'\{\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*,\s*0x([0-9A-Fa-f]+)\s*,' ,
        text,
    ):
        if name in values:
            duplicates.append(name)
        values[name] = int(value, 16)
    return values, duplicates


def _cpp_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def verify_dump(
    output: Path,
    *,
    input_path: Path | None = None,
    member: str | None = None,
    patterns_path: Path | None = None,
    minimum_flag_coverage: float = 0.70,
) -> VerificationResult:
    output = output.resolve()
    result = VerificationResult()
    required_files = {
        "manifest": output / "manifest.json",
        "offsets": output / "offsets.json",
        "fflags": output / "fflags.json",
        "flag_scan": output / "flag_scan.json",
        "offset_candidates": output / "offset_candidates.json",
        "offset_header": output / "Offsets.hpp",
        "fflag_header": output / "FFlags.hpp",
        "fflag_map": output / "fflags_map.hpp",
    }
    missing = [str(path) for path in required_files.values() if not path.is_file()]
    if missing:
        result.errors.extend(f"missing-file:{path}" for path in missing)
        return result

    manifest = _read_json(required_files["manifest"])
    offsets = _read_json(required_files["offsets"])
    flags = _read_json(required_files["fflags"])
    flag_scan = _read_json(required_files["flag_scan"])
    candidates = _read_json(required_files["offset_candidates"])
    if not isinstance(manifest, dict):
        result.errors.append("manifest:not-object")
        return result
    if (
        not isinstance(offsets, list)
        or not isinstance(flags, list)
        or not isinstance(flag_scan, dict)
        or not isinstance(candidates, list)
    ):
        result.errors.append("reports:invalid-json-shape")
        return result

    macho = manifest.get("macho")
    input_identity = manifest.get("input")
    if not isinstance(macho, dict) or not isinstance(input_identity, dict):
        result.errors.append("manifest:missing-identity")
        return result
    image_base = macho.get("image_base")
    segments = macho.get("segments")
    if not isinstance(image_base, int) or not isinstance(segments, list):
        result.errors.append("manifest:missing-layout")
        return result
    typed_segments = [item for item in segments if isinstance(item, dict)]

    if bool(macho.get("encrypted")):
        result.errors.append("input:encrypted-code-is-not-statically-verifiable")
    if manifest.get("status") != "complete":
        result.errors.append(f"manifest:status={manifest.get('status')}")

    offset_names: set[str] = set()
    resolved_offsets = 0
    for item in offsets:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            result.errors.append("offset:invalid-entry")
            continue
        name = str(item["name"])
        if name in offset_names:
            result.errors.append(f"offset:{name}:duplicate-name")
        offset_names.add(name)
        rva = item.get("rva")
        if rva is None:
            continue
        if not isinstance(rva, int) or rva < 0:
            result.errors.append(f"offset:{name}:invalid-rva")
            continue
        resolved_offsets += 1
        segment = _segment_for_rva(typed_segments, image_base, rva)
        protection = segment.get("initial_protection", 0) if segment else 0
        if segment is None or not isinstance(protection, int) or not protection & 4:
            result.errors.append(f"offset:{name}:rva-not-executable")
        address = item.get("address")
        if isinstance(address, int) and address != image_base + rva:
            result.errors.append(f"offset:{name}:address-rva-mismatch")
        if item.get("confidence") not in {"medium", "high"}:
            result.warnings.append(f"offset:{name}:weak-confidence")
        if not item.get("evidence"):
            result.errors.append(f"offset:{name}:missing-evidence")

    if patterns_path is not None and patterns_path.is_file():
        patterns_payload = _read_json(patterns_path)
        patterns = patterns_payload.get("patterns") if isinstance(patterns_payload, dict) else None
        if isinstance(patterns, list):
            required = {
                str(item.get("name"))
                for item in patterns
                if isinstance(item, dict) and bool(item.get("required"))
            }
            unresolved = sorted(required.difference(
                str(item.get("name"))
                for item in offsets
                if isinstance(item, dict) and isinstance(item.get("rva"), int)
            ))
            result.errors.extend(f"offset:{name}:required-missing" for name in unresolved)

    for item in candidates:
        if not isinstance(item, dict):
            result.errors.append("offset-candidate:invalid-entry")
            continue
        name = item.get("name")
        rva = item.get("rva")
        address = item.get("address")
        score = item.get("score")
        if not isinstance(name, str) or not isinstance(rva, int) or rva < 0:
            result.errors.append("offset-candidate:invalid-name-or-rva")
            continue
        if not isinstance(address, int) or address != image_base + rva:
            result.errors.append(f"offset-candidate:{name}:address-rva-mismatch")
        segment = _segment_for_rva(typed_segments, image_base, rva)
        protection = segment.get("initial_protection", 0) if segment else 0
        if segment is None or not isinstance(protection, int) or not protection & 4:
            result.errors.append(f"offset-candidate:{name}:rva-not-executable")
        if not isinstance(score, (int, float)) or not 0.0 <= float(score) <= 1.0:
            result.errors.append(f"offset-candidate:{name}:invalid-score")
        if bool(item.get("promoted")):
            result.errors.append(f"offset-candidate:{name}:unexpectedly-promoted")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            result.errors.append(f"offset-candidate:{name}:missing-evidence")

    flag_names: set[str] = set()
    flag_addresses: dict[int, str] = {}
    for item in flags:
        if not isinstance(item, dict):
            result.errors.append("fflag:invalid-entry")
            continue
        name = item.get("name")
        rva = item.get("rva")
        if not isinstance(name, str) or not isinstance(rva, int) or rva < 0:
            result.errors.append("fflag:invalid-name-or-rva")
            continue
        prefix = _typed_prefix(name)
        if prefix is None or item.get("prefix") != prefix:
            result.errors.append(f"fflag:{name}:invalid-type-prefix")
        if name in flag_names:
            result.errors.append(f"fflag:{name}:duplicate-name")
        flag_names.add(name)
        segment = _segment_for_rva(typed_segments, image_base, rva)
        protection = segment.get("initial_protection", 0) if segment else 0
        if segment is None or not isinstance(protection, int) or not protection & 2:
            result.errors.append(f"fflag:{name}:rva-not-writable")
        if not bool(item.get("writable")):
            result.errors.append(f"fflag:{name}:scanner-did-not-mark-writable")
        address = item.get("address")
        if isinstance(address, int) and address != image_base + rva:
            result.errors.append(f"fflag:{name}:address-rva-mismatch")
        previous = flag_addresses.setdefault(rva, name)
        if previous != name:
            result.warnings.append(f"fflag:{name}:shares-storage-with:{previous}")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not any(
            isinstance(value, str) and value.startswith("storage-write:") for value in evidence
        ):
            result.errors.append(f"fflag:{name}:missing-direct-store-evidence")

    registration_calls = flag_scan.get("registration_calls", 0)
    decoded_calls = flag_scan.get("decoded_calls", len(flags))
    if not isinstance(registration_calls, int) or not isinstance(decoded_calls, int):
        result.errors.append("fflag:invalid-scan-counts")
        coverage = 0.0
    else:
        coverage = decoded_calls / registration_calls if registration_calls else 0.0
        if registration_calls <= 0:
            result.errors.append("fflag:no-registration-calls")
        elif coverage < minimum_flag_coverage:
            result.errors.append(
                f"fflag:coverage={coverage:.4f}-below-{minimum_flag_coverage:.4f}"
            )

    offset_header = _header_values(required_files["offset_header"])
    flag_header = _header_values(required_files["fflag_header"])
    flag_map, flag_map_duplicates = _map_values(required_files["fflag_map"])
    expected_offsets = {
        _cpp_identifier(str(item["name"])): int(item["rva"])
        for item in offsets
        if isinstance(item, dict) and isinstance(item.get("rva"), int)
    }
    expected_flags = {
        _cpp_identifier(str(item["name"])): int(item["rva"])
        for item in flags
        if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("rva"), int)
    }
    if offset_header != expected_offsets:
        result.errors.append("Offsets.hpp:does-not-match-offsets.json")
    if flag_header != expected_flags:
        result.errors.append("FFlags.hpp:does-not-match-fflags.json")
    if flag_map_duplicates:
        result.errors.extend(
            f"fflags_map.hpp:duplicate-name:{name}" for name in sorted(set(flag_map_duplicates))
        )
    if flag_map != {
        str(item["name"]): int(item["rva"])
        for item in flags
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("rva"), int)
    }:
        result.errors.append("fflags_map.hpp:does-not-match-fflags.json")

    if input_path is not None:
        manifest_member = input_identity.get("binary_path")
        selected_member = member or (manifest_member if input_path.suffix.lower() in {".ipa", ".zip"} else None)
        loaded = load_input(input_path, selected_member if isinstance(selected_member, str) else None)
        image = MachOImage(loaded.binary, str(macho.get("architecture", "auto")))
        if loaded.sha256 != input_identity.get("sha256"):
            result.errors.append("identity:sha256-mismatch")
        if image.uuid != macho.get("uuid"):
            result.errors.append("identity:uuid-mismatch")
        if image.architecture != macho.get("architecture"):
            result.errors.append("identity:architecture-mismatch")
        if image.image_base != image_base:
            result.errors.append("identity:image-base-mismatch")
        result.checks["input_reloaded"] = True
    else:
        result.warnings.append("identity:input-not-reloaded")
        result.checks["input_reloaded"] = False

    result.checks.update(
        {
            "offsets_resolved": resolved_offsets,
            "offsets_total": len(offsets),
            "offset_candidates": len(candidates),
            "fflags_decoded": len(flags),
            "registration_calls": registration_calls,
            "flag_coverage": round(coverage, 6),
            "minimum_flag_coverage": minimum_flag_coverage,
            "fflags_map_entries": len(flag_map),
            "uuid": macho.get("uuid"),
            "sha256": input_identity.get("sha256"),
        }
    )
    return result
