#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <WebKit/WebKit.h>
#import <IronSource/IronSource.h>
#import <dispatch/dispatch.h>

static UIViewController *MSRootViewController;
static WKWebView *MSWebView;
static LPMRewardedAd *MSRewarded;
static LPMInterstitialAd *MSInterstitial;
static LPMBannerAdView *MSBanner;
static LPMBannerAdView *MSHiddenBanner;
static NSString *MSPendingRequest;
static NSString *MSPendingFormat;
static NSString *MSRewardedAdUnitId;
static NSString *MSInterstitialAdUnitId;
static NSString *MSBannerAdUnitId;
static BOOL MSRewardEarned;
static BOOL MSInitialized;
static BOOL MSInitializationStarted;
static BOOL MSPrivacyPromptVisible;
static BOOL MSAdShowing;
static BOOL MSRewardedLoadInFlight;
static BOOL MSInterstitialLoadInFlight;
static BOOL MSRewardedLoadFailed;
static BOOL MSInterstitialLoadFailed;
static BOOL MSHiddenBannerLoaded;
static BOOL MSHiddenBannerLoadInFlight;
static NSTimeInterval MSLastHiddenBannerReloadTime;
static BOOL MSPendingUsingInterstitialFallback;
static BOOL MSPendingUsingBannerFallback;

static void MSLoadRewardedAd(void);
static void MSLoadInterstitialAd(void);
static void MSLoadHiddenBannerAd(void);
static void MSReloadAllAds(void);

static NSString *const MSPrivacyChoiceSavedKey = @"masterstrap_ads_privacy_choice_saved";
static NSString *const MSGDPRConsentKey = @"masterstrap_ads_gdpr_consent";
static NSString *const MSCCPAOptOutKey = @"masterstrap_ads_ccpa_opt_out";

extern void app_log(const char* fmt, ...);

static void MSLog(NSString *format, ...) {
    va_list args;
    va_start(args, format);
    NSString *message = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);
    NSLog(@"[Masterstrap-LevelPlay] %@", message);
    app_log("[LevelPlay] %s", [message UTF8String]);
}

static void MSDeliverResult(NSString *requestId, NSString *format,
                            NSString *outcome, NSString *detail) {
    if (requestId.length == 0 || !MSWebView) return;
    NSDictionary *event = @{
        @"requestId": requestId,
        @"format": format ?: @"unknown",
        @"outcome": outcome ?: @"failed",
        @"detail": detail ?: @""
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:event options:0 error:nil];
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    NSString *script = [NSString stringWithFormat:
        @"window.MasterstrapAds&&window.MasterstrapAds.nativeResult(%@);", json];
    [MSWebView evaluateJavaScript:script completionHandler:^(id result, NSError *error) {
        if (error) MSLog(@"JS result callback failed: %@", error.localizedDescription);
    }];
    MSLog(@"request=%@ format=%@ outcome=%@ detail=%@",
          requestId, format, outcome, detail);
}

static void MSResolve(NSString *outcome, NSString *detail) {
    if (!MSPendingRequest) return;
    MSDeliverResult(MSPendingRequest, MSPendingFormat, outcome, detail);
    MSPendingRequest = nil;
    MSPendingFormat = nil;
    MSAdShowing = NO;
    MSPendingUsingInterstitialFallback = NO;
    MSPendingUsingBannerFallback = NO;
}

