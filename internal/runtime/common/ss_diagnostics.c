#include "dm.h"
#include "touchable_game.h"
#include <signal.h>
#include <sys/ucontext.h>
#ifdef __ANDROID__
#include <sys/syscall.h>
#include <sys/uio.h>
#include "dm_ucontext.h"
#else
#include <mach/arm/thread_status.h>
#include <mach/mach.h>
#endif
#include <unistd.h>
#include <stdatomic.h>

static _Atomic(int (*)(void *, int, void *)) fault_hook;
static _Atomic(void (*)(void *, size_t)) write_hook;
static void (*resource_report)(uint32_t, const char *);
static void (*wait_dump)(void);
void dm_fault_hook_register(int (*function)(void *, int, void *)) { atomic_store(&fault_hook, function); }
void dm_guest_write_prep_register(void (*function)(void *, size_t)) { atomic_store(&write_hook, function); }
void dm_guest_write_prepare(void *address, size_t size) {
    void (*function)(void *, size_t) = atomic_load(&write_hook);
    if (function) function(address, size);
}
void dm_res_report_register(void (*function)(uint32_t, const char *)) { resource_report = function; }
void (*dm_res_report_cb(void))(uint32_t, const char *) { return resource_report; }
void crash_set_wait_dump(void (*function)(void)) { wait_dump = function; }

static pthread_key_t signal_stack_key;
static pthread_once_t signal_stack_once = PTHREAD_ONCE_INIT;
static void free_signal_stack(void *pointer) {
#ifdef __ANDROID__
    stack_t disabled = {.ss_flags = SS_DISABLE};
    sigaltstack(&disabled, NULL);
#endif
    free(pointer);
}
static void create_signal_key(void) {
    if (pthread_key_create(&signal_stack_key, free_signal_stack)) die("Cannot create signal stack key");
}
void crash_thread_init(void) {
    pthread_once(&signal_stack_once, create_signal_key);
    if (pthread_getspecific(signal_stack_key)) return;
    void *memory = malloc(64 * 1024);
    stack_t stack = {.ss_sp = memory, .ss_size = 64 * 1024};
    if (!memory || sigaltstack(&stack, NULL) || pthread_setspecific(signal_stack_key, memory))
        die("Cannot install guest signal stack");
}
static void describe_address(const char *label, uintptr_t address) {
    module_t *module = module_of((void *)address);
    LOG("%s %s+%#lx (%#lx)", label, module ? module->name : "host",
        module ? address - (uintptr_t)module->base : address, address);
}
void dm_backtrace(uint64_t frame) {
    for (int i = 0; frame && !(frame & 15) && i < 32; ++i) {
        uint64_t pair[2];
#ifdef __ANDROID__
        struct iovec local = {pair, sizeof(pair)};
        struct iovec remote = {(void *)(uintptr_t)frame, sizeof(pair)};
        if (syscall(SYS_process_vm_readv, getpid(), &local, 1, &remote, 1, 0) != sizeof(pair)) break;
#else
        vm_size_t copied = 0;
        if (vm_read_overwrite(mach_task_self(), frame, sizeof(pair), (vm_address_t)pair, &copied) ||
            copied != sizeof(pair)) break;
#endif
        describe_address("frame", pair[1]);
        if (pair[0] <= frame || pair[0] - frame > 16 * 1024 * 1024) break;
        frame = pair[0];
    }
}
/* Diagnostic builds log a crash and stop. They never resume an invalid load. */
static void crash(int number, siginfo_t *info, void *context) {
    ucontext_t *uc = context;
    int (*function)(void *, int, void *) = atomic_load(&fault_hook);
    if ((number == SIGSEGV || number == SIGBUS) && function &&
#ifdef __ANDROID__
        function(info->si_addr, dm_uc_is_write(uc), context)) return;
#else
        function(info->si_addr, (uc->uc_mcontext->__es.__esr >> 6) & 1, context)) return;
#endif
    LOG("CRASH signal=%d address=%p", number, info->si_addr);
#ifdef __ANDROID__
    describe_address("PC", DM_UC_PC(uc));
    describe_address("LR", DM_UC_LR(uc));
    for (int i = 0; i < 29; ++i) LOG("x%d=%#llx", i, (unsigned long long)DM_UC_REG(uc, i));
    dm_backtrace(DM_UC_FP(uc));
#else
    describe_address("PC", arm_thread_state64_get_pc(uc->uc_mcontext->__ss));
    describe_address("LR", arm_thread_state64_get_lr(uc->uc_mcontext->__ss));
    for (int i = 0; i < 29; ++i) LOG("x%d=%#llx", i, uc->uc_mcontext->__ss.__x[i]);
    dm_backtrace(arm_thread_state64_get_fp(uc->uc_mcontext->__ss));
#endif
    _exit(128 + number);
}
void ss_crash_install(void) {
    crash_thread_init();
    struct sigaction action = {0};
    action.sa_flags = SA_SIGINFO | SA_ONSTACK;
    action.sa_sigaction = crash;
    sigemptyset(&action.sa_mask);
    int signals[] = {SIGSEGV, SIGBUS, SIGILL, SIGABRT};
    for (size_t i = 0; i < sizeof(signals) / sizeof(signals[0]); ++i)
        if (sigaction(signals[i], &action, NULL)) die("Cannot install crash handler");
}

/* Optional upstream observation hooks are unused by this host. */
void dm_prof_add(int id, uint64_t ns, uint64_t bytes) { (void)id; (void)ns; (void)bytes; }
void dm_prof_mark(const char *stage) { LOG("BOOT %s", stage); }
void uimsg_subscribe(void (*callback)(const char *, const char *, const char *)) { (void)callback; }
void crash_watch_frame(void) {}
uint64_t svc_host_call(int number, uint64_t *registers) {
    if (touchable_game_host_call(number, registers)) return 1;
    die("Unhandled game host call: %#x", number);
}
