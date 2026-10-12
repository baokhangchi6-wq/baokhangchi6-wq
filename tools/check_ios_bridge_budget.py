#!/usr/bin/env python3
"""Run real launch/CHECK JavaScript with deferred requests, plus native patch safety."""
import contextlib
import io
import re
import subprocess
import tempfile
from pathlib import Path

from build_masterstrap_app_ipa import HTML_TEMPLATE_RAW, configure_ios_runtime_html
from build_integrated_roblox_ipa import patch_robloxlib_fps_clamps, generate_native_hook_source

with contextlib.redirect_stdout(io.StringIO()):
    html = configure_ios_runtime_html(HTML_TEMPLATE_RAW)
    unknown = bytearray(0x93B550)
    assert patch_robloxlib_fps_clamps(unknown) == bytes(len(unknown)), 'Unknown binary was patched'
    source = generate_native_hook_source([], [], 0, [], {})
    assert 'strcmp(version, "3") == 0) protocol_version = 3;' in source

launch = html.split('const msSaveAndLaunch = saveAndLaunch;', 1)[1].split('// Cold Start', 1)[0]
interaction = '// Additional interaction placement;' + html.split('// Additional interaction placement;', 1)[1].split('// VIP Hard-Lock Function', 1)[0]
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
let now = 100000, ads = 0, free = true, busy = false;
const listeners = new Map(), adValues = new Map();
const adContext = {
  Date: { now: () => now },
  MasterstrapAds: { isFree: () => free, busy: () => busy, request: (format, placement) => {
    assert.equal(format, 'interstitial'); assert.equal(placement, 'interaction_30s'); ads++;
    return Promise.resolve(true);
  } },
  document: { visibilityState: 'visible', addEventListener: (name, fn) => listeners.set(name, fn) },
  localStorage: { getItem: key => adValues.get(key) ?? null }
};
vm.createContext(adContext); vm.runInContext(__INTERACTION_SOURCE__, adContext);
const element = (range = false, excluded = false) => ({ disabled: false,
  closest: query => query.includes('button,') ? elementRef : excluded ? {} : null,
  matches: () => range });
let elementRef = element();
function fire(kind) { listeners.get(kind)({type:kind,target:elementRef}); }
fire('click'); assert.equal(ads,1);
now += 29999; fire('click'); assert.equal(ads,1);
now++; fire('click'); assert.equal(ads,2);
elementRef = element(true); now += 30000; fire('click'); assert.equal(ads,2);
fire('change'); assert.equal(ads,3);
elementRef = element(false,true); now += 30000; fire('click'); assert.equal(ads,3);
elementRef = element(); free = false; fire('click'); assert.equal(ads,3);
free = true; adContext.document.visibilityState = 'hidden'; fire('click'); assert.equal(ads,3);
adContext.document.visibilityState = 'visible'; adValues.set('masterstrap_ads_last_interstitial_ms',now);
fire('click'); assert.equal(ads,3);
now += 30000; fire('click'); assert.equal(ads,4);
now += 30000; busy = true; fire('click'); assert.equal(ads,4);
console.log('PASS: 100 repeated launch clicks produce one ad/launch; unknown binary stays unchanged');
'''
import json
script = script.replace('__LAUNCH_SOURCE__', json.dumps(launch)).replace('__INTERACTION_SOURCE__', json.dumps(interaction))
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / 'bridge-budget.mjs'
    path.write_text(script, encoding='utf-8')
    subprocess.run(['node', str(path)], check=True)
