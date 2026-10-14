#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <WebKit/WebKit.h>
#import <objc/runtime.h>
#import <objc/message.h>

static UIViewController *MSAppodealRootViewController = nil;
static WKWebView *MSAppodealWebView = nil;
static NSString *MSAppodealPendingRequest = nil;
static NSString *MSAppodealPendingFormat = nil;
static BOOL MSAppodealInitialized = NO;
static BOOL MSAppodealAdShowing = NO;
static BOOL MSAppodealRewardEarned = NO;

static NSString *const kDefaultAppodealAppKey = @"f877ffd799eb622831d6849e35cad47afa3116838cfc2032";

extern void app_log(const char* fmt, ...);

static void MSAppodealLog(NSString *format, ...) {
    va_list args;
    va_start(args, format);
    NSString *message = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);
    NSLog(@"[Masterstrap-Appodeal] %@", message);
    app_log("[Appodeal] %s", [message UTF8String]);
}

static void MSAppodealDeliverResult(NSString *requestId, NSString *format,
                                    NSString *outcome, NSString *detail) {
    if (requestId.length == 0 || !MSAppodealWebView) return;
    NSDictionary *event = @{
        @"requestId": requestId,
        @"format": format ?: @"unknown",
        @"outcome": outcome ?: @"failed",
        @"detail": detail ?: @"",
        @"network": @"appodeal"
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:event options:0 error:nil];
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    NSString *script = [NSString stringWithFormat:
        @"window.MasterstrapAds&&window.MasterstrapAds.nativeResult(%@);", json];
    dispatch_async(dispatch_get_main_queue(), ^{
        if (MSAppodealWebView) {
            [MSAppodealWebView evaluateJavaScript:script completionHandler:^(id result, NSError *error) {
                if (error) MSAppodealLog(@"JS result callback failed: %@", error.localizedDescription);
            }];
        }
    });
    MSAppodealLog(@"request=%@ format=%@ outcome=%@ detail=%@",
                  requestId, format, outcome, detail);
}

static void MSAppodealResolve(NSString *outcome, NSString *detail) {
    if (!MSAppodealPendingRequest) return;
    MSAppodealDeliverResult(MSAppodealPendingRequest, MSAppodealPendingFormat, outcome, detail);
    MSAppodealPendingRequest = nil;
    MSAppodealPendingFormat = nil;
    MSAppodealAdShowing = NO;
}

@interface MSAppodealDelegateHandler : NSObject
@end

@implementation MSAppodealDelegateHandler

// Interstitial delegate methods
- (void)interstitialDidLoadAdIsPrecache:(BOOL)precache {
    MSAppodealLog(@"interstitial loaded (precache=%d)", precache);
}

- (void)interstitialDidFailToLoadAd {
    MSAppodealLog(@"interstitial failed to load");
    if ([MSAppodealPendingFormat isEqualToString:@"interstitial"] && !MSAppodealAdShowing) {
        MSAppodealResolve(@"failed", @"interstitial_load_failed");
    }
}

- (void)interstitialDidDismiss {
    MSAppodealLog(@"interstitial dismissed");
    if ([MSAppodealPendingFormat isEqualToString:@"interstitial"]) {
        MSAppodealResolve(@"completed", @"interstitial_closed");
    }
}

- (void)interstitialDidFailToPresent {
    MSAppodealLog(@"interstitial failed to present");
    if ([MSAppodealPendingFormat isEqualToString:@"interstitial"]) {
        MSAppodealResolve(@"failed", @"interstitial_present_failed");
    }
}

// Rewarded video delegate methods
- (void)rewardedVideoDidLoadAdIsPrecache:(BOOL)precache {
    MSAppodealLog(@"rewarded video loaded (precache=%d)", precache);
}

- (void)rewardedVideoDidFailToLoadAd {
    MSAppodealLog(@"rewarded video failed to load");
    if ([MSAppodealPendingFormat isEqualToString:@"rewarded"] && !MSAppodealAdShowing) {
        MSAppodealResolve(@"failed", @"rewarded_load_failed");
    }
}

- (void)rewardedVideoDidFinish:(float)amount name:(NSString *)name {
    MSAppodealLog(@"rewarded video finished, amount=%.1f name=%@", amount, name);
    MSAppodealRewardEarned = YES;
}

- (void)rewardedVideoDidDismiss:(BOOL)completed {
    MSAppodealLog(@"rewarded video dismissed (completed=%d, rewardEarned=%d)", completed, MSAppodealRewardEarned);
    if ([MSAppodealPendingFormat isEqualToString:@"rewarded"]) {
        if (MSAppodealRewardEarned || completed) {
            MSAppodealResolve(@"completed", @"rewarded_earned");
        } else {
            MSAppodealResolve(@"failed", @"rewarded_dismissed_without_reward");
        }
    }
    MSAppodealRewardEarned = NO;
}

