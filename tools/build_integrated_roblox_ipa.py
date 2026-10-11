#!/usr/bin/env python3
"""
Masterstrap iOS IPA Builder & Live Integrator.
Dumps offsets, compiles the native iOS ARM64 bridge with guarded direct-RVA
FastFlag writes, embeds ClientAppSettings.json, tracks miss_offsets, and
packages a ready-to-sideload IPA. Engine SetFFlag/GetFastFlag hooks are not
claimed until their iOS-specific ABI and signatures are independently verified.
"""

import argparse
import io
import json
import os
from pathlib import Path
import plistlib
import re
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "output"
REMOTE_FFLAGS_URL = "https://masterstrap.org/Data/IOS/IOS_FFlags.hpp"
REMOTE_OFFSETS_URL = "https://masterstrap.org/Data/IOS/IOS_Offsets.hpp"
GLOBAL_BRIDGE_SCHEME = "masterstrap-rbx-bridge-v1"
VNG_BRIDGE_SCHEME = "masterstrap-rbx-vng-bridge-v1"


def register_bridge_url_scheme(plist: dict) -> str:
    bundle_id = str(plist.get("CFBundleIdentifier", ""))
    bridge_scheme = (
        VNG_BRIDGE_SCHEME
        if "vng" in bundle_id.lower()
        else GLOBAL_BRIDGE_SCHEME
    )
    url_types = plist.setdefault("CFBundleURLTypes", [])
    scheme_exists = any(
        bridge_scheme in entry.get("CFBundleURLSchemes", [])
        for entry in url_types
        if isinstance(entry, dict)
    )
    if not scheme_exists:
        url_types.append({
            "CFBundleTypeRole": "Editor",
            "CFBundleURLName": "com.masterstrap.bridge.v1",
            "CFBundleURLSchemes": [bridge_scheme],
        })
    return bridge_scheme


def configure_ios_runtime_plist(plist: dict) -> dict:
    """Enable high-refresh presentation without forcing a frame rate."""
    plist["CADisableMinimumFrameDurationOnPhone"] = True
    return plist


def sanitize_and_configure_roblox_plist(plist: dict) -> str:
    """Purge third-party modifications, restore canonical bundle id & URL schemes."""
    orig_bundle_id = str(plist.get("CFBundleIdentifier", ""))
    is_vng = "vng" in orig_bundle_id.lower() or "vng" in str(plist.get("CFBundleName", "")).lower()

    # 1. Restore canonical Roblox bundle identifier
    canonical_bundle_id = "com.vng.roblox" if is_vng else "com.roblox.robloxmobile"
    plist["CFBundleIdentifier"] = canonical_bundle_id

    # 2. Presentation and metadata
    plist["CFBundleDisplayName"] = "Masterstrap"
    plist["CFBundleName"] = "Masterstrap"

    # 3. Enable file sharing & high-refresh presentation
    plist["UIFileSharingEnabled"] = True
    plist["LSSupportsOpeningDocumentsInPlace"] = True
    configure_ios_runtime_plist(plist)

    # 4. Clean third-party / delta schemes and register canonical schemes
    bridge_scheme = register_bridge_url_scheme(plist)
    required_schemes = (
        ["roblox-vng", "robloxmobile", "roblox", bridge_scheme]
        if is_vng
        else ["robloxmobile", "roblox", bridge_scheme]
    )

    clean_url_types = []
    existing_schemes = set()
    for entry in plist.get("CFBundleURLTypes", []):
        if not isinstance(entry, dict):
            continue
        schemes = entry.get("CFBundleURLSchemes", [])
        # Purge third-party/delta schemes
        purged = [s for s in schemes if "delta" not in str(s).lower()]
        if purged:
            entry["CFBundleURLSchemes"] = purged
            for s in purged:
                if isinstance(s, str):
                    existing_schemes.add(s.lower())
            clean_url_types.append(entry)

    for req in required_schemes:
        if req.lower() not in existing_schemes:
            clean_url_types.append({
                "CFBundleTypeRole": "Editor",
                "CFBundleURLName": f"com.masterstrap.{req}",
                "CFBundleURLSchemes": [req],
            })
            existing_schemes.add(req.lower())

    plist["CFBundleURLTypes"] = clean_url_types
    return bridge_scheme


def find_clang_lld():
    clang = shutil_which("clang")
    lld = shutil_which("ld.lld") or shutil_which("lld") or shutil_which("ld64.lld")
    if clang and lld:
        return clang, lld

    ndk_clang = Path(r"C:\Program Files (x86)\Android\android-sdk\ndk\27.0.12077973\toolchains\llvm\prebuilt\windows-x86_64\bin\clang.exe")
    ndk_lld = Path(r"C:\Program Files (x86)\Android\android-sdk\ndk\27.0.12077973\toolchains\llvm\prebuilt\windows-x86_64\bin\ld.lld.exe")
    if ndk_clang.is_file() and ndk_lld.is_file():
        return str(ndk_clang), str(ndk_lld)

    return "clang", "ld.lld"


def shutil_which(cmd):
    import shutil
    return shutil.which(cmd)


