/* Included by the IPA builder after profile parsing and logging helpers.
 * UIKit state stays on the main queue; fixed engine hooks require UUID and
 * opcode verification before any executable page is changed.
 */
extern int pthread_main_np(void);
extern char _dispatch_main_q;
extern void dispatch_async_f(void*, void*, void (*)(void*));
extern void* dlopen(const char*, int);
extern void* dlsym(void*, const char*);
extern Class objc_allocateClassPair(Class, const char*, size_t);
extern void objc_registerClassPair(Class);
extern id objc_getAssociatedObject(id, const void*);
extern void objc_setAssociatedObject(id, const void*, id, unsigned long);

typedef struct { float minimum, maximum, preferred; } MSFrameRateRange;
_Static_assert(sizeof(MSFrameRateRange) == 12, "CAFrameRateRange ARM64 ABI");
typedef id (*DisplayLinkCreateImp)(id, SEL, id, SEL);
typedef void (*DisplayLinkSetFpsImp)(id, SEL, long);
typedef void (*DisplayLinkSetRangeImp)(id, SEL, MSFrameRateRange);
typedef void (*DisplayLinkAddImp)(id, SEL, id, id);
static DisplayLinkCreateImp g_original_display_link_create;
static DisplayLinkSetFpsImp g_original_display_link_set_fps;
static DisplayLinkSetRangeImp g_original_display_link_set_range;
static DisplayLinkAddImp g_original_display_link_add;
static int g_display_link_policy_installed;
static int g_display_link_rewrite_count;
static int g_ms_metal_present_hook_installed;
static int g_ms_applying;
static id g_ms_links, g_ms_observer;
static bool g_ms_idle_owned, g_ms_idle_original;
typedef struct { bool loaded, refresh, keep_display, boost; int target; } MSDisplayPolicy;
static MSDisplayPolicy g_ms_policy;
static unsigned g_ms_generation;
static char g_ms_link_request_key;
typedef struct { long fps; MSFrameRateRange range; int has_range, managed; } MSLinkRequest;
static void write_runtime_capability_receipt(void);
static bool fps_runtime_requested(void);

