#!/bin/sh
# R2-B/C full cutover backup.  Default is a read-only dry run.
#
# Captures the dirty production worktree without reset/clean:
#   - tracked.patch: git diff --binary HEAD (tracked modifications)
#   - untracked.tar.gz: every untracked, non-ignored file
#   - business-data.tar.gz: lossless snapshot of data/reports/podcasts/config
#     (ignored business data included; data/backups and data/tmp excluded)
#   - business-data.manifest.tsv: size + SHA256 + path for every captured file
#   - restore_staging verification when --execute (extract + compare + remove)
#   - status.txt, HEAD, file lists, SHA256SUMS
#
# Secrets are NOT put into the public repository.  Selected secret/config
# filenames are excluded from the local snapshot and listed by name only in
# secrets_not_included.txt.  The backup directory is 0700/0600 and must live
# outside the project; never commit or upload it.
#
# Usage:
#   sh ops/r2b/backup_worktree.sh
#   sh ops/r2b/backup_worktree.sh --execute --output-root <BACKUP_ROOT>
#   sh ops/r2b/backup_worktree.sh --execute --output-root <ROOT> --no-restore-check
#
# This script never deletes or overwrites an existing backup directory.

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT="${PROJECT_ROOT:-$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)}"
OUTPUT_ROOT="${OUTPUT_ROOT:-}"
EXECUTE=0
RESTORE_CHECK=1

usage() {
  sed -n '2,34p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --execute) EXECUTE=1; shift ;;
    --project-root) PROJECT_ROOT="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --no-restore-check) RESTORE_CHECK=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "not a git worktree: $PROJECT_ROOT" >&2; exit 2; }
test -d "$PROJECT_ROOT/data" || { echo "missing production data directory" >&2; exit 2; }
test -n "$OUTPUT_ROOT" || [ "$EXECUTE" -ne 1 ] || { echo "set --output-root to a persistent path outside the project" >&2; exit 2; }

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${OUTPUT_ROOT:-<output-root-unset>}/cutover-$STAMP"
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
  echo "would run: tar -czf business-data.tar.gz data reports podcasts config (secrets/backups/tmp excluded)"
  echo "would run: business-data.manifest.tsv + sha256sum + tar -tzf verification"
  echo "would run: extract business-data.tar.gz to staging and compare file manifest"
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

# Build a lossless business-data snapshot.  The file list is generated from
# the source tree first, so tar and restore verification use exactly the same
# path set.  Directories are created by tar; manifests list files only.
BUSINESS_LIST="$OUT/business-data.list"
: > "$BUSINESS_LIST"
for rel in data reports podcasts config memory; do
  [ -e "$PROJECT_ROOT/$rel" ] || continue
  find "$PROJECT_ROOT/$rel"     \( -path "$PROJECT_ROOT/data/backups" -o -path "$PROJECT_ROOT/data/backups/*"        -o -path "$PROJECT_ROOT/data/tmp" -o -path "$PROJECT_ROOT/data/tmp/*"        -o -path "$PROJECT_ROOT/__pycache__" -o -path "*/__pycache__/*"        -o -name '.DS_Store' -o -name '*.pyc' \) -prune -o -type f -print
 done | sed "s#^$PROJECT_ROOT/##" | LC_ALL=C sort > "$BUSINESS_LIST"

# Never copy credentials into the snapshot.  Record names only for manual restore.
: > "$OUT/secrets_not_included.txt"
if [ -d "$PROJECT_ROOT/config" ]; then
  find "$PROJECT_ROOT/config" -type f     \( -iname '*secret*' -o -iname '*credential*' -o -iname '*password*'        -o -iname 'email.yaml' -o -iname '*.key' -o -iname '*.pem'        -o -iname '*.enc' -o -iname '.env*' \) -print     | sed "s#^$PROJECT_ROOT/##" | LC_ALL=C sort >> "$OUT/secrets_not_included.txt"
fi
for rel in .env .env.local config/email.yaml config/credentials.yaml; do
  [ -f "$PROJECT_ROOT/$rel" ] && echo "$rel" >> "$OUT/secrets_not_included.txt"
done
LC_ALL=C sort -u -o "$OUT/secrets_not_included.txt" "$OUT/secrets_not_included.txt"

