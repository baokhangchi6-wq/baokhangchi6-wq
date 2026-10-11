/* Only verified RobloxLib wait imports; never change a wait deadline or result. */
extern int ms_pacing_prepare(void);
extern void ms_condition_wait(void*, void*);
extern void ms_condition_timed_wait(void*, void*, int64_t);
static os_unfair_lock g_pacing_lock = OS_UNFAIR_LOCK_INIT;
static uintptr_t g_pacing_base;
static unsigned g_pacing_epoch;
typedef struct { unsigned long calls; uint64_t sum, maximum; } MSPacingCounter;
static MSPacingCounter g_pacing_counters[sizeof(kPacingCallers) / sizeof(kPacingCallers[0])];

unsigned ms_pacing_generation(void) { return __atomic_load_n(&g_pacing_epoch, __ATOMIC_ACQUIRE); }
void ms_pacing_record(int kind, uintptr_t caller, uint64_t nanoseconds, unsigned generation) {
    (void)kind;
    os_unfair_lock_lock(&g_pacing_lock);
    if (generation != ms_pacing_generation() || !g_pacing_base || caller < g_pacing_base) { os_unfair_lock_unlock(&g_pacing_lock); return; }
    uintptr_t rva = caller - g_pacing_base;
    for (size_t i = 0; kPacingCallers[i].call; i++) {
        if (rva != kPacingCallers[i].call + 4) continue;
        MSPacingCounter* counter = &g_pacing_counters[i];
        counter->calls++; counter->sum += nanoseconds;
        if (nanoseconds > counter->maximum) counter->maximum = nanoseconds;
        break;
    }
    os_unfair_lock_unlock(&g_pacing_lock);
}
static void ms_pacing_write_receipt(void) {
    SESSION_LOCK;
    char content[16384];
    os_unfair_lock_lock(&g_pacing_lock);
    if (!ms_pacing_generation()) { os_unfair_lock_unlock(&g_pacing_lock); return; }
    int length = snprintf(content, sizeof(content), "{\"schema\":1,\"request\":\"%s\",\"pid\":%d,\"generatedAtUnix\":%ld,\"mode\":\"observe-only-preserve-wait\",\"engineFps\":null,\"waits\":[", g_applied_request, getpid(), time(NULL));
    bool first = true;
    for (size_t i = 0; kPacingCallers[i].call; i++) {
        MSPacingCounter counter = g_pacing_counters[i];
        if (!counter.calls) continue;
        double average = (double)counter.sum / counter.calls / 1000000, maximum = (double)counter.maximum / 1000000;
        int added = snprintf(content + length, sizeof(content) - (size_t)length,
            "%s{\"callerRva\":%lu,\"functionRva\":%lu,\"calls\":%lu,\"averageMs\":%.3f,\"maximumMs\":%.3f}",
            first ? "" : ",", (unsigned long)kPacingCallers[i].call, (unsigned long)kPacingCallers[i].start, counter.calls, average, maximum);
        if (added < 0 || (size_t)added >= sizeof(content) - (size_t)length - 4) break;
        length += added; first = false;
    }
    if (length > 0 && (size_t)length + 4 < sizeof(content)) {
        snprintf(content + length, sizeof(content) - (size_t)length, "]}\n");
        bridge_write_document("MasterstrapPacingReceipt.json", content);
    }
    os_unfair_lock_unlock(&g_pacing_lock);
}
static void ms_install_pacing_probe(uintptr_t base, unsigned generation) {
    __atomic_store_n(&g_pacing_epoch, 0, __ATOMIC_RELEASE);
    if (!base || !kEngineIdentityPinned || !engine_identity_matches(base)) return;
    int ready = ms_pacing_prepare();
    unsigned installed = 0;
    for (size_t i = 0; kPacingSlots[i].rva; i++) {
        int kind = kPacingSlots[i].kind;
        if (!(ready & (1 << kind))) continue;
        if (font_rebind_slot(base, kPacingSlots[i].rva,
            kind ? (uintptr_t)ms_condition_timed_wait : (uintptr_t)ms_condition_wait)) installed++;
    }
    os_unfair_lock_lock(&g_pacing_lock);
    g_pacing_base = base;
    for (size_t i = 0; i < sizeof(g_pacing_counters) / sizeof(g_pacing_counters[0]); i++) g_pacing_counters[i] = (MSPacingCounter){0};
    __atomic_store_n(&g_pacing_epoch, installed ? generation : 0, __ATOMIC_RELEASE);
    os_unfair_lock_unlock(&g_pacing_lock);
    runtime_note("[PACING][IMPORT] installed=%u resolvedABIs=%d mode=OBSERVE_ONLY originalWaits=PRESERVED", installed, ready);
}
