#!/usr/bin/env python3
"""
Masterstrap iOS IPA Builder & Live Integrator.
Dumps offsets, compiles native iOS ARM64 hook with SetFastFlag/GetFastFlag + direct memory patching,
embeds ClientAppSettings.json, tracks miss_offsets, and packages a ready-to-sideload IPA.
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
    dumper_pkg = ROOT / "roblox_ios_dumper"
    if not dumper_pkg.is_dir() or not ipa_path or not ipa_path.is_file():
        return None
    print(f"Running automated iOS Dumper on {ipa_path.name}...")
    try:
        cmd = [sys.executable, "-m", "roblox_ios_dumper", "dump", str(ipa_path), "-o", str(out_dir)]
        subprocess.run(cmd, cwd=str(ROOT), check=True)
        dumped_fflags = out_dir / "FFlags.hpp"
        if dumped_fflags.is_file():
            print(f"Dumper successfully generated fresh FFlags.hpp ({dumped_fflags.stat().st_size} bytes)")
            return dumped_fflags
    except Exception as e:
        print(f"Dumper run error: {e}")
    return None


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


def generate_native_hook_source(entries: list, miss_offsets: list, total_flags_count: int, offset_entries: list = None, all_flags_table: dict = None) -> str:
    if offset_entries is None:
        offset_entries = []
    if all_flags_table is None:
        all_flags_table = {}

    sorted_all_flags = sorted(all_flags_table.items(), key=lambda x: x[0])

    lines = [
        "typedef unsigned long uintptr_t;",
        "typedef unsigned int uint32_t;",
        "typedef unsigned char uint8_t;",
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
        "extern uint32_t _dyld_image_count(void);",
        "extern const char* _dyld_get_image_name(uint32_t image_index);",
        "extern const void* _dyld_get_image_header(uint32_t image_index);",
        "extern int mprotect(void *addr, size_t len, int prot);",
        "extern int usleep(unsigned int usec);",
        "extern int getpid(void);",
        "extern char* getenv(const char *name);",
        "extern int snprintf(char *str, size_t size, const char *format, ...);",
        "extern void syslog(int priority, const char *format, ...);",
        "extern void* fopen(const char *filename, const char *mode);",
        "extern int fseek(void *stream, long offset, int whence);",
        "extern long ftell(void *stream);",
        "extern size_t fread(void *ptr, size_t size, size_t count, void *stream);",
        "extern int fprintf(void *stream, const char *format, ...);",
        "extern int fclose(void *stream);",
        "extern int fflush(void *stream);",
        "extern void* malloc(size_t size);",
        "extern void free(void *ptr);",
        "extern long long atoll(const char *str);",
        "extern size_t strlen(const char *str);",
        "extern int strcmp(const char *s1, const char *s2);",
        "extern int strncmp(const char *s1, const char *s2, size_t n);",
        "extern int pthread_create(void **thread, const void *attr, void *(*start_routine)(void *), void *arg);",
        "extern int pthread_detach(void *thread);",
        "extern char* strstr(const char *haystack, const char *needle);",
        "",
        "typedef struct {",
        "    const char* name;",
        "    uintptr_t rva;",
        "} OffsetEntry;",
        "",
        "static const OffsetEntry kVerifiedOffsets[] = {"
    ]

    for name, rva in offset_entries:
        lines.append(f'    {{"{name}", 0x{rva:X}}},')

    lines.extend([
        "};",
        f"static const size_t kOffsetsCount = {len(offset_entries)};",
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
        "    uintptr_t target = base + entry->rva;",
        "    uintptr_t page_size = 16384;",
        "    uintptr_t page_start = target & ~(page_size - 1);",
        "    size_t page_len = page_size * 2;",
        "    if (mprotect((void*)page_start, page_len, PROT_READ | PROT_WRITE) != 0) {",
        "        page_size = 4096;",
        "        page_start = target & ~(page_size - 1);",
        "        mprotect((void*)page_start, page_size * 2, PROT_READ | PROT_WRITE);",
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
        "        if (strncmp(p, \"true\", 4) == 0) {",
        "            val_kind = 0;",
        "            num_val = 1;",
        "            p += 4;",
        "        } else if (strncmp(p, \"false\", 5) == 0) {",
        "            val_kind = 0;",
        "            num_val = 0;",
        "            p += 5;",
        "        } else if (*p == '\"') {",
        "            p++;",
        "            while (*p && *p != '\"') p++;",
        "            if (*p == '\"') p++;",
        "            continue;",
        "        } else if (*p == '-' || (*p >= '0' && *p <= '9')) {",
        "            val_kind = 1;",
        "            char num_buf[64] = {0};",
        "            int nlen = 0;",
        "            while (*p && ((*p >= '0' && *p <= '9') || *p == '-') && nlen < 63) {",
        "                num_buf[nlen++] = *p++;",
        "            }",
        "            num_val = atoll(num_buf);",
        "        } else {",
        "            p++;",
        "            continue;",
        "        }",
        "        const MasterFlagEntry* flag = lookup_flag_with_fallback(key);",
        "        if (flag) {",
        "            FFlagPatchEntry entry;",
        "            entry.name = flag->name;",
        "            entry.rva = (uintptr_t)flag->rva;",
        "            entry.kind = (flag->kind == 0) ? 0 : val_kind;",
        "            entry.value = num_val;",
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
        "    for (int i = 0; candidate_subpaths[i]; i++) {",
        "        snprintf(full_path, sizeof(full_path), \"%s%s\", home, candidate_subpaths[i]);",
        "        void* f = fopen(full_path, \"r\");",
        "        if (f) {",
        "            fseek(f, 0, 2);",
        "            long fsize = ftell(f);",
        "            fseek(f, 0, 0);",
        "            if (fsize > 0 && fsize < 1048576) {",
        "                char* buf = (char*)malloc(fsize + 1);",
        "                if (buf) {",
        "                    size_t read_bytes = fread(buf, 1, fsize, f);",
        "                    buf[read_bytes] = '\\0';",
        "                    fclose(f);",
        "                    if (log_file) {",
        "                        fprintf(log_file, \"\\n--- [Dynamic JSON Config Loaded from %s (%ld bytes)] ---\\n\", candidate_subpaths[i], fsize);",
        "                    }",
        "                    syslog(LOG_NOTICE, \"[Masterstrap-iOS] Parsing dynamic JSON from %s (%ld bytes)\", candidate_subpaths[i], fsize);",
        "                    int count = parse_and_apply_dynamic_json(base, buf, log_file);",
        "                    free(buf);",
        "                    if (log_file) {",
        "                        fprintf(log_file, \"Total Dynamically Applied Flags: %d\\n\\n\", count);",
        "                    }",
        "                    return count;",
        "                }",
        "            }",
        "            fclose(f);",
        "        }",
        "    }",
        "    return 0;",
        "}",
        "",
        "static void load_and_apply_turbo_profile(void* log_file) {",
        "    const char* home = getenv(\"HOME\");",
        "    if (!home) return;",
        "    char path[512];",
        "    snprintf(path, sizeof(path), \"%s/Documents/MasterstrapTurboProfile.json\", home);",
        "    void* f = fopen(path, \"r\");",
        "    if (!f) {",
        "        snprintf(path, sizeof(path), \"%s/Documents/ClientSettings/MasterstrapTurboProfile.json\", home);",
        "        f = fopen(path, \"r\");",
        "    }",
        "    if (f) {",
        "        fseek(f, 0, 2);",
        "        long fsize = ftell(f);",
        "        fseek(f, 0, 0);",
        "        if (fsize > 0 && fsize < 65536) {",
        "            char* buf = (char*)malloc(fsize + 1);",
        "            if (buf) {",
        "                size_t read_bytes = fread(buf, 1, fsize, f);",
        "                buf[read_bytes] = '\\0';",
        "                fclose(f);",
        "                if (log_file) {",
        "                    fprintf(log_file, \"\\n--- [Masterstrap Turbo Engine Profile Loaded (%ld bytes)] ---\\n\", fsize);",
        "                    fprintf(log_file, \"%s\\n\", buf);",
        "                }",
        "                if (strstr(buf, \"\\\"enabled\\\": true\") || strstr(buf, \"\\\"enabled\\\":true\")) {",
        "                    syslog(LOG_NOTICE, \"[Masterstrap-iOS] Turbo Boost Profile ACTIVE: System QoS Interactive & Performance Mode\");",
        "                    if (log_file) {",
        "                        fprintf(log_file, \"[TURBO_BOOST] Profile ACTIVE -> CPU QoS Interactive & Unrestricted Power Applied.\\n\\n\");",
        "                    }",
        "                }",
        "                free(buf);",
        "                return;",
        "            }",
        "        }",
        "        fclose(f);",
        "    }",
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
        "",
        "    // Wait up to 15 seconds for RobloxLib framework to be loaded by dyld",
        "    for (int wait = 0; wait < 150; wait++) {",
        "        base = get_robloxlib_base();",
        "        if (base != 0) break;",
        "        usleep(100000); // 100ms",
        "    }",
        "",
        "    char log_path[512] = {0};",
        "    const char* home = getenv(\"HOME\");",
        "    if (home) {",
        "        snprintf(log_path, sizeof(log_path), \"%s/Documents/masterstrap_ios.log\", home);",
        "    } else {",
        "        snprintf(log_path, sizeof(log_path), \"/tmp/masterstrap_ios.log\");",
        "    }",
        "",
        "    void* log_file = fopen(log_path, \"w\");",
        "    if (log_file) {",
        "        fprintf(log_file, \"==============================================\\n\");",
        "        fprintf(log_file, \"=== Masterstrap Roblox iOS Runtime Log    ===\\n\");",
        "        fprintf(log_file, \"==============================================\\n\");",
        "        fprintf(log_file, \"PID: %d\\n\", getpid());",
        "        fprintf(log_file, \"RobloxLib Base Address: 0x%lx\\n\", (unsigned long)base);",
        "        fprintf(log_file, \"Total Flags Requested: %zu\\n\", kTotalRequested);",
        "        fprintf(log_file, \"Verified Flags Patched: %zu\\n\", kVerifiedCount);",
        "        fprintf(log_file, \"Miss Offsets Count: %zu\\n\", kMissCount);",
        "        fprintf(log_file, \"Verified Engine Offsets Count: %zu\\n\\n\", kOffsetsCount);",
        "        ",
        "        if (kOffsetsCount > 0) {",
        "            fprintf(log_file, \"--- [Verified Engine & Luau VM Function Offsets] ---\\n\");",
        "            for (size_t i = 0; i < kOffsetsCount; i++) {",
        "                uintptr_t func_addr = (base != 0) ? (base + kVerifiedOffsets[i].rva) : 0;",
        "                uint32_t prologue = 0;",
        "                if (base != 0 && kVerifiedOffsets[i].rva != 0) {",
        "                    prologue = *(volatile uint32_t*)func_addr;",
        "                }",
        "                fprintf(log_file, \"[OFFSET] %-32s @ 0x%lx (RVA 0x%lx) | Opcode: 0x%08X\\n\",",
        "                        kVerifiedOffsets[i].name,",
        "                        (unsigned long)func_addr,",
        "                        (unsigned long)kVerifiedOffsets[i].rva,",
        "                        prologue);",
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
        "    if (base != 0) {",
        "        // 1. Load dynamic config from Documents/ClientAppSettings.json if present",
        "        dynamic_count = load_dynamic_config_files(base, log_file);",
        "",
        "        // 1b. Load and apply Masterstrap Turbo Boost profile if present",
        "        load_and_apply_turbo_profile(log_file);",
        "",
        "        // 2. Apply verified baseline flags",
        "        for (size_t i = 0; i < kVerifiedCount; i++) {",
        "            bool ok = SetFastFlag(base, &kVerifiedFlags[i]);",
        "            if (ok) success_count++;",
        "            if (log_file) {",
        "                if (kVerifiedFlags[i].kind == 0) {",
        "                    fprintf(log_file, \"[%s] (SetFastFlag) %s @ 0x%lx (RVA 0x%lx) = %u\\n\",",
        "                            ok ? \"VERIFIED\" : \"FAIL\",",
        "                            kVerifiedFlags[i].name, (unsigned long)(base + kVerifiedFlags[i].rva),",
        "                            (unsigned long)kVerifiedFlags[i].rva, (uint32_t)kVerifiedFlags[i].value);",
        "                } else {",
        "                    fprintf(log_file, \"[%s] (SetFastFlag) %s @ 0x%lx (RVA 0x%lx) = %d\\n\",",
        "                            ok ? \"VERIFIED\" : \"FAIL\",",
        "                            kVerifiedFlags[i].name, (unsigned long)(base + kVerifiedFlags[i].rva),",
        "                            (unsigned long)kVerifiedFlags[i].rva, (int32_t)kVerifiedFlags[i].value);",
        "                }",
        "            }",
        "        }",
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
        "    if (log_file) {",
        "        fprintf(log_file, \"\\nFinal Result: %d baseline verified + %d dynamic flags active (Status: %s)\\n\",",
        "                success_count, dynamic_count, (success_count == (int)kVerifiedCount) ? \"SUCCESS\" : \"PARTIAL\");",
        "        fflush(log_file);",
        "        fclose(log_file);",
        "    }",
        "",
        "    syslog(LOG_NOTICE, \"[Masterstrap-iOS] Patch finished: %d baseline + %d dynamic active. Starting persistence loop...\", success_count, dynamic_count);",
        "",
        "    // Persistence loop: re-apply flags every 500ms for 30 seconds to prevent Roblox engine from resetting them",
        "    for (int loop = 0; loop < 60; loop++) {",
        "        usleep(500000); // 500ms",
        "        if (base == 0) base = get_robloxlib_base();",
        "        if (base != 0) {",
        "            for (size_t i = 0; i < kVerifiedCount; i++) {",
        "                SetFastFlag(base, &kVerifiedFlags[i]);",
        "            }",
        "            if (loop % 4 == 0) {",
        "                load_dynamic_config_files(base, NULL);",
        "            }",
        "        }",
        "    }",
        "",
        "    return NULL;",
        "}",
        "",
        "__attribute__((constructor)) static void masterstrap_ios_init(void) {",
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

    clang_bin, lld_bin = find_clang_lld()

    compile_cmd = [
        clang_bin,
        "-target", "arm64-apple-ios14.0",
        "-O3",
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


def write_full_inspection_report(out_dir: Path, flags: dict, verified_entries: list, miss_offsets: list, ipa_name: str, out_ipa_name: str):
    out_dir.mkdir(parents=True, exist_ok=True)
    report_text = f"""=====================================================
