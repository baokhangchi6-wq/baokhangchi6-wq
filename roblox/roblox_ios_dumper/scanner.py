from __future__ import annotations

import collections
import bisect
import re
import sys
from array import array
from dataclasses import asdict, dataclass

from .arm64 import (
    AddressReference,
    decode_address_reference,
    decode_bl,
    is_bl,
    is_probable_function_prologue,
    base_register,
    store_kind,
)
from .macho import MachOImage, MachOSection


KNOWN_PREFIXES = (
    "FFlag",
    "DFFlag",
    "SFFlag",
    "FInt",
    "DFInt",
    "SFInt",
    "FString",
    "DFString",
    "SFString",
    "FLog",
)


@dataclass(frozen=True)
class StringXref:
    string_address: int
    instruction_address: int
    reference_kind: str


@dataclass(frozen=True)
class OffsetResult:
    name: str
    address: int | None
    rva: int | None
    confidence: str
    method: str
    string: str
    xrefs: tuple[int, ...]
    evidence: tuple[str, ...]

    def json_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FastFlagResult:
    name: str
    prefix: str
    suffix: str
    address: int
    rva: int
    segment: str
    writable: bool
    confidence: str
    call_site: int
    register_function: int
    name_address: int
    evidence: tuple[str, ...]

    def json_dict(self) -> dict[str, object]:
        return asdict(self)