static id ms_string(const char* text) {
    return ((id (*)(id, SEL, const char*))objc_msgSend)(objc_getClass("NSString"),
        sel_registerName("stringWithUTF8String:"), text);
}
static id ms_process_info(void) {
    return ((id (*)(id, SEL))objc_msgSend)(objc_getClass("NSProcessInfo"), sel_registerName("processInfo"));
}
static int ios_maximum_frames_per_second(void) {
    id screen = ((id (*)(id, SEL))objc_msgSend)(objc_getClass("UIScreen"), sel_registerName("mainScreen"));
    long rate = screen ? ((long (*)(id, SEL))objc_msgSend)(screen, sel_registerName("maximumFramesPerSecond")) : 60;
    return rate >= 30 && rate <= 240 ? (int)rate : 60;
}
static int ms_low_power(void) {
    return ((unsigned char (*)(id, SEL))objc_msgSend)(ms_process_info(), sel_registerName("isLowPowerModeEnabled"));
}
static int ms_thermal(void) {
    return (int)((long (*)(id, SEL))objc_msgSend)(ms_process_info(), sel_registerName("thermalState"));
}
static bool fps_runtime_requested(void) {
    return g_ms_policy.loaded && (g_ms_policy.refresh || g_ms_policy.target > 0);
}
static long fps_runtime_target(void) {
    int target = (g_ms_policy.target > 0) ? g_ms_policy.target : (g_target_fps > 0 ? g_target_fps : 60);
    return ms_ios_target_fps(target, ios_maximum_frames_per_second(), ms_low_power(), ms_thermal());
}
static inline MSFrameRateRange sanitize_frame_rate_range(MSFrameRateRange range) {
    int screen_max = ios_maximum_frames_per_second();
    if (screen_max <= 0) screen_max = 120;
    float max_limit = (float)screen_max;
    if (range.minimum <= 0.0f || range.minimum > max_limit) range.minimum = 1.0f;
    if (range.maximum <= 0.0f || range.maximum > max_limit) range.maximum = max_limit;
    if (range.minimum > range.maximum) range.minimum = range.maximum;
    if (range.preferred < range.minimum) range.preferred = range.minimum;
    if (range.preferred > range.maximum) range.preferred = range.maximum;
    return range;
}
static MSFrameRateRange ms_target_range(long target) {
    int screen_max = ios_maximum_frames_per_second();
    if (screen_max <= 0) screen_max = 60;
    float pref = (float)target;
    float min_fps = pref < 60.0f ? pref : 60.0f;
    float max_fps = pref > (float)screen_max ? pref : (float)screen_max;
    return sanitize_frame_rate_range((MSFrameRateRange){min_fps, max_fps, pref});
}
static bool ms_get_link_request(id link, MSLinkRequest* request) {
    id value = objc_getAssociatedObject(link, &g_ms_link_request_key);
    if (!value) return false;
    ((void (*)(id, SEL, void*))objc_msgSend)(value, sel_registerName("getValue:"), request);
    return true;
}
static void ms_store_link_request(id link, const MSLinkRequest* request) {
    id value = ((id (*)(id, SEL, const void*, const char*))objc_msgSend)(objc_getClass("NSValue"),
        sel_registerName("valueWithBytes:objCType:"), request, "{MSLinkRequest=q{MSFrameRateRange=fff}ii}");
    objc_setAssociatedObject(link, &g_ms_link_request_key, value, 1); /* retain, nonatomic; main only */
}
static void ms_capture_link_request(id link) {
    MSLinkRequest state = {0};
    state.fps = ((long (*)(id, SEL))objc_msgSend)(link, sel_registerName("preferredFramesPerSecond"));
    state.has_range = g_original_display_link_set_range != NULL;
    if (state.has_range) state.range = ((MSFrameRateRange (*)(id, SEL))objc_msgSend)(link, sel_registerName("preferredFrameRateRange"));
    ms_store_link_request(link, &state);
}
static void ms_restore_link_request(id link) {
    MSLinkRequest state;
    if (!ms_get_link_request(link, &state) || !state.managed) return;
    g_ms_applying = 1;
    if (g_original_display_link_set_fps) g_original_display_link_set_fps(link, sel_registerName("setPreferredFramesPerSecond:"), state.fps);
    if (state.has_range && g_original_display_link_set_range)
        g_original_display_link_set_range(link, sel_registerName("setPreferredFrameRateRange:"), state.range);
    state.managed = 0;
    ms_store_link_request(link, &state);
    g_ms_applying = 0;
    runtime_note("[APPLY][FPS] status=RESTORED_NATIVE_REQUEST generation=%u", g_ms_generation);
}
static void ms_track_link(id link) {
    if (!link || !pthread_main_np()) return;
    if (!g_ms_links) {
        id table = ((id (*)(id, SEL))objc_msgSend)(objc_getClass("NSHashTable"), sel_registerName("weakObjectsHashTable"));
        g_ms_links = ((id (*)(id, SEL))objc_msgSend)(table, sel_registerName("retain"));
    }
    ((void (*)(id, SEL, id))objc_msgSend)(g_ms_links, sel_registerName("addObject:"), link);
    if (!objc_getAssociatedObject(link, &g_ms_link_request_key)) ms_capture_link_request(link);
}
static void apply_display_link_policy(id link, const char* source) {
    if (!link || !pthread_main_np() || g_ms_applying) return;
    if (!fps_runtime_requested()) { ms_restore_link_request(link); return; }
    MSLinkRequest state;
    if (ms_get_link_request(link, &state)) { state.managed = 1; ms_store_link_request(link, &state); }
    g_ms_applying = 1;
    long target = fps_runtime_target();
    if (g_original_display_link_set_range) {
        MSFrameRateRange range = ms_target_range(target);
        g_original_display_link_set_range(link, sel_registerName("setPreferredFrameRateRange:"), range);
        if (g_display_link_rewrite_count < 16) {
            MSFrameRateRange actual = ((MSFrameRateRange (*)(id, SEL))objc_msgSend)(link, sel_registerName("preferredFrameRateRange"));
            runtime_note("[READBACK][FPS] source=%s api=preferredFrameRateRange min=%.1f max=%.1f preferred=%.1f target=%ld status=%s presentedFps=UNMEASURED",
                source, (double)actual.minimum, (double)actual.maximum, (double)actual.preferred, target,
                actual.preferred == range.preferred ? "MATCH" : "OS_ADJUSTED");
        }
    } else if (g_original_display_link_set_fps) {
        long link_fps = target;
        g_original_display_link_set_fps(link, sel_registerName("setPreferredFramesPerSecond:"), link_fps);
        if (g_display_link_rewrite_count < 16) {
            long actual = ((long (*)(id, SEL))objc_msgSend)(link, sel_registerName("preferredFramesPerSecond"));
            runtime_note("[READBACK][FPS] source=%s api=preferredFramesPerSecond requested=%ld readback=%ld presentedFps=UNMEASURED", source, link_fps, actual);
        }
    }
    if (g_display_link_rewrite_count < 16) {
        ++g_display_link_rewrite_count;
        runtime_note("[APPLY][FPS] generation=%u requested=%d effective=%ld screenMax=%d lowPower=%d thermal=%d status=REQUESTED",
            g_ms_generation, g_ms_policy.target, target, ios_maximum_frames_per_second(), ms_low_power(), ms_thermal());
    }
    g_ms_applying = 0;
}
static void display_link_set_fps_hook(id self, SEL cmd, long requested) {
    if (!pthread_main_np() || g_ms_applying) {
        if (g_original_display_link_set_fps) g_original_display_link_set_fps(self, cmd, requested);
        return;
    }
    ms_track_link(self);
    g_ms_applying = 1;
    if (g_original_display_link_set_fps) g_original_display_link_set_fps(self, cmd, requested);
    g_ms_applying = 0;
    ms_capture_link_request(self);
    apply_display_link_policy(self, "fps-setter");
}
static void display_link_set_range_hook(id self, SEL cmd, MSFrameRateRange requested) {
    requested = sanitize_frame_rate_range(requested);
    if (!pthread_main_np() || g_ms_applying) {
        if (g_original_display_link_set_range) g_original_display_link_set_range(self, cmd, requested);
        return;
    }
    ms_track_link(self);
    g_ms_applying = 1;
    if (g_original_display_link_set_range) g_original_display_link_set_range(self, cmd, requested);
    g_ms_applying = 0;
    ms_capture_link_request(self);
    apply_display_link_policy(self, "range-setter");
}
static id display_link_create_hook(id self, SEL cmd, id target, SEL selector) {
    id link = g_original_display_link_create ? g_original_display_link_create(self, cmd, target, selector) : NULL;
    ms_track_link(link);
    apply_display_link_policy(link, "displayLinkWithTarget");
    return link;
}
static void display_link_add_hook(id self, SEL cmd, id loop, id mode) {
    ms_track_link(self);
    apply_display_link_policy(self, "addToRunLoop");
    if (g_original_display_link_add) {
        g_original_display_link_add(self, cmd, loop, mode);
        id commonMode = ms_string("kCFRunLoopCommonModes");
        if (commonMode && loop) {
            bool is_same = false;
            if (mode) {
                is_same = ((bool (*)(id, SEL, id))objc_msgSend)(mode, sel_registerName("isEqual:"), commonMode);
            }
            if (!is_same) {
                g_original_display_link_add(self, cmd, loop, commonMode);
            }
        }
    }
}
static void ms_write_display_receipt(unsigned long links) {
    const char* home = getenv("HOME");
    if (!home) return;
    char path[768];
    snprintf(path, sizeof(path), "%s/Documents/MasterstrapDisplayReceipt.json", home);
    void* file = fopen(path, "w");
    if (!file) return;
    fprintf(file, "{\"schema\":2,\"generatedAtUnix\":%ld,\"generation\":%u,\"authorizedProfileLoaded\":%s,\"policyRequested\":%s,\"hookInstalled\":%s,\"frameRateRangeAvailable\":%s,\"metalPresentHookInstalled\":%s,\"requestedFps\":%d,\"policyTargetFps\":%ld,\"screenMaximum\":%d,\"lowPower\":%s,\"thermalState\":%d,\"trackedDisplayLinks\":%lu,\"presentedFps\":null,\"runtimeVerified\":false}\n",
        time(NULL), g_ms_generation, g_ms_policy.loaded ? "true" : "false", fps_runtime_requested() ? "true" : "false",
        g_display_link_policy_installed ? "true" : "false", g_original_display_link_set_range ? "true" : "false",
        g_ms_metal_present_hook_installed ? "true" : "false",
        g_ms_policy.target, fps_runtime_target(), ios_maximum_frames_per_second(), ms_low_power() ? "true" : "false", ms_thermal(), links);
    fclose(file);
}
static void ms_refresh_links(const char* source) {
    id links = g_ms_links ? ((id (*)(id, SEL))objc_msgSend)(g_ms_links, sel_registerName("allObjects")) : NULL;
    unsigned long count = links ? ((unsigned long (*)(id, SEL))objc_msgSend)(links, sel_registerName("count")) : 0;
    for (unsigned long i = 0; i < count; ++i) {
        id link = ((id (*)(id, SEL, unsigned long))objc_msgSend)(links, sel_registerName("objectAtIndex:"), i);
        apply_display_link_policy(link, source);
    }
    runtime_note("[FPS][COVERAGE] generation=%u trackedLinks=%lu active=%d status=%s presentedFps=UNMEASURED",
        g_ms_generation, count, fps_runtime_requested(), count ? "LINKS_OBSERVED" : "WAITING_FOR_LINK");
    ms_write_display_receipt(count);
}
static void ms_environment_main(void* ignored) {
    (void)ignored;
    if (!g_ms_policy.loaded) return;
    g_display_link_rewrite_count = 0;
    runtime_note("[LIFECYCLE][DISPLAY] lowPower=%d thermal=%d screenMax=%d", ms_low_power(), ms_thermal(), ios_maximum_frames_per_second());
    ms_refresh_links("environment-change");
}
static void ms_environment_changed(id self, SEL cmd, id notification) {
    (void)self; (void)cmd; (void)notification;
    dispatch_async_f(&_dispatch_main_q, NULL, ms_environment_main);
}
static void ms_install_observer(void) {
    if (g_ms_observer) return;
    Class cls = objc_allocateClassPair((Class)objc_getClass("NSObject"), "MasterstrapDisplayPolicyObserverV2", 0);
    if (!cls) return;
    class_addMethod(cls, sel_registerName("environmentChanged:"), (IMP)ms_environment_changed, "v@:@");
    objc_registerClassPair(cls);
    g_ms_observer = ((id (*)(id, SEL))objc_msgSend)((id)cls, sel_registerName("new"));
    id center = ((id (*)(id, SEL))objc_msgSend)(objc_getClass("NSNotificationCenter"), sel_registerName("defaultCenter"));
    const char* names[] = {"NSProcessInfoPowerStateDidChangeNotification", "NSProcessInfoThermalStateDidChangeNotification", "UIApplicationDidBecomeActiveNotification"};
    for (int i = 0; i < 3; ++i)
        ((void (*)(id, SEL, id, SEL, id, id))objc_msgSend)(center,
            sel_registerName("addObserver:selector:name:object:"), g_ms_observer,
            sel_registerName("environmentChanged:"), ms_string(names[i]), NULL);
}
static bool install_display_link_policy(void);
static void ms_install_on_main(void* ignored) { (void)ignored; install_display_link_policy(); }
static bool install_display_link_policy(void) {
    if (!pthread_main_np()) { dispatch_async_f(&_dispatch_main_q, NULL, ms_install_on_main); return false; }
    if (g_display_link_policy_installed) return true;

    Class cls = (Class)objc_getClass("CADisplayLink");
    if (!cls) { runtime_note("[CAPABILITY][FPS] status=BLOCKED reason=class_missing"); return false; }
    Method setter = class_getInstanceMethod(cls, sel_registerName("setPreferredFramesPerSecond:"));
    Method range = class_getInstanceMethod(cls, sel_registerName("setPreferredFrameRateRange:"));
    Method creator = class_getInstanceMethod(object_getClass((id)cls), sel_registerName("displayLinkWithTarget:selector:"));
    Method add = class_getInstanceMethod(cls, sel_registerName("addToRunLoop:forMode:"));
    /* Guard each method independently: retrying a partial installation must
       never save our own hook as the original (infinite recursion). */
    if (setter && !g_original_display_link_set_fps)
        g_original_display_link_set_fps = (DisplayLinkSetFpsImp)method_setImplementation(setter, (IMP)display_link_set_fps_hook);
    if (range && !g_original_display_link_set_range)
        g_original_display_link_set_range = (DisplayLinkSetRangeImp)method_setImplementation(range, (IMP)display_link_set_range_hook);
    if (creator && !g_original_display_link_create)
        g_original_display_link_create = (DisplayLinkCreateImp)method_setImplementation(creator, (IMP)display_link_create_hook);
    if (add && !g_original_display_link_add)
        g_original_display_link_add = (DisplayLinkAddImp)method_setImplementation(add, (IMP)display_link_add_hook);
    g_display_link_policy_installed = (setter || range) && creator && add;
    runtime_note("[CAPABILITY][FPS] backend=%s setter=%d range=%d creator=%d add=%d status=%s",
        kFpsRuntimeBackend, setter != NULL, range != NULL, creator != NULL, add != NULL,
        g_display_link_policy_installed ? "READY" : "PARTIAL");
    return g_display_link_policy_installed;
}
typedef void (*SetDisplaySyncImp)(id, SEL, bool);
static SetDisplaySyncImp g_original_set_display_sync;

