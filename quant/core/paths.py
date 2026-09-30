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

PORTFOLIO_ROOT = ROOT / "trade" / "portfolio"
STATE_DIR = PORTFOLIO_ROOT / "state"
POSITIONS_PATH = STATE_DIR / "positions.json"
# Current writer path plus explicit read-only Git history aliases.
POSITION_HISTORY_PATHS = ("trade/portfolio/state/positions.json",
                          "trade/us_stock/holding/state/positions.json")
WATCHLIST_PATH = STATE_DIR / "watchlist.json"
EVENTS_DIR = PORTFOLIO_ROOT / "events"
JOURNALS_DIR = PORTFOLIO_ROOT / "journals"
ANALYSIS_DIR = PORTFOLIO_ROOT / "analysis"
PORTFOLIO_OBSERVATIONS_DIR = PORTFOLIO_ROOT / "observations"
CATALYSTS_PATH = PORTFOLIO_OBSERVATIONS_DIR / "catalysts.jsonl"
STRUCTURE_DRIFT_PATH = PORTFOLIO_OBSERVATIONS_DIR / "structure_drift.jsonl"
VALUATION_FORWARD_PATH = PORTFOLIO_OBSERVATIONS_DIR / "valuation_forward.jsonl"   # 研究级 12 个月目标前向账本 (valuation/research_target, 每日逐票一行, 12 月后对账)

RESEARCH_DIR = ROOT / "trade" / "knowledge" / "research"
STRATEGY_DIR = ROOT / "trade" / "strategy"

MARKET_DIR = ROOT / "trade" / "market"
OPTION_KLINE_DIR = MARKET_DIR / "option_klines"
IV_HISTORY_DIR = MARKET_DIR / "iv_history"
KLINE_CACHE_DIR = MARKET_DIR / "klines"
KLINE_DEEP_DIR = MARKET_DIR / "klines_deep"   # 多年深度日线, 回测专用 (research/, 不入 git)
EARNINGS_DEEP_DIR = MARKET_DIR / "earnings_deep"   # 历史财报事件存档 <TICKER>.json
# 前视财报日历 + per-ticker 缩仓比例 (, earnings_window 路 A)。
# 单文件入 git: entry_guard/brief/备注都在 needs_opend=False 的路径上读它。
EARNINGS_CALENDAR_PATH = MARKET_DIR / "earnings_calendar.json"
MARKET_REGIME_PATH = MARKET_DIR / "market_regime.jsonl"   # 大盘状态卡每日快照 = 可对账的预测账本 (journal/market_regime_log, 判读层)
MACRO_EVENT_CALENDAR_PATH = MARKET_DIR / "macro_event_calendar.json"   # 可选: FOMC / CPI / 非农 官方公布日 (journal/macro_lens_log 日历段读, 缺失即空)
MACRO_DAILY_DIR = MARKET_DIR / "macro_daily"   # 跨资产宏观日线缓存 <KEY>.jsonl (data/macro_series, 尾部覆盖式, 可重拉不入 git)
PREMIUM_DIR = MARKET_DIR / "premium"          # ADR/跨市场溢价追踪, <TICKER>.jsonl ()
SKHY_PREMIUM_PATH = PREMIUM_DIR / "SKHY.jsonl"
FUNDAMENTALS_DIR = MARKET_DIR / "fundamentals"   # OpenD 三表/快照/估值分位缓存 <TICKER>.json (可重拉, 不入 git; 估值层)
VALUATION_DIR = ROOT / "trade" / "valuation"           # 估值层 (判断层, 零授权效力)
VALUATION_INPUTS_PATH = VALUATION_DIR / "inputs.json"               # 人工区: 商业模式原型 + 覆盖 + 增量新闻 overlay
# valuations.jsonl / calibration.jsonl (旧估值账本与财报对账) 已随旧估值栈退役, 不再有代码读写
VALUATION_RESPONSE_EVENTS_PATH = VALUATION_DIR / "response_events.jsonl"
VALUATION_RESPONSE_QUERIES_PATH = VALUATION_DIR / "response_queries.json"
VALUATION_COMPANY_MODELS_PATH = VALUATION_DIR / "company_models.json"  # explicit company research inputs; no publishing authority
VALUATION_EPS_MODELS_PATH = VALUATION_DIR / "eps_models.json"  # 精简版 EPS 包 (valuation/eps_model: 未来 8 季三情景利润表, 财报后机械滚动 + 复核; 研究级目标 EPS 源)
VALUATION_RELEASES_PATH = VALUATION_DIR / "releases.json"  # persistent release journal and immutable artifacts
VALUATION_RELEASE_POLICY_PATH = VALUATION_DIR / "release_policy.json"  # host rollout and verified exact review identities
VALUATION_RELEASE_LOCK_PATH = ROOT / "var" / "valuation-daily" / ".release.lock"
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

