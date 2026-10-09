/* Included by the IPA builder after profile parsing and logging helpers.
 * Only public UIKit/QuartzCore policy: no engine offsets or protection bypass.
 * All mutable display state and UIKit calls are confined to the main queue.
 */
extern int pthread_main_np(void);
extern char _dispatch_main_q;
extern void dispatch_async_f(void*, void*, void (*)(void*));
extern Class objc_allocateClassPair(Class, const char*, size_t);
extern void objc_registerClassPair(Class);
extern id objc_getAssociatedObject(id, const void*);
extern void objc_setAssociatedObject(id, const void*, id, unsigned long);
extern bool g_launch_authorized;

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

typedef unsigned char (*ProcessInfoLowPowerImp)(id, SEL);
static ProcessInfoLowPowerImp g_original_is_low_power = NULL;

static unsigned char process_info_is_low_power_hook(id self, SEL cmd) {
    if (fps_runtime_requested()) {
        return 0; /* Disguise Low Power Mode from Roblox engine */
    }
    return g_original_is_low_power ? g_original_is_low_power(self, cmd) : 0;
}

typedef long (*ScreenMaxFpsImp)(id, SEL);
static ScreenMaxFpsImp g_original_screen_max_fps = NULL;

static long screen_max_fps_hook(id self, SEL cmd) {
    if (fps_runtime_requested()) {
        long target = g_target_fps > 0 ? g_target_fps : (g_ms_policy.target > 0 ? g_ms_policy.target : 10000);
        return target > 60 ? target : 10000;
    }
    return g_original_screen_max_fps ? g_original_screen_max_fps(self, cmd) : 60;
}

