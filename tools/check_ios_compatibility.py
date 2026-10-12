"""Scan a supplied IPA corpus without Apple SDK, patching profiles only in memory."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from build_integrated_roblox_ipa import run_local_dumper, verify_ipa_profile, fetch_or_load_fflags_map, patch_robloxlib_fps_clamps
from ios_font_profile import build_font_profile
from ios_render_idle_profile import build_render_idle_profile
from ios_render_roles import build_render_roles_profile, patch_render_roles
from ios_stretch_profile import build_stretch_profile
from fetch_roblox_ios_ipa import validate_ipa_version

def check(ipa,output):
    output.mkdir(parents=True,exist_ok=True)
    header=run_local_dumper(ipa,output)
    uuid=verify_ipa_profile(ipa,header);flags=fetch_or_load_fflags_map(header)
    roles=build_render_roles_profile(ipa,output/'render_roles_profile.json')
    profiles=[roles,build_font_profile(ipa,output/'font_asset_profile.json'),
              build_render_idle_profile(ipa,flags,output/'render_idle_profile.json'),
              build_stretch_profile(ipa,output/'stretch_profile.json')]
    if not flags or not all(p['uuid']==uuid and p['sourceSha256']==roles['sourceSha256'] for p in profiles):
        raise ValueError('Feature identity mismatch')
    if not all(p.get('verified',True) for p in profiles) or not roles.get('fpsVerified') or not roles.get('stretchVerified'):
        raise ValueError('Missing feature contract')
    with zipfile.ZipFile(ipa) as archive:
        binary=archive.read('Payload/Roblox.app/Frameworks/RobloxLib.framework/RobloxLib')
    patched,_=patch_render_roles(bytearray(binary),roles)
    patch_robloxlib_fps_clamps(patched,output/'fps_patch_profile.json')
    fps=json.loads((output/'fps_patch_profile.json').read_text(encoding='utf-8'))
    if fps['resolved']!=fps['required']:raise ValueError('FPS patch graph incomplete')
    version=validate_ipa_version(ipa,'')
    result={'version':version,'uuid':uuid,'sourceSha256':hashlib.sha256(binary).hexdigest(),
            'features':dict.fromkeys(('flag','fps','stretch','font'),True),'deviceVerified':False}
    (output/'compatibility_profile.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ipa',nargs='+',type=Path);parser.add_argument('--out-dir',required=True,type=Path)
    args=parser.parse_args()
    for i,ipa in enumerate(args.ipa):
        result=check(ipa,args.out_dir/str(i));print(json.dumps(result))
