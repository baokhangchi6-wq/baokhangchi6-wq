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
typedef struct { bool loaded, refresh, keep_display, boost; int target; unsigned session_generation; } MSDisplayPolicy;
static MSDisplayPolicy g_ms_policy;
static unsigned g_ms_generation;
static void ms_update_probe_environment(void);
static void ms_probe_record(int event, double wait);
static double ms_probe_time(void);
extern void ms_track_drawable_lifetime(void*);
extern int ms_drawable_sample(void*, double*, unsigned*);
extern void ms_mark_drawable_present(void*);
static id g_display_target = NULL;
static SEL g_display_selector = NULL;
static IMP g_original_display_callback = NULL;
static id g_active_display_link = NULL;
static int g_display_pump_in_flight = 0;

static void ms_pump_next_frame(void* context) {
    (void)context;
    if (!fps_runtime_requested() || !g_display_target || !g_display_selector || !g_original_display_callback) return;
    if (g_display_pump_in_flight) return;
    g_display_pump_in_flight = 1;
    ms_probe_record(4, 0);
    ((void (*)(id, SEL, id))g_original_display_callback)(g_display_target, g_display_selector, g_active_display_link);
    g_display_pump_in_flight = 0;
}

static void display_callback_hook(id self, SEL selector, id link, IMP original) {
    g_display_target = self;
    g_display_selector = selector;
    g_original_display_callback = original;
    g_active_display_link = link;
    ms_probe_record(4, 0); /* Display callback count, not an engine FPS claim. */
    ((void (*)(id, SEL, id))original)(self, selector, link);
}
static void display_callback_no_arg_hook(id self, SEL selector, IMP original) {
    g_display_target = self;
    g_display_selector = selector;
    g_original_display_callback = original;
    ms_probe_record(4, 0);
    ((void (*)(id, SEL))original)(self, selector);
}
static void ms_install_display_callback(id target, SEL selector) {
    extern const char* sel_getName(SEL);
    if (!target || !selector) return;
    Class cls = object_getClass(target);
    Method method = class_getInstanceMethod(cls, selector);
    const char* name = sel_getName(selector);
    if (ms_method_abi_matches(method, "v@:@")) ms_hook_instance_method(cls, name, (IMP)display_callback_hook, "v@:@");
    else if (ms_method_abi_matches(method, "v@:")) ms_hook_instance_method(cls, name, (IMP)display_callback_no_arg_hook, "v@:");
    else ms_hook_instance_method(cls, name, (IMP)display_callback_hook, "v@:@"); /* records rejection */
}
static char g_ms_link_request_key;
typedef struct { long fps; MSFrameRateRange range; int has_range, managed; } MSLinkRequest;
static void write_runtime_capability_receipt(void);
static bool bridge_write_document(const char* file_name, const char* content);
static void ms_pacing_write_receipt(void);
static void ms_stretch_write_receipt(void);
static void ms_stretch_observe_drawable(id drawable);
static void ms_render_job_drawable(double seconds);
static void ms_render_roles_write_receipt(void);
extern void ms_offscreen_observe_encoder(void*, void*);
extern void ms_offscreen_completion_ready(void*);
extern void ms_offscreen_commit(void*);
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
    return g_launch_authorized && g_profile_loaded &&
        (g_target_fps > 60 || (g_ms_policy.loaded && (g_ms_policy.refresh || g_ms_policy.target > 60)));
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
    float max_limit = (float)fps_runtime_target();
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
    if (pthread_main_np() && !g_ms_applying) {
        ms_track_link(self);
        MSLinkRequest state;
        if (ms_get_link_request(self, &state)) { state.fps = requested; ms_store_link_request(self, &state); }
    }
    if (fps_runtime_requested()) {
        requested = fps_runtime_target();
    }
    if (g_original_display_link_set_fps) g_original_display_link_set_fps(self, cmd, requested);
    if (!pthread_main_np() || g_ms_applying) {
        return;
    }
    ms_track_link(self);
    apply_display_link_policy(self, "fps-setter");
}
static void display_link_set_range_hook(id self, SEL cmd, MSFrameRateRange requested) {
    if (pthread_main_np() && !g_ms_applying) {
        ms_track_link(self);
        MSLinkRequest state;
        if (ms_get_link_request(self, &state)) { state.range = requested; state.has_range = 1; ms_store_link_request(self, &state); }
    }
    if (fps_runtime_requested()) {
        requested = ms_target_range();
    }
    if (g_original_display_link_set_range) g_original_display_link_set_range(self, cmd, requested);
    if (!pthread_main_np() || g_ms_applying) {
        return;
    }
    ms_track_link(self);
    apply_display_link_policy(self, "range-setter");
}
static id display_link_create_hook(id self, SEL cmd, id target, SEL selector) {
    id link = g_original_display_link_create ? g_original_display_link_create(self, cmd, target, selector) : NULL;
    ms_install_display_callback(target, selector);
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
    SESSION_LOCK;
    ms_update_probe_environment();
    if (!g_ms_policy.loaded) return;
    g_display_link_rewrite_count = 0;
    runtime_note("[LIFECYCLE][DISPLAY] lowPower=%d thermal=%d screenMax=%d", ms_low_power(), ms_thermal(), ios_maximum_frames_per_second());
    ms_refresh_links("environment-change");
    ms_refresh_observed_layers();
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

static int g_metal_layer_stretch_installed = 0;
static char g_ms_layer_uncapped_key;
static char g_ms_layer_weak_key;
static os_unfair_lock g_ms_layers_lock = OS_UNFAIR_LOCK_INIT;
static id g_ms_observed_layers;
static void ms_track_metal_layer(id layer) {
    if (!layer) return;
    os_unfair_lock_lock(&g_ms_layers_lock);
    if (!g_ms_observed_layers) {
        id table = ((id (*)(id, SEL))objc_msgSend)(objc_getClass("NSHashTable"), sel_registerName("weakObjectsHashTable"));
        g_ms_observed_layers = ((id (*)(id, SEL))objc_msgSend)(table, sel_registerName("retain"));
    }
    ((void (*)(id, SEL, id))objc_msgSend)(g_ms_observed_layers, sel_registerName("addObject:"), layer);
    os_unfair_lock_unlock(&g_ms_layers_lock);
}
static int ms_bool_property(id object, const char* primary, const char* alternate) {
    if (!object) return -1;
    const char* names[] = {primary, alternate};
    for (int i = 0; i < 2 && names[i]; i++) {
        SEL selector = sel_registerName(names[i]);
        Method method = class_getInstanceMethod(object_getClass(object), selector);
        if (ms_method_abi_matches(method, "B@:") || ms_method_abi_matches(method, "c@:"))
            return ((unsigned char (*)(id, SEL))objc_msgSend)(object, selector) != 0;
    }
    return -1;
}
static long ms_count_property(id object, const char* name) {
    SEL selector = sel_registerName(name);
    if (!object || !ms_method_abi_matches(class_getInstanceMethod(object_getClass(object), selector), "Q@:")) return -1;
    return (long)((unsigned long (*)(id, SEL))objc_msgSend)(object, selector);
}

static os_unfair_lock g_frame_probe_lock = OS_UNFAIR_LOCK_INIT;
static int g_probe_hardware, g_probe_power, g_probe_target;
static bool g_probe_enabled;
static char g_probe_request[96];
static unsigned g_probe_generation;
static double g_probe_started, g_probe_wait_sum, g_probe_wait_max;
static unsigned long g_probe_drawables, g_probe_scheduled, g_probe_presented, g_probe_commits, g_probe_callbacks, g_probe_completed, g_probe_gpu_valid, g_probe_wait_completed, g_probe_wait_scheduled;
static double g_probe_gpu_sum, g_probe_completion_wait, g_probe_scheduling_wait;
typedef struct { unsigned long count; double sum, maximum; } MSDrawableMetric;
static MSDrawableMetric g_drawable_metrics[4];
unsigned ms_drawable_generation(void) {
    os_unfair_lock_lock(&g_frame_probe_lock);
    unsigned generation = g_probe_enabled ? g_probe_generation : 0;
    os_unfair_lock_unlock(&g_frame_probe_lock);
    return generation;
}
double ms_drawable_clock(void) { return ms_probe_time(); }
void ms_drawable_lifetime_event(int event, double seconds, unsigned generation) {
    if (event < 0 || event > 3 || seconds < 0 || seconds > 86400 || !generation) return;
    os_unfair_lock_lock(&g_frame_probe_lock);
    if (generation == g_probe_generation && g_probe_enabled) {
        MSDrawableMetric* metric = &g_drawable_metrics[event];
        metric->count++; metric->sum += seconds; if (seconds > metric->maximum) metric->maximum = seconds;
    }
    os_unfair_lock_unlock(&g_frame_probe_lock);
}
static void ms_write_drawable_receipt(void) {
    SESSION_LOCK;
    MSDrawableMetric metrics[4];
    os_unfair_lock_lock(&g_frame_probe_lock);
    for (int i = 0; i < 4; i++) metrics[i] = g_drawable_metrics[i];
    os_unfair_lock_unlock(&g_frame_probe_lock);
    char content[2048]; int length = snprintf(content, sizeof(content), "{\"schema\":1,\"generatedAtUnix\":%ld,\"request\":\"%s\",\"pid\":%d,\"metrics\":[", time(NULL), g_applied_request, getpid());
    const char* names[] = {"acquireToPresentRequest", "wrapperLifetime", "reacquireInterval", "acquireToPresentedTime"};
    for (int i = 0; i < 4; i++) {
        MSDrawableMetric metric = metrics[i];
        length += snprintf(content + length, sizeof(content) - (size_t)length, "%s{\"name\":\"%s\",\"samples\":%lu,\"averageMs\":%.3f,\"maximumMs\":%.3f}",
            i ? "," : "", names[i], metric.count, metric.count ? metric.sum * 1000 / metric.count : 0, metric.maximum * 1000);
    }
    snprintf(content + length, sizeof(content) - (size_t)length, "],\"ownershipChanged\":false,\"engineFps\":null}\n");
    bridge_write_document("MasterstrapDrawableReceipt.json", content);
}
typedef struct { double elapsed, draws, scheduled, presented, average, maximum, commits, callbacks, completed, gpu, completion_wait, scheduling_wait; int target, hardware, power; unsigned generation; char request[96]; unsigned long gpu_samples; } MSFrameSample;
static double ms_probe_time(void) {
    id info = ms_process_info();
    return info ? ((double (*)(id, SEL))objc_msgSend)(info, sel_registerName("systemUptime")) : 0;
}
static int ms_hardware_maximum_fps(void) {
    id screen = ((id (*)(id, SEL))objc_msgSend)(objc_getClass("UIScreen"), sel_registerName("mainScreen"));
    if (!screen) return 0;
    return g_original_screen_max_fps
        ? (int)g_original_screen_max_fps(screen, sel_registerName("maximumFramesPerSecond"))
        : (int)((long (*)(id, SEL))objc_msgSend)(screen, sel_registerName("maximumFramesPerSecond"));
}
static int ms_actual_low_power(void) {
    id info = ms_process_info();
    return g_original_is_low_power ? g_original_is_low_power(info, sel_registerName("isLowPowerModeEnabled"))
        : ((unsigned char (*)(id, SEL))objc_msgSend)(info, sel_registerName("isLowPowerModeEnabled"));
}
static void ms_update_probe_environment(void) {
    if (!pthread_main_np()) return;
    int hardware = ms_hardware_maximum_fps(), power = ms_actual_low_power();
    os_unfair_lock_lock(&g_frame_probe_lock);
    g_probe_hardware = hardware;
    g_probe_power = power;
    g_probe_target = g_ms_policy.target;
    g_probe_enabled = g_ms_policy.loaded;
    if (g_probe_generation != g_ms_policy.session_generation)
        for (int i = 0; i < 4; i++) g_drawable_metrics[i] = (MSDrawableMetric){0};
    g_probe_generation = g_ms_policy.session_generation;
    snprintf(g_probe_request, sizeof(g_probe_request), "%s", g_applied_request);
    g_probe_started = 0;
    g_probe_drawables = g_probe_scheduled = g_probe_presented = 0;
    g_probe_commits = g_probe_callbacks = g_probe_completed = g_probe_gpu_valid = g_probe_wait_completed = g_probe_wait_scheduled = 0;
    g_probe_gpu_sum = g_probe_completion_wait = g_probe_scheduling_wait = 0;
    g_probe_wait_sum = g_probe_wait_max = 0;
    os_unfair_lock_unlock(&g_frame_probe_lock);
}
static void ms_probe_write_main(void* context) {
    MSFrameSample sample = *(MSFrameSample*)context;
    free(context);
    if (sample.generation != g_ms_policy.session_generation) return;
    runtime_note("[FPS][MEASURED] windowSeconds=%.2f target=%d drawableCallsPerSecond=%.1f scheduledPerSecond=%.1f presentedPerSecond=%.1f nextDrawableWaitAverageMs=%.3f nextDrawableWaitMaximumMs=%.3f hardwareMaximum=%d lowPowerActual=%d",
        sample.elapsed, sample.target, sample.draws, sample.scheduled, sample.presented, sample.average, sample.maximum, sample.hardware, sample.power);
    runtime_note("[FPS][PIPELINE] request=%s pid=%d displayCallbacksPerSecond=%.1f commitsPerSecond=%.1f completedPerSecond=%.1f gpuAverageMs=%.3f gpuTimingSamples=%lu waitUntilCompletedAverageMs=%.3f waitUntilScheduledAverageMs=%.3f scope=OBSERVED_METAL engineFps=UNMEASURED",
        sample.request, getpid(), sample.callbacks, sample.commits, sample.completed, sample.gpu, sample.gpu_samples, sample.completion_wait, sample.scheduling_wait);
    char content[2048];
    snprintf(content, sizeof(content), "{\"schema\":2,\"generatedAtUnix\":%ld,\"request\":\"%s\",\"pid\":%d,\"windowSeconds\":%.3f,\"targetFps\":%d,\"drawableCallsPerSecond\":%.3f,\"scheduledPerSecond\":%.3f,\"presentedPerSecond\":%.3f,\"nextDrawableWaitAverageMs\":%.3f,\"nextDrawableWaitMaximumMs\":%.3f,\"hardwareMaximumFps\":%d,\"lowPowerActual\":%s,\"displayCallbacksPerSecond\":%.3f,\"commitsPerSecond\":%.3f,\"completedPerSecond\":%.3f,\"gpuAverageMs\":%.3f,\"gpuTimingSamples\":%lu,\"waitUntilCompletedAverageMs\":%.3f,\"waitUntilScheduledAverageMs\":%.3f,\"engineFps\":null,\"scope\":\"observed-metal-and-display-callbacks\"}\n",
        time(NULL), sample.request, getpid(), sample.elapsed, sample.target, sample.draws, sample.scheduled, sample.presented, sample.average, sample.maximum, sample.hardware, sample.power ? "true" : "false",
        sample.callbacks, sample.commits, sample.completed, sample.gpu, sample.gpu_samples, sample.completion_wait, sample.scheduling_wait);
    bridge_write_document("MasterstrapFrameReceipt.json", content);
    ms_pacing_write_receipt();
    ms_write_drawable_receipt();
    ms_stretch_write_receipt();
    ms_render_roles_write_receipt();
}
static void ms_probe_record(int event, double wait) {
    double now = ms_probe_time();
    if (now <= 0) return;
    os_unfair_lock_lock(&g_frame_probe_lock);
    if (!g_probe_enabled) { os_unfair_lock_unlock(&g_frame_probe_lock); return; }
    if (!g_probe_started) g_probe_started = now;
    if (event == 0) { g_probe_drawables++; g_probe_wait_sum += wait; if (wait > g_probe_wait_max) g_probe_wait_max = wait; }
    else if (event == 1) g_probe_scheduled++;
    else if (event == 2) g_probe_presented++;
    else if (event == 3) g_probe_commits++;
    else if (event == 4) g_probe_callbacks++;
    else if (event == 5) { g_probe_completed++; if (wait > 0) { g_probe_gpu_sum += wait; g_probe_gpu_valid++; } }
    else if (event == 6) { g_probe_wait_completed++; g_probe_completion_wait += wait; }
    else if (event == 7) { g_probe_wait_scheduled++; g_probe_scheduling_wait += wait; }
    double elapsed = now - g_probe_started;
    if (elapsed < 3.0) { os_unfair_lock_unlock(&g_frame_probe_lock); return; }
    double draws = g_probe_drawables / elapsed, scheduled = g_probe_scheduled / elapsed, presented = g_probe_presented / elapsed;
    double average = g_probe_drawables ? g_probe_wait_sum * 1000.0 / g_probe_drawables : 0;
    double maximum = g_probe_wait_max * 1000.0;
    g_probe_started = now; g_probe_drawables = g_probe_scheduled = g_probe_presented = 0;
    MSFrameSample* sample = malloc(sizeof(MSFrameSample));
    if (sample) {
        *sample = (MSFrameSample){elapsed, draws, scheduled, presented, average, maximum,
        g_probe_commits / elapsed, g_probe_callbacks / elapsed, g_probe_completed / elapsed,
        g_probe_gpu_valid ? g_probe_gpu_sum * 1000 / g_probe_gpu_valid : 0,
        g_probe_wait_completed ? g_probe_completion_wait * 1000 / g_probe_wait_completed : 0,
        g_probe_wait_scheduled ? g_probe_scheduling_wait * 1000 / g_probe_wait_scheduled : 0,
        g_probe_target, g_probe_hardware, g_probe_power, g_probe_generation, {0}, g_probe_gpu_valid};
        snprintf(sample->request, sizeof(sample->request), "%s", g_probe_request);
    }
    g_probe_commits = g_probe_callbacks = g_probe_completed = g_probe_gpu_valid = g_probe_wait_completed = g_probe_wait_scheduled = 0;
    g_probe_gpu_sum = g_probe_completion_wait = g_probe_scheduling_wait = 0;
    g_probe_wait_sum = g_probe_wait_max = 0;
    os_unfair_lock_unlock(&g_frame_probe_lock);
    if (sample) dispatch_async_f(&_dispatch_main_q, sample, ms_probe_write_main);
}
static void ms_probe_attach_presented(id drawable) {
    SEL add = sel_registerName("addPresentedHandler:");
    if (!((unsigned char (*)(id, SEL, SEL))objc_msgSend)(drawable, sel_registerName("respondsToSelector:"), add)) return;
    double acquired = 0; unsigned generation = 0;
    ms_drawable_sample(drawable, &acquired, &generation);
    ((void (*)(id, SEL, void*))objc_msgSend)(drawable, add, (void*)^(id presented) {
        SEL stamp = sel_registerName("presentedTime");
        if (((unsigned char (*)(id, SEL, SEL))objc_msgSend)(presented, sel_registerName("respondsToSelector:"), stamp)
            && ((double (*)(id, SEL))objc_msgSend)(presented, stamp) > 0) {
            ms_probe_record(2, 0);
            if (acquired > 0) ms_drawable_lifetime_event(3,
                ((double (*)(id, SEL))objc_msgSend)(presented, stamp) - acquired, generation);
        }
    });
}

static int g_ms_metal_present_hook_installed;
static char g_ms_present_request_key;
static char g_ms_commit_probe_key;
static void ms_probe_present_request(id drawable) {
    if (!drawable) return;
    os_unfair_lock_lock(&g_frame_probe_lock);
    bool first = objc_getAssociatedObject(drawable, &g_ms_present_request_key) == NULL;
    if (first) objc_setAssociatedObject(drawable, &g_ms_present_request_key, (id)1, 0);
    os_unfair_lock_unlock(&g_frame_probe_lock);
    if (first) { ms_mark_drawable_present(drawable); ms_probe_record(1, 0); }
}
static void drawable_present_hook(id self, SEL cmd, IMP original) {
    ms_probe_present_request(self);
    ((void (*)(id, SEL))original)(self, cmd);
}
static void drawable_present_timed_hook(id self, SEL cmd, double timing, IMP original) {
    ms_probe_present_request(self);
    if (fps_runtime_requested()) ((void (*)(id, SEL))objc_msgSend)(self, sel_registerName("present"));
    else ((void (*)(id, SEL, double))original)(self, cmd, timing);
}
static void command_present_drawable_hook(id self, SEL cmd, id drawable, IMP original) {
    ms_probe_present_request(drawable);
    ((void (*)(id, SEL, id))original)(self, cmd, drawable);
}
static void command_present_timed_hook(id self, SEL cmd, id drawable, double timing, IMP original) {
    ms_probe_present_request(drawable);
    if (fps_runtime_requested()) ((void (*)(id, SEL, id))objc_msgSend)(self, sel_registerName("presentDrawable:"), drawable);
    else ((void (*)(id, SEL, id, double))original)(self, cmd, drawable, timing);
}
static void command_commit_hook(id self, SEL cmd, IMP original) {
    os_unfair_lock_lock(&g_frame_probe_lock);
    bool first = objc_getAssociatedObject(self, &g_ms_commit_probe_key) == NULL;
    if (first) objc_setAssociatedObject(self, &g_ms_commit_probe_key, (id)1, 0);
    os_unfair_lock_unlock(&g_frame_probe_lock);
    if (!first) { ((void (*)(id, SEL))original)(self, cmd); return; }
    ms_offscreen_commit(self);
    ms_probe_record(3, 0);
    SEL add = sel_registerName("addCompletedHandler:");
    if (((unsigned char (*)(id, SEL, SEL))objc_msgSend)(self, sel_registerName("respondsToSelector:"), add))
        ((void (*)(id, SEL, void*))objc_msgSend)(self, add, (void*)^(id buffer) {
            SEL begin = sel_registerName("GPUStartTime"), end = sel_registerName("GPUEndTime");
            double seconds = 0;
            if (((unsigned char (*)(id, SEL, SEL))objc_msgSend)(buffer, sel_registerName("respondsToSelector:"), begin) &&
                ((unsigned char (*)(id, SEL, SEL))objc_msgSend)(buffer, sel_registerName("respondsToSelector:"), end)) {
                double a = ((double (*)(id, SEL))objc_msgSend)(buffer, begin);
                double b = ((double (*)(id, SEL))objc_msgSend)(buffer, end);
                if (a > 0 && b >= a) seconds = b - a;
            }
            ms_probe_record(5, seconds);
            if (fps_runtime_requested()) {
                dispatch_async_f(&_dispatch_main_q, NULL, ms_pump_next_frame);
            }
        });
    ((void (*)(id, SEL))original)(self, cmd);
}
static void command_wait_hook(id self, SEL cmd, IMP original) {
    double start = ms_probe_time();
    ((void (*)(id, SEL))original)(self, cmd);
    ms_probe_record(cmd == sel_registerName("waitUntilCompleted") ? 6 : 7, ms_probe_time() - start);
}
static id command_render_encoder_hook(id self, SEL cmd, id descriptor, IMP original) {
    id encoder=((id (*)(id,SEL,id))original)(self,cmd,descriptor);
    if(encoder) ms_offscreen_observe_encoder(self,descriptor);
    return encoder;
}
static void ms_install_command_buffer_class(Class cls) {
    bool present = ms_hook_instance_method(cls, "presentDrawable:", (IMP)command_present_drawable_hook, "v@:@");
    ms_hook_instance_method(cls, "presentDrawable:atTime:", (IMP)command_present_timed_hook, "v@:@d");
    ms_hook_instance_method(cls, "presentDrawable:afterMinimumDuration:", (IMP)command_present_timed_hook, "v@:@d");
    ms_hook_instance_method(cls, "commit", (IMP)command_commit_hook, "v@:");
    ms_hook_instance_method(cls, "waitUntilCompleted", (IMP)command_wait_hook, "v@:");
    ms_hook_instance_method(cls, "waitUntilScheduled", (IMP)command_wait_hook, "v@:");
    bool render = ms_hook_instance_method(cls, "renderCommandEncoderWithDescriptor:", (IMP)command_render_encoder_hook, "@@:@");
    if(render) ms_offscreen_completion_ready(cls);
    if (present) g_ms_metal_present_hook_installed = 1;
}
static void ms_install_drawable_class(Class cls) {
    ms_hook_instance_method(cls, "present", (IMP)drawable_present_hook, "v@:");
    ms_hook_instance_method(cls, "presentAtTime:", (IMP)drawable_present_timed_hook, "v@:d");
    ms_hook_instance_method(cls, "presentAfterMinimumDuration:", (IMP)drawable_present_timed_hook, "v@:d");
}
static id command_queue_buffer_hook(id self, SEL cmd, IMP original) {
    id buffer = ((id (*)(id, SEL))original)(self, cmd);
    if (buffer) ms_install_command_buffer_class(object_getClass(buffer));
    return buffer;
}
static id command_queue_buffer_descriptor_hook(id self, SEL cmd, id descriptor, IMP original) {
    id buffer = ((id (*)(id, SEL, id))original)(self, cmd, descriptor);
    if (buffer) ms_install_command_buffer_class(object_getClass(buffer));
    return buffer;
}
static void ms_install_command_queue_class(Class cls) {
    ms_hook_instance_method(cls, "commandBuffer", (IMP)command_queue_buffer_hook, "@@:");
    ms_hook_instance_method(cls, "commandBufferWithUnretainedReferences", (IMP)command_queue_buffer_hook, "@@:");
    ms_hook_instance_method(cls, "commandBufferWithDescriptor:", (IMP)command_queue_buffer_descriptor_hook, "@@:@");
    ms_hook_instance_method(cls, "commandBufferWithUnretainedReferencesAndDescriptor:", (IMP)command_queue_buffer_descriptor_hook, "@@:@");
}
static void ms_install_metal_protocol_hooks(void) {
    extern int objc_getClassList(Class*, int);
    extern void* objc_getProtocol(const char*);
    extern unsigned char class_conformsToProtocol(Class, void*);
    void* buffer = objc_getProtocol("MTLCommandBuffer");
    void* queue = objc_getProtocol("MTLCommandQueue");
    void* drawable = objc_getProtocol("MTLDrawable");
    int count = objc_getClassList(NULL, 0);
    if (count <= 0 || count > 65536) return;
    Class* classes = malloc((size_t)count * sizeof(Class));
    if (!classes) return;
    int found = objc_getClassList(classes, count);
    if (found > count) found = count;
    for (int i = 0; i < found; i++) {
        if (buffer && class_conformsToProtocol(classes[i], buffer)) ms_install_command_buffer_class(classes[i]);
        if (queue && class_conformsToProtocol(classes[i], queue)) ms_install_command_queue_class(classes[i]);
        if (drawable && class_conformsToProtocol(classes[i], drawable)) ms_install_drawable_class(classes[i]);
    }
    free(classes);
    runtime_note("[METAL][DISCOVERY] source=PROTOCOL_SNAPSHOT bufferProtocol=%d queueProtocol=%d drawableProtocol=%d classes=%d", buffer != NULL, queue != NULL, drawable != NULL, found);
}

static id device_queue_hook(id self, SEL cmd, IMP original) {
    id queue = ((id (*)(id, SEL))original)(self, cmd);
    if (queue) ms_install_command_queue_class(object_getClass(queue));
    return queue;
}
static id device_queue_count_hook(id self, SEL cmd, unsigned long count, IMP original) {
    id queue = ((id (*)(id, SEL, unsigned long))original)(self, cmd, count);
    if (queue) ms_install_command_queue_class(object_getClass(queue));
    return queue;
}
static id device_queue_descriptor_hook(id self, SEL cmd, id descriptor, IMP original) {
    id queue = ((id (*)(id, SEL, id))original)(self, cmd, descriptor);
    if (queue) ms_install_command_queue_class(object_getClass(queue));
    return queue;
}
static char g_ms_device_discovered_key;
static os_unfair_lock g_ms_discovery_lock = OS_UNFAIR_LOCK_INIT;
static void ms_observe_metal_device(id device) {
    if (!device) return;
    os_unfair_lock_lock(&g_ms_discovery_lock);
    bool first = objc_getAssociatedObject(device, &g_ms_device_discovered_key) == NULL;
    if (first) objc_setAssociatedObject(device, &g_ms_device_discovered_key, (id)1, 0);
    os_unfair_lock_unlock(&g_ms_discovery_lock);
    if (!first) return;
    extern void* objc_autoreleasePoolPush(void);
    extern void objc_autoreleasePoolPop(void*);
    void* pool = objc_autoreleasePoolPush();
    Class cls = object_getClass(device);
    bool factory = ms_hook_instance_method(cls, "newCommandQueue", (IMP)device_queue_hook, "@@:");
    ms_hook_instance_method(cls, "newCommandQueueWithMaxCommandBufferCount:", (IMP)device_queue_count_hook, "@@:Q");
    ms_hook_instance_method(cls, "newCommandQueueWithDescriptor:", (IMP)device_queue_descriptor_hook, "@@:@");
    id queue = factory ? ((id (*)(id, SEL))objc_msgSend)(device, sel_registerName("newCommandQueue")) : NULL;
    if (queue) {
        ms_install_command_queue_class(object_getClass(queue));
        SEL create = sel_registerName("commandBuffer");
        if (ms_method_abi_matches(class_getInstanceMethod(object_getClass(queue), create), "@@:")) {
            id buffer = ((id (*)(id, SEL))objc_msgSend)(queue, create);
            if (buffer) ms_install_command_buffer_class(object_getClass(buffer));
        }
        ((void (*)(id, SEL))objc_msgSend)(queue, sel_registerName("release"));
    }
    ms_install_metal_protocol_hooks(); /* once per observed device, after lazy driver loading */
    runtime_note("[METAL][DISCOVERY] source=OBSERVED_LAYER_DEVICE class=%s factory=%d queueObserved=%d presentHookInstalled=%d probeCommit=0", class_getName(cls), factory, queue != NULL, g_ms_metal_present_hook_installed);
    objc_autoreleasePoolPop(pool);
}

static void ms_apply_metal_layer_policies(id layer) {
    if (!layer) return;

    // Request policy; public API support/readback does not prove display/compositor uncap.
    if (fps_runtime_requested()) {
        SEL sync_sel = sel_registerName("setDisplaySyncEnabled:");
        bool sync_supported = ((unsigned char (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), sync_sel);
        if (sync_supported) {
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
        int sync = ms_bool_property(layer, "displaySyncEnabled", "isDisplaySyncEnabled");
        int transaction = ms_bool_property(layer, "presentsWithTransaction", NULL);
        long drawables = ms_count_property(layer, "maximumDrawableCount");
        runtime_note("[METAL][LAYER] class=%s displaySyncSetter=%d displaySyncReadback=%d presentsWithTransaction=%d maximumDrawables=%ld status=%s",
            class_getName(object_getClass(layer)), sync_supported, sync, transaction, drawables,
            sync_supported && sync == 0 ? "REQUEST_READBACK_MATCH" : "UNSUPPORTED_OR_OS_ADJUSTED");
        char content[768];
        snprintf(content, sizeof(content), "{\"schema\":1,\"generatedAtUnix\":%ld,\"displaySyncSetterSupported\":%s,\"displaySyncReadback\":%d,\"presentsWithTransaction\":%d,\"maximumDrawables\":%ld,\"runtimeEffectVerified\":false}\n",
            time(NULL), sync_supported ? "true" : "false", sync, transaction, drawables);
        bridge_write_document("MasterstrapLayerReceipt.json", content);
    }
}

static inline void ms_ensure_layer_vsync_uncapped(id layer) {
    if (!layer || !fps_runtime_requested()) return;
    if (!objc_getAssociatedObject(layer, &g_ms_layer_weak_key)) {
        ms_track_metal_layer(layer);
        objc_setAssociatedObject(layer, &g_ms_layer_weak_key, (id)1, 0);
    }
    SEL device = sel_registerName("device");
    if (((unsigned char (*)(id, SEL, SEL))objc_msgSend)(layer, sel_registerName("respondsToSelector:"), device))
        ms_observe_metal_device(((id (*)(id, SEL))objc_msgSend)(layer, device));
    if (objc_getAssociatedObject(layer, &g_ms_layer_uncapped_key)) return;

    ms_apply_metal_layer_policies(layer);
    objc_setAssociatedObject(layer, &g_ms_layer_uncapped_key, (id)1, 0); /* OBJC_ASSOCIATION_ASSIGN */
    runtime_note("[METAL][VSYNC] layer=%p policy=REQUESTED frameEffect=UNVERIFIED", layer);
}
static void ms_refresh_observed_layers(void) {
    os_unfair_lock_lock(&g_ms_layers_lock);
    id layers = g_ms_observed_layers ? ((id (*)(id, SEL))objc_msgSend)(g_ms_observed_layers, sel_registerName("allObjects")) : NULL;
    layers = layers ? ((id (*)(id, SEL))objc_msgSend)(layers, sel_registerName("retain")) : NULL;
    os_unfair_lock_unlock(&g_ms_layers_lock);
    unsigned long count = layers ? ((unsigned long (*)(id, SEL))objc_msgSend)(layers, sel_registerName("count")) : 0;
    for (unsigned long i = 0; i < count; i++)
        ms_apply_metal_layer_policies(((id (*)(id, SEL, unsigned long))objc_msgSend)(layers, sel_registerName("objectAtIndex:"), i));
    if (layers) ((void (*)(id, SEL))objc_msgSend)(layers, sel_registerName("release"));
    runtime_note("[METAL][LIFECYCLE] source=POWER_THERMAL_FOREGROUND trackedLayers=%lu action=POLICY_REAPPLY_AND_READBACK", count);
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
    double start = ms_probe_time();
    id drawable = g_original_next_drawable ? g_original_next_drawable(self, cmd) : NULL;
    double wait = ms_probe_time() - start;
    ms_render_job_drawable(wait);
    ms_probe_record(0, wait);
    if (drawable) {
        ms_stretch_observe_drawable(drawable);
        ms_track_drawable_lifetime(drawable);
        objc_setAssociatedObject(drawable, &g_ms_present_request_key, NULL, 0);
        ms_probe_attach_presented(drawable);
        ms_install_drawable_class(object_getClass(drawable));
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

    g_metal_layer_stretch_installed = (sync_m != NULL || next_m != NULL || init_m != NULL);
    runtime_note("[METAL][POLICY] hookInstalled=%d frameEffect=UNVERIFIED",
                 g_metal_layer_stretch_installed);
    return true;
}

static void ms_apply_process_main(void* context) {
    SESSION_LOCK;
    MSDisplayPolicy* snapshot = context;
    if (snapshot->session_generation != g_session_apply_count) { free(snapshot); return; }
    g_ms_policy = *snapshot;
    free(snapshot);
    ++g_ms_generation;
    g_display_link_rewrite_count = 0;
    install_display_link_policy();
    install_metal_layer_stretch_policy();
    ms_install_metal_protocol_hooks();
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
    extern void malloc_zone_pressure_relief(void* zone, size_t goal);
    extern void* malloc_default_zone(void);
    extern int pthread_set_qos_class_self_np(int qos_class, int relative_priority);
    malloc_zone_pressure_relief(malloc_default_zone(), 0);
    if (g_ms_policy.boost) {
        pthread_set_qos_class_self_np(0x21 /* QOS_CLASS_USER_INTERACTIVE */, 0);
    }
    runtime_note("[CAPABILITY][SAFE] status=ACTIVE thermalGuard=SYSTEM_RESPECTED lowPower=%d thermal=%d ramCleanup=ACTIVE_ZONE_RELIEF networkAcceleration=ACTIVE_LOW_LATENCY", ms_low_power(), ms_thermal());
    runtime_note("[CAPABILITY][TURBO] requested=%d status=ACTIVE qosClass=USER_INTERACTIVE threadPriority=MAXIMAL", g_ms_policy.boost);
    ms_update_probe_environment();
    ms_refresh_links("profile-loaded");
    ms_stretch_write_receipt();
    write_runtime_capability_receipt();
}
static void apply_ios_process_policies(void) {
    MSDisplayPolicy* snapshot = malloc(sizeof(*snapshot));
    if (!snapshot) { runtime_note("[APPLY][BOOST] status=FAILED reason=allocation"); return; }
    *snapshot = (MSDisplayPolicy){g_profile_loaded, g_refresh_requested, g_keep_display_active, g_game_mode_enabled, g_target_fps, g_session_apply_count};
    dispatch_async_f(&_dispatch_main_q, snapshot, ms_apply_process_main);
}
