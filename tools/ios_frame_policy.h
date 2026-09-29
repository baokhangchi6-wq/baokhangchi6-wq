#pragma once

/* Engine throughput and display refresh are different limits. */
static int ms_ios_engine_fps(int requested) {
    if (requested <= 0 || requested > 9999) return 9999;
    return requested < 30 ? 30 : requested;
}

/* Called with string.h declarations available in both the native template and the check. */
static int ms_ios_xml_fps(const char* xml, int fallback) {
    const char* tag = "<int name=\"FramerateCap\">";
    const char* value = xml ? strstr(xml, tag) : 0;
    if (!value) return fallback;
    value += strlen(tag);
    int target = 0, digits = 0;
    while (*value >= '0' && *value <= '9') {
        int digit = *value++ - '0';
        if (target > (9999 - digit) / 10) return fallback;
        target = target * 10 + digit;
        digits++;
    }
    return digits && strncmp(value, "</int>", 6) == 0 && target >= 30 ? target : fallback;
}

/* Bound CADisplayLink to the panel only. Low Power is an OS hint; applying a
 * second 60 FPS clamp here reintroduces the Roblox refresh cap we removed. */
static int ms_ios_target_fps(int requested, int maximum, int low_power, int thermal) {
    (void)low_power;
    if (maximum < 30 || maximum > 240) maximum = 60;
    if (requested < 1) requested = maximum;
    if (requested > maximum) requested = maximum;
    if (thermal >= 2 && requested > 60) requested = 60;
    if (thermal >= 3 && requested > 30) requested = 30;
    return requested;
}
