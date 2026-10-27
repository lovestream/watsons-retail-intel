#!/bin/sh
# Idempotent, non-destructive installer for the Watsons supervisor program file.
# Default is a dry run.  It never starts, stops, or restarts a program.
#
# Usage:
#   ./install_config.sh                         # show what would happen
#   ./install_config.sh --root /tmp/newroot --apply
#   ./install_config.sh --root / --apply       # production file copy: separate approval
#
# The persistent source lives under /app/working (the mounted volume).  On a
# container rebuild, mount this file read-only into /etc/supervisor/conf.d/ so
# supervisor sees it before any program starts.
set -eu

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG="$SELF_DIR/watsons-retail-intel-r2b.conf"
ROOT="/"
APPLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT=${2:?}; shift 2 ;;
    --config) CONFIG=${2:?}; shift 2 ;;
    --apply) APPLY=1; shift ;;
    -h|--help) sed -n '1,18p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

DEST_DIR="$ROOT/etc/supervisor/conf.d"
DEST="$DEST_DIR/watsons-retail-intel-r2b.conf"
test -f "$CONFIG" || { echo "missing source config: $CONFIG" >&2; exit 1; }
echo "source: $CONFIG"
echo "dest:   $DEST"
if [ "$APPLY" -eq 0 ]; then
  echo "DRY RUN: pass --apply to copy; supervisor is not touched."
  exit 0
fi
mkdir -p "$DEST_DIR"
tmp="$DEST.tmp.$$"
cp "$CONFIG" "$tmp"
chmod 0644 "$tmp"
mv "$tmp" "$DEST"
echo "installed: $DEST"
echo "NOT running supervisorctl reread/update; that is a separate, approved action."
