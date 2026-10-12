#!/usr/bin/env python3
"""
Masterstrap iOS – Fetch Roblox iOS Decrypted IPA
=================================================
Automated fetcher for Roblox iOS IPA (similar to fetch_latest_roblox.py on Android).

Resolution pipeline:
  1. Explicit CLI argument (--url)
  2. Environment variable INPUT_IPA_URL
  3. Environment variable SECRET_IPA_URL (from GitHub Secrets)
  4. Auto-scrape direct download URL from Delta iOS distributor:
     https://bstshrt.com/u/delta-ios (extracts SSR finalUrl)
  5. Fallback mirrors
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

# Force UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRAPE_SOURCE_URL = "https://bstshrt.com/u/delta-ios"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
GITHUB_OUTPUT_ENV = "GITHUB_OUTPUT"


def log(msg: str) -> None:
    print(f"[FETCH_IPA] {msg}", flush=True)


def emit_github_output(key: str, value: str) -> None:
    out_file = os.environ.get(GITHUB_OUTPUT_ENV, "")
    line = f"{key}={value}\n"
    if out_file:
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(line)
    print(f"[OUTPUT] {line.strip()}", flush=True)


def scrape_delta_direct_url(timeout: int = 30, max_retries: int = 3, expected_version: str = "") -> str:
    """Scrape the direct IPA download URL with retry logic and fallback mirrors."""
    sources = [
        SCRAPE_SOURCE_URL,
        "https://delta-executor.com/ios",
    ]
    for attempt in range(1, max_retries + 1):
        for src in sources:
            try:
                log(f"Querying distributor page (attempt {attempt}/{max_retries}): {src}")
                req = urllib.request.Request(src, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")
                match = re.search(r'finalUrl[\\"]+:\s*[\\"]+(https?://[^\\"]+\.ipa)', html)
                if not match:
                    match = re.search(r'(https?://deltaexecutor\.filesadda\.com/[^\s"\'<>]+\.ipa)', html)
                if match:
                    url = match.group(1)
                    log(f"Extracted direct IPA URL: {url}")
                    return url
            except Exception as exc:
                log(f"Distributor request failed for {src}: {exc}")
        if attempt < max_retries:
            time.sleep(3)

    if re.fullmatch(r'\d+(\.\d+)+',expected_version):
        return f"https://deltaexecutor.filesadda.com/Delta-{expected_version}.ipa"
    log("All distributor scrape attempts failed; no current IPA source available")
    return ""


def download_file(url: str, dest_path: Path, max_retries: int = 3) -> bool:
    """Download file with progress report and retry logic."""
    log(f"Starting download: {url}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                total_bytes = int(resp.headers.get("Content-Length", 0))
                total_mb = total_bytes / (1024 * 1024) if total_bytes else 0
                log(f"Content length: {total_mb:.1f} MB (attempt {attempt}/{max_retries})")

                downloaded = 0
                start_time = time.time()
                last_report = start_time

                with open(dest_path, "wb") as f:
                    while True:
                        chunk = resp.read(1024 * 1024)  # 1MB chunks
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        if now - last_report >= 5.0 or downloaded == total_bytes:
                            pct = (downloaded / total_bytes * 100) if total_bytes else 0
                            speed = (downloaded / (1024 * 1024)) / max(now - start_time, 0.1)
                            log(f"Downloaded {downloaded / (1024 * 1024):.1f}/{total_mb:.1f} MB ({pct:.1f}%) @ {speed:.2f} MB/s")
                            last_report = now

            log(f"Download complete: {dest_path.name} ({dest_path.stat().st_size / (1024 * 1024):.1f} MB)")
            return True
        except Exception as exc:
            log(f"Attempt {attempt} failed: {exc}")
            if dest_path.is_file():
                dest_path.unlink()
            if attempt < max_retries:
                time.sleep(5)

    return False


def validate_ipa_archive(ipa_path: Path) -> bool:
    """Validate that the file is a valid non-empty ZIP archive containing Payload/."""
    if not ipa_path.is_file() or ipa_path.stat().st_size < 1024 * 1024:
        return False
    try:
        with zipfile.ZipFile(ipa_path, "r") as z:
            names = z.namelist()[:30]
            if any("Payload/" in n for n in names):
                return True
    except Exception:
        pass
    return False


def validate_ipa_version(ipa_path: Path, expected: str) -> str:
    with zipfile.ZipFile(ipa_path) as archive:
        paths=[name for name in archive.namelist() if re.fullmatch(r'Payload/[^/]+\.app/Info\.plist',name)]
        if len(paths)!=1: raise ValueError('IPA needs exactly one main Info.plist')
        version=plistlib.loads(archive.read(paths[0])).get('CFBundleShortVersionString')
    if not isinstance(version,str) or not re.fullmatch(r'\d+(\.\d+)+',version):
        raise ValueError('IPA version missing or invalid')
    if expected and version!=expected:
        raise ValueError(f'IPA version {version} differs from requested {expected}; waiting for matching source')
    return version


def main() -> int:
    ap = argparse.ArgumentParser(description="Masterstrap iOS – Fetch Roblox iOS Decrypted IPA")
    ap.add_argument("--url", default="", help="Explicit direct HTTPS URL to decrypted IPA")
    ap.add_argument("--output", default="roblox_input/roblox.ipa", help="Destination path")
    ap.add_argument("--expected-version", default="", help="Reject an IPA for a different App Store version")
    args = ap.parse_args()

    out_path = Path(args.output)
    target_url = (
        args.url
        or os.environ.get("INPUT_IPA_URL", "").strip()
        or os.environ.get("SECRET_IPA_URL", "").strip()
    )

    if not target_url:
        log("No explicit URL provided. Checking automated distributor scraper...")
        target_url = scrape_delta_direct_url(expected_version=args.expected_version)

    if not target_url:
        log("ERROR: Could not resolve a valid direct IPA URL.")
        emit_github_output("available", "false")
        emit_github_output("ipa_path", "")
        return 1

    success = download_file(target_url, out_path)
    if not success or not validate_ipa_archive(out_path):
        log(f"ERROR: Downloaded file is invalid or corrupt: {out_path}")
        if out_path.is_file():
            out_path.unlink()
        emit_github_output("available", "false")
        emit_github_output("ipa_path", "")
        return 1

    try:
        version=validate_ipa_version(out_path,args.expected_version)
    except (ValueError,zipfile.BadZipFile,plistlib.InvalidFileException) as exc:
        log(f"ERROR: {exc}"); emit_github_output("available","false"); return 1
    log(f"SUCCESS: Roblox IPA {version} confirmed at {out_path}; encryption checked by dumper")
    emit_github_output("available", "true")
    emit_github_output("ipa_path", str(out_path).replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
