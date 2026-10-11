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
void test_observe_device(void* device) {
    g_probe_enabled = true;
    g_probe_started = ms_probe_time();
    ms_observe_metal_device(device);
}
int test_pipeline(unsigned commits, unsigned completed, unsigned presents) {
    return g_probe_commits == commits && g_probe_completed == completed && g_probe_scheduled == presents;
}
unsigned test_method_count(void) { return g_ms_method_hook_count; }
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
int test_layer_properties(void* layer) {
    ms_apply_metal_layer_policies(layer);
    return ms_bool_property(layer, "displaySyncEnabled", "isDisplaySyncEnabled") == 0 &&
        ms_count_property(layer, "maximumDrawableCount") == 3 && ms_bool_property(layer, "presentsWithTransaction", NULL) == 0;
}
int test_missing_layer_property(void* object) { return ms_bool_property(object, "displaySyncEnabled", "isDisplaySyncEnabled"); }
void test_drawable_track(void* drawable) {
    g_probe_enabled = true; g_probe_generation = 1;
    ms_track_drawable_lifetime(drawable);
}
unsigned long test_drawable_metric(int index) { return g_drawable_metrics[index].count; }
void test_refresh_layers(void* layer) { ms_track_metal_layer(layer); ms_refresh_observed_layers(); }
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
extern void test_observe_device(void*);
extern int test_pipeline(unsigned, unsigned, unsigned);
extern unsigned test_method_count(void);
extern int test_range_target(int);
extern void test_font_paths(const char*, const char*);
extern void* test_font_read(const char*, const char*);
extern int test_font_open(const char*, int);
extern int test_font_rebind(void*);
extern void* test_font_slot(void);
extern int test_layer_properties(void*);
extern int test_missing_layer_property(void*);
extern void test_drawable_track(void*);
extern unsigned long test_drawable_metric(int);
extern void test_refresh_layers(void*);
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
static unsigned commits, directPresents, delayedPresents;
@interface MSLateBuffer : NSObject { void (^completion)(id); }
- (void)commit;
- (void)presentDrawable:(id)drawable;
- (void)presentDrawable:(id)drawable atTime:(double)time;
- (void)addCompletedHandler:(void (^)(id))handler;
- (double)GPUStartTime;
- (double)GPUEndTime;
@end
@implementation MSLateBuffer
- (void)commit { commits++; if (completion) completion(self); }
- (void)presentDrawable:(id)drawable { (void)drawable; directPresents++; }
- (void)presentDrawable:(id)drawable atTime:(double)time { (void)drawable; (void)time; delayedPresents++; }
- (void)addCompletedHandler:(void (^)(id))handler { [completion release]; completion = [handler copy]; }
- (double)GPUStartTime { return 1.0; }
- (double)GPUEndTime { return 1.003; }
- (void)dealloc { [completion release]; [super dealloc]; }
@end
@interface MSLateQueue : NSObject
- (id)commandBuffer;
@end
@implementation MSLateQueue
- (id)commandBuffer { return [[MSLateBuffer new] autorelease]; }
@end
@interface MSLateDevice : NSObject
- (id)newCommandQueue;
- (id)newCommandQueueWithMaxCommandBufferCount:(unsigned long)count;
@end
@implementation MSLateDevice
- (id)newCommandQueue { return [MSLateQueue new]; }
- (id)newCommandQueueWithMaxCommandBufferCount:(unsigned long)count { (void)count; return [MSLateQueue new]; }
@end
@interface MSLayerProperties : NSObject
@property BOOL displaySyncEnabled;
@property BOOL presentsWithTransaction;
@property BOOL allowsNextDrawableTimeout;
@property NSUInteger maximumDrawableCount;
@end
@implementation MSLayerProperties @end
static unsigned drawableDestroyed;
@interface MSObservedDrawable : NSObject @end
@implementation MSObservedDrawable
- (void)dealloc { drawableDestroyed++; [super dealloc]; }
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
    MSLayerProperties* layer = [MSLayerProperties new]; layer.displaySyncEnabled = YES; layer.presentsWithTransaction = YES;
    assert(test_layer_properties(layer)); assert(test_missing_layer_property(root) == -1);
    layer.displaySyncEnabled = YES; test_refresh_layers(layer); assert(layer.displaySyncEnabled == NO); [layer release];
    MSLateDevice* device = [MSLateDevice new]; // no Metal protocols; concrete late discovery must work
    test_observe_device(device);
    assert(commits == 0 && test_pipeline(0, 0, 0)); // bootstrap cannot submit GPU work
    unsigned methodCount = test_method_count();
    test_observe_device(device); assert(test_method_count() == methodCount);
    MSLateQueue* queue = [device newCommandQueueWithMaxCommandBufferCount:3];
    MSLateBuffer* buffer = [queue commandBuffer];
    [buffer presentDrawable:root atTime:99];
    assert(directPresents == 1 && delayedPresents == 0 && test_pipeline(0, 0, 1));
    [buffer commit]; assert(commits == 1 && test_pipeline(1, 1, 1));
    [queue release]; [device release];
    @autoreleasepool {
        MSObservedDrawable* drawable = [MSObservedDrawable new];
        test_drawable_track(drawable); test_drawable_track(drawable);
        assert(test_drawable_metric(2) == 1 && drawableDestroyed == 0);
        [drawable release]; assert(drawableDestroyed == 1); // tracker must not retain the drawable
        assert(test_drawable_metric(1) == 1);
    }
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
    assert(!test_font_rebind(NULL)); // reject unmapped input before any pointer read
} return 0; }
'''
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'native.c').write_text(source, encoding='utf-8')
        (root / 'check.m').write_text(harness, encoding='utf-8')
        subprocess.run(['xcrun', 'clang', '-O0', '-fblocks', '-c', str(root / 'native.c'), '-o', str(root / 'native.o')], check=True)
        subprocess.run(['xcrun', 'clang++', '-std=c++17', '-O0', '-c', str(Path(__file__).parent / 'ios_pacing_probe.cpp'),
                        '-o', str(root / 'pacing.o')], check=True)
        subprocess.run(['xcrun', 'clang', '-fobjc-arc', '-O0', '-c', str(Path(__file__).parent / 'ios_drawable_lifetime.m'),
                        '-o', str(root / 'drawable.o')], check=True)
        subprocess.run(['xcrun', 'clang', str(root / 'check.m'), str(root / 'native.o'), str(root / 'pacing.o'), str(root / 'drawable.o'), '-framework', 'Foundation', '-lz', '-lc++', '-o', str(root / 'check')], check=True)
        subprocess.run([str(root / 'check')], env={**os.environ, 'HOME': directory}, check=True)
    print('PASS native backends: class-local/inherited/super hooks, ABI rejection, font scope/fallback, read-only import rebinding')

if __name__ == '__main__': main()
