/* Feed the existing Android gamepad rumble worker from Touchable's Switch HID. */
#include "dm_input.h"
#include "ss_haptics.h"
#include <pthread.h>
#include <string.h>
#include <errno.h>
#include <time.h>

static pthread_mutex_t phone_lock = PTHREAD_MUTEX_INITIALIZER;
static ss_haptic_output phone_output;
static uint64_t phone_sequence;
static pthread_cond_t phone_changed = PTHREAD_COND_INITIALIZER;

/* The common mixer calls this only when its mono output changes.  The
 * controller worker forwards the latest value to the Activity's Vibrator;
 * keeping the sink here avoids doing JNI work while the guest holds the
 * shared haptics mutex. */
void ss_haptics_platform_apply(ss_haptic_output output) {
 pthread_mutex_lock(&phone_lock);
 phone_output = output;
 ++phone_sequence;
 pthread_cond_signal(&phone_changed);
 pthread_mutex_unlock(&phone_lock);
}

void dm_android_haptics_read(ss_haptic_output *out, uint64_t *sequence) {
 if (!out || !sequence) return;
 pthread_mutex_lock(&phone_lock);
 *out = phone_output;
 *sequence = phone_sequence;
 pthread_mutex_unlock(&phone_lock);
}

void dm_android_haptics_wait(uint64_t sequence, unsigned milliseconds) {
 struct timespec deadline;
 clock_gettime(CLOCK_REALTIME, &deadline);
 deadline.tv_sec += (time_t)(milliseconds / 1000);
 deadline.tv_nsec += (long)(milliseconds % 1000) * 1000000L;
 if (deadline.tv_nsec >= 1000000000L) { ++deadline.tv_sec; deadline.tv_nsec -= 1000000000L; }
 pthread_mutex_lock(&phone_lock);
 while (phone_sequence == sequence) {
  if (pthread_cond_timedwait(&phone_changed, &phone_lock, &deadline) == ETIMEDOUT) break;
 }
 pthread_mutex_unlock(&phone_lock);
}

static pthread_mutex_t test_lock = PTHREAD_MUTEX_INITIALIZER;
static ss_vibration_value test_values[2];
static int test_hold;
static uint64_t test_sends;

void dm_vibration_enable(int on) { ss_haptics_configure(on); }
void dm_vibration_test_hold(int on) {
    pthread_mutex_lock(&test_lock);
    test_hold = !!on;
    if (on) memset(test_values, 0, sizeof test_values);
    ++test_sends;
    pthread_mutex_unlock(&test_lock);
}
void dm_vibration_test(int side, const float value[4]) {
    if (side < 0 || side > 1 || !value) return;
    pthread_mutex_lock(&test_lock);
    if (test_hold) {
        memcpy(&test_values[side], value, sizeof test_values[side]);
        ++test_sends;
    }
    pthread_mutex_unlock(&test_lock);
}
void dm_vibration_take(dm_vib_snapshot *out) {
    ss_haptic_status status = ss_haptics_status();
    ss_vibration_value values[2] = {
        ss_haptics_actual(SS_VIBRATION_LEFT), ss_haptics_actual(SS_VIBRATION_RIGHT)
    };
    pthread_mutex_lock(&test_lock);
    if (test_hold) memcpy(values, test_values, sizeof values);
    uint64_t sends = status.requests + test_sends;
    pthread_mutex_unlock(&test_lock);
    memset(out, 0, sizeof *out);
    memcpy(out->v, values, sizeof values);
    out->sends = sends;
    out->seq = status.output_changes;
    for (int side = 0; side < 2; ++side)
        out->peak[side] = values[side].amplitude_low > values[side].amplitude_high ?
            values[side].amplitude_low : values[side].amplitude_high;
}
