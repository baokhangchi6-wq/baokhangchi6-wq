/* Observe verified framebuffer callers; keep both GUI target and UIKit native.
 * Stretch only the main scene perspective argument resolved from this IPA. */
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
static unsigned long g_stretch_scene_reads, g_stretch_scene_calls;
static int g_stretch_scene_reason;
extern void ms_offscreen_mark_layer(void*);
static float g_stretch_scene_input, g_stretch_scene_output;

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
    bool note = allowed && (g_stretch_hits == 0 || g_stretch_last_percent != percent ||
        g_stretch_native.size.width != native.size.width || g_stretch_native.size.height != native.size.height);
    if (allowed) {
        g_stretch_hits++; g_stretch_native = native; g_stretch_adjusted = adjusted; g_stretch_last_percent = percent;
    }
    os_unfair_lock_unlock(&g_stretch_lock);
    if (note) runtime_note("[STRETCH][FRAMEBUFFER_INPUT] percent=%d native=%.1fx%.1f adjusted=%.1fx%.1f scope=NATIVE_GUI_TARGET layoutChanged=0", percent,
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
static float ms_stretch_scene_aspect(float input) {
    os_unfair_lock_lock(&g_stretch_lock);
    g_stretch_scene_calls++;
    float result = input;
    int reason = !g_stretch_base ? 1 : g_stretch_target < 25 || g_stretch_target >= 100 ? 2 :
        g_stretch_native.size.width <= g_stretch_native.size.height ? 3 :
        g_stretch_native.size.height < 64 || g_stretch_native.size.width > 16384 ? 4 :
        !__builtin_isfinite(input) || input <= 0 ? 5 : 0;
    if (!reason && __builtin_fabs(input-g_stretch_native.size.width/g_stretch_native.size.height)>0.01) reason=6;
    bool first=false;
    if (!reason) {
        result=input*g_stretch_target/100.0f;
        first=g_stretch_scene_reads++==0;
    }
    g_stretch_scene_reason=reason;
    g_stretch_scene_input=__builtin_isfinite(input) ? input : 0;
    g_stretch_scene_output=__builtin_isfinite(result) ? result : 0;
    os_unfair_lock_unlock(&g_stretch_lock);
    if (first) runtime_note("[STRETCH][SCENE_ASPECT] input=%.5f output=%.5f scope=VERIFIED_MAIN_CAMERA guiTarget=NATIVE touchLayout=NATIVE",input,result);
    // ponytail: native target preserves GUI; lower scene pixels need a verified scene-only upscale pass.
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
    bool verified = kStretchVerified && kStretchSceneVerified && base && engine_identity_matches(base);
    // UIKit [UIView frame] swizzling disabled: prevents GUI distortion and crashes;
    // Stretch resolution runs safely through Luau VM camera aspect stream.
    g_stretch_original_frame = NULL;
    os_unfair_lock_lock(&g_stretch_lock);
    __atomic_store_n(&g_stretch_base, verified ? base : 0, __ATOMIC_RELEASE);
    g_stretch_target = g_profile_loaded && g_launch_authorized && g_stretch_percent >= 25 && g_stretch_percent <= 100 ? g_stretch_percent : 100;
    g_stretch_hits = 0;
    g_stretch_texture_width = g_stretch_texture_height = 0;
    g_stretch_drawable_samples = 0;
    g_stretch_scene_calls = g_stretch_scene_reads = 0; g_stretch_scene_reason = 0;
    g_stretch_scene_input = g_stretch_scene_output = 0;
    os_unfair_lock_unlock(&g_stretch_lock);
    runtime_note("[CAPABILITY][STRETCH] status=%s percent=%d backend=verified-scene-aspect-native-gui deviceEffect=UNVERIFIED",
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
    unsigned long scene = g_stretch_scene_reads, scene_calls = g_stretch_scene_calls;
    int scene_reason = g_stretch_scene_reason;
    double scale = g_stretch_content_scale;
    float scene_input = g_stretch_scene_input, scene_output = g_stretch_scene_output;
    os_unfair_lock_unlock(&g_stretch_lock);
    char content[1536];
    snprintf(content, sizeof(content), "{\"schema\":4,\"request\":\"%s\",\"pid\":%d,\"gateOpen\":%s,\"percent\":%d,\"frameReads\":%lu,\"nativeWidth\":%.3f,\"nativeHeight\":%.3f,\"framebufferInputWidth\":%.3f,\"framebufferInputHeight\":%.3f,\"observedDrawableWidth\":%ld,\"observedDrawableHeight\":%ld,\"contentScale\":%.3f,\"sceneAspectCalls\":%lu,\"sceneAspectReads\":%lu,\"sceneInputAspect\":%.5f,\"sceneOutputAspect\":%.5f,\"sceneRejectedReason\":%d,\"guiTarget\":\"NATIVE\",\"pixelReduction\":false,\"layoutChanged\":false,\"deviceVerified\":false}\n",
        g_applied_request, getpid(), enabled ? "true" : "false", percent, hits, native.size.width, native.size.height, adjusted.size.width, adjusted.size.height, width, height, scale, scene_calls, scene, scene_input, scene_output, scene_reason);
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
