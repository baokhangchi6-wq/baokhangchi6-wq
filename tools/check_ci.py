import urllib.request
import json
import time
import subprocess
import sys
from pathlib import Path

def get_token():
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n",
            capture_output=True,
            text=True,
            check=True
        )
        for line in proc.stdout.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1]
    except Exception as e:
        print("Could not get token from git credential:", e)
    return None

def api_request(endpoint):
    token = get_token()
    url = f"https://api.github.com/repos/baokhangchi6-wq/baokhangchi6-wq/{endpoint}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.github+json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def download_asset(asset_url, out_path):
    token = get_token()
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/octet-stream"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(asset_url, headers=headers)
    print(f"Downloading from {asset_url} to {out_path}...")
    with urllib.request.urlopen(req) as resp, open(out_path, "wb") as f:
        total = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
            print(f"\rDownloaded {total / (1024*1024):.1f} MB", end="", flush=True)
    print("\nDownload complete!")

def watch_run_and_download(run_id=36326933294):
    print(f"Tracking GitHub Actions Run #{run_id}...")
    while True:
        try:
            run_data = api_request(f"actions/runs/{run_id}")
            status = run_data.get("status")
            conclusion = run_data.get("conclusion")
            print(f"[{time.strftime('%H:%M:%S')}] Run status: {status} | conclusion: {conclusion}")
            if status == "completed":
                if conclusion != "success":
                    print(f"Workflow finished with failure conclusion: {conclusion}")
                    return False
                print("Workflow completed successfully!")
                break
        except Exception as e:
            print(f"Error checking run: {e}")
        time.sleep(15)

    print("User instructed: waiting 1 minute (60 seconds) after GitHub Actions completion...")
    for remaining in range(60, 0, -10):
        print(f"Waiting {remaining}s...")
        time.sleep(10)
    print("Wait finished! Finding release asset...")

    # Look for release matching run 13
    target_tag = "run-13"
    releases = api_request("releases?per_page=10")
    matching_release = None
    for rel in releases:
        if target_tag in rel.get("tag_name", ""):
            matching_release = rel
            break

    if not matching_release:
        print("Release matching run-13 not found in top releases, checking latest release...")
        matching_release = releases[0] if releases else None

    if not matching_release:
        print("No release found!")
        return False

    print(f"Found release: {matching_release.get('name')} (tag: {matching_release.get('tag_name')})")
    assets = matching_release.get("assets", [])
    ipa_asset = None
    for a in assets:
        if a.get("name", "").endswith(".ipa"):
            ipa_asset = a
            break

    if not ipa_asset:
        print("No .ipa asset found in release!")
        return False

    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / ipa_asset.get("name")
    download_asset(ipa_asset.get("url"), out_file)
    print(f"\n[DONE] Saved IPA to: {out_file.resolve()}")
    return True

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        watch_run_and_download()
    else:
        run_data = api_request("actions/runs/36326933294")
        print(f"Run 13 status: {run_data.get('status')} | conclusion: {run_data.get('conclusion')}")
        jobs = api_request("actions/runs/36326933294/jobs")
        for j in jobs.get("jobs", []):
            print(f"Job: {j.get('name')} ({j.get('status')})")
            for s in j.get("steps", []):
                print(f"  - {s.get('name')}: {s.get('status')} ({s.get('conclusion')})")
