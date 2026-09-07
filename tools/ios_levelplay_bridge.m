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
static NSString *MSPendingRequest;
static NSString *MSPendingFormat;
static NSString *MSRewardedAdUnitId;
static NSString *MSInterstitialAdUnitId;
static BOOL MSRewardEarned;
static BOOL MSInitialized;
static BOOL MSInitializationStarted;
static BOOL MSPrivacyPromptVisible;
static BOOL MSAdShowing;

static NSString *const MSPrivacyChoiceSavedKey = @"masterstrap_ads_privacy_choice_saved";
static NSString *const MSGDPRConsentKey = @"masterstrap_ads_gdpr_consent";
static NSString *const MSCCPAOptOutKey = @"masterstrap_ads_ccpa_opt_out";

static void MSLog(NSString *format, ...) {
    va_list args;
    va_start(args, format);
    NSString *message = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);
    NSLog(@"[Masterstrap-LevelPlay] %@", message);
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
}

static void MSTryShowPending(void) {
    if (!MSInitialized || !MSPendingRequest || MSAdShowing || !MSRootViewController) return;
    if ([MSPendingFormat isEqualToString:@"rewarded"] && [MSRewarded isAdReady]) {
        MSAdShowing = YES;
        [MSRewarded showAdWithViewController:MSRootViewController placementName:nil];
    } else if ([MSPendingFormat isEqualToString:@"interstitial"] && [MSInterstitial isAdReady]) {
        MSAdShowing = YES;
        [MSInterstitial showAdWithViewController:MSRootViewController placementName:nil];
    }
}

@interface MSLevelPlayDelegate : NSObject
    <LPMRewardedAdDelegate, LPMInterstitialAdDelegate, LPMBannerAdViewDelegate>
@end

@implementation MSLevelPlayDelegate
- (void)didLoadAdWithAdInfo:(LPMAdInfo *)adInfo {
    MSLog(@"ad loaded: %@", adInfo);
    MSTryShowPending();
}
- (void)didFailToLoadAdWithAdUnitId:(NSString *)adUnitId error:(NSError *)error {
    MSLog(@"ad load failed unit=%@ error=%@", adUnitId, error);
    if ([MSPendingFormat isEqualToString:@"interstitial"] &&
        [adUnitId isEqualToString:MSInterstitialAdUnitId]) {
        MSResolve(@"continue", @"interstitial_unavailable");
    } else if ([MSPendingFormat isEqualToString:@"rewarded"] &&
               [adUnitId isEqualToString:MSRewardedAdUnitId]) {
        MSResolve(@"failed", @"rewarded_unavailable");
    }
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
    MSResolve([MSPendingFormat isEqualToString:@"interstitial"] ? @"continue" : @"failed",
              @"display_failed");
}
- (void)didCloseAdWithAdInfo:(LPMAdInfo *)adInfo {
    BOOL rewarded = [MSPendingFormat isEqualToString:@"rewarded"];
    MSResolve(rewarded ? (MSRewardEarned ? @"rewarded" : @"failed") : @"continue",
              rewarded ? (MSRewardEarned ? @"completed" : @"incomplete") : @"closed");
    MSRewardEarned = NO;
    [MSRewarded loadAd];
    [MSInterstitial loadAd];
}
- (void)didClickAdWithAdInfo:(LPMAdInfo *)adInfo { MSLog(@"ad clicked: %@", adInfo); }
@end

static MSLevelPlayDelegate *MSDelegate;

static void MSInitializeLevelPlay(BOOL gdprConsent, BOOL ccpaOptOut) {
    if (MSInitialized || MSInitializationStarted) return;

    NSDictionary *info = NSBundle.mainBundle.infoDictionary;
    NSString *appKey = info[@"MSLevelPlayAppKey"];
    NSString *rewardedId = info[@"MSLevelPlayRewardedAdUnitId"];
    NSString *interstitialId = info[@"MSLevelPlayInterstitialAdUnitId"];
    NSString *bannerId = info[@"MSLevelPlayBannerAdUnitId"];
    if (appKey.length == 0 || rewardedId.length == 0 ||
        interstitialId.length == 0 || bannerId.length == 0) {
        MSLog(@"SDK disabled: missing iOS app key or ad-unit ID");
        return;
    }

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
        if (error) {
            MSInitializationStarted = NO;
            MSLog(@"init failed: %@", error);
            MSResolve(@"failed", @"sdk_init_failed");
            return;
        }
        MSInitialized = YES;
        MSRewardedAdUnitId = rewardedId;
        MSInterstitialAdUnitId = interstitialId;
        MSRewarded = [[LPMRewardedAd alloc] initWithAdUnitId:rewardedId];
        MSInterstitial = [[LPMInterstitialAd alloc] initWithAdUnitId:interstitialId];
        [MSRewarded setDelegate:MSDelegate];
        [MSInterstitial setDelegate:MSDelegate];
        [MSRewarded loadAd];
        [MSInterstitial loadAd];
        MSLog(@"init complete; fullscreen ads warming");
        MSTryShowPending();
    }];
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
        UIViewController *presenter = MSRootViewController;
        while (presenter.presentedViewController) presenter = presenter.presentedViewController;
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
    });
}

void MSLevelPlayConfigure(void *rootViewController, void *webView) {
    MSRootViewController = (__bridge UIViewController *)rootViewController;
    MSWebView = (__bridge WKWebView *)webView;
    if (MSInitialized || MSInitializationStarted) return;

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
    if (!MSInitialized) {
        if (MSInitializationStarted) {
            MSLog(@"request=%@ format=%@ waiting for SDK initialization", rid, kind);
            return;
        }
        MSResolve([kind isEqualToString:@"interstitial"] ? @"continue" : @"failed",
                  @"sdk_unavailable");
        return;
    }
    if ([kind isEqualToString:@"rewarded"]) {
        MSTryShowPending();
        if (!MSAdShowing) [MSRewarded loadAd];
    } else if ([kind isEqualToString:@"interstitial"]) {
        MSTryShowPending();
        if (!MSAdShowing) [MSInterstitial loadAd];
    } else {
        MSResolve(@"failed", @"unsupported_format");
    }
}

void MSLevelPlaySetBannerVisible(int visible) {
    if (!MSInitialized || !MSRootViewController) return;
    if (!visible) {
        [MSBanner destroy];
        [MSBanner removeFromSuperview];
        MSBanner = nil;
        MSLog(@"settings banner hidden");
        return;
    }
    if (MSBanner) return;
    NSString *bannerId = NSBundle.mainBundle.infoDictionary[@"MSLevelPlayBannerAdUnitId"];
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
}
