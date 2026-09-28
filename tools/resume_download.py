import os
import sys
import time
import zipfile
import urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_ci import get_token

ipa_path = r"C:\Users\Administrator\Downloads\Masterstrap-2.739.687.ipa"
current_size = os.path.getsize(ipa_path) if os.path.exists(ipa_path) else 0
print(f"Current file size on disk: {current_size:,} bytes ({current_size/(1024*1024):.2f} MB)")

token = get_token()
asset_id = "595457125"
url = f"https://api.github.com/repos/baokhangchi6-wq/baokhangchi6-wq/releases/assets/{asset_id}"
req = urllib.request.Request(url, headers={
    "Authorization": f"Bearer {token}",
    "Accept": "application/octet-stream",
    "User-Agent": "Masterstrap-IOS-Builder"
})

proxy_handler = urllib.request.ProxyHandler({
    "https": "socks5h://127.0.0.1:40000",
    "http": "socks5h://127.0.0.1:40000"
})
opener = urllib.request.build_opener(proxy_handler)

print("Resolving S3 redirect URL via Cloudflare WARP proxy...")
with opener.open(req) as resp:
    s3_url = resp.geturl()

print(f"Connecting to S3 with Range: bytes={current_size}-...")
req2 = urllib.request.Request(s3_url, headers={"Range": f"bytes={current_size}-"})
t0 = time.time()

with opener.open(req2) as resp:
    content_range = resp.headers.get("Content-Range", "0/140719642")
    total_expected = int(content_range.split("/")[-1])
    remaining = total_expected - current_size
    print(f"Resuming remaining {remaining:,} bytes ({remaining/(1024*1024):.2f} MB)...")

    with open(ipa_path, "ab" if current_size > 0 else "wb") as f:
        downloaded = 0
        last_print = time.time()
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last_print >= 2.0:
                last_print = now
                pct = (current_size + downloaded) / total_expected * 100
                speed = downloaded / (1024 * 1024 * max(0.001, now - t0))
                print(f"Progress: {pct:.1f}% ({downloaded/(1024*1024):.1f}/{remaining/(1024*1024):.1f} MB @ {speed:.2f} MB/s)", flush=True)

final_size = os.path.getsize(ipa_path)
print(f"\nFinal file size on disk: {final_size:,} bytes ({final_size/(1024*1024):.2f} MB)")
is_zip = zipfile.is_zipfile(ipa_path)
print(f"Zip integrity valid: {is_zip}")

if is_zip and final_size == total_expected:
    print(f"\n==========================================")
    print(f"[SUCCESS] Build 112 IPA is 100% complete and verified!")
    print(f"Destination: {ipa_path}")
    print(f"==========================================")
else:
    print(f"[ERROR] Downloaded file size mismatch or corrupt zip!")
    sys.exit(1)
