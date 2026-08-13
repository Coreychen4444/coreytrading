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
NAV_HISTORY_PATH = EVENTS_DIR / "nav_history.jsonl"
DECISIONS_PATH = EVENTS_DIR / "decisions.jsonl"
SCORES_PATH = JOURNALS_DIR / "scores.jsonl"

TMP_DIR = ROOT / "tmp"
HEARTBEAT_STATE_PATH = TMP_DIR / "heartbeat-state.json"

STATE_LOCK_PATH = Path(os.environ.get("TRADINGOPS_STATE_LOCK_PATH",
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
