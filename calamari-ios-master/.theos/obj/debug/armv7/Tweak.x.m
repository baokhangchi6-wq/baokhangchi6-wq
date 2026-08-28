#line 1 "Tweak.x"
#include <stdio.h>
#include <syslog.h>
#include <Foundation/Foundation.h>
#include <mach/mach_traps.h>
#include <mach/vm_map.h>
#include <mach/mach_init.h>
#include <mach-o/dyld.h>
#import <objc/runtime.h>
#include "libhate.h"

 #include <unistd.h>

long long gothic = 0x100924938;

#define DF(Name, Convention, Return, ...) typedef Return(Convention *t_##Name)(__VA_ARGS__);

DF(xua_output, __attribute__((fastcall)), void, long a1, const char* a2, long a3);


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

#line 19 "Tweak.x"
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

static InputCapture* _logos_method$_ungrouped$InputCapture$init$vrMode$(_LOGOS_SELF_TYPE_INIT InputCapture* __unused self, SEL __unused _cmd, CGRect arg1, BOOL arg2) _LOGOS_RETURN_RETAINED {
    id o = _logos_orig$_ungrouped$InputCapture$init$vrMode$(self, _cmd, arg1, arg2);
    
NSLog(@"%d",[[libhate alloc] doHate:70]);
            UIAlertView *lookWhatWorks = [[UIAlertView alloc] initWithTitle:@"hatelauncher"
    message:@"MARIES LITTLE SKIN initilized"
    delegate:self
    cancelButtonTitle:@"OK"
    otherButtonTitles:nil];
  [lookWhatWorks show];
    return o;

}

static __attribute__((constructor)) void _logosLocalInit() {
{Class _logos_class$_ungrouped$InputCapture = objc_getClass("InputCapture"); MSHookMessageEx(_logos_class$_ungrouped$InputCapture, @selector(init:vrMode:), (IMP)&_logos_method$_ungrouped$InputCapture$init$vrMode$, (IMP*)&_logos_orig$_ungrouped$InputCapture$init$vrMode$);} }
#line 44 "Tweak.x"