# Market intelligence data.  The news collection/publication workflow is optional
# and user-owned; this repository ships only the README below INTELLIGENCE_DIR.
# The macro-lens publisher (journal/macro_lens_log) is the only in-repo writer and
# writes only its own macro/ outputs; everything else is read-only and must degrade
# gracefully when absent.  Nothing here grants authorization.
INTELLIGENCE_DIR = ROOT / "trade" / "intelligence"
INTELLIGENCE_STATE_DIR = ROOT / "var" / "intelligence"
INTELLIGENCE_REPORTS_DIR = INTELLIGENCE_DIR / "reports"
INTELLIGENCE_SOURCES_DIR = INTELLIGENCE_DIR / "sources"
INTELLIGENCE_DELIVERY_PATH = INTELLIGENCE_STATE_DIR / "delivery.json"
MACRO_DIR = INTELLIGENCE_DIR / "macro"
MACRO_CURRENT_PATH = MACRO_DIR / "current.json"   # 宏观透镜最新读数 (schema macro_lens/1, authority none)
INTELLIGENCE_CALENDAR_PATH = MACRO_DIR / "calendar.json"  # optional independently verified release queue; not a trading calendar
MACRO_LEDGER_PATH = MACRO_DIR / "ledger.jsonl"   # 宏观透镜每日快照账本 = 可对账的判读记录 (journal/macro_lens_log)
MACRO_REPORTS_DIR = MACRO_DIR / "reports"      # 宏观透镜全文 YYYY-MM/YYYY-MM-DD.md
VALUATION_OVERLAYS_PATH = INTELLIGENCE_DIR / "valuation" / "overlays.jsonl"   # 新闻→估值驱动 overlay (情报层经 quant/valuation/overlays.py add 写, 估值层只读)
MACRO_LOCK_PATH = INTELLIGENCE_STATE_DIR / ".macro-state.lock"
MACRO_LENS_LOCK_PATH = INTELLIGENCE_STATE_DIR / ".macro-lens.lock"   # 宏观透镜发布锁 (journal/macro_lens_log.publish: 账本查重+追加原子化, 同一 asof 两个发布者不写两行)


def resolve_intelligence_reference(reference: str | Path) -> Path:
    """Locate archived data references without rewriting their original bytes.

    Accept current data/runtime paths and both former repo-local data roots.
    This is a reader compatibility boundary, never a second writer location.
    No existence requirement: callers distinguish missing evidence explicitly.
    """
    path = Path(reference)
    if ".." in path.parts:
        raise ValueError("intelligence reference must not traverse parent directories")
    if path.is_absolute():
        try:
            path = path.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError("intelligence reference must belong to this repository") from exc
    if path.parts and path.parts[0] == "ai_news":
        tail = Path(*path.parts[1:])
        if tail.parts and tail.parts[0] == "state":
            target = INTELLIGENCE_STATE_DIR / Path(*tail.parts[1:])
        elif tail.parts and tail.parts[0] in {"macro", "reports", "sources", "valuation", "schema", "examples"}:
            target = INTELLIGENCE_DIR / tail
        else:
            raise ValueError("legacy reference is not an intelligence data artifact")
    elif path.parts[:3] == ("trade", "us_stock", "intelligence"):
        target = INTELLIGENCE_DIR / Path(*path.parts[3:])
    else:
        target = ROOT / path
    resolved = target.resolve()
    if not any(resolved.is_relative_to(base.resolve())
               for base in (INTELLIGENCE_DIR, INTELLIGENCE_STATE_DIR)):
        raise ValueError("intelligence reference is outside the data/runtime roots")
    return resolved

STATE_LOCK_PATH = Path(os.environ.get("COREYTRADING_STATE_LOCK_PATH",
                                      str(ROOT / "var" / "portfolio" / ".state.lock")))
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

# Independent daily valuation research; never a trading-state writer.
VALUATION_RUNS_DIR = ROOT / "var" / "valuation-daily"
