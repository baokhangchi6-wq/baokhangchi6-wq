#include <string.h>
#include "ios_frame_policy.h"
#include <assert.h>
#include <stdio.h>

int main(void) {
    assert(ms_ios_xml_fps("<int name=\"FramerateCap\">1200</int>", 60) == 1200);
    assert(ms_ios_xml_fps("<int name=\"FramerateCap\">9999</int>", 60) == 9999);
    assert(ms_ios_xml_fps("<int name=\"FramerateCap\">1200oops</int>", 60) == 60);
    assert(ms_ios_xml_fps("<int name=\"FramerateCap\">99999999999999999999</int>", 60) == 60);
    assert(ms_ios_xml_fps("<int name=\"FramerateCap\">-1</int>", 60) == 60);
    int engine_target = ms_ios_engine_fps(1200);
    assert(engine_target == 1200);
    assert(ms_ios_target_fps(engine_target, 60, 0, 0) == 60);
    assert(ms_ios_target_fps(engine_target, 120, 0, 0) == 120);
    assert(ms_ios_target_fps(engine_target, 120, 1, 0) == 60);
    assert(engine_target == 1200); /* Display/Low Power policy cannot change engine throughput. */
    assert(ms_ios_engine_fps(9999) == 9999);
    assert(ms_ios_engine_fps(10000) == 9999);
    assert(ms_ios_engine_fps(1) == 30);
    assert(ms_ios_target_fps(240, 120, 0, 0) == 120);
    assert(ms_ios_target_fps(240, 60, 0, 0) == 60);
    assert(ms_ios_target_fps(120, 120, 1, 0) == 60);
    assert(ms_ios_target_fps(120, 120, 0, 2) == 60);
    assert(ms_ios_target_fps(120, 120, 0, 3) == 30);
    assert(ms_ios_target_fps(30, 120, 1, 0) == 30);
    assert(ms_ios_target_fps(0, 120, 0, 0) == 120);
    assert(ms_ios_target_fps(240, -1, 0, 0) == 60);
    assert(ms_ios_target_fps(120, 120, 0, 1) == 120);
    puts("PASS: display policy respects Low Power and thermal limits; engine target unchanged");
    return 0;
}
