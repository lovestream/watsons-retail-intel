#!/bin/sh
# Read-only checks for the persistent supervisor design.
# Exit 0 only when the persistent source is present, the installed copy is
# byte-identical when it exists, /app/working is a mount, and /etc is not the
# only copy.  Safe to run in production; it changes nothing.
set -eu
SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC="$SELF_DIR/watsons-retail-intel-r2b.conf"
INST=/etc/supervisor/conf.d/watsons-retail-intel-r2b.conf
rc=0
echo "== mounts =="
if mountpoint -q /app/working; then echo "OK /app/working is a mount"; else echo "FAIL /app/working is not a mount"; rc=1; fi
if mountpoint -q /etc; then echo "WARN /etc is a mount; verify its persistence separately"; else echo "INFO /etc is container writable layer (not persistent)"; fi
echo "== config =="
if [ -f "$SRC" ]; then echo "OK persistent source exists: $SRC"; else echo "FAIL missing persistent source: $SRC"; rc=1; fi
if [ -f "$INST" ]; then
  if cmp -s "$SRC" "$INST"; then echo "OK installed copy matches persistent source"; else echo "FAIL installed copy differs; run install_config.sh after approval"; rc=1; fi
  echo "INFO installed: $INST"
else
  echo "WARN program file not installed in this container yet"
fi
echo "== supervisor PID1 =="
tr '\0' ' ' </proc/1/cmdline 2>/dev/null || true
echo
echo "== supervisor status (read-only) =="
if command -v supervisorctl >/dev/null 2>&1; then
  supervisorctl status 2>&1 || echo "WARN supervisorctl status unavailable"
fi
echo "== host bind mount required for rebuild survival =="
echo "Add to NAS compose/container config (source is on the persistent /app/working volume):"
echo "  $SRC:/etc/supervisor/conf.d/watsons-retail-intel-r2b.conf:ro"
exit "$rc"
