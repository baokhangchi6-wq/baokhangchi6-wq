#!/usr/bin/env python3
"""
Fast multi-part HTTP range downloader for Roblox iOS IPA
"""
import concurrent.futures
import os
import sys
import time
import urllib.request
from pathlib import Path

URL = "https://deltaexecutor.filesadda.com/Delta-2.739.687-2.ipa"
OUT_PATH = Path("roblox_input/roblox.ipa")
WORKERS = 32
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB chunks
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

def get_content_length(url: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return int(resp.headers.get("Content-Length", 0))

def download_range(url: str, start: int, end: int, part_path: Path) -> bool:
    headers = {
        "User-Agent": USER_AGENT,
        "Range": f"bytes={start}-{end}"
    }
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                expected_len = end - start + 1
                if len(data) == expected_len:
                    part_path.write_bytes(data)
                    return True
        except Exception as e:
            time.sleep(1 + attempt)
    return False

def fast_download(url: str = URL, out_path: Path = OUT_PATH, workers: int = WORKERS):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = out_path.parent / ".chunks"
    temp_dir.mkdir(parents=True, exist_ok=True)

    print(f"Resolving file size for: {url} ...", flush=True)
    total_size = get_content_length(url)
    total_mb = total_size / (1024 * 1024)
    print(f"Total size: {total_mb:.2f} MB ({total_size} bytes)", flush=True)

    chunks = []
    start = 0
    idx = 0
    while start < total_size:
        end = min(start + CHUNK_SIZE - 1, total_size - 1)
        part_file = temp_dir / f"part_{idx:05d}.bin"
        chunks.append((idx, start, end, part_file))
        start = end + 1
        idx += 1

    print(f"Splitting into {len(chunks)} chunks with {workers} parallel threads...", flush=True)

    start_time = time.time()
    completed = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {
            executor.submit(download_range, url, c[1], c[2], c[3]): c for c in chunks
        }
        for future in concurrent.futures.as_completed(future_map):
            chunk = future_map[future]
            ok = future.result()
            if not ok:
                raise RuntimeError(f"Failed to download chunk {chunk[0]} ({chunk[1]}-{chunk[2]})")
            completed += 1
            now = time.time()
            elapsed = max(now - start_time, 0.1)
            downloaded_bytes = min(completed * CHUNK_SIZE, total_size)
            mb = downloaded_bytes / (1024 * 1024)
            speed = mb / elapsed
            pct = (completed / len(chunks)) * 100
            print(f"\rProgress: {completed}/{len(chunks)} ({pct:.1f}%) | {mb:.1f}/{total_mb:.1f} MB @ {speed:.2f} MB/s", end="", flush=True)

    print("\nMerging chunks into final IPA...", flush=True)
    with open(out_path, "wb") as outfile:
        for idx, start, end, part_file in chunks:
            data = part_file.read_bytes()
            outfile.write(data)
            part_file.unlink()

    temp_dir.rmdir()
    elapsed = max(time.time() - start_time, 0.1)
    print(f"SUCCESS -> Downloaded {out_path} ({out_path.stat().st_size / (1024*1024):.2f} MB) in {elapsed:.1f}s ({total_mb / elapsed:.2f} MB/s)!", flush=True)
    return out_path

if __name__ == "__main__":
    fast_download()
