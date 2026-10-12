/* Alter only the frame value read by verified framebuffer sizing callers.
 * UIKit's stored frame, layout, and touch coordinates remain native. */
typedef struct { double x, y; } MSStretchPoint;
typedef struct { double width, height; } MSStretchSize;
typedef struct { MSStretchPoint origin; MSStretchSize size; } MSStretchRect;
_Static_assert(sizeof(MSStretchRect) == 32, "64-bit CGRect ABI");
static MSStretchRect (*g_stretch_original_frame)(id, SEL);
static os_unfair_lock g_stretch_lock = OS_UNFAIR_LOCK_INIT;
static uintptr_t g_stretch_base;
static int g_stretch_target = 100;
static unsigned long g_stretch_hits;
static MSStretchRect g_stretch_native, g_stretch_adjusted;
static int g_stretch_last_percent = 100;
static long g_stretch_texture_width, g_stretch_texture_height;
static unsigned g_stretch_drawable_samples;
static double g_stretch_content_scale;
static unsigned long g_stretch_ui_reads;
static unsigned long g_stretch_ui_calls, g_stretch_ui_rejected[7];
static int g_stretch_ui_reason, g_stretch_ui_units;
static float g_stretch_ui_height;
extern void ms_offscreen_mark_layer(void*);
static float g_stretch_ui_input, g_stretch_ui_output;