static void MSTryShowPending(void) {
    if (!MSInitialized || !MSPendingRequest || MSAdShowing || !MSRootViewController) return;
    if ([MSPendingFormat isEqualToString:@"rewarded"]) {
        // Priority 1: Rewarded
        if ([MSRewarded isAdReady]) {
            MSAdShowing = YES;
            MSPendingUsingInterstitialFallback = NO;
            MSPendingUsingBannerFallback = NO;
            MSLog(@"showing preloaded rewarded request=%@", MSPendingRequest);
            [MSRewarded showAdWithViewController:MSRootViewController placementName:nil];
            return;
        }
        // Priority 2: Interstitial
        if ([MSInterstitial isAdReady]) {
            MSAdShowing = YES;
            MSPendingUsingInterstitialFallback = YES;
            MSPendingUsingBannerFallback = NO;
            MSLog(@"showing preloaded interstitial fallback for rewarded request=%@", MSPendingRequest);
            [MSInterstitial showAdWithViewController:MSRootViewController placementName:nil];
            return;
        }
        // Priority 3: Hidden Banner Fallback
        if (MSHiddenBannerLoaded || (MSHiddenBanner && !MSHiddenBannerLoadInFlight)) {
            MSPendingUsingBannerFallback = YES;
            MSLog(@"resolving rewarded via hidden banner fallback request=%@", MSPendingRequest);
            MSResolve(@"rewarded", @"hidden_banner_fallback_completed");
            MSReloadAllAds();
            return;
        }
        if (MSRewardedLoadFailed && MSInterstitialLoadFailed &&
            !MSRewardedLoadInFlight && !MSInterstitialLoadInFlight) {
            MSPendingUsingBannerFallback = YES;
            MSLog(@"rewarded_and_interstitial_unavailable; resolving via hidden banner fallback request=%@", MSPendingRequest);
            MSLoadHiddenBannerAd();
            MSResolve(@"rewarded", @"hidden_banner_fallback_completed");
            MSReloadAllAds();
            return;
        }
    } else if ([MSPendingFormat isEqualToString:@"interstitial"]) {
        // Priority 1: Interstitial
        if ([MSInterstitial isAdReady]) {
            MSAdShowing = YES;
            MSPendingUsingInterstitialFallback = NO;
            MSPendingUsingBannerFallback = NO;
            MSLog(@"showing preloaded interstitial request=%@", MSPendingRequest);
            [MSInterstitial showAdWithViewController:MSRootViewController placementName:nil];
        } else if (MSHiddenBannerLoaded || (MSHiddenBanner && !MSHiddenBannerLoadInFlight)) {
            MSPendingUsingBannerFallback = YES;
            MSLog(@"resolving interstitial via hidden banner fallback request=%@", MSPendingRequest);
            MSResolve(@"continue", @"hidden_banner_fallback_completed");
            MSReloadAllAds();
        } else if (MSInterstitialLoadFailed && !MSInterstitialLoadInFlight) {
            MSResolve(@"continue", @"interstitial_unavailable");
            MSReloadAllAds();
        }
    }
}

@interface MSLevelPlayDelegate : NSObject
    <LPMRewardedAdDelegate, LPMInterstitialAdDelegate, LPMBannerAdViewDelegate>
@end

