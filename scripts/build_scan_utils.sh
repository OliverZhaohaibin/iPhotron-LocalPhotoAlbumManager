#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
native_dir="$repo_root/src/iPhoto/_native"
src="$native_dir/scan_utils.c"

if [[ ! -f "$src" ]]; then
  echo "Missing source file: $src" >&2
  exit 1
fi

cc_bin="${CC:-}"
if [[ -z "$cc_bin" ]]; then
  for candidate in cc gcc clang; do
    if command -v "$candidate" >/dev/null 2>&1; then
      cc_bin="$candidate"
      break
    fi
  done
fi

if [[ -z "$cc_bin" ]]; then
  echo "No C compiler found. Set CC or install cc/gcc/clang." >&2
  exit 1
fi

uname_s="$(uname -s)"
build_dir="$native_dir/build/${uname_s,,}"

mkdir -p "$build_dir"

if [[ "$uname_s" == "Darwin" ]]; then
  out="$native_dir/_scan_utils.dylib"
  "$cc_bin" -O3 -dynamiclib -fPIC -I"$native_dir" "$src" -o "$out"
else
  out="$native_dir/_scan_utils.so"
  "$cc_bin" -O3 -shared -fPIC -I"$native_dir" "$src" -o "$out"
fi

echo "Built scan extension: $out"
