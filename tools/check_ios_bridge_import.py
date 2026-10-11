#!/usr/bin/env python3
"""Run the generated native URL importer on macOS, with network blocked."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.parse
import zlib

from build_integrated_roblox_ipa import generate_native_hook_source


def main():
    if sys.platform != 'darwin':
        raise SystemExit('Native bridge import verification requires macOS Foundation')
    payload = {
        'clientAppSettings': '{"FFlagExample":true,"DFIntExample":240}',
        'turboProfile': '{"schema":1,"targetFps":240,"stretchScreenPercent":80}',
        'globalSettings': '<roblox><int name="FramerateCap">240</int></roblox>',
        'shaderProfile': '{"schema":1,"package":"none","wetFloor":false}',
        'fontProfile': '{"schema":1,"font":"Minecraft 日本語","sizeStep":-1}',
        'targetChannel': 'global',
    }
    serialized = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()
    def url(version, data, request):
        encoded = urllib.parse.quote(base64.b64encode(data).decode(), safe='')
        return f'robloxmobile://apply?v={version}&request={request}&payload={encoded}'
    removed = {**payload, 'clientAppSettings': '{"DFIntExample":240}'}
    cases = [url(3, zlib.compress(serialized), 'native-v3-cold'),
             url(1, json.dumps(removed).encode(), 'native-v1-compat'),
             url(3, b'not-zlib', 'native-v3-invalid'),
             url(3, zlib.compress(b'x' * 524289), 'native-v3-oversize'),
             url(4, serialized, 'native-v4-rejected')]
    source = generate_native_hook_source([
        ('FFlagExample', 0x20, 0, 0), ('DFIntExample', 0x24, 1, 10000),
        ('DFIntTaskSchedulerTargetFps', 0x28, 1, 10000)], [], 3, [], {
        'FFlagExample': 0x20, 'DFIntExample': 0x24, 'DFIntTaskSchedulerTargetFps': 0x28})
    # Disable process injection and block HTTP only in this test executable.
    source = source.replace('__attribute__((constructor)) static void masterstrap_ios_init',
                            'static void masterstrap_ios_init')
    signature = 'static id bridge_json_post(const char* endpoint, id json_body, long expected_status) {'
    assert source.count(signature) == 1
    source = source.replace(signature, '''static int test_http_calls;
static id bridge_json_post(const char* endpoint, id json_body, long expected_status) {
    (void)endpoint; (void)json_body; (void)expected_status; test_http_calls++; return NULL;
}
static id unused_bridge_json_post(const char* endpoint, id json_body, long expected_status) {''')
    source += '''
int test_import_url(const char* text) {
    id url = ((id (*)(id, SEL, id))objc_msgSend)(objc_getClass("NSURL"),
        sel_registerName("URLWithString:"), bridge_nsstr(text));
    return import_masterstrap_bridge_url(url);
}
int test_network_count(void) { return test_http_calls; }
void test_publish_engine_base(void* base) { SESSION_LOCK; g_engine_base = (uintptr_t)base; }
void test_boot(void* base) { complete_runtime_boot((uintptr_t)base, NULL); }
char* test_document(const char* name) { SESSION_LOCK; return read_profile_file(name, NULL); }
int test_session(void* base, unsigned expected) {
    SESSION_LOCK;
    if (!g_runtime_ready || !g_profile_loaded || !g_launch_authorized ||
        g_session_apply_count != expected || strcmp(g_session_request, g_applied_request) != 0 ||
        g_target_fps != 240 || g_stretch_percent != 80 || g_font_size_step != -1) return 0;
    // Persistence must not restore baseline values into user-owned slots.
    apply_baseline_flags((uintptr_t)base);
    apply_cached_dynamic_flags((uintptr_t)base);
    write_runtime_capability_receipt();
    return *(uint8_t*)((uintptr_t)base + 0x20) == (expected == 1 ? 1 : 0) &&
        *(int32_t*)((uintptr_t)base + 0x24) == 240 &&
        *(int32_t*)((uintptr_t)base + 0x28) == 240;
}
int test_stale_authorization(void) {
    {
        SESSION_LOCK;
        ((void (*)(id, SEL))objc_msgSend)(g_session_payload, sel_registerName("release"));
        g_session_payload = NULL;
        g_session_request[0] = g_applied_request[0] = 0;
        g_launch_authorized = false;
        g_cached_dynamic_count = 0;
    }
    return local_launch_authorization_valid(NULL);
}

'''
    harness = r'''
#import <Foundation/Foundation.h>
#include <assert.h>
#include <pthread.h>
#include <sys/mman.h>
#include <string.h>
extern int test_import_url(const char*);
extern int test_network_count(void);
extern void test_publish_engine_base(void*);
extern void test_boot(void*);
extern char* test_document(const char*);
extern int test_session(void*, unsigned);
extern int test_stale_authorization(void);
static void* boot_thread(void* base) { @autoreleasepool { test_boot(base); } return NULL; }
int main(int argc, const char* argv[]) {
    @autoreleasepool {
        assert(argc == 8);
        NSString* documents = [NSString stringWithFormat:@"%s/Documents", getenv("HOME")];
        [[NSFileManager defaultManager] createDirectoryAtPath:documents
            withIntermediateDirectories:YES attributes:nil error:NULL];
        void* base = mmap(NULL, 16384, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
        assert(base != MAP_FAILED);
        test_publish_engine_base(base); // engine found does not mean boot completed
        pthread_t worker;
        bool racing = strcmp(argv[7], "race") == 0;
        if (strcmp(argv[7], "before") == 0) test_boot(base);
        if (racing) assert(pthread_create(&worker, NULL, boot_thread, base) == 0);
        assert(test_import_url(argv[1]));
        if (racing) assert(pthread_join(worker, NULL) == 0);
        if (strcmp(argv[7], "after") == 0) test_boot(base);
        assert(test_session(base, 1));
        test_boot(base); // same boot completion cannot reread consumed files
        assert(test_session(base, 1));
        assert(test_import_url(argv[1])); // duplicate cold/warm callback
        assert(test_session(base, 1));
        NSDictionary* receipt = [NSJSONSerialization JSONObjectWithData:
            [NSData dataWithContentsOfFile:[documents stringByAppendingPathComponent:@"MasterstrapBridgeReceipt.json"]]
            options:0 error:NULL];
        assert([receipt[@"protocol"] intValue] == 3);
        assert([receipt[@"status"] isEqual:@"imported"]);
        for (NSString* key in @[@"client", @"turbo", @"global", @"shader", @"font"])
            assert([receipt[key] boolValue]);
        NSDictionary* expected = [NSJSONSerialization JSONObjectWithData:
            [NSData dataWithContentsOfFile:[NSString stringWithUTF8String:argv[6]]] options:0 error:NULL];
        NSDictionary* files = @{@"clientAppSettings":@"ClientAppSettings.json",
            @"turboProfile":@"MasterstrapTurboProfile.json", @"globalSettings":@"GlobalBasicSettings_13.xml",
            @"shaderProfile":@"MasterstrapShaderProfile.json", @"fontProfile":@"MasterstrapFontProfile.json"};
        for (NSString* key in files) {
            char* bytes = test_document([files[key] UTF8String]);
            assert(bytes);
            assert([[NSString stringWithUTF8String:bytes] isEqual:expected[key]]);
            free(bytes);
        }
        assert(![[NSFileManager defaultManager] fileExistsAtPath:
            [documents stringByAppendingPathComponent:@"MasterstrapLaunchAuthorization.json"]]);
        assert(test_import_url(argv[2])); // uncompressed warm update
        assert(test_session(base, 2));
        assert(!test_import_url(argv[3]));
        assert(!test_import_url(argv[4]));
        assert(!test_import_url(argv[5]));
        assert(test_session(base, 2)); // rejected input must not damage the active snapshot
        assert(test_network_count() == 0);
        NSString* stale = [NSString stringWithFormat:@"{\"request\":\"native-v1-compat\",\"issuedAtUnix\":%.0f,\"singleUse\":true}", [[NSDate date] timeIntervalSince1970]];
        [stale writeToFile:[documents stringByAppendingPathComponent:@"MasterstrapLaunchAuthorization.json"]
            atomically:YES encoding:NSUTF8StringEncoding error:NULL];
        assert(!test_stale_authorization());
        assert(munmap(base, 16384) == 0);
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'native.c').write_text(source, encoding='utf-8')
        (root / 'check.m').write_text(harness, encoding='utf-8')
        (root / 'expected.json').write_bytes(serialized)
        subprocess.run(['xcrun', 'clang', '-O0', '-fblocks', '-c', str(root / 'native.c'),
                        '-o', str(root / 'native.o')], check=True)
        subprocess.run(['xcrun', 'clang', str(root / 'check.m'), str(root / 'native.o'),
                        '-framework', 'Foundation', '-lz', '-o', str(root / 'check')], check=True)
        for mode in ['before', 'after', 'race']:
            home = root / mode
            home.mkdir()
            subprocess.run([str(root / 'check'), *cases, str(root / 'expected.json'), mode], check=True,
                           env={**os.environ, 'HOME': str(home)})
            assert (home / 'Documents' / 'GlobalBasicSettings_13.xml').read_text() == payload['globalSettings']
            receipt = json.loads((home / 'Documents' / 'MasterstrapRuntimeReceipt.json').read_text())
            assert receipt['profileLoaded'] and receipt['configSource'] == 'BRIDGE_SNAPSHOT'
            assert receipt['applyCount'] == 2 and receipt['request'] == receipt['appliedRequest']
    print('PASS native importer: boot before/after/concurrent preserves five profiles and user flags; each request applies once; stale auth rejected; zero HTTP')


if __name__ == '__main__':
    main()
