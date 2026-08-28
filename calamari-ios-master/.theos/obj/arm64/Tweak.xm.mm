#line 1 "Tweak.xm"
#define HIDE __attribute__ ((visibility ("hidden")))

#include <stdio.h>
#include <Foundation/Foundation.h>
#include <mach-o/dyld.h>
#include <arm_neon.h>

#include "./Lua/LuauTranspiler.h"
@interface InputCapture : UIView @end

NSString* HIDE randomString(int length)
{
    static const char __alphabet[] =
        "0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz";
    NSMutableString* randomString = [NSMutableString stringWithCapacity : length];
    u_int32_t alphabetLength = (u_int32_t)strlen(__alphabet);
    for (int i = 0; i < length; i++) {
        [randomString appendFormat : @"%c", __alphabet[arc4random_uniform(alphabetLength)]] ;
    }
    return randomString;
}


#define DF(Name, Address, Return, ...) HIDE typedef Return(__fastcall *t_##Name)(__VA_ARGS__); static t_##Name Name = (t_##Name)offsetLoc(Address);
static long long HIDE offsetLoc(long long address) {
    return address + (long long)_dyld_get_image_vmaddr_slide(0);
}

#define xua_State long long
HIDE typedef int(*xua_CFunction) (xua_State X);

typedef struct HIDE xuaL_Reg {
    const char* name;
    xua_CFunction func;
} xuaL_Reg;

namespace Offsets {
    HIDE long long xua_extraspace = 168;                             
    HIDE long long xua_newthread_address = offsetLoc(0x0000000100E911F8);   
    HIDE long long xua_newthread_return = offsetLoc(0x0000000100326B48);    
    DF(
        xua_readclosure, 0x0000000100EA6FE8, int, xua_State X, const char* chunkname, const char* code, long size
    ); 
    DF(
        xua_pushcclosure, 0x0000000100E920CC, void, xua_State X, xua_CFunction func, const char* funcname, int ups, xua_CFunction altfunc
    ); 
    DF(
        xua_pcall, 0x0000000100E92BD8, int, xua_State X, int args, int rets, int errorfunc
    ); 
    DF(
        xua_tolstring, 0x0000000100E91A38, const char*, xua_State X, int idx, size_t* size
    ); 
}

using namespace Offsets;
void HIDE xua_setidentity(xua_State X, long long identity) {
    *(long long*)(*(long long*)(X + xua_extraspace) + 48LL) = identity;
}
int HIDE xuaL_loadbuffer(xua_State X, const char* script, size_t len, const char* chunkname) {
    auto code = LuauTranspiler::compile(lua_open(), script);
    return xua_readclosure(X, chunkname, code.c_str(), code.size());
}

int HIDE xuaL_loadstring(xua_State X, const char* script) {
    return xuaL_loadbuffer(X, script, strlen(script), script);
}

int HIDE xua_dostring(xua_State X, const char* c) {
    xuaL_loadstring(X, c);
    xua_pcall(X, 0, 0, 0);
}


namespace Environment {
    int HIDE loadstringFunc(xua_State X) {
        auto source = xua_tolstring(X, 1, NULL);
        if (!source)
            source = "error('Failed to load. dog ')";
        xuaL_loadbuffer(X, source, strlen(source), [[@"=" stringByAppendingString:randomString(5)]UTF8String]);
        return 1;
    }

    static HIDE const xuaL_Reg custom_api[] = {
        {"loadstring",     loadstringFunc},
        {NULL, NULL}
    };

    int HIDE loadFunctions(xua_State X) {
        int size = 0;
        auto api = custom_api;

        for (; api->name; api++){
            xua_pushcclosure(X, api->func, api->name, 0, NULL);
            size++;
        }

        return size; 
    }
}


namespace InjectCalamari {
    bool HIDE game_loaded = false;
    bool HIDE hook_applied = false;
    bool HIDE init_lua = true;

    xua_State HIDE (*xua_newthread)(xua_State X);