@implementation MSLevelPlayDelegate
- (void)didLoadAdWithAdInfo:(LPMAdInfo *)adInfo {
    // The delegate callback is shared by rewarded/interstitial/banner ad objects.
    MSRewardedLoadInFlight = NO;
    MSInterstitialLoadInFlight = NO;
    MSHiddenBannerLoadInFlight = NO;
    MSLog(@"ad loaded: %@", adInfo);
    if (MSBannerAdUnitId && [adInfo.adUnitId isEqualToString:MSBannerAdUnitId]) {
        MSHiddenBannerLoaded = YES;
    }
    MSTryShowPending();
}
- (void)didFailToLoadAdWithAdUnitId:(NSString *)adUnitId error:(NSError *)error {
    MSLog(@"ad load failed unit=%@ error=%@", adUnitId, error);
    if ([adUnitId isEqualToString:MSRewardedAdUnitId]) {
        MSRewardedLoadInFlight = NO;
        MSRewardedLoadFailed = YES;
    }
    if ([adUnitId isEqualToString:MSInterstitialAdUnitId]) {
        MSInterstitialLoadInFlight = NO;
        MSInterstitialLoadFailed = YES;
    }
    if ([adUnitId isEqualToString:MSBannerAdUnitId]) {
        MSHiddenBannerLoadInFlight = NO;
    }
    MSTryShowPending();
}
- (void)didDisplayAdWithAdInfo:(LPMAdInfo *)adInfo {
    MSLog(@"ad displayed: %@", adInfo);
}
- (void)didRewardAdWithAdInfo:(LPMAdInfo *)adInfo reward:(LPMReward *)reward {
    MSRewardEarned = YES;
    MSLog(@"reward earned: %@", reward);
}
- (void)didFailToDisplayAdWithAdInfo:(LPMAdInfo *)adInfo error:(NSError *)error {
    MSLog(@"ad display failed: %@", error);
    BOOL fallback = MSPendingUsingInterstitialFallback || MSPendingUsingBannerFallback;
    NSString *detail = @"display_failed";
    if (MSPendingUsingInterstitialFallback) {
        detail = @"interstitial_fallback_display_failed";
    } else if (MSPendingUsingBannerFallback) {
        detail = @"hidden_banner_fallback_display_failed";
    }
    MSResolve([MSPendingFormat isEqualToString:@"interstitial"] || fallback ? @"continue" : @"failed", detail);
    MSReloadAllAds();
}
- (void)didCloseAdWithAdInfo:(LPMAdInfo *)adInfo {
    BOOL rewarded = [MSPendingFormat isEqualToString:@"rewarded"];
    BOOL fallback = MSPendingUsingInterstitialFallback || MSPendingUsingBannerFallback;
    NSString *detail = @"closed";
    if (rewarded) {
        if (MSPendingUsingInterstitialFallback) {
            detail = @"interstitial_fallback_completed";
        } else if (MSPendingUsingBannerFallback) {
            detail = @"hidden_banner_fallback_completed";
        } else if (MSRewardEarned) {
            detail = @"completed";
        } else {
            detail = @"incomplete";
        }
    }
    MSResolve(rewarded ? (fallback || MSRewardEarned ? @"rewarded" : @"failed") : @"continue", detail);
    MSRewardEarned = NO;
    MSReloadAllAds();
}
- (void)didClickAdWithAdInfo:(LPMAdInfo *)adInfo { MSLog(@"ad clicked: %@", adInfo); }
@end

static MSLevelPlayDelegate *MSDelegate;

static void MSLoadRewardedAd(void) {
    if (!MSRewarded || MSRewardedLoadInFlight || [MSRewarded isAdReady]) return;
    MSRewardedLoadInFlight = YES;
    MSRewardedLoadFailed = NO;
    MSLog(@"preloading rewarded ad unit=%@", MSRewardedAdUnitId);
    [MSRewarded loadAd];
}

static void MSLoadInterstitialAd(void) {
    if (!MSInterstitial || MSInterstitialLoadInFlight || [MSInterstitial isAdReady]) return;
    MSInterstitialLoadInFlight = YES;
    MSInterstitialLoadFailed = NO;
    MSLog(@"preloading interstitial ad unit=%@", MSInterstitialAdUnitId);
    [MSInterstitial loadAd];
}

