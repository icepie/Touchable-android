#ifndef SS_ANDROID_TOUCH_H
#define SS_ANDROID_TOUCH_H
#include <android/input.h>
#include <stdint.h>

/* These calls may arrive on the Java UI thread, native input thread or controller thread;
   the implementation serializes its state and publishes a complete draw snapshot. */
void ss_android_touch_metrics(int width, int height, float density, float left, float top, float right, float bottom);
void ss_android_touch_pad(uint64_t buttons, int32_t x, int32_t y, int controller_mode);
int ss_android_touch_motion(const AInputEvent *event);
void ss_android_touch_cancel(void);
/* Refresh scene hints and pointer token, then return 1 if controls should be drawn. */
int ss_android_touch_tick(void);
/* Snapshot: width,height,left zone's left/right/top/bottom,layout,first,second;
   then nine groups of bit,x,y,size,appearance,pressed (see RhythmOverlay);
   then safe top and sixteen touch previews of bit,x,y,alpha. */
int ss_android_touch_draw(float *out, int capacity);
#endif
