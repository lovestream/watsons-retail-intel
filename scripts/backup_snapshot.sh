#!/bin/sh
# backup_snapshot.sh — 本地代码快照备份（§37，git push 被 SSH 阻塞时的替代防护）
#
# 用法:
#   sh scripts/backup_snapshot.sh            # 备份到 data/backups/
#   sh scripts/backup_snapshot.sh /dest/dir  # 备份到指定目录
#
# 内容:
#   1. git bundle（含全部已提交历史 + 未推送提交）
#   2. 代码/配置/脚本/文档 tar.gz（排除 data/、podcasts/、reports/、.venv_packages/ 等大目录）
#
# 保留最近 N=14 份，自动清理旧档。

set -e

PROJECT_ROOT="/app/working/projects/watsons-retail-intel"
DEST="${1:-$PROJECT_ROOT/data/backups}"
mkdir -p "$DEST"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
cd "$PROJECT_ROOT"

echo "=== backup_snapshot.sh $STAMP ==="

# 1. git bundle（包含所有分支 + 未推送提交）
if git rev-parse --git-dir >/dev/null 2>&1; then
  BUNDLE="$DEST/watsons-retail-intel-$STAMP.bundle"
  git bundle create "$BUNDLE" --all >/dev/null 2>&1 || git bundle create "$BUNDLE" main >/dev/null 2>&1 || true
  if [ -f "$BUNDLE" ]; then
    echo "✅ bundle: $BUNDLE ($(du -h "$BUNDLE" | cut -f1))"
  fi
fi

# 2. 代码 tar.gz（排除大目录与运行时产物）
TARBALL="$DEST/watsons-retail-intel-code-$STAMP.tar.gz"
tar -czf "$TARBALL" \
  --exclude='.venv_packages' \
  --exclude='data' \
  --exclude='podcasts' \
  --exclude='reports' \
  --exclude='.git' \
  --exclude='__pycache__' \
  config skills scripts requirements.lock README.md 2>/dev/null || true
if [ -f "$TARBALL" ]; then
  echo "✅ tarball: $TARBALL ($(du -h "$TARBALL" | cut -f1))"
fi

# 3. 清理旧档（保留 14 份）
ls -1t "$DEST"/watsons-retail-intel-*.bundle "$DEST"/watsons-retail-intel-code-*.tar.gz 2>/dev/null | tail -n +15 | while read -r f; do
  echo "🗑 清理旧备份: $f"
  rm -f "$f"
done

echo "=== done ==="
