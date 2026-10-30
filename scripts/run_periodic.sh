#!/bin/sh
# Watsons 周期报告全流程启动器 —— 供 cron 调用，自包含、可后台脱离运行。
#
# 用法:
#   sh scripts/run_periodic.sh              # 跑今天(按日期自动判定周报/月报/年报)
#   sh scripts/run_periodic.sh 2026-06-28   # 跑指定日期
#
# 行为:
#   - 用项目本地 .venv_packages + 正确的 venv python（避免缺依赖）
#   - Step0 自愈装包 -> Step1 周期报告(含发邮件 + 播客)
#   - 全程日志写入 data/logs/<date>/_run_periodic.out
#   - 退出码: 0 成功; 非0 表示失败

set -e

PROJECT_ROOT="/app/working/projects/watsons-retail-intel"
PY="/app/venv/bin/python3"
RECIPIENT="123399974@qq.com"

cd "$PROJECT_ROOT"

# 日期：参数优先，否则用 Asia/Shanghai 当天
if [ -n "$1" ]; then
  TODAY="$1"
else
  TODAY=$(TZ=Asia/Shanghai date +%Y-%m-%d)
fi

LOG_DIR="$PROJECT_ROOT/data/logs/$TODAY"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/_run_periodic.out"

export PYTHONPATH=".venv_packages:.:$PYTHONPATH"
# Default is fail-closed. Only a separate, reviewed NAS release may enable reader_brief.
: "${WATSONS_PERIODIC_MAIL_MODE:=hold}"
export WATSONS_PERIODIC_MAIL_MODE

{
  echo "=== run_periodic.sh start $(date -u +%Y-%m-%dT%H:%M:%SZ) date=$TODAY ==="

  # Step 0: 自愈装包到项目本地 .venv_packages/
  if ! "$PY" skills/health_check/pip_ensure.py --project-root . --quiet; then
    echo "❌ pip_ensure 自愈失败，中止"
    exit 1
  fi

  # Step 1: 周期报告全流程（含发邮件 + 播客生成）。
  # 规则内嵌在 run_periodic_pipeline: 周一->周报, 月初->月报, 1/1->年报, 冲突串行。
  set +e
  "$PY" -m skills.run_periodic_pipeline.run_periodic_pipeline \
    --project-root . \
    --date "$TODAY" \
    --send-email true \
    --recipient "$RECIPIENT"
  RC=$?
  set -e
  echo "=== run_periodic.sh done $(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$RC ==="
  exit "$RC"
} >> "$LOG" 2>&1
