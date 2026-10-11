/* Only RobloxLib's verified imports are rebound; stock assets remain unchanged. */
extern char* realpath(const char*, char*);
extern int access(const char*, int);
extern int open(const char*, int, ...);
extern unsigned int mach_task_self_;
extern int mach_vm_region(unsigned int, uint64_t*, uint64_t*, int, int*, unsigned int*, unsigned int*);
extern int mach_port_deallocate(unsigned int, unsigned int);
static os_unfair_lock g_font_assets_lock = OS_UNFAIR_LOCK_INIT;
static char g_font_assets_root[1024], g_font_target_path[1024];
static unsigned g_font_imports_installed, g_font_remap_count;
static void font_write_receipt_main(void* unused) {
    (void)unused;
    SESSION_LOCK;
    os_unfair_lock_lock(&g_font_assets_lock);
    bool configured = g_font_target_path[0] != 0;
    os_unfair_lock_unlock(&g_font_assets_lock);
    unsigned remaps = __atomic_load_n(&g_font_remap_count, __ATOMIC_RELAXED);
    char content[768];
    snprintf(content, sizeof(content), "{\"schema\":1,\"generatedAtUnix\":%ld,\"request\":\"%s\",\"configured\":%s,\"importsInstalled\":%u,\"remapCount\":%u,\"familyStatus\":\"%s\",\"sizeStep\":%d,\"sizeStatus\":\"%s\",\"visualVerified\":false}\n",
        time(NULL), g_applied_request, configured ? "true" : "false", g_font_imports_installed, remaps,
        remaps ? "OBSERVED_ASSET_REMAP" : configured ? "REQUIRES_DEVICE_READBACK" : "PASSTHROUGH",
        g_font_size_step, g_font_size_step ? "UNSUPPORTED_KEEP_ORIGINAL" : "UNCHANGED");
    bridge_write_document("MasterstrapFontReceipt.json", content);
}

