#!/bin/sh
# R2-B cutover restore draft.  Default is a read-only verification.
#
# This intentionally restores to an EMPTY staging directory, never in place.
# It does not run git reset/checkout/clean and does not touch production data.
# A human must review the staged files and apply tracked.patch to a clean
# checkout before copying any runtime file back.
#
# Usage:
#   sh ops/r2b/restore_worktree.sh --archive /path/to/cutover-...
#   sh ops/r2b/restore_worktree.sh --archive ... --stage-dir /tmp/r2b-restore --execute

set -eu

ARCHIVE=""
STAGE=""
EXECUTE=0

usage() {
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --archive) ARCHIVE="$2"; shift 2 ;;
    --stage-dir) STAGE="$2"; shift 2 ;;
    --execute) EXECUTE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

test -n "$ARCHIVE" || { echo "--archive is required" >&2; exit 2; }
test -d "$ARCHIVE" || { echo "archive directory not found: $ARCHIVE" >&2; exit 2; }
test -f "$ARCHIVE/SHA256SUMS" || { echo "missing SHA256SUMS" >&2; exit 2; }
test -f "$ARCHIVE/tracked.patch" || { echo "missing tracked.patch" >&2; exit 2; }
test -f "$ARCHIVE/untracked.tar.gz" || { echo "missing untracked.tar.gz" >&2; exit 2; }

echo "archive=$ARCHIVE"
echo "head=$(cat "$ARCHIVE/HEAD" 2>/dev/null || echo missing)"
echo "dirty_entries=$(cat "$ARCHIVE/status.txt" 2>/dev/null | wc -l | tr -d ' ')"

( cd "$ARCHIVE" && sha256sum -c SHA256SUMS )
tar -tzf "$ARCHIVE/untracked.tar.gz" >/dev/null

if [ "$EXECUTE" -ne 1 ]; then
  echo "MODE=dry-run (archive verified; no staging directory written)"
  exit 0
fi

test -n "$STAGE" || { echo "--stage-dir is required with --execute" >&2; exit 2; }
test ! -e "$STAGE" || { echo "refusing to overwrite existing stage: $STAGE" >&2; exit 2; }
mkdir -p "$STAGE"
chmod 700 "$STAGE"

cp "$ARCHIVE/HEAD" "$ARCHIVE/status.txt" "$ARCHIVE/diffstat.txt" "$ARCHIVE/tracked.patch" "$STAGE/"
tar -xzf "$ARCHIVE/untracked.tar.gz" -C "$STAGE"
chmod -R go-rwx "$STAGE"

echo "MODE=execute"
echo "stage=$STAGE"
echo "next: review status.txt and tracked.patch; apply tracked.patch only to a clean checkout,"
echo "      then copy required runtime files after human confirmation."
