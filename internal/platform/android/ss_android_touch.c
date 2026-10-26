/* Android counterpart of the iOS rhythm layout: NativeActivity owns all touches, the Java
   panel only paints. A pointer token change releases stale menu/hint fingers immediately. */
#include "ss_android_touch.h"
#include "ss_pointer.h"
#include "ss_touch.h"
#include "ss_display.h"
#include <math.h>
#include <string.h>
#include <pthread.h>
#include <time.h>

enum { FINGERS = 16, BUTTONS = 9, NONE, ZONE_LEFT, ZONE_A, POINTER_MENU, POINTER_BUTTON };
typedef struct { int id, role, bit; float x, y; uint64_t released; } finger;
typedef struct { int bit; float x, y, size; int visible; } control;
static struct {
    int width, height, controls, mode, layout, first, second, extra, pointer_mode, a_visible, demo_bit, demo_active, paused, initialized;
    float density, safe_l, safe_t, safe_r, safe_b, left_l, left_r, zone_top, zone_bottom, half;
    uint64_t hint, token, pad;
    int32_t axis_x, axis_y;
    finger fingers[FINGERS];
    control buttons[BUTTONS];
    int menu_id;
} T = {.density = 1, .demo_bit = 3, .menu_id = -1};
static pthread_mutex_t touch_lock=PTHREAD_MUTEX_INITIALIZER;
void ss_input_set(uint64_t buttons, int32_t x, int32_t y);

