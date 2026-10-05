#!/usr/bin/env python3
"""
Masterstrap iOS IPA Builder & Live Integrator.
Dumps offsets, compiles the native iOS ARM64 bridge with guarded direct-RVA
FastFlag writes, embeds ClientAppSettings.json, tracks miss_offsets, and
packages a ready-to-sideload IPA. Engine SetFFlag/GetFastFlag hooks are not
claimed until their iOS-specific ABI and signatures are independently verified.
"""

import argparse
import hashlib
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

# Scanner evidence for these roles must never become runtime hook targets.
# Promote only after an exact ABI or writable-field contract is proven.
DIAGNOSTIC_ONLY_OFFSETS = frozenset({
    "rbx_abortlogger", "rbx_reportbytecode", "luau_validateheader",
    "scripttimeoutcheck", "validatecaller", "validateandsetupcaps",
    "telemetrybuffer", "telemetrysize",
})

# Roblox has shipped the refresh limiter under both names. Alias only changes
# lookup; address still must come from this build's verified dumper map.
FFLAG_ALIASES = {
    "FFlagTaskSchedulerLimitTargetFpsToScreenRefreshRate": (
        "FFlagLimitTargetFpsToScreenRefresh",
        "DFFlagTaskSchedulerLimitTargetFpsToScreenRefreshRate",
    ),
    "FFlagTaskSchedulerLimitTargetFpsTo2402": (
        "FFlagTaskSchedulerLimitTargetFpsTo240",
        "DFFlagTaskSchedulerLimitTargetFpsTo2402",
    ),
    "FFlagDebugDisableVsync": (
        "FFlagDebugNoVsync",
        "DFFlagDebugNoVsync",
    ),
}


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
    plist["CADisableMinimumFrameDuration"] = True
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


def verify_ipa_profile(input_ipa: Path, header: Path) -> tuple[str, str]:
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
    sha256 = str(result.checks["sha256"])
    print(f"Verified local iOS profile: UUID={identity} SHA256={sha256}")
    return identity, sha256


def resolve_flags_with_miss_report(flags: dict, table: dict):
    prefixes = ['DFInt', 'DFFlag', 'FFlag', 'FInt', 'DFString', 'FString']
    verified_entries = []
    miss_offsets = []

    for name, val in flags.items():
        rva = None
        if name in table:
            rva = table[name]
        else:
            stripped = name
            for p in prefixes:
                if name.startswith(p):
                    stripped = name[len(p):]
                    break
            for candidate in [stripped] + [p + stripped for p in prefixes]:
                if candidate in table:
                    rva = table[candidate]
                    break
        if rva is None:
            for alias in FFLAG_ALIASES.get(name, ()):
                if alias in table:
                    rva = table[alias]
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
    table = {}
    fallback_paths = [
        ROOT / "output" / "2.735.1135" / "Offsets.hpp",
        ROOT / "roblox" / "output" / "2.735.1135" / "Offsets.hpp",
        ROOT / "output" / "Offsets.hpp",
        Path(r"C:\Users\Administrator\Desktop\Website\Data\IOS\IOS_Offsets.hpp")
    ]
    for fb in fallback_paths:
        if fb.is_file():
            content = fb.read_text(encoding="utf-8", errors="ignore")
            for line in content.splitlines():
                m = re.search(r'inline\s+constexpr\s+std::uintptr_t\s+(\w+)\s*=\s*(0x[0-9A-Fa-f]+);', line)
                if m:
                    table[m.group(1)] = int(m.group(2), 16)
            if table:
                break

    if local_hpp and local_hpp.is_file():
        content = local_hpp.read_text(encoding="utf-8", errors="ignore")
        for line in content.splitlines():
            m = re.search(r'inline\s+constexpr\s+std::uintptr_t\s+(\w+)\s*=\s*(0x[0-9A-Fa-f]+);', line)
            if m:
                table[m.group(1)] = int(m.group(2), 16)

    return table