# Exclude the secret filenames from the file list (defensive; keep config public).
if [ -s "$OUT/secrets_not_included.txt" ]; then
  grep -v -F -f "$OUT/secrets_not_included.txt" "$BUSINESS_LIST" > "$OUT/business-data.list.tmp" || true
  mv "$OUT/business-data.list.tmp" "$BUSINESS_LIST"
fi

if [ -s "$BUSINESS_LIST" ]; then
  tar -czf "$OUT/business-data.tar.gz" -C "$PROJECT_ROOT" -T "$BUSINESS_LIST"
  # Full file-level manifest (size, sha256, path).  This is the restore oracle.
  : > "$OUT/business-data.manifest.tsv"
  while IFS= read -r f; do
    [ -f "$PROJECT_ROOT/$f" ] || continue
    size=$(wc -c < "$PROJECT_ROOT/$f" | tr -d ' ')
    sha=$(sha256sum "$PROJECT_ROOT/$f" | awk '{print $1}')
    printf '%s\t%s\t%s\n' "$size" "$sha" "$f" >> "$OUT/business-data.manifest.tsv"
  done < "$BUSINESS_LIST"
else
  : > "$OUT/business-data.manifest.tsv"
  tar -czf "$OUT/business-data.tar.gz" -T /dev/null
fi

chmod 600 "$OUT"/*

# Verification, not just creation.
if [ -s "$OUT/tracked.patch" ]; then
  git -C "$PROJECT_ROOT" apply --check --reverse "$OUT/tracked.patch"
fi
tar -tzf "$OUT/untracked.tar.gz" >/dev/null
tar -tzf "$OUT/business-data.tar.gz" >/dev/null
(
  cd "$OUT"
  sha256sum HEAD status.txt diffstat.txt tracked.patch untracked.list.z untracked.tar.gz     business-data.list business-data.manifest.tsv business-data.tar.gz secrets_not_included.txt > SHA256SUMS
  chmod 600 SHA256SUMS
  sha256sum -c SHA256SUMS
) > "$OUT/verify.txt"

if [ "$RESTORE_CHECK" -eq 1 ]; then
  STAGE="$OUT/restore_staging"
  mkdir -p "$STAGE"
  tar -xzf "$OUT/business-data.tar.gz" -C "$STAGE"
  # Compare extracted file set and file-level hashes to the source manifest.
  : > "$STAGE.restore.manifest.tsv"
  while IFS=$(printf '\t') read -r size sha rel; do
    [ -n "$rel" ] || continue
    if [ ! -f "$STAGE/$rel" ]; then
      echo "MISSING\t$rel" >> "$STAGE.restore.manifest.tsv"
      continue
    fi
    rsize=$(wc -c < "$STAGE/$rel" | tr -d ' ')
    rsha=$(sha256sum "$STAGE/$rel" | awk '{print $1}')
    if [ "$size" = "$rsize" ] && [ "$sha" = "$rsha" ]; then
      printf 'OK\t%s\t%s\n' "$sha" "$rel" >> "$STAGE.restore.manifest.tsv"
    else
      printf 'MISMATCH\tsize=%s/%s\tsha=%s/%s\t%s\n' "$size" "$rsize" "$sha" "$rsha" "$rel" >> "$STAGE.restore.manifest.tsv"
    fi
  done < "$OUT/business-data.manifest.tsv"
  total=$(wc -l < "$OUT/business-data.manifest.tsv" | tr -d ' ')
  good=$(grep -c '^OK' "$STAGE.restore.manifest.tsv" 2>/dev/null || true)
  bad=$(grep -vc '^OK' "$STAGE.restore.manifest.tsv" 2>/dev/null || true)
  printf 'restore_files=%s ok=%s bad=%s\n' "$total" "$good" "$bad" > "$OUT/restore_verify.txt"
  chmod 600 "$OUT/restore_verify.txt"
  if [ "$bad" != "0" ]; then
    echo "restore verification failed: $bad mismatches" >&2
    # Keep staging for forensic inspection; do not claim success.
    exit 3
  fi
  rm -rf "$STAGE"
fi

echo "MODE=execute"
echo "backup_ready=$OUT"
echo "verify=$OUT/verify.txt"
if [ "$RESTORE_CHECK" -eq 1 ]; then echo "restore_verify=$OUT/restore_verify.txt"; fi
du -sh "$OUT"