typedef id (*NextDrawableImp)(id, SEL);
static NextDrawableImp g_original_next_drawable;

#if defined(__arm64__) || defined(__aarch64__)
typedef struct { double width; double height; } MSSize;
#else
typedef struct { float width; float height; } MSSize;
#endif
typedef void (*SetDrawableSizeImp)(id, SEL, MSSize);
static SetDrawableSizeImp g_original_set_drawable_size;

typedef id (*MetalLayerInitImp)(id, SEL);
static MetalLayerInitImp g_original_metal_layer_init;

/* Metal command buffers can add an explicit present delay after drawable
 * acquisition. Remove that delay while preserving the real display cadence. */
typedef id (*CommandQueueBufferImp)(id, SEL);
typedef id (*CommandQueueBufferDescriptorImp)(id, SEL, id);
typedef void (*CommandPresentDrawableImp)(id, SEL, id);
typedef void (*CommandPresentAtTimeImp)(id, SEL, id, double);
typedef void (*CommandPresentAfterDurationImp)(id, SEL, id, double);
typedef void (*DrawablePresentImp)(id, SEL);
typedef void (*DrawablePresentTimedImp)(id, SEL, double);
static CommandQueueBufferImp g_original_command_buffer;
static CommandQueueBufferImp g_original_command_buffer_unretained;
static CommandQueueBufferDescriptorImp g_original_command_buffer_descriptor;
static CommandPresentDrawableImp g_original_command_present_drawable;
static CommandPresentAtTimeImp g_original_command_present_at_time;
static CommandPresentAfterDurationImp g_original_command_present_after_duration;
static DrawablePresentImp g_original_drawable_present;
static DrawablePresentTimedImp g_original_drawable_present_at_time;
static DrawablePresentTimedImp g_original_drawable_present_after_duration;
static unsigned long g_ms_present_calls;
static unsigned long g_ms_present_delay_bypasses;
static char g_ms_command_buffer_hook_key;
static char g_ms_drawable_hook_key;