class Arm64Scanner:
    def __init__(self, image: MachOImage, known_flag_names: set[str] | None = None) -> None:
        self.image = image
        self._known_by_suffix: dict[str, list[str]] = collections.defaultdict(list)
        for full_name in known_flag_names or ():
            classified = self._classify_string(full_name)
            if classified:
                _prefix, suffix = classified
                self._known_by_suffix[suffix].append(full_name)
        self._code_sections = image.executable_sections
        self._section_words: list[tuple[MachOSection, array]] = []
        for section in self._code_sections:
            usable = section.size - (section.size % 4)
            words = array("I")
            words.frombytes(
                image.data[section.file_offset : section.file_offset + usable]
            )
            if sys.byteorder != "little":
                words.byteswap()
            self._section_words.append((section, words))
        self._xref_index: dict[int, list[StringXref]] = {}
        self.offset_candidates: list[dict[str, object]] = []

    def _word(self, address: int) -> int | None:
        for section, words in self._section_words:
            if section.address <= address <= section.end_address - 4:
                index = (address - section.address) // 4
                return words[index] if 0 <= index < len(words) else None
        return None

    def _is_code(self, address: int) -> bool:
        return any(
            section.address <= address < section.end_address
            for section in self._code_sections
        )

    def _iter_words(self):
        for section, words in self._section_words:
            for index, word in enumerate(words):
                yield section.address + index * 4, word

    def _decode_reference(self, address: int, first: int) -> AddressReference | None:
        ref = decode_address_reference(address, first, self._word(address + 4))
        if ref is None:
            return None
        if ref.kind in {"ldr-literal-slot", "adrp+ldr-slot"}:
            target = self.image.read_u64(ref.target_address)
            if target is None:
                return None
            return AddressReference(
                instruction_address=ref.instruction_address,
                target_address=target,
                register=ref.register,
                kind=ref.kind.removesuffix("-slot"),
                instruction_count=ref.instruction_count,
            )
        return ref

    def _index_targets(self, targets: set[int]) -> None:
        missing = targets.difference(self._xref_index)
        if not missing:
            return
        for target in missing:
            self._xref_index[target] = []
        for address, first in self._iter_words():
            ref = self._decode_reference(address, first)
            if ref is not None and ref.target_address in missing:
                self._xref_index[ref.target_address].append(
                    StringXref(ref.target_address, ref.instruction_address, ref.kind)
                )

    def find_xrefs(self, target: int) -> list[StringXref]:
        self._index_targets({target})
        return list(self._xref_index.get(target, ()))

    def find_string_xrefs(self, value: str) -> list[StringXref]:
        hits: list[StringXref] = []
        for address in self.image.find_cstrings(value):
            hits.extend(self.find_xrefs(address))
        return sorted(hits, key=lambda item: item.instruction_address)

    def resolve_function_start(
        self, address: int, max_scan: int = 0x1000
    ) -> tuple[int | None, str]:
        starts = self.image.function_starts
        if starts:
            index = bisect.bisect_right(starts, address) - 1
            if index >= 0:
                candidate = starts[index]
                # LC_FUNCTION_STARTS describes the containing function directly.
                # Large generated functions routinely exceed the heuristic
                # backward-prologue scan limit, so do not apply max_scan here.
                if self._is_code(candidate):
                    return candidate, "lc-function-starts"
        lower = max(0, address - max_scan)
        current = address & ~3
        while current >= lower:
            instruction = self._word(current)
            if instruction is not None and is_probable_function_prologue(instruction):
                return current, "arm64-prologue"
            current -= 4
        return None, "not-found"

    def find_function_start(self, address: int, max_scan: int = 0x1000) -> int | None:
        return self.resolve_function_start(address, max_scan)[0]

    def _compile_hex_pattern(self, pattern_str: str) -> re.Pattern:
        tokens = pattern_str.strip().split()
        parts: list[bytes] = []
        wildcard_count = 0
        for tok in tokens:
            if tok in ("??", "?"):
                wildcard_count += 1
            else:
                if wildcard_count > 0:
                    parts.append(f".{{{wildcard_count}}}".encode("ascii"))
                    wildcard_count = 0
                val = bytes.fromhex(tok)
                parts.append(re.escape(val))
        if wildcard_count > 0:
            parts.append(f".{{{wildcard_count}}}".encode("ascii"))
        return re.compile(b"".join(parts), re.DOTALL)

    def scan_hex_pattern(
        self, pattern_str: str, relative_offset: int = 0
    ) -> tuple[int | None, list[int], list[str]]:
        try:
            compiled = self._compile_hex_pattern(pattern_str)
        except Exception as exc:
            return None, [], [f"invalid-hex-pattern:{exc}"]

        matches: list[int] = []
        for section in self._code_sections:
            sec_data = self.image.data[
                section.file_offset : section.file_offset + section.size
            ]
            for m in compiled.finditer(sec_data):
                va = section.address + m.start() + relative_offset
                if va % 4 == 0:
                    matches.append(va)

        if len(matches) == 1:
            sec = self.image.section_for_va(matches[0])
            evidence = [
                "hex-pattern:unique",
                f"match-count:1",
                f"offset:{relative_offset}",
                f"section:{sec.segment_name},{sec.name}" if sec else "section:unknown",
            ]
            return matches[0], matches, evidence
        elif len(matches) > 1:
            sec = self.image.section_for_va(matches[0])
            evidence = [
                "hex-pattern:multiple",
                f"match-count:{len(matches)}",
                f"offset:{relative_offset}",
                f"section:{sec.segment_name},{sec.name}" if sec else "section:unknown",
            ]
            return matches[0], matches, evidence
        return None, [], ["hex-pattern:not-found"]

    @staticmethod
    def _pattern_bytes(pattern_str: str) -> tuple[bytes, bytes]:
        values = bytearray()
        masks = bytearray()
        for token in pattern_str.strip().split():
            if token in {"?", "??"}:
                values.append(0)
                masks.append(0)
            else:
                raw = bytes.fromhex(token)
                if len(raw) != 1:
                    raise ValueError(f"expected one byte token, got {token!r}")
                values.append(raw[0])
                masks.append(0xFF)
        return bytes(values), bytes(masks)

    def scan_relaxed_hex_candidates(
        self,
        name: str,
        pattern_str: str,
        relative_offset: int = 0,
        *,
        minimum_score: float = 0.65,
        maximum_candidates: int = 5,
    ) -> list[dict[str, object]]:
        """Find evidence candidates without promoting fuzzy matches to offsets.

        A stable fixed-byte island anchors each search. The complete wildcard
        pattern is then scored at that location. This is deliberately
        candidate-only: compiler changes can make a high byte score point at a
        semantically different function.
        """
        try:
            values, masks = self._pattern_bytes(pattern_str)
        except (ValueError, TypeError):
            return []
        if not values:
            return []

        chunks: list[tuple[int, bytes]] = []
        start: int | None = None
        for index, mask in enumerate(masks + b"\0"):
            if mask and start is None:
                start = index
            elif not mask and start is not None:
                chunk = values[start:index]
                if len(chunk) >= 8:
                    chunks.append((start, chunk))
                start = None
        if not chunks:
            return []
        chunks.sort(key=lambda item: len(item[1]), reverse=True)

        fixed_count = sum(1 for mask in masks if mask)
        if fixed_count < 16:
            return []
        candidate_scores: dict[int, tuple[float, int, int]] = {}
        for section in self._code_sections:
            section_data = self.image.data[
                section.file_offset : section.file_offset + section.size
            ]
            for chunk_offset, chunk in chunks[:3]:
                cursor = 0
                hits = 0
                while hits < 64:
                    found = section_data.find(chunk, cursor)
                    if found < 0:
                        break
                    hits += 1
                    cursor = found + 1
                    pattern_start = found - chunk_offset
                    if pattern_start < 0 or pattern_start + len(values) > len(section_data):
                        continue
                    address = section.address + pattern_start + relative_offset
                    if address % 4 or not self._is_code(address):
                        continue
                    window = section_data[pattern_start : pattern_start + len(values)]
                    matched = sum(
                        1
                        for expected, actual, mask in zip(values, window, masks)
                        if mask and expected == actual
                    )
                    score = matched / fixed_count
                    if score < minimum_score:
                        continue
                    previous = candidate_scores.get(address)
                    if previous is None or score > previous[0]:
                        candidate_scores[address] = (score, matched, fixed_count)

        ranked = sorted(
            candidate_scores.items(), key=lambda item: (-item[1][0], item[0])
        )[:maximum_candidates]
        results: list[dict[str, object]] = []
        for rank, (address, (score, matched, total)) in enumerate(ranked, start=1):
            section = self.image.section_for_va(address)
            function_start, boundary_source = self.resolve_function_start(address)
            results.append(
                {
                    "name": name,
                    "address": address,
                    "rva": self.image.to_rva(address),
                    "method": "relaxed-hex-candidate",
                    "score": round(score, 6),
                    "rank": rank,
                    "promoted": False,
                    "containing_function_rva": (
                        self.image.to_rva(function_start) if function_start is not None else None
                    ),
                    "function_delta": (
                        address - function_start if function_start is not None else None
                    ),
                    "evidence": [
                        f"fixed-bytes:{matched}/{total}",
                        f"anchor-islands:{min(3, len(chunks))}",
                        f"section:{section.segment_name},{section.name}"
                        if section
                        else "section:unknown",
                        f"function-boundary:{boundary_source}",
                        "candidate-only:not-emitted-to-Offsets.hpp",
                    ],
                }
            )
        return results

    def scan_offsets(self, patterns: list[dict[str, object]]) -> list[OffsetResult]:
        self.offset_candidates = []
        string_addresses: dict[str, list[int]] = {}
        targets: set[int] = set()
        for pattern in patterns:
            value = str(pattern.get("string") or "")
            if value:
                addresses = self.image.find_cstrings(value)
                string_addresses[value] = addresses
                targets.update(addresses)
        self._index_targets(targets)

        results: list[OffsetResult] = []
        for pattern in patterns:
            name = str(pattern.get("name", "")).strip()
            hex_pat = pattern.get("hex_pattern")
            offset_val = int(pattern.get("offset", 0))

            if hex_pat and isinstance(hex_pat, str) and hex_pat.strip():
                address, matches, evidence = self.scan_hex_pattern(
                    hex_pat.strip(), offset_val
                )
                # Lua patterns often match an instruction in the body. Only
                # LC_FUNCTION_STARTS can turn such a hit into an entry point.
                if address is not None and name.startswith("lua"):
                    entries = set()
                    for match in matches:
                        entry, boundary = self.resolve_function_start(match - offset_val)
                        if boundary == "lc-function-starts" and entry is not None:
                            entries.add(entry)
                    address = next(iter(entries)) if len(entries) == 1 else None
                    evidence.append(f"lua-function-boundaries:{len(entries)}")
                    evidence.append("callable-abi:not-verified")
                confidence = (
                    "high"
                    if address is not None and len(matches) == 1
                    else ("medium" if address is not None else "missing")
                )
                if address is None:
                    self.offset_candidates.extend(
                        self.scan_relaxed_hex_candidates(name, hex_pat.strip(), offset_val)
                    )
                results.append(
                    OffsetResult(
                        name=name,
                        address=address,
                        rva=self.image.to_rva(address) if address is not None else None,
                        confidence=confidence,
                        method="hex-pattern",
                        string=hex_pat.strip(),
                        xrefs=tuple(matches),
                        evidence=tuple(evidence),
                    )
                )
                continue

            value = str(pattern.get("string") or "")
            max_scan = int(pattern.get("max_function_scan", 4096))
            xref_index = int(pattern.get("xref_index", 0))
            xrefs = sorted(
                (
                    xref
                    for target in string_addresses.get(value, ())
                    for xref in self._xref_index.get(target, ())
                ),
                key=lambda item: item.instruction_address,
            )
            resolved_xrefs = [
                (item, *self.resolve_function_start(item.instruction_address, max_scan))
                for item in xrefs
            ]
            functions = [candidate for _item, candidate, _source in resolved_xrefs if candidate]
            unique_functions = sorted(set(functions))
            address = (
                unique_functions[xref_index]
                if 0 <= xref_index < len(unique_functions)
                else None
            )
            confidence = "missing"
            evidence: list[str] = []
            if address is not None:
                matching_xrefs = [
                    item
                    for item, candidate, _source in resolved_xrefs
                    if candidate == address
                ]
                boundary_sources = sorted(
                    {
                        source
                        for _item, candidate, source in resolved_xrefs
                        if candidate == address
                    }
                )
                confidence = (
                    "high"
                    if len(matching_xrefs) >= 2 or "lc-function-starts" in boundary_sources
                    else "medium"
                )
                section = self.image.section_for_va(address)
                evidence.extend(
                    [
                        f"exact-cstring:{value}",
                        f"string-xrefs:{len(xrefs)}",
                        f"candidate-functions:{len(unique_functions)}",
                        f"function-boundary:{','.join(boundary_sources)}",
                        f"section:{section.segment_name},{section.name}" if section else "section:unknown",
                    ]
                )
            elif xrefs:
                for item, _candidate, source in resolved_xrefs[:8]:
                    self.offset_candidates.append(
                        {
                            "name": name,
                            "address": item.instruction_address,
                            "rva": self.image.to_rva(item.instruction_address),
                            "method": "unresolved-string-xref",
                            "score": 0.0,
                            "rank": 0,
                            "promoted": False,
                            "evidence": [
                                f"exact-cstring:{value}",
                                f"xref:{item.instruction_address:#x}",
                                f"reference-kind:{item.reference_kind}",
                                f"function-boundary:{source}",
                                "candidate-only:not-emitted-to-Offsets.hpp",
                            ],
                        }
                    )
            results.append(
                OffsetResult(
                    name=name,
                    address=address,
                    rva=self.image.to_rva(address) if address is not None else None,
                    confidence=confidence,
                    method="cstring+xref+function-boundary",
                    string=value,
                    xrefs=tuple(item.instruction_address for item in xrefs),
                    evidence=tuple(evidence),
                )
            )
        return results

    def _first_bl_after(self, xref: int, limit: int = 40) -> int | None:
        for address in range(xref + 4, xref + limit + 1, 4):
            instruction = self._word(address)
            if instruction is not None and is_bl(instruction):
                return decode_bl(instruction, address)
        return None

    def find_register_function(self) -> tuple[int | None, list[str]]:
        anchor_xrefs = self.find_string_xrefs("EnableLoadModule")
        counts: collections.Counter[int] = collections.Counter()
        for xref in anchor_xrefs:
            target = self._first_bl_after(xref.instruction_address)
            if target is not None and self._is_code(target):
                counts[target] += 1
        if not counts:
            return None, [
                "anchor:EnableLoadModule",
                f"anchor-xrefs:{len(anchor_xrefs)}",
                "register-function:not-found",
            ]
        target, count = counts.most_common(1)[0]
        return target, [
            "anchor:EnableLoadModule",
            f"anchor-xrefs:{len(anchor_xrefs)}",
            f"register-target-votes:{count}",
        ]

    def _registration_calls(self, target: int) -> list[int]:
        calls: list[int] = []
        for address, instruction in self._iter_words():
            if is_bl(instruction) and decode_bl(instruction, address) == target:
                calls.append(address)
        return calls

    def _references_before(self, address: int, instruction_count: int = 48) -> list[AddressReference]:
        lower = address - instruction_count * 4
        references: list[AddressReference] = []
        for pc in range(lower & ~3, address, 4):
            first = self._word(pc)
            if first is None:
                continue
            ref = self._decode_reference(pc, first)
            if ref is not None:
                references.append(ref)
        return references

    def _classify_string(self, value: str) -> tuple[str, str] | None:
        for prefix in KNOWN_PREFIXES:
            if value.startswith(prefix) and len(value) > len(prefix):
                return prefix, value[len(prefix) :]
        return None

    def _direct_storage(self, ref: AddressReference) -> tuple[str, int] | None:
        next_pc = ref.instruction_address + ref.instruction_count * 4
        for pc in range(next_pc, next_pc + 16, 4):
            instruction = self._word(pc)
            if instruction is None:
                break
            kind = store_kind(instruction)
            if kind and base_register(instruction) == ref.register:
                return kind, pc
        return None

    def _resolve_flag_name(self, suffix: str, inferred_kind: str) -> tuple[str, str, str]:
        compatible_prefixes = {
            "bool": {"FFlag", "DFFlag", "SFFlag"},
            "bool16": {"FFlag", "DFFlag", "SFFlag"},
            "int32": {"FInt", "DFInt", "SFInt"},
            "pointer64": {"FString", "DFString", "SFString"},
        }.get(inferred_kind, set())
        known = sorted(
            {
                name
                for name in self._known_by_suffix.get(suffix, ())
                if (self._classify_string(name) or ("", ""))[0] in compatible_prefixes
            }
        )
        if len(known) == 1:
            classified = self._classify_string(known[0])
            if classified:
                return known[0], classified[0], "known-name-map"
        inferred_prefix = {
            "bool": "FFlag",
            "bool16": "FFlag",
            "int32": "FInt",
            "pointer64": "FString",
        }.get(inferred_kind, "FFlag")
        return inferred_prefix + suffix, inferred_prefix, "opcode-type-inference"

    def _decode_registration(self, call_site: int, register_function: int) -> FastFlagResult | None:
        references = self._references_before(call_site)
        string_candidates: list[tuple[int, str, str, str]] = []
        storage_candidates: list[tuple[int, str, bool, str, int]] = []
        prefix_candidates: list[str] = []

        for ref in references:
            value = self.image.read_ascii_identifier(ref.target_address)
            if value:
                classified = self._classify_string(value)
                if classified:
                    prefix, suffix = classified
                    string_candidates.append((ref.target_address, prefix, suffix, value))
                elif value in KNOWN_PREFIXES:
                    prefix_candidates.append(value)
                else:
                    # Some binaries pass prefix and suffix separately.
                    string_candidates.append((ref.target_address, "", value, value))

            segment = self.image.segment_for_va(ref.target_address)
            if segment and segment.is_writable:
                direct = self._direct_storage(ref)
                if direct:
                    storage_candidates.append(
                        (
                            ref.target_address,
                            segment.name,
                            segment.is_writable,
                            direct[0],
                            direct[1],
                        )
                    )

        if not string_candidates or not storage_candidates:
            return None

        full = [item for item in string_candidates if item[1]]
        if full:
            name_address, prefix, suffix, full_name = full[-1]
            name_source = "binary-full-name"
        else:
            name_address, _empty, suffix, _value = string_candidates[-1]
            prefix = prefix_candidates[-1] if prefix_candidates else ""
            name_source = "binary-split-prefix" if prefix else ""

        storage_address, segment_name, writable, storage_kind, store_pc = storage_candidates[-1]
        if not prefix:
            full_name, prefix, name_source = self._resolve_flag_name(suffix, storage_kind)
        if storage_address == name_address:
            return None
        confidence = "high" if writable and name_source != "opcode-type-inference" else "medium"
        evidence = (
            f"call-site:{call_site:#x}",
            f"register-function:{register_function:#x}",
            f"name-address:{name_address:#x}",
            f"storage-segment:{segment_name}",
            f"storage-writable:{int(writable)}",
            f"storage-write:{storage_kind}@{store_pc:#x}",
            f"name-source:{name_source}",
            f"reference-window:{len(references)}",
        )
        return FastFlagResult(
            name=full_name,
            prefix=prefix,
            suffix=suffix,
            address=storage_address,
            rva=self.image.to_rva(storage_address),
            segment=segment_name,
            writable=writable,
            confidence=confidence,
            call_site=call_site,
            register_function=register_function,
            name_address=name_address,
            evidence=evidence,
        )

    def scan_fast_flags(self) -> tuple[list[FastFlagResult], dict[str, object]]:
        register_function, anchor_evidence = self.find_register_function()
        if register_function is None:
            return [], {
                "register_function": None,
                "registration_calls": 0,
                "decoded_calls": 0,
                "anchor_evidence": anchor_evidence,
            }
        call_sites = self._registration_calls(register_function)
        decoded: dict[str, FastFlagResult] = {}
        for call_site in call_sites:
            result = self._decode_registration(call_site, register_function)
            if result is not None:
                old = decoded.get(result.name)
                if old is None or (old.confidence != "high" and result.confidence == "high"):
                    decoded[result.name] = result
        results = sorted(decoded.values(), key=lambda item: item.name)
        return results, {
            "register_function": register_function,
            "register_function_rva": self.image.to_rva(register_function),
            "registration_calls": len(call_sites),
            "decoded_calls": len(results),
            "anchor_evidence": anchor_evidence,
        }
