#!/bin/sh
# R2-B cutover backup draft.  Default is a read-only dry run.
#
# This captures the dirty production worktree without using reset/clean:
#   - tracked.patch: git diff --binary HEAD (tracked modifications)
#   - untracked.tar.gz: every untracked, non-ignored file
#   - status.txt, HEAD, file lists, SHA256SUMS
#
# The archive is written OUTSIDE the production checkout.  It may contain
# private report data or diagnostic scripts; never commit or upload it.
#
# Usage:
#   sh ops/r2b/backup_worktree.sh
#   sh ops/r2b/backup_worktree.sh --execute --output-root /app/working/backups/watsons-retail-intel
#
# This script never deletes or overwrites an existing backup directory.

set -eu

PROJECT_ROOT="${PROJECT_ROOT:-/app/working/projects/watsons-retail-intel}"
OUTPUT_ROOT="${OUTPUT_ROOT:-/app/working/backups/watsons-retail-intel}"
EXECUTE=0

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --execute) EXECUTE=1; shift ;;
    --project-root) PROJECT_ROOT="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

test -d "$PROJECT_ROOT/.git" || { echo "not a git worktree: $PROJECT_ROOT" >&2; exit 2; }
test -d "$PROJECT_ROOT/data" || { echo "missing production data directory" >&2; exit 2; }

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$OUTPUT_ROOT/cutover-$STAMP"
STATUS_COUNT=$(git -C "$PROJECT_ROOT" status --porcelain=v1 -uall | wc -l | tr -d ' ')
UNTRACKED_COUNT=$(git -C "$PROJECT_ROOT" ls-files --others --exclude-standard | wc -l | tr -d ' ')
HEAD_SHA=$(git -C "$PROJECT_ROOT" rev-parse HEAD)

echo "project_root=$PROJECT_ROOT"
echo "head=$HEAD_SHA"
echo "dirty_entries=$STATUS_COUNT untracked=$UNTRACKED_COUNT"
echo "output=$OUT"

if [ "$EXECUTE" -ne 1 ]; then
  echo "MODE=dry-run (no files written)"
  echo "would run: git diff --binary HEAD > tracked.patch"
  echo "would run: git ls-files --others --exclude-standard -z | tar -czf untracked.tar.gz -C project"
  echo "would run: sha256sum + tar -tzf verification"
  exit 0
fi

test ! -e "$OUT" || { echo "refusing to overwrite existing backup: $OUT" >&2; exit 2; }
mkdir -p "$OUT"
chmod 700 "$OUT"

git -C "$PROJECT_ROOT" rev-parse HEAD > "$OUT/HEAD"
git -C "$PROJECT_ROOT" status --porcelain=v1 -uall > "$OUT/status.txt"
git -C "$PROJECT_ROOT" diff --stat HEAD > "$OUT/diffstat.txt"
git -C "$PROJECT_ROOT" diff --binary HEAD > "$OUT/tracked.patch"
git -C "$PROJECT_ROOT" ls-files --others --exclude-standard -z > "$OUT/untracked.list.z"

# GNU tar understands --null; keep path names exact, including spaces.
tar --null -czf "$OUT/untracked.tar.gz" -C "$PROJECT_ROOT" -T "$OUT/untracked.list.z"

chmod 600 "$OUT"/*

# Verification, not just creation.
if [ -s "$OUT/tracked.patch" ]; then
  git -C "$PROJECT_ROOT" apply --check --reverse "$OUT/tracked.patch"
fi
tar -tzf "$OUT/untracked.tar.gz" >/dev/null
(
  cd "$OUT"
  sha256sum HEAD status.txt diffstat.txt tracked.patch untracked.list.z untracked.tar.gz > SHA256SUMS
  chmod 600 SHA256SUMS
  sha256sum -c SHA256SUMS
) > "$OUT/verify.txt"
chmod 600 "$OUT/verify.txt"

echo "MODE=execute"
echo "backup_ready=$OUT"
echo "verify=$OUT/verify.txt"
du -sh "$OUT"
