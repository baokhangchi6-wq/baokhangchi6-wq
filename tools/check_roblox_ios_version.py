#!/usr/bin/env python3
"""
Masterstrap iOS – Roblox App Store Version Checker
===================================================
Queries the iTunes Lookup API for Roblox (id=431946152) and compares against
the last successfully built version recorded in `last_built_roblox_ios_version.json`.

Threshold policy: threshold = 0  (IMMEDIATE -- build as soon as any new version appears,
no minimum version-distance or age-days gate like the Android global-arm64 lane).

Exit codes / GitHub Actions outputs:
  should_build=true   --> New version detected, proceed with build pipeline.
  should_build=false  --> Already built or no new version available.
  store_version       --> Current App Store version string (e.g. "2.740.1234").
  last_version        --> Last built version string (empty if never built).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Fix Windows cp1252 encoding when running locally (CI uses utf-8 by default)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup?id=431946152&country=us"
GITHUB_OUTPUT_ENV = "GITHUB_OUTPUT"

# ──────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ──────────────────────────────────────────────────────────────────────────────

def version_tuple(v: str) -> tuple[int, ...]:
    """Convert '2.740.1234' → (2, 740, 1234) for numeric comparison."""
    import re
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts) if parts else (0,)


def fetch_appstore_version(timeout: int = 15) -> str:
    """Query iTunes Lookup API and return the current App Store version string."""
    req = urllib.request.Request(
        ITUNES_LOOKUP_URL,
        headers={"User-Agent": "Masterstrap-iOS-VersionChecker/2.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"iTunes Lookup API request failed: {exc}") from exc

    results = data.get("results", [])
    if not results:
        raise RuntimeError(
            f"iTunes Lookup returned 0 results for id=431946152. "
            f"Full response: {json.dumps(data)[:400]}"
        )
    version = results[0].get("version", "")
    if not version:
        raise RuntimeError("iTunes Lookup result has no 'version' field.")
    return version.strip()


def load_checkpoint(checkpoint_file: Path) -> dict:
    """Load the last-built checkpoint JSON, returning {} if missing."""
    if checkpoint_file.is_file():
        try:
            return json.loads(checkpoint_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[WARN] Checkpoint file is corrupt, treating as empty: {checkpoint_file}")
    return {}


def write_checkpoint(checkpoint_file: Path, version: str) -> None:
    """Persist the newly built version to the checkpoint JSON."""
    data = {
        "version": version,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "platform": "ios",
        "channel": "global-appstore",
    }
    checkpoint_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"[CHECKPOINT] Written -> {checkpoint_file}: {version}")



def emit_github_output(key: str, value: str) -> None:
    """Write key=value to $GITHUB_OUTPUT (multi-line safe) or stdout."""
    import os
    out_file = os.environ.get(GITHUB_OUTPUT_ENV, "")
    line = f"{key}={value}\n"
    if out_file:
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(line)
    # Always echo to stdout for local debugging
    print(f"[OUTPUT] {line.strip()}")


# ──────────────────────────────────────────────────────────────────────────────
# Main logic
# ──────────────────────────────────────────────────────────────────────────────

def check_version(
    checkpoint_file: Path,
    force: bool = False,
    advance: bool = False,
    expected_version: str = "",
) -> int:
    """
    Core check logic.

    Returns:
        0  – should_build=true
        1  – should_build=false (already built / no new version)
        2  – error
    """
    print("=" * 60)
    print("[CHECK] Masterstrap iOS – Roblox App Store Version Checker")
    print(f"[CHECK] threshold = 0 (IMMEDIATE – no version-distance or age gate)")
    print("=" * 60)

    # 1. Fetch live App Store version
    try:
        store_version = fetch_appstore_version()
        print(f"[CHECK] App Store (id=431946152) -> {store_version}")
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        emit_github_output("should_build", "false")
        emit_github_output("store_version", "unknown")
        emit_github_output("last_version", "")
        return 2

    emit_github_output("store_version", store_version)

    # 2. Load last-built checkpoint
    checkpoint = load_checkpoint(checkpoint_file)
    last_version = checkpoint.get("version", "")
    emit_github_output("last_version", last_version)
    print(f"[CHECK] Last built version  → '{last_version or '(never)' }'")

    # 3. Optional: validate caller-supplied expected version
    if expected_version and expected_version != store_version:
        print(
            f"[WARN] Caller requested version '{expected_version}' but "
            f"App Store has '{store_version}'. Proceeding with store version."
        )

    # 4. Force flag overrides all gates
    if force:
        print("[GATE] --force passed -> unconditional rebuild.")
        emit_github_output("should_build", "true")
        if advance:
            write_checkpoint(checkpoint_file, expected_version or store_version)
        return 0

    # 5. Threshold = 0: build whenever store_version > last_version
    if not last_version:
        print("[GATE] No prior build found -> first build, triggering.")
        emit_github_output("should_build", "true")
        if advance:
            write_checkpoint(checkpoint_file, expected_version or store_version)
        return 0

    if version_tuple(store_version) > version_tuple(last_version):
        print(
            f"[GATE] New version detected: {last_version} -> {store_version}. "
            f"Triggering build (threshold=0, IMMEDIATE)."
        )
        emit_github_output("should_build", "true")
        if advance:
            write_checkpoint(checkpoint_file, expected_version or store_version)
        return 0

    # Same or older version
    print(
        f"[GATE] No new version. store={store_version} last={last_version}. "
        "Skipping build."
    )
    emit_github_output("should_build", "false")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Masterstrap iOS – Roblox App Store version gate (threshold=0)."
    )
    ap.add_argument(
        "--checkpoint-file",
        default="last_built_roblox_ios_version.json",
        help="Path to the JSON file tracking the last successfully built version.",
    )
    ap.add_argument(
        "--expected-version",
        default="",
        help="Optional version string to confirm (informational; does not gate the build).",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Bypass all version-comparison gates and force a rebuild.",
    )
    ap.add_argument(
        "--advance-checkpoint",
        action="store_true",
        help="Write the new version to the checkpoint file when should_build=true.",
    )
    args = ap.parse_args()

    checkpoint_file = Path(args.checkpoint_file)
    return check_version(
        checkpoint_file=checkpoint_file,
        force=args.force,
        advance=args.advance_checkpoint,
        expected_version=args.expected_version,
    )


if __name__ == "__main__":
    sys.exit(main())
