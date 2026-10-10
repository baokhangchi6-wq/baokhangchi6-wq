#!/usr/bin/env python3
"""Run real launch/CHECK JavaScript with deferred requests, plus native patch safety."""
import contextlib
import io
import re
import subprocess
import tempfile
from pathlib import Path

from build_masterstrap_app_ipa import HTML_TEMPLATE_RAW, configure_ios_runtime_html
from build_integrated_roblox_ipa import patch_robloxlib_fps_clamps

with contextlib.redirect_stdout(io.StringIO()):
    html = configure_ios_runtime_html(HTML_TEMPLATE_RAW)
    unknown = bytearray(0x93B550)
    assert patch_robloxlib_fps_clamps(unknown) == bytes(len(unknown)), 'Unknown binary was patched'

launch = html.split('const msSaveAndLaunch = saveAndLaunch;', 1)[1].split('// Cold Start', 1)[0]
check = html.split('let msLicenseCheckInFlight = false;', 1)[1].split('\nfunction ', 1)[0]
script = r'''
import assert from 'node:assert/strict';
import vm from 'node:vm';
let launches = 0, requests = 0, completeAd;
const context = { window: {}, showToast() {}, getI18nText: key => key,
  saveAndLaunch: () => { launches++; },
  MasterstrapAds: { isFree: () => true, request: () => { requests++; return new Promise(resolve => completeAd = resolve); } } };
vm.createContext(context);
vm.runInContext('const msSaveAndLaunch = saveAndLaunch;' + __LAUNCH_SOURCE__, context);
for (let i = 0; i < 100; i++) context.saveAndLaunch();
assert.equal(requests, 1);
completeAd(true); await new Promise(resolve => setImmediate(resolve));
assert.equal(launches, 1);
context.window.msLaunchFinished(true); context.MasterstrapAds.isFree = () => false;
context.saveAndLaunch(); context.saveAndLaunch(); assert.equal(launches, 2);
context.window.msLaunchFinished(false); context.saveAndLaunch(); assert.equal(launches, 3);
let checks = 0, finishCheck;
const values = new Map();
Object.assign(context, { document: { getElementById: () => ({value:'VALID-TEST-LICENSE'}), querySelector: () => ({disabled:false}) },
  localStorage: {getItem:key=>values.get(key)||null, setItem:(key,value)=>values.set(key,value)},
  fetch: () => { checks++; return new Promise(resolve => finishCheck=resolve); },
  checkLicenseExpired:()=>false, saveLicenseStorage(){}, clearLicenseStorage(){}, updateLicenseUI(){} });
vm.runInContext('let msLicenseCheckInFlight = false;' + __CHECK_SOURCE__, context);
const a=context.activateLicenseKey(), b=context.activateLicenseKey(); assert.equal(checks,1);
finishCheck({ok:true,json:async()=>({isValid:true,plan:'Plus',expirationDate:'2030-01-01'})});await Promise.all([a,b]);
const c=context.activateLicenseKey();assert.equal(checks,2);
finishCheck({ok:true,json:async()=>({isValid:false})});await c;
console.log('PASS: 100 repeated launch clicks produce one ad/launch; overlapping licence CHECK produces one fetch; unknown binary stays unchanged');
'''
import json
script = script.replace('__LAUNCH_SOURCE__', json.dumps(launch)).replace('__CHECK_SOURCE__', json.dumps(check))
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'bridge-budget.mjs'
    path.write_text(script, encoding='utf-8')
    subprocess.run(['node', str(path)], check=True)