def fetch_or_load_fflags_map(local_hpp: Path) -> dict:
    content = ""
    if local_hpp and local_hpp.is_file():
        print(f"Loading local FFlags header: {local_hpp}")
        content = local_hpp.read_text(encoding="utf-8", errors="ignore")
    else:
        print(f"Fetching FFlags from {REMOTE_FFLAGS_URL}...")
        try:
            req = urllib.request.Request(REMOTE_FFLAGS_URL, headers={'User-Agent': 'Masterstrap-IOS-Builder/1.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"Warning: Remote fetch failed ({e}). Checking local fallbacks...")
            fallback_paths = [
                ROOT / "output" / "FFlags.hpp",
                ROOT / "IOS_FFlags.hpp",
                Path(r"C:\Users\Administrator\Desktop\Website\Data\IOS\IOS_FFlags.hpp")
            ]
            for fb in fallback_paths:
                if fb.is_file():
                    print(f"Loaded fallback header: {fb}")
                    content = fb.read_text(encoding="utf-8", errors="ignore")
                    break

    table = {}
    for line in content.splitlines():
        m = re.search(r'inline\s+constexpr\s+std::uintptr_t\s+(\w+)\s*=\s*(0x[0-9A-Fa-f]+);', line)
        if m:
            table[m.group(1)] = int(m.group(2), 16)
    return table


def run_local_dumper(ipa_path: Path, out_dir: Path) -> Path:
    dumper_root = ROOT / "roblox"
    if not (dumper_root / "roblox_ios_dumper").is_dir():
        raise RuntimeError("Local iOS dumper missing; refusing remote offsets for an IPA build")
    print(f"Running automated iOS Dumper on {ipa_path.name}...")
    # Explicit sys.path also supports the bundled/isolated Windows Python.
    entry = "import sys; sys.path.insert(0, sys.argv.pop(1)); from roblox_ios_dumper.cli import main; raise SystemExit(main())"
    cmd = [sys.executable, "-c", entry, str(dumper_root), "dump", str(ipa_path.resolve()),
           "--patterns", str(ROOT / "patterns.ios.json"), "-o", str(out_dir.resolve())]
    subprocess.run(cmd, cwd=str(dumper_root), check=True)
    dumped_fflags = out_dir / "FFlags.hpp"
    if not dumped_fflags.is_file():
        raise RuntimeError("Local iOS dump did not produce FFlags.hpp")
    return dumped_fflags


def verify_ipa_profile(input_ipa: Path, header: Path) -> str:
    """Recheck binary identity AND generated maps; a filename is not evidence."""
    import uuid
    if header.name != "FFlags.hpp":
        raise RuntimeError("Select FFlags.hpp from the complete verified dump directory")
    sys.path.insert(0, str(ROOT / "roblox"))
    from roblox_ios_dumper.verification import verify_dump
    result = verify_dump(header.parent, input_path=input_ipa)
    if not result.ok:
        raise RuntimeError("iOS profile verification failed: " + "; ".join(result.errors))
    identity = str(uuid.UUID(str(result.checks["uuid"])))
    print(f"Verified local iOS profile: UUID={identity} SHA256={result.checks['sha256']}")
    return identity


def resolve_flags_with_miss_report(flags: dict, table: dict):
    all_prefixes = (
        'SFFlag', 'SFInt', 'SFString', 'SFLog',
        'DFFlag', 'DFInt', 'DFString', 'DFLog',
        'FFlag', 'FInt', 'FString', 'FLog',
        'FCString', 'Flag', 'Int', 'String', 'Log',
    )
    verified_entries = []
    miss_offsets = []

    # Build multi-index mapping for version-agnostic flag matching
    exact_lookup = dict(table)
    lower_lookup = {k.lower(): v for k, v in table.items()}
    bare_lookup = {}
    bare_lower_lookup = {}

    for k, v in table.items():
        bare = k
        for p in all_prefixes:
            if k.startswith(p):
                bare = k[len(p):]
                break
        bare_lookup.setdefault(bare, v)
        bare_lower_lookup.setdefault(bare.lower(), v)

    for name, val in flags.items():
        rva = None
        if name in exact_lookup:
            rva = exact_lookup[name]
        elif name.lower() in lower_lookup:
            rva = lower_lookup[name.lower()]
        else:
            # Strip known prefix from query
            bare = name
            for p in all_prefixes:
                if name.startswith(p):
                    bare = name[len(p):]
                    break

            # Suffix variations: e.g. "TaskSchedulerLimitTargetFpsTo2402" <-> "TaskSchedulerLimitTargetFpsTo240"
            bare_variants = [
                bare,
                bare.rstrip('0123456789'),
                bare + '2',
                bare + '3',
            ]

            for b in bare_variants:
                if b in bare_lookup:
                    rva = bare_lookup[b]
                    break
                if b.lower() in bare_lower_lookup:
                    rva = bare_lower_lookup[b.lower()]
                    break
                # Try all prefixes
                matched = False
                for p in all_prefixes:
                    cand = p + b
                    if cand in exact_lookup:
                        rva = exact_lookup[cand]
                        matched = True
                        break
                    if cand.lower() in lower_lookup:
                        rva = lower_lookup[cand.lower()]
                        matched = True
                        break
                if matched:
                    break

        if rva is not None:
            if isinstance(val, bool):
                kind = 0
                num_val = 1 if val else 0
            elif isinstance(val, int):
                kind = 1
                num_val = val
            else:
                continue
            verified_entries.append((name, rva, kind, num_val))
        else:
            miss_offsets.append(name)

    return sorted(verified_entries, key=lambda x: x[1]), miss_offsets


def fetch_or_load_offsets_map(local_hpp: Path) -> dict:
    content = ""
    if local_hpp and local_hpp.is_file():
        content = local_hpp.read_text(encoding="utf-8", errors="ignore")
    else:
        fallback_paths = [
            ROOT / "output" / "2.735.1135" / "Offsets.hpp",
            ROOT / "roblox" / "output" / "2.735.1135" / "Offsets.hpp",
            ROOT / "output" / "Offsets.hpp",
            Path(r"C:\Users\Administrator\Desktop\Website\Data\IOS\IOS_Offsets.hpp")
        ]
        for fb in fallback_paths:
            if fb.is_file():
                content = fb.read_text(encoding="utf-8", errors="ignore")
                break

    table = {}
    for line in content.splitlines():
        m = re.search(r'inline\s+constexpr\s+std::uintptr_t\s+(\w+)\s*=\s*(0x[0-9A-Fa-f]+);', line)
        if m:
            table[m.group(1)] = int(m.group(2), 16)
    return table


def generate_native_hook_source(entries: list, miss_offsets: list, total_flags_count: int, offset_entries: list = None, all_flags_table: dict = None, expected_uuid: str = None, font_profile: dict = None) -> str:
    import uuid
    uuid_bytes = uuid.UUID(expected_uuid).bytes if expected_uuid else bytes(16)
    if offset_entries is None:
        offset_entries = []
    if all_flags_table is None:
        all_flags_table = {}
    font_profile = font_profile or {}

    sorted_all_flags = sorted((name, rva) for name, rva in all_flags_table.items()
        if name.startswith(('FFlag', 'DFFlag', 'SFFlag', 'FInt', 'DFInt', 'SFInt')))

    lines = [
        "#include <os/lock.h>",
        "#ifdef bool",
        "#undef bool",
        "#endif",
        "typedef unsigned long uintptr_t;",
        "typedef unsigned int uint32_t;",
        "typedef unsigned char uint8_t;",
        "typedef unsigned long long uint64_t;",
        "typedef int int32_t;",
        "typedef long long int64_t;",
        "typedef unsigned long size_t;",
        "typedef int bool;",
        "#define true 1",
        "#define false 0",
        "#define NULL ((void*)0)",
        "#define PROT_READ 0x1",
        "#define PROT_WRITE 0x2",
        "#define PROT_EXEC 0x4",
        "#define LOG_NOTICE 5",
        "#define LOG_ERR 3",
        "",
        "#pragma clang diagnostic ignored \"-Wbuiltin-requires-header\"",
        "#pragma clang diagnostic ignored \"-Wbuiltin-declaration-mismatch\"",
        "",
        "extern uint32_t _dyld_image_count(void);",
        "extern const char* _dyld_get_image_name(uint32_t image_index);",
        "extern const void* _dyld_get_image_header(uint32_t image_index);",
        "extern int mprotect(void *addr, size_t len, int prot);",
        "extern int getpagesize(void);",
        "extern int usleep(unsigned int usec);",
        "extern int getpid(void);",
        "extern long time(long *timer);",
        "extern char* getenv(const char *name);",
        "extern int setenv(const char *name, const char *value, int overwrite);",
        "extern int snprintf(char *str, size_t size, const char *format, ...);",
        "extern int vsnprintf(char *str, size_t size, const char *format, __builtin_va_list ap);",
        "extern void syslog(int priority, const char *format, ...);",
        "extern void* fopen(const char *filename, const char *mode);",
        "extern int fseek(void *stream, long offset, int whence);",
        "extern long ftell(void *stream);",
        "extern size_t fread(void *ptr, size_t size, size_t count, void *stream);",
        "extern int fprintf(void *stream, const char *format, ...) __attribute__((format(printf, 2, 3)));",
        "extern int fclose(void *stream);",
        "extern int fflush(void *stream);",
        "extern int unlink(const char *path);",
        "extern int rename(const char *old_path, const char *new_path);",
        "extern void* malloc(size_t size);",
        "extern void free(void *ptr);",
        "extern long long atoll(const char *str);",
        "extern size_t strlen(const char *str);",
        "extern int strcmp(const char *s1, const char *s2);",
        "extern int strncmp(const char *s1, const char *s2, size_t n);",
        "extern int pthread_create(void **thread, const void *attr, void *(*start_routine)(void *), void *arg);",
        "extern int pthread_detach(void *thread);",
        "extern char* strstr(const char *haystack, const char *needle);",
        "extern int fputs(const char *str, void *stream);",
        "typedef void* id;",
        "typedef void* SEL;",
        "typedef void* Class;",
        "typedef void* Method;",
        "typedef void* IMP;",
        "extern id objc_getClass(const char *name);",
        "extern SEL sel_registerName(const char *name);",
        "extern id objc_msgSend(id self, SEL op, ...);",
        "extern Class object_getClass(id obj);",
        "extern Method class_getInstanceMethod(Class cls, SEL name);",
        "extern IMP method_getImplementation(Method method);",
        "extern const char* method_getTypeEncoding(Method method);",
        "extern int class_addMethod(Class cls, SEL name, IMP imp, const char *types);",
        "extern IMP method_setImplementation(Method method, IMP imp);",
        "extern const char* class_getName(Class cls);",
        "static os_unfair_lock g_session_lock = OS_UNFAIR_LOCK_INIT;",
        "static void session_unlock(int* unused) { (void)unused; os_unfair_lock_unlock(&g_session_lock); }",
        "#define SESSION_LOCK int session_guard __attribute__((cleanup(session_unlock))) = (os_unfair_lock_lock(&g_session_lock), 0)",
        "static id g_session_payload = NULL;",
        "static char g_session_request[96] = {0};",
        "static char g_applied_request[96] = {0};",
        "static bool g_runtime_ready = false;",
        "static int g_baseline_applied = 0;",
        "static unsigned g_session_apply_count = 0;",
        "static char* read_profile_file(const char*, long*);",
        "static const char* bridge_utf8(id);",
        "static id bridge_nsstr(const char*);",
        "",
        "typedef struct {",
        "    const char* name;",
        "    uintptr_t rva;",
        "} OffsetEntry;",
        "typedef struct { uint32_t magic; int32_t cputype; int32_t cpusubtype; uint32_t filetype; uint32_t ncmds; uint32_t sizeofcmds; uint32_t flags; uint32_t reserved; } MachHeader64;",
        "typedef struct { uint32_t cmd; uint32_t cmdsize; } MachLoadCommand;",
        "typedef struct { uint32_t cmd; uint32_t cmdsize; char segname[16]; uint64_t vmaddr; uint64_t vmsize; uint64_t fileoff; uint64_t filesize; int32_t maxprot; int32_t initprot; uint32_t nsects; uint32_t flags; } MachSegment64;",
        "#define MH_MAGIC_64 0xFEEDFACF",
        "#define LC_SEGMENT_64 0x19",
        "static const uint8_t kExpectedEngineUuid[16] = {" + ",".join(str(b) for b in uuid_bytes) + "};",
        'static const char* kExpectedEngineUuidText = "' + (str(uuid.UUID(expected_uuid)) if expected_uuid else "unbound") + '";',
        f"static const bool kEngineIdentityPinned = {'true' if expected_uuid else 'false'};",
        "static bool engine_identity_matches(uintptr_t base) {",
        "    if (!base || !kEngineIdentityPinned) return false;",
        "    const MachHeader64* h = (const MachHeader64*)base;",
        "    if (h->magic != MH_MAGIC_64 || h->ncmds > 4096 || h->sizeofcmds > 4194304) return false;",
        "    const uint8_t* cursor = (const uint8_t*)(h + 1);",
        "    const uint8_t* end = cursor + h->sizeofcmds;",
        "    for (uint32_t i = 0; i < h->ncmds; ++i) {",
        "        if (cursor + sizeof(MachLoadCommand) > end) return false;",
        "        const MachLoadCommand* cmd = (const MachLoadCommand*)cursor;",
        "        if (cmd->cmdsize < sizeof(*cmd) || cmd->cmdsize > (size_t)(end - cursor)) return false;",
        "        if (cmd->cmd == 0x1b && cmd->cmdsize >= 24) {",
        "            for (int n = 0; n < 16; ++n) if (cursor[8 + n] != kExpectedEngineUuid[n]) return false;",
        "            return true;",
        "        }",
        "        cursor += cmd->cmdsize;",
        "    }",
        "    return false;",
        "}",
        "",
        "static const OffsetEntry kVerifiedOffsets[] = {"
    ]

    for name, rva in offset_entries:
        lines.append(f'    {{"{name}", 0x{rva:X}}},')

    lines.extend([
        "};",
        f"static const size_t kOffsetsCount = {len(offset_entries)};",
        "static int g_abi_probe_total = 0;",
        "static int g_abi_probe_executable = 0;",
        "static int g_abi_probe_plausible = 0;",
        "static int g_abi_probe_duplicate_roles = 0;",
        "static int g_abi_probe_call_gate_open = 0;",
        "",
        "typedef struct {",
        "    const char* name;",
        "    uint32_t rva;",
        "    uint8_t kind; // 0 = bool, 1 = int32",
        "} MasterFlagEntry;",
        "",
        "static const MasterFlagEntry kMasterFlags[] = {"
    ])

    for name, rva in sorted_all_flags:
        kind = 0 if name.startswith(('FFlag', 'DFFlag', 'SFFlag')) else 1
        lines.append(f'    {{"{name}", 0x{rva:X}, {kind}}},')

    lines.extend([
        "};",
        f"static const size_t kMasterFlagsCount = {len(sorted_all_flags)};",
        "typedef struct { const MasterFlagEntry* flag; int kind; int64_t value; } CachedDynamicFlag;",
        "#define MAX_CACHED_DYNAMIC_FLAGS 1024",
        "static CachedDynamicFlag g_cached_dynamic_flags[MAX_CACHED_DYNAMIC_FLAGS];",
        "static size_t g_cached_dynamic_count = 0;",
        f"static const size_t kOriginalFlagCapacity = {max(64, len(sorted_all_flags) + len(entries))};",
        "typedef struct { uintptr_t rva; int kind; int64_t value; bool active; } OriginalFlag;",
        f"static OriginalFlag g_original_flags[{max(64, len(sorted_all_flags) + len(entries))}];",
        "static size_t g_original_flag_count = 0;",
        "",
        "typedef struct {",
        "    const char* name;",
        "    uintptr_t rva;",
        "    int kind; // 0 = bool, 1 = int32",
        "    int64_t value;",
        "} FFlagPatchEntry;",
        "",
        "static const FFlagPatchEntry kVerifiedFlags[] = {"
    ])

    for name, rva, kind, val in entries:
        lines.append(f'    {{"{name}", 0x{rva:X}, {kind}, {val}}},')

    lines.extend([
        "};",
        f"static const size_t kVerifiedCount = {len(entries)};",
        f"static const size_t kTotalRequested = {total_flags_count};",
        f"static const size_t kMissCount = {len(miss_offsets)};",
        "static const char* kFastFlagBackend = \"direct-rva-write-v1\";",
        "static const char* kEngineSetFFlagBackend = \"not-installed\";",
        "static const char* kEngineGetFastFlagBackend = \"not-installed\";",
        "static const char* kFpsRuntimeBackend = \"cadisplaylink-range-policy-v2\";",
        "static const char* kFontRuntimeBackend = \"engine-import-stock-family-v1\";",
        "static const char* kShaderRuntimeBackend = \"guarded-ios-luau-pending\";",
        "static const char* kStretchRuntimeBackend = \"cametallayer-drawable-resize\";",
        "",
        "static const char* kMissOffsets[] = {"
    ])

    for m in miss_offsets:
        lines.append(f'    "{m}",')

    lines.extend([
        "    NULL",
        "};",
        "",
        "static bool SetFastFlag(uintptr_t base, const FFlagPatchEntry* entry) {",
        "    if (!base || !entry || entry->kind < 0 || entry->kind > 1) return false;",
        "    if (entry->kind == 1 && (entry->value < -2147483648LL || entry->value > 2147483647LL)) return false;",
        "    uintptr_t target = base + entry->rva;",
        "    uintptr_t page_size = (uintptr_t)getpagesize();",
        "    if (!page_size || (page_size & (page_size - 1))) return false;",
        "    uintptr_t page_start = target & ~(page_size - 1);",
        "    size_t width = entry->kind == 0 ? 1 : 4;",
        "    size_t page_len = (((target + width - 1) & ~(page_size - 1)) - page_start) + page_size;",
        "    size_t original = 0;",
        "    while (original < g_original_flag_count && g_original_flags[original].rva != entry->rva) original++;",
        "    if (original == g_original_flag_count && original >= kOriginalFlagCapacity) return false;",
        "    if (original < g_original_flag_count && g_original_flags[original].kind != entry->kind) return false;",
        "    if (mprotect((void*)page_start, page_len, PROT_READ | PROT_WRITE) != 0) {",
        "        return false; /* Never write after a failed protection change. */",
        "    }",
        "",
        "    if (original == g_original_flag_count) {",
        "        g_original_flags[original] = (OriginalFlag){entry->rva, entry->kind,",
        "            entry->kind == 0 ? *(volatile uint8_t*)target : *(volatile int32_t*)target, false};",
        "        g_original_flag_count++;",
        "    }",
        "    if (entry->kind == 0) {",
        "        volatile uint8_t* ptr = (volatile uint8_t*)target;",
        "        *ptr = (uint8_t)(entry->value ? 1 : 0);",
        "        return (*ptr == (uint8_t)(entry->value ? 1 : 0));",
        "    } else {",
        "        volatile int32_t* ptr = (volatile int32_t*)target;",
        "        *ptr = (int32_t)entry->value;",
        "        return (*ptr == (int32_t)entry->value);",
        "    }",
        "}",
        "",
        "static void cache_dynamic_flag(const MasterFlagEntry* flag, int kind, int64_t value) {",
        "    if (!flag) return;",
        "    for (size_t i = 0; i < g_original_flag_count; i++)",
        "        if (g_original_flags[i].rva == flag->rva) g_original_flags[i].active = true;",
        "    for (size_t i = 0; i < g_cached_dynamic_count; i++) {",
        "        if (g_cached_dynamic_flags[i].flag == flag) {",
        "            g_cached_dynamic_flags[i].kind = kind;",
        "            g_cached_dynamic_flags[i].value = value;",
        "            return;",
        "        }",
        "    }",
        "    if (g_cached_dynamic_count >= MAX_CACHED_DYNAMIC_FLAGS) return;",
        "    g_cached_dynamic_flags[g_cached_dynamic_count].flag = flag;",
        "    g_cached_dynamic_flags[g_cached_dynamic_count].kind = kind;",
        "    g_cached_dynamic_flags[g_cached_dynamic_count].value = value;",
        "    g_cached_dynamic_count++;",
        "}",
        "",
        "static void runtime_note(const char* fmt, ...);",
        "static void restore_inactive_flags(uintptr_t base) {",
        "    for (size_t i = 0; i < g_original_flag_count; i++) {",
        "        OriginalFlag* original = &g_original_flags[i];",
        "        if (original->active) continue;",
        "        FFlagPatchEntry entry = {\"restore-original\", original->rva, original->kind, original->value};",
        "        if (SetFastFlag(base, &entry)) runtime_note(\"[FFLAG][RESTORE] rva=0x%lx value=%lld source=PRE_MASTERSTRAP\", (unsigned long)entry.rva, (long long)entry.value);",
        "    }",
        "}",
        "static int apply_cached_dynamic_flags(uintptr_t base) {",
        "    int applied = 0;",
        "    if (!base) return 0;",
        "    for (size_t i = 0; i < g_cached_dynamic_count; i++) {",
        "        FFlagPatchEntry entry;",
        "        entry.name = g_cached_dynamic_flags[i].flag->name;",
        "        entry.rva = (uintptr_t)g_cached_dynamic_flags[i].flag->rva;",
        "        entry.kind = g_cached_dynamic_flags[i].kind;",
        "        entry.value = g_cached_dynamic_flags[i].value;",
        "        if (SetFastFlag(base, &entry)) applied++;",
        "    }",
        "    return applied;",
        "}",
        "",
        "static const MasterFlagEntry* lookup_flag_with_fallback(const char* name) {",
        "    int low = 0;",
        "    int high = (int)kMasterFlagsCount - 1;",
        "    while (low <= high) {",
        "        int mid = low + (high - low) / 2;",
        "        int cmp = strcmp(name, kMasterFlags[mid].name);",
        "        if (cmp == 0) return &kMasterFlags[mid];",
        "        if (cmp < 0) high = mid - 1;",
        "        else low = mid + 1;",
        "    }",
        "    const char* prefixes[] = {\"DFInt\", \"DFFlag\", \"FFlag\", \"FInt\", \"DFString\", \"FString\", NULL};",
        "    const char* base_name = name;",
        "    for (int i = 0; prefixes[i]; i++) {",
        "        size_t plen = strlen(prefixes[i]);",
        "        if (strncmp(name, prefixes[i], plen) == 0) {",
        "            base_name = name + plen;",
        "            break;",
        "        }",
        "    }",
        "    char buf[256];",
        "    for (int i = 0; prefixes[i]; i++) {",
        "        snprintf(buf, sizeof(buf), \"%s%s\", prefixes[i], base_name);",
        "        low = 0; high = (int)kMasterFlagsCount - 1;",
        "        while (low <= high) {",
        "            int mid = low + (high - low) / 2;",
        "            int cmp = strcmp(buf, kMasterFlags[mid].name);",
        "            if (cmp == 0) return &kMasterFlags[mid];",
        "            if (cmp < 0) high = mid - 1;",
        "            else low = mid + 1;",
        "        }",
        "    }",
        "    return NULL;",
        "}",
        "",
        "static char dynamic_ascii_lower(char c) {",
        "    return (c >= 'A' && c <= 'Z') ? (char)(c + ('a' - 'A')) : c;",
        "}",
        "",
        "static bool dynamic_text_equals_ci(const char* value, const char* expected) {",
        "    if (!value || !expected) return false;",
        "    while (*value && *expected) {",
        "        if (dynamic_ascii_lower(*value) != dynamic_ascii_lower(*expected)) return false;",
        "        value++;",
        "        expected++;",
        "    }",
        "    return *value == '\\0' && *expected == '\\0';",
        "}",
        "",
        "static bool parse_dynamic_scalar_text(const char* value, int64_t* number, int* kind) {",
        "    if (!value || !*value || !number || !kind) return false;",
        "    const char* true_aliases[] = {\"true\", \"t\", \"yes\", \"y\", \"on\", \"enable\", \"enabled\", NULL};",
        "    const char* false_aliases[] = {\"false\", \"f\", \"no\", \"n\", \"off\", \"disable\", \"disabled\", NULL};",
        "    for (int i = 0; true_aliases[i]; i++) {",
        "        if (dynamic_text_equals_ci(value, true_aliases[i])) {",
        "            *number = 1;",
        "            *kind = 0;",
        "            return true;",
        "        }",
        "    }",
        "    for (int i = 0; false_aliases[i]; i++) {",
        "        if (dynamic_text_equals_ci(value, false_aliases[i])) {",
        "            *number = 0;",
        "            *kind = 0;",
        "            return true;",
        "        }",
        "    }",
        "    const char* p = value;",
        "    if (*p == '+' || *p == '-') p++;",
        "    if (!*p) return false;",
        "    while (*p) {",
        "        if (*p < '0' || *p > '9') return false;",
        "        p++;",
        "    }",
        "    *number = atoll(value);",
        "    *kind = 1;",
        "    return true;",
        "}",
        "",
        "static int parse_and_apply_dynamic_json(uintptr_t base, const char* json_text, void* log_file) {",
        "    if (!json_text || !base) return 0;",
        "    int applied = 0;",
        "    const char* p = json_text;",
        "    while (*p) {",
        "        while (*p && *p != '\"') p++;",
        "        if (!*p) break;",
        "        p++;",
        "        char key[256] = {0};",
        "        int klen = 0;",
        "        while (*p && *p != '\"' && klen < 255) {",
        "            key[klen++] = *p++;",
        "        }",
        "        if (*p != '\"') break;",
        "        p++;",
        "        while (*p && *p != ':' && *p != '}' && *p != ']') p++;",
        "        if (*p != ':') break;",
        "        p++;",
        "        while (*p && (*p == ' ' || *p == '\\t' || *p == '\\r' || *p == '\\n')) p++;",
        "        if (!*p) break;",
        "        int64_t num_val = 0;",
        "        int val_kind = 1;",
        "        char scalar[64] = {0};",
        "        int scalar_len = 0;",
        "        if (*p == '\"') {",
        "            p++;",
        "            while (*p && *p != '\"' && scalar_len < 63) scalar[scalar_len++] = *p++;",
        "            if (*p != '\"') {",
        "                if (log_file) fprintf(log_file, \"[DYNAMIC_JSON_INVALID] %s (unterminated quoted value)\\n\", key);",
        "                break;",
        "            }",
        "            p++;",
        "        } else {",
        "            while (*p && *p != ',' && *p != '}' && *p != ']'",
        "                   && *p != ' ' && *p != '\\t' && *p != '\\r' && *p != '\\n'",
        "                   && scalar_len < 63) {",
        "                scalar[scalar_len++] = *p++;",
        "            }",
        "        }",
        "        scalar[scalar_len] = '\\0';",
        "        if (!parse_dynamic_scalar_text(scalar, &num_val, &val_kind)) {",
        "            if (log_file) fprintf(log_file, \"[DYNAMIC_JSON_UNSUPPORTED] %s (non boolean/integer value)\\n\", key);",
        "            continue;",
        "        }",
        "        const MasterFlagEntry* flag = lookup_flag_with_fallback(key);",
        "        if (flag) {",
        "            FFlagPatchEntry entry;",
        "            entry.name = flag->name;",
            "            entry.rva = (uintptr_t)flag->rva;",
            "            entry.kind = flag->kind; // Storage width comes from the verified map, not JSON syntax.",
            "            entry.value = num_val;",
            "            bool ok = SetFastFlag(base, &entry);",
            "            if (ok) cache_dynamic_flag(flag, entry.kind, entry.value);",
        "            if (ok) applied++;",
        "            if (log_file) {",
        "                fprintf(log_file, \"[DYNAMIC_JSON] (SetFastFlag) %s @ 0x%lx (RVA 0x%x) = %lld (Status: %s)\\n\",",
        "                        flag->name, (unsigned long)(base + flag->rva), flag->rva,",
        "                        (long long)num_val, ok ? \"VERIFIED\" : \"FAIL\");",
        "            }",
        "        } else {",
        "            if (log_file) {",
        "                fprintf(log_file, \"[DYNAMIC_JSON_MISS] %s (Not found in FFlags map)\\n\", key);",
        "            }",
        "        }",
        "    }",
        "    return applied;",
        "}",
        "",
        "static int load_dynamic_config_files(uintptr_t base, void* log_file) {",
        "    if (g_session_payload) {",
        "        long size = 0;",
        "        char* json = read_profile_file(\"ClientAppSettings.json\", &size);",
        "        g_cached_dynamic_count = 0;",
        "        if (!json) return 0;",
        "        if (log_file) fprintf(log_file, \"[CONFIG_SOURCE] request=%s source=BRIDGE_SNAPSHOT bytes=%ld\\n\", g_session_request, size);",
        "        int applied = parse_and_apply_dynamic_json(base, json, log_file);",
        "        free(json);",
        "        return applied;",
        "    }",
        "    const char* home = getenv(\"HOME\");",
        "    if (!home) return 0;",
        "    const char* candidate_subpaths[] = {",
        "        \"/Documents/ClientAppSettings.json\",",
        "        \"/Documents/ClientSettings/ClientAppSettings.json\",",
        "        \"/Documents/masterstrap_flags.json\",",
        "        \"/Documents/masterstrap_baked_flags.json\",",
        "        NULL",
        "    };",
        "    char full_path[512];",
        "    if (home) {",
        "        for (int i = 0; candidate_subpaths[i]; i++) {",
        "            snprintf(full_path, sizeof(full_path), \"%s%s\", home, candidate_subpaths[i]);",
        "            void* f = fopen(full_path, \"r\");",
        "            if (f) {",
        "                fseek(f, 0, 2);",
        "                long fsize = ftell(f);",
        "                fseek(f, 0, 0);",
        "                if (fsize > 0 && fsize < 1048576) {",
        "                    char* buf = (char*)malloc(fsize + 1);",
        "                    if (buf) {",
        "                        size_t read_bytes = fread(buf, 1, fsize, f);",
        "                        buf[read_bytes] = '\\0';",
        "                        fclose(f);",
        "                        if (log_file) {",
        "                            fprintf(log_file, \"\\n--- [Dynamic JSON Config Loaded from %s (%ld bytes)] ---\\n\", candidate_subpaths[i], fsize);",
        "                        }",
        "                        g_cached_dynamic_count = 0;",
        "                        syslog(LOG_NOTICE, \"[Masterstrap-iOS] Parsing dynamic JSON from %s (%ld bytes)\", candidate_subpaths[i], fsize);",
        "                        int count = parse_and_apply_dynamic_json(base, buf, log_file);",
        "                        free(buf);",
        "                        if (log_file) {",
        "                            fprintf(log_file, \"Total Dynamically Applied Flags: %d\\n\\n\", count);",
        "                        }",
        "                        return count;",
        "                    }",
        "                }",
        "                fclose(f);",
        "            }",
        "        }",
        "    }",
        "    id NSBundle = objc_getClass(\"NSBundle\");",
        "    if (NSBundle) {",
        "        id bundle = ((id (*)(id, SEL))objc_msgSend)(NSBundle, sel_registerName(\"mainBundle\"));",
        "        if (bundle) {",
        "            id bundle_path_ns = ((id (*)(id, SEL))objc_msgSend)(bundle, sel_registerName(\"bundlePath\"));",
        "            if (bundle_path_ns) {",
        "                const char* bundle_path = ((const char* (*)(id, SEL))objc_msgSend)(bundle_path_ns, sel_registerName(\"UTF8String\"));",
        "                if (bundle_path) {",
        "                    const char* bundle_subpaths[] = {",
        "                        \"/ClientSettings/ClientAppSettings.json\",",
        "                        \"/masterstrap_baked_flags.json\",",
        "                        NULL",
        "                    };",
        "                    for (int j = 0; bundle_subpaths[j]; j++) {",
        "                        snprintf(full_path, sizeof(full_path), \"%s%s\", bundle_path, bundle_subpaths[j]);",
        "                        void* fb = fopen(full_path, \"r\");",
        "                        if (fb) {",
        "                            fseek(fb, 0, 2);",
        "                            long fsize = ftell(fb);",
        "                            fseek(fb, 0, 0);",
        "                            if (fsize > 0 && fsize < 1048576) {",
        "                                char* buf = (char*)malloc(fsize + 1);",
        "                                if (buf) {",
        "                                    size_t read_bytes = fread(buf, 1, fsize, fb);",
        "                                    buf[read_bytes] = '\\0';",
        "                                    fclose(fb);",
        "                                    if (log_file) {",
        "                                        fprintf(log_file, \"\\n--- [Dynamic JSON Config Loaded from bundle %s (%ld bytes)] ---\\n\", bundle_subpaths[j], fsize);",
        "                                    }",
        "                                    g_cached_dynamic_count = 0;",
        "                                    int count = parse_and_apply_dynamic_json(base, buf, log_file);",
        "                                    free(buf);",
        "                                    if (log_file) {",
        "                                        fprintf(log_file, \"Total Dynamically Applied Flags: %d\\n\\n\", count);",
        "                                    }",
        "                                    return count;",
        "                                }",
        "                            }",
        "                            fclose(fb);",
        "                        }",
        "                    }",
        "                }",
        "            }",
        "        }",
        "    }",
        "    return 0;",
        "}",
        "",
        "static bool g_profile_loaded = false;",
        "static uintptr_t g_engine_base = 0;",
        "static bool g_game_mode_enabled = false;",
        "static bool g_refresh_requested = true;",
        "static bool g_keep_display_active = false;",
        "static int g_target_fps = 10000;",
        "static int g_stretch_percent = 100;",
        "static char g_renderer_mode[32] = \"Automatic\";",
        "static char g_shader_package[64] = \"none\";",
        "static bool g_shader_wet_floor = false;",
        "static char g_font_family[64] = \"Default\";",
        "static bool g_font_enabled = false;",
        "static int g_font_size_step = 0;",
        "bool g_launch_authorized = false;",
        "",
        "static void runtime_note(const char* fmt, ...);",
        "",
        "static const char* profile_find_value(const char* json, const char* key) {",
        "    if (!json || !key) return NULL;",
        "    char needle[128];",
        "    snprintf(needle, sizeof(needle), \"\\\"%s\\\"\", key);",
        "    const char* found = strstr(json, needle);",
        "    if (!found) return NULL;",
        "    found += strlen(needle);",
        "    while (*found && *found != ':') found++;",
        "    if (*found != ':') return NULL;",
        "    found++;",
        "    while (*found == ' ' || *found == '\\t' || *found == '\\r' || *found == '\\n') found++;",
        "    return found;",
        "}",
        "",
        "static bool profile_read_bool(const char* json, const char* first, const char* second, bool fallback) {",
        "    const char* value = profile_find_value(json, first);",
        "    if (!value && second) value = profile_find_value(json, second);",
        "    if (!value) return fallback;",
        "    char token[48]; size_t length = 0;",
        "    bool quoted = (unsigned char)*value == 34; if (quoted) value++;",
        "    while (*value && length + 1 < sizeof(token)) {",
        "        if ((quoted && (unsigned char)*value == 34) || (!quoted && (*value == ',' || *value == '}' || *value == ' ' || *value == '\\n' || *value == '\\r' || *value == '\\t'))) break;",
        "        token[length++] = *value++;",
        "    }",
        "    if (length + 1 == sizeof(token)) return fallback;",
        "    token[length] = '\\0';",
        "    int64_t number = 0; int kind = 0;",
        "    if (!parse_dynamic_scalar_text(token, &number, &kind) || (number != 0 && number != 1)) return fallback;",
        "    return number != 0;",
        "}",
        "",
        "static int profile_read_int(const char* json, const char* first, const char* second, int fallback) {",
        "    const char* value = profile_find_value(json, first);",
        "    if (!value && second) value = profile_find_value(json, second);",
        "    if (!value) return fallback;",
        "    if ((unsigned char)*value == 34) value++;",
        "    long long parsed = atoll(value);",
        "    if (parsed < 1 || parsed > 9999) return fallback;",
        "    return (int)parsed;",
        "}",
        "",
        "static int profile_read_signed_int(const char* json, const char* key, int fallback) {",
        "    const char* value = profile_find_value(json, key);",
        "    if (!value) return fallback;",
        "    if ((unsigned char)*value == 34) value++;",
        "    long long parsed = atoll(value);",
        "    if (parsed < -16 || parsed > 16) return fallback;",
        "    return (int)parsed;",
        "}",
        "",
        "static bool profile_read_string(const char* json, const char* first, const char* second, char* output, size_t capacity) {",
        "    const char* value = profile_find_value(json, first);",
        "    if (!value && second) value = profile_find_value(json, second);",
        "    if (!value || (unsigned char)*value != 34 || !output || capacity < 2) return false;",
        "    value++;",
        "    size_t count = 0;",
        "    while (value[count] && (unsigned char)value[count] != 34 && count + 1 < capacity) {",
        "        output[count] = value[count];",
        "        count++;",
        "    }",
        "    output[count] = '\\0';",
        "    return (unsigned char)value[count] == 34;",
        "}",
        "",
        "static char* read_profile_file(const char* file_name, long* size_out) {",
        "    if (g_session_payload) {",
        "        const char* names[] = {\"ClientAppSettings.json\", \"MasterstrapTurboProfile.json\", \"GlobalBasicSettings_13.xml\", \"MasterstrapShaderProfile.json\", \"MasterstrapFontProfile.json\"};",
        "        const char* keys[] = {\"clientAppSettings\", \"turboProfile\", \"globalSettings\", \"shaderProfile\", \"fontProfile\"};",
        "        for (int i = 0; i < 5; i++) {",
        "            if (strcmp(file_name, names[i]) != 0) continue;",
        "            id value = ((id (*)(id, SEL, id))objc_msgSend)(g_session_payload, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(keys[i]));",
        "            const char* text = bridge_utf8(value);",
        "            size_t size = strlen(text);",
        "            if (!size || size > 524288) return NULL;",
        "            char* copy = malloc(size + 1);",
        "            if (!copy) return NULL;",
        "            for (size_t n = 0; n <= size; n++) copy[n] = text[n];",
        "            if (size_out) *size_out = (long)size;",
        "            return copy;",
        "        }",
        "    }",
        "    const char* home = getenv(\"HOME\");",
        "    if (!file_name || !home) return NULL;",
        "    char path[768];",
        "    snprintf(path, sizeof(path), \"%s/Documents/%s\", home, file_name);",
        "    void* file = fopen(path, \"r\");",
        "    if (!file) {",
        "        snprintf(path, sizeof(path), \"%s/Documents/ClientSettings/%s\", home, file_name);",
        "        file = fopen(path, \"r\");",
        "    }",
        "    if (!file) return NULL;",
        "    fseek(file, 0, 2);",
        "    long size = ftell(file);",
        "    fseek(file, 0, 0);",
        "    if (size <= 0 || size > 524288) { fclose(file); return NULL; }",
        "    char* content = (char*)malloc((size_t)size + 1);",
        "    if (!content) { fclose(file); return NULL; }",
        "    size_t read_bytes = fread(content, 1, (size_t)size, file);",
        "    fclose(file);",
        "    content[read_bytes] = '\\0';",
        "    if (size_out) *size_out = (long)read_bytes;",
        "    return content;",
        "}",
        "",
        "static bool local_launch_authorization_valid(void* log_file) {",
        "    for (int attempt = 0; attempt < 5; attempt++) {",
        "        {",
        "            SESSION_LOCK;",
        "            if (g_session_payload && g_session_request[0]) {",
        "                if (log_file) fprintf(log_file, \"[SESSION][AUTH] request=%s status=VALID source=MEMORY_SNAPSHOT\\n\", g_session_request);",
        "                return true;",
        "            }",
        "        }",
        "        usleep(100000);",
        "    }",
        "    runtime_note(\"[SESSION][AUTH] status=MISSING decision=BLOCK_DIRECT_LAUNCH\");",
        "    return false;",
        "}",
        "static int purge_local_launch_configuration(const char* reason, bool write_receipt) {",
        "    const char* home = getenv(\"HOME\");",
        "    if (!home) return 0;",
        "    const char* paths[] = {",
        "        \"/Documents/ClientAppSettings.json\",",
        "        \"/Documents/ClientSettings/ClientAppSettings.json\",",
        "        \"/Documents/masterstrap_flags.json\",",
        "        \"/Documents/masterstrap_baked_flags.json\",",
        "        \"/Documents/MasterstrapTurboProfile.json\",",
        "        \"/Documents/MasterstrapShaderProfile.json\",",
        "        \"/Documents/MasterstrapFontProfile.json\",",
        "        \"/Documents/ClientSettings/MasterstrapTurboProfile.json\",",
        "        \"/Documents/ClientSettings/MasterstrapShaderProfile.json\",",
        "        \"/Documents/ClientSettings/MasterstrapFontProfile.json\",",
        "        \"/Documents/MasterstrapLaunchAuthorization.json\",",
        "        NULL",
        "    };",
        "    int deleted = 0;",
        "    char path[768];",
        "    for (int i = 0; paths[i]; i++) {",
        "        snprintf(path, sizeof(path), \"%s%s\", home, paths[i]);",
        "        if (unlink(path) == 0) { deleted++; runtime_note(\"[SESSION][PURGE] file=%s status=DELETED\", paths[i]); }",
        "    }",
        "    runtime_note(\"[SESSION][CONSUME] reason=%s cachedFlags=%zu deletedFiles=%d status=CONSUMED\", reason ? reason : \"unknown\", g_cached_dynamic_count, deleted);",
        "    if (write_receipt) {",
        "        snprintf(path, sizeof(path), \"%s/Documents/MasterstrapConsumedReceipt.json\", home);",
        "        void* receipt = fopen(path, \"w\");",
        "        if (receipt) {",
        "            fprintf(receipt, \"{\\\"schema\\\":1,\\\"consumedAtUnix\\\":%ld,\\\"reason\\\":\\\"%s\\\",\\\"request\\\":\\\"%s\\\",\\\"source\\\":\\\"%s\\\",\\\"applyCount\\\":%u,\\\"cachedFlags\\\":%zu,\\\"deletedFiles\\\":%d,\\\"reusable\\\":false}\\n\", time(NULL), reason ? reason : \"unknown\", g_session_request, g_session_payload ? \"BRIDGE_SNAPSHOT\" : \"BASELINE\", g_session_apply_count, g_cached_dynamic_count, deleted);",
        "            fflush(receipt); fclose(receipt);",
        "        }",
        "    }",
        "    return deleted;",
        "}",
        "",
        "static void load_and_apply_runtime_profiles(void* log_file) {",
        "    // Every new request replaces the previous profile, including missing fields.",
        "    g_profile_loaded = g_game_mode_enabled = g_refresh_requested = g_keep_display_active = false;",
        "    g_target_fps = 10000; g_stretch_percent = 80; g_shader_wet_floor = false; g_font_size_step = 0; g_font_enabled = false;",
        "    snprintf(g_renderer_mode, sizeof(g_renderer_mode), \"Automatic\");",
        "    snprintf(g_shader_package, sizeof(g_shader_package), \"none\");",
        "    snprintf(g_font_family, sizeof(g_font_family), \"Default\");",
        "    long turbo_size = 0;",
        "    char* turbo = read_profile_file(\"MasterstrapTurboProfile.json\", &turbo_size);",
        "    g_profile_loaded = turbo != NULL;",
        "    if (turbo) {",
        "        g_game_mode_enabled = profile_read_bool(turbo, \"enabled\", \"game_mode_master\", false);",
        "        g_refresh_requested = profile_read_bool(turbo, \"displayRefreshContract\", \"turbo_refresh_contract\", false);",
        "        g_keep_display_active = profile_read_bool(turbo, \"keepDisplayActive\", \"keep_display_active\", false);",
        "        g_target_fps = profile_read_int(turbo, \"targetFrameRate\", \"targetFps\", 10000);",
        "        g_stretch_percent = profile_read_int(turbo, \"stretchScreenPercent\", \"display_stretch_screen\", 80);",
        "        profile_read_string(turbo, \"rendererMode\", \"display_renderer_mode\", g_renderer_mode, sizeof(g_renderer_mode));",
        "        if (log_file) fprintf(log_file, \"[PROFILE][TURBO] status=LOADED bytes=%ld enabled=%d targetFps=%d refresh=%d keepDisplay=%d stretch=%d renderer=%s\\n\", turbo_size, g_game_mode_enabled, g_target_fps, g_refresh_requested, g_keep_display_active, g_stretch_percent, g_renderer_mode);",
        "        runtime_note(\"[PROFILE][TURBO] status=LOADED bytes=%ld enabled=%d targetFps=%d refresh=%d keepDisplay=%d stretch=%d renderer=%s\", turbo_size, g_game_mode_enabled, g_target_fps, g_refresh_requested, g_keep_display_active, g_stretch_percent, g_renderer_mode);",
        "        free(turbo);",
        "    } else {",
        "        if (log_file) fprintf(log_file, \"[PROFILE][TURBO] status=MISSING\\n\");",
        "        runtime_note(\"[PROFILE][TURBO] status=MISSING defaults=ACTIVE\");",
        "    }",
        "",
        "    long shader_size = 0;",
        "    char* shader = read_profile_file(\"MasterstrapShaderProfile.json\", &shader_size);",
        "    if (shader) {",
        "        profile_read_string(shader, \"package\", \"display_shader_package\", g_shader_package, sizeof(g_shader_package));",
        "        g_shader_wet_floor = profile_read_bool(shader, \"wetFloor\", \"display_shader_wet_floor\", false);",
        "        if (log_file) fprintf(log_file, \"[PROFILE][SHADER] status=LOADED bytes=%ld package=%s wetFloor=%d enabled=%d\\n\", shader_size, g_shader_package, g_shader_wet_floor, profile_read_bool(shader, \"enabled\", NULL, false));",
        "        runtime_note(\"[PROFILE][SHADER] status=LOADED bytes=%ld package=%s wetFloor=%d enabled=%d\", shader_size, g_shader_package, g_shader_wet_floor, profile_read_bool(shader, \"enabled\", NULL, false));",
        "        free(shader);",
        "    } else { if (log_file) fprintf(log_file, \"[PROFILE][SHADER] status=MISSING\\n\"); runtime_note(\"[PROFILE][SHADER] status=MISSING\"); }",
        "",
        "    long font_size = 0;",
        "    char* font = read_profile_file(\"MasterstrapFontProfile.json\", &font_size);",
        "    if (font) {",
        "        g_font_enabled = profile_read_bool(font, \"enabled\", NULL, false);",
        "        profile_read_string(font, \"font\", NULL, g_font_family, sizeof(g_font_family));",
        "        g_font_size_step = profile_read_signed_int(font, \"sizeStep\", 0);",
        "        if (log_file) fprintf(log_file, \"[PROFILE][FONT] status=LOADED bytes=%ld family=%s sizeStep=%d enabled=%d\\n\", font_size, g_font_family, g_font_size_step, profile_read_bool(font, \"enabled\", NULL, false));",
        "        runtime_note(\"[PROFILE][FONT] status=LOADED bytes=%ld family=%s sizeStep=%d enabled=%d\", font_size, g_font_family, g_font_size_step, profile_read_bool(font, \"enabled\", NULL, false));",
        "        free(font);",
        "    } else { if (log_file) fprintf(log_file, \"[PROFILE][FONT] status=MISSING\\n\"); runtime_note(\"[PROFILE][FONT] status=MISSING\"); }",
        "    runtime_note(\"[LIFECYCLE] profilesLoaded=1 policy=REAPPLY_ON_BRIDGE_AND_PROCESS_START\");",
        "}",
        "",
        "static void bridge_log(bool is_error, const char* fmt, ...) {",
        "    char message[1024];",
        "    __builtin_va_list args;",
        "    __builtin_va_start(args, fmt);",
        "    vsnprintf(message, sizeof(message), fmt, args);",
        "    __builtin_va_end(args);",
        "    syslog(is_error ? LOG_ERR : LOG_NOTICE, \"[Masterstrap-Bridge] %s\", message);",
        "    const char* home = getenv(\"HOME\");",
        "    if (!home) return;",
        "    char path[768];",
        "    snprintf(path, sizeof(path), \"%s/Documents/masterstrap_bridge.log\", home);",
        "    void* file = fopen(path, \"a+\");",
        "    if (file) {",
        "        fseek(file, 0, 2);",
        "        long size = ftell(file);",
        "        if (size > 524288) {",
        "            fclose(file);",
        "            file = fopen(path, \"w\");",
        "            if (file) fprintf(file, \"[%ld] [INFO] log rotated after %ld bytes\\n\", time(NULL), size);",
        "        }",
        "        if (file) {",
        "            fprintf(file, \"[%ld] [%s] %s\\n\", time(NULL), is_error ? \"ERROR\" : \"INFO\", message);",
        "            fflush(file);",
        "            fclose(file);",
        "        }",
        "    }",
        "}",
        "",
        "static void runtime_note(const char* fmt, ...) {",
        "    const char* home = getenv(\"HOME\");",
        "    if (!home) return;",
        "    char path[768];",
        "    char message[1024];",
        "    __builtin_va_list args;",
        "    __builtin_va_start(args, fmt);",
        "    vsnprintf(message, sizeof(message), fmt, args);",
        "    __builtin_va_end(args);",
        "    snprintf(path, sizeof(path), \"%s/Documents/masterstrap_ios.log\", home);",
        "    void* file = fopen(path, \"a+\");",
        "    if (file) {",
        "        fseek(file, 0, 2);",
        "        long size = ftell(file);",
        "        if (size > 1048576) {",
        "            fclose(file);",
        "            file = fopen(path, \"w\");",
        "            if (file) fprintf(file, \"[%ld] [RUNTIME] log rotated after %ld bytes\\n\", time(NULL), size);",
        "        }",
        "        if (file) {",
        "            fprintf(file, \"[%ld] [RUNTIME] %s\\n\", time(NULL), message);",
        "            fflush(file);",
        "            fclose(file);",
        "        }",
        "    }",
        "}",
        "",
        "static const OffsetEntry* find_engine_offset(const char* name) {",
        "    for (size_t i = 0; i < kOffsetsCount; i++) {",
        "        if (strcmp(kVerifiedOffsets[i].name, name) == 0) return &kVerifiedOffsets[i];",
        "    }",
        "    return NULL;",
        "}",
        "",
        "static int offset_role_count(uintptr_t rva) {",
        "    int count = 0;",
        "    for (size_t i = 0; i < kOffsetsCount; i++) if (kVerifiedOffsets[i].rva == rva) count++;",
        "    return count;",
        "}",
        "",
        "static bool macho_rva_is_executable(uintptr_t base, uintptr_t rva) {",
        "    if (!base || !rva) return false;",
        "    const MachHeader64* header = (const MachHeader64*)base;",
        "    if (header->magic != MH_MAGIC_64 || header->ncmds == 0 || header->ncmds > 4096 || header->sizeofcmds > 4194304) return false;",
        "    const uint8_t* first = (const uint8_t*)(header + 1);",
        "    const uint8_t* end = first + header->sizeofcmds;",
        "    const uint8_t* cursor = first;",
        "    uint64_t image_vmaddr = 0;",
        "    bool image_vmaddr_found = false;",
        "    for (uint32_t i = 0; i < header->ncmds; i++) {",
        "        if (cursor + sizeof(MachLoadCommand) > end) return false;",
        "        const MachLoadCommand* command = (const MachLoadCommand*)cursor;",
        "        if (command->cmdsize < sizeof(MachLoadCommand) || cursor + command->cmdsize > end) return false;",
        "        if (command->cmd == LC_SEGMENT_64 && command->cmdsize >= sizeof(MachSegment64)) {",
        "            const MachSegment64* segment = (const MachSegment64*)cursor;",
        "            if (segment->fileoff == 0 && segment->filesize != 0) { image_vmaddr = segment->vmaddr; image_vmaddr_found = true; break; }",
        "        }",
        "        cursor += command->cmdsize;",
        "    }",
        "    if (!image_vmaddr_found) return false;",
        "    uint64_t target_vmaddr = image_vmaddr + (uint64_t)rva;",
        "    cursor = first;",
        "    for (uint32_t i = 0; i < header->ncmds; i++) {",
        "        if (cursor + sizeof(MachLoadCommand) > end) return false;",
        "        const MachLoadCommand* command = (const MachLoadCommand*)cursor;",
        "        if (command->cmdsize < sizeof(MachLoadCommand) || cursor + command->cmdsize > end) return false;",
        "        if (command->cmd == LC_SEGMENT_64 && command->cmdsize >= sizeof(MachSegment64)) {",
        "            const MachSegment64* segment = (const MachSegment64*)cursor;",
        "            if ((segment->initprot & PROT_EXEC) && target_vmaddr >= segment->vmaddr && target_vmaddr + sizeof(uint32_t) <= segment->vmaddr + segment->vmsize) return true;",
        "        }",
        "        cursor += command->cmdsize;",
        "    }",
        "    return false;",
        "}",
        "",
        "static bool arm64_opcode_is_plausible(uint32_t opcode) {",
        "    if (opcode == 0 || opcode == 0xFFFFFFFF) return false;",
        "    if ((opcode & 0xFFE0001F) == 0xD4200000) return false; /* BRK */",
        "    return true;",
        "}",
        "",
        "static void run_engine_abi_probe(uintptr_t base, void* log_file) {",
        "    static const char* critical_roles[] = {",
        "        \"luau_load\", \"luau_bytecodeload\", \"lua_resume\", \"lua_gettop\", \"lua_settop\",",
        "        \"lua_pushvalue\", \"lua_type\", \"lua_getfield\", \"lua_setfield\", \"lua_pushstring\",",
        "        \"lua_pushlstring\", \"lua_tostring\", \"lua_toboolean\", \"lua_next\",",
        "        \"scriptcontextresume\", \"resumewaitingscripts\", \"game_loaded\", \"ongameloaded\",",
        "        \"loadstring\", \"enableloadmodule\", NULL",
        "    };",
        "    g_abi_probe_total = g_abi_probe_executable = g_abi_probe_plausible = g_abi_probe_duplicate_roles = 0;",
        "    g_abi_probe_call_gate_open = 0; /* A valid address cannot prove the C ABI/signature. */",
        "    const char* home = getenv(\"HOME\");",
        "    char receipt_path[768] = {0};",
        "    void* receipt = NULL;",
        "    if (home) { snprintf(receipt_path, sizeof(receipt_path), \"%s/Documents/MasterstrapAbiProbe.json\", home); receipt = fopen(receipt_path, \"w\"); }",
        "    if (receipt) fprintf(receipt, \"{\\\"schema\\\":1,\\\"generatedAtUnix\\\":%ld,\\\"baseFound\\\":%s,\\\"callGate\\\":\\\"closed\\\",\\\"roles\\\":[\", time(NULL), base ? \"true\" : \"false\");",
        "    bool first_receipt = true;",
        "    for (size_t role_index = 0; critical_roles[role_index]; role_index++) {",
        "        const char* role = critical_roles[role_index];",
        "        const OffsetEntry* entry = find_engine_offset(role);",
        "        g_abi_probe_total++;",
        "        bool executable = entry && macho_rva_is_executable(base, entry->rva);",
        "        uint32_t opcode = 0; /* Do not dereference code memory at runtime to prevent EXC_BAD_ACCESS */",
        "        bool plausible = executable;",
        "        int duplicates = entry ? offset_role_count(entry->rva) : 0;",
        "        if (executable) g_abi_probe_executable++;",
        "        if (plausible) g_abi_probe_plausible++;",
        "        if (duplicates > 1) g_abi_probe_duplicate_roles++;",
        "        const char* status = !entry ? \"MISSING\" : !executable ? \"NON_EXECUTABLE\" : duplicates > 1 ? \"AMBIGUOUS_RVA\" : \"ADDRESS_PLAUSIBLE\";",
        "        if (log_file) fprintf(log_file, \"[ABI_PROBE] role=%s rva=0x%lx executable=%d opcode=0x%08X aliases=%d status=%s callSafe=0\\n\", role, (unsigned long)(entry ? entry->rva : 0), executable, opcode, duplicates, status);",
        "        runtime_note(\"[ABI_PROBE] role=%s rva=0x%lx executable=%d opcode=0x%08X aliases=%d status=%s callSafe=0\", role, (unsigned long)(entry ? entry->rva : 0), executable, opcode, duplicates, status);",
        "        if (receipt) {",
        "            fprintf(receipt, \"%s{\\\"role\\\":\\\"%s\\\",\\\"rva\\\":\\\"0x%lx\\\",\\\"executable\\\":%s,\\\"opcode\\\":\\\"0x%08X\\\",\\\"aliases\\\":%d,\\\"status\\\":\\\"%s\\\",\\\"callSafe\\\":false}\", first_receipt ? \"\" : \",\", role, (unsigned long)(entry ? entry->rva : 0), executable ? \"true\" : \"false\", opcode, duplicates, status);",
        "            first_receipt = false;",
        "        }",
        "    }",
        "    if (receipt) { fprintf(receipt, \"],\\\"summary\\\":{\\\"total\\\":%d,\\\"executable\\\":%d,\\\"plausible\\\":%d,\\\"ambiguousRoles\\\":%d}}\\n\", g_abi_probe_total, g_abi_probe_executable, g_abi_probe_plausible, g_abi_probe_duplicate_roles); fflush(receipt); fclose(receipt); }",
        "    runtime_note(\"[ABI_PROBE][SUMMARY] total=%d executable=%d plausible=%d ambiguousRoles=%d callGate=CLOSED reason=function-signatures-unverified\", g_abi_probe_total, g_abi_probe_executable, g_abi_probe_plausible, g_abi_probe_duplicate_roles);",
        "}",
        "",
        *(ROOT / "tools" / "ios_frame_policy.h").read_text(encoding="utf-8").splitlines(),
        *(ROOT / "tools" / "ios_bridge_codec.h").read_text(encoding="utf-8").splitlines(),
        *(ROOT / "tools" / "ios_method_hooks.c").read_text(encoding="utf-8").splitlines(),
        *(ROOT / "tools" / "ios_display_policy.c").read_text(encoding="utf-8").splitlines(),
        "static const struct { const char* name; const char* asset; } kFontCatalog[] = {",
        *[f"    {{{json.dumps(name)}, {json.dumps(asset)}}}," for name, asset in sorted(font_profile.get('catalog', {}).items())],
        "    {NULL, NULL}};",
        "static const char* kStockFontFamilies[] = {",
        *[f"    {json.dumps(name)}," for name in font_profile.get('stockFamilies', [])],
        "    NULL};",
        "static const struct { uintptr_t rva; int kind; } kFontReaderSlots[] = {",
        *[f"    {{0x{slot['rva']:x}, {slot['kind']}}}," for slot in font_profile.get('readerSlots', [])],
        "    {0, 0}};",
        *(ROOT / "tools" / "ios_font_assets.c").read_text(encoding="utf-8").splitlines(),
        "",
        "static void log_runtime_consumer_contract(void) {",
        "    runtime_note(\"[REQUEST][FONT] family=%s sizeStep=%d\", g_font_family, g_font_size_step);",
        "    runtime_note(\"[CAPABILITY][FONT] backend=%s imports=%u familyEffect=REQUIRES_DEVICE_READBACK sizeEffect=UNSUPPORTED_KEEP_ORIGINAL\", kFontRuntimeBackend, g_font_imports_installed);",
        "    runtime_note(\"[REQUEST][SHADER] package=%s wetFloor=%d\", g_shader_package, g_shader_wet_floor);",
        "    runtime_note(\"[CAPABILITY][SHADER] backend=%s status=BLOCKED reason=address-probe-only-luau-lifecycle-unverified abiPlausible=%d/%d\", kShaderRuntimeBackend, g_abi_probe_plausible, g_abi_probe_total);",
        "    runtime_note(\"[REQUEST][STRETCH] percent=%d\", g_stretch_percent);",
        "    runtime_note(\"[CAPABILITY][STRETCH] backend=%s status=STANDBY_FPS_ONLY hookInstalled=%d resolutionHooks=0\", kStretchRuntimeBackend, g_metal_layer_stretch_installed);",
        "    runtime_note(\"[CAPABILITY][RENDERER] requested=%s effective=Metal status=NATIVE_PLATFORM_ONLY\", g_renderer_mode);",
        "}",
        "",
        "static void write_runtime_capability_receipt(void) {",
        "    const char* home = getenv(\"HOME\");",
        "    if (!home) return;",
        "    char path[768];",
        "    snprintf(path, sizeof(path), \"%s/Documents/MasterstrapRuntimeReceipt.json\", home);",
        "    void* file = fopen(path, \"w\");",
        "    if (!file) return;",
        "    fprintf(file, \"{\\\"schema\\\":2,\\\"generatedAtUnix\\\":%ld,\\\"profileLoaded\\\":%s,\\\"request\\\":\\\"%s\\\",\\\"appliedRequest\\\":\\\"%s\\\",\\\"configSource\\\":\\\"%s\\\",\\\"applyCount\\\":%u,\\\"abiProbe\\\":{\\\"total\\\":%d,\\\"executable\\\":%d,\\\"plausible\\\":%d,\\\"ambiguousRoles\\\":%d,\\\"callGateOpen\\\":false},\\\"fps\\\":{\\\"backend\\\":\\\"%s\\\",\\\"requested\\\":%d,\\\"screenMaximum\\\":%d,\\\"hookInstalled\\\":%s},\\\"renderer\\\":{\\\"requested\\\":\\\"%s\\\",\\\"effective\\\":\\\"Metal\\\"},\\\"boost\\\":{\\\"enabled\\\":%s,\\\"status\\\":\\\"%s\\\"},\\\"font\\\":{\\\"requested\\\":\\\"%s\\\",\\\"sizeStep\\\":%d,\\\"status\\\":\\\"family_requires_device_readback\\\",\\\"sizeStatus\\\":\\\"unsupported_keep_original\\\"},\\\"shader\\\":{\\\"requested\\\":\\\"%s\\\",\\\"wetFloor\\\":%s,\\\"status\\\":\\\"blocked_abi_lifecycle\\\"},\\\"stretch\\\":{\\\"requestedPercent\\\":%d,\\\"status\\\":\\\"blocked_camera_role\\\"}}\\n\",",
        "            time(NULL), g_profile_loaded ? \"true\" : \"false\", g_session_request, g_applied_request, g_session_payload ? \"BRIDGE_SNAPSHOT\" : \"BASELINE\", g_session_apply_count,",
        "            g_abi_probe_total, g_abi_probe_executable, g_abi_probe_plausible, g_abi_probe_duplicate_roles,",
        "            kFpsRuntimeBackend, g_target_fps,",
        "            ios_maximum_frames_per_second(), g_display_link_policy_installed ? \"true\" : \"false\",",
        "            g_renderer_mode, g_game_mode_enabled ? \"true\" : \"false\",",
        "            g_game_mode_enabled ? \"partial_os_managed\" : \"inactive\",",
        "            g_font_family, g_font_size_step, g_shader_package, g_shader_wet_floor ? \"true\" : \"false\", g_stretch_percent);",
        "    fflush(file);",
        "    fclose(file);",
        "    runtime_note(\"[RECEIPT] file=MasterstrapRuntimeReceipt.json status=WRITTEN\");",
        "}",
        "",
        "static const char* bridge_utf8(id value) {",
        "    if (!value) return \"\";",
        "    id NSString = objc_getClass(\"NSString\");",
        "    if (!NSString) return \"\";",
        "    bool is_string = ((bool (*)(id, SEL, id))objc_msgSend)(",
        "        value, sel_registerName(\"isKindOfClass:\"), NSString);",
        "    if (!is_string) return \"\";",
        "    return ((const char* (*)(id, SEL))objc_msgSend)(",
        "        value, sel_registerName(\"UTF8String\"));",
        "}",
        "",
        "static id bridge_nsstr(const char* value) {",
        "    id NSString = objc_getClass(\"NSString\");",
        "    if (!NSString || !value) return NULL;",
        "    return ((id (*)(id, SEL, const char*))objc_msgSend)(",
        "        NSString, sel_registerName(\"stringWithUTF8String:\"), value);",
        "}",
        "",
        "static bool bridge_token_is_valid(const char* value, size_t min_length, size_t max_length) {",
        "    if (!value) return false;",
        "    size_t length = strlen(value);",
        "    if (length < min_length || length > max_length) return false;",
        "    for (size_t i = 0; i < length; i++) {",
        "        char c = value[i];",
        "        bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')",
        "            || (c >= '0' && c <= '9') || c == '-' || c == '_';",
        "        if (!ok) return false;",
        "    }",
        "    return true;",
        "}",
        "",
        "static id bridge_json_post(const char* endpoint, id json_body, long expected_status) {",
        "    id NSJSONSerialization = objc_getClass(\"NSJSONSerialization\");",
        "    id NSURL = objc_getClass(\"NSURL\");",
        "    id NSMutableURLRequest = objc_getClass(\"NSMutableURLRequest\");",
        "    id NSURLConnection = objc_getClass(\"NSURLConnection\");",
        "    if (!NSJSONSerialization || !NSURL || !NSMutableURLRequest || !NSURLConnection || !json_body) return NULL;",
        "    id error = NULL;",
        "    id request_data = ((id (*)(id, SEL, id, unsigned long, id*))objc_msgSend)(",
        "        NSJSONSerialization, sel_registerName(\"dataWithJSONObject:options:error:\"),",
        "        json_body, 0, &error);",
        "    if (!request_data || error) return NULL;",
        "    id url = ((id (*)(id, SEL, id))objc_msgSend)(",
        "        NSURL, sel_registerName(\"URLWithString:\"), bridge_nsstr(endpoint));",
        "    id request = url ? ((id (*)(id, SEL, id, unsigned long, double))objc_msgSend)(",
        "        NSMutableURLRequest,",
        "        sel_registerName(\"requestWithURL:cachePolicy:timeoutInterval:\"),",
        "        url, 1, 6.0) : NULL;",
        "    if (!request) return NULL;",
        "    ((void (*)(id, SEL, id))objc_msgSend)(request, sel_registerName(\"setHTTPMethod:\"), bridge_nsstr(\"POST\"));",
        "    ((void (*)(id, SEL, id, id))objc_msgSend)(request, sel_registerName(\"setValue:forHTTPHeaderField:\"),",
        "                                               bridge_nsstr(\"application/json\"), bridge_nsstr(\"Content-Type\"));",
        "    ((void (*)(id, SEL, id, id))objc_msgSend)(request, sel_registerName(\"setValue:forHTTPHeaderField:\"),",
        "                                               bridge_nsstr(\"application/json\"), bridge_nsstr(\"Accept\"));",
        "    ((void (*)(id, SEL, id))objc_msgSend)(request, sel_registerName(\"setHTTPBody:\"), request_data);",
        "    id response = NULL;",
        "    error = NULL;",
        "    id response_data = ((id (*)(id, SEL, id, id*, id*))objc_msgSend)(",
        "        NSURLConnection,",
        "        sel_registerName(\"sendSynchronousRequest:returningResponse:error:\"),",
        "        request, &response, &error);",
        "    long status = response",
        "        ? ((long (*)(id, SEL))objc_msgSend)(response, sel_registerName(\"statusCode\")) : 0;",
        "    if (!response_data || error || status != expected_status) {",
        "        bridge_log(true, \"Bridge API request failed: status=%ld\", status);",
        "        return NULL;",
        "    }",
        "    error = NULL;",
        "    id result = ((id (*)(id, SEL, id, unsigned long, id*))objc_msgSend)(",
        "        NSJSONSerialization, sel_registerName(\"JSONObjectWithData:options:error:\"),",
        "        response_data, 0, &error);",
        "    return error ? NULL : result;",
        "}",
        "",
        "static id bridge_consume_v2(const char* ticket, const char* request, const char* expected_channel) {",
        "    id NSDictionary = objc_getClass(\"NSDictionary\");",
        "    if (!NSDictionary || !bridge_token_is_valid(ticket, 43, 95)) return NULL;",
        "    id consume_body = ((id (*)(id, SEL))objc_msgSend)(objc_getClass(\"NSMutableDictionary\"), sel_registerName(\"dictionary\"));",
        "    ((void (*)(id, SEL, id, id))objc_msgSend)(consume_body, sel_registerName(\"setObject:forKey:\"), bridge_nsstr(ticket), bridge_nsstr(\"ticket\"));",
        "    ((void (*)(id, SEL, id, id))objc_msgSend)(consume_body, sel_registerName(\"setObject:forKey:\"), bridge_nsstr(request), bridge_nsstr(\"requestId\"));",
        "    ((void (*)(id, SEL, id, id))objc_msgSend)(consume_body, sel_registerName(\"setObject:forKey:\"), bridge_nsstr(expected_channel), bridge_nsstr(\"channel\"));",
        "    bridge_log(false, \"Bridge API consume started: request=%s protocol=2\", request);",
        "    id result = bridge_json_post(",
        "        \"https://bridge.masterstrap.org/v2/launch-configs/consume\", consume_body, 200);",
        "    bool is_dictionary = result && ((bool (*)(id, SEL, id))objc_msgSend)(",
        "        result, sel_registerName(\"isKindOfClass:\"), NSDictionary);",
        "    if (!is_dictionary) return NULL;",
        "    id success = ((id (*)(id, SEL, id))objc_msgSend)(",
        "        result, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"success\"));",
        "    id protocol_value = ((id (*)(id, SEL, id))objc_msgSend)(",
        "        result, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"protocol\"));",
        "    const char* response_request = bridge_utf8(((id (*)(id, SEL, id))objc_msgSend)(",
        "        result, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"requestId\")));",
        "    const char* response_channel = bridge_utf8(((id (*)(id, SEL, id))objc_msgSend)(",
        "        result, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"channel\")));",
        "    id payload = ((id (*)(id, SEL, id))objc_msgSend)(",
        "        result, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"payload\"));",
        "    bool success_ok = success && ((bool (*)(id, SEL))objc_msgSend)(success, sel_registerName(\"boolValue\"));",
        "    int protocol_number = protocol_value",
        "        ? ((int (*)(id, SEL))objc_msgSend)(protocol_value, sel_registerName(\"intValue\")) : 0;",
        "    bool payload_ok = payload && ((bool (*)(id, SEL, id))objc_msgSend)(",
        "        payload, sel_registerName(\"isKindOfClass:\"), NSDictionary);",
        "    if (!success_ok || protocol_number != 2 || strcmp(response_request, request) != 0",
        "        || strcmp(response_channel, expected_channel) != 0 || !payload_ok) {",
        "        bridge_log(true, \"Bridge API response rejected: request=%s validation failed\", request);",
        "        return NULL;",
        "    }",
        "    bridge_log(false, \"Bridge API consume complete: request=%s channel=%s\", request, expected_channel);",
        "    const char* payload_hash = bridge_utf8(((id (*)(id, SEL, id))objc_msgSend)(result, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"payloadSha256\")));",
        "    if (strncmp(ticket, \"do_\", 3) == 0 && !ms_bridge_hash_matches(payload, payload_hash)) {",
        "        bridge_log(true, \"Bridge checksum rejected: request=%s\", request); return NULL;",
        "    }",
        "    return payload;",
        "}",
        "",
        "static bool bridge_write_document(const char* file_name, const char* content) {",
        "    if (!file_name || !content || strlen(content) == 0) return false;",
        "    const char* home = getenv(\"HOME\");",
        "    if (!home) return false;",
        "    char path[768];",
        "    snprintf(path, sizeof(path), \"%s/Documents/%s\", home, file_name);",
        "    char temporary[800];",
        "    snprintf(temporary, sizeof(temporary), \"%s.tmp\", path);",
        "    void* file = fopen(temporary, \"w\");",
        "    if (!file) return false;",
        "    bool ok = fputs(content, file) >= 0;",
        "    if (fflush(file) != 0) ok = false;",
        "    if (fclose(file) != 0) ok = false;",
        "    if (ok && rename(temporary, path) != 0) ok = false;",
        "    if (!ok) unlink(temporary);",
        "    return ok;",
        "}",
        "",
        "static int apply_baseline_flags(uintptr_t base) {",
        "    (void)base; return 0; /* Only fresh launch snapshots own flags, including watchdog/direct boot. */",
        "}",
        "static int apply_pending_launch_locked(const char* reason, void* log_file) {",
        "    if (!g_runtime_ready || !g_engine_base || !g_session_payload) return 0;",
        "    if (strcmp(g_applied_request, g_session_request) == 0) return (int)g_cached_dynamic_count;",
        "    g_cached_dynamic_count = 0;",
        "    for (size_t i = 0; i < g_original_flag_count; i++) g_original_flags[i].active = false;",
        "    int user_flags = load_dynamic_config_files(g_engine_base, log_file);",
        "    load_and_apply_runtime_profiles(log_file);",
        "    const MasterFlagEntry* fps = lookup_flag_with_fallback(\"DFIntTaskSchedulerTargetFps\");",
        "    if (g_profile_loaded && fps && fps->kind == 1) {",
        "        FFlagPatchEntry entry = {fps->name, fps->rva, 1, g_target_fps};",
        "        if (SetFastFlag(g_engine_base, &entry)) cache_dynamic_flag(fps, 1, g_target_fps);",
        "    }",
        "    restore_inactive_flags(g_engine_base);",
        "    snprintf(g_applied_request, sizeof(g_applied_request), \"%s\", g_session_request);",
        "    g_launch_authorized = true;",
        "    apply_font_asset_profile(g_engine_base);",
        "    g_session_apply_count++;",
        "    install_display_link_policy();",
        "    install_metal_layer_stretch_policy();",
        "    apply_ios_process_policies();",
        "    log_runtime_consumer_contract();",
        "    purge_local_launch_configuration(reason, true);",
        "    runtime_note(\"[SESSION][APPLIED] request=%s source=BRIDGE_SNAPSHOT userFlags=%d cachedFlags=%zu applyCount=%u profileLoaded=%d targetFps=%d\", g_applied_request, user_flags, g_cached_dynamic_count, g_session_apply_count, g_profile_loaded, g_target_fps);",
        "    return user_flags;",
        "}",
        "static int complete_runtime_boot(uintptr_t base, void* log_file) {",
        "    SESSION_LOCK;",
        "    g_engine_base = base;",
        "    if (!g_runtime_ready) {",
        "        g_baseline_applied = 0;",
        "        g_runtime_ready = true;",
        "        if (!g_session_payload) apply_ios_process_policies();",
        "    }",
        "    if (g_session_payload) return apply_pending_launch_locked(\"cold_launch_applied\", log_file);",
        "    purge_local_launch_configuration(\"direct_or_stale_launch_blocked\", true);",
        "    return 0;",
        "}",
        "static char g_last_bridge_request[96] = {0};",
        "static int g_bridge_import_busy = 0;",
        "static void bridge_release_import_guard(int* ignored) { (void)ignored; __sync_lock_release(&g_bridge_import_busy); }",
        "",
        "static bool bridge_request_is_valid(const char* request) {",
        "    if (!request) return false;",
        "    size_t length = strlen(request);",
        "    if (length == 0 || length >= sizeof(g_last_bridge_request)) return false;",
        "    for (size_t i = 0; i < length; i++) {",
        "        char c = request[i];",
        "        bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')",
        "            || (c >= '0' && c <= '9') || c == '-' || c == '_';",
        "        if (!ok) return false;",
        "    }",
        "    return true;",
        "}",
        "",
        "static bool import_masterstrap_bridge_url(id url) {",
        "    if (!url) return false;",
        "    const char* scheme = bridge_utf8(((id (*)(id, SEL))objc_msgSend)(",
        "        url, sel_registerName(\"scheme\")));",
        "    if (!scheme) return false;",
        "    bool global_bridge = strcmp(scheme, \"masterstrap-rbx-bridge-v1\") == 0 || strcmp(scheme, \"robloxmobile\") == 0 || strcmp(scheme, \"roblox\") == 0;",
        "    bool vng_bridge = strcmp(scheme, \"masterstrap-rbx-vng-bridge-v1\") == 0 || strcmp(scheme, \"roblox-vng\") == 0;",
        "    if (!global_bridge && !vng_bridge) return false;",
        "",
        "    const char* host = bridge_utf8(((id (*)(id, SEL))objc_msgSend)(",
        "        url, sel_registerName(\"host\")));",
        "    if (strcmp(host, \"apply\") != 0) {",
        "        bridge_log(true, \"Rejected URL: invalid host\");",
        "        return false;",
        "    }",
        "",
        "    id NSURLComponents = objc_getClass(\"NSURLComponents\");",
        "    id components = NSURLComponents",
        "        ? ((id (*)(id, SEL, id, bool))objc_msgSend)(",
        "            NSURLComponents,",
        "            sel_registerName(\"componentsWithURL:resolvingAgainstBaseURL:\"),",
        "            url,",
        "            false)",
        "        : NULL;",
        "    id query_items = components",
        "        ? ((id (*)(id, SEL))objc_msgSend)(components, sel_registerName(\"queryItems\"))",
        "        : NULL;",
        "    unsigned long item_count = query_items",
        "        ? ((unsigned long (*)(id, SEL))objc_msgSend)(query_items, sel_registerName(\"count\"))",
        "        : 0;",
        "    id payload = NULL;",
        "    id ticket_value = NULL;",
        "    id request_value = NULL;",
        "    int protocol_version = 0;",
        "    for (unsigned long i = 0; i < item_count; i++) {",
        "        id item = ((id (*)(id, SEL, unsigned long))objc_msgSend)(",
        "            query_items, sel_registerName(\"objectAtIndex:\"), i);",
        "        const char* name = bridge_utf8(((id (*)(id, SEL))objc_msgSend)(",
        "            item, sel_registerName(\"name\")));",
        "        id value = ((id (*)(id, SEL))objc_msgSend)(item, sel_registerName(\"value\"));",
        "        if (strcmp(name, \"v\") == 0) {",
        "            const char* version = bridge_utf8(value);",
        "            if (strcmp(version, \"1\") == 0) protocol_version = 1;",
        "            else if (strcmp(version, \"2\") == 0) protocol_version = 2;",
        "            else if (strcmp(version, \"3\") == 0) protocol_version = 3;",
        "        } else if (strcmp(name, \"payload\") == 0) {",
        "            payload = value;",
        "        } else if (strcmp(name, \"ticket\") == 0) {",
        "            ticket_value = value;",
        "        } else if (strcmp(name, \"request\") == 0) {",
        "            request_value = value;",
        "        }",
        "    }",
        "",
        "    const char* request_text = bridge_utf8(request_value);",
        "    if ((protocol_version != 1 && protocol_version != 2 && protocol_version != 3) || !bridge_request_is_valid(request_text)) {",
        "        bridge_log(true, \"Rejected URL: protocol or request missing/invalid\");",
        "        return false;",
        "    }",
        "    char request[96];",
        "    snprintf(request, sizeof(request), \"%s\", request_text);",
        "    bridge_log(false, \"Importer invoked: request=%s scheme=%s host=%s\", request, scheme, host);",
        "    if (strcmp(g_last_bridge_request, request) == 0) {",
        "        bridge_log(false, \"Duplicate request ignored after successful import: request=%s\", request);",
        "        return true;",
        "    }",
        "    id body = NULL;",
        "    if (__sync_lock_test_and_set(&g_bridge_import_busy, 1)) { bridge_log(false, \"Import already in flight: request=%s\", request); return false; }",
        "    int import_guard __attribute__((cleanup(bridge_release_import_guard))) = 0;",
        "    id error = NULL;",
        "    if (protocol_version == 2) {",
        "        const char* ticket = bridge_utf8(ticket_value);",
        "        if (!bridge_token_is_valid(ticket, 43, 95)) {",
        "            bridge_log(true, \"Rejected URL: request=%s ticket missing/invalid\", request);",
        "            return false;",
        "        }",
        "        body = bridge_consume_v2(ticket, request, vng_bridge ? \"vng\" : \"global\");",
        "        if (!body) {",
        "            bridge_log(true, \"Bridge API consume failed: request=%s\", request);",
        "            return false;",
        "        }",
        "    } else {",
        "        if (!payload) {",
        "            bridge_log(true, \"Rejected URL: request=%s protocol=1 payload missing\", request);",
        "            return false;",
        "        }",
        "        unsigned long payload_length = ((unsigned long (*)(id, SEL))objc_msgSend)(",
        "            payload, sel_registerName(\"length\"));",
        "        if (payload_length == 0 || payload_length > 1048576) {",
        "            bridge_log(true, \"Rejected URL: request=%s encoded payload size=%lu out of range\", request, payload_length);",
        "            return false;",
        "        }",
        "        bridge_log(false, \"Payload accepted for decode: request=%s encodedBytes=%lu\", request, payload_length);",
        "        id NSData = objc_getClass(\"NSData\");",
        "        id data = NSData",
        "            ? ((id (*)(id, SEL, id, unsigned long))objc_msgSend)(",
        "                ((id (*)(id, SEL))objc_msgSend)(NSData, sel_registerName(\"alloc\")),",
        "                sel_registerName(\"initWithBase64EncodedString:options:\"), payload, 0)",
        "            : NULL;",
        "        unsigned long decoded_length = data",
        "            ? ((unsigned long (*)(id, SEL))objc_msgSend)(data, sel_registerName(\"length\")) : 0;",
        "        if (decoded_length == 0 || decoded_length > 524288) {",
        "            bridge_log(true, \"Rejected URL: request=%s decoded payload size=%lu out of range\", request, decoded_length);",
        "            return false;",
        "        }",
        "        if (protocol_version == 3) {",
        "            data = ms_bridge_codec(data, false);",
        "            if (!data) { bridge_log(true, \"Rejected compressed payload: request=%s invalid or exceeds 512 KiB\", request); return false; }",
        "            decoded_length = ((unsigned long (*)(id, SEL))objc_msgSend)(data, sel_registerName(\"length\"));",
        "        }",
        "        bridge_log(false, \"Payload decoded: request=%s decodedBytes=%lu protocol=%d\", request, decoded_length, protocol_version);",
        "        id NSJSONSerialization = objc_getClass(\"NSJSONSerialization\");",
        "        body = NSJSONSerialization",
        "            ? ((id (*)(id, SEL, id, unsigned long, id*))objc_msgSend)(",
        "                NSJSONSerialization, sel_registerName(\"JSONObjectWithData:options:error:\"),",
        "                data, 0, &error)",
        "            : NULL;",
        "    }",
        "    id NSDictionary = objc_getClass(\"NSDictionary\");",
        "    bool is_dictionary = body && NSDictionary",
        "        && ((bool (*)(id, SEL, id))objc_msgSend)(",
        "            body, sel_registerName(\"isKindOfClass:\"), NSDictionary);",
        "    if (!is_dictionary || error) {",
        "        bridge_log(true, \"Rejected URL: request=%s invalid JSON envelope\", request);",
        "        return false;",
        "    }",
        "    bridge_log(false, \"JSON envelope validated: request=%s\", request);",
        "",
        "    id client_value = ((id (*)(id, SEL, id))objc_msgSend)(",
        "        body, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"clientAppSettings\"));",
        "    id turbo_value = ((id (*)(id, SEL, id))objc_msgSend)(",
        "        body, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"turboProfile\"));",
        "    id global_value = ((id (*)(id, SEL, id))objc_msgSend)(",
        "        body, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"globalSettings\"));",
        "    id shader_value = ((id (*)(id, SEL, id))objc_msgSend)(",
        "        body, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"shaderProfile\"));",
        "    id font_value = ((id (*)(id, SEL, id))objc_msgSend)(",
        "        body, sel_registerName(\"objectForKeyedSubscript:\"), bridge_nsstr(\"fontProfile\"));",
        "",
        "    id values[] = {client_value, turbo_value, global_value, shader_value, font_value};",
        "    for (int i = 0; i < 5; i++) {",
        "        if (!values[i]) continue;",
        "        if (!((bool (*)(id, SEL, id))objc_msgSend)(values[i], sel_registerName(\"isKindOfClass:\"), objc_getClass(\"NSString\"))) {",
        "            bridge_log(true, \"Rejected document type: request=%s index=%d\", request, i); return false;",
        "        }",
        "        const char* text = bridge_utf8(values[i]);",
        "        if (!text[0] || i == 2) continue;",
        "        id data = ((id (*)(id, SEL, unsigned long))objc_msgSend)(values[i], sel_registerName(\"dataUsingEncoding:\"), 4);",
        "        id document = ((id (*)(id, SEL, id, unsigned long, id*))objc_msgSend)(objc_getClass(\"NSJSONSerialization\"), sel_registerName(\"JSONObjectWithData:options:error:\"), data, 0, &error);",
        "        if (!document || error || !((bool (*)(id, SEL, id))objc_msgSend)(document, sel_registerName(\"isKindOfClass:\"), NSDictionary)) {",
        "            bridge_log(true, \"Rejected document JSON: request=%s index=%d\", request, i); return false;",
        "        }",
        "    }",
        "    SESSION_LOCK;",
        "    purge_local_launch_configuration(\"bridge_replace_previous\", false);",
        "    bool wrote_client = bridge_write_document(",
        "        \"ClientAppSettings.json\", bridge_utf8(client_value));",
        "    bool wrote_turbo = bridge_write_document(",
        "        \"MasterstrapTurboProfile.json\", bridge_utf8(turbo_value));",
        "    bool wrote_global = bridge_write_document(",
        "        \"GlobalBasicSettings_13.xml\", bridge_utf8(global_value));",
        "    bool wrote_shader = bridge_write_document(",
        "        \"MasterstrapShaderProfile.json\", bridge_utf8(shader_value));",
        "    bool wrote_font = bridge_write_document(",
        "        \"MasterstrapFontProfile.json\", bridge_utf8(font_value));",
        "    bool imported = (wrote_client || wrote_turbo || wrote_global || wrote_shader || wrote_font)",
        "        && (!bridge_utf8(client_value)[0] || wrote_client) && (!bridge_utf8(turbo_value)[0] || wrote_turbo)",
        "        && (!bridge_utf8(global_value)[0] || wrote_global) && (!bridge_utf8(shader_value)[0] || wrote_shader)",
        "        && (!bridge_utf8(font_value)[0] || wrote_font);",
        "    char authorization[256];",
        "    snprintf(authorization, sizeof(authorization), \"{\\\"schema\\\":1,\\\"request\\\":\\\"%s\\\",\\\"issuedAtUnix\\\":%ld,\\\"singleUse\\\":true}\", request, time(NULL));",
        "    bool wrote_authorization = imported && bridge_write_document(\"MasterstrapLaunchAuthorization.json\", authorization);",
        "    imported = imported && wrote_authorization;",
        "    bridge_log(false, \"Document writes: request=%s client=%d turbo=%d global=%d shader=%d font=%d\",",
        "               request, wrote_client, wrote_turbo, wrote_global, wrote_shader, wrote_font);",
        "    bridge_log(false, \"One-time authorization: request=%s written=%d singleUse=1 ttlSeconds=180\", request, wrote_authorization);",
        "    if (imported) {",
        "        char receipt[512];",
        "        snprintf(receipt, sizeof(receipt),",
        "                 \"{\\\"protocol\\\":%d,\\\"status\\\":\\\"imported\\\",\\\"request\\\":\\\"%s\\\",\\\"receivedAtUnix\\\":%ld,\\\"client\\\":%s,\\\"turbo\\\":%s,\\\"global\\\":%s,\\\"shader\\\":%s,\\\"font\\\":%s}\",",
        "                 protocol_version, request, time(NULL), wrote_client ? \"true\" : \"false\",",
        "                 wrote_turbo ? \"true\" : \"false\", wrote_global ? \"true\" : \"false\",",
        "                 wrote_shader ? \"true\" : \"false\", wrote_font ? \"true\" : \"false\");",
        "        bridge_write_document(",
        "            \"MasterstrapBridgeReceipt.json\",",
        "            receipt);",
        "        snprintf(g_last_bridge_request, sizeof(g_last_bridge_request), \"%s\", request);",
        "        bridge_log(false, \"Import complete: request=%s protocol=%d\", request, protocol_version);",
        "        if (g_session_payload) ((void (*)(id, SEL))objc_msgSend)(g_session_payload, sel_registerName(\"release\"));",
        "        g_session_payload = ((id (*)(id, SEL))objc_msgSend)(body, sel_registerName(\"retain\"));",
        "        snprintf(g_session_request, sizeof(g_session_request), \"%s\", request);",
        "        if (g_runtime_ready) apply_pending_launch_locked(\"warm_bridge_applied\", NULL);",
        "        else bridge_log(false, \"Snapshot pending runtime boot: request=%s\", request);",
        "    } else {",
        "        bridge_log(true, \"Import failed: request=%s envelope contained no writable configuration\", request);",
        "    }",
        "    return imported;",
        "}",
        "",
        "typedef bool (*BridgeDidFinishImp)(id, SEL, id, id);",
        "typedef bool (*BridgeOpenUrlImp)(id, SEL, id, id, id);",
        "typedef bool (*BridgeHandleUrlImp)(id, SEL, id, id);",
        "static BridgeDidFinishImp g_original_did_finish = NULL;",
        "static BridgeOpenUrlImp g_original_open_url = NULL;",
        "static BridgeHandleUrlImp g_original_handle_url = NULL;",
        "static Class g_bridge_delegate_class = NULL;",
        "",
        "static bool bridge_did_finish(id self, SEL cmd, id application, id launch_options) {",
        "    if (launch_options) {",
        "        id url = ((id (*)(id, SEL, id))objc_msgSend)(",
        "            launch_options,",
        "            sel_registerName(\"objectForKey:\"),",
        "            bridge_nsstr(\"UIApplicationLaunchOptionsURLKey\"));",
        "        if (url) {",
        "            bridge_log(false, \"Cold-launch URL callback received\");",
        "            import_masterstrap_bridge_url(url);",
        "        }",
        "    }",
        "    return g_original_did_finish",
        "        ? g_original_did_finish(self, cmd, application, launch_options)",
        "        : true;",
        "}",
        "",
        "static bool bridge_open_url(id self, SEL cmd, id application, id url, id options) {",
        "    bridge_log(false, \"Warm-launch URL callback received\");",
        "    bool imported = import_masterstrap_bridge_url(url);",
        "    bool original_result = g_original_open_url",
        "        ? g_original_open_url(self, cmd, application, url, options)",
        "        : false;",
        "    return imported || original_result;",
        "}",
        "",
        "static bool bridge_handle_url(id self, SEL cmd, id application, id url) {",
        "    bridge_log(false, \"Legacy URL callback received\");",
        "    bool imported = import_masterstrap_bridge_url(url);",
        "    bool original_result = g_original_handle_url",
        "        ? g_original_handle_url(self, cmd, application, url)",
        "        : false;",
        "    return imported || original_result;",
        "}",
        "",
        "static bool install_bridge_hooks_for_class(Class cls) {",
        "    if (!cls) return false;",
        "    if (cls == g_bridge_delegate_class) return true;",
        "",
        "    SEL did_finish_sel = sel_registerName(\"application:didFinishLaunchingWithOptions:\");",
        "    Method did_finish_method = class_getInstanceMethod(cls, did_finish_sel);",
        "    if (did_finish_method) {",
        "        IMP current = method_getImplementation(did_finish_method);",
        "        if (current != (IMP)bridge_did_finish) {",
        "            g_original_did_finish = (BridgeDidFinishImp)current;",
        "            if (!class_addMethod(cls, did_finish_sel, (IMP)bridge_did_finish,",
        "                                 method_getTypeEncoding(did_finish_method))) {",
        "                method_setImplementation(did_finish_method, (IMP)bridge_did_finish);",
        "            }",
        "        }",
        "    }",
        "",
        "    SEL open_url_sel = sel_registerName(\"application:openURL:options:\");",
        "    Method open_url_method = class_getInstanceMethod(cls, open_url_sel);",
        "    if (open_url_method) {",
        "        IMP current = method_getImplementation(open_url_method);",
        "        if (current != (IMP)bridge_open_url) {",
        "            g_original_open_url = (BridgeOpenUrlImp)current;",
        "            if (!class_addMethod(cls, open_url_sel, (IMP)bridge_open_url,",
        "                                 method_getTypeEncoding(open_url_method))) {",
        "                method_setImplementation(open_url_method, (IMP)bridge_open_url);",
        "            }",
        "        }",
        "    } else {",
        "        class_addMethod(cls, open_url_sel, (IMP)bridge_open_url, \"B@:@@@\");",
        "    }",
        "",
        "    SEL handle_url_sel = sel_registerName(\"application:handleOpenURL:\");",
        "    Method handle_url_method = class_getInstanceMethod(cls, handle_url_sel);",
        "    if (handle_url_method) {",
        "        IMP current = method_getImplementation(handle_url_method);",
        "        if (current != (IMP)bridge_handle_url) {",
        "            g_original_handle_url = (BridgeHandleUrlImp)current;",
        "            if (!class_addMethod(cls, handle_url_sel, (IMP)bridge_handle_url,",
        "                                 method_getTypeEncoding(handle_url_method))) {",
        "                method_setImplementation(handle_url_method, (IMP)bridge_handle_url);",
        "            }",
        "        }",
        "    }",
        "",
        "    g_bridge_delegate_class = cls;",
        "    bridge_log(false, \"Installed URL receiver hooks: delegateClass=%s didFinish=%d openURL=%d legacy=%d\",",
        "               class_getName(cls), did_finish_method != NULL, open_url_method != NULL, handle_url_method != NULL);",
        "    return true;",
        "}",
        "",
        "static bool install_runtime_bridge_hooks(void) {",
        "    Class roma = (Class)objc_getClass(\"AppDelegateROMA\");",
        "    if (roma && install_bridge_hooks_for_class(roma)) return true;",
        "    Class exp = (Class)objc_getClass(\"EXAppDelegateWrapper\");",
        "    if (exp && install_bridge_hooks_for_class(exp)) return true;",
        "    return false;",
        "}",
        "",
        "static uintptr_t get_robloxlib_base(void) {",
        "    uint32_t count = _dyld_image_count();",
        "    // Prioritize RobloxLib framework which contains the actual engine!",
        "    for (uint32_t i = 0; i < count; i++) {",
        "        const char* name = _dyld_get_image_name(i);",
        "        if (name && strstr(name, \"RobloxLib\")) {",
        "            return (uintptr_t)_dyld_get_image_header(i);",
        "        }",
        "    }",
        "    // Fallback only if RobloxLib is merged",
        "    for (uint32_t i = 0; i < count; i++) {",
        "        const char* name = _dyld_get_image_name(i);",
        "        if (name && strstr(name, \"Roblox.app/Roblox\")) {",
        "            return (uintptr_t)_dyld_get_image_header(i);",
        "        }",
        "    }",
        "    return 0;",
        "}",
        "",
        "static void* worker_thread(void* arg) {",
        "    (void)arg;",
        "    uintptr_t base = 0;",
        "    syslog(LOG_NOTICE, \"[Masterstrap-iOS] Dylib initialized in PID %d. Waiting for RobloxLib...\", getpid());",
        "    bool bridge_hooked = false;",
        "    for (int hook_wait = 0; hook_wait < 30; hook_wait++) {",
        "        if (install_runtime_bridge_hooks()) { bridge_hooked = true; break; }",
        "        usleep(50000);",
        "    }",
        "    if (!bridge_hooked) bridge_log(true, \"Runtime app delegate hook unavailable after 30 attempts\");",
        "",
        "    // Wait up to 15 seconds for RobloxLib framework to be loaded by dyld",
        "    for (int wait = 0; wait < 150; wait++) {",
        "        base = get_robloxlib_base();",
        "        if (base != 0) break;",
        "        usleep(100000); // 100ms",
        "    }",
        "    if (!engine_identity_matches(base)) { runtime_note(\"[IDENTITY] UUID status=MISMATCH_OR_UNPINNED engineWrites=BLOCKED\"); base = 0; }",
        "    else runtime_note(\"[IDENTITY] UUID status=MATCH expected=%s engineWrites=ALLOWED functionCalls=STILL_GATED\", kExpectedEngineUuidText);",
        "    { SESSION_LOCK; g_engine_base = base; }",
        "",
        "    char log_path[512] = {0};",
        "    const char* home = getenv(\"HOME\");",
        "    if (home) {",
        "        snprintf(log_path, sizeof(log_path), \"%s/Documents/masterstrap_ios.log\", home);",
        "    } else {",
        "        snprintf(log_path, sizeof(log_path), \"/tmp/masterstrap_ios.log\");",
        "    }",
        "",
        "    void* log_file = fopen(log_path, \"a+\");",
        "    if (log_file) {",
        "        fseek(log_file, 0, 2);",
        "        long previous_size = ftell(log_file);",
        "        if (previous_size > 1048576) {",
        "            fclose(log_file);",
        "            log_file = fopen(log_path, \"w\");",
        "            if (log_file) {",
        "                fprintf(log_file, \"[%ld] [RUNTIME] log rotated after %ld bytes\\n\", time(NULL), previous_size);",
        "                fflush(log_file);",
        "            }",
        "        }",
        "    }",
        "    run_engine_abi_probe(base, log_file);",
        "    if (log_file) fflush(log_file);",
        "    if (log_file) {",
        "        fprintf(log_file, \"==============================================\\n\");",
        "        fprintf(log_file, \"=== Masterstrap Roblox iOS Runtime Log    ===\\n\");",
        "        fprintf(log_file, \"==============================================\\n\");",
        "        fprintf(log_file, \"Session Unix Time: %ld\\n\", time(NULL));",
        "        fprintf(log_file, \"PID: %d\\n\", getpid());",
        "        fprintf(log_file, \"RobloxLib Base Address: 0x%lx\\n\", (unsigned long)base);",
        "        fprintf(log_file, \"Total Flags Requested: %zu\\n\", kTotalRequested);",
        "        fprintf(log_file, \"Verified Flags Patched: %zu\\n\", kVerifiedCount);",
        "        fprintf(log_file, \"Miss Offsets Count: %zu\\n\", kMissCount);",
        "        fprintf(log_file, \"Verified Engine Offsets Count: %zu\\n\\n\", kOffsetsCount);",
        "        fprintf(log_file, \"FastFlag Apply Backend: %s\\n\", kFastFlagBackend);",
        "        fprintf(log_file, \"Engine SetFFlag Registry: %s\\n\", kEngineSetFFlagBackend);",
        "        fprintf(log_file, \"Engine GetFastFlag Hook: %s\\n\", kEngineGetFastFlagBackend);",
        "        fprintf(log_file, \"FPS Runtime Consumer: %s\\n\", kFpsRuntimeBackend);",
        "        fprintf(log_file, \"Font Runtime Consumer: %s\\n\", kFontRuntimeBackend);",
        "        fprintf(log_file, \"Shader Runtime Consumer: %s\\n\", kShaderRuntimeBackend);",
        "        fprintf(log_file, \"Stretch Runtime Consumer: %s\\n\\n\", kStretchRuntimeBackend);",
        "        fprintf(log_file, \"\\n\");",
        "        ",
        "        if (kOffsetsCount > 0) {",
        "            fprintf(log_file, \"--- [Engine Offset Address Map - No Calls] ---\\n\");",
        "            for (size_t i = 0; i < kOffsetsCount; i++) {",
        "                uintptr_t func_addr = (base != 0) ? (base + kVerifiedOffsets[i].rva) : 0;",
        "                bool executable = macho_rva_is_executable(base, kVerifiedOffsets[i].rva);",
        "                fprintf(log_file, \"[OFFSET_MAP] %-32s @ 0x%lx (RVA 0x%lx) executable=%d aliases=%d\\n\",",
        "                        kVerifiedOffsets[i].name,",
        "                        (unsigned long)func_addr,",
        "                        (unsigned long)kVerifiedOffsets[i].rva,",
        "                        executable, offset_role_count(kVerifiedOffsets[i].rva));",
        "            }",
        "            fprintf(log_file, \"\\n\");",
        "        }",
        "",
        "        fprintf(log_file, \"--- [Active FastFlags Status] ---\\n\");",
        "        fflush(log_file);",
        "    }",
        "",
        "    syslog(LOG_NOTICE, \"[Masterstrap-iOS] RobloxLib base found at 0x%lx. Applying dynamic and verified flags...\", (unsigned long)base);",
        "",
        "    int dynamic_count = 0;",
        "    int success_count = 0;",
        "    bool launch_authorized = false;",
        "    if (base != 0) {",
        "        runtime_note(\"[BOOT] stage=authorization_begin base=0x%lx\", (unsigned long)base);",
        "        launch_authorized = local_launch_authorization_valid(log_file);",
        "        runtime_note(\"[BOOT] stage=authorization_end authorized=%d\", launch_authorized);",
        "        dynamic_count = complete_runtime_boot(base, log_file);",
        "        { SESSION_LOCK; launch_authorized = g_launch_authorized; }",
        "        success_count = g_baseline_applied;",
        "    } else {",
        "        if (log_file) fprintf(log_file, \"ERROR: Could not locate RobloxLib base in memory!\\n\");",
        "        syslog(LOG_ERR, \"[Masterstrap-iOS] ERROR: Could not locate RobloxLib base in memory!\");",
        "    }",
        "",
        "    if (log_file && kMissCount > 0) {",
        "        fprintf(log_file, \"\\n--- [Miss Offsets List] ---\\n\");",
        "        for (size_t i = 0; kMissOffsets[i]; i++) {",
        "            fprintf(log_file, \"[MISS_OFFSET] %s (Not found in FFlags map)\\n\", kMissOffsets[i]);",
        "        }",
        "    }",
        "",
        "    // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "    // [10/10 FEATURE DIAGNOSTIC LOG] masterstrap_ios.log",
        "    // Grep each FEATURE_XX tag to instantly identify failing features.",
        "    // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "    if (log_file) {",
        "        fprintf(log_file, \"\\n=== [10/10 FEATURE DIAGNOSTIC MATRIX] ===\\n\");",
        "        // FEATURE_01: FPS Unlock",
        "        fprintf(log_file, \"[FEATURE_01_FPS] status=%s backend=%s targetFps=%d gate=EngineBase(%d)&&MetalUncapped(%d) blockedReason=%s\\n\",",
        "                g_target_fps != 60 ? \"ACTIVE\" : \"DEFAULT_60\", kFpsRuntimeBackend, g_target_fps,",
        "                (base != 0), g_metal_layer_stretch_installed,",
        "                base == 0 ? \"NO_ENGINE_BASE\" : \"none\");",
        "        // FEATURE_02: Game Mode",
        "        fprintf(log_file, \"[FEATURE_02_GAME_MODE] status=%s enabled=%d verifiedFlags=%d/%zu gate=Auth(%d) blockedReason=%s\\n\",",
        "                launch_authorized && g_game_mode_enabled ? \"ACTIVE\" : \"INACTIVE\",",
        "                g_game_mode_enabled, success_count, kVerifiedCount, launch_authorized,",
        "                !launch_authorized ? \"NO_AUTH\" : !g_game_mode_enabled ? \"GAME_MODE_OFF_IN_PROFILE\" : \"none\");",
        "        // FEATURE_03: Dynamic FFlag Editor",
        "        fprintf(log_file, \"[FEATURE_03_FFLAG_EDITOR] status=%s dynamicApplied=%d baselineApplied=%d totalMapped=%zu gate=Auth(%d) blockedReason=%s\\n\",",
        "                launch_authorized && dynamic_count > 0 ? \"ACTIVE\" : launch_authorized ? \"ACTIVE_BASELINE_ONLY\" : \"BLOCKED\",",
        "                dynamic_count, success_count, kMasterFlagsCount, launch_authorized,",
        "                !launch_authorized ? \"NO_AUTH\" : dynamic_count == 0 ? \"NO_DYNAMIC_JSON\" : \"none\");",
        "        // FEATURE_04: Cloud Bridge Protocol v2",
        "        fprintf(log_file, \"[FEATURE_04_BRIDGE_V2] status=%s hookInstalled=%d scheme=masterstrap-rbx-bridge-v1 lastRequest=%s blockedReason=%s\\n\",",
        "                bridge_hooked ? \"ACTIVE\" : \"BLOCKED\", bridge_hooked,",
        "                g_last_bridge_request[0] ? g_last_bridge_request : \"(none)\",",
        "                !bridge_hooked ? \"DELEGATE_HOOK_FAILED_30_ATTEMPTS\" : \"none\");",
        "        // FEATURE_05: Renderer Mode",
        "        fprintf(log_file, \"[FEATURE_05_RENDERER] status=%s mode=%s gate=Auth(%d)&&Profile(%d) backend=%s blockedReason=%s\\n\",",
        "                launch_authorized && g_profile_loaded ? \"ACTIVE\" : \"BLOCKED\", g_renderer_mode,",
        "                launch_authorized, g_profile_loaded, kShaderRuntimeBackend,",
        "                !launch_authorized ? \"NO_AUTH\" : !g_profile_loaded ? \"NO_PROFILE\" : \"none\");",
        "        // FEATURE_06: Shader / Wet Floor",
        "        fprintf(log_file, \"[FEATURE_06_SHADER] status=%s package=%s wetFloor=%d gate=Auth(%d)&&Profile(%d) blockedReason=%s\\n\",",
        "                launch_authorized && g_profile_loaded && g_shader_wet_floor ? \"ACTIVE\" :",
        "                launch_authorized && g_profile_loaded ? \"INACTIVE_IN_PROFILE\" : \"BLOCKED\",",
        "                g_shader_package, g_shader_wet_floor, launch_authorized, g_profile_loaded,",
        "                !launch_authorized ? \"NO_AUTH\" : !g_profile_loaded ? \"NO_PROFILE\" : !g_shader_wet_floor ? \"WET_FLOOR_DISABLED\" : \"none\");",
        "        // FEATURE_07: Font Override",
        "        fprintf(log_file, \"[FEATURE_07_FONT] status=REQUIRES_DEVICE_READBACK family=%s sizeStep=%d backend=%s sizeEffect=UNSUPPORTED_KEEP_ORIGINAL\\n\",",
        "                g_font_family, g_font_size_step, kFontRuntimeBackend);",
        "        // FEATURE_08: Stretch / Aspect-Ratio",
        "        fprintf(log_file, \"[FEATURE_08_STRETCH] status=STANDBY stretchPct=%d backend=%s gate=NativePassthrough(1) blockedReason=none\\n\",",
        "                g_stretch_percent, kStretchRuntimeBackend);",
        "        // FEATURE_09: Display Keep-Alive",
        "        fprintf(log_file, \"[FEATURE_09_KEEPALIVE] status=%s keepActive=%d refreshReq=%d gate=Auth(%d)&&Profile(%d) blockedReason=%s\\n\",",
        "                launch_authorized && g_keep_display_active ? \"ACTIVE\" : \"INACTIVE\",",
        "                g_keep_display_active, g_refresh_requested,",
        "                launch_authorized, g_profile_loaded,",
        "                !launch_authorized ? \"NO_AUTH\" : !g_keep_display_active ? \"DISABLED_IN_PROFILE\" : \"none\");",
        "        // FEATURE_10: Stealth (7 offsets: AbortLogger, ReportBytecode, ValidateHeader, ScriptTimeout, ValidateCaller, ValidateAndSetupCaps, Telemetry)",
        "        {",
        "            int stl_abort=0,stl_rbc=0,stl_vh=0,stl_st=0,stl_vc=0,stl_vc2=0,stl_tel=0;",
        "            for (size_t si = 0; si < kOffsetsCount; si++) {",
        "                const char* sn = kVerifiedOffsets[si].name;",
        "                if (strcmp(sn,\"rbx_abortlogger\")==0) stl_abort=1;",
        "                else if (strcmp(sn,\"rbx_reportbytecode\")==0) stl_rbc=1;",
        "                else if (strcmp(sn,\"luau_validateheader\")==0) stl_vh=1;",
        "                else if (strcmp(sn,\"scripttimeoutcheck\")==0) stl_st=1;",
        "                else if (strcmp(sn,\"validatecaller\")==0) stl_vc=1;",
        "                else if (strcmp(sn,\"validateandsetupcaps\")==0) stl_vc2=1;",
        "                else if (strcmp(sn,\"telemetrybuffer\")==0||strcmp(sn,\"telemetrysize\")==0) stl_tel=1;",
        "            }",
        "            int stl_total = stl_abort+stl_rbc+stl_vh+stl_st+stl_vc+stl_vc2+stl_tel;",
        "            fprintf(log_file, \"[FEATURE_10_STEALTH] status=%s offsetsLoaded=%d/7 \"",
        "                    \"abortLogger=%d reportBytecode=%d validateHeader=%d \"",
        "                    \"scriptTimeout=%d validateCaller=%d validateAndSetupCaps=%d telemetry=%d \"",
        "                    \"gate=EngineBase(%d) blockedReason=%s\\n\",",
        "                    base != 0 && stl_total==7 ? \"ACTIVE_ALL\" : base != 0 && stl_total>0 ? \"ACTIVE_PARTIAL\" : \"BLOCKED\",",
        "                    stl_total, stl_abort, stl_rbc, stl_vh, stl_st, stl_vc, stl_vc2, stl_tel,",
        "                    (base != 0),",
        "                    base == 0 ? \"ENGINE_BASE_NOT_FOUND\" : stl_total < 7 ? \"SOME_OFFSETS_NOT_DUMPED\" : \"none\");",
        "        }",
        "        fprintf(log_file, \"=== [END FEATURE DIAGNOSTIC MATRIX] ===\\n\\n\");",
        "    }",
        "",
        "    if (log_file) {",
        "        fprintf(log_file, \"\\nFinal Result: authorized=%d, %d baseline verified + %d dynamic flags active (Status: %s)\\n\",",
        "                launch_authorized, success_count, dynamic_count, !launch_authorized ? \"DIRECT_LAUNCH_NO_CONFIG\" : g_applied_request[0] && g_profile_loaded ? \"BRIDGE_SNAPSHOT_APPLIED\" : \"PARTIAL\");",
        "        fflush(log_file);",
        "        fclose(log_file);",
        "    }",
        "",

        "    syslog(LOG_NOTICE, \"[Masterstrap-iOS] Patch finished: %d baseline + %d dynamic active. Starting persistence loop...\", success_count, dynamic_count);",
        "",
        "    // Persistence uses the in-memory cache only; consumed files are never read again.",
        "    runtime_note(\"Persistence loop requested: pid=%d durationSeconds=30 authorized=%d cachedFlags=%zu\", getpid(), launch_authorized, g_cached_dynamic_count);",
        "    for (int loop = 0; launch_authorized && loop < 60; loop++) {",
        "        usleep(500000); // 500ms",
        "        if (base == 0) { base = get_robloxlib_base(); if (!engine_identity_matches(base)) base = 0; if (base) complete_runtime_boot(base, NULL); }",
        "        if (base != 0) {",
        "            SESSION_LOCK;",
        "            apply_baseline_flags(base);",
        "            apply_cached_dynamic_flags(base);",
        "        }",
        "        if (loop == 19 || loop == 39 || loop == 59) {",
        "            runtime_note(\"Persistence checkpoint: elapsedSeconds=%d baseReady=%d\", (loop + 1) / 2, base != 0);",
        "        }",
        "    }",
        "    runtime_note(\"Persistence initial 30s completed. Starting continuous watchdog: pid=%d\", getpid());",
        "    int watchdog_ticks = 0;",
        "    while (1) {",
        "        usleep(100000); // 100ms heartbeat",
        "        watchdog_ticks++;",
        "        if (base == 0) { base = get_robloxlib_base(); if (!engine_identity_matches(base)) base = 0; if (base) complete_runtime_boot(base, NULL); }",
        "        if (base != 0) {",
        "            SESSION_LOCK;",
        "            apply_baseline_flags(base);",
        "            apply_cached_dynamic_flags(base);",
        "        }",
        "        if (watchdog_ticks % 300 == 0) {",
        "            runtime_note(\"Watchdog checkpoint: elapsedSeconds=%d baseReady=%d\", 30 + watchdog_ticks, base != 0);",
        "        }",
        "    }",
        "    runtime_note(\"Watchdog loop terminated: pid=%d\", getpid());",
        "",
        "    return NULL;",
        "}",
        "",
        "__attribute__((constructor)) static void masterstrap_ios_init(void) {",
        "    if (getenv(\"MASTERSTRAP_INITIALIZED\")) {",
        "        return;",
        "    }",
        "    setenv(\"MASTERSTRAP_INITIALIZED\", \"1\", 1);",
        "    static int s_initialized = 0;",
        "    if (s_initialized) return;",
        "    s_initialized = 1;",
        "",
        "    install_runtime_bridge_hooks();",
        "    install_display_link_policy();",
        "    install_metal_layer_stretch_policy();",
        "    void* thread = NULL;",
        "    pthread_create(&thread, NULL, worker_thread, NULL);",
        "    if (thread) pthread_detach(thread);",
        "}"
    ])

    return "\n".join(lines)


def compile_dylib(c_source: str, out_dylib: Path) -> Path:
    work_dir = out_dylib.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    c_file = work_dir / "masterstrap_ios.c"
    obj_file = work_dir / "masterstrap_ios.o"
    c_file.write_text(c_source, encoding="utf-8")
    native_options = ["-O0", "-g"] if os.environ.get("MASTERSTRAP_BUILD_TYPE") == "Debug" else ["-O3"]

    if sys.platform == "darwin":
        isysroot = []
        try:
            sdk_proc = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True)
            if sdk_proc.returncode == 0 and sdk_proc.stdout.strip():
                isysroot = ["-isysroot", sdk_proc.stdout.strip()]
        except Exception as error:
            raise RuntimeError("Apple iphoneos SDK lookup failed") from error
        if not isysroot:
            raise RuntimeError("Apple iphoneos SDK is required")

        compile_cmd = [
            "clang",
            "-target", "arm64-apple-ios14.0",
            "-dynamiclib",
            "-fblocks",
            *native_options,
            "-undefined", "dynamic_lookup",
            "-install_name", "@rpath/libmasterstrap.dylib",
            "-lz",
            *isysroot,
            str(c_file),
            "-o", str(out_dylib)
        ]
        proc = subprocess.run(compile_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Clang dynamiclib compilation failed:\n{proc.stderr}")
        print(f"Compiled native iOS dylib -> {out_dylib} ({out_dylib.stat().st_size} bytes)")
        return out_dylib

    clang_bin, lld_bin = find_clang_lld()

    compile_cmd = [
        clang_bin,
        "-target", "arm64-apple-ios14.0",
        *native_options,
        "-fblocks",
        "-c", str(c_file),
        "-o", str(obj_file)
    ]
    proc = subprocess.run(compile_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Compilation failed:\n{proc.stderr}")

    link_cmd = [
        lld_bin,
        "-flavor", "darwin",
        "-arch", "arm64",
        "-dylib",
        "-undefined", "dynamic_lookup",
        "-platform_version", "ios", "14.0.0", "14.0.0",
        "-adhoc_codesign",
        "-install_name", "@rpath/libmasterstrap.dylib",
        str(obj_file),
        "-o", str(out_dylib)
    ]
    proc = subprocess.run(link_cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Linking failed:\n{proc.stderr}")

    print(f"Compiled native iOS dylib -> {out_dylib} ({out_dylib.stat().st_size} bytes)")
    return out_dylib



def remove_load_dylib(macho_bytes: bytes, target_substrings: list) -> bytes:
    data = bytearray(macho_bytes)
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved = struct.unpack('<IIIIIIII', data[:32])
    if magic != 0xFEEDFACF:
        return macho_bytes

    offset = 32
    cmd_index = 0
    while cmd_index < ncmds:
        cmd, cmdsize = struct.unpack('<II', data[offset:offset+8])
        if cmd in (0x0C, 0x80000018):  # LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB
            name_offset = struct.unpack('<I', data[offset+8:offset+12])[0]
            name = data[offset+name_offset:offset+cmdsize].split(b'\x00')[0].decode('utf-8', errors='ignore')
            if any(target.lower() in name.lower() for target in target_substrings):
                print(f"[PURGE] Removed load command {cmd_index}: {name} (size {cmdsize})")
                rem_len = (32 + sizeofcmds) - (offset + cmdsize)
                data[offset:offset+rem_len] = data[offset+cmdsize:offset+cmdsize+rem_len]
                data[offset+rem_len:offset+rem_len+cmdsize] = b'\x00' * cmdsize
                sizeofcmds -= cmdsize
                ncmds -= 1
                struct.pack_into('<II', data, 16, ncmds, sizeofcmds)
                continue
        offset += cmdsize
        cmd_index += 1
    return bytes(data)


def insert_load_dylib(macho_bytes: bytes, dylib_path: str) -> bytes:
    data = bytearray(macho_bytes)
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, reserved = struct.unpack('<IIIIIIII', data[:32])
    if magic != 0xFEEDFACF:
        raise ValueError(f'Unsupported Mach-O magic: 0x{magic:X}')

    if dylib_path.encode('utf-8') in bytes(data[:32 + sizeofcmds]):
        print(f"LC_LOAD_DYLIB ({dylib_path}) already present in Mach-O, skipping injection")
        return macho_bytes

    path_bytes = dylib_path.encode('utf-8') + b'\x00'
    cmdsize = 24 + len(path_bytes)
    if cmdsize % 8 != 0:
        cmdsize += 8 - (cmdsize % 8)
    padding_len = cmdsize - 24 - len(path_bytes)

    cmd_bytes = struct.pack('<IIIIII', 0x0C, cmdsize, 24, 2, 0x10000, 0x10000) + path_bytes + (b'\x00' * padding_len)

    insert_pos = 32 + sizeofcmds
    if insert_pos + cmdsize <= len(data) and not any(data[insert_pos : insert_pos + cmdsize]):
        data[insert_pos : insert_pos + cmdsize] = cmd_bytes
        struct.pack_into('<II', data, 16, ncmds + 1, sizeofcmds + cmdsize)
        print(f"Injected LC_LOAD_DYLIB ({dylib_path}) into Mach-O")
    else:
        print(f"Warning: No padding available for LC_LOAD_DYLIB ({dylib_path}) in Mach-O header")
    return bytes(data)


def write_full_inspection_report(out_dir: Path, flags: dict, verified_entries: list, miss_offsets: list, ipa_name: str, out_ipa_name: str, embed_flags: bool = True):
    out_dir.mkdir(parents=True, exist_ok=True)
    report_text = f"""=====================================================
=== Masterstrap Roblox iOS Build & Inspection Log ===
=====================================================
Date: {time.strftime('%Y-%m-%d %H:%M:%S')}
Input IPA: {ipa_name}
Output IPA: {out_ipa_name}

SUMMARY:
  - Embedded Baseline Mode: {'ENABLED' if embed_flags else 'DISABLED (bridge-only isolation build)'}
  - Total Flags Requested: {len(flags)}
  - Verified Active Flags: {len(verified_entries)}
  - Miss Offsets Count:    {len(miss_offsets)}
  - Success Ratio:         {(len(verified_entries)/len(flags)*100) if flags else 0:.1f}%

--- [VERIFIED ACTIVE FASTFLAGS (SetFastFlag)] ---
"""
    for n, r, k, v in verified_entries:
        report_text += f"  [VERIFIED] (SetFastFlag) {n} -> RVA: 0x{r:X} | Type: {'bool' if k==0 else 'int32'} | Value: {v}\n"

    if miss_offsets:
        report_text += "\n--- [MISS OFFSETS (FLAG NOT IN RVA MAP)] ---\n"
        for m in miss_offsets:
            report_text += f"  [MISS_OFFSET] {m}\n"

    report_file = out_dir / "flag_inject_report.log"
    report_file.write_text(report_text, encoding="utf-8")
    print(f"Wrote full report -> {report_file}")

    # Also write a runtime log template
    runtime_log = out_dir / "masterstrap_ios.log"
    runtime_log.write_text(report_text, encoding="utf-8")


def patch_robloxlib_fps_clamps(data: bytearray, report_path: Path = None) -> bytearray:
    """Resolve unique aligned instruction sites inside this exact Mach-O's executable sections."""
    sys.path.insert(0, str(ROOT / "roblox"))
    from roblox_ios_dumper.macho import MachOImage, MachOError
    from bisect import bisect_right
    import hashlib
    try:
        image = MachOImage(bytes(data), "arm64")
    except MachOError:
        print("[PATCH_ROBLOXLIB] Invalid Mach-O; no bytes modified")
        return data
    if image.slice.file_offset or image.is_encrypted:
        raise RuntimeError("FPS binary gate requires a decrypted thin ARM64 Mach-O")
    records = []
    patches = [
        # 1. GetDefaultFramerateCap 240 FPS clamp (cmp w8, #240; mov w9, #240; csel -> mov w9, w8)
        (
            "GetDefaultFramerateCap 240 FPS clamp",
            re.compile(re.escape(b"\x1f\xc1\x03\x71\x09\x1e\x80\x52") + b"(" + re.escape(b"\x09\xb1\x89\x1a") + b"|" + re.escape(b"\xe9\x03\x08\x2a") + b")"),
            8,
            b"\xe9\x03\x08\x2a",
        ),
        # 2. TaskScheduler dt 240 FPS clamp (cmp w11, #240; mov w12, #240; csel -> nop)
        (
            "TaskScheduler dt 240 FPS clamp",
            re.compile(re.escape(b"\x7f\xc1\x03\x71\x0c\x1e\x80\x52") + b"(" + re.escape(b"\x6b\xb1\x8c\x1a") + b"|" + re.escape(b"\x1f\x20\x03\xd5") + b")"),
            8,
            b"\x1f\x20\x03\xd5",
        ),
        # 3. TaskScheduler target frametime calculator 240 clamp (cmp w23, #240; mov w8, #240; csel -> mov w8, w23)
        (
            "TaskScheduler frametime calculator 240 clamp",
            re.compile(re.escape(b"\xff\xc2\x03\x71\x08\x1e\x80\x52") + b"(" + re.escape(b"\xe8\x32\x88\x1a") + b"|" + re.escape(b"\xe8\x03\x17\x2a") + b")"),
            8,
            b"\xe8\x03\x17\x2a",
        ),
        # 4. TaskScheduler getTargetFps fallback 60 FPS (cmp w8, #0; mov w8, #0x3c -> mov w8, #10000)
        (
            "TaskScheduler getTargetFps fallback 60 FPS",
            re.compile(re.escape(b"\x1f\x01\x00\x71") + b"(" + re.escape(b"\x88\x07\x80\x52") + b"|" + re.escape(b"\x08\xe2\x84\x52") + b")" + re.escape(b"\x00\x01\x89\x1a")),
            4,
            b"\x08\xe2\x84\x52",
        ),
        # 5. AdaptiveSync schedule target 10000 FPS (ldr w8, [x0, #0x118]; cmp w8, #1; csinc/mov w22, #10000)
        (
            "AdaptiveSync schedule target 10000 FPS",
            re.compile(re.escape(b"\x08\x18\x41\xb9\x1f\x05\x00\x71") + b"(" + re.escape(b"\x16\xc5\x9f\x1a") + b"|" + re.escape(b"\x16\xe2\x84\x52") + b")"),
            8,
            b"\x16\xe2\x84\x52",
        ),
        # 6. AdaptiveSync refresh clamp bypass (cmp w21, w22; csel -> nop)
        (
            "AdaptiveSync refresh clamp bypass",
            re.compile(re.escape(b"\xbf\x02\x16\x6b") + b"(" + re.escape(b"\xb6\x32\x96\x1a") + b"|" + re.escape(b"\x1f\x20\x03\xd5") + b")"),
            4,
            b"\x1f\x20\x03\xd5",
        ),
        # 7. DisplayTiming in-game pacing clamp (csel w8, w21, w22, ne -> mov w8, w22; ucvtf s1, w21 -> ucvtf s1, w22)
        (
            "DisplayTiming in-game pacing clamp",
            re.compile(b"(" + re.escape(b"\xa8\x12\x96\x1a") + b"|" + re.escape(b"\xe8\x03\x16\x2a") + b")" + re.escape(b"\x80\x02\x23\x1e") + b"(" + re.escape(b"\xa1\x02\x23\x1e") + b"|" + re.escape(b"\xc1\x02\x23\x1e") + b")" + re.escape(b"\x02\x01\x23\x1e")),
            0,
            b"\xe8\x03\x16\x2a\x80\x02\x23\x1e\xc1\x02\x23\x1e\x02\x01\x23\x1e",
        ),
        # 8. DeviceMtl presentDrawable:atTime: delay clamp bypass (b.le presentDrawable -> b presentDrawable)
        (
            "DeviceMtl presentDrawable:atTime: delay clamp bypass",
            re.compile(re.escape(b"\x62\x7a\x40\xf9\x08\x20\x60\x1e") + b"(" + b"[\x00-\xff]{2}\x00\x54|" + re.escape(b"\x5e\x01\x00\x14") + b")"),
            8,
            b"\x5e\x01\x00\x14",
        ),
    ]

    applied = 0
    for name, pattern, offset, replacement in patches:
        matches = [match for section in image.executable_sections
                   for match in pattern.finditer(data, section.file_offset, section.file_offset + section.size)
                   if match.start() % 4 == 0]
        record = {"role": name, "matches": len(matches), "status": "unresolved"}
        records.append(record)
        if len(matches) == 1:
            site = matches[0].start() + offset
            address = image.file_to_virtual(site)
            index = bisect_right(image.function_starts, address) - 1 if address is not None else -1
            if index < 0 or site + len(replacement) > matches[0].end():
                record["status"] = "missing-function-boundary"
                continue
            function_start = image.function_starts[index]
            function_end = image.function_starts[index + 1] if index + 1 < len(image.function_starts) else address + len(replacement)
            if image.file_to_virtual(matches[0].start()) < function_start or image.file_to_virtual(matches[0].end() - 1) >= function_end:
                record["status"] = "crosses-function-boundary"
                continue
            original = bytes(data[site:site + len(replacement)])
            rep = replacement
            if name == "DeviceMtl presentDrawable:atTime: delay clamp bypass":
                cur_insn = data[site:site + 4]
                if cur_insn[3] == 0x54:
                    raw_val = struct.unpack('<I', cur_insn)[0]
                    imm19 = (raw_val >> 5) & 0x7ffff
                    if imm19 & (1 << 18):
                        imm19 -= (1 << 19)
                    imm26 = imm19 & 0x3ffffff
                    rep = struct.pack('<I', (0x14 << 24) | imm26)
            data[site:site + len(rep)] = rep
            applied += 1
            record.update(status="resolved-signature-site", fileOffset=site, rva=image.to_rva(address),
                          functionRva=image.to_rva(function_start), before=original.hex(), after=rep.hex(),
                          runtimeEffect="unverified-requires-device-trace")
            print(f"[PATCH_ROBLOXLIB_DYNAMIC] {name} matched & un-clamped @ 0x{site:X} (signature)")
        else:
            print(f"[PATCH_ROBLOXLIB] {name} skipped: expected unique signature, found {len(matches)}")

    print(f"[PATCH_ROBLOXLIB] Applied {applied}/{len(patches)} version-agnostic FPS uncap clamps")
    report = {"schema": 1, "uuid": image.uuid, "architecture": image.architecture,
              "sourceSha256": hashlib.sha256(image.data).hexdigest(), "outputSha256": hashlib.sha256(data).hexdigest(), "patches": records,
              "resolved": applied, "required": len(patches), "deviceVerified": False}
    if report_path:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if os.environ.get("MASTERSTRAP_BUILD_TYPE", "Release") != "Debug" and applied != len(patches):
        raise RuntimeError("FPS binary gate rejected unresolved/ambiguous patch roles; inspect fps_patch_profile.json")
    return data


def build_pipeline(input_ipa: Path, settings_json: Path, hpp_path: Path, out_dir: Path, embed_flags: bool = True):
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # IPA builds must use a verified local dump, never a remote version's map.
    dumped_header = None
    expected_uuid = None
    if input_ipa and input_ipa.is_file():
        dumped_header = hpp_path if hpp_path and hpp_path.is_file() else run_local_dumper(input_ipa, out_dir)
        expected_uuid = verify_ipa_profile(input_ipa, dumped_header)

    # 2. Resolve FFlags table (dumped or remote)
    target_header = dumped_header if dumped_header else hpp_path
    table = fetch_or_load_fflags_map(target_header)

    flags = {}
    if not embed_flags:
        print("Bridge-only isolation build: embedded baseline flags are DISABLED")
    elif settings_json and settings_json.is_file():
        flags = json.loads(settings_json.read_text(encoding="utf-8"))
    else:
        # Default flags set
        flags = {
            "FFlagDebugDisplayFPS": True,
            "FFlagDebugSkyGray": True,
            "DFIntTaskSchedulerTargetFps": 10000,
            "FFlagTaskSchedulerLimitTargetFpsToScreenRefreshRate": False,
            "FFlagTaskSchedulerLimitTargetFpsTo2402": False,
            "FFlagGameBasicSettingsFramerateCap": False,
            "FFlagApplicationFrameRate": False,
            "FFlagApplicationFrameRatePolicy": False,
            "FFlagDebugExperimentalVSyncFramePacing": False,
            "FFlagDebugDisableVsync": True,
            "FFlagMetalVsyncPreferMaximumRefreshRate": False,
            "FFlagMetalDisplayTiming": False,
            "FFlagMetalDisplayTimingAdjustMinFPS": False,
            "FIntMetalDisplayTimingMinFPS": 0,
            "FFlagPerformanceControlExecution": False,
            "FFlagPerformanceControlObserveLowPowerMode": False,
            "FFlagPerformanceControlDynamicFrameTimeTarget": False,
            "FFlagPerformanceControlComputeUseTSFrameTimeTarget": False,
            "FFlagPerformanceControlDisplayRefreshRate": False,
            "FFlagPerformanceControlDisplayRefreshRateInAdaptiveSync": False,
            "FFlagPerformanceControlDisplayRefreshRateMigration": False,
            "DFIntDebugFRMQualityLevelOverride": 1,
            "DFIntDebugTextureManagerSkipMips": 12,
            "FIntDebugForceMSAASamples": 1,
            "DFIntDebugDynamicRenderKiloPixels": 922,
            "FFlagAutomaticDRS": True,
            "FFlagAutomaticDRSUseGpuTime": True,
            "DFFlagEnableMeshPreloading2": True,
            "DFFlagOptimizePartsInPart": True,
            "FIntFRMMaxGrassDistance": 0,
            "FIntFRMMinGrassDistance": 0,
            "FIntRobloxGuiBlurIntensity": 0,
            "FFlagDRSBasicManagement": True,
            "DFIntRenderPostFxBasePixelCount": 0,
            "DFIntDebugLimitMinTextureResolutionWhenSkipMips": 64,
            "FFlagAnimationClipMemCacheEnabled": True,
            "FFlagAnimationLodBoneEnabled": True,
            "FFlagEnableAndroidPowerManager": False,
            "FFlagUnifiedMeshCacheSizeComputation": True,
            "FFlagAdaptiveMeshCacheSizeForAndroid": True,
            "DFIntCSGLevelOfDetailSwitchingDistanceL12": 0,
            "DFIntCSGLevelOfDetailSwitchingDistanceL23": 0,
            "DFIntCSGLevelOfDetailSwitchingDistanceL34": 0,
            "DFIntDataSenderMaxBandwidthBps": 100000000,
            "DFIntDataSenderRate": 38760,
            "DFIntS2PhysicsSenderRate": 38760,
            "FFlagBatchNetAssetJoinBlobEnable": True,
            "FFlagFixAndroidBackgroundedSession": True,
            "FFlagDebugGraphicsPreferOpenGL": False
        }

    verified_entries, miss_offsets = resolve_flags_with_miss_report(flags, table)
    print(f"Flags Requested: {len(flags)} | Verified Active RVAs: {len(verified_entries)} | Miss Offsets: {len(miss_offsets)}")

    ipa_name = input_ipa.name if (input_ipa and input_ipa.is_file()) else "No_Direct_IPA"
    out_ipa_name = "Masterstrap-Roblox-Patched.ipa" if (input_ipa and input_ipa.is_file()) else "No_IPA_Packed"

    # Always write report log
    write_full_inspection_report(
        out_dir, flags, verified_entries, miss_offsets, ipa_name, out_ipa_name,
        embed_flags=embed_flags,
    )

    offsets_table = fetch_or_load_offsets_map(dumped_header.parent / "Offsets.hpp" if dumped_header else None)
    offsets_entries = sorted(offsets_table.items(), key=lambda x: x[0])
    print(f"Loaded Engine Offsets: {len(offsets_entries)} functions")

    if input_ipa and input_ipa.is_file():
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            dylib_path = tpath / "libmasterstrap.dylib"
            from ios_font_profile import build_font_profile
            font_profile = build_font_profile(input_ipa, out_dir / "font_asset_profile.json")
            if font_profile['uuid'] != expected_uuid:
                raise RuntimeError("Font profile UUID does not match verified engine")
            c_src = generate_native_hook_source(verified_entries, miss_offsets, len(flags), offsets_entries, all_flags_table=table, expected_uuid=expected_uuid, font_profile=font_profile)
            compile_dylib(c_src, dylib_path)
            dylib_bytes = dylib_path.read_bytes()

            # ── Extract version from Info.plist for canonical IPA filename ──
            roblox_version = "unknown"
            try:
                with zipfile.ZipFile(input_ipa, "r") as _z_ver:
                    for _name in _z_ver.namelist():
                        if _name.endswith("Roblox.app/Info.plist"):
                            _pl = plistlib.loads(_z_ver.read(_name))
                            roblox_version = (
                                _pl.get("CFBundleShortVersionString")
                                or _pl.get("CFBundleVersion")
                                or "unknown"
                            )
                            break
            except Exception as _e:
                print(f"Warning: Could not read version from Info.plist: {_e}")

            out_ipa = out_dir / f"Masterstrap-{roblox_version}.ipa"
            print(f"[BUILD] Roblox version detected from Info.plist: {roblox_version}")
            print(f"[BUILD] Output IPA: {out_ipa.name}")

            with zipfile.ZipFile(input_ipa, "r") as zin:
                with zipfile.ZipFile(out_ipa, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                    seen_entries = set()
                    embedded_settings_entries = {
                        "Payload/Roblox.app/ClientSettings/ClientAppSettings.json",
                        "Payload/Roblox.app/Frameworks/RobloxLib.framework/ClientSettings/ClientAppSettings.json",
                        "Payload/Roblox.app/masterstrap_baked_flags.json",
                        "Payload/Roblox.app/MasterstrapTurboProfile.json",
                        "Payload/Roblox.app/ClientSettings/MasterstrapTurboProfile.json",
                        "Payload/Roblox.app/GlobalBasicSettings_13.xml",
                        "Payload/Roblox.app/GlobalBasicSettings.xml",
                    }

                    for item in zin.infolist():
                        name = item.filename
                        if name in seen_entries or name == "Payload/Roblox.app/Frameworks/libmasterstrap.dylib":
                            continue
                        if name in embedded_settings_entries:
                            print(f"Removed embedded settings entry -> {name}")
                            continue

                        # ── Delta / Third-Party Sanitization Purge Filter ──
                        # 1. Skip Delta script package, injector UI, app extensions/plugins, and old signatures
                        if any(k in name.lower() for k in ["/gloop/", "dlt.zip", "_codesignature", "embedded.mobileprovision", "/plugins/", ".appex"]):
                            print(f"[PURGE] Removed third-party file -> {name}")
                            continue

                        # 2. Completely remove any third-party injector dylib (libgloop, Delta)
                        if name in (
                            "Payload/Roblox.app/libgloop.dylib",
                            "Payload/Roblox.app/Delta.dylib",
                            "Payload/Roblox.app/Frameworks/libgloop.dylib",
                            "Payload/Roblox.app/Frameworks/Delta.dylib",
                        ):
                            print(f"[PURGE] Completely removed third-party injector dylib -> {name}")
                            continue

                        data = zin.read(name)

                        if name == "Payload/Roblox.app/Roblox":
                            data = remove_load_dylib(data, ["libgloop", "gloop", "delta", "libmasterstrap"])
                            has_robloxlib = any("Frameworks/RobloxLib.framework/RobloxLib" in n for n in zin.namelist())
                            if not has_robloxlib:
                                data = insert_load_dylib(data, "@executable_path/Frameworks/libmasterstrap.dylib")
                        elif name == "Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib":
                            data = remove_load_dylib(data, ["libgloop", "gloop", "delta", "libmasterstrap"])
                            data = patch_robloxlib_fps_clamps(bytearray(data), out_dir / "fps_patch_profile.json")
                            data = bytes(data)
                            data = insert_load_dylib(data, "@rpath/libmasterstrap.dylib")
                        elif name.endswith("Roblox.app/Info.plist"):
                            try:
                                pl = plistlib.loads(data)
                                bridge_scheme = sanitize_and_configure_roblox_plist(pl)
                                data = plistlib.dumps(pl)
                                print(
                                    f"[PURIFY] Info.plist: restored bundle id '{pl['CFBundleIdentifier']}', "
                                    f"renamed to Masterstrap, enabled file sharing, "
                                    f"high-refresh, and registered bridge scheme '{bridge_scheme}'"
                                )
                            except Exception as e:
                                print(f"Warning: Failed to update Info.plist: {e}")

                        zout.writestr(item, data)
                        seen_entries.add(name)


                    # Embed dylib
                    dylib_entry = "Payload/Roblox.app/Frameworks/libmasterstrap.dylib"
                    zout.writestr(dylib_entry, dylib_bytes)

                    # Font asset embedding disabled per user instruction
                    print("[ASSET_FONT] Font embedding disabled (pure stock assets)")

                    # Embed baseline JSON only for normal builds. Isolation builds
                    # intentionally depend on the versioned Masterstrap bridge.
                    if embed_flags:
                        json_bytes = json.dumps(flags, indent=4).encode("utf-8")
                        turbo_default = {
                            "schema": 1,
                            "enabled": True,
                            "targetFrameRate": 10000,
                            "displayRefreshContract": True,
                            "keepDisplayActive": True,
                            "stretchScreenPercent": 80,
                            "rendererMode": "Automatic"
                        }
                        turbo_bytes = json.dumps(turbo_default, indent=4).encode("utf-8")
                        xml_default = (
                            '<?xml version="1.0" encoding="UTF-8"?>\n'
                            '<roblox version="4">\n'
                            '  <Item class="UserGameSettings" referent="RBX0">\n'
                            '    <Properties>\n'
                            '      <bool name="ReducedMotion">false</bool>\n'
                            '      <float name="HapticStrength">1.000000</float>\n'
                            '      <int name="FramerateCap">10000</int>\n'
                            '      <bool name="PlayerNamesEnabled">true</bool>\n'
                            '    </Properties>\n'
                            '  </Item>\n'
                            '</roblox>\n'
                        ).encode("utf-8")
                        for jp in embedded_settings_entries:
                            if jp.endswith(".xml"):
                                zout.writestr(jp, xml_default)
                            elif jp.endswith("MasterstrapTurboProfile.json"):
                                zout.writestr(jp, turbo_bytes)
                            else:
                                zout.writestr(jp, json_bytes)

            print(f"\nSUCCESS -> {out_ipa} ({out_ipa.stat().st_size / (1024*1024):.1f} MB)")
            import hashlib
            with zipfile.ZipFile(out_ipa) as built:
                names = built.namelist()
                if len(names) != len(set(names)):
                    raise RuntimeError("Packaged IPA contains duplicate ZIP paths")
            identity = {
                "commit": os.environ.get("GITHUB_SHA", "local"),
                "runId": os.environ.get("GITHUB_RUN_ID", "local"),
                "platform": sys.platform, "version": roblox_version,
                "buildType": os.environ.get("MASTERSTRAP_BUILD_TYPE", "Release"),
                "engineUuid": expected_uuid,
                "dylibSha256": hashlib.sha256(dylib_bytes).hexdigest(),
                "ipaSha256": hashlib.sha256(out_ipa.read_bytes()).hexdigest(),
                "bridge": {"inlineProtocol": 3, "cloudProtocol": 2, "completeProfiles": 5},
            }
            (out_dir / "bridge_build_identity.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")

            # Emit version to GitHub Actions output if in CI environment
            import os as _os
            _gho = _os.environ.get("GITHUB_OUTPUT", "")
            if _gho:
                with open(_gho, "a", encoding="utf-8") as _f:
                    _f.write(f"ipa_version={roblox_version}\n")
                    _f.write(f"ipa_name={out_ipa.name}\n")
                print(f"[CI] GITHUB_OUTPUT: ipa_version={roblox_version}, ipa_name={out_ipa.name}")

            mumu_dir = Path(r"C:\Users\Administrator\Documents\MuMuSharedFolder\Download\Masterstrap")
            if mumu_dir.is_dir():
                dest = mumu_dir / out_ipa.name
                import shutil
                shutil.copy2(out_ipa, dest)
                print(f"Copied to MuMu Shared Folder -> {dest}")




def main() -> int:
    ap = argparse.ArgumentParser(description="Masterstrap Roblox iOS IPA Builder")
    ap.add_argument("--input-ipa", default="")
    ap.add_argument("--settings-json", default="")
    ap.add_argument("--hpp", default="")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument(
        "--no-embedded-flags",
        action="store_true",
        help="Build Roblox patched with zero baseline flags and no packaged settings JSON",
    )
    args = ap.parse_args()

    build_pipeline(
        Path(args.input_ipa) if args.input_ipa else None,
        Path(args.settings_json) if args.settings_json else None,
        Path(args.hpp) if args.hpp else None,
        Path(args.out_dir),
        embed_flags=not args.no_embedded_flags,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
