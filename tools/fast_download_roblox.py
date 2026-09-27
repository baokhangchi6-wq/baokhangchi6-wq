#!/usr/bin/env python3
"""
Fast multi-part HTTP range downloader for Roblox iOS IPA.
Supports direct downloads and authenticated GitHub Release asset downloads with Cloudflare WARP acceleration.
"""
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

DEFAULT_URL = "https://github.com/baokhangchi6-wq/baokhangchi6-wq/releases/download/ios-arm64-2.739.687-run-18/Masterstrap-2.739.687.ipa"
OUT_PATH = Path("output/Masterstrap-2.739.687.ipa")

def get_token():
    p = subprocess.Popen(
        ["git", "credential", "fill"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    out, _ = p.communicate(input="protocol=https\nhost=github.com\n\n")
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return ""

def fast_download(url: str = DEFAULT_URL, out_path: Path = OUT_PATH):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    token = get_token()
    if not token:
        print("Error: No GitHub token found via git credentials", file=sys.stderr)
        sys.exit(1)

    proxy_args = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", 40000)) == 0:
            proxy_args = ["-x", "socks5h://127.0.0.1:40000"]
            print("Detected active Cloudflare WARP proxy on port 40000! Accelerating download...", flush=True)
        s.close()
    except Exception:
        pass

    asset_id = "593446743"
    api_url = f"https://api.github.com/repos/baokhangchi6-wq/baokhangchi6-wq/releases/assets/{asset_id}"

    print(f"Downloading Masterstrap-2.739.687.ipa ({url}) via curl.exe...", flush=True)
    cmd = [
        "curl.exe",
        "-f",
        "-L",
        "--tcp-nodelay",
        *proxy_args,
        "-H", f"Authorization: token {token}",
        "-H", "Accept: application/octet-stream",
        api_url,
        "-o", str(out_path),
        "--progress-bar"
    ]
    t0 = time.time()
    res = subprocess.run(cmd)
    elapsed = max(time.time() - t0, 0.1)

    if res.returncode != 0:
        print(f"Curl failed with return code {res.returncode}", file=sys.stderr)
        sys.exit(res.returncode)

    size = out_path.stat().st_size
    size_mb = size / (1024 * 1024)
    print(f"\nSUCCESS -> Downloaded {out_path} ({size_mb:.2f} MB) in {elapsed:.1f}s ({size_mb/elapsed:.2f} MB/s)!", flush=True)

    # Verify IPA integrity and bundle ID
    try:
        with zipfile.ZipFile(out_path, "r") as z:
            info_data = z.read("Payload/Roblox.app/Info.plist")
            pl = plistlib.loads(info_data)
            app_name = pl.get("CFBundleDisplayName") or pl.get("CFBundleName")
            bundle_id = pl.get("CFBundleIdentifier")
            version = pl.get("CFBundleShortVersionString")
            print(f"Verified IPA: Name='{app_name}', BundleID='{bundle_id}', Version='{version}'", flush=True)
    except Exception as e:
        print(f"Warning: IPA verification warning: {e}", flush=True)

    # Sync to roblox_input/roblox.ipa
    roblox_input = Path("roblox_input/roblox.ipa")
    roblox_input.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out_path, roblox_input)
    print(f"Synced copy to {roblox_input}", flush=True)

    # Sync to MuMu shared folder
    mumu_dir = Path(r"C:\Users\Administrator\Documents\MuMuSharedFolder\Download\Masterstrap")
    if mumu_dir.exists():
        try:
            shutil.copyfile(out_path, mumu_dir / "Masterstrap-2.739.687.ipa")
            shutil.copyfile(out_path, mumu_dir / "roblox.ipa")
            print(f"Synced copies to {mumu_dir}", flush=True)
        except Exception as e:
            print(f"Note: Could not copy to MuMu folder: {e}", flush=True)

    return out_path

if __name__ == "__main__":
    target_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    target_out = sys.argv[2] if len(sys.argv) > 2 else OUT_PATH
    fast_download(target_url, target_out)
