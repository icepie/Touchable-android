/* Stop the shared audio renderer and Vulkan work before Android exits the process. */
#include "hle.h"
#include <stdatomic.h>
#include <unistd.h>

void dm_host_shutdown(const char *reason) {
    static _Atomic int shutting_down;
    if (atomic_exchange(&shutting_down, 1)) return;
    LOG("Android shutdown: %s", reason);
    dm_audio_suspend(1);
    dm_gfx_suspend(1);
    LOG("Android graphics and audio stopped");
    _exit(0);
}
