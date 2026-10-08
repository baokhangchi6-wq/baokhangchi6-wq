#pragma once

/* Pure policy: requesting a rate is not proof of rendered/presented FPS. */
static int ms_ios_target_fps(int requested, int maximum, int low_power, int thermal) {
    if (maximum < 30 || maximum > 240) maximum = 60;
    if (requested < 1) requested = maximum;
    if (requested > maximum) requested = maximum;
    /* Keep system thermal protection; never attempt an overclock. */
    if (thermal >= 2 && requested > 60) requested = 60;
    if (thermal >= 3 && requested > 30) requested = 30;
    return requested;
}
