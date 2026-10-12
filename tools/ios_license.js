// Native Keychain holds credentials. localStorage holds display/preferences only.
window.msLicenseAuthorized=false;
window.msLicensePending=true;
window.msLicenseUnknown=false;
let msLicenseRecord={key:'',deviceId:''}, msLicenseFlight=null, msLicenseGeneration=0, msLicenseReady=false, msLicenseExpiryTimer, msLicenseClockOffset=0;
const msLicenseStore = body => {
    const store=window.webkit?.messageHandlers?.licenseStore;
    if(!store)return Promise.reject(new Error('Keychain unavailable'));
    return store.postMessage(body);
};
const msLicenseKey = () => msLicenseRecord.key;
const msLicenseRetry = () => showToast(getI18nText('msg_license_retry'));
const msLicenseLog = message => window.webkit?.messageHandlers?.log?.postMessage('[LICENSE] '+message);

function checkLicenseExpired(plan,expiry) {
    if(String(plan).toLowerCase()==='max' && (!expiry||expiry==='Lifetime'))return false;
    const deadline=Date.parse(expiry);
    return !Number.isFinite(deadline)||Date.now()+msLicenseClockOffset>=deadline;
}
async function clearLicenseStorage() {
    msLicenseGeneration++;
    window.msLicenseAuthorized=false; window.msLicenseUnknown=false;
    clearTimeout(msLicenseExpiryTimer);
    msLicenseRecord.key='';
    localStorage.removeItem('masterstrap_license_key');
    localStorage.removeItem('masterstrap_license_active');
    localStorage.removeItem('masterstrap_license_expiry');
    localStorage.setItem('masterstrap_license_plan','Free');
    try{await msLicenseStore({action:'save',...msLicenseRecord});}catch{msLicenseRetry();}
}
function msLicenseShow() {
    updateLicenseUI(window.msLicenseAuthorized,localStorage.getItem('masterstrap_license_plan')||'Free',localStorage.getItem('masterstrap_license_expiry')||'');
    const field=document.getElementById('license-key-input');if(field)field.value=msLicenseKey();
    if(window.MasterstrapAds) MasterstrapAds.banner(!window.msLicenseAuthorized);
}
async function msLicenseRequest(key,action) {
    if(msLicenseFlight)return msLicenseFlight;
    const generation=msLicenseGeneration;
    window.msLicensePending=true;
    if(action==='status')window.msLicenseAuthorized=false;
    msLicenseFlight=(async()=>{
        const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),10000);
        try{
            // Safelisted content type avoids a CORS preflight; body remains JSON.
            const response=await fetch('https://license.masterstrap.org/api/ios-licenses/'+action,{
                method:'POST',headers:{'Content-Type':'text/plain;charset=UTF-8','Accept':'application/json'},
                body:JSON.stringify({key,deviceId:msLicenseRecord.deviceId,clientInfo:'masterstrap-ios/1.3'}),signal:controller.signal});
            const data=await response.json();
            msLicenseLog('action='+action+' status='+response.status+' valid='+(data?.isValid===true));
            if(generation!==msLicenseGeneration)return;
            if(!response.ok||typeof data?.isValid!=='boolean')throw new Error('License unconfirmed');
            if(!data.isValid){
                if(key===msLicenseKey())await clearLicenseStorage();
                window.msLicenseUnknown=false;
                return;
            }
            const plan=String(data.plan), expiry=data.expirationDate||'';
            const serverNow=action==='status'?Date.parse(data.serverTime):Date.now();
            const deadline=expiry?Date.parse(expiry):null;
            if(!['Plus','Max'].includes(plan)||!Number.isFinite(serverNow)||
                (plan==='Plus'&&deadline===null)||(deadline!==null&&(!Number.isFinite(deadline)||deadline<=serverNow))||
                (action==='status'&&data.bindingVerified!==true))throw new Error('Invalid entitlement');
            if(key!==msLicenseKey()){
                await msLicenseStore({action:'save',key,deviceId:msLicenseRecord.deviceId});
                if(generation!==msLicenseGeneration)return;
                msLicenseRecord.key=key;
            }
            localStorage.removeItem('masterstrap_license_key');
            localStorage.setItem('masterstrap_license_plan',plan);
            localStorage.setItem('masterstrap_license_expiry',expiry);
            localStorage.setItem('masterstrap_license_active','true');
            msLicenseClockOffset=serverNow-Date.now();
            window.msLicenseAuthorized=true; window.msLicenseUnknown=false;
            clearTimeout(msLicenseExpiryTimer);
            if(deadline!==null){
                const end=performance.now()+deadline-serverNow;
                const expire=()=>{
                    if(generation!==msLicenseGeneration)return;
                    const remaining=end-performance.now();
                    if(remaining>0){msLicenseExpiryTimer=setTimeout(expire,Math.min(remaining,2147483647));return;}
                    clearLicenseStorage().then(msLicenseShow);
                };expire();
            }
        }catch{
            msLicenseLog('action='+action+' status=UNCONFIRMED');
            if(generation!==msLicenseGeneration)return;
            if(action==='status')window.msLicenseAuthorized=false;
            window.msLicenseUnknown=!!msLicenseKey();
            msLicenseRetry();
        }finally{
            clearTimeout(timeout);
            msLicenseFlight=null;window.msLicensePending=false;
            if(msLicenseReady){
                if(window.msLicenseAuthorized && typeof SettingsPersistence!=='undefined')SettingsPersistence.restoreAllUI();
                msLicenseShow();
                if(typeof syncConfigToNative==='function')syncConfigToNative();
            }
        }
    })();
    return msLicenseFlight;
}
async function activateLicenseKey() {
    if(msLicenseFlight||window.msLicensePending)return;
    const field=document.getElementById('license-key-input'), key=String(field?.value||'').trim().toUpperCase();
    if(!/^[A-Z0-9-]{8,64}$/.test(key)){msLicenseRetry();return;}
    const button=document.querySelector('.license-activate-btn');if(button)button.disabled=true;
    try{await msLicenseRequest(key,'validate');}finally{if(button)button.disabled=false;}
}
window.msLicenseForeground=()=>{
    if(!msLicenseReady||!msLicenseKey())return;
    msLicenseRequest(msLicenseKey(),'status');
};
async function msLicenseBoot() {
    try{
        const record=await msLicenseStore({action:'load'});
        if(typeof record?.key!=='string'||typeof record?.deviceId!=='string')throw new Error('Invalid Keychain record');
        msLicenseRecord={key:record.key,deviceId:record.deviceId};
        if(!msLicenseRecord.deviceId){
            msLicenseRecord.key=String(localStorage.getItem('masterstrap_license_key')||'').trim().toUpperCase();
            msLicenseRecord.deviceId=localStorage.getItem('masterstrap_device_id')||record.defaultDeviceId;
            await msLicenseStore({action:'save',...msLicenseRecord});
        }
        localStorage.removeItem('masterstrap_license_key');
        localStorage.removeItem('masterstrap_device_id');
        if(msLicenseKey())await msLicenseRequest(msLicenseKey(),'status');
        else {localStorage.removeItem('masterstrap_license_active');localStorage.setItem('masterstrap_license_plan','Free');}
    }catch{window.msLicenseAuthorized=false;window.msLicenseUnknown=true;msLicenseRetry();}
    finally{window.msLicensePending=false;msLicenseReady=true;}
}
