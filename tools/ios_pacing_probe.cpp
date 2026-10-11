// Observe libc++ waits; preserve arguments, synchronization and exceptions.
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <dlfcn.h>
#include <cstdint>

using Wait = void (*)(std::condition_variable*, std::unique_lock<std::mutex>&);
using TimedWait = void (*)(std::condition_variable*, std::unique_lock<std::mutex>&,
    std::chrono::time_point<std::chrono::system_clock, std::chrono::nanoseconds>);
static_assert(sizeof(std::chrono::time_point<std::chrono::system_clock, std::chrono::nanoseconds>) == sizeof(int64_t), "libc++ deadline ABI");
static Wait original_wait;
static TimedWait original_timed_wait;
static std::once_flag prepare_once;
#ifdef MASTERSTRAP_PACING_TEST
extern "C" void ms_pacing_test_set_wait(Wait function) { original_wait = function; }
#endif
extern "C" unsigned ms_pacing_generation();
extern "C" void ms_pacing_record(int, uintptr_t, uint64_t, unsigned);

extern "C" int ms_pacing_prepare() {
    std::call_once(prepare_once, [] {
    original_wait = reinterpret_cast<Wait>(dlsym(RTLD_DEFAULT, "_ZNSt3__118condition_variable4waitERNS_11unique_lockINS_5mutexEEE"));
    original_timed_wait = reinterpret_cast<TimedWait>(dlsym(RTLD_DEFAULT, "_ZNSt3__118condition_variable15__do_timed_waitERNS_11unique_lockINS_5mutexEEENS_6chrono10time_pointINS5_12system_clockENS5_8durationIxNS_5ratioILl1ELl1000000000EEEEEEE"));
    });
    return (original_wait ? 1 : 0) | (original_timed_wait ? 2 : 0);
}
struct WaitSample {
    int kind;
    uintptr_t caller;
    unsigned generation = ms_pacing_generation();
    std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();
    ~WaitSample() {
        if (generation) ms_pacing_record(kind, caller,
            std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now() - start).count(), generation);
    }
};
extern "C" void ms_condition_wait(std::condition_variable* condition, std::unique_lock<std::mutex>& lock) {
    WaitSample sample{0, reinterpret_cast<uintptr_t>(__builtin_return_address(0))};
    original_wait(condition, lock);
}
extern "C" void ms_condition_timed_wait(std::condition_variable* condition, std::unique_lock<std::mutex>& lock,
    std::chrono::time_point<std::chrono::system_clock, std::chrono::nanoseconds> deadline) {
    WaitSample sample{1, reinterpret_cast<uintptr_t>(__builtin_return_address(0))};
    original_timed_wait(condition, lock, deadline);
}