static void MSLoadHiddenBannerAd(void) {
    if (!MSInitialized || !MSRootViewController) return;
    if (MSHiddenBannerLoadInFlight) return;

    @try {
        NSString *bannerId = MSBannerAdUnitId ?: NSBundle.mainBundle.infoDictionary[@"MSLevelPlayBannerAdUnitId"];
        if (bannerId.length == 0) bannerId = MSRewardedAdUnitId;
        if (bannerId.length == 0) return;

        MSHiddenBannerLoadInFlight = YES;
        MSHiddenBannerLoaded = NO;
        MSLastHiddenBannerReloadTime = [NSDate timeIntervalSinceReferenceDate];

        if (!MSHiddenBanner) {
            MSHiddenBanner = [[LPMBannerAdView alloc] initWithAdUnitId:bannerId];
            [MSHiddenBanner setDelegate:MSDelegate];
            MSHiddenBanner.userInteractionEnabled = NO;
            MSHiddenBanner.alpha = 0.01;
            MSHiddenBanner.translatesAutoresizingMaskIntoConstraints = NO;

            if (MSWebView && [MSWebView superview] == MSRootViewController.view) {
                [MSRootViewController.view insertSubview:MSHiddenBanner belowSubview:MSWebView];
            } else {
                [MSRootViewController.view insertSubview:MSHiddenBanner atIndex:0];
            }

            [NSLayoutConstraint activateConstraints:@[
                [MSHiddenBanner.centerXAnchor constraintEqualToAnchor:MSRootViewController.view.centerXAnchor],
                [MSHiddenBanner.bottomAnchor constraintEqualToAnchor:MSRootViewController.view.bottomAnchor],
                [MSHiddenBanner.widthAnchor constraintEqualToConstant:320.0],
                [MSHiddenBanner.heightAnchor constraintEqualToConstant:50.0]
            ]];
        }
        MSLog(@"preloading hidden background banner unit=%@", bannerId);
        [MSHiddenBanner loadAdWithViewController:MSRootViewController];
    } @catch (NSException *e) {
        MSHiddenBannerLoadInFlight = NO;
        MSLog(@"MSLoadHiddenBannerAd caught exception: %@ - %@", e.name, e.reason);
    }
}

static void MSReloadAllAds(void) {
    MSLoadRewardedAd();
    MSLoadInterstitialAd();
    MSLoadHiddenBannerAd();
}

static void MSPrepareAdObjects(void) {
    if (!MSDelegate) return;
    if (!MSRewarded && MSRewardedAdUnitId.length > 0) {
        MSRewarded = [[LPMRewardedAd alloc] initWithAdUnitId:MSRewardedAdUnitId];
        [MSRewarded setDelegate:MSDelegate];
    }
    if (!MSInterstitial && MSInterstitialAdUnitId.length > 0) {
        MSInterstitial = [[LPMInterstitialAd alloc] initWithAdUnitId:MSInterstitialAdUnitId];
        [MSInterstitial setDelegate:MSDelegate];
    }
    MSReloadAllAds();
}

static void MSInitializeLevelPlay(BOOL gdprConsent, BOOL ccpaOptOut) {
    if (MSInitialized || MSInitializationStarted) return;

    @try {
        NSDictionary *info = NSBundle.mainBundle.infoDictionary;
        NSString *appKey = info[@"MSLevelPlayAppKey"];
        NSString *rewardedId = info[@"MSLevelPlayRewardedAdUnitId"];
        NSString *interstitialId = info[@"MSLevelPlayInterstitialAdUnitId"];
        NSString *bannerId = info[@"MSLevelPlayBannerAdUnitId"];
        if (appKey.length == 0 || rewardedId.length == 0) {
            MSLog(@"SDK disabled: missing iOS app key or ad-unit ID");
            return;
        }
        if (interstitialId.length == 0) interstitialId = rewardedId;
        if (bannerId.length == 0) bannerId = rewardedId;
        MSBannerAdUnitId = bannerId;

        // Match Android: an explicit user choice is applied before LevelPlay init.
        [LPMPrivacySettings setGDPRConsent:gdprConsent];
        [LPMPrivacySettings setCCPA:ccpaOptOut];
        [LPMPrivacySettings setCOPPA:NO];
        MSLog(@"privacy applied explicitChoice=1 gdprConsent=%d ccpaOptOut=%d childDirected=0",
              gdprConsent, ccpaOptOut);

        MSInitializationStarted = YES;
        MSDelegate = [MSLevelPlayDelegate new];
        LPMInitRequest *request = [[[LPMInitRequestBuilder alloc] initWithAppKey:appKey] build];
        MSLog(@"initializing SDK version=%@", [LevelPlay sdkVersion]);
        [LevelPlay initWithRequest:request completion:^(LPMConfiguration *config, NSError *error) {
            @try {
                if (error) {
                    MSInitializationStarted = NO;
                    MSLog(@"init failed: %@", error);
                    MSResolve(@"failed", @"sdk_init_failed");
                    return;
                }
                MSInitialized = YES;
                MSRewardedAdUnitId = rewardedId;
                MSInterstitialAdUnitId = interstitialId;
                MSLog(@"init complete; LevelPlay SDK ready");
                MSPrepareAdObjects();
                MSTryShowPending();
            } @catch (NSException *e) {
                MSLog(@"LevelPlay completion block caught exception: %@ - %@", e.name, e.reason);
            }
        }];
    } @catch (NSException *e) {
        MSInitializationStarted = NO;
        MSLog(@"MSInitializeLevelPlay caught exception: %@ - %@", e.name, e.reason);
        MSResolve(@"failed", @"init_exception");
    }
}

