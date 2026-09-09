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
    """Extracts index.html directly from build_masterstrap_app_ipa with iOS runtime configuration."""
    builder_path = TOOLS_DIR / "build_masterstrap_app_ipa.py"

    if builder_path.is_file():
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("builder", str(builder_path))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            
            html_ui = mod.configure_ios_runtime_html(mod.HTML_TEMPLATE_RAW)
            html_ui = html_ui.replace("POPULAR_PRESETS_PLACEHOLDER", mod.POPULAR_PRESETS_JSON)
            html_ui = html_ui.replace("HOT_PRESETS_PLACEHOLDER", mod.HOT_PRESETS_JSON)
            html_ui = html_ui.replace("PRESET_IMAGES_PLACEHOLDER", mod.PRESET_IMAGES_JSON)
            html_ui = html_ui.replace("LOGO_PLACEHOLDER", mod.LOGO_BASE64)
            return html_ui
        except Exception as e:
            print(f"Notice: Loading template directly due to: {e}")

    raise RuntimeError("Could not find Masterstrap iOS HTML template in build_masterstrap_app_ipa.py.")


def inject_simulator_bridge(html_content: str) -> str:
    """Injects WebKit simulator mock bridge into preview HTML for full Windows browser interactivity."""
    if "Masterstrap Windows Live Preview WebKit Simulator Bridge" in html_content:
        return html_content

    bridge_script = """
<!-- Masterstrap Windows Live Preview WebKit Simulator Bridge -->
<script>
(function() {
    if (typeof window === "undefined") return;
    if (!window.webkit) {
        window.webkit = {
            messageHandlers: {
                log: {
                    postMessage: function(msg) {
                        console.log("%c[iOS Native Log]", "color: #00A5FF;", msg);
                    }
                },
                setLevelPlayBannerVisible: {
                    postMessage: function(visible) {
                        console.log("%c[iOS Banner]", "color: #38BDF8;", visible);
                        var isVis = (visible === true || visible === "true");
                        var existing = document.getElementById("simulator-levelplay-banner");
                        if (isVis) {
                            if (!existing) {
                                var b = document.createElement("div");
                                b.id = "simulator-levelplay-banner";
                                b.style.cssText = "position:fixed;bottom:0;left:0;right:0;height:44px;background:linear-gradient(90deg,rgba(15,23,42,0.95),rgba(30,41,59,0.95));color:#38BDF8;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;z-index:99999;border-top:1px solid rgba(56,189,248,0.4);backdrop-filter:blur(10px);box-shadow:0 -4px 14px rgba(0,0,0,0.5);letter-spacing:0.3px;";
                                b.innerHTML = "📢 [iOS Simulator] LevelPlay Banner Ad Active (Settings)";
                                document.body.appendChild(b);
                            }
                        } else {
                            if (existing) existing.remove();
                        }
                    }
                },
                requestLevelPlayAd: {
                    postMessage: function(payload) {
                        console.log("%c[iOS Ad Request]", "color: #F59E0B;", payload);
                        var format = payload ? payload.format : "";
                        var reqId = payload ? payload.requestId : "";
                        var isRewarded = (format === "rewarded");

                        if (typeof showToast === "function") {
                            showToast(isRewarded ? "🎬 [Simulator] Rewarded Ad in progress..." : "📺 [Simulator] Interstitial Ad shown");
                        }

                        setTimeout(function() {
                            if (window.MasterstrapAds && typeof window.MasterstrapAds.nativeResult === "function") {
                                window.MasterstrapAds.nativeResult({
                                    requestId: reqId,
                                    outcome: isRewarded ? "rewarded" : "continue",
                                    detail: isRewarded ? "rewarded" : "closed"
                                });
                            }
                        }, isRewarded ? 800 : 250);
                    }
                },
                launchRoblox: {
                    postMessage: function(payload) {
                        console.log("%c[iOS Roblox Launched]", "color: #10B981; font-weight: bold;", payload);
                        if (typeof showToast === "function") {
                            showToast("🚀 [Simulator] Roblox Launched Successfully!");
                        }
                    }
                },
                saveConfig: {
                    postMessage: function(payload) {
                        console.log("%c[iOS Native Config Saved]", "color: #10B981;", payload);
                    }
                },
                openURL: {
                    postMessage: function(url) {
                        console.log("%c[iOS Open URL]", "color: #A855F7;", url);
                        try {
                            window.open(url, "_blank");
                        } catch (e) {
                            location.href = url;
                        }
                    }
                }
            }
        };
        console.log("%c[Masterstrap Live Preview] WebKit iOS Simulator Bridge Initialized", "color: #00A5FF; font-weight: bold; font-size: 12px;");
    }
})();
</script>
"""
    if "</head>" in html_content:
        return html_content.replace("</head>", bridge_script + "\n</head>", 1)
    return bridge_script + html_content


def create_simulator_html(raw_app_html: str) -> str:
    out_file = PREVIEW_DIR / "index.html"
    preview_html = inject_simulator_bridge(raw_app_html)
    out_file.write_text(preview_html, encoding="utf-8", errors="surrogatepass")
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


def start_preview_server(port=8899, force_refresh=True):
    out_file = PREVIEW_DIR / "index.html"
    if force_refresh or not out_file.is_file():
        print("Synchronizing latest Masterstrap iOS UI to preview/index.html...")
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
    parser.add_argument("--no-refresh", action="store_true", help="Do not refresh preview HTML if it already exists")
    args = parser.parse_args()

    if args.generate_only:
        html_content = extract_or_generate_html()
        out_path = create_simulator_html(html_content)
        print(f"Generated standalone preview at: {out_path}")
        return 0

    start_preview_server(args.port, force_refresh=not args.no_refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