static bool font_redirect_path(const char* path, char* output, size_t capacity) {
    if (!path || !output || capacity < 1024 || !strstr(path, "/fonts/families/")) return false;
    bool redirect = false;
    os_unfair_lock_lock(&g_font_assets_lock);
    size_t root_length = strlen(g_font_assets_root);
    char canonical[1024];
    if (g_font_target_path[0] && root_length && realpath(path, canonical) &&
        strncmp(canonical, g_font_assets_root, root_length) == 0) {
        const char* name = canonical + root_length;
        bool stock = false;
        for (size_t i = 0; kStockFontFamilies[i]; i++)
            if (strcmp(name, kStockFontFamilies[i]) == 0) { stock = true; break; }
        if (stock && strncmp(name, "Noto", 4) && strncmp(name, "RobloxEmoji", 11) &&
            strcmp(canonical, g_font_target_path)) {
            snprintf(output, capacity, "%s", g_font_target_path);
            redirect = true;
        }
    }
    os_unfair_lock_unlock(&g_font_assets_lock);
    return redirect;
}
static void font_note_remap(void) {
    unsigned count = __atomic_add_fetch(&g_font_remap_count, 1, __ATOMIC_RELAXED);
    if (count <= 16) {
        runtime_note("[FONT][ASSET] remapped=1 sample=%u scope=ROBLOXLIB_STOCK_FAMILY", count);
        dispatch_async_f(&_dispatch_main_q, NULL, font_write_receipt_main);
    }
}
static void* font_fopen_hook(const char* path, const char* mode) {
    char target[1024];
    if (mode && mode[0] == 'r' && !strstr(mode, "+") && font_redirect_path(path, target, sizeof(target))) {
        void* file = fopen(target, mode);
        if (file) { font_note_remap(); return file; }
    }
    return fopen(path, mode);
}
static int font_open_hook(const char* path, int flags, ...) {
    unsigned int permissions = 0;
    if (flags & 0x200) { /* Darwin O_CREAT; retain the original variadic ABI. */
        __builtin_va_list args;
        __builtin_va_start(args, flags); permissions = __builtin_va_arg(args, unsigned int); __builtin_va_end(args);
    }
    char target[1024];
    if (!(flags & (3 | 0x8 | 0x200 | 0x400)) && font_redirect_path(path, target, sizeof(target))) {
        int descriptor = open(target, flags);
        if (descriptor >= 0) { font_note_remap(); return descriptor; }
    }
    return (flags & 0x200) ? open(path, flags, permissions) : open(path, flags);
}
static bool font_rebind_slot(uintptr_t base, uintptr_t rva, uintptr_t replacement) {
    uintptr_t* slot = (uintptr_t*)(base + rva);
    if (__atomic_load_n(slot, __ATOMIC_ACQUIRE) == replacement) return true;
    uint64_t address = (uint64_t)slot, size = 0;
    int region[16] = {0}; unsigned int count = 16, object = 0;
    int status = mach_vm_region(mach_task_self_, &address, &size, 9, region, &count, &object);
    if (object) mach_port_deallocate(mach_task_self_, object);
    if (status || count < 2 || (uint64_t)slot < address || (uint64_t)slot + sizeof(*slot) > address + size ||
        !(region[0] & PROT_READ) || (region[0] & PROT_EXEC) || !(region[1] & PROT_WRITE)) return false;
    uintptr_t page_size = (uintptr_t)getpagesize();
    if (!page_size || (page_size & (page_size - 1)) || rva % sizeof(*slot)) return false;
    uintptr_t page = (uintptr_t)slot & ~(page_size - 1);
    if (mprotect((void*)page, page_size, region[0] | PROT_WRITE)) return false;
    uintptr_t previous = __atomic_exchange_n(slot, replacement, __ATOMIC_ACQ_REL);
    if (mprotect((void*)page, page_size, region[0])) {
        __atomic_store_n(slot, previous, __ATOMIC_RELEASE);
        mprotect((void*)page, page_size, region[0]);
        return false;
    }
    return true;
}
static void apply_font_asset_profile(uintptr_t base) {
    const char* target = NULL;
    if (g_font_enabled && g_launch_authorized && g_profile_loaded)
        for (size_t i = 0; kFontCatalog[i].name; i++)
            if (strcmp(g_font_family, kFontCatalog[i].name) == 0) target = kFontCatalog[i].asset;
    os_unfair_lock_lock(&g_font_assets_lock);
    g_font_target_path[0] = 0;
    __atomic_store_n(&g_font_remap_count, 0, __ATOMIC_RELAXED);
    if (target && base && kEngineIdentityPinned && engine_identity_matches(base)) {
        const char* engine = NULL;
        for (uint32_t i = 0; i < _dyld_image_count(); i++)
            if ((uintptr_t)_dyld_get_image_header(i) == base) { engine = _dyld_get_image_name(i); break; }
        const char* end = engine ? strstr(engine, "/RobloxLib.framework/RobloxLib") : NULL;
        char directory[1024], canonical[1024];
        if (end && strcmp(end, "/RobloxLib.framework/RobloxLib") == 0 && (size_t)(end - engine) < 700) {
            snprintf(directory, sizeof(directory), "%.*s/RobloxLib.framework/content/fonts/families", (int)(end - engine), engine);
            if (realpath(directory, canonical)) {
                snprintf(g_font_assets_root, sizeof(g_font_assets_root), "%s/", canonical);
                char candidate[1024];
                int length = snprintf(candidate, sizeof(candidate), "%s%s", g_font_assets_root, target);
                if (length > 0 && (size_t)length < sizeof(candidate) && realpath(candidate, canonical) &&
                    strncmp(canonical, g_font_assets_root, strlen(g_font_assets_root)) == 0 && access(canonical, 4) == 0)
                    snprintf(g_font_target_path, sizeof(g_font_target_path), "%s", canonical);
            }
        }
        if (g_font_target_path[0]) {
            g_font_imports_installed = 0;
            for (size_t i = 0; kFontReaderSlots[i].rva; i++)
                if (font_rebind_slot(base, kFontReaderSlots[i].rva,
                    kFontReaderSlots[i].kind == 0 ? (uintptr_t)font_fopen_hook : (uintptr_t)font_open_hook)) g_font_imports_installed++;
            if (!g_font_imports_installed) g_font_target_path[0] = 0;
        }
    }
    bool ready = g_font_target_path[0] != 0;
    os_unfair_lock_unlock(&g_font_assets_lock);
    dispatch_async_f(&_dispatch_main_q, NULL, font_write_receipt_main);
    runtime_note("[CAPABILITY][FONT] backend=engine-import-stock-family-v1 status=%s family=%s sizeStep=%d sizeBackend=%s imports=%u runtimeEffect=REQUIRES_DEVICE_READBACK",
        ready ? "READY" : target ? "UNAVAILABLE" : "DEFAULT_PASSTHROUGH", g_font_family, g_font_size_step,
        g_font_size_step ? "UNSUPPORTED_KEEP_ORIGINAL" : "UNCHANGED", g_font_imports_installed);
}
