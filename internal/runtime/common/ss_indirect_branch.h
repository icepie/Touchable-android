#ifndef SS_INDIRECT_BRANCH_H
#define SS_INDIRECT_BRANCH_H

#include <pthread.h>
#include <stddef.h>
#include <stdint.h>

/* Registered X18 BRK #0x6e18 instruction sites on arm64 Apple or Android.
 * Configure before guest threads start; do not change the table while guest
 * code runs. The caller owns the virtual x18 pthread key for the runtime.
 * On Android the key must be bound to Akari's DM_K18 context slot; guest
 * threads must initialize that context before entering rewritten code.
 * Give each guest thread an alternate signal stack before guest execution.
 */
typedef enum { SS_BR_X18 = 1, SS_MOV_SP_X18 = 2, SS_BLR_X18 = 3 } ss_trap_kind;
typedef struct {
    uintptr_t address;
    uintptr_t resume; /* Original instruction + 4 for MOV SP, X18 or BLR X18. */
    ss_trap_kind kind;
} ss_trap_site;
int ss_indirect_install(const ss_trap_site *sites, size_t count,
                        pthread_key_t x18_key);
/* Call only after every guest thread has stopped. */
int ss_indirect_uninstall(void);

#endif
