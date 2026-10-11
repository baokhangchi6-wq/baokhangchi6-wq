/* Concrete-class hooks; inherited methods must never mutate their superclass. */
extern Class class_getSuperclass(Class);
extern unsigned int method_getNumberOfArguments(Method);
extern char* method_copyReturnType(Method);
extern char* method_copyArgumentType(Method, unsigned int);
extern IMP imp_implementationWithBlock(id);
typedef struct { Class cls; SEL selector; IMP original, hook; } MSMethodHook;
static MSMethodHook g_ms_method_hooks[256];
/* ponytail: 256 class/selector pairs; increase only after coverage reports exhaustion. */
static unsigned g_ms_method_hook_count;
static unsigned g_ms_method_rejections;
static os_unfair_lock g_ms_method_hook_lock = OS_UNFAIR_LOCK_INIT;

static bool ms_method_abi_matches(Method method, const char* signature) {
    if (!method || !signature || method_getNumberOfArguments(method) != strlen(signature) - 1) return false;
    char* type = method_copyReturnType(method);
    bool valid = type && type[0] == signature[0] && type[1] == 0;
    free(type);
    for (unsigned i = 0; valid && signature[i + 1]; i++) {
        type = method_copyArgumentType(method, i);
        valid = type && type[0] == signature[i + 1] &&
            (type[1] == 0 || (signature[i + 1] == '@' && type[1] == '"'));
        free(type);
    }
    return valid;
}
static bool ms_hook_instance_method(Class cls, const char* name, IMP handler, const char* signature) {
    if (!cls) return false;
    SEL selector = sel_registerName(name);
    os_unfair_lock_lock(&g_ms_method_hook_lock);
    for (unsigned i = 0; i < g_ms_method_hook_count; i++)
        if (g_ms_method_hooks[i].cls == cls && g_ms_method_hooks[i].selector == selector) {
            os_unfair_lock_unlock(&g_ms_method_hook_lock); return true;
        }
    Method method = class_getInstanceMethod(cls, selector);
    if (!ms_method_abi_matches(method, signature) || g_ms_method_hook_count == 256) {
        unsigned note = g_ms_method_rejections++;
        const char* reason = !method ? "SELECTOR_MISSING" : g_ms_method_hook_count == 256 ? "CAPACITY" : "ABI_MISMATCH";
        const char* encoding = method ? method_getTypeEncoding(method) : "none";
        os_unfair_lock_unlock(&g_ms_method_hook_lock);
        if (note < 64) runtime_note("[HOOK][REJECT] class=%s selector=%s expected=%s actual=%s reason=%s", class_getName(cls), name, signature, encoding, reason);
        return false;
    }
    IMP original = method_getImplementation(method);
    if (!original) { os_unfair_lock_unlock(&g_ms_method_hook_lock); return false; }
    for (unsigned i = 0; i < g_ms_method_hook_count; i++)
        if (original == g_ms_method_hooks[i].hook) {
            os_unfair_lock_unlock(&g_ms_method_hook_lock); return true; /* inherited, already owned */
        }
    /* Capture the original in each IMP. Super calls cannot select a child's original. */
    IMP replacement = NULL;
    if (strcmp(signature, "@@:") == 0)
        replacement = imp_implementationWithBlock((id)^(id self) { return ((id (*)(id, SEL, IMP))handler)(self, selector, original); });
    else if (strcmp(signature, "@@:@") == 0)
        replacement = imp_implementationWithBlock((id)^(id self, id arg) { return ((id (*)(id, SEL, id, IMP))handler)(self, selector, arg, original); });
    else if (strcmp(signature, "@@:Q") == 0)
        replacement = imp_implementationWithBlock((id)^(id self, unsigned long arg) { return ((id (*)(id, SEL, unsigned long, IMP))handler)(self, selector, arg, original); });
    else if (strcmp(signature, "v@:") == 0)
        replacement = imp_implementationWithBlock((id)^(id self) { ((void (*)(id, SEL, IMP))handler)(self, selector, original); });
    else if (strcmp(signature, "v@:@") == 0)
        replacement = imp_implementationWithBlock((id)^(id self, id arg) { ((void (*)(id, SEL, id, IMP))handler)(self, selector, arg, original); });
    else if (strcmp(signature, "v@:d") == 0)
        replacement = imp_implementationWithBlock((id)^(id self, double arg) { ((void (*)(id, SEL, double, IMP))handler)(self, selector, arg, original); });
    else if (strcmp(signature, "v@:@d") == 0)
        replacement = imp_implementationWithBlock((id)^(id self, id arg, double number) { ((void (*)(id, SEL, id, double, IMP))handler)(self, selector, arg, number, original); });
    if (!replacement) {
        os_unfair_lock_unlock(&g_ms_method_hook_lock); return false;
    }
    class_addMethod(cls, selector, original, method_getTypeEncoding(method));
    method = class_getInstanceMethod(cls, selector);
    g_ms_method_hooks[g_ms_method_hook_count++] = (MSMethodHook){cls, selector, original, replacement};
    method_setImplementation(method, replacement);
    os_unfair_lock_unlock(&g_ms_method_hook_lock);
    runtime_note("[HOOK][CLASS] class=%s selector=%s abi=VERIFIED status=INSTALLED", class_getName(cls), name);
    return true;
}
