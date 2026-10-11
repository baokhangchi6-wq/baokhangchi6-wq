#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <os/lock.h>

extern unsigned ms_drawable_generation(void);
extern double ms_drawable_clock(void);
extern void ms_drawable_lifetime_event(int, double, unsigned);
static char tokenKey;
static char rawTokenKey;
static os_unfair_lock tokenLock = OS_UNFAIR_LOCK_INIT;

@interface MSDrawableLifeToken : NSObject
@property double acquired;
@property unsigned generation;
@property BOOL reused;
@end
@implementation MSDrawableLifeToken
- (void)dealloc {
    ms_drawable_lifetime_event(self.reused ? 2 : 1, ms_drawable_clock() - self.acquired, self.generation);
}
@end

void ms_track_drawable_lifetime(void* pointer) {
    unsigned generation = ms_drawable_generation();
    if (!pointer || !generation) return;
    id drawable = (__bridge id)pointer;
    os_unfair_lock_lock(&tokenLock);
    __unsafe_unretained MSDrawableLifeToken* previous = objc_getAssociatedObject(drawable, &rawTokenKey);
    previous.reused = YES;
    MSDrawableLifeToken* token = [MSDrawableLifeToken new];
    token.acquired = ms_drawable_clock(); token.generation = generation;
    objc_setAssociatedObject(drawable, &tokenKey, token, OBJC_ASSOCIATION_RETAIN_NONATOMIC);
    objc_setAssociatedObject(drawable, &rawTokenKey, token, OBJC_ASSOCIATION_ASSIGN);
    os_unfair_lock_unlock(&tokenLock);
}
int ms_drawable_sample(void* pointer, double* acquired, unsigned* generation) {
    if (!pointer) return 0;
    os_unfair_lock_lock(&tokenLock);
    __unsafe_unretained MSDrawableLifeToken* token = objc_getAssociatedObject((__bridge id)pointer, &rawTokenKey);
    if (!token) { os_unfair_lock_unlock(&tokenLock); return 0; }
    *acquired = token.acquired; *generation = token.generation;
    os_unfair_lock_unlock(&tokenLock);
    return 1;
}
void ms_mark_drawable_present(void* pointer) {
    double acquired; unsigned generation;
    if (ms_drawable_sample(pointer, &acquired, &generation))
        ms_drawable_lifetime_event(0, ms_drawable_clock() - acquired, generation);
}
