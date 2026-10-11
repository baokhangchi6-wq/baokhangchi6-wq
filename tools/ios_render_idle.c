/* Disable only a verified minimum-idle guard; preserve explicit user flags. */
static void apply_render_idle_policy(uintptr_t base) {
    if (!kRenderIdleVerified || !base || !g_profile_loaded || g_target_fps <= 60 || !engine_identity_matches(base)) return;
    const MasterFlagEntry* idle = lookup_flag_with_fallback("FFlagMainThreadIdle");
    const MasterFlagEntry* minimum = lookup_flag_with_fallback("FIntMainThreadMinIdleTimeUs");
    if (!idle || idle->kind != 0 || idle->rva != kRenderIdleFlagRva || !minimum || minimum->kind != 1 || minimum->rva != kRenderIdleMinimumRva) return;
    for (size_t i = 0; i < g_cached_dynamic_count; i++)
        if (g_cached_dynamic_flags[i].flag->rva == idle->rva) {
            runtime_note("[FPS][RENDER_IDLE] status=USER_CONFIG_PRESERVED requested=%d", g_target_fps); return;
        }
    int before = *(volatile uint8_t*)(base + idle->rva);
    int microseconds = *(volatile int32_t*)(base + minimum->rva);
    if (before != 0 && before != 1) return;
    FFlagPatchEntry entry = {idle->name, idle->rva, 0, 0};
    bool written = SetFastFlag(base, &entry);
    if (written) cache_dynamic_flag(idle, 0, 0);
    runtime_note("[FPS][RENDER_IDLE] status=%s before=%d after=%d minimumIdleUs=%d requested=%d source=VERIFIED_BINARY_ROLE", written ? "DISABLED_MINIMUM_IDLE" : "WRITE_FAILED", before, *(volatile uint8_t*)(base + idle->rva), microseconds, g_target_fps);
}
