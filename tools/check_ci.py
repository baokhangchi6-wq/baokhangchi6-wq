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
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proxy_args = []
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        if s.connect_ex(("127.0.0.1", 40000)) == 0:
            proxy_args = ["-x", "socks5h://127.0.0.1:40000"]
            print("Detected active Cloudflare WARP proxy on port 40000! Accelerating download...", flush=True)
        s.close()
    except Exception:
        pass

    print(f"Downloading from {asset_url} to {out_path} via curl.exe...", flush=True)
    cmd = [
        "curl.exe",
        "-f",
        "-L",
        "-C", "-",
        "--tcp-nodelay",
        *proxy_args,
        "-H", f"Authorization: token {token}",
        "-H", "Accept: application/octet-stream",
        asset_url,
        "-o", str(out_path),
        "--progress-bar"
    ]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"Curl failed with return code {res.returncode}", flush=True)
        sys.exit(res.returncode)
    print("\nDownload complete!", flush=True)

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

    download_dir = Path(r"C:\Users\Administrator\Downloads")
    download_dir.mkdir(parents=True, exist_ok=True)
    out_file = download_dir / ipa_asset.get("name")
    if out_file.exists():
        print(f"Removing previous build at {out_file} before downloading new build...")
        out_file.unlink()
    download_asset(ipa_asset.get("url"), out_file)

    print(f"\n==========================================")
    print(f"[SUCCESS] Downloaded IPA exclusively to single path: {out_file.resolve()}")
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
