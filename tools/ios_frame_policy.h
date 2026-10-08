/* Pure policy: requesting a rate is not proof of rendered/presented FPS. */
static int ms_ios_target_fps(int requested, int maximum, int low_power, int thermal) {
    (void)low_power;
    (void)maximum;
    if (requested < 1) requested = 10000;
    /* Android parity: never clamp engine target to screen maximum */
    /* Keep system thermal protection; never attempt an overclock. */
    if (thermal >= 3 && requested > 30) requested = 30;
    return requested;
}
