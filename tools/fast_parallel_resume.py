#!/usr/bin/env python3
import os
import sys
import time
import zipfile
import plistlib
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_ci import get_token, api_request

TARGET_FILE = r"C:\Users\Administrator\Downloads\Masterstrap-2.739.687.ipa"
NUM_CHUNKS = 8

def main():
    print("Finding latest release on GitHub...")
    releases = api_request("releases?per_page=5")
    if not releases:
        print("[ERROR] No releases found!")
        sys.exit(1)

    latest_rel = releases[0]
    print(f"Latest Release: {latest_rel.get('name')} (tag: {latest_rel.get('tag_name')})")

    ipa_asset = None
    for a in latest_rel.get("assets", []):
        if a.get("name", "").endswith(".ipa"):
            ipa_asset = a
            break

    if not ipa_asset:
        print("[ERROR] No IPA asset found in latest release!")
        sys.exit(1)

    asset_id = str(ipa_asset.get("id"))
    total_size = ipa_asset.get("size")
    asset_name = ipa_asset.get("name")
    print(f"Asset: {asset_name} (ID: {asset_id}, Size: {total_size:,} bytes)")

    if not os.path.exists(TARGET_FILE):
        current_size = 0
    else:
        current_size = os.path.getsize(TARGET_FILE)

    print(f"Target: {TARGET_FILE}")
    print(f"Current size on disk: {current_size:,} / {total_size:,} bytes ({current_size/total_size*100:.1f}%)")

    if current_size == total_size and zipfile.is_zipfile(TARGET_FILE):
        print("Checking existing file integrity...")
        verify_ipa(TARGET_FILE)
        return

    if current_size > total_size or (current_size > 0 and current_size != total_size and not os.path.exists(TARGET_FILE + ".part")):
        # If existing file doesn't match total_size, start fresh
        try:
            os.remove(TARGET_FILE)
        except Exception:
            pass
        current_size = 0

    token = get_token()
    url = f"https://api.github.com/repos/baokhangchi6-wq/baokhangchi6-wq/releases/assets/{asset_id}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/octet-stream",
        "User-Agent": "Masterstrap-IOS-Builder"
    })

    print("Resolving S3 redirect CDN URL...")
    with urllib.request.urlopen(req, timeout=15) as resp:
        s3_url = resp.geturl()
    print("Resolved S3 CDN URL successfully!")

    # Calculate chunks for remaining bytes
    remaining_bytes = total_size - current_size
    chunk_size = remaining_bytes // NUM_CHUNKS
    ranges = []
    start = current_size
    for i in range(NUM_CHUNKS):
        if i == NUM_CHUNKS - 1:
            end = total_size - 1
        else:
            end = start + chunk_size - 1
        ranges.append((i, start, end))
        start = end + 1

    temp_dir = os.path.join(os.path.dirname(TARGET_FILE), "_tmp_chunks_115")
    os.makedirs(temp_dir, exist_ok=True)

    print(f"Starting {NUM_CHUNKS} parallel download workers over Cloudflare WARP proxy...")
    t0 = time.time()

    def download_range(idx, r_start, r_end):
        part_file = os.path.join(temp_dir, f"part_{idx:02d}.bin")
        expected_len = r_end - r_start + 1

        if os.path.exists(part_file) and os.path.getsize(part_file) == expected_len:
            print(f"  [Chunk {idx+1}/{NUM_CHUNKS}] Already completed ({expected_len:,} bytes)", flush=True)
            return idx, part_file, True

        for attempt in range(1, 5):
            cmd = [
                "curl.exe", "-s", "-f", "-L",
                "-r", f"{r_start}-{r_end}",
                s3_url,
                "-o", part_file
            ]
            res = subprocess.run(cmd)
            if res.returncode == 0 and os.path.exists(part_file) and os.path.getsize(part_file) == expected_len:
                print(f"  [Chunk {idx+1}/{NUM_CHUNKS}] Done ({expected_len:,} bytes, range {r_start}-{r_end})", flush=True)
                return idx, part_file, True
            else:
                print(f"  [Chunk {idx+1}/{NUM_CHUNKS}] Attempt {attempt} failed, retrying...", flush=True)
                time.sleep(1)
        return idx, part_file, False

    results = []
    with ThreadPoolExecutor(max_workers=NUM_CHUNKS) as pool:
        futures = {pool.submit(download_range, idx, s, e): idx for (idx, s, e) in ranges}
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            if not res[2]:
                print(f"[ERROR] Failed to download chunk {res[0]}", flush=True)
                sys.exit(1)

    t1 = time.time()
    elapsed = max(0.01, t1 - t0)
    print(f"\nAll {NUM_CHUNKS} chunks downloaded in {elapsed:.2f}s ({remaining_bytes/(1024*1024)/elapsed:.2f} MB/s)!", flush=True)

    # Assemble chunks
    print("Appending chunks to target IPA...")
    with open(TARGET_FILE, "ab" if current_size > 0 else "wb") as out_f:
        for idx in range(NUM_CHUNKS):
            part_file = os.path.join(temp_dir, f"part_{idx:02d}.bin")
            with open(part_file, "rb") as in_f:
                while True:
                    buf = in_f.read(1024 * 1024)
                    if not buf:
                        break
                    out_f.write(buf)
            try:
                os.remove(part_file)
            except Exception:
                pass

    try:
        os.rmdir(temp_dir)
    except Exception:
        pass

    final_size = os.path.getsize(TARGET_FILE)
    print(f"Final file size: {final_size:,} bytes")
    if final_size != total_size:
        print(f"[ERROR] Final size mismatch: {final_size} != {total_size}")
        sys.exit(1)

    verify_ipa(TARGET_FILE)

def verify_ipa(ipa_path):
    if not zipfile.is_zipfile(ipa_path):
        print("[ERROR] File is not a valid zip archive!")
        sys.exit(1)

    with zipfile.ZipFile(ipa_path, "r") as z:
        test = z.testzip()
        if test is not None:
            print(f"[ERROR] Corrupt file inside IPA: {test}")
            sys.exit(1)

        info_data = z.read("Payload/Roblox.app/Info.plist")
        pl = plistlib.loads(info_data)
        app_name = pl.get("CFBundleDisplayName") or pl.get("CFBundleName")
        bundle_id = pl.get("CFBundleIdentifier")
        version = pl.get("CFBundleShortVersionString")

        # Verify libmasterstrap.dylib is present
        has_dylib = "Payload/Roblox.app/Frameworks/libmasterstrap.dylib" in z.namelist()

        print("\n==========================================")
        print("[SUCCESS] Build 113 IPA is 100% complete and verified!")
        print(f"  App Name:       {app_name}")
        print(f"  Bundle ID:      {bundle_id}")
        print(f"  Version:        {version}")
        print(f"  File Path:      {ipa_path}")
        print(f"  File Size:      {os.path.getsize(ipa_path):,} bytes")
        print(f"  Dylib Injected: {has_dylib}")
        print("==========================================")

if __name__ == "__main__":
    main()
