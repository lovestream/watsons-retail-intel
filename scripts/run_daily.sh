#!/bin/sh
# Watsons 日报全流程启动器 —— 供 cron 调用，自包含、可后台脱离运行。
# 设计目标：cron 端只需一行调用，agent 几乎不消耗 token（无需调查/汇报）。
#
# 用法:
#   sh scripts/run_daily.sh              # 跑今天
#   sh scripts/run_daily.sh 2026-06-28   # 跑指定日期
#
# 行为:
#   - 用项目本地 .venv_packages + 正确的 venv python（避免缺依赖）
#   - Step0 自愈装包 -> Step1 全流程（含发邮件）
#   - 全程日志写入 data/logs/<date>/_run_daily.out
#   - 退出码: 0 成功启动并完成; 非0 表示启动阶段失败

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
LOG="$LOG_DIR/_run_daily.out"

export PYTHONPATH=".venv_packages:.:$PYTHONPATH"

{
  echo "=== run_daily.sh start $(date -u +%Y-%m-%dT%H:%M:%SZ) date=$TODAY ==="

  # Step 0: 自愈装包到项目本地 .venv_packages/
  if ! "$PY" skills/health_check/pip_ensure.py --project-root . --quiet; then
    echo "❌ pip_ensure 自愈失败，中止"
    exit 1
  fi

  # Step 1: 采集/提取/评分/分析/新颖性检查，生成事件池（供周/月/年报使用）。
  # 2026-08 起不再生成日报/播客/日邮 —— 停在 novelty_check，end at event pool。
  "$PY" -m skills.run_daily_pipeline.run_daily_pipeline \
    --project-root . \
    --date "$TODAY" \
    --use-llm true \
    --send-email false \
    --recipient "$RECIPIENT" \
    --end-step novelty_check \
    --resume false \
    --continue-on-error true

  echo "=== run_daily.sh done $(date -u +%Y-%m-%dT%H:%M:%SZ) exit=$? ==="
} >> "$LOG" 2>&1
