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
if ! "$swift_stdlib_tool" \
    --copy \
    --verbose \
    --scan-executable "$app_binary" \
    --platform iphoneos \
    --unsigned-destination "$frameworks_dir"; then
  echo "swift-stdlib-tool failed; copying the executable's Swift dependencies directly." >&2
fi

# Xcode 26.6's swift-stdlib-tool can crash while scanning this standalone
# force-loaded executable. Mirror its essential copy phase from the same
# Xcode toolchain so the generated IPA remains deterministic.
swift_binary="$(xcrun --find swift)"
swift_library_root="$(cd "$(dirname "$swift_binary")/../lib" && pwd)"
# The copied concurrency dylib has its own @rpath dependencies (at least
# libswiftCore). Walk the dependency graph so dyld can resolve every Swift
# dylib from the app's Frameworks directory on older iOS versions.
swift_queue=("$app_binary")
swift_seen=()

find_swift_library() {
  local swift_library="$1"
  local preferred_root="$swift_library_root/swift-5.5/iphoneos"
  local source_library=""
  if [[ -f "$preferred_root/$swift_library" ]]; then
    source_library="$preferred_root/$swift_library"
  else
    source_library="$(
      find "$swift_library_root" -type f \
        -path "*/iphoneos/$swift_library" -print |
        sort |
        head -n 1
    )"
  fi
  printf '%s' "$source_library"
}

copy_swift_library() {
  local swift_library="$1"
  local seen_library
  for seen_library in "${swift_seen[@]}"; do
    [[ "$seen_library" == "$swift_library" ]] && return 0
  done
  swift_seen+=("$swift_library")

  local target_library="$frameworks_dir/$swift_library"
  if [[ ! -f "$target_library" ]]; then
    local source_library="$(find_swift_library "$swift_library")"
    if [[ -z "$source_library" || ! -f "$source_library" ]]; then
      echo "Swift runtime dependency not found in Xcode toolchain: $swift_library" >&2
      exit 1
    fi
    echo "Embedding $swift_library from $source_library"
    cp -f "$source_library" "$target_library"
    chmod 755 "$target_library"
  fi
  swift_queue+=("$target_library")
}

# Force the concurrency library into the graph even when the executable's
# load command is weak-linked and otool does not report it as @rpath.
copy_swift_library "libswift_Concurrency.dylib"

queue_index=0
while [[ "$queue_index" -lt "${#swift_queue[@]}" ]]; do
  swift_binary_to_scan="${swift_queue[$queue_index]}"
  queue_index=$((queue_index + 1))
  while IFS= read -r swift_library; do
    [[ -n "$swift_library" ]] || continue
    copy_swift_library "$swift_library"
  done < <(
    otool -L "$swift_binary_to_scan" |
      awk '/@rpath\/libswift[^ ]*\.dylib/ { name=$1; sub(/^@rpath\//, "", name); print name }' |
      sort -u
  )
done

if [[ ! -f "$frameworks_dir/libswift_Concurrency.dylib" ||
      ! -f "$frameworks_dir/libswiftCore.dylib" ]]; then
  echo "Swift Concurrency runtime graph is incomplete" >&2
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
