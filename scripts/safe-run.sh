#!/usr/bin/env bash
set -o pipefail

MAX_BYTES="${MAX_BYTES:-4000}"

if [ "$#" -eq 0 ]; then
  echo "Usage: scripts/safe-run.sh <command...>"
  exit 2
fi

tmp="$(mktemp)"
"$@" >"$tmp" 2>&1
status=$?

head -c "$MAX_BYTES" "$tmp"

size="$(wc -c < "$tmp" | tr -d ' ')"
echo
echo "[exit=$status bytes=$size shown_max=$MAX_BYTES]"

if [ "$size" -gt "$MAX_BYTES" ]; then
  echo "[truncated=true]"
  echo "[hint] Rerun a narrower command instead of dumping full output."
fi

rm -f "$tmp"
exit "$status"