static void MSSavePrivacyChoiceAndInitialize(BOOL personalized) {
    BOOL ccpaOptOut = !personalized;
    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    [defaults setBool:YES forKey:MSPrivacyChoiceSavedKey];
    [defaults setBool:personalized forKey:MSGDPRConsentKey];
    [defaults setBool:ccpaOptOut forKey:MSCCPAOptOutKey];
    MSPrivacyPromptVisible = NO;
    MSInitializeLevelPlay(personalized, ccpaOptOut);
}

static void MSPresentPrivacyChoice(void) {
    if (MSPrivacyPromptVisible || !MSRootViewController) return;
    MSPrivacyPromptVisible = YES;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(500 * NSEC_PER_MSEC)),
                   dispatch_get_main_queue(), ^{
        @try {
            UIViewController *presenter = MSRootViewController;
            while (presenter.presentedViewController) presenter = presenter.presentedViewController;
            if (!presenter.view.window) {
                MSLog(@"Presenter view not yet in window hierarchy; initializing with safe default");
                MSSavePrivacyChoiceAndInitialize(NO);
                return;
            }
            UIAlertController *alert = [UIAlertController
                alertControllerWithTitle:@"Advertising privacy"
                message:@"Choose how ads are served. Both choices keep Masterstrap usable. Privacy policy: https://masterstrap.org/privacy/"
                preferredStyle:UIAlertControllerStyleAlert];
            [alert addAction:[UIAlertAction actionWithTitle:@"Personalized ads"
                                                      style:UIAlertActionStyleDefault
                                                    handler:^(UIAlertAction *action) {
                MSSavePrivacyChoiceAndInitialize(YES);
            }]];
            [alert addAction:[UIAlertAction actionWithTitle:@"Non-personalized ads"
                                                      style:UIAlertActionStyleDefault
                                                    handler:^(UIAlertAction *action) {
                MSSavePrivacyChoiceAndInitialize(NO);
            }]];
            [presenter presentViewController:alert animated:YES completion:nil];
            MSLog(@"LevelPlay init deferred: awaiting explicit ad privacy choice");
        } @catch (NSException *e) {
            MSLog(@"Privacy prompt presentation caught exception: %@ - %@", e.name, e.reason);
            MSSavePrivacyChoiceAndInitialize(NO);
        }
    });
}

void MSLevelPlayConfigure(void *rootViewController, void *webView) {
    if (rootViewController) {
        MSRootViewController = (__bridge UIViewController *)rootViewController;
    }
    if (webView) {
        MSWebView = (__bridge WKWebView *)webView;
    }
    if (MSInitialized || MSInitializationStarted) return;
    if (!MSRootViewController) return;

    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    if (![defaults boolForKey:MSPrivacyChoiceSavedKey]) {
        MSPresentPrivacyChoice();
        return;
    }
    MSInitializeLevelPlay([defaults boolForKey:MSGDPRConsentKey],
                          [defaults boolForKey:MSCCPAOptOutKey]);
}

