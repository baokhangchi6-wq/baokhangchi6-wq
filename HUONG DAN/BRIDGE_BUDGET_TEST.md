# iPhone bridge budget check — 10/10/2026

## Contract

- App and patched Roblox must both come from this commit's macOS Actions builds.
- Small payloads use protocol 3: bounded zlib JSON in the local launch URL. All five documents, including XML, are retained. No HTTP or D1 is needed for this path.
- Larger payloads use the existing protocol 2 API. Server now stores new `do_` tickets in SQLite-backed Durable Objects, with atomic single-use consume and a 60-second expiry alarm. No D1 reads/writes for these new tickets. Durable Object requests/storage have their own pricing.
- Old D1 tickets retain their original consume contract; cron drains expired old rows in bounded batches. Do not apply unrelated Website/license migrations.
- A launch is locked through ad/animation/native dispatch/openURL completion. CHECK licence is locked only while its request is in flight; licence validity is still checked by the existing server, with exact existing counters. 429/5xx does not erase a saved key.
- The native direct-write engine handles verified bool/int storage. Failed memory protection and ambiguous/missing FPS signatures cannot lead to blind writes.
- These are Debug test artifacts, not a release. Existing font/stretch isolation remains enabled; their profiles are transported in full but this build does not claim the disabled backends are working. Use receipts to distinguish import from in-game effects.

## Evidence

- Live sample: 7/7 creates used User-Agent `masterstrap-android/1.2`, no consume in that sample. A User-Agent is not authentication. Android source also uploads cloud configuration in parallel with its preferred local FileProvider/loopback handoff; only the VNG ARM32 native fallback currently consumes it. Do not attribute every DB query to iOS DAU.
- Server tests: 10,000 create/consume pairs retain all five profiles with zero D1 calls. Concurrent consume: one 200, one 410. A wrong channel does not consume the ticket.
- Real local workerd and production regression checks passed; local workerd also retained a 410 KB Unicode envelope.
- Production version: `5f508894-2ed0-42a4-a21e-fdc9376ea7e3`.
- Commands: `python -B tools/check_ios_bridge_budget.py`; `PYTHONPATH=roblox python -B -m unittest discover -s roblox/tests -t roblox`.
- macOS gate: `python -B tools/check_ios_bridge_import.py` compiles the generated native importer and runs real Foundation URL parsing, zlib decode, all five file writes and receipt checks with HTTP blocked. It covers v3/v1, duplicate callbacks, invalid compression, unsupported protocol and decompression above 512 KiB. The previous missing `v=3` parse branch fails this test.

## Optimization rules

- Runtime keeps a retained immutable launch envelope behind a native lock. Engine base discovery is separate from boot completion; one shared apply gate handles cold/warm imports and worker completion.
- A valid snapshot remains the configuration source after its files are consumed. Baseline cannot replace snapshot flags; persistence skips baseline slots owned by the user, and the FPS profile owns its scheduler target.
- Authorization files are removed after apply. Receipts record request/applied request, `BRIDGE_SNAPSHOT`, and the apply count; a leftover authorization file alone does not authorize a fresh process.
- The macOS test executes boot before import, after import and on a concurrent thread. It checks full profile contents, actual bool/int memory values, warm update, duplicate request, stale authorization and zero HTTP calls.

- Success means every supplied profile imported intact, not only fewer SQL calls or `openURL=1`.
- Save/toggle/idle stays local. Do not add bridge polling or licence CHECK to a slider, timer or launch.
- Bound and compress the whole envelope. Keep flag JSON, FPS/stretch profile, XML, font and shader documents; never drop a profile to fit a URL.
- Use a local URL only within the tested 2,048-character encoded payload limit; larger payloads use one publish and one atomic consume. Do not silently launch with incomplete data after transfer fails.
- Coalesce clicks/callbacks for one intent. Retry must not mint endless new tickets or reuse a consumed ticket.
- New cloud tickets use Durable Objects; do not reintroduce per-request D1 rate counters, cleanup or diagnostics writes. Measure DO/Worker costs separately.
- Licence authorization still comes from the existing server; do not reduce SQL by granting a new key from local cache or by losing device/expiry/disabled checks.
- Test protocol, full file content and import receipt, plus the request/DB budget, before shipping both app versions together.

## Budget interpretation

Bridge D1 writes for 10,000 new-ticket launches: **0**, independently of repeated launch count. Protocol 3 also avoids Worker requests; count how often it is selected on iPhone before estimating total requests/cost.

Licence CHECK remains a separate DB: ordinary repeat CHECK currently updates activation + key, approximately 2 logical rows per CHECK before index/trigger costs. 10,000 CHECKs are not 10,000 DAU unless everyone checks exactly once. This change does not promise a global 20,000-write ceiling across unlimited CHECKs or all account DBs.

## iPhone checks

1. Install both artifacts from the same commit. Save a profile changing flag/FPS/stretch/font. Tap launch repeatedly during the ad/animation: one dispatch should be logged.
2. Test cold and warm launch. `MasterstrapBridgeReceipt.json` must show every sent document imported. Check XML/FPS and font profile content in Roblox Documents.
3. Test a large flag configuration to select protocol 2; verify a `do_` ticket without copying the raw ticket into logs or support messages.
4. Open Roblox directly five times. Keep crash `.ips` plus `masterstrap_ios.log`, `masterstrap_bridge.log`, runtime/ABI/bridge receipts and `bridge_build_identity.json`.
5. Copy Masterstrap app log into `Log/Masterstrap`; Roblox logs into `Log/Roblox`. Record device/iOS version, Actions run, and which feature values were set.
6. Test no patched Roblox installed, invalid/expired/reused ticket, interrupted network and 429/503. No missing-profile launch should be reported as a successful complete import.

No claim of a fixed direct-launch crash until the Debug IPA has been tested on iPhone and its stack trace matched to the exact build. No automatic feature restoration based only on an offset name or a successful compile.