static void publish(void) {
    if (!T.initialized) {
        for (int i=0;i<FINGERS;i++) T.fingers[i]=(finger){.id=-1,.bit=-1};
        T.initialized=1;
    }
    uint64_t held = T.pad;
    for (int i = 0; i < FINGERS; ++i) if (T.fingers[i].id >= 0 && T.fingers[i].bit >= 0)
        held |= 1ull << T.fingers[i].bit;
    ss_input_set(held, T.axis_x, T.axis_y);
}
static void reset_fingers(void) {
    if (T.menu_id >= 0 && T.token) ss_pointer_push((ss_pointer_event){T.token, SS_POINTER_CANCEL, 0, 0});
    T.initialized=1;
    for (int i = 0; i < FINGERS; ++i) T.fingers[i] = (finger){.id = -1, .bit = -1};
    T.menu_id = -1;
    publish();
}
void ss_android_touch_cancel(void) {
    pthread_mutex_lock(&touch_lock);reset_fingers();pthread_mutex_unlock(&touch_lock);
}
void ss_android_touch_pad(uint64_t buttons, int32_t x, int32_t y, int controller_mode) {
    pthread_mutex_lock(&touch_lock);
    (void)controller_mode; /* iOS keeps the rhythm controls when a controller is used. */
    T.pad = buttons; T.axis_x = x; T.axis_y = y; publish();
    pthread_mutex_unlock(&touch_lock);
}
static float maximum(float a, float b) { return a > b ? a : b; }
static float minimum(float a, float b) { return a < b ? a : b; }
static void layout(void) {
    float w = T.width, h = T.height, d = T.density;
    float game_w = minimum(w, h * 16 / 9), game_h = game_w * 9 / 16;
    int vertical = (h - game_h) / 2 >= 44 * d;
    float content_l = vertical ? (w - game_w) / 2 : 0;
    float content_r = vertical ? (w + game_w) / 2 : w;
    float content_b = vertical ? (h + game_h) / 2 : h;
    T.half = (content_l + content_r) / 2;
    T.zone_top = maximum(T.safe_t, 0) + 60 * d;
    T.zone_bottom = content_b - 6 * d;
    T.left_l = content_l + 4 * d; T.left_r = T.half - 4 * d;
    float left = maximum(T.safe_l + 12 * d, 28 * d), right = w - maximum(T.safe_r + 12 * d, 28 * d);
    float bottom = h - maximum(T.safe_b + 18 * d, 36 * d);
    float control_bottom = vertical ? content_b : bottom;
    float cx = left + 74 * d, cy = control_bottom - 74 * d;
    float bar = (w - game_w) / 2;
    int side_fits = bar >= 52 * d || vertical;
    float lx = bar >= 52 * d ? bar / 2 : left + 30 * d;
    float rx = bar >= 52 * d ? w - bar / 2 : right - 30 * d;
    float by = maximum(T.safe_t + 30 * d, 48 * d);
    float xx[BUTTONS] = {cx - 54*d, cx, cx + 54*d, cx, lx, rx, rx, lx, lx};
    float yy[BUTTONS] = {cy, cy - 54*d, cy, cy + 54*d, by, by + 56*d, by, by + 56*d, by + 112*d};
    int bits[BUTTONS] = {SS_BUTTON_LEFT, SS_BUTTON_UP, SS_BUTTON_RIGHT, SS_BUTTON_DOWN, SS_BUTTON_B, 11, 10, 3, 2};
    if (vertical) {
        float top = minimum(maximum(T.safe_t + 22*d, 22*d), (h - game_h) / 4);
        xx[4]=lx; xx[7]=lx+50*d; xx[8]=lx+100*d; xx[5]=rx-50*d; xx[6]=rx;
        for (int i=4;i<BUTTONS;i++) yy[i]=top;
    }
    for (int i=0;i<BUTTONS;i++) T.buttons[i]=(control){bits[i],xx[i],yy[i],(i<4?50:44)*d,0};
    T.buttons[7].bit=T.demo_bit;
    int direct = T.pointer_mode == SS_POINTER_MENU || T.pointer_mode == SS_POINTER_MAP;
    int in_level = T.pointer_mode == SS_POINTER_HINTS && !T.paused && T.layout != SS_TOUCH_MENU;
    int dpad = T.layout == SS_TOUCH_DPAD;
    int menu = T.layout == SS_TOUCH_MENU;
    unsigned directions = T.first ? (unsigned)T.first : SS_TOUCH_DIR_ALL;
    /* Appearance: 0 absent, 1 glass, 2 unused-direction ring, 3 transparent hit target. */
    for (int i=0;i<4;i++) {
        int show=dpad || (menu && !direct) || (in_level && (T.extra & SS_TOUCH_SHOW_DPAD));
        T.buttons[i].visible = T.controls==SS_CONTROLS_HIDE || !show ? 0 :
            !in_level ? 3 : dpad && !(directions & (1u<<i)) ? 2 : 1;
    }
    for (int i=4;i<=6;i++) T.buttons[i].visible = T.controls==SS_CONTROLS_HIDE ? 0 : side_fits ? 1 : 3;
    T.buttons[7].visible = T.controls!=SS_CONTROLS_HIDE && side_fits && in_level && ((T.extra & SS_TOUCH_SHOW_Y) || T.demo_active);
    T.buttons[8].visible = T.controls!=SS_CONTROLS_HIDE && side_fits && in_level && (T.extra & SS_TOUCH_SHOW_X);
    T.a_visible = !direct;
}
void ss_android_touch_metrics(int width, int height, float density, float left, float top, float right, float bottom) {
    if (width<=0 || height<=0 || !isfinite(density) || density<=0) return;
    pthread_mutex_lock(&touch_lock);
    if (T.width!=width || T.height!=height || T.density!=density) reset_fingers();
    T.width=width;T.height=height;T.density=density;T.safe_l=left;T.safe_t=top;T.safe_r=right;T.safe_b=bottom;
    ss_display_set_view(width,height);layout();pthread_mutex_unlock(&touch_lock);
}
int ss_android_touch_tick(void) {
    pthread_mutex_lock(&touch_lock);
    int controls=ss_pointer_controls();
    ss_pointer_frame frame;
    int has=ss_pointer_read(&frame);
    uint64_t token=has?frame.token:0;
    if (controls!=SS_CONTROLS_KEEP && token!=T.token) {reset_fingers();T.token=token;}
    if (controls != SS_CONTROLS_KEEP) {
        uint64_t hint=ss_touch_left_get();
        int mode=has?frame.mode:SS_POINTER_NONE;
        if (mode!=SS_POINTER_HINTS || ss_touch_layout(hint)==SS_TOUCH_MENU || T.pointer_mode==SS_POINTER_MENU) T.paused=0;
        if (mode!=SS_POINTER_HINTS || T.paused || ss_touch_layout(hint)==SS_TOUCH_MENU) hint=SS_TOUCH_MENU;
        if ((uint32_t)hint!=(uint32_t)T.hint || mode!=T.pointer_mode || controls!=T.controls) {
            reset_fingers();T.hint=hint;T.pointer_mode=mode;T.controls=controls;
            T.layout=ss_touch_layout(hint);T.first=ss_touch_first(hint);T.second=ss_touch_second(hint);T.extra=ss_touch_extra(hint);
        }
    }
    if (has && T.pointer_mode==SS_POINTER_HINTS) {
        uint64_t demo=frame.tutorial_buttons;
        int bit=demo?__builtin_ctzll(demo):3;
        if (T.demo_bit!=bit) {T.demo_bit=bit;reset_fingers();}
        T.demo_active=demo!=0;
    }
    else T.demo_active=0;
    layout();
    int visible=T.controls!=SS_CONTROLS_HIDE;
    pthread_mutex_unlock(&touch_lock);
    return visible;
}
static int zone_bit(float x, float y) {
    if (T.layout==SS_TOUCH_ONE) return T.first;
    if (T.layout==SS_TOUCH_TWO) return x<(T.left_l+T.left_r)/2?T.first:T.second;
    float mid=(T.zone_top+T.zone_bottom)/2, dx=x-(T.left_l+T.left_r)/2, dy=y-mid;
    if (hypotf(dx,dy)<20*T.density) return -1;
    return fabsf(dx)>fabsf(dy)?(dx<0?SS_BUTTON_LEFT:SS_BUTTON_RIGHT):(dy<0?SS_BUTTON_UP:SS_BUTTON_DOWN);
}
static int locate(float x,float y,float *gx,float *gy) {
    return ss_pointer_location(T.width,T.height,x,y,gx,gy);
}
static void pointer_event(int type,float x,float y) {
    float gx=0,gy=0;locate(x,y,&gx,&gy);
    ss_pointer_push((ss_pointer_event){T.token,type,gx,gy});
}
static uint64_t preview_time(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000000000ull + now.tv_nsec;
}
static void release(finger *f,float x,float y,int cancelled) {
    if (f->role==POINTER_MENU && f->id==T.menu_id) {pointer_event(cancelled?SS_POINTER_CANCEL:SS_POINTER_UP,x,y);T.menu_id=-1;}
    f->id=-1;f->x=x;f->y=y;f->released=cancelled?0:preview_time();publish();
}
static void down(int id,float x,float y) {
    finger *f=NULL;
    for(int i=0;i<FINGERS;i++) if(T.fingers[i].id<0){f=&T.fingers[i];break;}
    if(!f)return;
    *f=(finger){.id=id,.role=NONE,.bit=-1,.x=x,.y=y};
    ss_pointer_frame frame; int has=ss_pointer_read(&frame);
    float gx,gy; int located=has&&locate(x,y,&gx,&gy);
    /* The painted controls are above the menu touch surface (as on iOS). */
    for(int i=BUTTONS-1;i>=0;i--) {
        control b=T.buttons[i];float r=b.size*.57f;
        if(b.visible && fabsf(x-b.x)<=r && fabsf(y-b.y)<=r){
            if(b.bit==10) T.paused=!T.paused;
            else if(b.bit==SS_BUTTON_A || b.bit==SS_BUTTON_B) T.paused=0;
            f->role=POINTER_BUTTON;f->bit=b.bit;publish();return;
        }
    }
    if(located){int target=ss_pointer_hit(&frame,gx,gy);
        if(target>=0 && (frame.targets[target].flags&SS_POINTER_BUTTON)) {
            int bit=frame.targets[target].button;
            if(bit>=0&&bit<64){f->role=POINTER_BUTTON;f->bit=bit;publish();return;}
        }
        if(T.menu_id<0 && (frame.mode==SS_POINTER_MENU || frame.mode==SS_POINTER_MAP)) {
            f->role=POINTER_MENU;T.menu_id=id;pointer_event(SS_POINTER_DOWN,x,y);return;
        }
    }
    if(T.a_visible && x>=T.half+4*T.density && x<T.width-4*T.density && y>=T.zone_top && y<T.zone_bottom){T.paused=0;f->role=ZONE_A;f->bit=SS_BUTTON_A;}
    else if(T.controls!=SS_CONTROLS_HIDE && T.layout!=SS_TOUCH_MENU && T.layout!=SS_TOUCH_DPAD && x>=T.left_l && x<T.left_r && y>=T.zone_top && y<T.zone_bottom){f->role=ZONE_LEFT;f->bit=zone_bit(x,y);}
    publish();
}
int ss_android_touch_motion(const AInputEvent *event) {
    if(AInputEvent_getType(event)!=AINPUT_EVENT_TYPE_MOTION || !(AInputEvent_getSource(event)&AINPUT_SOURCE_TOUCHSCREEN))return 0;
    pthread_mutex_lock(&touch_lock);
    int action=AMotionEvent_getAction(event),type=action&AMOTION_EVENT_ACTION_MASK;
    if(type==AMOTION_EVENT_ACTION_CANCEL){reset_fingers();pthread_mutex_unlock(&touch_lock);return 1;}
    int count=AMotionEvent_getPointerCount(event);
    if(type==AMOTION_EVENT_ACTION_DOWN || type==AMOTION_EVENT_ACTION_POINTER_DOWN){
        int index=(action&AMOTION_EVENT_ACTION_POINTER_INDEX_MASK)>>AMOTION_EVENT_ACTION_POINTER_INDEX_SHIFT;
        if(index<count)down(AMotionEvent_getPointerId(event,index),AMotionEvent_getX(event,index),AMotionEvent_getY(event,index));
    } else if(type==AMOTION_EVENT_ACTION_MOVE){
        for(int index=0;index<count;index++)for(int i=0;i<FINGERS;i++) {
            finger *f=&T.fingers[i];if(f->id!=AMotionEvent_getPointerId(event,index))continue;
            float x=AMotionEvent_getX(event,index),y=AMotionEvent_getY(event,index);
            if(f->role==POINTER_MENU)pointer_event(SS_POINTER_MOVE,x,y);
            if(f->role==ZONE_LEFT && T.layout!=SS_TOUCH_ONE) {f->bit=zone_bit(x,y);publish();}
            f->x=x;f->y=y;break;
        }
    }else if(type==AMOTION_EVENT_ACTION_UP || type==AMOTION_EVENT_ACTION_POINTER_UP){
        int index=(action&AMOTION_EVENT_ACTION_POINTER_INDEX_MASK)>>AMOTION_EVENT_ACTION_POINTER_INDEX_SHIFT;
        if(index<count)for(int i=0;i<FINGERS;i++)if(T.fingers[i].id==AMotionEvent_getPointerId(event,index)){
            release(&T.fingers[i],AMotionEvent_getX(event,index),AMotionEvent_getY(event,index),0);break;}
    }
    pthread_mutex_unlock(&touch_lock);
    return 1;
}
int ss_android_touch_draw(float *out,int capacity) {
    if(!out || capacity<10+BUTTONS*6+FINGERS*4)return 0;
    pthread_mutex_lock(&touch_lock);
    out[0]=T.width;out[1]=T.height;out[2]=T.layout!=SS_TOUCH_MENU&&T.layout!=SS_TOUCH_DPAD&&T.layout!=SS_TOUCH_ONE&&T.controls!=SS_CONTROLS_HIDE?T.left_l:0;
    out[3]=T.left_r;out[4]=T.zone_top;out[5]=T.zone_bottom;out[6]=T.layout;out[7]=T.first;out[8]=T.second;
    int n=9;
    for(int i=0;i<BUTTONS;i++){
        control b=T.buttons[i];int pressed=0;
        for(int j=0;j<FINGERS;j++)if(T.fingers[j].id>=0&&T.fingers[j].bit==b.bit){pressed=1;break;}
        out[n++]=b.bit;out[n++]=b.x;out[n++]=b.y;out[n++]=b.size;out[n++]=b.visible;out[n++]=pressed;
    }
    out[n++]=T.safe_t;
    uint64_t now=preview_time();
    for(int i=0;i<FINGERS;i++) {
        finger f=T.fingers[i];
        float alpha=f.id>=0?1:f.released && now-f.released<160000000ull?1-(now-f.released)/160000000.0f:0;
        if((f.role!=ZONE_A && !(f.role==ZONE_LEFT && T.layout==SS_TOUCH_ONE)) || f.bit<0) alpha=0;
        out[n++]=f.bit;out[n++]=f.x;out[n++]=f.y;out[n++]=alpha;
    }
    pthread_mutex_unlock(&touch_lock);
    return n;
}