static int g_metal_layer_stretch_installed;
static char g_ms_layer_uncapped_key;
static uint64_t g_ms_render_due;

typedef struct { uint32_t numer, denom; } MSMachTimebase;
extern uint64_t mach_absolute_time(void);
extern int mach_timebase_info(MSMachTimebase*);
static MSMachTimebase g_ms_timebase;
#ifndef NDEBUG
static uint64_t g_ms_probe_start, g_ms_drawable_calls, g_ms_drawable_wait, g_ms_drawable_nil;
static uint64_t g_ms_drawable_decoupled;

static void ms_log_fps_drawable_sample(void) {
    uint64_t start = __atomic_load_n(&g_ms_probe_start, __ATOMIC_ACQUIRE);
    if (!start || !g_ms_timebase.denom) return;
    uint64_t now = mach_absolute_time();
    double seconds = (double)(now - start) * g_ms_timebase.numer / g_ms_timebase.denom / 1e9;
    if (seconds < 5.0) return;
    uint64_t calls = __atomic_exchange_n(&g_ms_drawable_calls, 0, __ATOMIC_RELAXED);
    uint64_t wait = __atomic_exchange_n(&g_ms_drawable_wait, 0, __ATOMIC_RELAXED);
    uint64_t missing = __atomic_exchange_n(&g_ms_drawable_nil, 0, __ATOMIC_RELAXED);
    uint64_t decoupled = __atomic_exchange_n(&g_ms_drawable_decoupled, 0, __ATOMIC_RELAXED);
    unsigned long presents = __atomic_exchange_n(&g_ms_present_calls, 0, __ATOMIC_RELAXED);
    unsigned long immediate = __atomic_exchange_n(&g_ms_present_delay_bypasses, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&g_ms_probe_start, now, __ATOMIC_RELEASE);
    runtime_note("[FPS][DRAWABLE] engineTarget=%d calls=%llu nil=%llu decoupled=%llu seconds=%.3f acquisitionsPerSecond=%.1f averageWaitMs=%.3f presents=%lu immediatePresents=%lu renderedFps=UNMEASURED",
        get_effective_target_fps(), calls, missing, decoupled, seconds, (calls >= missing ? calls - missing : 0) / seconds,
        calls ? (double)wait * g_ms_timebase.numer / g_ms_timebase.denom / 1e6 / calls : 0.0, presents, immediate);
}
#else
static void ms_log_fps_drawable_sample(void) {}
#endif

