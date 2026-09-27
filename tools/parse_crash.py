import json
import sys

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else r"output\crashes\Roblox-2026-09-27-211657.ips"
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    meta = json.loads(lines[0])
    body = json.loads("".join(lines[1:]))

    print("=== CRASH METADATA ===")
    for k in ["app_name", "bundleID", "os_version", "timestamp"]:
        print(f"{k}: {meta.get(k)}")

    print("\n=== TERMINATION ===")
    print(json.dumps(body.get("termination"), indent=2))
    print("\n=== EXCEPTION ===")
    print(json.dumps(body.get("exception"), indent=2))
    print("faultingThread:", body.get("faultingThread"))

    fault_idx = body.get("faultingThread", 0)
    threads = body.get("threads", [])
    if fault_idx < len(threads):
        th = threads[fault_idx]
        print(f"\n=== FAULTING THREAD {fault_idx} ({th.get('name')}) ===")
        images = body.get("usedImages", [])
        for i, frame in enumerate(th.get("frames", [])[:40]):
            img_idx = frame.get("imageIndex", -1)
            img_name = (images[img_idx].get("name") if 0 <= img_idx < len(images) else "?") or "?"
            sym = frame.get("symbol", "")
            offset = frame.get("imageOffset", 0)
            loc = frame.get("symbolLocation", 0)
            print(f"#{i:02d} {img_name:<25} 0x{offset:08x} {sym} + {loc}")

    print("\n=== IMAGES IN ROBLOX.APP ===")
    for img in body.get("usedImages", []):
        path = img.get("path", "")
        if "Roblox.app" in path:
            print(f"  {img.get('name')}: {path}")

if __name__ == "__main__":
    main()
