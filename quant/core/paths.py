"""Canonical repo paths. Everything resolves from this file's location.

唯一允许推导 repo root 的地方 —— 别的模块**不要**再写
`Path(__file__).resolve().parent.parent`：那种写法把「自己在第几层」编码进了
业务代码， 目录分层时 5 个文件因此一起指错 (见 quant/README.md)。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # quant/core/paths.py → repo root

HOLDING_ROOT = ROOT / "trade" / "us_stock" / "holding"
STATE_DIR = HOLDING_ROOT / "state"
POSITIONS_PATH = STATE_DIR / "positions.json"
WATCHLIST_PATH = STATE_DIR / "watchlist.json"
EVENTS_DIR = HOLDING_ROOT / "events"
JOURNALS_DIR = HOLDING_ROOT / "journals"
ANALYSIS_DIR = HOLDING_ROOT / "analysis"

RESEARCH_DIR = ROOT / "trade" / "us_stock" / "knowledge" / "research"
STRATEGY_DIR = ROOT / "trade" / "us_stock" / "strategy"

MARKET_DIR = ROOT / "trade" / "us_stock" / "market"
IV_HISTORY_DIR = MARKET_DIR / "iv_history"
KLINE_CACHE_DIR = MARKET_DIR / "klines"
KLINE_DEEP_DIR = MARKET_DIR / "klines_deep"   # 多年深度日线, 回测专用 (research/, 不入 git)
EARNINGS_DEEP_DIR = MARKET_DIR / "earnings_deep"   # 历史财报事件存档 <TICKER>.json
# 前视财报日历 + per-ticker 缩仓比例 (, earnings_window 路 A)。
# 单文件入 git: entry_guard/brief/备注都在 needs_opend=False 的路径上读它。
EARNINGS_CALENDAR_PATH = MARKET_DIR / "earnings_calendar.json"
PREMIUM_DIR = MARKET_DIR / "premium"          # ADR/跨市场溢价追踪, <TICKER>.jsonl ()
SKHY_PREMIUM_PATH = PREMIUM_DIR / "SKHY.jsonl"
FUNDAMENTALS_DIR = MARKET_DIR / "fundamentals"   # OpenD 三表/快照/估值分位缓存 <TICKER>.json (可重拉, 不入 git; 估值层)
VALUATION_DIR = ROOT / "trade" / "us_stock" / "valuation"           # 估值层 (判断层, 零授权效力)
VALUATION_INPUTS_PATH = VALUATION_DIR / "inputs.json"               # 人工区: 商业模式原型 + 覆盖 + 增量新闻 overlay
VALUATIONS_PATH = VALUATION_DIR / "valuations.jsonl"                # 每次估值一行 = 可校准的预测账本
VALUATION_CALIBRATION_PATH = VALUATION_DIR / "calibration.jsonl"    # 对账/回测校准结果 (每条预测 vs 实际一行)
NAV_HISTORY_PATH = EVENTS_DIR / "nav_history.jsonl"
# 外部现金流账本: 只收银证转账 (出入金),
# 由 data/cash_flow_sync 从券商流水同步; NAV 区间变动必须剔它 (core/nav, report/nav_report)。
CASH_FLOWS_PATH = EVENTS_DIR / "cash_flows.jsonl"
CASH_FLOWS_SYNC_PATH = EVENTS_DIR / "cash_flows.sync.json"   # 同步到哪一天 (报告据此判「账本可能漏计」)
DECISIONS_PATH = EVENTS_DIR / "decisions.jsonl"
SCORES_PATH = JOURNALS_DIR / "scores.jsonl"

TMP_DIR = ROOT / "tmp"
HEARTBEAT_STATE_PATH = TMP_DIR / "heartbeat-state.json"
AGENT_SPEC_DIR = ROOT / "agents"
AGENT_ROUTES_PATH = AGENT_SPEC_DIR / "routing.json"
AGENT_RUNS_DIR = ROOT / "var" / "agent-runs"
AGENT_CONTROL_DIR = ROOT / "var" / "agent-control"
ZONE_WRITER_PATH = ROOT / ".claude" / "skills" / "trading-analysis" / "scripts" / "update_position_zones.py"

# Optional intelligence layer.  Everything below AI_NEWS_DIR is produced by a
# separate, user-owned news/macro workflow that lives outside this repository:
# trading code may only READ these paths and must degrade gracefully when they
# are absent.  Nothing here grants authorization.
AI_NEWS_DIR = ROOT / "ai_news"
AI_NEWS_STATE_DIR = AI_NEWS_DIR / "state"
MACRO_DIR = AI_NEWS_DIR / "macro"
MACRO_CURRENT_PATH = MACRO_DIR / "current.json"
MACRO_HISTORY_DIR = MACRO_DIR / "history"
MACRO_EVENTS_DIR = MACRO_DIR / "events"
MACRO_SNAPSHOTS_DIR = MACRO_DIR / "snapshots"
MACRO_SCHEMA_PATH = AI_NEWS_DIR / "schema" / "macro-state.schema.json"
VALUATION_OVERLAYS_PATH = AI_NEWS_DIR / "valuation" / "overlays.jsonl"   # 新闻→估值驱动 overlay (情报层经 quant/valuation/overlays.py add 写, 估值层只读)
MACRO_LOCK_PATH = AI_NEWS_STATE_DIR / ".macro-state.lock"

STATE_LOCK_PATH = Path(os.environ.get("COREYTRADING_STATE_LOCK_PATH",
                                      str(STATE_DIR / ".state.lock")))
OPTION_PLANS_PATH = STATE_DIR / "option_plans.json"   # entry_guard 落, 两个 sync 纯读
EXIT_GUARDS_PATH = STATE_DIR / "exit_guards.json"

ENV_PATH = ROOT / ".env"
OPENAPI_SCRIPTS = ROOT / ".claude" / "skills" / "openapi" / "scripts"


def add_openapi_path() -> None:
    """Put the moomoo openapi skill's script dir on sys.path — it provides `common`
    (OpenD context factory + RET_OK/safe_* helpers), which is not a package and can
    only be reached this way. Idempotent; call right before `from common import ...`.
    """
    p = str(OPENAPI_SCRIPTS)
    if p not in sys.path:
        sys.path.insert(0, p)


# Read-only scheduler metadata inventory; never credential or task body paths.
CLAUDE_SCHEDULE_REGISTRY_ROOT = Path.home() / "Library" / "Application Support" / "Claude" / "claude-code-sessions"
CODEX_AUTOMATION_ROOT = Path.home() / ".codex" / "automations"
