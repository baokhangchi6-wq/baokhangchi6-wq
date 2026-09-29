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
    favicon_tag = '<link rel="icon" type="image/png" href="masterstrap.png">\n'
    if '<link rel="icon"' not in html_content and "<head>" in html_content:
        html_content = html_content.replace("<head>", "<head>\n" + favicon_tag, 1)

    if "Masterstrap Windows Live Preview WebKit Simulator Bridge" in html_content:
        return html_content

    bridge_script = """
<!-- Masterstrap Windows Live Preview WebKit Simulator Bridge -->
<style id="simulator-styles">
/* =========================================================================
   iOS Device Frame & Status Bar Emulation (Pixel-perfect iPhone 16 Pro)
   ========================================================================= */
:root {
    --ios-safe-top: 50px;
    --ios-safe-bottom: 34px;
}

body.sim-phone-mode {
    padding-top: var(--ios-safe-top) !important;
    padding-bottom: calc(75px + var(--ios-safe-bottom)) !important;
}

/* iOS Status Bar */
#ios-status-bar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    height: var(--ios-safe-top);
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    padding: 12px 26px 0 26px;
    z-index: 10001;
    pointer-events: none;
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif;
    color: #FFFFFF;
    transition: all 0.25s ease;
}

[data-theme="light"] #ios-status-bar {
    color: #000000;
}

.ios-status-time {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.2px;
}

/* Dynamic Island */
.ios-dynamic-island {
    position: absolute;
    top: 10px;
    left: 50%;
    transform: translateX(-50%);
    width: 124px;
    height: 35px;
    background: #000000;
    border-radius: 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 12px;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.45);
    pointer-events: auto;
    cursor: pointer;
    transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
    z-index: 10002;
}

.ios-dynamic-island:hover {
    width: 170px;
    box-shadow: 0 6px 22px rgba(0, 165, 255, 0.35);
}

.ios-dynamic-island .island-camera {
    width: 11px;
    height: 11px;
    border-radius: 50%;
    background: radial-gradient(circle, #0D1B2A 30%, #000814 100%);
    border: 1px solid rgba(255, 255, 255, 0.15);
}

.ios-dynamic-island .island-sensor {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #050b14;
    border: 0.5px solid rgba(255, 255, 255, 0.08);
}

.ios-dynamic-island .island-label {
    font-size: 10px;
    font-weight: 700;
    color: #38BDF8;
    display: none;
    letter-spacing: 0.2px;
}

.ios-dynamic-island:hover .island-label {
    display: inline;
}

.ios-status-icons {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 600;
}

.ios-status-svg {
    width: 17px;
    height: 12px;
    fill: currentColor;
}

/* Home Indicator Bar */
#ios-home-indicator {
    position: fixed;
    bottom: 8px;
    left: 50%;
    transform: translateX(-50%);
    width: 138px;
    height: 5px;
    background: rgba(255, 255, 255, 0.7);
    border-radius: 100px;
    z-index: 10000;
    pointer-events: none;
    transition: background 0.2s ease;
}

[data-theme="light"] #ios-home-indicator {
    background: rgba(0, 0, 0, 0.65);
}

/* Simulated Ad Overlay */
#sim-ad-modal {
    position: fixed;
    inset: 0;
    background: rgba(3, 7, 18, 0.96);
    z-index: 99999;
    display: none;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px;
    color: #FFFFFF;
    text-align: center;
    animation: simFadeIn 0.25s ease;
}

@keyframes simFadeIn {
    from { opacity: 0; transform: scale(0.96); }
    to { opacity: 1; transform: scale(1); }
}

.sim-ad-box {
    width: 100%;
    max-width: 360px;
    background: #1E293B;
    border: 1px solid rgba(56, 189, 248, 0.4);
    border-radius: 18px;
    padding: 24px;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.7);
    display: flex;
    flex-direction: column;
    align-items: center;
}

.sim-ad-badge {
    background: rgba(56, 189, 248, 0.15);
    color: #38BDF8;
    font-size: 11px;
    font-weight: 800;
    padding: 4px 12px;
    border-radius: 9999px;
    margin-bottom: 12px;
    letter-spacing: 0.5px;
    border: 1px solid rgba(56, 189, 248, 0.35);
}

.sim-ad-timer-ring {
    width: 64px;
    height: 64px;
    border-radius: 50%;
    background: rgba(0, 165, 255, 0.1);
    border: 3px solid #00A5FF;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 24px;
    font-weight: 800;
    color: #38BDF8;
    margin: 16px 0;
}

.sim-btn-action {
    background: #00A5FF;
    border: none;
    color: #FFFFFF;
    font-size: 12px;
    font-weight: 700;
    padding: 8px 20px;
    border-radius: 8px;
    cursor: pointer;
    transition: transform 0.1s, background 0.15s;
}

.sim-btn-action:active {
    transform: scale(0.94);
}

/* Simulated Roblox Launch Payload Inspector Modal */
#sim-launch-modal {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.85);
    z-index: 99998;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 20px;
}

.sim-launch-box {
    width: 100%;
    max-width: 420px;
    max-height: 85vh;
    background: #181B22;
    border: 1.5px solid #00A5FF;
    border-radius: 16px;
    padding: 20px;
    box-shadow: 0 12px 40px rgba(0, 165, 255, 0.35);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    color: #FFFFFF;
}

.sim-launch-json-view {
    flex: 1;
    overflow-y: auto;
    background: #0F172A;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 12px;
    font-family: monospace;
    font-size: 11px;
    color: #38BDF8;
    white-space: pre-wrap;
    word-break: break-all;
    margin: 12px 0;
    max-height: 300px;
}
</style>

<div id="ios-status-bar">
    <div class="ios-status-time" id="sim-status-clock">9:41</div>
    <div class="ios-dynamic-island" onclick="toggleSimMode()" title="Nhấn để Bật/Tắt Khung iPhone 16 Pro">
        <div class="island-camera"></div>
        <div class="island-label">Masterstrap iOS</div>
        <div class="island-sensor"></div>
    </div>
    <div class="ios-status-icons">
        <svg class="ios-status-svg" viewBox="0 0 18 12">
            <rect x="0" y="8" width="3" height="4" rx="0.5"/>
            <rect x="4.5" y="6" width="3" height="6" rx="0.5"/>
            <rect x="9" y="3.5" width="3" height="8.5" rx="0.5"/>
            <rect x="13.5" y="0" width="3" height="12" rx="0.5"/>
        </svg>
        <span>5G</span>
        <svg class="ios-status-svg" viewBox="0 0 24 14" style="width:20px;height:12px">
            <rect x="1" y="1" width="19" height="12" rx="3" fill="none" stroke="currentColor" stroke-width="1.8"/>
            <rect x="3" y="3" width="15" height="8" rx="1.5"/>
            <path d="M21 5.5v3a1.5 1.5 0 0 0 1.5-1.5v0A1.5 1.5 0 0 0 21 5.5z"/>
        </svg>
    </div>
</div>

<div id="ios-home-indicator"></div>

<!-- Ad Player Simulation Modal -->
<div id="sim-ad-modal">
    <div class="sim-ad-box">
        <div class="sim-ad-badge">IronSource LevelPlay + Unity Ads (iOS Simulator)</div>
        <h3 id="sim-ad-title" style="font-size:17px;font-weight:700">Rewarded Video Ad</h3>
        <p style="font-size:12px;color:#94A3B8;margin-top:4px">Masterstrap iOS Ad Mediation Pipeline</p>
        <div class="sim-ad-timer-ring" id="sim-ad-timer">5</div>
        <p id="sim-ad-status-msg" style="font-size:12.5px;color:#38BDF8;font-weight:600">Simulating video playback...</p>
        <button id="sim-ad-action-btn" class="sim-btn-action" style="margin-top:16px;padding:8px 24px;display:none" onclick="dismissSimAd()">
            Claim Reward
        </button>
    </div>
</div>

<!-- Roblox Launch Inspection Modal -->
<div id="sim-launch-modal">
    <div class="sim-launch-box">
        <div style="display:flex;align-items:center;justify-content:space-between">
            <h3 style="font-size:16px;font-weight:800;color:#00A5FF">🚀 Launch Roblox Simulation</h3>
            <span style="cursor:pointer;font-size:18px;color:#94A3B8" onclick="closeSimLaunchModal()">✕</span>
        </div>
        <div style="font-size:11px;color:#94A3B8;margin-top:4px" id="sim-launch-target-info">Target: Roblox Global | Channel: global</div>
        <div class="sim-launch-json-view" id="sim-launch-payload-txt">Payload JSON Loading...</div>
        <div style="display:flex;justify-content:flex-end;gap:8px">
            <button class="sim-btn-action" style="background:#334155" onclick="copySimPayload()">Copy Payload</button>
            <button class="sim-btn-action" onclick="closeSimLaunchModal()">Close</button>
        </div>
    </div>
</div>

<script>
(function() {
    if (typeof window === "undefined") return;

    // Clock Updater
    function updateClock() {
        const d = new Date();
        const h = String(d.getHours()).padStart(2, "0");
        const m = String(d.getMinutes()).padStart(2, "0");
        const el = document.getElementById("sim-status-clock");
        if (el) el.innerText = h + ":" + m;
    }
    updateClock();
    setInterval(updateClock, 10000);

    // Apply safe area mode to body
    document.body.classList.add("sim-phone-mode");

    let currentAdPayload = null;
    let adCountdownTimer = null;
    let pendingLaunchPayload = null;

    window.toggleSimMode = function() {
        const isFramed = document.body.classList.toggle("sim-phone-mode");
        const bar = document.getElementById("ios-status-bar");
        const ind = document.getElementById("ios-home-indicator");
        if (bar) bar.style.display = isFramed ? "flex" : "none";
        if (ind) ind.style.display = isFramed ? "block" : "none";
        if (typeof showToast === "function") {
            showToast(isFramed ? "📱 iPhone 16 Pro View Active" : "🖥️ Full Window View Active");
        }
    };

    window.closeSimLaunchModal = function() {
        const m = document.getElementById("sim-launch-modal");
        if (m) m.style.display = "none";
    };

    window.copySimPayload = function() {
        if (pendingLaunchPayload) {
            navigator.clipboard.writeText(JSON.stringify(pendingLaunchPayload, null, 2));
            if (typeof showToast === "function") showToast("Copied Launch Payload JSON to Clipboard!");
        }
    };

    window.dismissSimAd = function() {
        const modal = document.getElementById("sim-ad-modal");
        if (modal) modal.style.display = "none";
        if (adCountdownTimer) clearInterval(adCountdownTimer);

        if (currentAdPayload && window.MasterstrapAds && typeof window.MasterstrapAds.nativeResult === "function") {
            window.MasterstrapAds.nativeResult({
                requestId: currentAdPayload.requestId || "req_sim",
                outcome: "rewarded",
                detail: "rewarded"
            });
        }
        if (typeof showToast === "function") {
            showToast("🎉 Ad Finished! Reward Granted.");
        }
    };

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
                refreshLevelPlayBanner: {
                    postMessage: function(action) {
                        console.log("%c[iOS Banner Refreshed]", "color: #38BDF8;", action);
                    }
                },
                requestLevelPlayAd: {
                    postMessage: function(payload) {
                        console.log("%c[iOS Ad Request]", "color: #F59E0B; font-weight:bold;", payload);
                        currentAdPayload = payload || {};
                        var format = payload ? payload.format : "rewarded";
                        var isRewarded = (format === "rewarded");

                        var modal = document.getElementById("sim-ad-modal");
                        var title = document.getElementById("sim-ad-title");
                        var timer = document.getElementById("sim-ad-timer");
                        var status = document.getElementById("sim-ad-status-msg");
                        var btn = document.getElementById("sim-ad-action-btn");

                        if (modal && title && timer && status && btn) {
                            modal.style.display = "flex";
                            title.innerText = isRewarded ? "🎬 Rewarded Video Ad" : "📺 Interstitial Ad";
                            btn.style.display = "none";
                            btn.innerText = isRewarded ? "Claim Reward" : "Close Ad";

                            var count = isRewarded ? 5 : 3;
                            timer.innerText = String(count);
                            status.innerText = "Playing ad video... (" + count + "s)";

                            if (adCountdownTimer) clearInterval(adCountdownTimer);
                            adCountdownTimer = setInterval(function() {
                                count--;
                                if (count > 0) {
                                    timer.innerText = String(count);
                                    status.innerText = "Playing ad video... (" + count + "s)";
                                } else {
                                    clearInterval(adCountdownTimer);
                                    timer.innerText = "✓";
                                    status.innerText = "Ad completed! You can now continue.";
                                    btn.style.display = "inline-block";
                                }
                            }, 1000);
                        }
                    }
                },
                launchRoblox: {
                    postMessage: function(payload) {
                        console.log("%c[iOS Launch Roblox]", "color: #10B981; font-weight: bold;", payload);
                        pendingLaunchPayload = payload;

                        var targetChan = (payload && payload.targetChannel) || "global";
                        var ticket = "tk_sim_" + Math.random().toString(36).substring(2, 10);
                        var scheme = (targetChan === "vng" ? "masterstrap-rbx-vng-bridge-v1://" : "masterstrap-rbx-bridge-v1://") + "launch?ticket=" + ticket;

                        var modal = document.getElementById("sim-launch-modal");
                        var info = document.getElementById("sim-launch-target-info");
                        var txt = document.getElementById("sim-launch-payload-txt");

                        if (modal && info && txt) {
                            info.innerHTML = "<b>Target:</b> Roblox " + (targetChan === "vng" ? "VNG" : "Global") + " | <b>URL Scheme:</b> <code style='color:#38BDF8'>" + scheme + "</code>";
                            txt.innerText = JSON.stringify(payload, null, 2);
                            modal.style.display = "flex";
                        }

                        if (typeof showToast === "function") {
                            showToast("🚀 [Simulator] Roblox Launched via URL Scheme!");
                        }
                    }
                },
                saveConfig: {
                    postMessage: function(payload) {
                        console.log("%c[iOS Native Config Saved]", "color: #10B981; font-weight:bold;", payload);
                        if (payload) {
                            if (payload.clientAppSettings) localStorage.setItem("masterstrap_sim_clientAppSettings", payload.clientAppSettings);
                            if (payload.turboProfile) localStorage.setItem("masterstrap_sim_turboProfile", payload.turboProfile);
                            if (payload.globalSettings) localStorage.setItem("masterstrap_sim_globalSettings", payload.globalSettings);
                            if (payload.shaderProfile) localStorage.setItem("masterstrap_sim_shaderProfile", payload.shaderProfile);
                            if (payload.fontProfile) localStorage.setItem("masterstrap_sim_fontProfile", payload.fontProfile);
                        }
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
        console.log("%c[Masterstrap Live Preview] WebKit iOS Simulator Bridge Initialized (Pixel-Perfect iPhone Frame)", "color: #00A5FF; font-weight: bold; font-size: 13px;");
    }
})();
</script>
"""
    if "</head>" in html_content:
        return html_content.replace("</head>", bridge_script + "\n</head>", 1)
    return bridge_script + html_content


def create_simulator_html(raw_app_html: str) -> str:
    # Ensure masterstrap.png exists in preview directory
    src_logo = ROOT / "Masterstrap" / "app" / "src" / "main" / "res" / "drawable" / "masterstrap.png"
    if not src_logo.is_file():
        src_logo = ROOT.parent / "Android" / "Masterstrap" / "app" / "src" / "main" / "res" / "drawable" / "masterstrap.png"
    if src_logo.is_file():
        dst_logo = PREVIEW_DIR / "masterstrap.png"
        if not dst_logo.is_file():
            shutil.copy2(src_logo, dst_logo)

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
