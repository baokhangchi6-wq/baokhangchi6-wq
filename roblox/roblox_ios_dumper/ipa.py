from __future__ import annotations

import hashlib
import plistlib
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .macho import MachOError


@dataclass(frozen=True)
class LoadedInput:
    binary: bytes
    source_path: Path
    binary_path: str
    bundle_identifier: str | None
    short_version: str | None
    build_version: str | None
    sha256: str
    source_kind: str

    def bundle_dict(self) -> dict[str, str | None]:
        return {
            "source_kind": self.source_kind,
            "source_path": str(self.source_path),
            "binary_path": self.binary_path,
            "bundle_identifier": self.bundle_identifier,
            "short_version": self.short_version,
            "build_version": self.build_version,
            "sha256": self.sha256,
        }


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


def _framework_executable(name: str) -> bool:
    match = re.fullmatch(r"Payload/[^/]+\.app/Frameworks/([^/]+)\.framework/([^/]+)", name)
    return bool(match and match.group(1) == match.group(2))


def _score_engine_candidate(name: str, binary: bytes, main_name: str) -> int:
    score = 1 if name == main_name else 0
    lowered = name.lower()
    if "robloxlib.framework/robloxlib" in lowered:
        score += 100
    if b"EnableLoadModule\0" in binary:
        score += 80
    if b"FastLogValueChanged" in binary:
        score += 40
    if b"FFlag" in binary or b"DFFlag" in binary:
        score += 20
    score += min(len(binary) // (16 * 1024 * 1024), 16)
    return score


def _load_ipa(path: Path, member: str | None = None) -> LoadedInput:
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise MachOError(f"Unable to open IPA/ZIP: {exc}") from exc

    with archive:
        names = archive.namelist()
        info_candidates = sorted(
            name
            for name in names
            if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)
        )
        if not info_candidates:
            raise MachOError("IPA has no Payload/*.app/Info.plist")

        selected: tuple[str, dict[str, object], str] | None = None
        failures: list[str] = []
        for info_name in info_candidates:
            try:
                info = plistlib.loads(archive.read(info_name))
            except Exception as exc:  # plistlib raises several parse exceptions
                failures.append(f"{info_name}: invalid plist ({exc})")
                continue
            executable = info.get("CFBundleExecutable")
            if not isinstance(executable, str) or not executable:
                failures.append(f"{info_name}: CFBundleExecutable missing")
                continue
            app_root = info_name.rsplit("/", 1)[0]
            executable_name = f"{app_root}/{executable}"
            if executable_name not in names:
                failures.append(f"{info_name}: {executable_name} missing")
                continue
            selected = info_name, info, executable_name
            bundle_id = info.get("CFBundleIdentifier")
            if isinstance(bundle_id, str) and "roblox" in bundle_id.lower():
                break

        if selected is None:
            detail = "; ".join(failures) if failures else "no usable application bundle"
            raise MachOError(f"IPA application executable was not found: {detail}")

        _info_name, info, executable_name = selected
        candidates = [executable_name]
        candidates.extend(
            name
            for name in names
            if name != executable_name
            and (_framework_executable(name) or name.lower().endswith(".dylib"))
        )
        if member:
            if member not in names:
                raise MachOError(f"Requested IPA member does not exist: {member}")
            candidates = [member]

        engine_candidates: list[tuple[int, str, bytes]] = []
        for candidate in candidates:
            info_entry = archive.getinfo(candidate)
            if info_entry.file_size < 32:
                continue
            binary_candidate = archive.read(candidate)
            if binary_candidate[:4] not in MACHO_MAGICS:
                continue
            engine_candidates.append(
                (
                    _score_engine_candidate(candidate, binary_candidate, executable_name),
                    candidate,
                    binary_candidate,
                )
            )
        if not engine_candidates:
            raise MachOError("IPA has no usable Mach-O application/framework executable")
        _score, executable_name, binary = max(
            engine_candidates, key=lambda item: (item[0], len(item[2]))
        )
        if len(binary) < 32:
            raise MachOError("IPA application executable is truncated")

        def text_value(key: str) -> str | None:
            value = info.get(key)
            return value if isinstance(value, str) else None

        return LoadedInput(
            binary=binary,
            source_path=path.resolve(),
            binary_path=executable_name,
            bundle_identifier=text_value("CFBundleIdentifier"),
            short_version=text_value("CFBundleShortVersionString"),
            build_version=text_value("CFBundleVersion"),
            sha256=_hash(binary),
            source_kind="ipa",
        )


def load_input(path: Path, member: str | None = None) -> LoadedInput:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise MachOError(f"Input file does not exist: {path}")
    if path.suffix.lower() in {".ipa", ".zip"}:
        return _load_ipa(path, member)
    if member:
        raise MachOError("--member can only be used with an IPA/ZIP input")
    try:
        binary = path.read_bytes()
    except OSError as exc:
        raise MachOError(f"Unable to read input binary: {exc}") from exc
    return LoadedInput(
        binary=binary,
        source_path=path,
        binary_path=path.name,
        bundle_identifier=None,
        short_version=None,
        build_version=None,
        sha256=_hash(binary),
        source_kind="macho",
    )
