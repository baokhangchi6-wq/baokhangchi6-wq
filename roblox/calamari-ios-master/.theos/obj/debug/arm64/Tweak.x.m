#line 1 "Tweak.x"
#include <stdio.h>
#include <syslog.h>
#include <Foundation/Foundation.h>
#include <mach/mach_traps.h>
#include <mach/vm_map.h>
#include <mach/mach_init.h>
#include <mach-o/dyld.h>
#include "libhate.h"
#include <sys/mman.h>
extern kern_return_t mach_vm_region
(
 vm_map_t target_task,
 mach_vm_address_t *address,
 mach_vm_size_t *size,
 vm_region_flavor_t flavor,
 vm_region_info_t info,
 mach_msg_type_number_t *infoCnt,
 mach_port_t *object_name
 );
 #define kerncall(x) ({ \
    kern_return_t _kr = (x); \
    if(_kr != KERN_SUCCESS) \
        fprintf(stderr, "%s failed with error code: 0x%x\n", #x, _kr); \
    _kr; \
})
 int e = 0;

@interface InputCapture : UIView 
@end
void (*original)(void *);

void *setfield(void *arg0) {
    NSLog(@"%p",arg0);
    return nil;
}


#include <substrate.h>
#if defined(__clang__)
#if __has_feature(objc_arc)
#define _LOGOS_SELF_TYPE_NORMAL __unsafe_unretained
#define _LOGOS_SELF_TYPE_INIT __attribute__((ns_consumed))
#define _LOGOS_SELF_CONST const
#define _LOGOS_RETURN_RETAINED __attribute__((ns_returns_retained))
#else
#define _LOGOS_SELF_TYPE_NORMAL
#define _LOGOS_SELF_TYPE_INIT
#define _LOGOS_SELF_CONST
#define _LOGOS_RETURN_RETAINED
#endif
#else
#define _LOGOS_SELF_TYPE_NORMAL
#define _LOGOS_SELF_TYPE_INIT
#define _LOGOS_SELF_CONST
#define _LOGOS_RETURN_RETAINED
#endif

@class InputCapture; 
static InputCapture* (*_logos_orig$_ungrouped$InputCapture$init$vrMode$)(_LOGOS_SELF_TYPE_INIT InputCapture*, SEL, CGRect, BOOL) _LOGOS_RETURN_RETAINED; static InputCapture* _logos_method$_ungrouped$InputCapture$init$vrMode$(_LOGOS_SELF_TYPE_INIT InputCapture*, SEL, CGRect, BOOL) _LOGOS_RETURN_RETAINED; 

#line 37 "Tweak.x"

static InputCapture* _logos_method$_ungrouped$InputCapture$init$vrMode$(_LOGOS_SELF_TYPE_INIT InputCapture* __unused self, SEL __unused _cmd, CGRect arg1, BOOL arg2) _LOGOS_RETURN_RETAINED {
    id o = _logos_orig$_ungrouped$InputCapture$init$vrMode$(self, _cmd, arg1, arg2);
    
    if (e == 0) {
         UIAlertView *alert = [[UIAlertView alloc] initWithTitle:@"hatelauncher exploit" 
                                                    message:@"HatelaKEEP ON BLAH BLAH BLAHuncher for iOS initilized. Thank you for supporting!" 
                                                    delegate:self 
                                                    cancelButtonTitle:@"OK" 
                                                    otherButtonTitles:nil];
        [alert show];
        e += 1;
        return o;
    }
         UIAlertView *alert = [[UIAlertView alloc] initWithTitle:@"hatelauncher exploit" 
                                                    message:@"Puppy wuppy underpants." 
                                                    delegate:self 
                                                    cancelButtonTitle:@"OK" 
                                                    otherButtonTitles:nil];
        [alert show];
    int* f = (void*)(0x100797b7c + _dyld_get_image_vmaddr_slide(0));

        MSHookFunction((void*)f, &setfield, NULL);

       return o;
}


static __attribute__((constructor)) void _logosLocalInit() {
{Class _logos_class$_ungrouped$InputCapture = objc_getClass("InputCapture"); MSHookMessageEx(_logos_class$_ungrouped$InputCapture, @selector(init:vrMode:), (IMP)&_logos_method$_ungrouped$InputCapture$init$vrMode$, (IMP*)&_logos_orig$_ungrouped$InputCapture$init$vrMode$);} }
#line 65 "Tweak.x"
