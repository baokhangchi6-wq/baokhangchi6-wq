#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <WebKit/WebKit.h>
extern void app_log(const char *format, ...);

static NSDictionary *ms_license_query(void) {
    return @{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
             (__bridge id)kSecAttrService:@"com.masterstrap.app.license",
             (__bridge id)kSecAttrAccount:@"key-and-device"};
}

@interface MSLicenseStore : NSObject <WKScriptMessageHandlerWithReply>
@end
@implementation MSLicenseStore
- (void)userContentController:(WKUserContentController *)controller
       didReceiveScriptMessage:(WKScriptMessage *)message replyHandler:(void (^)(id, NSString *))reply {
    if (!message.frameInfo.isMainFrame || ![message.frameInfo.securityOrigin.protocol isEqual:@"file"] ||
        ![message.body isKindOfClass:NSDictionary.class]) {
        reply(nil,@"Invalid license storage request"); return;
    }
    NSDictionary *body=message.body, *query=ms_license_query();
    if ([body[@"action"] isEqual:@"load"]) {
        NSMutableDictionary *read=[query mutableCopy];
        read[(__bridge id)kSecReturnData]=@YES;
        CFTypeRef result=NULL; OSStatus status=SecItemCopyMatching((__bridge CFDictionaryRef)read,&result);
        app_log("[LICENSE][KEYCHAIN] action=LOAD status=%d",(int)status);
        if(status==errSecItemNotFound) {reply(@{@"key":@"",@"deviceId":@"",@"defaultDeviceId":NSUUID.UUID.UUIDString},nil);return;}
        if(status!=errSecSuccess) {reply(nil,[NSString stringWithFormat:@"License storage unavailable (%d)",(int)status]);return;}
        id record=[NSJSONSerialization JSONObjectWithData:CFBridgingRelease(result) options:0 error:NULL];
        if(![record isKindOfClass:NSDictionary.class]) {reply(nil,@"Invalid license storage");return;}
        reply(record,nil);return;
    }
    NSString *key=body[@"key"], *device=body[@"deviceId"];
    if(![body[@"action"] isEqual:@"save"] || ![key isKindOfClass:NSString.class] ||
       ![device isKindOfClass:NSString.class] || key.length>64 || !device.length || device.length>128) {
        reply(nil,@"Invalid license storage request");return;
    }
    NSData *data=[NSJSONSerialization dataWithJSONObject:@{@"key":key,@"deviceId":device} options:0 error:NULL];
    NSDictionary *attributes=@{(__bridge id)kSecValueData:data,
        (__bridge id)kSecAttrAccessible:(__bridge id)kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly};
    OSStatus status=SecItemUpdate((__bridge CFDictionaryRef)query,(__bridge CFDictionaryRef)attributes);
    if(status==errSecItemNotFound) {
        NSMutableDictionary *item=[query mutableCopy];[item addEntriesFromDictionary:attributes];
        status=SecItemAdd((__bridge CFDictionaryRef)item,NULL);
    }
    reply(status==errSecSuccess?@YES:nil,status==errSecSuccess?nil:[NSString stringWithFormat:@"License storage unavailable (%d)",(int)status]);
    app_log("[LICENSE][KEYCHAIN] action=SAVE status=%d",(int)status);
}
@end

void ms_install_license_store(WKUserContentController *controller) {
    [controller addScriptMessageHandlerWithReply:[MSLicenseStore new] contentWorld:WKContentWorld.pageWorld name:@"licenseStore"];
}
