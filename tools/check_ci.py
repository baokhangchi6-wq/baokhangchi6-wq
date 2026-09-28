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

def watch_latest_run_and_download():
    # Find latest Build Global IPA run
    runs_data = api_request("actions/runs?per_page=10")
    runs = runs_data.get("workflow_runs", [])
    if not runs:
        print("No runs found!")
        return False

    target_run = None
    for r in runs:
        if "Build Global IPA" in r.get("name", ""):
            target_run = r
            break

    if not target_run:
        target_run = runs[0]

    run_id = target_run.get("id")
    run_number = target_run.get("run_number")
    sha = target_run.get("head_sha", "")[:7]
    print(f"Tracking GitHub Actions Run #{run_number} ({target_run.get('name')}, ID: {run_id}, Commit: {sha})...")

    while True:
        try:
            run_data = api_request(f"actions/runs/{run_id}")
            status = run_data.get("status")
            conclusion = run_data.get("conclusion")
            print(f"[{time.strftime('%H:%M:%S')}] Run #{run_number} status: {status} | conclusion: {conclusion}")
            if status == "completed":
                if conclusion != "success":
                    print(f"Workflow finished with failure conclusion: {conclusion}")
                    return False
                print("Workflow completed successfully!")
                break
        except Exception as e:
            print(f"Error checking run: {e}")
        time.sleep(15)

    print("\nUser requested: waiting 1 minute (60 seconds) after GitHub Actions completion...")
    for remaining in range(60, 0, -10):
        print(f"Waiting {remaining}s...")
        time.sleep(10)
    print("Wait finished! Finding release asset...")

    target_tag = f"run-{run_number}"
    matching_release = None
    for attempt in range(5):
        releases = api_request("releases?per_page=10")
        for rel in releases:
            if target_tag in rel.get("tag_name", ""):
                matching_release = rel
                break
        if matching_release:
            break
        print(f"Waiting for release tag containing {target_tag}... (attempt {attempt+1}/5)")
        time.sleep(10)

    if not matching_release:
        releases = api_request("releases?per_page=5")
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

    # Copy directly into Download directory with NO extra subdirectories
    import shutil
    ipa_filename = ipa_asset.get("name")
    for dl_path in [
        Path(r"C:\Users\Administrator\Documents\MuMuSharedFolder\Download"),
        Path(r"C:\Users\Administrator\Downloads")
    ]:
        if dl_path.is_dir():
            target_dest = dl_path / ipa_filename
            shutil.copy2(out_file, target_dest)
            print(f"Copied to Download directory: {target_dest}")

    print(f"\n==========================================")
    print(f"[SUCCESS] Downloaded IPA to: {out_file.resolve()}")
    print(f"==========================================")
    return True

def list_runs():
    runs_data = api_request("actions/runs?per_page=5")
    for r in runs_data.get("workflow_runs", []):
        print(f"Run #{r.get('run_number')} ({r.get('id')}): {r.get('name')} | status={r.get('status')} | conclusion={r.get('conclusion')} | sha={r.get('head_sha')[:7]}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        watch_latest_run_and_download()
    else:
        list_runs()