static MSStretchRect ms_stretch_frame_value(MSStretchRect native, uintptr_t caller) {
    if (!__builtin_isfinite(native.size.width) || !__builtin_isfinite(native.size.height)) return native;
    uintptr_t base = __atomic_load_n(&g_stretch_base, __ATOMIC_ACQUIRE);
    bool allowed = false;
    for (size_t i = 0; i < sizeof(kStretchFrameReturns) / sizeof(kStretchFrameReturns[0]); i++)
        if (base && kStretchFrameReturns[i] && caller == base + kStretchFrameReturns[i]) allowed = true;
    if (!allowed) return native;
    os_unfair_lock_lock(&g_stretch_lock);
    if (base != g_stretch_base) { os_unfair_lock_unlock(&g_stretch_lock); return native; }
    MSStretchRect adjusted = native;
    int percent = g_stretch_target;
    if (allowed && percent >= 25 && percent < 100 && native.size.width > native.size.height &&
        native.size.height >= 64 && native.size.width <= 16384) {
        adjusted.size.width = native.size.width * percent / 100.0;
        if (adjusted.size.width < 64) adjusted.size.width = 64;
    }
    bool note = allowed && (g_stretch_hits == 0 || g_stretch_last_percent != percent ||
        g_stretch_native.size.width != native.size.width || g_stretch_native.size.height != native.size.height);
    if (allowed) {
        g_stretch_hits++; g_stretch_native = native; g_stretch_adjusted = adjusted; g_stretch_last_percent = percent;
    }
    os_unfair_lock_unlock(&g_stretch_lock);
    if (note) runtime_note("[STRETCH][FRAMEBUFFER_INPUT] percent=%d native=%.1fx%.1f adjusted=%.1fx%.1f scope=VERIFIED_FRAME_READ layoutChanged=0", percent,
        native.size.width, native.size.height, adjusted.size.width, adjusted.size.height);
    return adjusted;
}
__attribute__((noinline, used)) static MSStretchRect ms_stretch_frame_hook(id self, SEL cmd) {
    uintptr_t caller = (uintptr_t)__builtin_extract_return_addr(__builtin_return_address(0));
    MSStretchRect native = g_stretch_original_frame(self, cmd);
    uintptr_t base = __atomic_load_n(&g_stretch_base, __ATOMIC_ACQUIRE);
    for (size_t i = 0; base && i < sizeof(kStretchFrameReturns) / sizeof(kStretchFrameReturns[0]); i++) {
        if (!kStretchFrameReturns[i] || caller != base + kStretchFrameReturns[i]) continue;
        SEL layer_selector = sel_registerName("layer");
        if(ms_method_abi_matches(class_getInstanceMethod(object_getClass(self),layer_selector),"@@:"))
            ms_offscreen_mark_layer(((id (*)(id,SEL))objc_msgSend)(self,layer_selector));
        SEL selector = sel_registerName("contentScaleFactor");
        if (ms_method_abi_matches(class_getInstanceMethod(object_getClass(self), selector), "d@:")) {
            double scale = ((double (*)(id, SEL))objc_msgSend)(self, selector);
            os_unfair_lock_lock(&g_stretch_lock);
            g_stretch_content_scale = scale >= 1 && scale <= 4 ? scale : 0;
            os_unfair_lock_unlock(&g_stretch_lock);
        }
        break;
    }
    return ms_stretch_frame_value(native, caller);
}
static float ms_stretch_ui_width(float input, float height) {
    os_unfair_lock_lock(&g_stretch_lock);
    unsigned long call = ++g_stretch_ui_calls;
    double native = g_stretch_native.size.width * g_stretch_content_scale;
    double expected = native * g_stretch_target / 100.0;
    float result = input;
    int reason = !g_stretch_base ? 1 : g_stretch_target < 25 || g_stretch_target >= 100 ? 2 :
        g_stretch_native.size.width <= g_stretch_native.size.height ? 3 :
        g_stretch_content_scale <= 0 ? 4 :
        !__builtin_isfinite(input) || !__builtin_isfinite(height) || input <= 0 || height <= 0 ? 5 : 0;
    g_stretch_ui_input = __builtin_isfinite(input) ? input : 0;
    g_stretch_ui_height = __builtin_isfinite(height) ? height : 0;
    g_stretch_ui_reason = reason; g_stretch_ui_units = 0;
    int units = !reason && __builtin_fabs(input-expected)<=2 && __builtin_fabs(height-g_stretch_native.size.height*g_stretch_content_scale)<=2 ? 2 :
        !reason && __builtin_fabs(input-g_stretch_adjusted.size.width)<=1 && __builtin_fabs(height-g_stretch_native.size.height)<=1 ? 1 : 0;
    if (units) {
        result = (float)(units==2 ? native : g_stretch_native.size.width);
        bool first = g_stretch_ui_reads++ == 0;
        g_stretch_ui_input = input; g_stretch_ui_output = result;
        g_stretch_ui_units = units;
        os_unfair_lock_unlock(&g_stretch_lock);
        if (first) runtime_note("[STRETCH][UI_PROJECTION] inputWidth=%.1f logicalWidth=%.1f units=%s scope=VERIFIED_ORTHOGRAPHIC_MATRIX actualTargetWidth=UNCHANGED touchLayout=NATIVE", input, result, units==2 ? "PIXELS" : "POINTS");
        return result;
    }
    if (!reason) reason = 6;
    bool first_rejection = g_stretch_ui_rejected[reason]==0;
    g_stretch_ui_reason = reason; g_stretch_ui_rejected[reason]++;
    os_unfair_lock_unlock(&g_stretch_lock);
    if (call <= 4 || first_rejection) runtime_note("[STRETCH][UI_PROBE] call=%lu input=%.1fx%.1f expectedWidth=%.1f nativeWidth=%.1f rejectedReason=%d", call, input, height, expected, native, reason);
    return result;
}
static bool ms_stretch_frame_abi(Method method) {
    if (!method || method_getNumberOfArguments(method) != 2) return false;
    char* result = method_copyReturnType(method);
    char* self = method_copyArgumentType(method, 0);
    char* selector = method_copyArgumentType(method, 1);
    bool valid = result && strcmp(result, "{CGRect={CGPoint=dd}{CGSize=dd}}") == 0 &&
        self && strcmp(self, "@") == 0 && selector && strcmp(selector, ":") == 0;
    free(result); free(self); free(selector); return valid;
}
static void ms_configure_stretch(uintptr_t base) {
    bool verified = kStretchVerified && base && engine_identity_matches(base);
    if (verified && !g_stretch_original_frame) {
        Method method = class_getInstanceMethod(objc_getClass("UIView"), sel_registerName("frame"));
        if (ms_stretch_frame_abi(method)) {
            g_stretch_original_frame = (void*)method_getImplementation(method);
            method_setImplementation(method, (IMP)ms_stretch_frame_hook);
        }
    }
    os_unfair_lock_lock(&g_stretch_lock);
    __atomic_store_n(&g_stretch_base, verified && g_stretch_original_frame ? base : 0, __ATOMIC_RELEASE);
    g_stretch_target = g_profile_loaded && g_launch_authorized && g_stretch_percent >= 25 && g_stretch_percent <= 100 ? g_stretch_percent : 100;
    g_stretch_hits = 0;
    g_stretch_native = g_stretch_adjusted = (MSStretchRect){0};
    g_stretch_texture_width = g_stretch_texture_height = 0;
    g_stretch_drawable_samples = 0;
    g_stretch_content_scale = 0; g_stretch_ui_reads = 0;
    g_stretch_ui_calls = 0; g_stretch_ui_reason = g_stretch_ui_units = 0; g_stretch_ui_height = 0;
    for (int i=0;i<7;i++) g_stretch_ui_rejected[i]=0;
    g_stretch_ui_input = g_stretch_ui_output = 0;
    os_unfair_lock_unlock(&g_stretch_lock);
    runtime_note("[CAPABILITY][STRETCH] status=%s percent=%d backend=verified-framebuffer-input deviceEffect=UNVERIFIED",
        g_stretch_base ? "READY" : "BLOCKED_BINARY_OR_UIKIT_ABI", g_stretch_target);
}
static void ms_stretch_observe_drawable(id drawable) {
    if (!__atomic_load_n(&g_stretch_base, __ATOMIC_ACQUIRE)) return;
    os_unfair_lock_lock(&g_stretch_lock);
    bool sample = g_stretch_drawable_samples++ % 60 == 0;
    os_unfair_lock_unlock(&g_stretch_lock);
    if (!sample) return;
    SEL selector = sel_registerName("texture");
    if (!ms_method_abi_matches(class_getInstanceMethod(object_getClass(drawable), selector), "@@:")) return;
    id texture = ((id (*)(id, SEL))objc_msgSend)(drawable, selector);
    long width = ms_count_property(texture, "width"), height = ms_count_property(texture, "height");
    os_unfair_lock_lock(&g_stretch_lock);
    g_stretch_texture_width = width; g_stretch_texture_height = height;
    os_unfair_lock_unlock(&g_stretch_lock);
}
static void ms_stretch_write_receipt(void) {
    os_unfair_lock_lock(&g_stretch_lock);
    unsigned long hits = g_stretch_hits;
    MSStretchRect native = g_stretch_native, adjusted = g_stretch_adjusted;
    int percent = g_stretch_target; bool enabled = g_stretch_base != 0;
    long width = g_stretch_texture_width, height = g_stretch_texture_height;
    unsigned long ui = g_stretch_ui_reads, ui_calls = g_stretch_ui_calls;
    int ui_reason = g_stretch_ui_reason, ui_units = g_stretch_ui_units;
    unsigned long rejected[7]; for (int i=0;i<7;i++) rejected[i]=g_stretch_ui_rejected[i];
    float ui_height = g_stretch_ui_height;
    double scale = g_stretch_content_scale;
    float ui_input = g_stretch_ui_input, ui_output = g_stretch_ui_output;
    os_unfair_lock_unlock(&g_stretch_lock);
    char content[1536];
    snprintf(content, sizeof(content), "{\"schema\":3,\"request\":\"%s\",\"pid\":%d,\"gateOpen\":%s,\"percent\":%d,\"frameReads\":%lu,\"nativeWidth\":%.3f,\"nativeHeight\":%.3f,\"framebufferInputWidth\":%.3f,\"framebufferInputHeight\":%.3f,\"observedDrawableWidth\":%ld,\"observedDrawableHeight\":%ld,\"uiProjectionCalls\":%lu,\"uiProjectionReads\":%lu,\"uiInputWidth\":%.3f,\"uiInputHeight\":%.3f,\"contentScale\":%.3f,\"uiLogicalWidth\":%.3f,\"uiUnits\":%d,\"uiRejectedReason\":%d,\"uiRejectedCounts\":[%lu,%lu,%lu,%lu,%lu,%lu],\"layoutChanged\":false,\"deviceVerified\":false}\n",
        g_applied_request, getpid(), enabled ? "true" : "false", percent, hits, native.size.width, native.size.height, adjusted.size.width, adjusted.size.height, width, height, ui_calls, ui, ui_input, ui_height, scale, ui_output, ui_units, ui_reason, rejected[1],rejected[2],rejected[3],rejected[4],rejected[5],rejected[6]);
    bridge_write_document("MasterstrapStretchReceipt.json", content);
    char archive[160];
    char request[96]; size_t i=0;
    for (; g_applied_request[i] && i<sizeof(request)-1;i++) {
        char ch=g_applied_request[i];
        request[i]=(ch>='0'&&ch<='9')||(ch>='a'&&ch<='z')||(ch>='A'&&ch<='Z')||ch=='-'||ch=='_' ? ch : '_';
    }
    request[i]=0;
    snprintf(archive,sizeof(archive),"MasterstrapStretchReceipt-%s.json",request);
    bridge_write_document(archive, content);
}
