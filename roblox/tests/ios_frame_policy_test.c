#include "ios_frame_policy.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    assert(ms_ios_target_fps(240, 120, 0, 0) == 120);
    assert(ms_ios_target_fps(240, 60, 0, 0) == 60);
    assert(ms_ios_target_fps(120, 120, 1, 0) == 60);
    assert(ms_ios_target_fps(120, 120, 0, 2) == 60);
    assert(ms_ios_target_fps(120, 120, 0, 3) == 30);
    assert(ms_ios_target_fps(30, 120, 1, 0) == 30);
    assert(ms_ios_target_fps(0, 120, 0, 0) == 120);
    assert(ms_ios_target_fps(240, -1, 0, 0) == 60);
    assert(ms_ios_target_fps(120, 120, 0, 1) == 120);
    puts("PASS: iOS requested FPS bounds, low-power and thermal policy (not UIKit runtime)");
    return 0;
}
