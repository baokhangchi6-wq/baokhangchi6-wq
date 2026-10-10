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
    cases = [url(3, zlib.compress(serialized), 'native-v3-cold'),
             url(1, serialized, 'native-v1-compat'),
             url(3, b'not-zlib', 'native-v3-invalid'),
             url(3, zlib.compress(b'x' * 524289), 'native-v3-oversize'),
             url(4, serialized, 'native-v4-rejected')]
    source = generate_native_hook_source([], [], 0, [], {})
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
'''
    harness = r'''
#import <Foundation/Foundation.h>
#include <assert.h>
extern int test_import_url(const char*);
extern int test_network_count(void);
int main(int argc, const char* argv[]) {
    @autoreleasepool {
        assert(argc == 7);
        NSString* documents = [NSString stringWithFormat:@"%s/Documents", getenv("HOME")];
        [[NSFileManager defaultManager] createDirectoryAtPath:documents
            withIntermediateDirectories:YES attributes:nil error:NULL];
        assert(test_import_url(argv[1]));
        assert(test_import_url(argv[1])); // duplicate cold/warm callback
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
            NSString* value = [NSString stringWithContentsOfFile:[documents stringByAppendingPathComponent:files[key]]
                encoding:NSUTF8StringEncoding error:NULL];
            assert([value isEqual:expected[key]]);
        }
        assert(test_import_url(argv[2])); // uncompressed protocol 1 compatibility
        assert(!test_import_url(argv[3]));
        assert(!test_import_url(argv[4]));
        assert(!test_import_url(argv[5]));
        assert(test_network_count() == 0);
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'native.c').write_text(source, encoding='utf-8')
        (root / 'check.m').write_text(harness, encoding='utf-8')
        (root / 'expected.json').write_bytes(serialized)
        subprocess.run(['xcrun', 'clang', '-O0', '-c', str(root / 'native.c'),
                        '-o', str(root / 'native.o')], check=True)
        subprocess.run(['xcrun', 'clang', str(root / 'check.m'), str(root / 'native.o'),
                        '-framework', 'Foundation', '-lz', '-o', str(root / 'check')], check=True)
        subprocess.run([str(root / 'check'), *cases, str(root / 'expected.json')], check=True,
                       env={**os.environ, 'HOME': directory})
        for key, name in [('clientAppSettings', 'ClientAppSettings.json'),
                          ('turboProfile', 'MasterstrapTurboProfile.json'),
                          ('globalSettings', 'GlobalBasicSettings_13.xml'),
                          ('shaderProfile', 'MasterstrapShaderProfile.json'),
                          ('fontProfile', 'MasterstrapFontProfile.json')]:
            assert (root / 'Documents' / name).read_text(encoding='utf-8') == payload[key], name
    print('PASS native importer: v3/v1 preserve five profiles; duplicate, invalid and oversize URLs use zero HTTP')


if __name__ == '__main__':
    main()
