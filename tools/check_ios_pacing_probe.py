"""Run actual libc++ waits and prove the observer preserves locking/errors."""
from pathlib import Path
import subprocess
import sys
import tempfile

def main():
    if sys.platform != 'darwin': raise SystemExit('Pacing ABI verification requires macOS libc++')
    harness = r'''
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>
#include <system_error>
#include <cstdint>
extern "C" int ms_pacing_prepare();
extern "C" void ms_condition_wait(std::condition_variable*, std::unique_lock<std::mutex>&);
extern "C" void ms_condition_timed_wait(std::condition_variable*, std::unique_lock<std::mutex>&,
    std::chrono::time_point<std::chrono::system_clock,std::chrono::nanoseconds>);
static unsigned counts[2];
extern "C" unsigned ms_pacing_generation() { return 1; }
extern "C" void ms_pacing_record(int kind, uintptr_t caller, uint64_t elapsed, unsigned generation) {
    assert(caller && generation == 1 && kind >= 0 && kind <= 1); (void)elapsed; counts[kind]++;
}
int main() {
    assert(ms_pacing_prepare() == 3); assert(ms_pacing_prepare() == 3);
    std::condition_variable condition;
    std::mutex mutex;
    std::unique_lock<std::mutex> lock(mutex);
    bool ready = false;
    std::thread notify([&] { std::unique_lock<std::mutex> other(mutex); ready = true; condition.notify_one(); });
    while (!ready) ms_condition_wait(&condition, lock);
    assert(lock.owns_lock() && counts[0] > 0); notify.join();
    auto deadline = std::chrono::time_point_cast<std::chrono::nanoseconds>(std::chrono::system_clock::now()) + std::chrono::milliseconds(5);
    do { ms_condition_timed_wait(&condition, lock, deadline); } while (std::chrono::system_clock::now() < deadline);
    assert(lock.owns_lock() && counts[1] >= 1);
    assert(std::chrono::system_clock::now() >= deadline);
    lock.unlock();
    int expected = 0, observed = 0;
    try { condition.wait(lock); } catch (const std::system_error& error) { expected = error.code().value(); }
    try { ms_condition_wait(&condition, lock); } catch (const std::system_error& error) { observed = error.code().value(); }
    assert(expected != 0 && observed == expected);
}
'''
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'check.cpp').write_text(harness, encoding='utf-8')
        subprocess.run(['xcrun', 'clang++', '-std=c++17', str(root / 'check.cpp'), str(Path(__file__).parent / 'ios_pacing_probe.cpp'), '-o', str(root / 'check')], check=True)
        subprocess.run([str(root / 'check')], check=True)
    print('PASS pacing probe: real libc++ wait/timeout, mutex ownership, original error propagation and repeat preparation')

if __name__ == '__main__': main()