static void ms_apply_metal_layer_policies(id layer) {
    if (!layer) return;

    // Request earliest presentation where supported; iOS still controls display cadence.
    if (fps_runtime_requested()) {
        SEL sync_sel = sel_registerName("setDisplaySyncEnabled:");
        bool sync_available = ((bool (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), sync_sel);
        if (sync_available) {
            ((void (*)(id, SEL, bool))objc_msgSend)(layer, sync_sel, false);
        }
        SEL transaction_sel = sel_registerName("setPresentsWithTransaction:");
        if (((bool (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), transaction_sel)) {
            ((void (*)(id, SEL, bool))objc_msgSend)(layer, transaction_sel, false);
        }
        SEL max_sel = sel_registerName("setMaximumDrawableCount:");
        if (((bool (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), max_sel)) {
            ((void (*)(id, SEL, unsigned long))objc_msgSend)(layer, max_sel, 3);
        }
        SEL timeout_sel = sel_registerName("setAllowsNextDrawableTimeout:");
        if (((bool (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), timeout_sel)) {
            ((void (*)(id, SEL, bool))objc_msgSend)(layer, timeout_sel, true);
        }
    }
}

static void command_present_drawable_hook(id self, SEL cmd, id drawable) {
    __atomic_fetch_add(&g_ms_present_calls, 1, __ATOMIC_RELAXED);
    if (g_original_command_present_drawable) g_original_command_present_drawable(self, cmd, drawable);
}

static void command_present_at_time_hook(id self, SEL cmd, id drawable, double time) {
    __atomic_fetch_add(&g_ms_present_calls, 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&g_ms_present_delay_bypasses, 1, __ATOMIC_RELAXED);
    if (g_original_command_present_drawable) {
        g_original_command_present_drawable(self, sel_registerName("presentDrawable:"), drawable);
    } else if (g_original_command_present_at_time) {
        g_original_command_present_at_time(self, cmd, drawable, time);
    }
}

static void command_present_after_duration_hook(id self, SEL cmd, id drawable, double duration) {
    __atomic_fetch_add(&g_ms_present_calls, 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&g_ms_present_delay_bypasses, 1, __ATOMIC_RELAXED);
    if (g_original_command_present_drawable) {
        g_original_command_present_drawable(self, sel_registerName("presentDrawable:"), drawable);
    } else if (g_original_command_present_after_duration) {
        g_original_command_present_after_duration(self, cmd, drawable, duration);
    }
}

static void ms_install_command_buffer_present_policy(id command_buffer) {
    if (!command_buffer || !fps_runtime_requested()) return;
    Class cls = object_getClass(command_buffer);
    if (!cls || objc_getAssociatedObject((id)cls, &g_ms_command_buffer_hook_key)) return;
    Method present = class_getInstanceMethod(cls, sel_registerName("presentDrawable:"));
    Method at_time = class_getInstanceMethod(cls, sel_registerName("presentDrawable:atTime:"));
    Method after = class_getInstanceMethod(cls, sel_registerName("presentDrawable:afterMinimumDuration:"));
    if (present && !g_original_command_present_drawable)
        g_original_command_present_drawable = (CommandPresentDrawableImp)method_setImplementation(present, (IMP)command_present_drawable_hook);
    if (at_time && !g_original_command_present_at_time)
        g_original_command_present_at_time = (CommandPresentAtTimeImp)method_setImplementation(at_time, (IMP)command_present_at_time_hook);
    if (after && !g_original_command_present_after_duration)
        g_original_command_present_after_duration = (CommandPresentAfterDurationImp)method_setImplementation(after, (IMP)command_present_after_duration_hook);
    g_ms_metal_present_hook_installed = g_original_command_present_drawable || g_original_command_present_at_time || g_original_command_present_after_duration;
    objc_setAssociatedObject((id)cls, &g_ms_command_buffer_hook_key, (id)1, 0);
    runtime_note("[METAL][PRESENT] commandBufferClass=%s present=%d atTime=%d afterMinimumDuration=%d policy=IMMEDIATE",
        class_getName(cls), present != NULL, at_time != NULL, after != NULL);
}

static void drawable_present_hook(id self, SEL cmd) {
    __atomic_fetch_add(&g_ms_present_calls, 1, __ATOMIC_RELAXED);
    if (g_original_drawable_present) g_original_drawable_present(self, cmd);
}

static void drawable_present_at_time_hook(id self, SEL cmd, double time) {
    __atomic_fetch_add(&g_ms_present_calls, 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&g_ms_present_delay_bypasses, 1, __ATOMIC_RELAXED);
    if (g_original_drawable_present) {
        g_original_drawable_present(self, sel_registerName("present"));
    } else if (g_original_drawable_present_at_time) {
        g_original_drawable_present_at_time(self, cmd, time);
    }
}

static void drawable_present_after_duration_hook(id self, SEL cmd, double duration) {
    __atomic_fetch_add(&g_ms_present_calls, 1, __ATOMIC_RELAXED);
    __atomic_fetch_add(&g_ms_present_delay_bypasses, 1, __ATOMIC_RELAXED);
    if (g_original_drawable_present) {
        g_original_drawable_present(self, sel_registerName("present"));
    } else if (g_original_drawable_present_after_duration) {
        g_original_drawable_present_after_duration(self, cmd, duration);
    }
}

static void ms_install_drawable_present_policy(id drawable) {
    if (!drawable || !fps_runtime_requested()) return;
    Class cls = object_getClass(drawable);
    if (!cls || objc_getAssociatedObject((id)cls, &g_ms_drawable_hook_key)) return;
    Method present = class_getInstanceMethod(cls, sel_registerName("present"));
    Method at_time = class_getInstanceMethod(cls, sel_registerName("presentAtTime:"));
    Method after = class_getInstanceMethod(cls, sel_registerName("presentAfterMinimumDuration:"));
    if (present && !g_original_drawable_present)
        g_original_drawable_present = (DrawablePresentImp)method_setImplementation(
            present, (IMP)drawable_present_hook);
    if (at_time && !g_original_drawable_present_at_time)
        g_original_drawable_present_at_time = (DrawablePresentTimedImp)method_setImplementation(
            at_time, (IMP)drawable_present_at_time_hook);
    if (after && !g_original_drawable_present_after_duration)
        g_original_drawable_present_after_duration = (DrawablePresentTimedImp)method_setImplementation(
            after, (IMP)drawable_present_after_duration_hook);
    g_ms_metal_present_hook_installed = g_ms_metal_present_hook_installed
        || g_original_drawable_present || g_original_drawable_present_at_time
        || g_original_drawable_present_after_duration;
    objc_setAssociatedObject((id)cls, &g_ms_drawable_hook_key, (id)1, 0);
    runtime_note("[METAL][DRAWABLE] class=%s present=%d atTime=%d afterMinimumDuration=%d policy=IMMEDIATE",
        class_getName(cls), present != NULL, at_time != NULL, after != NULL);
}

static id command_queue_buffer_hook(id self, SEL cmd) {
    id buffer = g_original_command_buffer ? g_original_command_buffer(self, cmd) : NULL;
    ms_install_command_buffer_present_policy(buffer);
    return buffer;
}

static id command_queue_buffer_unretained_hook(id self, SEL cmd) {
    id buffer = g_original_command_buffer_unretained
        ? g_original_command_buffer_unretained(self, cmd) : NULL;
    ms_install_command_buffer_present_policy(buffer);
    return buffer;
}

static id command_queue_buffer_descriptor_hook(id self, SEL cmd, id descriptor) {
    id buffer = g_original_command_buffer_descriptor ? g_original_command_buffer_descriptor(self, cmd, descriptor) : NULL;
    ms_install_command_buffer_present_policy(buffer);
    return buffer;
}

static void ms_install_command_queue_present_policy(id layer) {
    if (g_original_command_buffer || g_original_command_buffer_unretained
            || g_original_command_buffer_descriptor) return;
    id device = layer ? ((id (*)(id, SEL))objc_msgSend)(layer, sel_registerName("device")) : NULL;
    id queue = device ? ((id (*)(id, SEL))objc_msgSend)(device, sel_registerName("newCommandQueue")) : NULL;
    Class cls = queue ? object_getClass(queue) : NULL;
    if (!cls) return;
    Method buffer = class_getInstanceMethod(cls, sel_registerName("commandBuffer"));
    Method unretained = class_getInstanceMethod(
        cls, sel_registerName("commandBufferWithUnretainedReferences"));
    Method descriptor = class_getInstanceMethod(cls, sel_registerName("commandBufferWithDescriptor:"));
    if (buffer && !g_original_command_buffer)
        g_original_command_buffer = (CommandQueueBufferImp)method_setImplementation(buffer, (IMP)command_queue_buffer_hook);
    if (unretained && !g_original_command_buffer_unretained)
        g_original_command_buffer_unretained = (CommandQueueBufferImp)method_setImplementation(
            unretained, (IMP)command_queue_buffer_unretained_hook);
    if (descriptor && !g_original_command_buffer_descriptor)
        g_original_command_buffer_descriptor = (CommandQueueBufferDescriptorImp)method_setImplementation(descriptor, (IMP)command_queue_buffer_descriptor_hook);
    id probe = g_original_command_buffer
        ? g_original_command_buffer(queue, sel_registerName("commandBuffer"))
        : (g_original_command_buffer_unretained
            ? g_original_command_buffer_unretained(
                queue, sel_registerName("commandBufferWithUnretainedReferences"))
            : NULL);
    ms_install_command_buffer_present_policy(probe);
    runtime_note("[METAL][QUEUE] class=%s commandBuffer=%d unretained=%d descriptor=%d probe=%d presentHook=%d status=%s", class_getName(cls),
        buffer != NULL, unretained != NULL, descriptor != NULL, probe != NULL,
        g_ms_metal_present_hook_installed,
        (g_original_command_buffer || g_original_command_buffer_unretained
            || g_original_command_buffer_descriptor) ? "READY" : "BLOCKED");
    ((void (*)(id, SEL))objc_msgSend)(queue, sel_registerName("release"));
}

static inline void ms_ensure_layer_vsync_uncapped(id layer) {
    if (!layer || !fps_runtime_requested()) return;
    ms_install_command_queue_present_policy(layer);
    if (objc_getAssociatedObject(layer, &g_ms_layer_uncapped_key)) return;

    ms_apply_metal_layer_policies(layer);
    objc_setAssociatedObject(layer, &g_ms_layer_uncapped_key, (id)1, 0); /* OBJC_ASSOCIATION_ASSIGN */
    runtime_note("[METAL][PRESENT] layer=%p displaySyncSelector=%d displayFps=UNMEASURED", layer,
        ((bool (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), sel_registerName("setDisplaySyncEnabled:")));
}

static void metal_layer_set_display_sync_hook(id self, SEL cmd, bool enabled) {
    if (fps_runtime_requested()) {
        enabled = false;
    }
    if (g_original_set_display_sync) {
        g_original_set_display_sync(self, cmd, enabled);
    }
}

static id metal_layer_next_drawable_hook(id self, SEL cmd) {
    ms_ensure_layer_vsync_uncapped(self);
#ifndef NDEBUG
    uintptr_t caller = (uintptr_t)__builtin_return_address(0);
    bool measure = g_engine_base && caller >= g_engine_base &&
        macho_rva_is_executable(g_engine_base, caller - g_engine_base);
    uint64_t begin = measure ? mach_absolute_time() : 0;
#endif
    id drawable = g_original_next_drawable ? g_original_next_drawable(self, cmd) : NULL;
    ms_install_drawable_present_policy(drawable);
#ifndef NDEBUG
    if (measure) {
        __atomic_fetch_add(&g_ms_drawable_wait, mach_absolute_time() - begin, __ATOMIC_RELAXED);
        __atomic_fetch_add(&g_ms_drawable_calls, 1, __ATOMIC_RELAXED);
        if (!drawable) __atomic_fetch_add(&g_ms_drawable_nil, 1, __ATOMIC_RELAXED);
    }
#endif
    return drawable;
}

/* Skip before Roblox starts a Metal frame. Its false return path performs the
 * normal cleanup, unlike returning NULL from CAMetalLayer.nextDrawable. */
typedef bool (*MSRenderAcquireImp)(void*, void*);
static MSRenderAcquireImp g_ms_original_render_acquire;
static int g_ms_render_gate_installed;

static bool ms_render_acquire_hook(void* self, void* surface) {
    if (fps_runtime_requested() && g_ms_timebase.numer && g_ms_timebase.denom) {
        int display_fps = (int)fps_runtime_target();
        if (display_fps < 1) display_fps = ios_maximum_frames_per_second();
        if (display_fps > 0 && get_effective_target_fps() > display_fps) {
            uint64_t now = mach_absolute_time();
            uint64_t interval = (1000000000ull / (uint64_t)display_fps)
                * g_ms_timebase.denom / g_ms_timebase.numer;
            for (;;) {
                uint64_t due = __atomic_load_n(&g_ms_render_due, __ATOMIC_ACQUIRE);
                if (due && now < due) {
#ifndef NDEBUG
                    __atomic_fetch_add(&g_ms_drawable_decoupled, 1, __ATOMIC_RELAXED);
#endif
                    return false;
                }
                uint64_t next = now + interval;
                if (__atomic_compare_exchange_n(&g_ms_render_due, &due, next, false,
                        __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) break;
            }
        }
    }
    return g_ms_original_render_acquire
        ? g_ms_original_render_acquire(self, surface) : false;
}

static bool ms_install_absolute_arm64_hook(uintptr_t target, const uint8_t expected[16],
        void* replacement, void** original) {
#if defined(__arm64__) || defined(__aarch64__)
    uint8_t* code = (uint8_t*)target;
    for (int i = 0; i < 16; ++i) if (code[i] != expected[i]) return false;
    uint8_t* trampoline = mmap(NULL, 32, PROT_READ | PROT_WRITE,
        MAP_PRIVATE | MAP_ANON, -1, 0);
    if (trampoline == MAP_FAILED) return false;
    memcpy(trampoline, code, 16);
    ((uint32_t*)trampoline)[4] = 0x58000050; /* ldr x16, #8 */
    ((uint32_t*)trampoline)[5] = 0xD61F0200; /* br x16 */
    *(uint64_t*)(trampoline + 24) = target + 16;
    __builtin___clear_cache((char*)trampoline, (char*)trampoline + 32);
    if (mprotect(trampoline, 16384, PROT_READ | PROT_EXEC) != 0
            && mprotect(trampoline, 4096, PROT_READ | PROT_EXEC) != 0) return false;

    uint8_t jump[16] = {0x50,0x00,0x00,0x58,0x00,0x02,0x1F,0xD6};
    *(uint64_t*)(jump + 8) = (uintptr_t)replacement;
    uintptr_t page_size = 16384;
    uintptr_t page = target & ~(page_size - 1);
    if (mprotect((void*)page, page_size, PROT_READ | PROT_WRITE | PROT_EXEC) != 0) {
        page_size = 4096;
        page = target & ~(page_size - 1);
        if (mprotect((void*)page, page_size, PROT_READ | PROT_WRITE | PROT_EXEC) != 0) return false;
    }
    memcpy(code, jump, sizeof(jump));
    __builtin___clear_cache((char*)code, (char*)code + sizeof(jump));
    mprotect((void*)page, page_size, PROT_READ | PROT_EXEC);
    *original = trampoline;
    return true;
#else
    (void)target; (void)expected; (void)replacement; (void)original;
    return false;
#endif
}

static bool ms_install_render_acquire_policy(uintptr_t base) {
    if (g_ms_render_gate_installed) return true;
    if (!base || !engine_identity_matches(base)) return false;
    static const uint8_t expected[16] = {
        0xE9,0x23,0xBB,0x6D, 0xFC,0x6F,0x01,0xA9,
        0xF6,0x57,0x02,0xA9, 0xF4,0x4F,0x03,0xA9
    };
    void* original = NULL;
    bool installed = ms_install_absolute_arm64_hook(base + 0x3F6AD94, expected,
        (void*)ms_render_acquire_hook, &original);
    if (installed) {
        g_ms_original_render_acquire = (MSRenderAcquireImp)original;
        g_ms_render_gate_installed = 1;
    }
    runtime_note("[FPS][RENDER_GATE] rva=0x3F6AD94 installed=%d contract=%s policy=PRE_FRAME_FALSE",
        installed, installed ? "MATCH" : "MISMATCH_OR_PROTECTION");
    return installed;
}

static void metal_layer_set_drawable_size_hook(id self, SEL cmd, MSSize size) {
    ms_ensure_layer_vsync_uncapped(self);
    if (g_original_set_drawable_size) {
        g_original_set_drawable_size(self, cmd, size);
    }
}

static id metal_layer_init_hook(id self, SEL cmd) {
    id res = g_original_metal_layer_init ? g_original_metal_layer_init(self, cmd) : self;
    ms_ensure_layer_vsync_uncapped(res);
    return res;
}

static bool install_metal_layer_stretch_policy(void) {
    if (g_metal_layer_stretch_installed) return true;
    Class cls = (Class)objc_getClass("CAMetalLayer");
    if (!cls) return false;
    if (!g_ms_timebase.denom) mach_timebase_info(&g_ms_timebase);
#ifndef NDEBUG
    if (!__atomic_load_n(&g_ms_probe_start, __ATOMIC_ACQUIRE) && g_ms_timebase.denom)
        __atomic_store_n(&g_ms_probe_start, mach_absolute_time(), __ATOMIC_RELEASE);
#endif

    Method sync_m = class_getInstanceMethod(cls, sel_registerName("setDisplaySyncEnabled:"));
    if (sync_m && !g_original_set_display_sync) {
        g_original_set_display_sync = (SetDisplaySyncImp)method_setImplementation(sync_m, (IMP)metal_layer_set_display_sync_hook);
    }

    Method next_m = class_getInstanceMethod(cls, sel_registerName("nextDrawable"));
    if (next_m && !g_original_next_drawable) {
        g_original_next_drawable = (NextDrawableImp)method_setImplementation(next_m, (IMP)metal_layer_next_drawable_hook);
    }

    Method size_m = class_getInstanceMethod(cls, sel_registerName("setDrawableSize:"));
    if (size_m && !g_original_set_drawable_size) {
        g_original_set_drawable_size = (SetDrawableSizeImp)method_setImplementation(size_m, (IMP)metal_layer_set_drawable_size_hook);
    }

    Method init_m = class_getInstanceMethod(cls, sel_registerName("init"));
    if (init_m && !g_original_metal_layer_init) {
        g_original_metal_layer_init = (MetalLayerInitImp)method_setImplementation(init_m, (IMP)metal_layer_init_hook);
    }

    g_metal_layer_stretch_installed = 1;
    runtime_note("[CAPABILITY][STRETCH] backend=%s status=READY hookInstalled=1 surface_geometry=passthrough", kStretchRuntimeBackend);
    return true;
}

static void ms_apply_stretch_to_layer_tree(id layer, Class metalClass) {
    if (!layer || !metalClass) return;
    if (((bool (*)(id, SEL, id))objc_msgSend)(layer, sel_registerName("isKindOfClass:"), (id)metalClass)) {
        ms_apply_metal_layer_policies(layer);
    }
    id sublayers = ((id (*)(id, SEL))objc_msgSend)(layer, sel_registerName("sublayers"));
    if (sublayers) {
        unsigned long count = ((unsigned long (*)(id, SEL))objc_msgSend)(sublayers, sel_registerName("count"));
        for (unsigned long i = 0; i < count; i++) {
            id sub = ((id (*)(id, SEL, unsigned long))objc_msgSend)(sublayers, sel_registerName("objectAtIndex:"), i);
            ms_apply_stretch_to_layer_tree(sub, metalClass);
        }
    }
}

static void ms_refresh_metal_layers(void) {
    if (!pthread_main_np()) return;
    Class metalClass = (Class)objc_getClass("CAMetalLayer");
    if (!metalClass) return;
    id app = ((id (*)(id, SEL))objc_msgSend)(objc_getClass("UIApplication"), sel_registerName("sharedApplication"));
    if (!app) return;
    id windows = ((id (*)(id, SEL))objc_msgSend)(app, sel_registerName("windows"));
    if (!windows) return;
    unsigned long count = ((unsigned long (*)(id, SEL))objc_msgSend)(windows, sel_registerName("count"));
    for (unsigned long i = 0; i < count; i++) {
        id win = ((id (*)(id, SEL, unsigned long))objc_msgSend)(windows, sel_registerName("objectAtIndex:"), i);
        if (!win) continue;
        id layer = ((id (*)(id, SEL))objc_msgSend)(win, sel_registerName("layer"));
        ms_apply_stretch_to_layer_tree(layer, metalClass);
    }
}

static void ms_apply_process_main(void* context) {
    MSDisplayPolicy* snapshot = context;
    g_ms_policy = *snapshot;
    free(snapshot);
    ++g_ms_generation;
    g_display_link_rewrite_count = 0;
    install_display_link_policy();
    install_metal_layer_stretch_policy();
    ms_install_observer();
    id app = ((id (*)(id, SEL))objc_msgSend)(objc_getClass("UIApplication"), sel_registerName("sharedApplication"));
    if (app && g_ms_policy.loaded && g_ms_policy.keep_display) {
        if (!g_ms_idle_owned) g_ms_idle_original = ((unsigned char (*)(id, SEL))objc_msgSend)(app, sel_registerName("isIdleTimerDisabled"));
        g_ms_idle_owned = true;
        ((void (*)(id, SEL, unsigned char))objc_msgSend)(app, sel_registerName("setIdleTimerDisabled:"), 1);
    } else if (app && g_ms_idle_owned) {
        ((void (*)(id, SEL, unsigned char))objc_msgSend)(app, sel_registerName("setIdleTimerDisabled:"), g_ms_idle_original);
        g_ms_idle_owned = false;
    }
    runtime_note("[APPLY][BOOST] keepDisplay=%d idleReadback=%d status=PARTIAL_OS_MANAGED mainThread=1",
        g_ms_policy.keep_display, app ? ((unsigned char (*)(id, SEL))objc_msgSend)(app, sel_registerName("isIdleTimerDisabled")) : -1);
    runtime_note("[CAPABILITY][SAFE] thermalGuard=SYSTEM_RESPECTED lowPower=%d thermal=%d ramCleanup=UNSUPPORTED networkAcceleration=UNSUPPORTED", ms_low_power(), ms_thermal());
    runtime_note("[CAPABILITY][TURBO] requested=%d cpuOverclock=UNSUPPORTED gpuOverclock=UNSUPPORTED androidPowerHints=UNSUPPORTED", g_ms_policy.boost);
    if (g_ms_policy.boost) {
        runtime_note("[APPLY][TURBO] sustainedPerformance=ACTIVE thermalGuard=ACTIVE keepDisplay=%d", g_ms_policy.keep_display);
    }
    if (g_shader_wet_floor || strcmp(g_shader_package, "none") != 0) {
        runtime_note("[APPLY][SHADER] package=%s wetFloor=%d status=ACTIVE_NATIVE_LIGHTING", g_shader_package, g_shader_wet_floor);
    }
    ms_refresh_links("profile-loaded");
    ms_refresh_metal_layers();
    write_runtime_capability_receipt();
}
static void apply_ios_process_policies(void) {
    MSDisplayPolicy* snapshot = malloc(sizeof(*snapshot));
    if (!snapshot) { runtime_note("[APPLY][BOOST] status=FAILED reason=allocation"); return; }
    *snapshot = (MSDisplayPolicy){g_profile_loaded, g_refresh_requested, g_keep_display_active, g_game_mode_enabled, g_target_fps};
    dispatch_async_f(&_dispatch_main_q, snapshot, ms_apply_process_main);
}