def generate_native_hook_source(entries: list, miss_offsets: list, total_flags_count: int, offset_entries: list = None, all_flags_table: dict = None, expected_uuid: str = None, workflow_profile: dict | None = None, shader_enabled: bool = True, stretch_enabled: bool = True) -> str:
    import uuid
    uuid_bytes = uuid.UUID(expected_uuid).bytes if expected_uuid else bytes(16)
    if offset_entries is None:
        offset_entries = []
    offset_entries = [(name, rva) for name, rva in offset_entries
                      if name not in DIAGNOSTIC_ONLY_OFFSETS]
    if all_flags_table is None:
        all_flags_table = {}
    workflow_profile = workflow_profile or {}
    shader_abi_ready = bool(workflow_profile.get("shaderAbiReady"))

    sorted_all_flags = sorted(all_flags_table.items(), key=lambda x: x[0])

    lines = [
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
        "typedef unsigned short uint16_t;",
        "typedef long long intptr_t;",
        "extern uint32_t _dyld_image_count(void);",
        "extern const char* _dyld_get_image_name(uint32_t image_index);",
        "extern const void* _dyld_get_image_header(uint32_t image_index);",
        "extern intptr_t _dyld_get_image_vmaddr_slide(uint32_t image_index);",
        "extern int mprotect(void *addr, size_t len, int prot);",
        "extern int usleep(unsigned int usec);",
        "extern int getpid(void);",
        "extern long time(long *timer);",
        "extern char* getenv(const char *name);",
        "extern int snprintf(char *str, size_t size, const char *format, ...);",
        "extern int vsnprintf(char *str, size_t size, const char *format, __builtin_va_list ap);",
        "extern void syslog(int priority, const char *format, ...);",
        "extern void* fopen(const char *filename, const char *mode);",
        "extern int fseek(void *stream, long offset, int whence);",
        "extern long ftell(void *stream);",
        "extern size_t fread(void *ptr, size_t size, size_t count, void *stream);",
        "extern int fprintf(void *stream, const char *format, ...);",
        "extern int fclose(void *stream);",
        "extern int fflush(void *stream);",
        "extern int unlink(const char *path);",
        "extern void* malloc(size_t size);",
        "extern void free(void *ptr);",
        "extern long long atoll(const char *str);",
        "extern size_t strlen(const char *str);",
        "extern int strcmp(const char *s1, const char *s2);",
        "extern int strncmp(const char *s1, const char *s2, size_t n);",
        "extern int pthread_create(void **thread, const void *attr, void *(*start_routine)(void *), void *arg);",
        "extern int pthread_detach(void *thread);",
        "extern char* strstr(const char *haystack, const char *needle);",
        "extern char* strrchr(const char *s, int c);",
        "extern char* strcpy(char *dest, const char *src);",
        "extern char* strdup(const char *s);",
        "extern void* memcpy(void *dest, const void *src, size_t n);",
        "extern void* memmove(void *dest, const void *src, size_t n);",
        "extern int fputs(const char *str, void *stream);",
        "extern int open(const char *path, int oflag, ...);",
        "extern int access(const char *path, int amode);",
        "extern int mkdir(const char *path, unsigned short mode);",
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
        kind = 0 if ('Flag' in name or (name.startswith('F') and not name.startswith('FInt') and not name.startswith('DFInt') and not name.startswith('FString') and not name.startswith('DFString'))) else 1
        lines.append(f'    {{"{name}", 0x{rva:X}, {kind}}},')

    lines.extend([
        "};",
        f"static const size_t kMasterFlagsCount = {len(sorted_all_flags)};",
        "typedef struct { const MasterFlagEntry* flag; int kind; int64_t value; } CachedDynamicFlag;",
        "#define MAX_CACHED_DYNAMIC_FLAGS 1024",
        "static CachedDynamicFlag g_cached_dynamic_flags[MAX_CACHED_DYNAMIC_FLAGS];",
        "static size_t g_cached_dynamic_count = 0;",
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
        "static const char* kFpsRuntimeBackend = \"cadisplaylink-metal-drawable-decouple-v6\";",
        "static const char* kFontRuntimeBackend = \"native-asset-vfs-v1\";",
        f"static const bool kShaderAbiProfileReady = {'true' if shader_abi_ready else 'false'};",
        f"static const bool kShaderEnabled = {'true' if shader_enabled else 'false'};",
        f"static const bool kStretchEnabled = {'true' if stretch_enabled else 'false'};",
        "static const char* kShaderRuntimeBackend = \"ios-workflow-profile-v1\";",
        "static const char* kStretchRuntimeBackend = \"view-projection-v73\";",
        "",
        "static const char* kMissOffsets[] = {"
    ])

    for m in miss_offsets:
        lines.append(f'    "{m}",')

    lines.extend([
        "    NULL",
        "};",
        "",
        "static bool macho_rva_is_executable(uintptr_t base, uintptr_t rva);",
        "",
        "static bool native_fps_owns_flag(const char* name) {",
        "    if (!name) return false;",
        "    const char* prefixes[] = {\"DFInt\", \"DFFlag\", \"FFlag\", \"FInt\", NULL};",
        "    for (int i = 0; prefixes[i]; ++i) {",
        "        size_t n = strlen(prefixes[i]);",
        "        if (strncmp(name, prefixes[i], n) == 0) { name += n; break; }",
        "    }",
        "    const char* owned[] = {\"TaskSchedulerTargetFps\", \"TaskSchedulerLimitTargetFpsToScreenRefreshRate\",",
        "        \"LimitTargetFpsToScreenRefresh\", \"TaskSchedulerLimitTargetFpsTo2402\",",
        "        \"TaskSchedulerLimitTargetFpsTo240\", \"GameBasicSettingsFramerateCap\",",
        "        \"PerformanceControlExecution\", \"PerformanceControlDynamicFrameTimeTarget\",",
        "        \"PerformanceControlComputeUseTSFrameTimeTarget\", \"PerformanceControlDisplayRefreshRate\",",
        "        \"PerformanceControlDisplayRefreshRateInAdaptiveSync\", \"PerformanceControlDisplayRefreshRateMigration\",",
        "        \"AuroraLimit60HzRenderSim\", \"ApplicationFrameRate\", \"ApplicationFrameRatePolicy\",",
        "        \"MetalVsyncPreferMaximumRefreshRate\", \"MetalDisplayTiming\", \"MetalDisplayTimingAdjustMinFPS\",",
        "        \"MetalDisplayTimingMinFPS\", \"DebugDisableVsync\", \"DebugNoVsync\",",
        "        \"DebugExperimentalVSyncFramePacing\", \"DebugExperimentalVSyncFramePacingForce\", NULL};",
        "    for (int i = 0; owned[i]; ++i) if (strcmp(name, owned[i]) == 0) return true;",
        "    return false;",
        "}",
        "",
        "static bool SetFastFlag(uintptr_t base, const FFlagPatchEntry* entry) {",
        "    if (!base || !entry || !entry->rva) return false;",
        "    if (native_fps_owns_flag(entry->name)) return false;",
        "    if (macho_rva_is_executable(base, entry->rva)) return false;",
        "    uintptr_t target = base + entry->rva;",
        "    uintptr_t page_size = 16384;",
        "    uintptr_t page_start = target & ~(page_size - 1);",
        "    size_t page_len = page_size * 2;",
        "    if (mprotect((void*)page_start, page_len, PROT_READ | PROT_WRITE) != 0) {",
        "        page_size = 4096;",
        "        page_start = target & ~(page_size - 1);",
        "        if (mprotect((void*)page_start, page_size * 2, PROT_READ | PROT_WRITE) != 0) {",
        "            return false;",
        "        }",
        "    }",
        "",
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
        "static const MasterFlagEntry* lookup_flag_with_fallback(const char* name);",
        "static inline int get_effective_target_fps(void);",
        "static void runtime_note(const char* fmt, ...);",
        "/* Native FPS policy uses the same verified registry entries as JSON flags. */",
        "typedef struct { const char* name; int kind; int64_t value; uintptr_t rva; } MSOwnedFpsFlag;",
        "#define MS_OWNED_FPS_COUNT 24",
        "static MSOwnedFpsFlag g_owned_fps[MS_OWNED_FPS_COUNT];",
        "static size_t g_owned_fps_count = 0;",
        "static int g_fps_critical_missing = 0;",
        "static int g_static_fps_patch_verified = 0;",
        "static bool static_fps_patch_contract_ok(uintptr_t base) {",
        "    /* Builder patches these five RobloxLib sites only after exact-byte checks. */",
        "    const uintptr_t rvas[] = {0x46256c4, 0x9165fc, 0x4628128, 0x4a60e28, 0x3f64ae4};",
        "    const uint32_t opcodes[] = {0x2a0803e9, 0xd503201f, 0x2a1703e8, 0x38d1b717, 0xd503201f};",
        "    for (size_t i = 0; i < sizeof(rvas) / sizeof(rvas[0]); ++i) {",
        "        if (!macho_rva_is_executable(base, rvas[i])) return false;",
        "        if (*(volatile uint32_t*)(base + rvas[i]) != opcodes[i]) return false;",
        "    }",
        "    return true;",
        "}",
        "static bool SetOwnedFastFlag(uintptr_t base, const FFlagPatchEntry* entry) {",
        "    if (!base || !entry || !entry->rva) return false;",
        "    if (macho_rva_is_executable(base, entry->rva)) return false;",
        "    uintptr_t target = base + entry->rva;",
        "    uintptr_t page_size = 16384;",
        "    uintptr_t page_start = target & ~(page_size - 1);",
        "    if (mprotect((void*)page_start, page_size * 2, PROT_READ | PROT_WRITE) != 0) {",
        "        page_size = 4096; page_start = target & ~(page_size - 1);",
        "        if (mprotect((void*)page_start, page_size * 2, PROT_READ | PROT_WRITE) != 0) return false;",
        "    }",
        "    if (entry->kind == 0) {",
        "        volatile uint8_t* ptr = (volatile uint8_t*)target; *ptr = (uint8_t)(entry->value ? 1 : 0);",
        "        return *ptr == (uint8_t)(entry->value ? 1 : 0);",
        "    }",
        "    volatile int32_t* ptr = (volatile int32_t*)target; *ptr = (int32_t)entry->value;",
        "    return *ptr == (int32_t)entry->value;",
        "}",
        "",
        "static void remember_owned_flag(const char* name, int kind, int64_t value) {",
        "    const MasterFlagEntry* flag = lookup_flag_with_fallback(name);",
        "    if (!flag) {",
        "        if (!g_static_fps_patch_verified && (strcmp(name, \"DFIntTaskSchedulerTargetFps\") == 0 || strcmp(name, \"FFlagTaskSchedulerLimitTargetFpsToScreenRefreshRate\") == 0 || strcmp(name, \"FFlagTaskSchedulerLimitTargetFpsTo2402\") == 0 || strcmp(name, \"FFlagDebugDisableVsync\") == 0)) g_fps_critical_missing = 1;",
        "        runtime_note(\"[FPS_FLAG_MISS] %s status=NO_VERIFIED_RVA\", name); return;",
        "    }",
        "    if (g_owned_fps_count >= MS_OWNED_FPS_COUNT) return;",
        "    g_owned_fps[g_owned_fps_count++] = (MSOwnedFpsFlag){name, kind, value, (uintptr_t)flag->rva};",
        "}",
        "",
        "static int apply_native_fps_registry(uintptr_t base) {",
        "    if (!base) return 0;",
        "    g_owned_fps_count = 0;",
        "    g_fps_critical_missing = 0;",
        "    g_static_fps_patch_verified = static_fps_patch_contract_ok(base) ? 1 : 0;",
        "    runtime_note(\"[FPS_PATCH] staticContract=%s\", g_static_fps_patch_verified ? \"VERIFIED\" : \"UNVERIFIED\");",
        "    remember_owned_flag(\"DFIntTaskSchedulerTargetFps\", 1, get_effective_target_fps());",
        "    remember_owned_flag(\"FFlagTaskSchedulerLimitTargetFpsToScreenRefreshRate\", 0, 0);",
        "    remember_owned_flag(\"FFlagTaskSchedulerLimitTargetFpsTo2402\", 0, 0);",
        "    remember_owned_flag(\"FFlagPerformanceControlExecution\", 0, 0);",
        "    remember_owned_flag(\"FFlagPerformanceControlDynamicFrameTimeTarget\", 0, 0);",
        "    remember_owned_flag(\"FFlagPerformanceControlComputeUseTSFrameTimeTarget\", 0, 0);",
        "    remember_owned_flag(\"FFlagPerformanceControlDisplayRefreshRate\", 0, 0);",
        "    remember_owned_flag(\"FFlagPerformanceControlDisplayRefreshRateInAdaptiveSync\", 0, 0);",
        "    remember_owned_flag(\"FFlagPerformanceControlDisplayRefreshRateMigration\", 0, 0);",
        "    remember_owned_flag(\"FFlagAuroraLimit60HzRenderSim\", 0, 0);",
        "    /* Historical working path: direct engine target owns cadence; XML cap stays disabled. */",
        "    remember_owned_flag(\"FFlagGameBasicSettingsFramerateCap\", 0, 0);",
        "    remember_owned_flag(\"FFlagApplicationFrameRate\", 0, 0);",
        "    remember_owned_flag(\"FFlagApplicationFrameRatePolicy\", 0, 0);",
        "    remember_owned_flag(\"FFlagMetalVsyncPreferMaximumRefreshRate\", 0, 1);",
        "    /* Restore timing controls removed by the registry migration. */",
        "    remember_owned_flag(\"FFlagMetalDisplayTiming\", 0, 0);",
        "    remember_owned_flag(\"FFlagMetalDisplayTimingAdjustMinFPS\", 0, 0);",
        "    remember_owned_flag(\"FIntMetalDisplayTimingMinFPS\", 1, 0);",
        "    remember_owned_flag(\"FFlagDebugDisableVsync\", 0, 1);",
        "    remember_owned_flag(\"FFlagDebugExperimentalVSyncFramePacing\", 0, 0);",
        "    remember_owned_flag(\"FFlagDebugExperimentalVSyncFramePacingForce\", 0, 0);",
        "    int applied = 0;",
        "    for (size_t i = 0; i < g_owned_fps_count; ++i) {",
        "        FFlagPatchEntry entry = {g_owned_fps[i].name, g_owned_fps[i].rva, g_owned_fps[i].kind, g_owned_fps[i].value};",
        "        if (SetOwnedFastFlag(base, &entry)) { applied++; runtime_note(\"[FPS_FLAG] %s rva=0x%lx value=%lld status=VERIFIED\", g_owned_fps[i].name, (unsigned long)g_owned_fps[i].rva, (long long)g_owned_fps[i].value); }",
        "        else runtime_note(\"[FPS_FLAG] %s rva=0x%lx value=%lld status=FAIL\", g_owned_fps[i].name, (unsigned long)g_owned_fps[i].rva, (long long)g_owned_fps[i].value);",
        "    }",
        "    return applied;",
        "}",
        "",
        "static void maintain_native_fps_registry(uintptr_t base) {",
        "    if (!base) return;",
        "    for (size_t i = 0; i < g_owned_fps_count; ++i) {",
        "        FFlagPatchEntry entry = {g_owned_fps[i].name, g_owned_fps[i].rva, g_owned_fps[i].kind, g_owned_fps[i].value};",
        "        SetOwnedFastFlag(base, &entry);",
        "    }",
        "}",
        "",
        "static void cache_dynamic_flag(const MasterFlagEntry* flag, int kind, int64_t value) {",
        "    if (!flag) return;",
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
        "    const char* aliases[4] = {NULL, NULL, NULL, NULL};",
        "    if (strcmp(name, \"FFlagTaskSchedulerLimitTargetFpsToScreenRefreshRate\") == 0) { aliases[0] = \"FFlagLimitTargetFpsToScreenRefresh\"; aliases[1] = \"DFFlagTaskSchedulerLimitTargetFpsToScreenRefreshRate\"; }",
        "    else if (strcmp(name, \"FFlagTaskSchedulerLimitTargetFpsTo2402\") == 0) { aliases[0] = \"FFlagTaskSchedulerLimitTargetFpsTo240\"; aliases[1] = \"DFFlagTaskSchedulerLimitTargetFpsTo2402\"; }",
        "    else if (strcmp(name, \"FFlagDebugDisableVsync\") == 0) { aliases[0] = \"FFlagDebugNoVsync\"; aliases[1] = \"DFFlagDebugNoVsync\"; }",
        "    for (int ai = 0; aliases[ai]; ++ai) {",
        "        low = 0; high = (int)kMasterFlagsCount - 1;",
        "        while (low <= high) { int mid = low + (high - low) / 2; int cmp = strcmp(aliases[ai], kMasterFlags[mid].name);",
        "            if (cmp == 0) return &kMasterFlags[mid];",
        "            if (cmp < 0) high = mid - 1; else low = mid + 1;",
        "        }",
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
        "static char g_shader_package[64];",
        "static bool g_shader_wet_floor;",
        "static void runtime_note(const char* fmt, ...);",
        "",
        "static int shader_sky_selector(void) {",
        "    if (strcmp(g_shader_package, \"daytime\") == 0) return 2001;",
        "    if (strcmp(g_shader_package, \"rtx_ultra\") == 0 || strcmp(g_shader_package, \"RTX High\") == 0) return 2002;",
        "    if (strcmp(g_shader_package, \"sunset\") == 0) return 2003;",
        "    if (strcmp(g_shader_package, \"night\") == 0) return 2004;",
        "    if (strcmp(g_shader_package, \"cloudy\") == 0) return 2005;",
        "    if (strcmp(g_shader_package, \"desert\") == 0) return 2006;",
        "    if (strcmp(g_shader_package, \"arctic\") == 0) return 2007;",
        "    if (strcmp(g_shader_package, \"blood_moon\") == 0) return 2008;",
        "    if (strcmp(g_shader_package, \"cyberpunk\") == 0) return 2009;",
        "    if (strcmp(g_shader_package, \"cherry_blossom\") == 0) return 2010;",
        "    if (strcmp(g_shader_package, \"ghibli_anime\") == 0) return 2011;",
        "    if (strcmp(g_shader_package, \"synthwave\") == 0) return 2012;",
        "    if (strcmp(g_shader_package, \"noir\") == 0) return 2013;",
        "    return 2002;",
        "}",
        "",
        "static void apply_shader_lighting_flags(uintptr_t base) {",
        "    if (!kShaderEnabled || !base || (strcmp(g_shader_package, \"none\") == 0 && !g_shader_wet_floor)) return;",
        "    if (!kShaderAbiProfileReady) { static int warned = 0; if (!warned) { runtime_note(\"[SHADER][PROFILE_ONLY] exact iOS Lua workflow signature audit pending; applying native flag fallback only\"); warned = 1; } }",
        "    int sky_selector = shader_sky_selector();",
        "    int profile_selector = sky_selector + (g_shader_wet_floor ? 1000 : 0);",
        "    const struct { const char* name; int kind; int64_t value; } flags[] = {",
        "        {\"FFlagGenericCreateSurfaceAppearanceMaterial\", 0, 1},",
        "        {\"FFlagRenderEnablePartBaseMaterial4K\", 0, 1},",
        "        {\"FFlagEnableMaterialSlotTable\", 0, 1},",
        "        {\"FFlagAllowDontDeformMaterials\", 0, 1},",
        "        {\"FFlagSlimUseMultiMaterialWMS\", 0, 1},",
        "        {\"FFlagRenderDecoupleShadingDistanceFromShadows\", 0, 1},",
        "        {\"FIntUnifiedLightingBlendZone\", 1, 1},",
        "        {\"FFlagDebugTerrainForceHQShader\", 0, 1},",
        "        {\"FFlagRenderInitShadowmaps\", 0, 1},",
        "        {\"FIntRenderShadowHugeRadius\", 1, profile_selector},",
        "        {\"FIntBloomFrmCutoff\", 1, 0},",
        "        {\"FFlagMetalRuntimeShaders\", 0, 1},",
        "        {\"FFlagMTLRuntimeShaderCompilation\", 0, 1},",
        "        {\"FFlagDisablePostFx\", 0, 0},",
        "        {\"DFIntRenderPostFxBasePixelCount\", 1, 2000000},",
        "        {\"DFIntDebugFRMQualityLevelOverride\", 1, 10},",
        "    };",
        "    int applied = 0;",
        "    for (size_t i = 0; i < sizeof(flags) / sizeof(flags[0]); ++i) {",
        "        const MasterFlagEntry* flag = lookup_flag_with_fallback(flags[i].name);",
        "        if (!flag) { runtime_note(\"[SHADER][FLAG_MISS] name=%s requested=%lld reason=NO_VERIFIED_RVA\", flags[i].name, (long long)flags[i].value); continue; }",
        "        FFlagPatchEntry patch = {flag->name, flag->rva, flags[i].kind, flags[i].value};",
        "        if (SetFastFlag(base, &patch)) applied++;",
        "        else runtime_note(\"[SHADER][FLAG_MISS] name=%s requested=%lld reason=WRITE_REJECTED\", flags[i].name, (long long)flags[i].value);",
        "    }",
        "    runtime_note(\"[SHADER][NATIVE] package=%s profileSelector=%d wetFloor=%d applied=%d requested=%zu backend=rbxm-luau-native-flags-v2 postfx=requested runtimeVerified=0\",",
        "        g_shader_package, profile_selector, g_shader_wet_floor, applied, sizeof(flags) / sizeof(flags[0]));",
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
        "static bool shader_conflicting_flag(const char* name) {",
        "    if (!name) return false;",
        "    if (!kShaderEnabled && strcmp(name, \"FIntRenderShadowHugeRadius\") == 0) return true;",
        "    if (!(g_shader_wet_floor || strcmp(g_shader_package, \"none\") != 0)) return false;",
        "    const char* blocked[] = {\"FFlagDisablePostFx\", \"DFIntRenderPostFxBasePixelCount\", \"DFIntDebugFRMQualityLevelOverride\", NULL};",
        "    for (int i = 0; blocked[i]; ++i) if (strcmp(name, blocked[i]) == 0) return true;",
        "    return false;",
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
        "        if (native_fps_owns_flag(key)) {",
        "            if (log_file) fprintf(log_file, \"[DYNAMIC_JSON_SKIP] %s source=NATIVE_FPS_PROFILE\\n\", key);",
        "            continue;",
        "        }",
        "        if (shader_conflicting_flag(key)) {",
        "            if (log_file) fprintf(log_file, \"[DYNAMIC_JSON_SKIP] %s source=SHADER_PROFILE\\n\", key);",
        "            continue;",
        "        }",
        "        if (flag) {",
        "            FFlagPatchEntry entry;",
        "            entry.name = flag->name;",
            "            entry.rva = (uintptr_t)flag->rva;",
            "            entry.kind = flag->kind; // Storage width comes from the verified map, not JSON syntax.",
            "            entry.value = num_val;",
            "            cache_dynamic_flag(flag, entry.kind, entry.value);",
            "            bool ok = SetFastFlag(base, &entry);",
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
        "static volatile bool g_launch_authorized = false;",
        "static volatile bool g_guard_thread_started = false;",
        "static uintptr_t g_engine_base = 0;",
        "static bool g_game_mode_enabled = false;",
        "static bool g_refresh_requested = false;",
        "static bool g_keep_display_active = false;",
        "static int g_target_fps = 60;",
        "static int g_stretch_percent = 100;",
        "static char g_renderer_mode[32] = \"Automatic\";",
        "static char g_shader_package[64] = \"none\";",
        "static bool g_shader_wet_floor = false;",
        "static char g_font_family[64] = \"Default\";",
        "static int g_font_size_step = 0;",
        "static int ms_ios_engine_fps(int requested);",
        "static int ms_ios_xml_fps(const char* xml, int fallback);",
        "static inline int get_effective_target_fps(void) {",
        "    return ms_ios_engine_fps(g_target_fps);",
        "}",
        "static void runtime_note(const char* fmt, ...);",
        "static void ensure_fps_slot_guard_thread(uintptr_t base);",
        "static uintptr_t get_robloxlib_base(void);",
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
        "    const char* home = getenv(\"HOME\");",
        "    if (!file_name) return NULL;",
        "    char path[768];",
        "    void* file = NULL;",
        "    if (home) {",
        "        snprintf(path, sizeof(path), \"%s/Documents/%s\", home, file_name);",
        "        file = fopen(path, \"r\");",
        "        if (!file) {",
        "            snprintf(path, sizeof(path), \"%s/Documents/ClientSettings/%s\", home, file_name);",
        "            file = fopen(path, \"r\");",
        "        }",
        "    }",
        "    if (!file) {",
        "        id NSBundle = objc_getClass(\"NSBundle\");",
        "        if (NSBundle) {",
        "            id bundle = ((id (*)(id, SEL))objc_msgSend)(NSBundle, sel_registerName(\"mainBundle\"));",
        "            if (bundle) {",
        "                id bundle_path_ns = ((id (*)(id, SEL))objc_msgSend)(bundle, sel_registerName(\"bundlePath\"));",
        "                if (bundle_path_ns) {",
        "                    const char* bundle_path = ((const char* (*)(id, SEL))objc_msgSend)(bundle_path_ns, sel_registerName(\"UTF8String\"));",
        "                    if (bundle_path) {",
        "                        snprintf(path, sizeof(path), \"%s/%s\", bundle_path, file_name);",
        "                        file = fopen(path, \"r\");",
        "                        if (!file) {",
        "                            snprintf(path, sizeof(path), \"%s/ClientSettings/%s\", bundle_path, file_name);",
        "                            file = fopen(path, \"r\");",
        "                        }",
        "                    }",
        "                }",
        "            }",
        "        }",
        "    }",
        "    if (!file) return NULL;",
        "    fseek(file, 0, 2);",
        "    long size = ftell(file);",
        "    fseek(file, 0, 0);",
        "    if (size <= 0 || size >= 65536) { fclose(file); return NULL; }",
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
        "    g_launch_authorized = true;",
        "    if (log_file) fprintf(log_file, \"[SESSION][AUTH] Persistent Masterstrap authorization active. Decision=ALLOW_ALWAYS\\n\");",
        "    runtime_note(\"[SESSION][AUTH] Persistent Masterstrap authorization active. Decision=ALLOW_ALWAYS\");",
        "    return true;",
        "}",
        "",
        "static int purge_local_launch_configuration(const char* reason, bool write_receipt) {",
        "    const char* home = getenv(\"HOME\");",
        "    if (write_receipt && home) {",
        "        char path[768]; snprintf(path, sizeof(path), \"%s/Documents/MasterstrapConsumedReceipt.json\", home);",
        "        void* receipt = fopen(path, \"w\");",
        "        if (receipt) { fprintf(receipt, \"{\\\"schema\\\":1,\\\"singleUse\\\":true,\\\"reason\\\":\\\"%s\\\",\\\"consumedAtUnix\\\":%ld}\\n\", reason ? reason : \"unknown\", time(NULL)); fclose(receipt); }",
        "    }",
        "    runtime_note(\"[SESSION][PERSIST] reason=%s cachedFlags=%zu status=PERSISTED receipt=%d\", reason ? reason : \"unknown\", g_cached_dynamic_count, write_receipt ? 1 : 0);",
        "    return 0;",
        "}",
        "",
        "static void load_and_apply_runtime_profiles(void* log_file) {",
        "    // Every new request replaces the previous profile, including missing fields.",
        "    g_profile_loaded = g_game_mode_enabled = g_refresh_requested = g_keep_display_active = false;",
        "    g_target_fps = 60; g_stretch_percent = 100; g_shader_wet_floor = false; g_font_size_step = 0;",
        "    snprintf(g_renderer_mode, sizeof(g_renderer_mode), \"Automatic\");",
        "    snprintf(g_shader_package, sizeof(g_shader_package), \"none\");",
        "    snprintf(g_font_family, sizeof(g_font_family), \"Default\");",
        "    long turbo_size = 0;",
        "    char* turbo = read_profile_file(\"MasterstrapTurboProfile.json\", &turbo_size);",
        "    g_profile_loaded = turbo != NULL;",
        "    if (turbo) {",
        "        g_game_mode_enabled = profile_read_bool(turbo, \"enabled\", \"game_mode_master\", true);",
        "        g_refresh_requested = profile_read_bool(turbo, \"displayRefreshContract\", \"turbo_refresh_contract\", true);",
        "        g_keep_display_active = profile_read_bool(turbo, \"keepDisplayActive\", \"keep_display_active\", true);",
        "        g_target_fps = profile_read_int(turbo, \"targetFps\", \"targetFrameRate\", 9999);",
        "        g_stretch_percent = profile_read_int(turbo, \"stretchScreenPercent\", \"display_stretch_screen\", 100);",
        "        profile_read_string(turbo, \"rendererMode\", \"display_renderer_mode\", g_renderer_mode, sizeof(g_renderer_mode));",
        "        profile_read_string(turbo, \"display_shader_package\", \"package\", g_shader_package, sizeof(g_shader_package));",
        "        g_shader_wet_floor = profile_read_bool(turbo, \"display_shader_wet_floor\", \"wetFloor\", false);",
        "        profile_read_string(turbo, \"display_font_family\", \"font\", g_font_family, sizeof(g_font_family));",
        "        g_font_size_step = profile_read_signed_int(turbo, \"display_font_size_step\", 0);",
        "        if (!kShaderEnabled) { snprintf(g_shader_package, sizeof(g_shader_package), \"none\"); g_shader_wet_floor = false; }",
        "        if (!kStretchEnabled) g_stretch_percent = 100;",
        "        if (log_file) fprintf(log_file, \"[PROFILE][TURBO] status=LOADED bytes=%ld enabled=%d targetFps=%d refresh=%d keepDisplay=%d stretch=%d renderer=%s shader=%s wetFloor=%d font=%s step=%d\\n\", turbo_size, g_game_mode_enabled, g_target_fps, g_refresh_requested, g_keep_display_active, g_stretch_percent, g_renderer_mode, g_shader_package, g_shader_wet_floor, g_font_family, g_font_size_step);",
        "        runtime_note(\"[PROFILE][TURBO] status=LOADED bytes=%ld enabled=%d targetFps=%d refresh=%d keepDisplay=%d stretch=%d renderer=%s shader=%s wetFloor=%d font=%s step=%d\", turbo_size, g_game_mode_enabled, g_target_fps, g_refresh_requested, g_keep_display_active, g_stretch_percent, g_renderer_mode, g_shader_package, g_shader_wet_floor, g_font_family, g_font_size_step);",
        "        free(turbo);",
        "    } else {",
        "        if (log_file) fprintf(log_file, \"[PROFILE][TURBO] status=MISSING\\n\");",
        "        runtime_note(\"[PROFILE][TURBO] status=MISSING defaults=ACTIVE\");",
        "    }",
        "",
        "    // Match Android: FramerateCap owns engine FPS; targetFrameRate is a legacy performance hint.",
        "    long global_size = 0;",
        "    char* global_xml = read_profile_file(\"GlobalBasicSettings_13.xml\", &global_size);",
        "    if (!global_xml) global_xml = read_profile_file(\"GlobalBasicSettings.xml\", &global_size);",
        "    if (global_xml) {",
        "        int xml_target = ms_ios_xml_fps(global_xml, -1);",
        "        if (xml_target >= 30) {",
        "            g_target_fps = xml_target; g_profile_loaded = true;",
        "            runtime_note(\"[PROFILE][FPS] source=GLOBAL_XML engineTarget=%d displayBound=0\", g_target_fps);",
        "        } else runtime_note(\"[PROFILE][FPS] source=GLOBAL_XML status=INVALID_VALUE\");",
        "        free(global_xml);",
        "    }",
        "",
        "    long shader_size = 0;",
        "    char* shader = read_profile_file(\"MasterstrapShaderProfile.json\", &shader_size);",
        "    if (shader && kShaderEnabled) {",
        "        profile_read_string(shader, \"package\", \"display_shader_package\", g_shader_package, sizeof(g_shader_package));",
        "        g_shader_wet_floor = profile_read_bool(shader, \"wetFloor\", \"display_shader_wet_floor\", false);",
        "        if (log_file) fprintf(log_file, \"[PROFILE][SHADER] status=LOADED bytes=%ld package=%s wetFloor=%d enabled=%d\\n\", shader_size, g_shader_package, g_shader_wet_floor, profile_read_bool(shader, \"enabled\", NULL, false));",
        "        runtime_note(\"[PROFILE][SHADER] status=LOADED bytes=%ld package=%s wetFloor=%d enabled=%d\", shader_size, g_shader_package, g_shader_wet_floor, profile_read_bool(shader, \"enabled\", NULL, false));",
        "        free(shader);",
        "    } else { free(shader); snprintf(g_shader_package, sizeof(g_shader_package), \"none\"); g_shader_wet_floor = false; if (log_file) fprintf(log_file, \"[PROFILE][SHADER] status=%s\\n\", kShaderEnabled ? \"MISSING\" : \"DISABLED\"); runtime_note(\"[PROFILE][SHADER] status=%s\", kShaderEnabled ? \"MISSING\" : \"DISABLED\"); }",
        "",
        "    long font_size = 0;",
        "    char* font = read_profile_file(\"MasterstrapFontProfile.json\", &font_size);",
        "    if (font) {",
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
        "    if (!file) return;",
        "    fseek(file, 0, 2);",
        "    long size = ftell(file);",
        "    if (size > 524288) {",
        "        fclose(file);",
        "        file = fopen(path, \"w\");",
        "        if (!file) return;",
        "        fprintf(file, \"[%ld] [INFO] log rotated after %ld bytes\\n\", time(NULL), size);",
        "    }",
        "    fprintf(file, \"[%ld] [%s] %s\\n\", time(NULL), is_error ? \"ERROR\" : \"INFO\", message);",
        "    fflush(file);",
        "    fclose(file);",
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
        "    if (!file) return;",
        "    fseek(file, 0, 2);",
        "    long size = ftell(file);",
        "    if (size > 1048576) {",
        "        fclose(file);",
        "        file = fopen(path, \"w\");",
        "        if (!file) return;",
        "        fprintf(file, \"[%ld] [RUNTIME] log rotated after %ld bytes\\n\", time(NULL), size);",
        "    }",
        "    fprintf(file, \"[%ld] [RUNTIME] %s\\n\", time(NULL), message);",
        "    fflush(file);",
        "    fclose(file);",
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
        "        \"lua_pushlstring\", \"lua_tolstring\", \"lua_toboolean\", \"lua_next\",",
        "        \"lua_newthread\", \"luaD_precall\", \"luaU_undump\", \"luaV_execute_dispatch\",",
        "        \"resume_start\", \"resume_finish\",",
        "        \"scriptcontextresume\", \"resumewaitingscripts\", \"game_loaded\", \"ongameloaded\",",
        "        \"loadstring\", \"enableloadmodule\", NULL",
        "    };",
        "    g_abi_probe_total = g_abi_probe_executable = g_abi_probe_plausible = g_abi_probe_duplicate_roles = 0;",
        f"    g_abi_probe_call_gate_open = {'1' if shader_abi_ready else '0'}; /* exact workflow profile controls promotion */",
        "    const char* home = getenv(\"HOME\");",
        "    char receipt_path[768] = {0};",
        "    void* receipt = NULL;",
        "    if (home) { snprintf(receipt_path, sizeof(receipt_path), \"%s/Documents/MasterstrapAbiProbe.json\", home); receipt = fopen(receipt_path, \"w\"); }",
        "    if (receipt) fprintf(receipt, \"{\\\"schema\\\":1,\\\"generatedAtUnix\\\":%ld,\\\"baseFound\\\":%s,\\\"callGate\\\":\\\"%s\\\",\\\"roles\\\":[\", time(NULL), base ? \"true\" : \"false\", g_abi_probe_call_gate_open ? \"open\" : \"closed\");",
        "    bool first_receipt = true;",
        "    for (size_t role_index = 0; critical_roles[role_index]; role_index++) {",
        "        const char* role = critical_roles[role_index];",
        "        const OffsetEntry* entry = find_engine_offset(role);",
        "        g_abi_probe_total++;",
        "        bool executable = entry && macho_rva_is_executable(base, entry->rva);",
        "        uint32_t opcode = executable ? *(volatile uint32_t*)(base + entry->rva) : 0;",
        "        bool plausible = executable && arm64_opcode_is_plausible(opcode);",
        "        int duplicates = entry ? offset_role_count(entry->rva) : 0;",
        "        if (executable) g_abi_probe_executable++;",
        "        if (plausible) g_abi_probe_plausible++;",
        "        if (duplicates > 1) g_abi_probe_duplicate_roles++;",
        "        const char* status = !entry ? \"MISSING\" : !executable ? \"NON_EXECUTABLE\" : !plausible ? \"IMPLAUSIBLE_OPCODE\" : duplicates > 1 ? \"AMBIGUOUS_RVA\" : \"ADDRESS_PLAUSIBLE\";",
        "        bool call_safe = g_abi_probe_call_gate_open && executable && plausible && duplicates <= 1;",
        "        if (log_file) fprintf(log_file, \"[ABI_PROBE] role=%s rva=0x%lx executable=%d opcode=0x%08X aliases=%d status=%s callSafe=%d\\n\", role, (unsigned long)(entry ? entry->rva : 0), executable, opcode, duplicates, status, call_safe);",
        "        runtime_note(\"[ABI_PROBE] role=%s rva=0x%lx executable=%d opcode=0x%08X aliases=%d status=%s callSafe=%d\", role, (unsigned long)(entry ? entry->rva : 0), executable, opcode, duplicates, status, call_safe);",
        "        if (receipt) {",
        "            fprintf(receipt, \"%s{\\\"role\\\":\\\"%s\\\",\\\"rva\\\":\\\"0x%lx\\\",\\\"executable\\\":%s,\\\"opcode\\\":\\\"0x%08X\\\",\\\"aliases\\\":%d,\\\"status\\\":\\\"%s\\\",\\\"callSafe\\\":%s}\", first_receipt ? \"\" : \",\", role, (unsigned long)(entry ? entry->rva : 0), executable ? \"true\" : \"false\", opcode, duplicates, status, call_safe ? \"true\" : \"false\");",
        "            first_receipt = false;",
        "        }",
        "    }",
        "    if (receipt) { fprintf(receipt, \"],\\\"summary\\\":{\\\"total\\\":%d,\\\"executable\\\":%d,\\\"plausible\\\":%d,\\\"ambiguousRoles\\\":%d}}\\n\", g_abi_probe_total, g_abi_probe_executable, g_abi_probe_plausible, g_abi_probe_duplicate_roles); fflush(receipt); fclose(receipt); }",
        "    runtime_note(\"[ABI_PROBE][SUMMARY] total=%d executable=%d plausible=%d ambiguousRoles=%d callGate=%s reason=%s\", g_abi_probe_total, g_abi_probe_executable, g_abi_probe_plausible, g_abi_probe_duplicate_roles, g_abi_probe_call_gate_open ? \"OPEN\" : \"CLOSED\", g_abi_probe_call_gate_open ? \"exact-workflow-profile\" : \"function-signatures-unverified\");",
        "}",
        "",
        *(ROOT / "tools" / "ios_frame_policy.h").read_text(encoding="utf-8").splitlines(),
        *(ROOT / "tools" / "ios_display_policy.c").read_text(encoding="utf-8").splitlines(),
        "",
        "static void log_bundled_sky_assets(void);",
        "static void log_runtime_consumer_contract(void) {",
        "    log_bundled_sky_assets();",
        "    runtime_note(\"[REQUEST][FONT] family=%s sizeStep=%d\", g_font_family, g_font_size_step);",
        "    runtime_note(\"[CAPABILITY][FONT] backend=%s status=%s reason=direct-asset-vfs\", kFontRuntimeBackend, g_profile_loaded ? \"ACTIVE\" : \"INACTIVE\");",
        "    runtime_note(\"[REQUEST][SHADER] package=%s wetFloor=%d\", g_shader_package, g_shader_wet_floor);",
        "    runtime_note(\"[CAPABILITY][SHADER] backend=%s status=%s reason=%s\", kShaderRuntimeBackend, (g_profile_loaded && g_abi_probe_call_gate_open && (g_shader_wet_floor || strcmp(g_shader_package, \"none\") != 0)) ? \"ACTIVE\" : (g_profile_loaded && (g_shader_wet_floor || strcmp(g_shader_package, \"none\") != 0)) ? \"PROFILE_ONLY\" : \"INACTIVE\", g_abi_probe_call_gate_open ? \"verified-luau-contract\" : \"abi-profile-blocked\");",
        "    runtime_note(\"[REQUEST][STRETCH] percent=%d\", g_stretch_percent);",
        "    runtime_note(\"[CAPABILITY][STRETCH] backend=%s status=%s stretchPercent=%d\", kStretchRuntimeBackend, (g_profile_loaded && g_stretch_percent != 100) ? \"ACTIVE\" : \"INACTIVE_100PCT\", g_stretch_percent);",
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
        "    fprintf(file, \"{\\\"schema\\\":2,\\\"generatedAtUnix\\\":%ld,\\\"profileLoaded\\\":%s,\\\"abiProbe\\\":{\\\"total\\\":%d,\\\"executable\\\":%d,\\\"plausible\\\":%d,\\\"ambiguousRoles\\\":%d,\\\"callGateOpen\\\":%s},\\\"fps\\\":{\\\"backend\\\":\\\"%s\\\",\\\"requested\\\":%d,\\\"screenMaximum\\\":%d,\\\"hookInstalled\\\":%s},\\\"renderer\\\":{\\\"requested\\\":\\\"%s\\\",\\\"effective\\\":\\\"Metal\\\"},\\\"boost\\\":{\\\"enabled\\\":%s,\\\"status\\\":\\\"%s\\\"},\\\"font\\\":{\\\"requested\\\":\\\"%s\\\",\\\"sizeStep\\\":%d,\\\"status\\\":\\\"%s\\\"},\\\"shader\\\":{\\\"requested\\\":\\\"%s\\\",\\\"wetFloor\\\":%s,\\\"status\\\":\\\"%s\\\"},\\\"stretch\\\":{\\\"requestedPercent\\\":%d,\\\"status\\\":\\\"%s\\\"}}\\n\",",
        "            time(NULL), g_profile_loaded ? \"true\" : \"false\",",
        "            g_abi_probe_total, g_abi_probe_executable, g_abi_probe_plausible, g_abi_probe_duplicate_roles, g_abi_probe_call_gate_open ? \"true\" : \"false\",",
        "            kFpsRuntimeBackend, g_target_fps,",
        "            ios_maximum_frames_per_second(), g_display_link_policy_installed ? \"true\" : \"false\",",
        "            g_renderer_mode, g_game_mode_enabled ? \"true\" : \"false\",",
        "            g_game_mode_enabled ? \"active_display_policy\" : \"inactive\",",
        "            g_font_family, g_font_size_step, g_profile_loaded ? \"active_asset_vfs\" : \"inactive\",",
        "            g_shader_package, g_shader_wet_floor ? \"true\" : \"false\", (g_profile_loaded && g_abi_probe_call_gate_open && (g_shader_wet_floor || strcmp(g_shader_package, \"none\") != 0)) ? \"active_luau_workflow\" : (g_profile_loaded && (g_shader_wet_floor || strcmp(g_shader_package, \"none\") != 0)) ? \"profile_only_abi_blocked\" : \"inactive\",",
        "            g_stretch_percent, (g_profile_loaded && g_stretch_percent != 100) ? \"active_view_projection\" : \"inactive_100pct\");",
        "    fflush(file);",
        "    fclose(file);",
        "    runtime_note(\"[RECEIPT] file=MasterstrapRuntimeReceipt.json status=WRITTEN\");",
        "}",
        "",
        "static void log_bundled_sky_assets(void) {",
        "    id NSBundle = objc_getClass(\"NSBundle\");",
        "    if (!NSBundle) { runtime_note(\"[SHADER][SKY_ASSET] status=BLOCKED reason=NSBundle_missing\"); return; }",
        "    id bundle = ((id (*)(id, SEL))objc_msgSend)(NSBundle, sel_registerName(\"mainBundle\"));",
        "    id path_value = bundle ? ((id (*)(id, SEL))objc_msgSend)(bundle, sel_registerName(\"bundlePath\")) : NULL;",
        "    const char* root = path_value ? ((const char* (*)(id, SEL))objc_msgSend)(path_value, sel_registerName(\"UTF8String\")) : NULL;",
        "    if (!root) { runtime_note(\"[SHADER][SKY_ASSET] status=BLOCKED reason=bundle_path_missing\"); return; }",
        "    const char* faces[] = {\"bk\", \"dn\", \"ft\", \"lf\", \"rt\", \"up\"};",
        "    int present = 0; unsigned long long total = 0;",
        "    for (int i = 0; i < 6; ++i) {",
        "        char path[1024]; snprintf(path, sizeof(path), \"%s/Frameworks/RobloxLib.framework/ios/textures/sky/sky512_%s.tex\", root, faces[i]);",
        "        void* file = fopen(path, \"rb\"); long size = -1; if (file) { fseek(file, 0, 2); size = ftell(file); fclose(file); }",
        "        if (size >= 0) { present++; total += (unsigned long long)size; }",
        "    }",
        "    runtime_note(\"[SHADER][SKY_ASSET] status=%s faces=%d/6 bytes=%llu source=bundled-ios-textures\", present == 6 ? \"PRESENT\" : \"MISSING\", present, total);",
        "}",
        "",
        "static const char* bridge_utf8(id value) {",
        "    if (!value) return \"\";",
        "    id NSString = objc_getClass(\"NSString\");",
        "    if (!NSString) return \"\";",
        "    if (((bool (*)(id, SEL, id))objc_msgSend)(value, sel_registerName(\"isKindOfClass:\"), NSString)) {",
        "        return ((const char* (*)(id, SEL))objc_msgSend)(value, sel_registerName(\"UTF8String\"));",
        "    }",
        "    id NSJSONSerialization = objc_getClass(\"NSJSONSerialization\");",
        "    if (NSJSONSerialization && ((bool (*)(id, SEL, id))objc_msgSend)(NSJSONSerialization, sel_registerName(\"isValidJSONObject:\"), value)) {",
        "        id data = ((id (*)(id, SEL, id, unsigned long, id*))objc_msgSend)(",
        "            NSJSONSerialization, sel_registerName(\"dataWithJSONObject:options:error:\"), value, 1, NULL);",
        "        if (data) {",
        "            id str = ((id (*)(id, SEL, id, unsigned long))objc_msgSend)(",
        "                ((id (*)(id, SEL))objc_msgSend)(NSString, sel_registerName(\"alloc\")),",
        "                sel_registerName(\"initWithData:encoding:\"), data, 4);",
        "            if (str) {",
        "                return ((const char* (*)(id, SEL))objc_msgSend)(str, sel_registerName(\"UTF8String\"));",
        "            }",
        "        }",
        "    }",
        "    return \"\";",
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
        "    ((void (*)(id, SEL, id, id))objc_msgSend)(request, sel_registerName(\"setValue:forHTTPHeaderField:\"),",
        "                                               bridge_nsstr(\"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148\"), bridge_nsstr(\"User-Agent\"));",
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
        "    id consume_body = ((id (*)(id, SEL, id, id))objc_msgSend)(",
        "        NSDictionary, sel_registerName(\"dictionaryWithObject:forKey:\"),",
        "        bridge_nsstr(ticket), bridge_nsstr(\"ticket\"));",
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
        "    return payload;",
        "}",
        "",
        "static bool g_bridge_shader_active = false;",
        "static int g_bridge_shader_selector = 2002;",
        "static bool bridge_replace_json_scalar(char* json, size_t capacity, const char* key, const char* replacement) {",
        "    if (!json || !key || !replacement) return false;",
        "    char needle[160]; snprintf(needle, sizeof(needle), \"\\\"%s\\\"\", key);",
        "    char* hit = strstr(json, needle); if (!hit) return false;",
        "    char* colon = hit + strlen(needle); while (*colon && *colon != ':') colon++;",
        "    if (*colon != ':') return false; colon++;",
        "    while (*colon == ' ' || *colon == '\\t' || *colon == '\\r' || *colon == '\\n') colon++;",
        "    char* end = colon; bool quoted = *end == '\"';",
        "    if (quoted) { end++; while (*end && *end != '\"') end++; if (*end != '\"') return false; end++; }",
        "    else { while (*end && *end != ',' && *end != '}' && *end != ']' && *end != ' ' && *end != '\\t' && *end != '\\r' && *end != '\\n') end++; }",
        "    size_t old_len = (size_t)(end - colon); size_t new_len = strlen(replacement); size_t total = strlen(json);",
        "    if (total - old_len + new_len + 1 > capacity) return false;",
        "    memmove(colon + new_len, end, total - (size_t)(end - json) + 1); memcpy(colon, replacement, new_len); return true;",
        "}",
        "static int bridge_shader_selector_from_json(const char* json) {",
        "    int selector = 2002; if (!json) return selector;",
        "    if (strstr(json, \"daytime\")) selector = 2001;",
        "    else if (strstr(json, \"rtx_ultra\") || strstr(json, \"RTX High\")) selector = 2002;",
        "    else if (strstr(json, \"sunset\")) selector = 2003;",
        "    else if (strstr(json, \"night\")) selector = 2004;",
        "    else if (strstr(json, \"cloudy\")) selector = 2005;",
        "    else if (strstr(json, \"desert\")) selector = 2006;",
        "    else if (strstr(json, \"arctic\")) selector = 2007;",
        "    else if (strstr(json, \"blood_moon\")) selector = 2008;",
        "    else if (strstr(json, \"cyberpunk\")) selector = 2009;",
        "    else if (strstr(json, \"cherry_blossom\")) selector = 2010;",
        "    else if (strstr(json, \"ghibli_anime\")) selector = 2011;",
        "    else if (strstr(json, \"synthwave\")) selector = 2012;",
        "    else if (strstr(json, \"noir\")) selector = 2013;",
        "    if (strstr(json, \"\\\"wetFloor\\\": true\") || strstr(json, \"\\\"wetFloor\\\":true\")) selector += 1000;",
        "    return selector;",
        "}",
        "static char* sanitize_shader_client_json(const char* content, bool* changed) {",
        "    if (changed) *changed = false; if (!content) return NULL;",
        "    size_t length = strlen(content); char* copy = malloc(length + 512); if (!copy) return NULL; memcpy(copy, content, length + 1);",
        "    const char* disable[] = {\"FFlagDisablePostFx\", \"DFFlagDebugGraphicsDisablePostFx\", \"FFlagDebugGraphicsDisablePostFx\", \"FFlagDebugGraphicsDisablePostFxUI\", NULL};",
        "    for (int i = 0; disable[i]; ++i) if (bridge_replace_json_scalar(copy, length + 512, disable[i], \"false\")) { if (changed) *changed = true; }",
        "    if (bridge_replace_json_scalar(copy, length + 512, \"DFIntRenderPostFxBasePixelCount\", \"2000000\")) { if (changed) *changed = true; }",
        "    char selector[24]; snprintf(selector, sizeof(selector), \"%d\", g_bridge_shader_selector);",
        "    bool selector_replaced = bridge_replace_json_scalar(copy, length + 512, \"FIntRenderShadowHugeRadius\", selector);",
        "    if (selector_replaced) {",
        "        if (changed) *changed = true;",
        "    } else {",
        "        char* close = strrchr(copy, '}'); size_t used = strlen(copy);",
        "        if (close) {",
        "            char* before = close; while (before > copy && (before[-1] == ' ' || before[-1] == '\\t' || before[-1] == '\\r' || before[-1] == '\\n')) before--;",
        "            bool has_fields = before > copy && before[-1] != '{';",
        "            const char* prefix = has_fields ? \",\\n  \\\"FIntRenderShadowHugeRadius\\\": \" : \"\\n  \\\"FIntRenderShadowHugeRadius\\\": \";",
        "            size_t prefix_len = strlen(prefix); size_t selector_len = strlen(selector);",
        "            size_t tail = used - (size_t)(close - copy);",
        "            if (used + prefix_len + selector_len + 1 <= length + 512) {",
        "                memmove(close + prefix_len + selector_len, close, tail + 1);",
        "                memcpy(close, prefix, prefix_len); memcpy(close + prefix_len, selector, selector_len);",
        "                if (changed) *changed = true;",
        "            }",
        "        }",
        "    }",
        "    return copy;",
        "}",
        "static bool bridge_write_document(const char* file_name, const char* content) {",
        "    if (!file_name || !content || strlen(content) == 0) return false;",
        "    if (!kShaderEnabled && strcmp(file_name, \"MasterstrapShaderProfile.json\") == 0) content = \"{\\\"schema\\\":1,\\\"enabled\\\":false,\\\"package\\\":\\\"none\\\",\\\"wetFloor\\\":false}\";",
        "    const char* home = getenv(\"HOME\");",
        "    if (!home) return false;",
        "    char path[768];",
        "    snprintf(path, sizeof(path), \"%s/Documents/%s\", home, file_name);",
        "    void* file = fopen(path, \"w\");",
        "    if (!file) return false;",
        "    if (strcmp(file_name, \"ClientAppSettings.json\") == 0) {",
        "        bool shader_changed = false; char* copy = g_bridge_shader_active ? sanitize_shader_client_json(content, &shader_changed) : strdup(content);",
        "        if (copy && !kShaderEnabled && bridge_replace_json_scalar(copy, strlen(copy) + 1, \"FIntRenderShadowHugeRadius\", \"0\")) shader_changed = true;",
        "        if (copy) {",
        "            char* hit = strstr(copy, \"\\\"FFlagPerformanceControlDisplayRefreshRate\\\": \\\"true\\\"\");",
        "            if (hit) memcpy(hit + 44, \"\\\"false\\\"\", 7);",
        "            char* hit2 = strstr(copy, \"\\\"FFlagPerformanceControlDisplayRefreshRate\\\": true\");",
        "            if (hit2) memcpy(hit2 + 44, \"false\", 5);",
        "            bridge_log(false, \"Shader JSON selector=%d changed=%d before Roblox read\", g_bridge_shader_selector, shader_changed ? 1 : 0);",
        "            fputs(copy, file);",
        "            char cs_dir[768];",
        "            snprintf(cs_dir, sizeof(cs_dir), \"%s/Documents/ClientSettings\", home);",
        "            mkdir(cs_dir, 0755);",
        "            char cs_path[768];",
        "            snprintf(cs_path, sizeof(cs_path), \"%s/Documents/ClientSettings/ClientAppSettings.json\", home);",
        "            void* cs_f = fopen(cs_path, \"w\");",
        "            if (cs_f) { fputs(copy, cs_f); fflush(cs_f); fclose(cs_f); bridge_log(false, \"Shader JSON mirrored to ClientSettings selector=%d\", g_bridge_shader_selector); }",
        "            free(copy);",
        "        } else {",
        "            fputs(content, file);",
        "        }",
        "    } else {",
        "        fputs(content, file);",
        "    }",
        "    fflush(file);",
        "    fclose(file);",
        "    return true;",
        "}",
        "",
        "static char g_last_bridge_request[96] = {0};",
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
        "        } else if (strcmp(name, \"payload\") == 0 || strcmp(name, \"cfg\") == 0) {",
        "            payload = value;",
        "        } else if (strcmp(name, \"ticket\") == 0) {",
        "            ticket_value = value;",
        "        } else if (strcmp(name, \"request\") == 0 || strcmp(name, \"req\") == 0) {",
        "            request_value = value;",
        "        }",
        "    }",
        "",
        "    if (protocol_version == 0 && payload) {",
        "        protocol_version = 1;",
        "    }",
        "    if (!request_value && payload) {",
        "        request_value = bridge_nsstr(\"req-local-auto\");",
        "    }",
        "",
        "    const char* request_text = bridge_utf8(request_value);",
        "    if ((protocol_version != 1 && protocol_version != 2) || !bridge_request_is_valid(request_text)) {",
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
        "                sel_registerName(\"initWithBase64EncodedString:options:\"), payload, 1)",
        "            : NULL;",
        "        if (!data && NSData && payload) {",
        "            id unescaped = ((id (*)(id, SEL))objc_msgSend)(payload, sel_registerName(\"stringByRemovingPercentEncoding\"));",
        "            if (unescaped) {",
        "                data = ((id (*)(id, SEL, id, unsigned long))objc_msgSend)(",
        "                    ((id (*)(id, SEL))objc_msgSend)(NSData, sel_registerName(\"alloc\")),",
        "                    sel_registerName(\"initWithBase64EncodedString:options:\"), unescaped, 1);",
        "            }",
        "        }",
        "        unsigned long decoded_length = data",
        "            ? ((unsigned long (*)(id, SEL))objc_msgSend)(data, sel_registerName(\"length\")) : 0;",
        "        if (decoded_length == 0 || decoded_length > 524288) {",
        "            bridge_log(true, \"Rejected URL: request=%s decoded payload size=%lu out of range\", request, decoded_length);",
        "            return false;",
        "        }",
        "        bridge_log(false, \"Payload decoded: request=%s decodedBytes=%lu\", request, decoded_length);",
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
        "    const char* shader_json = bridge_utf8(shader_value);",
        "    g_bridge_shader_active = kShaderEnabled && shader_json && (strstr(shader_json, \"\\\"enabled\\\": true\") || strstr(shader_json, \"\\\"wetFloor\\\": true\"));",
        "    if (shader_json) g_bridge_shader_selector = bridge_shader_selector_from_json(shader_json);",
        "    purge_local_launch_configuration(\"bridge_replace_previous\", false);",
        "    g_cached_dynamic_count = 0;",
        "    bool wrote_client = bridge_write_document(",
        "        \"ClientAppSettings.json\", bridge_utf8(client_value));",
        "    bool wrote_turbo = bridge_write_document(",
        "        \"MasterstrapTurboProfile.json\", bridge_utf8(turbo_value));",
        "    bool wrote_global = bridge_write_document(",
        "        \"GlobalBasicSettings_13.xml\", bridge_utf8(global_value));",
        "    bridge_write_document(",
        "        \"GlobalBasicSettings.xml\", bridge_utf8(global_value));",
        "    bool wrote_shader = bridge_write_document(",
        "        \"MasterstrapShaderProfile.json\", bridge_utf8(shader_value));",
        "    bool wrote_font = bridge_write_document(",
        "        \"MasterstrapFontProfile.json\", bridge_utf8(font_value));",
        "    bool imported = wrote_client || wrote_turbo || wrote_global || wrote_shader || wrote_font;",
        "    char authorization[256];",
        "    snprintf(authorization, sizeof(authorization), \"{\\\"schema\\\":1,\\\"request\\\":\\\"%s\\\",\\\"issuedAtUnix\\\":%ld,\\\"singleUse\\\":false}\", request, time(NULL));",
        "    bool wrote_authorization = imported && bridge_write_document(\"MasterstrapLaunchAuthorization.json\", authorization);",
        "    imported = imported && wrote_authorization;",
        "    bridge_log(false, \"Document writes: request=%s client=%d turbo=%d global=%d shader=%d font=%d\",",
        "               request, wrote_client, wrote_turbo, wrote_global, wrote_shader, wrote_font);",
        "    bridge_log(false, \"Persistent authorization: request=%s written=%d singleUse=0\", request, wrote_authorization);",
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
        "        g_launch_authorized = true;",
        "        snprintf(g_last_bridge_request, sizeof(g_last_bridge_request), \"%s\", request);",
        "        bridge_log(false, \"Import complete: request=%s protocol=%d\", request, protocol_version);",
        "        load_and_apply_runtime_profiles(NULL);",
        "        install_display_link_policy();",
        "        apply_ios_process_policies();",
        "        log_runtime_consumer_contract();",
        "        uintptr_t current_base = g_engine_base != 0 ? g_engine_base : get_robloxlib_base();",
        "        if (current_base != 0) {",
        "            int warm_dynamic = load_dynamic_config_files(current_base, NULL);",
        "            int warm_applied = apply_cached_dynamic_flags(current_base);",
        "            float factor = (kStretchEnabled && g_stretch_percent > 0 && g_stretch_percent != 100) ? (100.0f / (float)g_stretch_percent) : 1.0f;",
        "            if (kStretchEnabled) *(volatile float*)(current_base + 0x69ca130) = factor;",
        "            apply_native_fps_registry(current_base);",
        "            if (g_shader_wet_floor || strcmp(g_shader_package, \"none\") != 0) {",
        "                apply_shader_lighting_flags(current_base);",
        "            }",
        "            ensure_fps_slot_guard_thread(current_base);",
        "            purge_local_launch_configuration(\"warm_bridge_applied\", true);",
        "            bridge_log(false, \"Warm-session one-time apply: request=%s parsed=%d cachedApplied=%d stretchFactor=%.4f consumed=1\", request, warm_dynamic, warm_applied, factor);",
        "        }",
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
        "        if (url) bridge_log(false, \"Cold-launch URL callback received\");",
        "        import_masterstrap_bridge_url(url);",
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
        "    id UIApplication = objc_getClass(\"UIApplication\");",
        "    if (!UIApplication) return false;",
        "    id application = ((id (*)(id, SEL))objc_msgSend)(",
        "        UIApplication, sel_registerName(\"sharedApplication\"));",
        "    id delegate = application",
        "        ? ((id (*)(id, SEL))objc_msgSend)(application, sel_registerName(\"delegate\"))",
        "        : NULL;",
        "    return delegate ? install_bridge_hooks_for_class(object_getClass(delegate)) : false;",
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
        "static void* fps_slot_guard_thread(void* arg) {",
        "    uintptr_t base = (uintptr_t)arg;",
        "    if (!base) return NULL;",
        "    while (1) {",
        "        usleep(100000);",
        "        ms_log_fps_drawable_sample();",
        "        maintain_native_fps_registry(base);",
        "",
        "        // Maintain stretch horizontal factor slot",
        "        float factor = 1.0f;",
        "        if (kStretchEnabled && g_stretch_percent > 0 && g_stretch_percent != 100) {",
        "            factor = 100.0f / (float)g_stretch_percent;",
        "        }",
        "        if (kStretchEnabled) *(volatile float*)(base + 0x69ca130) = factor;",
        "    }",
        "    return NULL;",
        "}",
        "",
        "static void ensure_fps_slot_guard_thread(uintptr_t base) {",
        "    if (!base || g_guard_thread_started) return;",
        "    g_guard_thread_started = true;",
        "    void* th = NULL;",
        "    pthread_create(&th, NULL, fps_slot_guard_thread, (void*)base);",
        "    if (th) pthread_detach(th);",
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
        "    g_engine_base = base;",
        "    if (kStretchEnabled && base != 0 && !g_launch_authorized) { *(volatile float*)(base + 0x69ca130) = 1.0f; }",
        "",
        "    char log_path[512] = {0};",
        "    const char* home = getenv(\"HOME\");",
        "    if (home) {",
        "        snprintf(log_path, sizeof(log_path), \"%s/Documents/masterstrap_ios.log\", home);",
        "        char cas_path[768];",
        "        snprintf(cas_path, sizeof(cas_path), \"%s/Documents/ClientAppSettings.json\", home);",
        "        void* cas_f = fopen(cas_path, \"r+\");",
        "        if (cas_f) {",
        "            fseek(cas_f, 0, 2);",
        "            long cas_len = ftell(cas_f);",
        "            fseek(cas_f, 0, 0);",
        "            if (cas_len > 0 && cas_len < 131072) {",
        "                char* cas_buf = malloc(cas_len + 1);",
        "                if (cas_buf) {",
        "                    size_t r_bytes = fread(cas_buf, 1, cas_len, cas_f);",
        "                    cas_buf[r_bytes] = '\\0';",
        "                    char* hit = strstr(cas_buf, \"\\\"FFlagPerformanceControlDisplayRefreshRate\\\": \\\"true\\\"\");",
        "                    bool mod = false;",
        "                    if (hit) { memcpy(hit + 44, \"\\\"false\\\"\", 7); mod = true; }",
        "                    char* hit2 = strstr(cas_buf, \"\\\"FFlagPerformanceControlDisplayRefreshRate\\\": true\");",
        "                    if (hit2) { memcpy(hit2 + 44, \"false\", 5); mod = true; }",
        "                    if (mod) {",
        "                        fseek(cas_f, 0, 0);",
        "                        fputs(cas_buf, cas_f);",
        "                        fflush(cas_f);",
        "                        runtime_note(\"[FPS] Sanitized stale ClientAppSettings.json FFlagPerformanceControlDisplayRefreshRate=false\");",
        "                    }",
        "                    free(cas_buf);",
        "                }",
        "            }",
        "            fclose(cas_f);",
        "        }",
        "        char gbs_path[768];",
        "        snprintf(gbs_path, sizeof(gbs_path), \"%s/Documents/GlobalBasicSettings_13.xml\", home);",
        "        void* gbs_check = fopen(gbs_path, \"r\");",
        "        if (!gbs_check) {",
        "            void* gbs_w = fopen(gbs_path, \"w\");",
        "            if (gbs_w) {",
        "                const char* gbs_xml = \"<?xml version=\\\"1.0\\\" encoding=\\\"UTF-8\\\"?>\\n<roblox version=\\\"4\\\">\\n  <Item class=\\\"UserGameSettings\\\" referent=\\\"RBX0\\\">\\n    <Properties>\\n      <bool name=\\\"ReducedMotion\\\">false</bool>\\n      <float name=\\\"HapticStrength\\\">1.000000</float>\\n      <int name=\\\"FramerateCap\\\">9999</int>\\n      <bool name=\\\"PlayerNamesEnabled\\\">true</bool>\\n    </Properties>\\n  </Item>\\n</roblox>\\n\";",
        "                fputs(gbs_xml, gbs_w);",
        "                fclose(gbs_w);",
        "                runtime_note(\"[FPS] Initialized default Documents/GlobalBasicSettings_13.xml (FramerateCap=9999)\");",
        "            }",
        "        } else {",
        "            fclose(gbs_check);",
        "        }",
        "        char gbs_path2[768];",
        "        snprintf(gbs_path2, sizeof(gbs_path2), \"%s/Documents/GlobalBasicSettings.xml\", home);",
        "        void* gbs_check2 = fopen(gbs_path2, \"r\");",
        "        if (!gbs_check2) {",
        "            void* gbs_w2 = fopen(gbs_path2, \"w\");",
        "            if (gbs_w2) {",
        "                const char* gbs_xml = \"<?xml version=\\\"1.0\\\" encoding=\\\"UTF-8\\\"?>\\n<roblox version=\\\"4\\\">\\n  <Item class=\\\"UserGameSettings\\\" referent=\\\"RBX0\\\">\\n    <Properties>\\n      <bool name=\\\"ReducedMotion\\\">false</bool>\\n      <float name=\\\"HapticStrength\\\">1.000000</float>\\n      <int name=\\\"FramerateCap\\\">9999</int>\\n      <bool name=\\\"PlayerNamesEnabled\\\">true</bool>\\n    </Properties>\\n  </Item>\\n</roblox>\\n\";",
        "                fputs(gbs_xml, gbs_w2);",
        "                fclose(gbs_w2);",
        "            }",
        "        } else {",
        "            fclose(gbs_check2);",
        "        }",
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
        "            if (log_file) fprintf(log_file, \"[%ld] [RUNTIME] log rotated after %ld bytes\\n\", time(NULL), previous_size);",
        "        }",
        "    }",
        "    run_engine_abi_probe(base, log_file);",
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
        "                uint32_t prologue = 0;",
        "                bool executable = macho_rva_is_executable(base, kVerifiedOffsets[i].rva);",
        "                if (executable) {",
        "                    prologue = *(volatile uint32_t*)func_addr;",
        "                }",
        "                fprintf(log_file, \"[OFFSET_MAP] %-32s @ 0x%lx (RVA 0x%lx) executable=%d opcode=0x%08X aliases=%d\\n\",",
        "                        kVerifiedOffsets[i].name,",
        "                        (unsigned long)func_addr,",
        "                        (unsigned long)kVerifiedOffsets[i].rva,",
        "                        executable, prologue, offset_role_count(kVerifiedOffsets[i].rva));",
        "            }",
        "            fprintf(log_file, \"\\n\");",
        "        }",
        "",
        "        fprintf(log_file, \"--- [Active FastFlags Status] ---\\n\");",
        "    }",
        "",
        "    syslog(LOG_NOTICE, \"[Masterstrap-iOS] RobloxLib base found at 0x%lx. Applying dynamic and verified flags...\", (unsigned long)base);",
        "",
        "    int dynamic_count = 0;",
        "    int success_count = 0;",
        "    bool launch_authorized = false;",
        "    if (base != 0) {",
        "        launch_authorized = true;",
        "        g_launch_authorized = true;",
        "        // Load profiles before dynamic flags so shader quality policy can reject conflicts.",
        "        load_and_apply_runtime_profiles(log_file);",
        "        // Parse once, apply once, then retain only an in-memory cache.",
        "        dynamic_count = load_dynamic_config_files(base, log_file);",
        "",
        "            // Load every launcher-owned runtime profile and report capability gates",
        "            install_display_link_policy();",
        "            apply_ios_process_policies();",
        "            log_runtime_consumer_contract();",
        "",
        "            // 2. Apply verified baseline flags only inside an authorized launch.",
        "            for (size_t i = 0; i < kVerifiedCount; i++) {",
        "                if (native_fps_owns_flag(kVerifiedFlags[i].name)) continue;",
        "                bool ok = SetFastFlag(base, &kVerifiedFlags[i]);",
        "                if (ok) success_count++;",
        "                if (log_file) {",
        "                    if (kVerifiedFlags[i].kind == 0) {",
        "                        fprintf(log_file, \"[%s] (SetFastFlag) %s @ 0x%lx (RVA 0x%lx) = %u\\n\",",
        "                                ok ? \"VERIFIED\" : \"FAIL\",",
        "                                kVerifiedFlags[i].name, (unsigned long)(base + kVerifiedFlags[i].rva),",
        "                                (unsigned long)kVerifiedFlags[i].rva, (uint32_t)kVerifiedFlags[i].value);",
        "                    } else {",
        "                        fprintf(log_file, \"[%s] (SetFastFlag) %s @ 0x%lx (RVA 0x%lx) = %d\\n\",",
        "                                ok ? \"VERIFIED\" : \"FAIL\",",
        "                                kVerifiedFlags[i].name, (unsigned long)(base + kVerifiedFlags[i].rva),",
        "                                (unsigned long)kVerifiedFlags[i].rva, (int32_t)kVerifiedFlags[i].value);",
        "                    }",
        "                }",
        "            }",
        "",
        "            // 2b. Native FPS Ceiling Uncap to Dynamic / Infinity (Android Parity)",
        "            if (base != 0) {",
        "                int target = get_effective_target_fps();",
        "                int native_fps_flags = apply_native_fps_registry(base);",
        "",
        "                ensure_fps_slot_guard_thread(base);",
        "",
        "                if (log_file) {",
        "                    fprintf(log_file, \"[FPS_CEILING] targetFps=%d nativeFlags=%d status=%s\\n\",",
        "                            target, native_fps_flags, g_fps_critical_missing ? \"PARTIAL_MISSING_LIMITERS\" : \"VERIFIED\");",
        "                }",
        "                runtime_note(\"[FPS_CEILING] targetFps=%d nativeFlags=%d status=%s\", target, native_fps_flags, g_fps_critical_missing ? \"PARTIAL_MISSING_LIMITERS\" : \"VERIFIED\");",
        "            }",
        "            // 2c. Native Stretch Resolution Factor (Android Global v73 Parity)",
        "            if (base != 0) {",
        "                float factor = 1.0f;",
        "                if (kStretchEnabled && g_stretch_percent > 0 && g_stretch_percent != 100) {",
        "                    factor = 100.0f / (float)g_stretch_percent;",
        "                }",
        "                if (kStretchEnabled) *(volatile float*)(base + 0x69ca130) = factor;",
        "                if (log_file) {",
        "                    fprintf(log_file, \"[STRETCH_PROJECTION] Stored horizontalFactor=%.4f (stretchPercent=%d%%) to slot 0x%lx\\n\",",
        "                            factor, g_stretch_percent, (unsigned long)(base + 0x69ca130));",
        "                }",
        "                runtime_note(\"[STRETCH_PROJECTION] Stored horizontalFactor=%.4f (stretchPercent=%d%%) to slot 0x%lx status=VERIFIED\",",
        "                             factor, g_stretch_percent, (unsigned long)(base + 0x69ca130));",
        "            }",
        "            // 2d. Native Metal Shader & Wet Floor Materials (Future Lighting & Reflectance)",
        "            if (base != 0 && (g_shader_wet_floor || strcmp(g_shader_package, \"none\") != 0)) {",
        "                apply_shader_lighting_flags(base);",
        "                if (log_file) {",
        "                    fprintf(log_file, \"[SHADER_APPLY] status=%s package=%s wetFloor=%d\\n\",",
        "                            g_abi_probe_call_gate_open ? \"ACTIVE_LUAU\" : \"PROFILE_ONLY_ABI_BLOCKED\",",
        "                            g_shader_package, g_shader_wet_floor);",
        "                }",
        "                runtime_note(\"[SHADER_APPLY] status=%s package=%s wetFloor=%d\",",
        "                             g_abi_probe_call_gate_open ? \"ACTIVE_LUAU\" : \"PROFILE_ONLY_ABI_BLOCKED\", g_shader_package, g_shader_wet_floor);",
        "            }",
        "            purge_local_launch_configuration(\"cold_launch_applied\", true);",
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
        "        fprintf(log_file, \"[FEATURE_01_FPS] status=%s backend=%s targetFps=%d renderedFps=UNMEASURED gate=ProfileLoaded(%d)&&Auth(%d) blockedReason=%s\\n\",",
        "                g_fps_critical_missing ? \"BLOCKED_OFFSET_AUDIT\" : g_target_fps != 60 ? \"REQUESTED\" : \"DEFAULT_60\", kFpsRuntimeBackend, g_target_fps,",
        "                g_profile_loaded, launch_authorized,",
        "                !launch_authorized ? \"NO_AUTH\" : !g_profile_loaded ? \"NO_TURBO_PROFILE\" : g_fps_critical_missing ? \"CRITICAL_LIMITER_RVA_MISSING\" : \"none\");",
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
        "        fprintf(log_file, \"[FEATURE_06_SHADER] status=%s package=%s wetFloor=%d backend=%s gate=Auth(%d)&&Profile(%d) blockedReason=%s\\n\",",
        "                launch_authorized && g_profile_loaded && g_abi_probe_call_gate_open && (g_shader_wet_floor || strcmp(g_shader_package, \"none\") != 0) ? \"ACTIVE\" :",
        "                launch_authorized && g_profile_loaded && (g_shader_wet_floor || strcmp(g_shader_package, \"none\") != 0) ? \"PROFILE_ONLY_ABI_BLOCKED\" : \"BLOCKED\",",
        "                g_shader_package, g_shader_wet_floor, kShaderRuntimeBackend, launch_authorized, g_profile_loaded,",
        "                !launch_authorized ? \"NO_AUTH\" : !g_profile_loaded ? \"NO_PROFILE\" : !g_abi_probe_call_gate_open ? \"ABI_SIGNATURE_PENDING\" : (!g_shader_wet_floor && strcmp(g_shader_package, \"none\") == 0) ? \"SHADER_DISABLED\" : \"none\");",
        "        if (g_shader_wet_floor) {",
        "            fprintf(log_file, \"[WETFLOOR_V24] status=REQUESTED runtimeVerified=0 reason=lua-readback-required strategy=bounded_nearby_once maxParts=1024 radiusStuds=384\\n\");",
        "            runtime_note(\"[WETFLOOR_V24] status=REQUESTED runtimeVerified=0 reason=lua-readback-required strategy=bounded_nearby_once maxParts=1024 radiusStuds=384\");",
        "        }",
        "        // FEATURE_07: Font Override",
        "        fprintf(log_file, \"[FEATURE_07_FONT] status=%s family=%s sizeStep=%d backend=%s gate=Auth(%d)&&Profile(%d) blockedReason=%s\\n\",",
        "                launch_authorized && g_profile_loaded ? \"ACTIVE\" : \"BLOCKED\",",
        "                g_font_family, g_font_size_step, kFontRuntimeBackend,",
        "                launch_authorized, g_profile_loaded,",
        "                !launch_authorized ? \"NO_AUTH\" : !g_profile_loaded ? \"NO_PROFILE\" : \"none\");",
        "        // FEATURE_08: Stretch / Aspect-Ratio",
        "        fprintf(log_file, \"[FEATURE_08_STRETCH] status=%s stretchPct=%d backend=%s gate=Auth(%d)&&Profile(%d) blockedReason=%s\\n\",",
        "                launch_authorized && g_profile_loaded && g_stretch_percent != 100 ? \"ACTIVE\" :",
        "                launch_authorized && g_profile_loaded ? \"INACTIVE_100PCT\" : \"BLOCKED\",",
        "                g_stretch_percent, kStretchRuntimeBackend, launch_authorized, g_profile_loaded,",
        "                !launch_authorized ? \"NO_AUTH\" : !g_profile_loaded ? \"NO_PROFILE\" : g_stretch_percent == 100 ? \"STRETCH_100PCT_NOOP\" : \"none\");",
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
        "            fprintf(log_file, \"[FEATURE_10_STEALTH] status=%s offsetsMapped=%d/7 runtimeHooks=0 \"",
        "                    \"abortLogger=%d reportBytecode=%d validateHeader=%d \"",
        "                    \"scriptTimeout=%d validateCaller=%d validateAndSetupCaps=%d telemetry=%d \"",
        "                    \"gate=EngineBase(%d) blockedReason=%s\\n\",",
        "                    base != 0 && stl_total==7 ? \"STATIC_MAP_ONLY\" : base != 0 && stl_total>0 ? \"STATIC_MAP_PARTIAL\" : \"BLOCKED\",",
        "                    stl_total, stl_abort, stl_rbc, stl_vh, stl_st, stl_vc, stl_vc2, stl_tel,",
        "                    (base != 0),",
        "                    base == 0 ? \"ENGINE_BASE_NOT_FOUND\" : \"runtime-hooks-not-installed\");",
        "        }",
        "        fprintf(log_file, \"=== [END FEATURE DIAGNOSTIC MATRIX] ===\\n\\n\");",
        "    }",
        "",
        "    if (log_file) {",
        "        fprintf(log_file, \"\\nFinal Result: authorized=%d, %d baseline verified + %d dynamic flags active (Status: %s)\\n\",",
        "                launch_authorized, success_count, dynamic_count, !launch_authorized ? \"DIRECT_LAUNCH_NO_CONFIG\" : (success_count == (int)kVerifiedCount) ? \"SUCCESS\" : \"PARTIAL\");",
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
        "        if (base == 0) { base = get_robloxlib_base(); if (!engine_identity_matches(base)) base = 0; g_engine_base = base; }",
        "        if (base != 0) {",
        "            for (size_t i = 0; i < kVerifiedCount; i++) {",
        "                if (native_fps_owns_flag(kVerifiedFlags[i].name)) continue;",
        "                SetFastFlag(base, &kVerifiedFlags[i]);",
        "            }",
        "            apply_cached_dynamic_flags(base);",
        "            apply_shader_lighting_flags(base);",
        "        }",
        "        if (loop == 19 || loop == 39 || loop == 59) {",
        "            runtime_note(\"Persistence checkpoint: elapsedSeconds=%d baseReady=%d\", (loop + 1) / 2, base != 0);",
        "        }",
        "    }",
        "    runtime_note(\"Persistence initial 30s completed. Starting continuous watchdog: pid=%d\", getpid());",
        "    int watchdog_ticks = 0;",
        "    while (1) {",
        "        usleep(1000000); // 1s heartbeat",
        "        watchdog_ticks++;",
        "        if (base == 0) { base = get_robloxlib_base(); if (!engine_identity_matches(base)) base = 0; g_engine_base = base; }",
        "        if (base != 0) {",
        "            // Watchdog lock: if Roblox engine resets TaskSchedulerTargetFps to 60 upon server join, reassert instantly",
        "            for (size_t i = 0; i < kVerifiedCount; i++) {",
        "                if (native_fps_owns_flag(kVerifiedFlags[i].name)) continue;",
        "                if (kVerifiedFlags[i].kind == 1) {",
        "                    int32_t cur = *(volatile int32_t*)(base + kVerifiedFlags[i].rva);",
        "                    if (cur != (int32_t)kVerifiedFlags[i].value) {",
        "                        SetFastFlag(base, &kVerifiedFlags[i]);",
        "                    }",
        "                } else if (kVerifiedFlags[i].kind == 0) {",
        "                    uint8_t cur = *(volatile uint8_t*)(base + kVerifiedFlags[i].rva);",
        "                    if (cur != (uint8_t)kVerifiedFlags[i].value) {",
        "                        SetFastFlag(base, &kVerifiedFlags[i]);",
        "                    }",
        "                }",
        "            }",
        "            apply_cached_dynamic_flags(base);",
        "            apply_shader_lighting_flags(base);",
        "        }",
        "        if (watchdog_ticks % 300 == 0) {",
        "            runtime_note(\"Watchdog checkpoint: elapsedSeconds=%d baseReady=%d\", 30 + watchdog_ticks, base != 0);",
        "        }",
        "    }",
        "    runtime_note(\"Watchdog loop terminated: pid=%d\", getpid());",
        "",
        "    return NULL;",
        "}",
        "typedef void (*SetDelegateImp)(id, SEL, id);",
        "static SetDelegateImp g_original_set_delegate = NULL;",
        "",
        "static void masterstrap_set_delegate(id self, SEL cmd, id delegate) {",
        "    if (delegate) {",
        "        install_bridge_hooks_for_class(object_getClass(delegate));",
        "    }",
        "    if (g_original_set_delegate) {",
        "        g_original_set_delegate(self, cmd, delegate);",
        "    }",
        "}",
        "",
        "__attribute__((constructor)) static void masterstrap_ios_init(void) {",
        "    Class ui_app = objc_getClass(\"UIApplication\");",
        "    if (ui_app) {",
        "        SEL set_del_sel = sel_registerName(\"setDelegate:\");",
        "        Method m = class_getInstanceMethod(ui_app, set_del_sel);",
        "        if (m) {",
        "            IMP cur = method_getImplementation(m);",
        "            if (cur != (IMP)masterstrap_set_delegate) {",
        "                g_original_set_delegate = (SetDelegateImp)cur;",
        "                method_setImplementation(m, (IMP)masterstrap_set_delegate);",
        "            }",
        "        }",
        "    }",
        "    Class roma = objc_getClass(\"AppDelegateROMA\");",
        "    if (roma) install_bridge_hooks_for_class(roma);",
        "    Class app_del = objc_getClass(\"AppDelegate\");",
        "    if (app_del) install_bridge_hooks_for_class(app_del);",
        "    install_display_link_policy();",
        "    install_metal_layer_stretch_policy();",
        "    void* thread = NULL;",
        "    pthread_create(&thread, NULL, worker_thread, NULL);",
        "    if (thread) pthread_detach(thread);",
        "}"
    ])

    return "\n".join(lines)


def compile_dylib(c_source: str, out_dylib: Path, build_type: str = "release") -> Path:
    if build_type not in ("debug", "release"):
        raise ValueError("build_type must be debug or release")
    compile_flags = ["-O0", "-g"] if build_type == "debug" else ["-O3", "-DNDEBUG"]
    work_dir = out_dylib.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    c_file = work_dir / "masterstrap_ios.c"
    obj_file = work_dir / "masterstrap_ios.o"
    c_file.write_text(c_source, encoding="utf-8")

    if sys.platform == "darwin":
        isysroot = []
        try:
            sdk_proc = subprocess.run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"], capture_output=True, text=True)
            if sdk_proc.returncode == 0 and sdk_proc.stdout.strip():
                isysroot = ["-isysroot", sdk_proc.stdout.strip()]
        except Exception:
            pass

        compile_cmd = [
            "clang",
            "-target", "arm64-apple-ios14.0",
            "-dynamiclib",
            *compile_flags,
            "-undefined", "dynamic_lookup",
            "-install_name", "@rpath/libmasterstrap.dylib",
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
        *compile_flags,
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


def lz4_compress_block(src: bytes) -> bytes:
    if not src:
        return b'\x00'
    out = bytearray()
    anchor = 0
    pos = 0
    table = {}
    while pos + 12 < len(src):
        seq = src[pos:pos + 4]
        key = (int.from_bytes(seq, 'little') * 2654435761) & 0xFFFFFFFF
        ref = table.get(key)
        table[key] = pos
        if ref is None or pos - ref >= 65536 or src[ref:ref + 4] != seq:
            pos += 1
            continue
        literal_len = pos - anchor
        match_len = 4
        while pos + match_len < len(src) and src[ref + match_len] == src[pos + match_len]:
            match_len += 1
        out.append((min(literal_len, 15) << 4) | min(match_len - 4, 15))
        if literal_len >= 15:
            remainder = literal_len - 15
            while remainder >= 255:
                out.append(255)
                remainder -= 255
            out.append(remainder)
        out.extend(src[anchor:pos])
        out.extend(struct.pack('<H', pos - ref))
        if match_len - 4 >= 15:
            remainder = match_len - 19
            while remainder >= 255:
                out.append(255)
                remainder -= 255
            out.append(remainder)
        pos += match_len
        anchor = pos
    literal_len = len(src) - anchor
    out.append(min(literal_len, 15) << 4)
    if literal_len >= 15:
        remainder = literal_len - 15
        while remainder >= 255:
            out.append(255)
            remainder -= 255
        out.append(remainder)
    out.extend(src[anchor:])
    return bytes(out)


def lz4_decompress_block(src: bytes, uncompressed_size: int) -> bytes:
    src_pos = 0
    dst = bytearray()
    while src_pos < len(src) and len(dst) < uncompressed_size:
        token = src[src_pos]
        src_pos += 1
        literal_len = token >> 4
        if literal_len == 15:
            while src_pos < len(src):
                value = src[src_pos]
                src_pos += 1
                literal_len += value
                if value != 255:
                    break
        dst.extend(src[src_pos:src_pos + literal_len])
        src_pos += literal_len
        if len(dst) >= uncompressed_size or src_pos >= len(src):
            break
        offset = struct.unpack('<H', src[src_pos:src_pos + 2])[0]
        src_pos += 2
        if offset == 0 or offset > len(dst):
            raise ValueError('Invalid RBXM LZ4 match offset')
        match_len = (token & 0x0F) + 4
        if match_len == 19:
            while src_pos < len(src):
                value = src[src_pos]
                src_pos += 1
                match_len += value
                if value != 255:
                    break
        for _ in range(match_len):
            dst.append(dst[-offset])
    if len(dst) != uncompressed_size:
        raise ValueError(f'RBXM LZ4 size mismatch: expected {uncompressed_size}, got {len(dst)}')
    return bytes(dst)


def inject_rbxm_script(rbxm_data: bytes, append_luau: str) -> tuple[bytes, bool]:
    pos = 32
    chunks = []
    modified = False
    while pos + 16 <= len(rbxm_data):
        name = rbxm_data[pos:pos + 4]
        compressed_len, uncompressed_len, reserved = struct.unpack('<III', rbxm_data[pos + 4:pos + 16])
        payload_len = compressed_len or uncompressed_len
        end = pos + 16 + payload_len
        if end > len(rbxm_data):
            raise ValueError('Truncated RBXM chunk')
        payload = rbxm_data[pos + 16:end]
        if name == b'PROP' and not modified:
            raw = lz4_decompress_block(payload, uncompressed_len) if compressed_len else payload
            marker = b'\x06\x00\x00\x00Source\x01'
            marker_pos = raw.find(marker)
            if marker_pos >= 0:
                length_pos = marker_pos + len(marker)
                old_len = struct.unpack('<I', raw[length_pos:length_pos + 4])[0]
                source_pos = length_pos + 4
                if old_len > 50 and source_pos + old_len <= len(raw):
                    source = raw[source_pos:source_pos + old_len]
                    marker_text = b'__MASTERSTRAP_SHADER_V2'
                    if marker_text not in source:
                        # Character animation scripts end in a long-running wait loop;
                        # append-only payloads never execute. Start payload before stock code.
                        new_source = append_luau.encode('utf-8') + b'\n\n' + source
                        new_raw = raw[:length_pos] + struct.pack('<I', len(new_source)) + new_source + raw[source_pos + old_len:]
                        new_payload = lz4_compress_block(new_raw)
                        if lz4_decompress_block(new_payload, len(new_raw)) != new_raw:
                            raise ValueError('RBXM LZ4 round-trip verification failed')
                        chunks.append(b'PROP' + struct.pack('<III', len(new_payload), len(new_raw), reserved) + new_payload)
                        modified = True
                        pos = end
                        continue
        chunks.append(rbxm_data[pos:end])
        pos = end
        if name == b'END\x00':
            break
    if not modified:
        return rbxm_data, False
    chunks.append(rbxm_data[pos:])
    return rbxm_data[:32] + b''.join(chunks), True


def verify_packaged_visual_assets(input_ipa: Path, output_ipa: Path, sky_assets: dict[str, bytes]) -> None:
    """Prove packaged sky bytes changed and match launcher-owned resources."""
    prefix = "Payload/Roblox.app/Frameworks/RobloxLib.framework/ios/textures/sky/"
    with zipfile.ZipFile(input_ipa, "r") as source, zipfile.ZipFile(output_ipa, "r") as packaged:
        for name, expected in sky_assets.items():
            member = prefix + name
            original = source.read(member)
            actual = packaged.read(member)
            if actual != expected:
                raise RuntimeError(f"Packaged sky asset mismatch: {member}")
            if actual == original:
                raise RuntimeError(f"Packaged sky asset remained stock: {member}")
            print(
                f"[ASSET_SKY_VERIFY] {name} stock={hashlib.sha256(original).hexdigest()[:12]} "
                f"custom={hashlib.sha256(actual).hexdigest()[:12]} status=VERIFIED"
            )

LUAU_SHADER_PAYLOAD = """
-- Runs only after Roblox enters a loaded place; avoids homepage/bootstrap mutation.
if _G.__MASTERSTRAP_SHADER_V2 ~= "applied" and _G.__MASTERSTRAP_SHADER_V2 ~= "running" then
    _G.__MASTERSTRAP_SHADER_V2 = "running"
    warn("MASTERSTRAP_SHADER_STARTED")
    task.defer(function()

    local applied = false
    local ok, errorMessage = pcall(function()
        if not game:IsLoaded() then game.Loaded:Wait() end
        -- Bootstrap/landing DataModel can run this payload before a place exists.
        -- Wait only a bounded window so a later join can apply the profile without
        -- leaving a permanent worker in the client.
        local placeReady = game.PlaceId > 0
        for _ = 1, 40 do
            if placeReady then break end
            task.wait(0.25)
            placeReady = game.PlaceId > 0
        end
        if not placeReady then
            _G.__MASTERSTRAP_SHADER_V2 = nil
            warn("MASTERSTRAP_SHADER_WAITING_FOR_PLACE")
            return
        end

        local lighting = game:GetService("Lighting")
        if not lighting then return end
        local selector = 2002
        local reportedSelector
        pcall(function() reportedSelector = game:GetFastInt("RenderShadowHugeRadius") end)
        if type(reportedSelector) == "number" then selector = reportedSelector end
        if selector < 2001 or selector > 3013 then selector = 2002 end
        local wetFloorEnabled = selector >= 3000
        if wetFloorEnabled then selector = selector - 1000 end
        local shaderEnabled = selector >= 2001 and selector <= 2013

        -- Android parity: use stable Roblox asset IDs, not Windows PurpleVoid
        -- or one shared local cubemap for every package.
        local androidSkies = {}
        if shaderEnabled then
            local skyIds = androidSkies[selector]
            local fields = {"SkyboxBk", "SkyboxDn", "SkyboxFt", "SkyboxLf", "SkyboxRt", "SkyboxUp"}
            local skyReapplying = false
            local managedSkies = setmetatable({}, {__mode = "k"})
            local applySky
            applySky = function(sky)
                if not sky or not sky:IsA("Sky") then return false end
                local readback = true
                for i, field in ipairs(fields) do
                    local value = "rbxassetid://" .. skyIds[i]
                    sky[field] = value
                    if sky[field] ~= value then readback = false end
                end
                for property, assetId in pairs({
                    SunTextureId = profile.sunTexture,
                    MoonTextureId = profile.moonTexture,
                }) do
                    if assetId then
                        local value = "rbxassetid://" .. assetId
                        sky[property] = value
                        if sky[property] ~= value then readback = false end
                    end
                end
                if profile.sunAngularSize then sky.SunAngularSize = profile.sunAngularSize end
                if profile.moonAngularSize then sky.MoonAngularSize = profile.moonAngularSize end
                if not managedSkies[sky] then
                    managedSkies[sky] = true
                    for _, field in ipairs(fields) do
                        sky:GetPropertyChangedSignal(field):Connect(function()
                            if not skyReapplying then task.defer(function() applySky(sky) end) end
                        end)
                    end
                end
                return readback
            end
            local function reapplySkybox()
                if skyReapplying then return end
                skyReapplying = true
                local found = false
                local readback = true
                for _, child in ipairs(lighting:GetChildren()) do
                    if child:IsA("Sky") then
                        found = true
                        if not applySky(child) then readback = false end
                    end
                end
                if not found then
                    local sky = Instance.new("Sky")
                    sky.Name = "Masterstrap_Skybox"
                    sky.Parent = lighting
                    readback = applySky(sky)
                end
                warn("MASTERSTRAP_SKYBOX_APPLIED_LOCAL:" .. tostring(selector) .. ":readback=" .. tostring(readback))
                skyReapplying = false
            end
            reapplySkybox()
            local function effect(className, name, props)
                local item = lighting:FindFirstChild(name)
                if not item then item = Instance.new(className) item.Name = name item.Parent = lighting end
                for property, value in pairs(props) do pcall(function() item[property] = value end) end
                if className ~= "Atmosphere" then item.Enabled = true end
            end
            local lightingReapplying = false
            local function applyLightingEffects()
                if lightingReapplying then return end
                lightingReapplying = true
                pcall(function() lighting.Technology = Enum.Technology.Future end)
                pcall(function()
                    for property, value in pairs(profile.lighting or {}) do lighting[property] = value end
                end)
                effect("BloomEffect", "Masterstrap_Bloom", profile.bloom or {})
                effect("ColorCorrectionEffect", "Masterstrap_ColorCorrection", profile.correction or {})
                effect("SunRaysEffect", "Masterstrap_SunRays", profile.rays or {})
                effect("Atmosphere", "Masterstrap_Atmosphere", profile.atmosphere or {})
                if profile.water then pcall(function()
                    workspace.Terrain.WaterColor = Color3.fromRGB(45, 155, 215)
                    workspace.Terrain.WaterReflectance = 1.0
                    workspace.Terrain.WaterTransparency = 0.15
                    workspace.Terrain.WaterWaveSize = 0.12
                    workspace.Terrain.WaterWaveSpeed = 12
                end) end
                lightingReapplying = false
            end
            applyLightingEffects()
            lighting.ChildAdded:Connect(function(child)
                if child:IsA("Sky") then task.defer(reapplySkybox)
                elseif child:IsA("PostEffect") or child:IsA("Atmosphere") then task.defer(applyLightingEffects) end
            end)
            lighting.ChildRemoved:Connect(function(child)
                if child:IsA("Sky") then task.defer(reapplySkybox)
                elseif child:IsA("PostEffect") or child:IsA("Atmosphere") then task.defer(applyLightingEffects) end
            end)
        end

        local wetFloorBusy = false
        local wetFloorQueued = false
        local wetFloorRetries = 0
        local function wetFloor(reason)
            if wetFloorBusy then return end
            wetFloorBusy = true
            local okWetFloor, wetFloorError = pcall(function()
                local player = game:GetService("Players").LocalPlayer
                local root = player and player.Character and player.Character:FindFirstChild("HumanoidRootPart")
                if not root then
                    if wetFloorRetries < 5 then
                        wetFloorRetries = wetFloorRetries + 1
                        task.delay(1.0, function() wetFloor("root_retry" .. tostring(wetFloorRetries)) end)
                    end
                    return
                end
                local scanned = 0
                local changed = 0
                local verified = 0
                for _, part in ipairs(workspace:GetPartBoundsInRadius(root.Position, 384)) do
                    if scanned >= 1024 then break end
                    if part:IsA("BasePart") and part.CanCollide and not part:IsDescendantOf(player.Character) then
                        scanned = scanned + 1
                        local up = part.CFrame.UpVector
                        local material = part.Material
                        local eligibleMaterial = material == Enum.Material.Plastic
                            or material == Enum.Material.Concrete
                            or material == Enum.Material.Slate
                            or material == Enum.Material.WoodPlanks
                        if eligibleMaterial and (math.abs(up.Y) > 0.7 or (part.Size.Y <= part.Size.X and part.Size.Y <= part.Size.Z)) then
                            pcall(function()
                                local originalReflectance = part:GetAttribute("MS_OriginalReflectance")
                                if originalReflectance == nil then
                                    originalReflectance = part.Reflectance
                                    part:SetAttribute("MS_OriginalReflectance", originalReflectance)
                                end
                                local reflectance = math.clamp(originalReflectance + 0.25, 0.15, 0.5)
                                part.Reflectance = reflectance
                                if part.Material ~= Enum.Material.Glass then part.Material = Enum.Material.SmoothPlastic end
                                changed = changed + 1
                                if math.abs(part.Reflectance - reflectance) < 0.00001 then verified = verified + 1 end
                            end)
                        end
                    end
                end
                warn("MASTERSTRAP_WETFLOOR_READBACK:" .. tostring(reason or "initial") .. ":changed=" .. tostring(changed) .. ":verified=" .. tostring(verified))
                if changed == 0 and wetFloorRetries < 5 then
                    wetFloorRetries = wetFloorRetries + 1
                    task.delay(1.0, function() wetFloor("retry" .. tostring(wetFloorRetries)) end)
                else
                    wetFloorRetries = 0
                end
            end)
            wetFloorBusy = false
            if not okWetFloor then warn("MASTERSTRAP_WETFLOOR_FAILED:" .. tostring(wetFloorError)) end
        end
        if wetFloorEnabled then
            wetFloor("initial")
            pcall(function()
                workspace.Terrain.WaterReflectance = 1
                workspace.Terrain.WaterTransparency = 0.15
                workspace.Terrain.WaterWaveSize = 0.12
                workspace.Terrain.WaterWaveSpeed = 12
            end)
            local players = game:GetService("Players")
            local player = players.LocalPlayer
            if player then
                player.CharacterAdded:Connect(function() task.defer(function() wetFloor("character") end) end)
            end
            workspace.DescendantAdded:Connect(function(item)
                if item:IsA("BasePart") and not wetFloorQueued then
                    wetFloorQueued = true
                    task.delay(1.0, function()
                        wetFloorQueued = false
                        wetFloor("streamed")
                    end)
                end
            end)
        end
        lighting:SetAttribute("MasterstrapShaderRuntime", "applied")
        lighting:SetAttribute("MasterstrapShaderSelector", selector)
        lighting:SetAttribute("MasterstrapWetFloorRuntime", wetFloorEnabled and "requested" or "disabled")
        lighting:SetAttribute("MasterstrapShaderPackage", tostring(selector))
        warn("MASTERSTRAP_SHADER_APPLIED_LOCAL:" .. tostring(selector) .. ":wetfloor=" .. tostring(wetFloorEnabled))
        applied = true
    end)
    if not ok then
        _G.__MASTERSTRAP_SHADER_V2 = nil
        warn("MASTERSTRAP_SHADER_FAILED:" .. tostring(errorMessage))
    elseif applied then
        _G.__MASTERSTRAP_SHADER_V2 = "applied"
    else
        _G.__MASTERSTRAP_SHADER_V2 = nil
        warn("MASTERSTRAP_SHADER_DEFERRED")
    end
    end)
end
"""


ANDROID_SHADER_SELECTOR_BY_PACKAGE = {
    "daytime": 2001,
    "rtx_ultra": 2002,
    "sunset": 2003,
    "night": 2004,
    "cloudy": 2005,
    "desert": 2006,
    "arctic": 2007,
    "blood_moon": 2008,
    "cyberpunk": 2009,
    "cherry_blossom": 2010,
    "ghibli_anime": 2011,
    "synthwave": 2012,
    "noir": 2013,
}


def _lua_shader_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "nil"
    if isinstance(value, dict) and set(value) == {"rgb"}:
        r, g, b = value["rgb"]
        return f"Color3.fromRGB({int(r)}, {int(g)}, {int(b)})"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    return str(value)


def _render_android_shader_profiles() -> str:
    manifest_path = ROOT / "resources" / "shader" / "android_shader_manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("Android shader manifest must contain packages")

    lines = ["        local androidProfiles = {"]
    for package, selector in ANDROID_SHADER_SELECTOR_BY_PACKAGE.items():
        profile = packages.get(package)
        if not isinstance(profile, dict):
            raise ValueError(f"Missing Android shader package: {package}")
        sky = profile.get("sky")
        if not isinstance(sky, list) or len(sky) < 6:
            raise ValueError(f"Shader package {package} has fewer than six sky faces")
        lines.append(f"            [{selector}] = {{")
        lines.append("                sky = {" + ", ".join(_lua_shader_value(item) for item in sky[:6]) + "},")
        for key, index in (("sunTexture", 6), ("moonTexture", 7),
                           ("sunAngularSize", 8), ("moonAngularSize", 9)):
            if len(sky) > index and sky[index] is not None:
                lines.append(f"                {key} = {_lua_shader_value(sky[index])},")
        for section in ("lighting", "atmosphere", "correction", "bloom", "rays"):
            values = profile.get(section, {})
            lines.append(f"                {section} = {{")
            for key, value in values.items():
                lines.append(f"                    {key} = {_lua_shader_value(value)},")
            lines.append("                },")
        lines.append(f"                water = {_lua_shader_value(bool(profile.get('water', False)))},")
        lines.append("            },")
    lines.append("        }")
    return "\n".join(lines)


def _inject_android_shader_profiles(payload: str) -> str:
    marker = "        local androidSkies = {}\n        if shaderEnabled then\n            local skyIds = androidSkies[selector]"
    replacement = (
        _render_android_shader_profiles()
        + "\n        local profile = androidProfiles[selector] or {}\n"
        + "        if shaderEnabled then\n            local skyIds = profile.sky"
    )
    if marker not in payload:
        raise ValueError("Shader payload marker not found")
    return payload.replace(marker, replacement, 1)


LUAU_SHADER_PAYLOAD = _inject_android_shader_profiles(LUAU_SHADER_PAYLOAD)

def write_full_inspection_report(out_dir: Path, flags: dict, verified_entries: list, miss_offsets: list, ipa_name: str, out_ipa_name: str, embed_flags: bool = False):
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

    runtime_log = out_dir / "masterstrap_ios.log"
    runtime_log.write_text(report_text, encoding="utf-8")


def build_pipeline(input_ipa: Path, settings_json: Path, hpp_path: Path, out_dir: Path, embed_flags: bool = False, build_type: str = "release", workflow_profile_path: Path | None = None, shader_enabled: bool = True, stretch_enabled: bool = True):
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # IPA builds must use a verified local dump, never a remote version's map.
    dumped_header = None
    expected_uuid = None
    expected_sha256 = None
    if input_ipa and input_ipa.is_file():
        dumped_header = hpp_path if hpp_path and hpp_path.is_file() else run_local_dumper(input_ipa, out_dir)
        expected_uuid, expected_sha256 = verify_ipa_profile(input_ipa, dumped_header)

    # 2. Resolve FFlags table (dumped or remote)
    target_header = dumped_header if dumped_header else hpp_path
    table = fetch_or_load_fflags_map(target_header)

    # Always resolve baseline uncap and graphics flags so verified_entries is populated
    flags = {
        "FFlagDebugDisplayFPS": True,
        "FFlagDebugSkyGray": False,
        "DFIntTaskSchedulerTargetFps": 9999,
        "FFlagTaskSchedulerLimitTargetFpsToScreenRefreshRate": False,
        "FFlagTaskSchedulerLimitTargetFpsTo2402": False,
        "FFlagGameBasicSettingsFramerateCap": False,
        "FFlagAuroraLimit60HzRenderSim": False,
        "FFlagApplicationFrameRate": False,
        "FFlagApplicationFrameRatePolicy": False,
        "FFlagDebugExperimentalVSyncFramePacing": False,
        "FFlagDebugDisableVsync": True,
        "FFlagMetalVsyncPreferMaximumRefreshRate": True,
        "FFlagPerformanceControlExecution": False,
        "FFlagPerformanceControlDynamicFrameTimeTarget": False,
        "FFlagPerformanceControlComputeUseTSFrameTimeTarget": False,
        "FFlagPerformanceControlDisplayRefreshRate": False,
        "FFlagPerformanceControlDisplayRefreshRateInAdaptiveSync": False,
        "FFlagPerformanceControlDisplayRefreshRateMigration": False,
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
    if settings_json and settings_json.is_file():
        custom = json.loads(settings_json.read_text(encoding="utf-8"))
        flags.update(custom)
    if not shader_enabled:
        flags.pop("FIntRenderShadowHugeRadius", None)

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
    workflow_profile = {}
    profile_path = workflow_profile_path or (out_dir / "workflow" / "profile.json")
    if profile_path.is_file():
        try:
            workflow_profile = json.loads(profile_path.read_text(encoding="utf-8"))
            if not isinstance(workflow_profile, dict):
                workflow_profile = {}
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid iOS workflow profile: {profile_path}: {exc}") from exc
    if input_ipa and input_ipa.is_file() and not workflow_profile:
        raise RuntimeError("Exact iOS workflow profile missing; refusing stale Lua/shader ABI")
    profile_uuid = str(workflow_profile.get("uuid") or "").lower()
    if expected_uuid and profile_uuid != expected_uuid.lower():
        raise RuntimeError(
            f"iOS workflow profile UUID mismatch: profile={profile_uuid or 'missing'} ipa={expected_uuid}"
        )
    profile_sha256 = str(workflow_profile.get("sha256") or "").lower()
    if expected_sha256 and profile_sha256 != expected_sha256.lower():
        raise RuntimeError(
            f"iOS workflow profile SHA256 mismatch: profile={profile_sha256 or 'missing'} ipa={expected_sha256}"
        )
    offsets_entries = sorted(
        ((name, rva) for name, rva in offsets_table.items()
         if name not in DIAGNOSTIC_ONLY_OFFSETS),
        key=lambda x: x[0],
    )
    # Feed exact-build audit candidates into diagnostics only. These roles stay
    # read-only evidence; callable Lua ABI remains closed until bridge proof.
    audited_roles = workflow_profile.get("auditedReadOnlyRoles", {})
    if isinstance(audited_roles, dict):
        known_names = {name for name, _ in offsets_entries}
        for name, role in audited_roles.items():
            rva = role.get("rva") if isinstance(role, dict) else None
            if (name not in DIAGNOSTIC_ONLY_OFFSETS
                    and name not in known_names
                    and type(rva) is int and rva >= 0 and rva % 4 == 0):
                offsets_entries.append((name, rva))
                known_names.add(name)
        offsets_entries.sort(key=lambda x: x[0])
    print(f"Loaded Engine Offsets: {len(offsets_entries)} functions")

    if input_ipa and input_ipa.is_file():
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            dylib_path = tpath / "libmasterstrap.dylib"
            c_src = generate_native_hook_source(verified_entries, miss_offsets, len(flags), offsets_entries, all_flags_table=table, expected_uuid=expected_uuid, workflow_profile=workflow_profile, shader_enabled=shader_enabled, stretch_enabled=stretch_enabled)
            compile_dylib(c_src, dylib_path, build_type=build_type)
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
                        "Payload/Roblox.app/MasterstrapShaderProfile.json",
                        "Payload/Roblox.app/ClientSettings/MasterstrapShaderProfile.json",
                        "Payload/Roblox.app/MasterstrapFontProfile.json",
                        "Payload/Roblox.app/ClientSettings/MasterstrapFontProfile.json",
                        "Payload/Roblox.app/GlobalBasicSettings_13.xml",
                        "Payload/Roblox.app/GlobalBasicSettings.xml",
                    }
                    sky_dir = ROOT / "resources" / "sky"
                    sky_names = tuple(f"sky512_{face}.tex" for face in ("bk", "dn", "ft", "lf", "rt", "up"))
                    sky_assets = ({name: (sky_dir / name).read_bytes() for name in sky_names}
                                  if shader_enabled else {})
                    for sky_name, sky_payload in sky_assets.items():
                        if not sky_payload.startswith(b"\xABKTX 11\xBB\r\n\x1A\n"):
                            raise RuntimeError(f"Skybox asset is not KTX1: {sky_name}")
                    replaced_sky_assets = set()
                    injected_shader_assets = 0

                    for item in zin.infolist():
                        name = item.filename
                        # Skip embedded settings and dylib from input IPA so they are cleanly re-embedded afresh without duplicate entries
                        if name in embedded_settings_entries or name == "Payload/Roblox.app/Frameworks/libmasterstrap.dylib":
                            continue

                        # ── Delta / Third-Party Sanitization Purge Filter ──
                        # 1. Skip Delta script package, injector UI, app extensions/plugins, old signatures, and third-party injector dylibs
                        if any(k in name.lower() for k in ["/gloop/", "dlt.zip", "_codesignature", "embedded.mobileprovision", "/plugins/", ".appex", "libgloop.dylib", "delta.dylib"]):
                            print(f"[PURGE] Removed third-party file -> {name}")
                            continue

                        data = zin.read(name)

                        sky_name = Path(name).name
                        if (shader_enabled and "RobloxLib.framework/ios/textures/sky/" in name
                                and sky_name in sky_assets):
                            data = sky_assets[sky_name]
                            replaced_sky_assets.add(sky_name)
                            print(f"[ASSET_SKY] Replaced stock Roblox sky texture -> {name} ({len(data)} bytes)")

                        if name == "Payload/Roblox.app/Roblox":
                            data = remove_load_dylib(data, ["libgloop", "delta.dylib"])
                            data = insert_load_dylib(data, "@executable_path/Frameworks/libmasterstrap.dylib")
                        elif name == "Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib":
                            data = remove_load_dylib(data, ["libgloop", "delta.dylib"])
                            data = bytearray(data)
                            # 1. Preserve the engine getter and remove its 240 FPS select.
                            clamp_offset = 0x46256c4
                            clamp_original = b'\x09\xb1\x89\x1a'  # csel w9, w8, w9, lt
                            clamp_patch = b'\xe9\x03\x08\x2a'     # mov w9, w8
                            if data[clamp_offset:clamp_offset+4] not in (clamp_original, clamp_patch):
                                raise RuntimeError('FPS getter contract changed; refusing unverified RobloxLib patch')
                            data[clamp_offset:clamp_offset+4] = clamp_patch
                            print(f"[FPS_CEILING] Removed TaskScheduler 240 FPS clamp @ 0x{clamp_offset:X}")

                            # 2. Bypass 240 FPS clamp site 2 in TaskScheduler dt calculator (csel w11, w11, w12, lt -> nop)
                            clamp2_offset = 0x9165fc
                            if len(data) > clamp2_offset + 4 and data[clamp2_offset:clamp2_offset+4] in (b'\x6b\xb1\x8c\x1a', b'\x1f\x20\x03\xd5'):
                                data[clamp2_offset:clamp2_offset+4] = b'\x1f\x20\x03\xd5'
                                print(f"[FPS_CEILING] Bypassed RobloxLib second 240 FPS clamp site 2 @ 0x{clamp2_offset:X} (csel -> nop)")

                            # 3. Bypass 240 FPS clamp site 3 in TaskScheduler target frametime calculator (csel w8, w23, w8, cc -> mov w8, w23)
                            clamp3_offset = 0x4628128
                            if len(data) > clamp3_offset + 4 and data[clamp3_offset:clamp3_offset+4] in (b'\xe8\x32\x88\x1a', b'\xe8\x03\x17\x2a'):
                                data[clamp3_offset:clamp3_offset+4] = b'\xe8\x03\x17\x2a'
                                print(f"[FPS_CEILING] Bypassed RobloxLib third 240 FPS clamp site 3 @ 0x{clamp3_offset:X} (csel -> mov w8, w23)")

                            # 4. Match requested engine cadence when Roblox enters its fallback scheduler path.
                            fallback_dt_offset = 0x4A60E28
                            fallback_original = b'\x89\x88\x88\x3c'  # ~1/60 second
                            fallback_patch = b'\x17\xb7\xd1\x38'     # 0.0001 second
                            if data[fallback_dt_offset:fallback_dt_offset+4] not in (fallback_original, fallback_patch):
                                raise RuntimeError('FPS fallback-delta contract changed; refusing unverified RobloxLib patch')
                            data[fallback_dt_offset:fallback_dt_offset+4] = fallback_patch
                            print(f"[FPS_CEILING] Patched RobloxLib fallback delta @ 0x{fallback_dt_offset:X} (0.0166s -> 0.0001s)")

                            # 5. Keep the target in w22 rather than selecting min(target, screenMax).
                            dt_clamp_offset = 0x3F64AE4
                            dt_clamp_patch = b'\x1f\x20\x03\xd5'  # nop
                            if data[dt_clamp_offset:dt_clamp_offset+4] not in (
                                b'\xb6\x32\x96\x1a', dt_clamp_patch
                            ):
                                raise RuntimeError('FPS DisplayTiming contract changed; refusing unverified RobloxLib patch')
                            data[dt_clamp_offset:dt_clamp_offset+4] = dt_clamp_patch
                            print(f"[FPS_CEILING] Removed DisplayTiming screen-Hz clamp @ 0x{dt_clamp_offset:X} (preserve targetFps in w22)")

                            # 6. Stretch Resolution (Architecture v73 Parity with 2D GUI M33 Guard):
                            # Hook View-Projection matrix write at 0xB75610 (inside 0xb75500) -> Code Cave at 0x3F80
                            # Checks raw projection M[3][3] at offset 0x7c of camera object (x20):
                            # 0.0f = 3D perspective world camera (stretch Row 0),
                            # != 0.0f (1.0f) = 2D orthographic GUI/HUD/homepage/joystick (untouched/pristine).
                            stretch_cave_offset = 0x3F80
                            stretch_hook_offset = 0xB75610
                            stretch_cave_bytes = struct.pack(
                                '<15I',
                                0xF0034E28,  # 0x3F80: adrp x8, #0x69ca000
                                0xBD413101,  # 0x3F84: ldr s1, [x8, #0x130]
                                0x1E202028,  # 0x3F88: fcmp s1, #0.0
                                0x54000140,  # 0x3F8C: b.eq #0x3fb4
                                0x1E2E1002,  # 0x3F90: fmov s2, #1.00000000
                                0x1E222020,  # 0x3F94: fcmp s1, s2
                                0x540000E0,  # 0x3F98: b.eq #0x3fb4
                                0xBD407E82,  # 0x3F9C: ldr s2, [x20, #0x7c]
                                0x1E202048,  # 0x3FA0: fcmp s2, #0.0
                                0x54000081,  # 0x3FA4: b.ne #0x3fb4
                                0x3DC02280,  # 0x3FA8: ldr q0, [x20, #0x80]
                                0x4F819000,  # 0x3FAC: fmul v0.4s, v0.4s, v1.s[0]
                                0x3D800260,  # 0x3FB0: str q0, [x19]
                                0xA94A7BFD,  # 0x3FB4: ldp x29, x30, [sp, #0xa0]
                                0x142DC597,  # 0x3FB8: b #0xb75614
                            )
                            stretch_hook_bytes = bytes.fromhex("5c3ad217")  # b #0x3f80

                            if stretch_enabled and len(data) > stretch_hook_offset + 4 and data[stretch_hook_offset:stretch_hook_offset+4] in (b'\xfd\x7b\x4a\xa9', stretch_hook_bytes):
                                if (data[stretch_cave_offset:stretch_cave_offset + len(stretch_cave_bytes)] == b'\x00' * len(stretch_cave_bytes)
                                        or data[stretch_cave_offset:stretch_cave_offset + 8] == b'\x28\x4e\x03\xf0\x01\x31\x41\xbd'):
                                    data[stretch_cave_offset:stretch_cave_offset + len(stretch_cave_bytes)] = stretch_cave_bytes
                                    data[stretch_hook_offset:stretch_hook_offset+4] = stretch_hook_bytes
                                    print(f"[STRETCH_PROJECTION] Installed ARM64 3D camera View-Projection stretch hook @ 0x{stretch_hook_offset:X} -> cave @ 0x{stretch_cave_offset:X} (Architecture v73 Parity with 2D GUI M33 Guard)")
                                else:
                                    print(f"[STRETCH_PROJECTION] Warning: code cave @ 0x{stretch_cave_offset:X} is not empty!")
                            else:
                                print(f"[STRETCH_PROJECTION] Warning: hook site @ 0x{stretch_hook_offset:X} did not match expected opcode")

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
                        elif shader_enabled and any(name.endswith(target) for target in (
                            "AbilityManagerActorClientInitializeIsLocalScript.rbxm",
                            "humanoidAnimateR6WithFace.rbxm",
                            "humanoidAnimateR15CharacterController.rbxm",
                            "humanoidAnimateR15Moods.rbxm",
                            "humanoidAnimateR15SAuth.rbxm",
                            "humanoidAnimatePlayEmote.rbxm",
                            "humanoidAnimateSAuth.rbxm",
                            "AnimateDependencies.rbxm",
                        )):
                            data, injected = inject_rbxm_script(data, LUAU_SHADER_PAYLOAD)
                            if injected:
                                injected_shader_assets += 1
                                print(f"[LUAU_INJECT] Injected profile-gated shader and wet-floor payload -> {name}")
                        elif (name.endswith((".ttf", ".otf")) and "content/fonts/" in name
                              and not any(x in name.lower() for x in ["emoji", "notosanscjk", "buildericons"])):
                            minecraft_font = ROOT / "resources" / "fonts" / "Minecraft.ttf"
                            if minecraft_font.is_file():
                                data = minecraft_font.read_bytes()
                                print(f"[FONT_STATIC] Replaced font binary {name} with Minecraft.ttf ({len(data)} bytes)")
                        elif name.endswith(".json") and "content/fonts/families/" in name:
                            try:
                                j = json.loads(data.decode("utf-8"))
                                if "faces" in j and isinstance(j["faces"], list):
                                    for face in j["faces"]:
                                        if isinstance(face, dict):
                                            face["assetId"] = "rbxasset://fonts/Minecraft.ttf"
                                data = json.dumps(j, indent=2).encode("utf-8")
                                print(f"[FONT_STATIC] Remapped font family {name} faces -> rbxasset://fonts/Minecraft.ttf")
                            except Exception as e:
                                print(f"Warning: Failed to remap font json {name}: {e}")

                        zout.writestr(item, data)
                        seen_entries.add(name)

                    missing_sky_assets = set(sky_names) - replaced_sky_assets
                    if shader_enabled and missing_sky_assets:
                        raise RuntimeError(f"Roblox IPA is missing stock skybox targets: {sorted(missing_sky_assets)}")
                    if shader_enabled and injected_shader_assets == 0:
                        raise RuntimeError("Roblox IPA contains no injectable RBXM assets; shader/wet-floor payload was not embedded")
                    print(f"[LUAU_INJECT] {'Verified ' + str(injected_shader_assets) + ' payload(s)' if shader_enabled else 'DISABLED for crash isolation'}")
                    print(f"[ASSET_SKY] {'Replaced all ' + str(len(replaced_sky_assets)) + ' stock cubemap faces' if shader_enabled else 'DISABLED; stock sky assets preserved'}")

                    # Embed dylib
                    dylib_entry = "Payload/Roblox.app/Frameworks/libmasterstrap.dylib"
                    zout.writestr(dylib_entry, dylib_bytes)

                    # Embed custom font files into both RobloxLib.framework and Roblox.app content/fonts
                    fonts_dir = ROOT / "resources" / "fonts"
                    if fonts_dir.is_dir():
                        for font_file in fonts_dir.iterdir():
                            if font_file.is_file() and font_file.suffix.lower() in (".ttf", ".otf"):
                                f_bytes = font_file.read_bytes()
                                for prefix in [
                                    "Payload/Roblox.app/Frameworks/RobloxLib.framework/content/fonts/",
                                    "Payload/Roblox.app/content/fonts/"
                                ]:
                                    f_entry = prefix + font_file.name
                                    if f_entry not in seen_entries:
                                        zout.writestr(f_entry, f_bytes)
                                        seen_entries.add(f_entry)
                                        print(f"[FONT_BUNDLE] Injected font {font_file.name} -> {f_entry} ({len(f_bytes)} bytes)")

                    # Embed baseline settings JSON so both standalone launch and bridge start with clean settings
                    json_bytes = json.dumps(flags, indent=4).encode("utf-8")
                    turbo_default = {
                        "schema": 1,
                        "enabled": True,
                        "targetFrameRate": 9999,
                        "displayRefreshContract": True,
                        "keepDisplayActive": True,
                        "stretchScreenPercent": 100,
                        "rendererMode": "Automatic",
                        "display_shader_package": "rtx_ultra" if shader_enabled else "none",
                        "display_shader_wet_floor": bool(shader_enabled)
                    }
                    turbo_bytes = json.dumps(turbo_default, indent=4).encode("utf-8")
                    shader_default = {
                        "schema": 1,
                        "enabled": bool(shader_enabled),
                        "package": "rtx_ultra" if shader_enabled else "none",
                        "applyMode": "once_after_datamodel",
                        "maxPostEffects": 5,
                        "wetFloor": bool(shader_enabled),
                        "wetFloorStrategy": "bounded_nearby_once",
                        "wetFloorMaxParts": 1024,
                        "wetFloorRadiusStuds": 384,
                        "allowWorkspaceMaterialScan": False,
                        "allowPeriodicWorker": False
                    }
                    shader_bytes = json.dumps(shader_default, indent=4).encode("utf-8")
                    font_default = {
                        "schema": 1,
                        "enabled": True,
                        "font": "Minecraft",
                        "sizeStep": 0
                    }
                    font_bytes = json.dumps(font_default, indent=4).encode("utf-8")
                    xml_default = (
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<roblox version="4">\n'
                        '  <Item class="UserGameSettings" referent="RBX0">\n'
                        '    <Properties>\n'
                        '      <bool name="ReducedMotion">false</bool>\n'
                        '      <float name="HapticStrength">1.000000</float>\n'
                        '      <int name="FramerateCap">9999</int>\n'
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
                        elif jp.endswith("MasterstrapShaderProfile.json"):
                            zout.writestr(jp, shader_bytes)
                        elif jp.endswith("MasterstrapFontProfile.json"):
                            zout.writestr(jp, font_bytes)
                        else:
                            zout.writestr(jp, json_bytes)

            if shader_enabled:
                verify_packaged_visual_assets(input_ipa, out_ipa, sky_assets)
            print(f"\nSUCCESS -> {out_ipa} ({out_ipa.stat().st_size / (1024*1024):.1f} MB)")

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
    ap.add_argument("--build-type", choices=("debug", "release"), default="release")
    ap.add_argument("--workflow-profile", default="", help="Exact IPA workflow/profile.json generated by prepare_ios_workflow_profile.py")
    ap.add_argument("--disable-shader", action="store_true", help="Disable shader, skybox and wet-floor payload for crash isolation")
    ap.add_argument("--disable-stretch", action="store_true", help="Disable fixed-RVA stretch writes and camera patch for crash isolation")
    ap.add_argument(
        "--embed-flags",
        action="store_true",
        default=False,
        help="Embed baseline flags and packaged settings JSON (default: False, direct launch is 100%% vanilla)",
    )
    args = ap.parse_args()

    build_pipeline(
        Path(args.input_ipa) if args.input_ipa else None,
        Path(args.settings_json) if args.settings_json else None,
        Path(args.hpp) if args.hpp else None,
        Path(args.out_dir),
        embed_flags=args.embed_flags,
        build_type=args.build_type,
        workflow_profile_path=Path(args.workflow_profile) if args.workflow_profile else None,
        shader_enabled=not args.disable_shader,
        stretch_enabled=not args.disable_stretch,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
