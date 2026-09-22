#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 path/to/Masterstrap.ipa" >&2
  exit 64
fi

ipa_path="$1"
if [[ ! -f "$ipa_path" ]]; then
  echo "IPA not found: $ipa_path" >&2
  exit 66
fi

ipa_dir="$(cd "$(dirname "$ipa_path")" && pwd)"
ipa_path="$ipa_dir/$(basename "$ipa_path")"
temp_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
work_dir="$(mktemp -d "$temp_root/masterstrap-swift-runtime.XXXXXX")"
unpacked_dir="$work_dir/unpacked"
rebuilt_ipa="$work_dir/Masterstrap.ipa"

cleanup() {
  rm -rf -- "$work_dir"
}
trap cleanup EXIT

mkdir -p "$unpacked_dir"
ditto -x -k "$ipa_path" "$unpacked_dir"

app_dir="$(find "$unpacked_dir/Payload" -maxdepth 1 -type d -name '*.app' -print -quit)"
if [[ -z "$app_dir" || ! -d "$app_dir" ]]; then
  echo "No .app bundle found in $ipa_path" >&2
  exit 65
fi

app_binary="$app_dir/Masterstrap"
if [[ ! -f "$app_binary" ]]; then
  echo "Masterstrap executable missing from $ipa_path" >&2
  exit 65
fi

frameworks_dir="$app_dir/Frameworks"
mkdir -p "$frameworks_dir"

swift_stdlib_tool="$(xcrun --find swift-stdlib-tool)"
"$swift_stdlib_tool" \
  --copy \
  --verbose \
  --scan-executable "$app_binary" \
  --platform iphoneos \
  --destination "$frameworks_dir" \
  --unsigned-destination

if [[ ! -f "$frameworks_dir/libswift_Concurrency.dylib" ]]; then
  echo "swift-stdlib-tool did not embed libswift_Concurrency.dylib" >&2
  exit 1
fi

if ! otool -l "$app_binary" | grep -A2 LC_RPATH | grep -q '@executable_path/Frameworks'; then
  echo "Masterstrap executable is missing @executable_path/Frameworks LC_RPATH" >&2
  exit 1
fi

for resource_name in omsdk-v1.js omid-session-client-v1.js PrivacyInfo.xcprivacy; do
  if [[ ! -f "$app_dir/$resource_name" ]]; then
    echo "Required packaged resource missing: $resource_name" >&2
    exit 1
  fi
done

echo "Embedded Swift libraries:"
find "$frameworks_dir" -maxdepth 1 -type f -name 'libswift*.dylib' -print -exec otool -L {} \;

(
  cd "$unpacked_dir"
  ditto -c -k --sequesterRsrc --keepParent Payload "$rebuilt_ipa"
)
mv -f "$rebuilt_ipa" "$ipa_path"

echo "Verified IPA contents:"
unzip -l "$ipa_path" | grep -E 'Frameworks/libswift|omsdk-v1.js|omid-session-client-v1.js|PrivacyInfo.xcprivacy'