void MSLevelPlayRequest(const char *format, const char *requestId) {
    NSString *kind = format ? [NSString stringWithUTF8String:format] : @"";
    NSString *rid = requestId ? [NSString stringWithUTF8String:requestId] : @"";
    if (rid.length == 0) {
        MSLog(@"ad request rejected: missing request id");
        return;
    }
    if (MSPendingRequest) {
        MSDeliverResult(rid, kind,
                        [kind isEqualToString:@"interstitial"] ? @"continue" : @"failed",
                        @"ad_busy");
        return;
    }
    MSPendingRequest = rid;
    MSPendingFormat = kind;
    MSRewardEarned = NO;
    MSPendingUsingInterstitialFallback = NO;
    MSPendingUsingBannerFallback = NO;
    if (!MSInitialized) {
        if (MSInitializationStarted) {
            MSLog(@"request=%@ format=%@ waiting for SDK initialization", rid, kind);
            return;
        }
        MSResolve([kind isEqualToString:@"interstitial"] ? @"continue" : @"failed",
                  @"sdk_unavailable");
        return;
    }
    @try {
        MSPrepareAdObjects();
        if ([kind isEqualToString:@"rewarded"]) {
            MSTryShowPending();
        } else if ([kind isEqualToString:@"interstitial"]) {
            MSTryShowPending();
        } else {
            MSResolve(@"failed", @"unsupported_format");
        }
    } @catch (NSException *e) {
        MSLog(@"MSLevelPlayRequest exception: %@ - %@", e.name, e.reason);
        MSResolve([kind isEqualToString:@"interstitial"] ? @"continue" : @"failed", @"exception");
    }
}

void MSLevelPlaySetBannerVisible(int visible) {
    if (!MSInitialized || !MSRootViewController) return;
    @try {
        if (!visible) {
            [MSBanner destroy];
            [MSBanner removeFromSuperview];
            MSBanner = nil;
            MSLog(@"settings banner hidden");
            return;
        }
        if (MSBanner) return;
        NSString *bannerId = MSBannerAdUnitId ?: NSBundle.mainBundle.infoDictionary[@"MSLevelPlayBannerAdUnitId"];
        if (bannerId.length == 0) bannerId = MSRewardedAdUnitId;
        if (bannerId.length == 0) return;
        MSBanner = [[LPMBannerAdView alloc] initWithAdUnitId:bannerId];
        [MSBanner setDelegate:MSDelegate];
        MSBanner.translatesAutoresizingMaskIntoConstraints = NO;
        [MSRootViewController.view addSubview:MSBanner];
        [NSLayoutConstraint activateConstraints:@[
            [MSBanner.centerXAnchor constraintEqualToAnchor:MSRootViewController.view.centerXAnchor],
            [MSBanner.bottomAnchor constraintEqualToAnchor:MSRootViewController.view.safeAreaLayoutGuide.bottomAnchor constant:-68.0],
            [MSBanner.widthAnchor constraintEqualToConstant:320.0],
            [MSBanner.heightAnchor constraintEqualToConstant:50.0]
        ]];
        [MSBanner loadAdWithViewController:MSRootViewController];
        MSLog(@"settings banner requested");
    } @catch (NSException *e) {
        MSLog(@"MSLevelPlaySetBannerVisible caught exception: %@ - %@", e.name, e.reason);
    }
}

void MSLevelPlayTriggerBackgroundBannerRefresh(void) {
    if (!MSInitialized || !MSRootViewController) return;
    NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];
    if (now - MSLastHiddenBannerReloadTime >= 60.0) {
        MSLog(@"refreshing hidden banner after 60s cooldown (user interaction triggered)");
        MSLoadHiddenBannerAd();
    }
}