- (void)rewardedVideoDidFailToPresent {
    MSAppodealLog(@"rewarded video failed to present");
    if ([MSAppodealPendingFormat isEqualToString:@"rewarded"]) {
        MSAppodealResolve(@"failed", @"rewarded_present_failed");
    }
}

@end

static MSAppodealDelegateHandler *g_appodeal_delegate = nil;

__attribute__((visibility("default")))
void MSAppodealConfigure(UIViewController *rootVC, WKWebView *webView) {
    if (!rootVC || !webView) return;
    MSAppodealRootViewController = rootVC;
    MSAppodealWebView = webView;

    Class appodealClass = objc_getClass("Appodeal");
    if (!appodealClass) {
        MSAppodealLog(@"Appodeal SDK not dynamically present; bridge standby");
        return;
    }

    if (MSAppodealInitialized) return;

    if (!g_appodeal_delegate) {
        g_appodeal_delegate = [[MSAppodealDelegateHandler alloc] init];
    }

    NSString *appKey = kDefaultAppodealAppKey;
    NSString *bundleKey = [[NSBundle mainBundle] objectForInfoDictionaryKey:@"AppodealAppKey"];
    if (bundleKey && bundleKey.length > 0) {
        appKey = bundleKey;
    }

    // Set delegates
    SEL setInterDelegateSel = sel_registerName("setInterstitialDelegate:");
    if ([appodealClass respondsToSelector:setInterDelegateSel]) {
        ((void (*)(id, SEL, id))objc_msgSend)(appodealClass, setInterDelegateSel, g_appodeal_delegate);
    }

    SEL setRewardDelegateSel = sel_registerName("setRewardedVideoDelegate:");
    if ([appodealClass respondsToSelector:setRewardDelegateSel]) {
        ((void (*)(id, SEL, id))objc_msgSend)(appodealClass, setRewardDelegateSel, g_appodeal_delegate);
    }

    // Initialize Appodeal: AppodealAdTypeInterstitial (1) | AppodealAdTypeRewardedVideo (2)
    NSUInteger adTypes = (1 << 0) | (1 << 1);
    SEL initSel = sel_registerName("initializeWithApiKey:types:hasConsent:");
    if ([appodealClass respondsToSelector:initSel]) {
        ((void (*)(id, SEL, id, NSUInteger, BOOL))objc_msgSend)(appodealClass, initSel, appKey, adTypes, NO);
        MSAppodealInitialized = YES;
        MSAppodealLog(@"Appodeal initialized with key=%@ types=%lu", appKey, (unsigned long)adTypes);
    } else {
        SEL initSimpleSel = sel_registerName("initializeWithApiKey:types:");
        if ([appodealClass respondsToSelector:initSimpleSel]) {
            ((void (*)(id, SEL, id, NSUInteger))objc_msgSend)(appodealClass, initSimpleSel, appKey, adTypes);
            MSAppodealInitialized = YES;
            MSAppodealLog(@"Appodeal initialized (simple) with key=%@", appKey);
        }
    }
}

__attribute__((visibility("default")))
void MSAppodealRequest(const char* format_cstr, const char* request_id_cstr) {
    if (!format_cstr || !request_id_cstr) return;
    NSString *format = [NSString stringWithUTF8String:format_cstr];
    NSString *requestId = [NSString stringWithUTF8String:request_id_cstr];

    Class appodealClass = objc_getClass("Appodeal");
    if (!appodealClass || !MSAppodealRootViewController) {
        MSAppodealDeliverResult(requestId, format, @"failed", @"appodeal_not_linked");
        return;
    }

    if (MSAppodealAdShowing) {
        MSAppodealDeliverResult(requestId, format, @"failed", @"ad_already_showing");
        return;
    }

    MSAppodealPendingRequest = requestId;
    MSAppodealPendingFormat = format;
    MSAppodealRewardEarned = NO;

    NSUInteger showStyle = [format isEqualToString:@"rewarded"] ? 2 : 1; // 2 = Rewarded, 1 = Interstitial

    SEL isReadySel = sel_registerName("isReadyForShowWithStyle:");
    BOOL isReady = NO;
    if ([appodealClass respondsToSelector:isReadySel]) {
        isReady = ((BOOL (*)(id, SEL, NSUInteger))objc_msgSend)(appodealClass, isReadySel, showStyle);
    }

    if (isReady) {
        MSAppodealAdShowing = YES;
        SEL showSel = sel_registerName("showAd:rootViewController:");
        if ([appodealClass respondsToSelector:showSel]) {
            ((void (*)(id, SEL, NSUInteger, id))objc_msgSend)(appodealClass, showSel, showStyle, MSAppodealRootViewController);
            MSAppodealLog(@"showing Appodeal ad style=%lu request=%@", (unsigned long)showStyle, requestId);
        }
    } else {
        MSAppodealLog(@"Appodeal ad not ready for style=%lu request=%@", (unsigned long)showStyle, requestId);
        MSAppodealDeliverResult(requestId, format, @"failed", @"no_inventory");
        MSAppodealPendingRequest = nil;
        MSAppodealPendingFormat = nil;
    }
}