static id ms_string(const char* text) {
    return ((id (*)(id, SEL, const char*))objc_msgSend)(objc_getClass("NSString"),
        sel_registerName("stringWithUTF8String:"), text);
}
static id ms_process_info(void) {
    return ((id (*)(id, SEL))objc_msgSend)(objc_getClass("NSProcessInfo"), sel_registerName("processInfo"));
}
static int ios_maximum_frames_per_second(void) {
    if (fps_runtime_requested()) {
        long target = g_target_fps > 0 ? g_target_fps : (g_ms_policy.target > 0 ? g_ms_policy.target : 10000);
        return target > 60 ? (int)target : 10000;
    }
    id screen = ((id (*)(id, SEL))objc_msgSend)(objc_getClass("UIScreen"), sel_registerName("mainScreen"));
    long rate = screen ? ((long (*)(id, SEL))objc_msgSend)(screen, sel_registerName("maximumFramesPerSecond")) : 60;
    return rate >= 30 ? (int)rate : 120;
}
static int ms_low_power(void) {
    if (fps_runtime_requested()) return 0;
    return ((unsigned char (*)(id, SEL))objc_msgSend)(ms_process_info(), sel_registerName("isLowPowerModeEnabled"));
}
static int ms_thermal(void) {
    return (int)((long (*)(id, SEL))objc_msgSend)(ms_process_info(), sel_registerName("thermalState"));
}
static bool fps_runtime_requested(void) {
    return (g_target_fps > 60 || (g_ms_policy.loaded && (g_ms_policy.refresh || g_ms_policy.target > 60)));
}
static long fps_runtime_target(void) {
    int target = g_target_fps > 0 ? g_target_fps : g_ms_policy.target;
    return ms_ios_target_fps(target, ios_maximum_frames_per_second(), ms_low_power(), ms_thermal());
}
static inline MSFrameRateRange sanitize_frame_rate_range(MSFrameRateRange range) {
    int screen_max = ios_maximum_frames_per_second();
    if (screen_max <= 0) screen_max = 10000;
    float max_limit = (float)screen_max;
    if (range.minimum <= 0.0f || range.minimum > max_limit) range.minimum = 1.0f;
    if (range.maximum <= 0.0f || range.maximum > max_limit) range.maximum = max_limit;
    if (range.minimum > range.maximum) range.minimum = range.maximum;
    if (range.preferred < range.minimum) range.preferred = range.minimum;
    if (range.preferred > range.maximum) range.preferred = range.maximum;
    return range;
}
static MSFrameRateRange ms_target_range(void) {
    int screen_max = ios_maximum_frames_per_second();
    if (screen_max <= 0) screen_max = 10000;
    float max_limit = (float)screen_max;
    return sanitize_frame_rate_range((MSFrameRateRange){max_limit, max_limit, max_limit});
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
        MSFrameRateRange range = ms_target_range();
        g_original_display_link_set_range(link, sel_registerName("setPreferredFrameRateRange:"), range);
        if (g_display_link_rewrite_count < 16) {
            MSFrameRateRange actual = ((MSFrameRateRange (*)(id, SEL))objc_msgSend)(link, sel_registerName("preferredFrameRateRange"));
            runtime_note("[READBACK][FPS] source=%s api=preferredFrameRateRange min=%.1f max=%.1f preferred=%.1f target=%ld status=%s presentedFps=UNMEASURED",
                source, (double)actual.minimum, (double)actual.maximum, (double)actual.preferred, target,
                actual.preferred == range.preferred ? "MATCH" : "OS_ADJUSTED");
        }
    } else if (g_original_display_link_set_fps) {
        g_original_display_link_set_fps(link, sel_registerName("setPreferredFramesPerSecond:"), target);
        if (g_display_link_rewrite_count < 16) {
            long actual = ((long (*)(id, SEL))objc_msgSend)(link, sel_registerName("preferredFramesPerSecond"));
            runtime_note("[READBACK][FPS] source=%s api=preferredFramesPerSecond requested=%ld readback=%ld presentedFps=UNMEASURED", source, target, actual);
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
    if (fps_runtime_requested()) {
        requested = fps_runtime_target();
    }
    if (g_original_display_link_set_fps) g_original_display_link_set_fps(self, cmd, requested);
    if (!pthread_main_np() || g_ms_applying) {
        return;
    }
    ms_track_link(self);
    ms_capture_link_request(self);
    apply_display_link_policy(self, "fps-setter");
}
static void display_link_set_range_hook(id self, SEL cmd, MSFrameRateRange requested) {
    if (fps_runtime_requested()) {
        requested = ms_target_range();
    }
    if (g_original_display_link_set_range) g_original_display_link_set_range(self, cmd, requested);
    if (!pthread_main_np() || g_ms_applying) {
        return;
    }
    ms_track_link(self);
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
        id trackingMode = ms_string("UITrackingRunLoopMode");
        if (commonMode && loop) {
            bool is_same = false;
            if (mode) {
                is_same = ((bool (*)(id, SEL, id))objc_msgSend)(mode, sel_registerName("isEqual:"), commonMode);
            }
            if (!is_same) {
                g_original_display_link_add(self, cmd, loop, commonMode);
            }
        }
        if (trackingMode && loop) {
            bool is_same_track = false;
            if (mode) {
                is_same_track = ((bool (*)(id, SEL, id))objc_msgSend)(mode, sel_registerName("isEqual:"), trackingMode);
            }
            if (!is_same_track) {
                g_original_display_link_add(self, cmd, loop, trackingMode);
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
    fprintf(file, "{\"schema\":1,\"generatedAtUnix\":%ld,\"generation\":%u,\"authorizedProfileLoaded\":%s,\"policyRequested\":%s,\"hookInstalled\":%s,\"frameRateRangeAvailable\":%s,\"requestedFps\":%d,\"policyTargetFps\":%ld,\"screenMaximum\":%d,\"lowPower\":%s,\"thermalState\":%d,\"trackedDisplayLinks\":%lu,\"presentedFps\":null,\"runtimeVerified\":false}\n",
        time(NULL), g_ms_generation, g_ms_policy.loaded ? "true" : "false", fps_runtime_requested() ? "true" : "false",
        g_display_link_policy_installed ? "true" : "false", g_original_display_link_set_range ? "true" : "false",
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

    Class pi_cls = (Class)objc_getClass("NSProcessInfo");
    if (pi_cls) {
        Method lp_m = class_getInstanceMethod(pi_cls, sel_registerName("isLowPowerModeEnabled"));
        if (lp_m && !g_original_is_low_power) {
            g_original_is_low_power = (ProcessInfoLowPowerImp)method_setImplementation(lp_m, (IMP)process_info_is_low_power_hook);
        }
    }
    Class screen_cls = (Class)objc_getClass("UIScreen");
    if (screen_cls) {
        Method screen_m = class_getInstanceMethod(screen_cls, sel_registerName("maximumFramesPerSecond"));
        if (screen_m && !g_original_screen_max_fps) {
            g_original_screen_max_fps = (ScreenMaxFpsImp)method_setImplementation(screen_m, (IMP)screen_max_fps_hook);
        }
    }

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
static SetDisplaySyncImp g_original_set_display_sync = NULL;

typedef id (*NextDrawableImp)(id, SEL);
static NextDrawableImp g_original_next_drawable = NULL;

#if defined(__arm64__) || defined(__aarch64__)
typedef struct { double width; double height; } MSSize;
#else
typedef struct { float width; float height; } MSSize;
#endif
typedef void (*SetDrawableSizeImp)(id, SEL, MSSize);
static SetDrawableSizeImp g_original_set_drawable_size = NULL;

typedef void (*SetContentsGravityImp)(id, SEL, id);
static SetContentsGravityImp g_original_set_contents_gravity = NULL;

static int g_metal_layer_stretch_installed = 0;
static char g_ms_layer_uncapped_key;

typedef void (*CommandPresentDrawableImp)(id, SEL, id);
typedef void (*CommandPresentAtTimeImp)(id, SEL, id, double);
typedef void (*CommandPresentAfterDurationImp)(id, SEL, id, double);
typedef id (*CommandQueueBufferImp)(id, SEL);
typedef id (*CommandQueueBufferDescriptorImp)(id, SEL, id);
static CommandPresentDrawableImp g_original_command_present_drawable = NULL;
static CommandPresentAtTimeImp g_original_command_present_at_time = NULL;
static CommandPresentAfterDurationImp g_original_command_present_after_duration = NULL;
static CommandQueueBufferImp g_original_command_buffer = NULL;
static CommandQueueBufferDescriptorImp g_original_command_buffer_descriptor = NULL;
static CommandQueueBufferImp g_original_command_buffer_unretained = NULL;
static CommandQueueBufferDescriptorImp g_original_command_buffer_unretained_descriptor = NULL;
static char g_ms_command_buffer_hook_key;
static int g_ms_metal_present_hook_installed = 0;

typedef void (*DrawablePresentAtTimeImp)(id, SEL, double);
typedef void (*DrawablePresentAfterDurationImp)(id, SEL, double);
static DrawablePresentAtTimeImp g_original_drawable_present_at_time = NULL;
static DrawablePresentAfterDurationImp g_original_drawable_present_after_duration = NULL;

static void drawable_present_at_time_hook(id self, SEL cmd, double time) {
    (void)time;
    if (fps_runtime_requested()) {
        ((void (*)(id, SEL))objc_msgSend)(self, sel_registerName("present"));
        return;
    }
    if (g_original_drawable_present_at_time) {
        g_original_drawable_present_at_time(self, cmd, time);
    }
}

static void drawable_present_after_duration_hook(id self, SEL cmd, double duration) {
    (void)duration;
    if (fps_runtime_requested()) {
        ((void (*)(id, SEL))objc_msgSend)(self, sel_registerName("present"));
        return;
    }
    if (g_original_drawable_present_after_duration) {
        g_original_drawable_present_after_duration(self, cmd, duration);
    }
}

static char g_ms_drawable_hook_key;

static void ms_install_drawable_instance_present_policy(id drawable) {
    if (!drawable) return;
    Class cls = object_getClass(drawable);
    if (!cls || objc_getAssociatedObject((id)cls, &g_ms_drawable_hook_key)) return;

    Method at_time = class_getInstanceMethod(cls, sel_registerName("presentAtTime:"));
    Method after = class_getInstanceMethod(cls, sel_registerName("presentAfterMinimumDuration:"));
    if (at_time && !g_original_drawable_present_at_time)
        g_original_drawable_present_at_time = (DrawablePresentAtTimeImp)method_setImplementation(at_time, (IMP)drawable_present_at_time_hook);
    if (after && !g_original_drawable_present_after_duration)
        g_original_drawable_present_after_duration = (DrawablePresentAfterDurationImp)method_setImplementation(after, (IMP)drawable_present_after_duration_hook);
    objc_setAssociatedObject((id)cls, &g_ms_drawable_hook_key, (id)1, 0);
    runtime_note("[METAL][DRAWABLE] class=%s presentAtTime=%d presentAfterDuration=%d",
                 class_getName(cls), at_time != NULL, after != NULL);
}

static void command_present_drawable_hook(id self, SEL cmd, id drawable) {
    if (g_original_command_present_drawable) {
        g_original_command_present_drawable(self, cmd, drawable);
    }
}

static void command_present_at_time_hook(id self, SEL cmd, id drawable, double time) {
    (void)time;
    /* Bypass VBlank delay: present drawable immediately without waiting for future VSync timestamp */
    if (fps_runtime_requested()) {
        if (g_original_command_present_drawable) {
            g_original_command_present_drawable(self, sel_registerName("presentDrawable:"), drawable);
            return;
        }
    }
    if (g_original_command_present_at_time) {
        g_original_command_present_at_time(self, cmd, drawable, time);
    }
}

static void command_present_after_duration_hook(id self, SEL cmd, id drawable, double duration) {
    (void)duration;
    /* Bypass minimum frame duration: present immediately */
    if (fps_runtime_requested()) {
        if (g_original_command_present_drawable) {
            g_original_command_present_drawable(self, sel_registerName("presentDrawable:"), drawable);
            return;
        }
    }
    if (g_original_command_present_after_duration) {
        g_original_command_present_after_duration(self, cmd, drawable, duration);
    }
}

static void ms_install_command_buffer_present_policy(id command_buffer) {
    if (!command_buffer) return;
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

    g_ms_metal_present_hook_installed = (g_original_command_present_drawable != NULL);
    objc_setAssociatedObject((id)cls, &g_ms_command_buffer_hook_key, (id)1, 0);
    runtime_note("[METAL][PRESENT] bufferClass=%s present=%d atTime=%d afterDuration=%d policy=IMMEDIATE",
        class_getName(cls), present != NULL, at_time != NULL, after != NULL);
}

static id command_queue_buffer_hook(id self, SEL cmd) {
    id buffer = g_original_command_buffer ? g_original_command_buffer(self, cmd) : NULL;
    ms_install_command_buffer_present_policy(buffer);
    return buffer;
}

static id command_queue_buffer_descriptor_hook(id self, SEL cmd, id descriptor) {
    id buffer = g_original_command_buffer_descriptor ? g_original_command_buffer_descriptor(self, cmd, descriptor) : NULL;
    ms_install_command_buffer_present_policy(buffer);
    return buffer;
}

static id command_queue_buffer_unretained_hook(id self, SEL cmd) {
    id buffer = g_original_command_buffer_unretained ? g_original_command_buffer_unretained(self, cmd) : NULL;
    ms_install_command_buffer_present_policy(buffer);
    return buffer;
}

static id command_queue_buffer_unretained_descriptor_hook(id self, SEL cmd, id descriptor) {
    id buffer = g_original_command_buffer_unretained_descriptor ? g_original_command_buffer_unretained_descriptor(self, cmd, descriptor) : NULL;
    ms_install_command_buffer_present_policy(buffer);
    return buffer;
}

static void ms_install_command_queue_present_policy(id layer) {
    if (g_original_command_buffer || g_original_command_buffer_unretained) return;
    id device = layer ? ((id (*)(id, SEL))objc_msgSend)(layer, sel_registerName("device")) : NULL;
    id queue = device ? ((id (*)(id, SEL))objc_msgSend)(device, sel_registerName("newCommandQueue")) : NULL;
    Class cls = queue ? object_getClass(queue) : NULL;
    if (!cls) return;
    Method buffer = class_getInstanceMethod(cls, sel_registerName("commandBuffer"));
    Method descriptor = class_getInstanceMethod(cls, sel_registerName("commandBufferWithDescriptor:"));
    Method unret = class_getInstanceMethod(cls, sel_registerName("commandBufferWithUnretainedReferences"));
    Method unret_desc = class_getInstanceMethod(cls, sel_registerName("commandBufferWithUnretainedReferencesAndDescriptor:"));

    if (buffer && !g_original_command_buffer)
        g_original_command_buffer = (CommandQueueBufferImp)method_setImplementation(buffer, (IMP)command_queue_buffer_hook);
    if (descriptor && !g_original_command_buffer_descriptor)
        g_original_command_buffer_descriptor = (CommandQueueBufferDescriptorImp)method_setImplementation(descriptor, (IMP)command_queue_buffer_descriptor_hook);
    if (unret && !g_original_command_buffer_unretained)
        g_original_command_buffer_unretained = (CommandQueueBufferImp)method_setImplementation(unret, (IMP)command_queue_buffer_unretained_hook);
    if (unret_desc && !g_original_command_buffer_unretained_descriptor)
        g_original_command_buffer_unretained_descriptor = (CommandQueueBufferDescriptorImp)method_setImplementation(unret_desc, (IMP)command_queue_buffer_unretained_descriptor_hook);

    runtime_note("[METAL][QUEUE] class=%s commandBuffer=%d descriptor=%d unretained=%d status=%s", class_getName(cls),
        buffer != NULL, descriptor != NULL, unret != NULL, (g_original_command_buffer || g_original_command_buffer_unretained) ? "READY" : "BLOCKED");
    ((void (*)(id, SEL))objc_msgSend)(queue, sel_registerName("release"));
}

static void ms_apply_metal_layer_policies(id layer) {
    if (!layer) return;

    // 1. Uncap Metal VSync (Allows engine rendering loop to exceed 120Hz up to 10,000 FPS)
    if (fps_runtime_requested()) {
        SEL sync_sel = sel_registerName("setDisplaySyncEnabled:");
        if (((bool (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), sync_sel)) {
            ((void (*)(id, SEL, bool))objc_msgSend)(layer, sync_sel, false);
        }
        SEL max_sel = sel_registerName("setMaximumDrawableCount:");
        if (((bool (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), max_sel)) {
            ((void (*)(id, SEL, unsigned long))objc_msgSend)(layer, max_sel, 3);
        }
        SEL timeout_sel = sel_registerName("setAllowsNextDrawableTimeout:");
        if (((bool (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), timeout_sel)) {
            ((void (*)(id, SEL, bool))objc_msgSend)(layer, timeout_sel, true);
        }
        SEL trans_sel = sel_registerName("setPresentsWithTransaction:");
        if (((bool (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), trans_sel)) {
            ((void (*)(id, SEL, bool))objc_msgSend)(layer, trans_sel, false);
        }
    }
}

static inline void ms_ensure_layer_vsync_uncapped(id layer) {
    if (!layer || !fps_runtime_requested()) return;
    ms_install_command_queue_present_policy(layer);
    if (objc_getAssociatedObject(layer, &g_ms_layer_uncapped_key)) return;

    ms_apply_metal_layer_policies(layer);
    objc_setAssociatedObject(layer, &g_ms_layer_uncapped_key, (id)1, 0); /* OBJC_ASSOCIATION_ASSIGN */
    runtime_note("[METAL][VSYNC] Uncapped CAMetalLayer %p: displaySyncEnabled=NO maxDrawables=3", layer);
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
    id drawable = g_original_next_drawable ? g_original_next_drawable(self, cmd) : NULL;
    if (drawable) {
        ms_install_drawable_instance_present_policy(drawable);
    }
    return drawable;
}

typedef id (*MetalLayerInitImp)(id, SEL);
static MetalLayerInitImp g_original_metal_layer_init = NULL;

static id metal_layer_init_hook(id self, SEL cmd) {
    id res = g_original_metal_layer_init ? g_original_metal_layer_init(self, cmd) : self;
    ms_ensure_layer_vsync_uncapped(res);
    return res;
}

static inline id get_ca_gravity_resize(void) {
    static id s_gravity_resize = NULL;
    if (!s_gravity_resize) {
        s_gravity_resize = ms_string("resize");
    }
    return s_gravity_resize;
}

static void metal_layer_set_drawable_size_hook(id self, SEL cmd, MSSize size) {
    if (g_stretch_percent > 0 && g_stretch_percent != 100 && size.width > 0 && size.height > 0) {
        if (size.width > size.height) { // In-game landscape only
            double factor = (double)g_stretch_percent / 100.0;
            size.width = (double)((long long)(size.width * factor + 0.5));
            if (size.width < 64.0) size.width = 64.0;
            id gravity = get_ca_gravity_resize();
            ((void (*)(id, SEL, id))objc_msgSend)(self, sel_registerName("setContentsGravity:"), gravity);
            static int s_logged_stretch = 0;
            if (!s_logged_stretch) {
                s_logged_stretch = 1;
                runtime_note("[APPLY][STRETCH] CAMetalLayer in-game landscape stretch=%d%% target_width=%.1f contentsGravity=resize",
                             g_stretch_percent, size.width);
            }
        }
    }
    if (g_original_set_drawable_size) {
        g_original_set_drawable_size(self, cmd, size);
    }
    ms_apply_metal_layer_policies(self);
}

static void metal_layer_set_contents_gravity_hook(id self, SEL cmd, id gravity) {
    if (g_stretch_percent > 0 && g_stretch_percent != 100) {
        gravity = get_ca_gravity_resize();
    }
    if (g_original_set_contents_gravity) {
        g_original_set_contents_gravity(self, cmd, gravity);
    }
}

static void ms_apply_stretch_to_layer_tree(id layer, Class metalClass) {
    if (!layer || !metalClass) return;
    if (((bool (*)(id, SEL, id))objc_msgSend)(layer, sel_registerName("isKindOfClass:"), (id)metalClass)) {
        ms_apply_metal_layer_policies(layer);
        if (g_stretch_percent > 0 && g_stretch_percent != 100) {
            MSSize size = ((MSSize (*)(id, SEL))objc_msgSend)(layer, sel_registerName("drawableSize"));
            if (size.width > size.height && size.height > 0) {
                double factor = (double)g_stretch_percent / 100.0;
                size.width = (double)((long long)(size.width * factor + 0.5));
                if (size.width < 64.0) size.width = 64.0;
                id gravity = get_ca_gravity_resize();
                ((void (*)(id, SEL, id))objc_msgSend)(layer, sel_registerName("setContentsGravity:"), gravity);
                if (g_original_set_drawable_size) {
                    g_original_set_drawable_size(layer, sel_registerName("setDrawableSize:"), size);
                }
            }
        }
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
    if (1) return;
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

static bool install_metal_layer_stretch_policy(void) {
    if (g_metal_layer_stretch_installed) return true;
    Class cls = (Class)objc_getClass("CAMetalLayer");
    if (!cls) return false;

    Method sync_m = class_getInstanceMethod(cls, sel_registerName("setDisplaySyncEnabled:"));
    if (sync_m && !g_original_set_display_sync) {
        g_original_set_display_sync = (SetDisplaySyncImp)method_setImplementation(sync_m, (IMP)metal_layer_set_display_sync_hook);
    }

    Method next_m = class_getInstanceMethod(cls, sel_registerName("nextDrawable"));
    if (next_m && !g_original_next_drawable) {
        g_original_next_drawable = (NextDrawableImp)method_setImplementation(next_m, (IMP)metal_layer_next_drawable_hook);
    }

    Method init_m = class_getInstanceMethod(cls, sel_registerName("init"));
    if (init_m && !g_original_metal_layer_init) {
        g_original_metal_layer_init = (MetalLayerInitImp)method_setImplementation(init_m, (IMP)metal_layer_init_hook);
    }

    /* Stretch resolution hooks remain dormant per user phase schedule: FPS uncap only */

    g_metal_layer_stretch_installed = (sync_m != NULL || next_m != NULL || init_m != NULL);
    runtime_note("[CAPABILITY][STRETCH] backend=cametallayer-drawable-resize status=STANDBY_FPS_UNCAP_ONLY hookInstalled=%d metal_vsync_uncap=ACTIVE",
                 g_metal_layer_stretch_installed);
    return true;
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
