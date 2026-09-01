#!/usr/bin/env python3
"""
Masterstrap iOS UI Live Preview for Windows
Renders and serves the complete Masterstrap iOS WebKit UI directly in a dedicated
standalone mobile window (Edge/Chrome App Mode) or in an interactive iPhone 15 Pro simulator.
"""

import argparse
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
PREVIEW_DIR = ROOT / "preview"
PREVIEW_DIR.mkdir(parents=True, exist_ok=True)


def extract_or_generate_html() -> str:
    """Extracts index.html directly from build_masterstrap_app_ipa or generate script."""
    builder_path = TOOLS_DIR / "build_masterstrap_app_ipa.py"

    if builder_path.is_file():
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("builder", str(builder_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            
            html_ui = mod.HTML_TEMPLATE_RAW
            html_ui = html_ui.replace("POPULAR_PRESETS_PLACEHOLDER", mod.POPULAR_PRESETS_JSON)
            html_ui = html_ui.replace("HOT_PRESETS_PLACEHOLDER", mod.HOT_PRESETS_JSON)
            html_ui = html_ui.replace("PRESET_IMAGES_PLACEHOLDER", mod.PRESET_IMAGES_JSON)
            html_ui = html_ui.replace("LOGO_PLACEHOLDER", mod.LOGO_BASE64)
            return html_ui
        except Exception as e:
            print(f"Notice: Loading template directly due to: {e}")

    raise RuntimeError("Could not find Masterstrap iOS HTML template in build_masterstrap_app_ipa.py.")


def create_simulator_html(raw_app_html: str) -> str:
    out_file = PREVIEW_DIR / "index.html"
    out_file.write_text(raw_app_html, encoding="utf-8", errors="surrogatepass")
    return str(out_file)


def find_browser_executable():
    """Finds Edge or Chrome on Windows for App Mode."""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


class QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def start_preview_server(port=8899):
    html_content = extract_or_generate_html()
    create_simulator_html(html_content)

    os.chdir(str(PREVIEW_DIR))
    
    server = None
    actual_port = port
    for p in range(port, port + 20):
        try:
            server = socketserver.TCPServer(("127.0.0.1", p), QuietHTTPHandler)
            actual_port = p
            break
        except OSError:
            continue

    if not server:
        print("Could not bind port for preview server.")
        return

    target_url = f"http://127.0.0.1:{actual_port}/index.html"
    print("\n========================================================")
    print("  MASTERSTRAP iOS LIVE PREVIEW (WINDOWS)")
    print("========================================================")
    print(f" Local URL : {target_url}")
    print(f" File Path : {PREVIEW_DIR / 'index.html'}")
    print(" Display   : Standalone iPhone App Mode (430 x 900)")
    print("========================================================\n")

    browser_bin = find_browser_executable()
    if browser_bin:
        cmd = [
            browser_bin,
            f"--app={target_url}",
            "--window-size=430,900",
            "--window-position=500,60",
            "--user-data-dir=" + str(PREVIEW_DIR / ".browser_profile")
        ]
        print(f"Opening Standalone iOS App Window using: {browser_bin}...")
        subprocess.Popen(cmd)
    else:
        print("Opening default browser...")
        webbrowser.open(target_url)

    print("Preview server running... (Press Ctrl+C in terminal to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping preview server.")
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Masterstrap iOS Live Preview on Windows")
    parser.add_argument("--port", type=int, default=8899, help="Local preview port (default: 8899)")
    parser.add_argument("--generate-only", action="store_true", help="Only generate preview HTML file")
    args = parser.parse_args()

    if args.generate_only:
        html_content = extract_or_generate_html()
        out_path = create_simulator_html(html_content)
        print(f"Generated standalone preview at: {out_path}")
        return 0

    start_preview_server(args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
