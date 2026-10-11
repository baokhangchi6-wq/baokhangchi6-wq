/* Slots live in the build-created RW segment; no runtime executable memory changes. */
static os_unfair_lock g_render_role_lock = OS_UNFAIR_LOCK_INIT;
static unsigned g_render_role_generation;
static double g_render_role_started;
static unsigned long g_render_jobs, g_render_jobs_with_drawables, g_render_drawables;
static double g_render_job_sum, g_render_drawable_sum;
static _Thread_local double g_render_job_started, g_render_job_wait;
static _Thread_local unsigned g_render_job_epoch, g_render_job_drawables;
static _Thread_local unsigned g_render_job_depth;

static void ms_render_job_enter(void) {
    unsigned epoch = __atomic_load_n(&g_render_role_generation, __ATOMIC_ACQUIRE);
    if (!epoch) return;
    if (g_render_job_depth++) return;
    g_render_job_started = ms_probe_time(); g_render_job_wait = 0; g_render_job_drawables = 0;
    g_render_job_epoch = epoch;
}
static void ms_render_job_drawable(double seconds) {
    if (!g_render_job_depth || seconds < 0) return;
    g_render_job_wait += seconds; g_render_job_drawables++;
}
static void ms_render_job_exit(void) {
    if (!g_render_job_depth || --g_render_job_depth) return;
    double elapsed = ms_probe_time() - g_render_job_started;
    os_unfair_lock_lock(&g_render_role_lock);
    if (g_render_job_epoch == g_render_role_generation && elapsed >= 0) {
        g_render_jobs++; g_render_job_sum += elapsed;
        if (g_render_job_drawables) g_render_jobs_with_drawables++;
        g_render_drawables += g_render_job_drawables; g_render_drawable_sum += g_render_job_wait;
    }
    os_unfair_lock_unlock(&g_render_role_lock);
}
static void ms_bind_render_roles(uintptr_t base, unsigned generation) {
    __atomic_store_n(&g_render_role_generation, 0, __ATOMIC_RELEASE);
    if (!kRenderRolesStateRva || !base || !engine_identity_matches(base)) return;
    const MachHeader64* header = (const MachHeader64*)base;
    const uint8_t* cursor = (const uint8_t*)(header + 1);
    const uint8_t* end = cursor + header->sizeofcmds;
    const MachSegment64* state = NULL; uint64_t origin = 0;
    for (uint32_t i = 0; i < header->ncmds; i++) {
        if (cursor + sizeof(MachLoadCommand) > end) return;
        const MachLoadCommand* command = (const MachLoadCommand*)cursor;
        if (command->cmdsize < sizeof(*command) || command->cmdsize > (size_t)(end-cursor)) return;
        if (command->cmd == LC_SEGMENT_64 && command->cmdsize >= sizeof(MachSegment64)) {
            const MachSegment64* segment = (const MachSegment64*)cursor;
            if (!segment->fileoff && segment->filesize) origin = segment->vmaddr;
            if (strncmp(segment->segname, "__MSDATA", 16) == 0) state = segment;
        }
        cursor += command->cmdsize;
    }
    if (!state || state->vmaddr < origin || state->vmaddr-origin != kRenderRolesStateRva ||
        state->vmsize < 40 || state->initprot != 3 || state->maxprot != 3) return;
    uintptr_t* slots = (uintptr_t*)(base+kRenderRolesStateRva);
    if (slots[0] != 0x4D53524F4C455331ULL) return;
    os_unfair_lock_lock(&g_render_role_lock);
    g_render_jobs = g_render_jobs_with_drawables = g_render_drawables = 0;
    g_render_job_sum = g_render_drawable_sum = 0; g_render_role_started = ms_probe_time();
    __atomic_store_n(&g_render_role_generation, generation, __ATOMIC_RELEASE);
    os_unfair_lock_unlock(&g_render_role_lock);
    __atomic_store_n(&slots[2], (uintptr_t)ms_stretch_ui_width, __ATOMIC_RELEASE);
    __atomic_store_n(&slots[3], (uintptr_t)ms_render_job_enter, __ATOMIC_RELEASE);
    __atomic_store_n(&slots[4], (uintptr_t)ms_render_job_exit, __ATOMIC_RELEASE);
    runtime_note("[RENDER][ROLES] uiWidth=BOUND renderJob=BOUND state=RW_DATA executableWrites=0 generation=%u",generation);
}
static void ms_render_roles_write_receipt(void) {
    os_unfair_lock_lock(&g_render_role_lock);
    double elapsed = ms_probe_time()-g_render_role_started;
    unsigned long jobs=g_render_jobs, paired=g_render_jobs_with_drawables, draws=g_render_drawables;
    double total=g_render_job_sum, wait=g_render_drawable_sum;
    bool active=g_render_role_generation != 0;
    os_unfair_lock_unlock(&g_render_role_lock);
    if (!active || elapsed <= 0) return;
    char content[1024];
    snprintf(content,sizeof(content),"{\"schema\":1,\"request\":\"%s\",\"pid\":%d,\"scope\":\"SurfaceController::RenderJob::scheduleRender\",\"elapsedSeconds\":%.3f,\"completedJobs\":%lu,\"jobsPerSecond\":%.3f,\"jobsWithDrawables\":%lu,\"drawablesInsideJobs\":%lu,\"averageJobMs\":%.3f,\"averageDrawableWaitMs\":%.3f,\"engineFps\":null,\"synchronizationChanged\":false}\n",
        g_applied_request,getpid(),elapsed,jobs,jobs/elapsed,paired,draws,jobs ? total*1000/jobs : 0,draws ? wait*1000/draws : 0);
    bridge_write_document("MasterstrapRenderJobReceipt.json",content);
}
