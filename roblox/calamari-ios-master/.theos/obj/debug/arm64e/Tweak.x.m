#line 1 "Tweak.x"

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

#line 1 "Tweak.x"
#include <stdio.h>
#include <syslog.h>
#include <Foundation/Foundation.h>
#include <mach/mach_traps.h>
#include <mach/vm_map.h>
#include <mach/mach_init.h>
#include <mach-o/dyld.h>
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
    
    intptr_t slide;
    long long rw = 0x1014efcc4;
    for (uint32_t i = 0; i < _dyld_image_count(); i++) 
    {
        const char *name = _dyld_get_image_name(i); 
        NSString *path = [NSString stringWithFormat:@"%s", name];
        if ([path hasSuffix:@"Roblox"]) slide=_dyld_get_image_vmaddr_slide(i); 
    }
          NSLog(@"%@",NSTemporaryDirectory());
              freopen("/private/var/mobile/Containers/Data/Application/5FA5E5CE-C2C9-4714-A143-21C3387C260F/tmp/exploitlog", "a+", stdout);

      NSLog(@"Reading ... %@",[NSString stringWithFormat:@"%s",(char *)rw + slide]);
      char* address = (char *)0x1000077e0 + slide;
      *address = 'a';
            NSLog(@"After writing %@",[NSString stringWithFormat:@"%s",(char *)rw + slide]);

    return o;

}

static __attribute__((constructor)) void _logosLocalInit() {
{Class _logos_class$_ungrouped$InputCapture = objc_getClass("InputCapture"); MSHookMessageEx(_logos_class$_ungrouped$InputCapture, @selector(init:vrMode:), (IMP)&_logos_method$_ungrouped$InputCapture$init$vrMode$, (IMP*)&_logos_orig$_ungrouped$InputCapture$init$vrMode$);} }
#line 42 "Tweak.x"
