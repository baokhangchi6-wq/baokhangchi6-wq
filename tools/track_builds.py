import urllib.request
import json
import time
import subprocess
import sys
import zipfile
import shutil
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
        print("Git credential error:", e, flush=True)
    return None

def api_request(repo, endpoint, token):
    url = f"https://api.github.com/repos/{repo}/{endpoint}"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.github+json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    repo = "baokhangchi6-wq/baokhangchi6-wq"
    token = get_token()
    commit_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    short_sha = commit_sha[:7]
    print(f"Tracking builds for commit {short_sha} on {repo}...", flush=True)

    # Detect both workflows for this commit
    global_run = None
    ms_run = None
    for attempt in range(25):
        data = api_request(repo, f"actions/runs?head_sha={commit_sha}", token)
        for r in data.get("workflow_runs", []):
            name = r.get("name", "")
            if "Build Global IPA" in name and not global_run:
                global_run = r
            elif "Fast Build Masterstrap IPA" in name and not ms_run:
                ms_run = r
        if global_run and ms_run:
            break
        print(f"Waiting for CI runs to trigger... (attempt {attempt+1}/25 | global={bool(global_run)} ms={bool(ms_run)})", flush=True)
        time.sleep(4)

    tracked_runs = {}
    if global_run:
        tracked_runs["Global Roblox"] = global_run
    if ms_run:
        tracked_runs["Masterstrap App"] = ms_run

    if not tracked_runs:
        print("No CI workflow runs found for commit!", flush=True)
        return False

    completed_runs = {}
    while len(completed_runs) < len(tracked_runs):
        for label, run_info in tracked_runs.items():
            if label in completed_runs:
                continue
            run_id = run_info["id"]
            run_num = run_info.get("run_number")
            try:
                rdata = api_request(repo, f"actions/runs/{run_id}", token)
                status = rdata.get("status")
                conclusion = rdata.get("conclusion")
                print(f"[{time.strftime('%H:%M:%S')}] {label} #{run_num} -> {status} | conclusion={conclusion}", flush=True)
                if status == "completed":
                    if conclusion != "success":
                        print(f"Workflow {label} finished with failure: {conclusion}", flush=True)
                        return False
                    print(f"SUCCESS: {label} completed successfully!", flush=True)
                    completed_runs[label] = run_id
            except Exception as e:
                print(f"Error checking {label}: {e}", flush=True)
        time.sleep(12)

    downloads = Path(r"C:\Users\Administrator\Downloads")
    downloads.mkdir(parents=True, exist_ok=True)

    # RULE: Delete all old IPA files first
    print("\nDeleting old IPA files in Downloads folder...", flush=True)
    for old_ipa in downloads.glob("*.ipa"):
        try:
            old_ipa.unlink()
            print(f"Deleted old IPA: {old_ipa.name}", flush=True)
        except Exception as e:
            print(f"Error deleting {old_ipa.name}: {e}", flush=True)

    # Download artifacts from both runs
    for label, run_id in completed_runs.items():
        print(f"\nFetching artifacts for {label} run {run_id}...", flush=True)
        art_data = api_request(repo, f"actions/runs/{run_id}/artifacts", token)
        for art in art_data.get("artifacts", []):
            art_id = art["id"]
            art_name = art["name"]
            temp_zip = downloads / f"temp_{art_name}_{run_id}.zip"
            zip_url = f"https://api.github.com/repos/{repo}/actions/artifacts/{art_id}/zip"
            print(f"Downloading artifact {art_name} (ID: {art_id})...", flush=True)

            cmd = [
                "curl.exe", "-f", "-L",
                "-H", f"Authorization: token {token}",
                zip_url, "-o", str(temp_zip),
                "--progress-bar"
            ]
            res = subprocess.run(cmd)
            if res.returncode == 0 and temp_zip.exists():
                with zipfile.ZipFile(temp_zip) as z:
                    for info in z.infolist():
                        if info.filename.endswith(".ipa"):
                            target_filename = Path(info.filename).name
                            target_path = downloads / target_filename
                            if target_path.exists():
                                target_path.unlink()
                            with z.open(info) as src, open(target_path, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            print(f"[EXTRACTED] Saved {label} IPA: {target_path} ({target_path.stat().st_size:,} bytes)", flush=True)
                try:
                    temp_zip.unlink()
                except Exception:
                    pass

    print("\n==========================================", flush=True)
    print("FINISHED! Final IPAs ready in Downloads folder:", flush=True)
    for ipa in sorted(downloads.glob("*.ipa")):
        print(f" -> {ipa.name} ({ipa.stat().st_size:,} bytes | modified: {time.ctime(ipa.stat().st_mtime)})", flush=True)
    print("==========================================", flush=True)
    return True

if __name__ == "__main__":
    main()
