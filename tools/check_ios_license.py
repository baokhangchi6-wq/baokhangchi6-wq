"""Exercise emitted Desync and licence JS; compile Keychain bridge on Apple CI."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from build_masterstrap_app_ipa import HTML_TEMPLATE_RAW, configure_ios_runtime_html, ROOT

with contextlib.redirect_stdout(io.StringIO()):
    html=configure_ios_runtime_html(HTML_TEMPLATE_RAW)
flags=html[html.index('function updateFlagState()'):html.index('function updateFlagState()')+40000]
start=flags.index('// Retire only'); end=flags.index('\n    if (document.getElementById("sw-ffs-faster-speed")',start)
desync=flags[start:end]
license_source=(ROOT/'tools/ios_license.js').read_text(encoding='utf-8')
script=r'''
import assert from 'node:assert/strict';import vm from 'node:vm';
const flags={activeFlags:{DFIntDataSenderRate:38760,DFIntNetworkLatencyTolerance:1},isFreePlanFlags:false,
 document:{getElementById:()=>({checked:true})}};vm.createContext(flags);
vm.runInContext(__DESYNC__,flags);assert.deepEqual(JSON.parse(JSON.stringify(flags.activeFlags)),{DFIntS2PhysicsSenderRate:'38760'});
flags.document.getElementById=()=>({checked:false});vm.runInContext(__DESYNC__,flags);assert.equal(Object.keys(flags.activeFlags).length,0);
const data=new Map([['masterstrap_license_key','VALID-TEST-KEY'],['masterstrap_device_id','BOUND-DEVICE'],['masterstrap_license_plan','Plus']]);
let stored=null,requests=0,finish,updates=0,expires;
const context={window:{webkit:{messageHandlers:{licenseStore:{postMessage:async body=>{
 if(body.action==='load')return stored||{key:'',deviceId:'',defaultDeviceId:'NEW-DEVICE'};
 stored={key:body.key,deviceId:body.deviceId};return true;}}}}},
 document:{getElementById:()=>({value:'VALID-TEST-KEY'}),querySelector:()=>({disabled:false})},
 localStorage:{getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)},
 fetch:(url,options)=>{requests++;assert.equal(JSON.parse(options.body).deviceId,'BOUND-DEVICE');assert.equal(options.headers['Content-Type'],'text/plain;charset=UTF-8');return new Promise(resolve=>finish=resolve);},
 updateLicenseUI:()=>updates++,showToast(){},getI18nText:k=>k,AbortController,performance,
 setTimeout:(cb,ms)=>{if(ms!==10000)expires=cb;return 1;},clearTimeout(){},Date};
vm.createContext(context);vm.runInContext(__LICENSE__,context);
const wait=()=>new Promise(resolve=>setImmediate(resolve));
const good=()=>({ok:true,json:async()=>({isValid:true,plan:'Plus',expirationDate:'2030-01-01T00:00:00Z',serverTime:new Date().toISOString(),bindingVerified:true})});
const boot=vm.runInContext('msLicenseBoot()',context);await wait();assert.equal(requests,1);
finish(good());await boot;assert.equal(context.window.msLicenseAuthorized,true);
assert.equal(stored.deviceId,'BOUND-DEVICE');assert(!data.has('masterstrap_license_key'));
context.window.msLicenseForeground();context.window.msLicenseForeground();assert.equal(requests,2);
finish({ok:false,status:503,json:async()=>({errorCode:'ServiceUnavailable'})});await wait();
assert.equal(context.window.msLicenseAuthorized,false);assert.equal(context.window.msLicenseUnknown,true);assert.equal(stored.key,'VALID-TEST-KEY');
context.window.msLicenseForeground();finish(good());await wait();assert.equal(context.window.msLicenseAuthorized,true);
context.window.msLicenseForeground();finish({ok:true,json:async()=>({isValid:false,errorCode:'ActivationRevoked'})});await wait();
assert.equal(stored.key,'');assert.equal(stored.deviceId,'BOUND-DEVICE');assert.equal(context.window.msLicenseAuthorized,false);
context.window.msLicenseForeground();assert.equal(requests,4);
vm.runInContext("msLicenseRecord.key='VALID-TEST-KEY'",context);
stored.key='VALID-TEST-KEY';
const a=context.activateLicenseKey(),b=context.activateLicenseKey();assert.equal(requests,5);finish(good());await Promise.all([a,b]);
assert.equal(context.window.msLicenseAuthorized,true);assert(updates>0);
context.window.msLicenseForeground();finish({ok:true,json:async()=>({isValid:true,plan:'Plus',expirationDate:'invalid',serverTime:new Date().toISOString(),bindingVerified:true})});await wait();
assert.equal(context.window.msLicenseAuthorized,false);assert.equal(stored.key,'VALID-TEST-KEY');
context.window.msLicenseForeground();finish({ok:true,json:async()=>({isValid:true,plan:'Max',expirationDate:null,serverTime:new Date().toISOString(),bindingVerified:true})});await wait();
assert.equal(context.window.msLicenseAuthorized,true);
context.document.getElementById=()=>({value:''});await context.activateLicenseKey();
assert.equal(stored.key,'');assert.equal(context.window.msLicenseAuthorized,false);
context.document.getElementById=()=>({value:'INVALID-25-CHAR-KEY-LONG-EX'});
const inv=context.activateLicenseKey();assert.equal(requests,8);finish({ok:true,json:async()=>({isValid:false,errorCode:'InvalidKey'})});await inv;
assert.equal(stored.key,'');assert.equal(context.window.msLicenseAuthorized,false);
context.window.msLicenseForeground();finish({ok:true,json:async()=>({isValid:false,errorCode:'LicenseExpired'})});await wait();
assert.equal(stored.key,'');assert.equal(context.window.msLicenseAuthorized,false);
assert.equal(vm.runInContext("checkLicenseExpired('Plus','bad')",context),true);
assert.equal(vm.runInContext("checkLicenseExpired('Max','')",context),false);
console.log('PASS: single Desync flag; Keychain migration keeps binding; one status request; reset revokes; 503 keeps key; CHECK coalesces; empty/invalid resets to Free');
'''.replace('__DESYNC__',json.dumps(desync)).replace('__LICENSE__',json.dumps(license_source))
with tempfile.TemporaryDirectory() as directory:
    path=Path(directory)/'license.mjs';path.write_text(script,encoding='utf-8')
    subprocess.run(['node',str(path)],check=True)
    if sys.platform=='darwin':
        subprocess.run(['xcrun','clang','-fobjc-arc','-fblocks','-Werror','-c',str(ROOT/'tools/ios_license_store.m'),
                        '-o',str(Path(directory)/'license.o')],check=True)
        # Execute the actual WK reply bridge and Keychain in the isolated macOS runner.
        native=(ROOT/'tools/ios_license_store.m').read_text(encoding='utf-8').replace('com.masterstrap.app.license','com.masterstrap.ci.license')
        native+=r'''
#import <AppKit/AppKit.h>
void app_log(const char *format, ...) {}
int main(void) { @autoreleasepool {
    [NSApplication sharedApplication];
    WKWebViewConfiguration *config=[WKWebViewConfiguration new];ms_install_license_store(config.userContentController);
    WKWebView *view=[[WKWebView alloc] initWithFrame:NSMakeRect(0,0,200,200) configuration:config];
    NSString *html=@"<script>(async()=>{try{const s=window.webkit.messageHandlers.licenseStore;let r=await s.postMessage({action:'load'});await s.postMessage({action:'save',key:'CI-TEST-KEY',deviceId:'CI-DEVICE'});r=await s.postMessage({action:'load'});window.result=(r.key==='CI-TEST-KEY'&&r.deviceId==='CI-DEVICE')?'PASS':'INVALID';}catch(e){window.result=String(e)}})()</script>";
    [view loadHTMLString:html baseURL:[NSURL fileURLWithPath:NSTemporaryDirectory() isDirectory:YES]];
    __block NSString *result=nil;NSDate *end=[NSDate dateWithTimeIntervalSinceNow:25];
    while(!result && end.timeIntervalSinceNow>0) {
        [view evaluateJavaScript:@"window.result" completionHandler:^(id value,NSError *error){if([value isKindOfClass:NSString.class])result=value;}];
        [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.1]];
    }
    SecItemDelete((__bridge CFDictionaryRef)ms_license_query());
    if(![result isEqual:@"PASS"]) {fprintf(stderr,"WK/Keychain: %s\n",result.UTF8String?:"timeout");return 1;}
    puts("PASS real WK reply bridge and Keychain read/write");return 0;
}}
'''
        main=Path(directory)/'native.m';main.write_text(native,encoding='utf-8')
        binary=Path(directory)/'native'
        subprocess.run(['xcrun','clang','-fobjc-arc','-fblocks',str(main),'-o',str(binary),
                        '-framework','Foundation','-framework','Security','-framework','WebKit','-framework','AppKit'],check=True)
        subprocess.run(['codesign','--force','--sign','-',str(binary)],check=True)
        subprocess.run([str(binary)],check=True,timeout=35)
