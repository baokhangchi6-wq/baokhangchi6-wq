"""Execute generated hooks and font reader on macOS, including inherited/super calls."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from build_integrated_roblox_ipa import generate_native_hook_source

def main():
    if sys.platform != 'darwin': raise SystemExit('Native backend checks require macOS Foundation')
    source = generate_native_hook_source([], [], 0, font_profile={
        'catalog': {'Minecraft': 'PressStart2P.json'},
        'stockFamilies': ['SourceSansPro.json', 'PressStart2P.json', 'NotoSansCJKFallback.json', 'RobloxEmoji.json']})
    source = source.replace('__attribute__((constructor)) static void masterstrap_ios_init', 'static void masterstrap_ios_init')
    source += r'''
static unsigned test_hook_calls;
static void test_void_hook(id self, SEL cmd, IMP original) {
    test_hook_calls++;
    ((void (*)(id, SEL))original)(self, cmd);
}
int test_hook_class(void* cls, const char* name) { return ms_hook_instance_method(cls, name, (IMP)test_void_hook, "v@:"); }
unsigned test_hook_count(void) { return test_hook_calls; }
int test_range_target(int target) {
    g_launch_authorized = g_profile_loaded = true;
    g_target_fps = target;
    g_ms_policy = (MSDisplayPolicy){true, true, false, false, target, 1};
    MSFrameRateRange range = ms_target_range();
    return range.preferred == target && range.minimum == target && range.maximum == target;
}
void test_font_paths(const char* root, const char* target) {
    char canonical[1024];
    if (!realpath(root, canonical)) return;
    snprintf(g_font_assets_root, sizeof(g_font_assets_root), "%s/", canonical);
    snprintf(g_font_target_path, sizeof(g_font_target_path), "%s/%s", canonical, target);
}
void* test_font_read(const char* path, const char* mode) { return font_fopen_hook(path, mode); }
int test_font_open(const char* path, int flags) { return font_open_hook(path, flags); }
void* test_font_slot(void) { return (void*)font_fopen_hook; }
int test_font_rebind(void* page) { return font_rebind_slot((uintptr_t)page, 8, (uintptr_t)font_fopen_hook); }
'''
    harness = r'''
#import <Foundation/Foundation.h>
#include <assert.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
extern int test_hook_class(void*, const char*);
extern unsigned test_hook_count(void);
extern int test_range_target(int);
extern void test_font_paths(const char*, const char*);
extern void* test_font_read(const char*, const char*);
extern int test_font_open(const char*, int);
extern int test_font_rebind(void*);
extern void* test_font_slot(void);
static unsigned rootCalls, childCalls;
@interface MSBackendRoot : NSObject
- (void)step;
- (double)wrong;
@end
@implementation MSBackendRoot
- (void)step { rootCalls++; }
- (double)wrong { return 42.0; }
@end
@interface MSBackendInherited : MSBackendRoot @end
@implementation MSBackendInherited @end
@interface MSBackendOverride : MSBackendRoot @end
@implementation MSBackendOverride
- (void)step { childCalls++; [super step]; }
@end
static NSString* makeFile(NSString* root, NSString* name, NSString* value) {
    NSString* path = [root stringByAppendingPathComponent:name];
    assert([value writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:NULL]);
    return path;
}
static void checkRead(NSString* path, const char* mode, const char* expected) {
    FILE* file = test_font_read(path.fileSystemRepresentation, mode);
    assert(file); char bytes[64] = {0}; assert(fread(bytes, 1, 63, file) == strlen(expected));
    assert(strcmp(bytes, expected) == 0); assert(fclose(file) == 0);
}
int main(void) { @autoreleasepool {
    MSBackendRoot* root = [MSBackendRoot new];
    MSBackendInherited* inherited = [MSBackendInherited new];
    MSBackendOverride* override = [MSBackendOverride new];
    assert(test_hook_class([MSBackendInherited class], "step"));
    [root step]; assert(rootCalls == 1 && test_hook_count() == 0); // no superclass mutation
    [inherited step]; assert(rootCalls == 2 && test_hook_count() == 1);
    assert(test_hook_class([MSBackendRoot class], "step"));
    assert(test_hook_class([MSBackendOverride class], "step"));
    assert(test_hook_class([MSBackendOverride class], "step")); // no self-capture
    [override step]; assert(rootCalls == 3 && childCalls == 1 && test_hook_count() == 3);
    assert(!test_hook_class([MSBackendRoot class], "wrong")); assert([root wrong] == 42);
    assert(test_range_target(60)); assert(test_range_target(2000));
    NSString* directory = [NSString stringWithUTF8String:getenv("HOME")];
    NSString* fonts = [directory stringByAppendingPathComponent:@"content/fonts/families"];
    assert([[NSFileManager defaultManager] createDirectoryAtPath:fonts withIntermediateDirectories:YES attributes:nil error:NULL]);
    NSString* original = makeFile(fonts, @"SourceSansPro.json", @"original");
    NSString* selected = makeFile(fonts, @"PressStart2P.json", @"selected");
    NSString* noto = makeFile(fonts, @"NotoSansCJKFallback.json", @"noto");
    NSString* outside = makeFile(directory, @"SourceSansPro.json", @"outside");
    test_font_paths(fonts.fileSystemRepresentation, "PressStart2P.json");
    checkRead(original, "r", "selected"); checkRead(original, "r+", "original");
    checkRead(noto, "r", "noto"); checkRead(outside, "r", "outside"); checkRead(selected, "r", "selected");
    int descriptor = test_font_open(original.fileSystemRepresentation, O_RDONLY);
    assert(descriptor >= 0); char bytes[16] = {0}; assert(read(descriptor, bytes, 15) == 8);
    assert(strcmp(bytes, "selected") == 0); assert(close(descriptor) == 0);
    assert([[NSFileManager defaultManager] removeItemAtPath:selected error:NULL]);
    checkRead(original, "r", "original"); // failed redirect falls back to original reader
    long pageSize = getpagesize();
    void* page = mmap(NULL, pageSize, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANON, -1, 0);
    assert(page != MAP_FAILED); ((void**)page)[1] = (void*)fopen;
    assert(mprotect(page, pageSize, PROT_READ) == 0);
    assert(test_font_rebind(page)); assert(((void**)page)[1] == test_font_slot());
    mach_vm_address_t regionAddress = (mach_vm_address_t)page;
    mach_vm_size_t regionSize = 0;
    vm_region_basic_info_data_64_t region;
    mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
    mach_port_t object = MACH_PORT_NULL;
    assert(mach_vm_region(mach_task_self(), &regionAddress, &regionSize, VM_REGION_BASIC_INFO_64,
        (vm_region_info_t)&region, &count, &object) == KERN_SUCCESS);
    if (object) mach_port_deallocate(mach_task_self(), object);
    assert(region.protection == PROT_READ); // restore actual VM permission, not header guesses
    assert(test_font_rebind(page)); assert(munmap(page, pageSize) == 0);
} return 0; }
'''
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'native.c').write_text(source, encoding='utf-8')
        (root / 'check.m').write_text(harness, encoding='utf-8')
        subprocess.run(['xcrun', 'clang', '-O0', '-fblocks', '-c', str(root / 'native.c'), '-o', str(root / 'native.o')], check=True)
        subprocess.run(['xcrun', 'clang', str(root / 'check.m'), str(root / 'native.o'), '-framework', 'Foundation', '-lz', '-o', str(root / 'check')], check=True)
        subprocess.run([str(root / 'check')], env={**os.environ, 'HOME': directory}, check=True)
    print('PASS native backends: class-local/inherited/super hooks, ABI rejection, font scope/fallback, read-only import rebinding')

if __name__ == '__main__': main()