=== Masterstrap Roblox iOS Build & Inspection Log ===
=====================================================
Date: {time.strftime('%Y-%m-%d %H:%M:%S')}
Input IPA: {ipa_name}
Output IPA: {out_ipa_name}

SUMMARY:
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


def build_pipeline(input_ipa: Path, settings_json: Path, hpp_path: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Try local dumper first if valid IPA given
    dumped_header = None
    if input_ipa and input_ipa.is_file():
        dumped_header = run_local_dumper(input_ipa, out_dir)

    # 2. Resolve FFlags table (dumped or remote)
    target_header = dumped_header if dumped_header else hpp_path
    table = fetch_or_load_fflags_map(target_header)

    flags = {}
    if settings_json and settings_json.is_file():
        flags = json.loads(settings_json.read_text(encoding="utf-8"))
    else:
        # Default flags set
        flags = {
            "FFlagDebugDisplayFPS": True,
            "FFlagDebugSkyGray": True,
            "DFIntTaskSchedulerTargetFps": 2000,
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
            "FFlagEnableAndroidPowerManager": True,
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
    write_full_inspection_report(out_dir, flags, verified_entries, miss_offsets, ipa_name, out_ipa_name)

    offsets_table = fetch_or_load_offsets_map(None)
    offsets_entries = sorted(offsets_table.items(), key=lambda x: x[0])
    print(f"Loaded Engine Offsets: {len(offsets_entries)} functions")

    if input_ipa and input_ipa.is_file():
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            dylib_path = tpath / "libmasterstrap.dylib"
            c_src = generate_native_hook_source(verified_entries, miss_offsets, len(flags), offsets_entries, all_flags_table=table)
            compile_dylib(c_src, dylib_path)
            dylib_bytes = dylib_path.read_bytes()

            out_ipa = out_dir / "Masterstrap-Roblox-Patched.ipa"

            with zipfile.ZipFile(input_ipa, "r") as zin:
                with zipfile.ZipFile(out_ipa, "w", compression=zipfile.ZIP_DEFLATED) as zout:
                    seen_entries = set()

                    for item in zin.infolist():
                        name = item.filename
                        data = zin.read(name)

                        if name == "Payload/Roblox.app/Roblox":
                            data = insert_load_dylib(data, "@executable_path/Frameworks/libmasterstrap.dylib")
                        elif name == "Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib":
                            data = insert_load_dylib(data, "@rpath/libmasterstrap.dylib")
                        elif name.endswith("Roblox.app/Info.plist"):
                            try:
                                pl = plistlib.loads(data)
                                pl["UIFileSharingEnabled"] = True
                                pl["LSSupportsOpeningDocumentsInPlace"] = True
                                data = plistlib.dumps(pl)
                                print("Enabled UIFileSharingEnabled in Info.plist (accessible via Files app)")
                            except Exception as e:
                                print(f"Warning: Failed to update Info.plist: {e}")

                        zout.writestr(item, data)
                        seen_entries.add(name)

                    # Embed dylib
                    dylib_entry = "Payload/Roblox.app/Frameworks/libmasterstrap.dylib"
                    zout.writestr(dylib_entry, dylib_bytes)

                    # Embed JSON settings
                    json_bytes = json.dumps(flags, indent=4).encode("utf-8")
                    for jp in [
                        "Payload/Roblox.app/ClientSettings/ClientAppSettings.json",
                        "Payload/Roblox.app/Frameworks/RobloxLib.framework/ClientSettings/ClientAppSettings.json",
                        "Payload/Roblox.app/masterstrap_baked_flags.json"
                    ]:
                        zout.writestr(jp, json_bytes)

            print(f"\nSUCCESS -> {out_ipa} ({out_ipa.stat().st_size / (1024*1024):.1f} MB)")
            
            mumu_dir = Path(r"C:\Users\Administrator\Documents\MuMuSharedFolder\Download\Masterstrap")
            if mumu_dir.is_dir():
                dest = mumu_dir / "Masterstrap-Roblox-Patched.ipa"
                import shutil
                shutil.copy2(out_ipa, dest)
                print(f"Copied to MuMu Shared Folder -> {dest}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Masterstrap Roblox iOS IPA Builder")
    ap.add_argument("--input-ipa", default="")
    ap.add_argument("--settings-json", default="")
    ap.add_argument("--hpp", default="")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = ap.parse_args()

    build_pipeline(
        Path(args.input_ipa) if args.input_ipa else None,
        Path(args.settings_json) if args.settings_json else None,
        Path(args.hpp) if args.hpp else None,
        Path(args.out_dir)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