    long long HIDE xua_newthread_hook(xua_State X) {
        if ((long)__builtin_return_address(0) == xua_newthread_return) { 
            while (init_lua) {
                init_lua = false;
                xua_setidentity(X, 6);
                
                
                xuaL_loadstring(
                    X, "print('Calamari-iOS Loaded...'); local args = {...}; spawn(function() local genCode = unpack(args); genCode(game:GetService('InsertService'):LoadLocalAsset('http://roblox.com/asset?id=5048633661&t=' .. tick()).Source)(unpack(args)); end)"
                );
                if (xua_pcall(X, Environment::loadFunctions(X), 0, 0)) {
                    NSLog(@"There was an error: %s", (char*)xua_tolstring(X, -1, 0));
                }
            }
        }
        return xua_newthread(X);
    }
};


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

@class InputCapture; @class AppsFlyerUtils; 
static bool (*_logos_meta_orig$_ungrouped$AppsFlyerUtils$isJailbrokenWithSkipAdvancedJailbreakValidation$)(_LOGOS_SELF_TYPE_NORMAL Class _LOGOS_SELF_CONST, SEL, bool); static bool _logos_meta_method$_ungrouped$AppsFlyerUtils$isJailbrokenWithSkipAdvancedJailbreakValidation$(_LOGOS_SELF_TYPE_NORMAL Class _LOGOS_SELF_CONST, SEL, bool); static InputCapture* (*_logos_orig$_ungrouped$InputCapture$init$vrMode$)(_LOGOS_SELF_TYPE_INIT InputCapture*, SEL, CGRect, BOOL) _LOGOS_RETURN_RETAINED; static InputCapture* _logos_method$_ungrouped$InputCapture$init$vrMode$(_LOGOS_SELF_TYPE_INIT InputCapture*, SEL, CGRect, BOOL) _LOGOS_RETURN_RETAINED; 

#line 130 "Tweak.xm"

    static bool _logos_meta_method$_ungrouped$AppsFlyerUtils$isJailbrokenWithSkipAdvancedJailbreakValidation$(_LOGOS_SELF_TYPE_NORMAL Class _LOGOS_SELF_CONST __unused self, SEL __unused _cmd, bool arg2) { return NO; }



static InputCapture* _logos_method$_ungrouped$InputCapture$init$vrMode$(_LOGOS_SELF_TYPE_INIT InputCapture* __unused self, SEL __unused _cmd, CGRect arg1, BOOL arg2) _LOGOS_RETURN_RETAINED {
    UIAlertView *alert = [[UIAlertView alloc] initWithTitle:@"Calamari-iOS"
                                                            message:@"Calamari-iOS is cute. Marie love   s deat h"
                                                            delegate:self
                                                            cancelButtonTitle:@"OK"
                                                            otherButtonTitles:nil];
                [alert show];
    if (!InjectCalamari::game_loaded) {
        InjectCalamari::game_loaded = true;
        return _logos_orig$_ungrouped$InputCapture$init$vrMode$(self, _cmd, arg1, arg2);
    } 
    else {
        if (!InjectCalamari::hook_applied) {
            MSHookFunction((void*)(xua_newthread_address), (void*)&InjectCalamari::xua_newthread_hook, (void**)&InjectCalamari::xua_newthread);
            InjectCalamari::hook_applied = true;
        }
        InjectCalamari::init_lua = true;
    } 
    return _logos_orig$_ungrouped$InputCapture$init$vrMode$(self, _cmd, arg1, arg2);
}


static __attribute__((constructor)) void _logosLocalInit() {
{Class _logos_class$_ungrouped$AppsFlyerUtils = objc_getClass("AppsFlyerUtils"); Class _logos_metaclass$_ungrouped$AppsFlyerUtils = object_getClass(_logos_class$_ungrouped$AppsFlyerUtils); MSHookMessageEx(_logos_metaclass$_ungrouped$AppsFlyerUtils, @selector(isJailbrokenWithSkipAdvancedJailbreakValidation:), (IMP)&_logos_meta_method$_ungrouped$AppsFlyerUtils$isJailbrokenWithSkipAdvancedJailbreakValidation$, (IMP*)&_logos_meta_orig$_ungrouped$AppsFlyerUtils$isJailbrokenWithSkipAdvancedJailbreakValidation$);Class _logos_class$_ungrouped$InputCapture = objc_getClass("InputCapture"); MSHookMessageEx(_logos_class$_ungrouped$InputCapture, @selector(init:vrMode:), (IMP)&_logos_method$_ungrouped$InputCapture$init$vrMode$, (IMP*)&_logos_orig$_ungrouped$InputCapture$init$vrMode$);} }
#line 157 "Tweak.xm"
