"""事件剧本 (event playbook)：把一场 Tier-1 宏观事件的推演压成一个固定形状。

动机：自由形式的宏观推导容易太散、可读性低 —— 判断被埋在合规话术里，
情景树写成 schema 词表（"2Y 倾向下行"），没有线位、没有参与者、没有第二根。
这个模块把推演**思路**（不是内容）固化成结构：

    一句话判断 → 核心矛盾 → 冻结基准（一个数字一条）→ 命名联合条件（"super-cool" = 四件事同时）
    → 情景（结果条件 → 政策回应分层 → 第一根 → 第二根 → 谁受伤 → 什么观测让它滑向别的情景）
    → 判别线位（每条带品种/单位/出处/时点/复核日）→ 剩余分支 → 组合含义

代码只强制**形状**，不强制内容：分支互斥且穷尽（含边界归属）、第二根不是第一根的镜像、
线位必须有出处和寿命、正文里出现的 `x%` / `x bp` 必须能在线位表里找到、flip 必须指向
另一个情景。它不校准概率、不判断谁对，也不产生任何交易授权（宏观恒为 macro_prior_only）。

`to_schema_scenarios()` 把剧本编译成 `macro-state.schema.json` 的 `next_event.scenarios`，
所以 `macro_state.py` 写入器、immutable plan/result 与事后复评合同全部不变——剧本是**作者格式**，
plan 仍是**发布格式**。渲染在 `quant/report/macro_playbook_report.py`。
"""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, fields, is_dataclass
from datetime import date, datetime
import math
import re
from typing import Any

PLAYBOOK_SCHEMA_VERSION = "1.0.0"
VERDICT_MAX_CHARS = 120          # 一句话判断：一行读完；长了就不是「一句话」
TENSION_MAX_CHARS = 600          # 核心矛盾：一段，不是一章
RISK_LEVELS = ("supportive", "neutral", "adverse", "crisis", "mixed", "unknown")
POLICY_PATHS = ("hold", "one_and_done", "continued", "cut", "other")
JOINT_RULES = ("all", "any")

# 正文里出现的带单位数字：4.826% / 4.65 % / 325bp / 325 bp / 0.20个百分点 不收（口径太杂）
_LEVEL_IN_TEXT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|bp|bps)(?![\w])", re.I)


# ── 数据结构 ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Level:
    """一条判别线位。没有出处和复核日的数字不许进正文。"""
    instrument: str          # 品种：UST10Y / HY_OAS / UST2Y / DXY …
    value: float
    unit: str                # % / bp / index / usd
    meaning: str             # 过了这条线意味着什么（哪两个解释被区分）
    source: str              # 来源与选择理由（"某日收盘高点" / "8 月以来供给拒绝簇" / "研究者判断"）
    as_of: str               # 观测或选择时点，ISO 日期或日期时间
    review_by: str           # 复核日（ISO 日期）；过期 = 线位失效，不许沿用


@dataclass(frozen=True)
class Baseline:
    """冻结基准：一个数字一条，前值 / 共识 / 分项预估三者分开，不许互相冒充。"""
    name: str                # core_cpi_mom
    unit: str
    prior: float | None
    consensus: float | None
    estimate: float | None   # 分项模型或研究者预估；与 consensus 不同物，报告里分列
    source: str
    as_of: str
    note: str = ""           # 口径、季调、缺失原因


@dataclass(frozen=True)
class JointCondition:
    """命名联合条件。"super-cool" 不是 headline 少一位小数，是 N 件事同时发生。"""
    name: str
    parts: tuple[str, ...]
    rule: str = "all"        # all = 全部成立；any = 任一成立（写清 rule，读者不用猜）
    note: str = ""           # 缺哪项 = 「总量已知，广度待确认」


@dataclass(frozen=True)
class Leg:
    """一根 K 线级别的阶段：第一根是初始定价，第二根是市场是否接受政策解释。"""
    window: str              # "发布后 0–30m" / "当日收盘至 T+2 收盘"
    expected: str            # 条件反应（谁先动、往哪动）
    check: str               # 用什么观测检查（品种列表，逗号分隔）
    accepted_if: str         # 政策解释被接受 = 看到什么
    rejected_if: str         # 被拒绝 = 看到什么（滑向哪个情景由 Scenario.flip_to 指定）


@dataclass(frozen=True)
class Scenario:
    id: str
    label: str
    metric: str              # 分类用的基准名，须在 Playbook.baselines 里
    lower: float | None      # 含下界（None = −∞）
    upper: float | None      # 不含上界（None = +∞）
    condition: str           # 人话：结果条件（可含分项冲突的二级标记）
    policy_paths: tuple[str, ...]      # 本分支内要比较的政策回应，取自 POLICY_PATHS
    policy_note: str         # 各路径的条件与沟通要求；央行未给门槛就写未知
    first_leg: Leg
    second_leg: Leg
    hurt: tuple[str, ...]    # 谁受伤 / 被迫调仓 + 约束是什么（≥1）
    flip_to: str             # 什么观测让本情景滑向哪个情景：写 scenario id 或 "residual"
    flip_if: str
    risk_environment: str    # RISK_LEVELS
    levels: tuple[Level, ...] = ()
    probability: tuple[float, float] | None = None
    probability_basis: str = ""
    portfolio_note: str = "" # 本分支下组合/持仓的条件含义（不是授权）


@dataclass(frozen=True)
class Playbook:
    schema_version: str
    event_id: str
    name: str
    scheduled_at_et: str
    authored_at: str
    verdict: str             # 一句话判断（≤ VERDICT_MAX_CHARS）
    tension: str             # 核心矛盾：政策面临什么取舍、市场押什么、主要风险在哪
    priced_path: str         # 市场已计价（带来源与时点）
    predicted_path: str      # 我预测央行会做什么（可写「未定，见情景」）
    preferred_path: str      # 我认为损害最小的路径 —— 与预测分开，不许混
    baselines: tuple[Baseline, ...]
    joint_conditions: tuple[JointCondition, ...]
    scenarios: tuple[Scenario, ...]
    residual: str            # 剩余分支：缺失 / 延期 / 口径冲突 / 即时重大更正
    portfolio: str           # 组合层含义（共同暴露、期权期限），零授权
    shared_levels: tuple[Level, ...] = ()
    unknowns: tuple[str, ...] = ()


# ── 构造 / 序列化 ────────────────────────────────────────────────────────────

_NESTED = {
    "baselines": Baseline, "joint_conditions": JointCondition, "scenarios": Scenario,
    "shared_levels": Level, "levels": Level, "first_leg": Leg, "second_leg": Leg,
}


def _build(cls, data: dict[str, Any]):
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__}: expected object, got {type(data).__name__}")
    names = {f.name for f in fields(cls)}
    extra = set(data) - names
    if extra:
        raise ValueError(f"{cls.__name__}: unknown fields {sorted(extra)}")
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            if f.default is MISSING and f.default_factory is MISSING:
                raise ValueError(f"{cls.__name__}.{f.name}: required")
            continue
        value = data[f.name]
        sub = _NESTED.get(f.name)
        if sub is not None and isinstance(value, list):
            value = tuple(_build(sub, item) for item in value)
        elif sub is not None and isinstance(value, dict):
            value = _build(sub, value)
        elif isinstance(value, list):
            value = tuple(value)
        kwargs[f.name] = value
    return cls(**kwargs)


def from_dict(data: dict[str, Any]) -> Playbook:
    """JSON → Playbook。多余字段 / 缺字段直接报错，不静默补齐。"""
    return _build(Playbook, data)


def to_dict(playbook: Playbook) -> dict[str, Any]:
    def convert(value):
        if is_dataclass(value):
            return {k: convert(v) for k, v in asdict(value).items()}
        if isinstance(value, tuple):
            return [convert(v) for v in value]
        return value
    return convert(playbook)


# ── 校验 ─────────────────────────────────────────────────────────────────────

def _parse_day(value: str) -> date | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _level_errors(level: Level, where: str) -> list[str]:
    errors = []
    if not isinstance(level.value, (int, float)) or isinstance(level.value, bool) or math.isnan(level.value):
        errors.append(f"{where}: value must be a number")
    for name in ("instrument", "unit", "meaning", "source", "as_of", "review_by"):
        if not str(getattr(level, name) or "").strip():
            errors.append(f"{where}.{name}: required")
    a, r = _parse_day(level.as_of), _parse_day(level.review_by)
    if level.as_of and a is None:
        errors.append(f"{where}.as_of: not an ISO date")
    if level.review_by and r is None:
        errors.append(f"{where}.review_by: not an ISO date")
    if a and r and r < a:
        errors.append(f"{where}.review_by: earlier than as_of")
    return errors


def _numbers_in(text: str) -> list[tuple[float, str]]:
    out = []
    for m in _LEVEL_IN_TEXT.finditer(text or ""):
        unit = m.group(2).lower()
        out.append((float(m.group(1)), "bp" if unit.startswith("bp") else "%"))
    return out


def _level_known(num: float, unit: str, levels: tuple[Level, ...], baselines: tuple[Baseline, ...],
                 bounds: tuple[float, ...] = ()) -> bool:
    if unit == "%" and any(math.isclose(b, num, abs_tol=1e-9) for b in bounds):
        return True   # 分支边界本身就是冻结的数字，出处 = 情景树
    for lv in levels:
        if (lv.unit or "").lower().startswith(unit) and math.isclose(lv.value, num, rel_tol=0, abs_tol=1e-9):
            return True
    if unit == "%":
        for b in baselines:
            if (b.unit or "").startswith("%") or b.unit in ("percent", "pct"):
                for v in (b.prior, b.consensus, b.estimate):
                    if v is not None and math.isclose(v, num, abs_tol=1e-9):
                        return True
    return False


def _leg_errors(leg: Leg, where: str) -> list[str]:
    errors = []
    for name in ("window", "expected", "check", "accepted_if", "rejected_if"):
        if not str(getattr(leg, name) or "").strip():
            errors.append(f"{where}.{name}: required")
    return errors


def _interval_errors(scenarios: tuple[Scenario, ...]) -> list[str]:
    """数值分支互斥且穷尽：按 lower 排序后首尾开、相邻端点相等。边界归属 = 含下不含上。"""
    errors: list[str] = []
    by_metric: dict[str, list[Scenario]] = {}
    for s in scenarios:
        by_metric.setdefault(s.metric, []).append(s)
    if len(by_metric) != 1:
        errors.append(f"$.scenarios: numeric branches must share one metric, got {sorted(by_metric)}")
        return errors
    ordered = sorted(scenarios, key=lambda s: -math.inf if s.lower is None else s.lower)
    if ordered[0].lower is not None:
        errors.append(f"$.scenarios[{ordered[0].id}].lower: first branch must be open (-inf) — negative prints need a home")
    if ordered[-1].upper is not None:
        errors.append(f"$.scenarios[{ordered[-1].id}].upper: last branch must be open (+inf)")
    for a, b in zip(ordered, ordered[1:]):
        if a.upper is None or b.lower is None:
            errors.append(f"$.scenarios[{a.id}]→[{b.id}]: interior bounds must be finite")
            continue
        if not math.isclose(a.upper, b.lower, abs_tol=1e-12):
            kind = "gap" if a.upper < b.lower else "overlap"
            errors.append(f"$.scenarios[{a.id}]→[{b.id}]: {kind} between upper={a.upper} and lower={b.lower}")
    for s in scenarios:
        if s.lower is not None and s.upper is not None and s.lower >= s.upper:
            errors.append(f"$.scenarios[{s.id}]: lower >= upper")
    return errors


def validate(playbook: Playbook) -> list[str]:
    """形状校验。空列表 = 通过。不判断内容对错，不校准概率。"""
    errors: list[str] = []
    p = playbook
    if p.schema_version != PLAYBOOK_SCHEMA_VERSION:
        errors.append(f"$.schema_version: expected {PLAYBOOK_SCHEMA_VERSION}")
    for name in ("event_id", "name", "scheduled_at_et", "authored_at", "verdict", "tension",
                 "priced_path", "predicted_path", "preferred_path", "residual", "portfolio"):
        if not str(getattr(p, name) or "").strip():
            errors.append(f"$.{name}: required")
    if len(p.verdict or "") > VERDICT_MAX_CHARS:
        errors.append(f"$.verdict: {len(p.verdict)} chars > {VERDICT_MAX_CHARS}; 一句话判断要一行读完")
    if len(p.tension or "") > TENSION_MAX_CHARS:
        errors.append(f"$.tension: {len(p.tension)} chars > {TENSION_MAX_CHARS}; 矛盾是一段不是一章")
    if p.preferred_path.strip() == p.predicted_path.strip():
        errors.append("$.preferred_path: identical to predicted_path — 「我希望」和「我预测」要分开写；相同也要说明为什么相同")

    if not p.baselines:
        errors.append("$.baselines: at least one frozen baseline")
    baseline_names = [b.name for b in p.baselines]
    if len(baseline_names) != len(set(baseline_names)):
        errors.append("$.baselines: duplicate name")
    for i, b in enumerate(p.baselines):
        for name in ("name", "unit", "source", "as_of"):
            if not str(getattr(b, name) or "").strip():
                errors.append(f"$.baselines[{i}].{name}: required")
        if b.prior is None and b.consensus is None and b.estimate is None:
            errors.append(f"$.baselines[{i}] ({b.name}): prior/consensus/estimate all null — 一个数字都没有就不是基准")

    for i, jc in enumerate(p.joint_conditions):
        if not jc.name.strip():
            errors.append(f"$.joint_conditions[{i}].name: required")
        if len(jc.parts) < 2:
            errors.append(f"$.joint_conditions[{i}] ({jc.name}): a joint condition needs >= 2 parts")
        if jc.rule not in JOINT_RULES:
            errors.append(f"$.joint_conditions[{i}].rule: {jc.rule!r} not in {JOINT_RULES}")

    if len(p.scenarios) < 2:
        errors.append("$.scenarios: need >= 2 numeric branches (plus $.residual)")
        return errors
    ids = [s.id for s in p.scenarios]
    if len(ids) != len(set(ids)):
        errors.append("$.scenarios: duplicate id")
    errors.extend(_interval_errors(p.scenarios))

    for lv_i, lv in enumerate(p.shared_levels):
        errors.extend(_level_errors(lv, f"$.shared_levels[{lv_i}]"))

    probs = [s.probability for s in p.scenarios]
    if any(pr is not None for pr in probs) and any(pr is None for pr in probs):
        errors.append("$.scenarios: probabilities must be all ranges or all null")
    if all(pr is not None for pr in probs):
        mins = sum(pr[0] for pr in probs)
        maxes = sum(pr[1] for pr in probs)
        if mins > 100 or maxes < 100:
            errors.append(f"$.scenarios: probability envelope sum(min)={mins} sum(max)={maxes} must bracket 100")

    for s in p.scenarios:
        w = f"$.scenarios[{s.id}]"
        if s.metric not in baseline_names:
            errors.append(f"{w}.metric: {s.metric!r} not a frozen baseline")
        if not s.condition.strip():
            errors.append(f"{w}.condition: required")
        if not s.policy_paths:
            errors.append(f"{w}.policy_paths: compare at least hold vs one adjustment; 「某个 print 必然加息」不是分析")
        for path in s.policy_paths:
            if path not in POLICY_PATHS:
                errors.append(f"{w}.policy_paths: {path!r} not in {POLICY_PATHS}")
        if len(set(s.policy_paths)) < 2 and len(s.policy_paths) >= 1:
            errors.append(f"{w}.policy_paths: a single path is a prediction, not a comparison")
        if not s.policy_note.strip():
            errors.append(f"{w}.policy_note: required (央行未给门槛就写未知)")
        errors.extend(_leg_errors(s.first_leg, f"{w}.first_leg"))
        errors.extend(_leg_errors(s.second_leg, f"{w}.second_leg"))
        if s.first_leg.window.strip() == s.second_leg.window.strip():
            errors.append(f"{w}: first_leg and second_leg share a window — 第二根必须晚于第一根")
        if s.first_leg.expected.strip() == s.second_leg.expected.strip():
            errors.append(f"{w}: second_leg.expected mirrors first_leg — 第二根写「市场是否接受」，不是复述第一根")
        if s.second_leg.accepted_if.strip() == s.second_leg.rejected_if.strip():
            errors.append(f"{w}.second_leg: accepted_if == rejected_if")
        if not s.hurt:
            errors.append(f"{w}.hurt: who is constrained/forced to reposition? (>=1)")
        if not s.flip_if.strip():
            errors.append(f"{w}.flip_if: required")
        if s.flip_to != "residual" and s.flip_to not in ids:
            errors.append(f"{w}.flip_to: {s.flip_to!r} is not another scenario id or 'residual'")
        if s.flip_to == s.id:
            errors.append(f"{w}.flip_to: points at itself")
        if s.risk_environment not in RISK_LEVELS:
            errors.append(f"{w}.risk_environment: {s.risk_environment!r} not in {RISK_LEVELS}")
        if s.probability is None and not s.probability_basis.strip():
            errors.append(f"{w}.probability_basis: required when probability is null (写缺口，不编数)")
        if s.probability is not None:
            lo, hi = s.probability
            if not (0 <= lo <= hi <= 100):
                errors.append(f"{w}.probability: need 0 <= min <= max <= 100")
            if not s.probability_basis.strip():
                errors.append(f"{w}.probability_basis: required (调查 / 市场隐含 / 历史频率 / 研究者判断)")
        for lv_i, lv in enumerate(s.levels):
            errors.extend(_level_errors(lv, f"{w}.levels[{lv_i}]"))
        # 正文里的每个带单位数字都要能在线位表或基准表里找到 —— 「研究线位必须带出处与寿命」
        levels = tuple(s.levels) + tuple(p.shared_levels)
        bounds = tuple(b for sc in p.scenarios for b in (sc.lower, sc.upper) if b is not None)
        texts = (s.condition, s.policy_note, s.first_leg.expected, s.first_leg.accepted_if,
                 s.first_leg.rejected_if, s.second_leg.expected, s.second_leg.accepted_if,
                 s.second_leg.rejected_if, s.flip_if, s.portfolio_note, *s.hurt)
        for text in texts:
            for num, unit in _numbers_in(text):
                if not _level_known(num, unit, levels, p.baselines, bounds):
                    errors.append(f"{w}: {num:g}{unit} appears in prose but has no Level/Baseline with source and review_by")
    return errors


# ── 编译到发布 schema ────────────────────────────────────────────────────────

def _impact(channel: str, expected: str, timing: str, rationale: str) -> dict[str, Any]:
    return {"channel": channel, "expected_move": expected, "timing": timing, "rationale": rationale}


def _condition(cid: str, description: str, observable: str, window: str) -> dict[str, Any]:
    return {"id": cid, "description": description, "observable": observable,
            "comparison": "custom", "threshold": description, "unit": None, "window": window}


def to_schema_scenarios(playbook: Playbook) -> list[dict[str, Any]]:
    """Playbook.scenarios → macro-state.schema `scenario[]`。剧本是作者格式，plan 是发布格式。"""
    out = []
    for s in playbook.scenarios:
        hurt = "；".join(s.hurt)
        levels = "；".join(f"{lv.instrument} {lv.value:g}{lv.unit}（{lv.source}，{lv.as_of}，复核 {lv.review_by}）"
                          for lv in (*s.levels, *playbook.shared_levels))
        out.append({
            "id": s.id,
            "label": s.label,
            "condition": s.condition,
            "probability_range": (None if s.probability is None
                                  else {"min_pct": s.probability[0], "max_pct": s.probability[1]}),
            "probability_basis": s.probability_basis or "见剧本",
            "first_order": [_impact("first_leg", s.first_leg.expected, s.first_leg.window,
                                    f"政策回应比较：{'/'.join(s.policy_paths)}。{s.policy_note}")],
            "second_order": [_impact("second_leg", s.second_leg.expected, s.second_leg.window,
                                     f"接受={s.second_leg.accepted_if}；拒绝={s.second_leg.rejected_if}"
                                     + (f"；线位：{levels}" if levels else ""))],
            "third_order": [_impact("participants", hurt, "第二根之后，融资/盈利兑现期",
                                    f"滑向 {s.flip_to}：{s.flip_if}")],
            "confirmation_conditions": [_condition(f"C_{s.id}", s.second_leg.accepted_if,
                                                   s.second_leg.check, s.second_leg.window)],
            "invalidation_conditions": [_condition(f"I_{s.id}", s.second_leg.rejected_if,
                                                   s.second_leg.check, s.second_leg.window)],
            "risk_environment": s.risk_environment,
        })
    return out


def branch_for(playbook: Playbook, actual: float | None) -> str | None:
    """实际值落在哪个数值分支；None = 剩余分支（缺失 / 不可比）。事后复评用，不选「最接近」。"""
    if actual is None or (isinstance(actual, float) and math.isnan(actual)):
        return None
    for s in playbook.scenarios:
        lo_ok = s.lower is None or actual >= s.lower
        hi_ok = s.upper is None or actual < s.upper
        if lo_ok and hi_ok:
            return s.id
    return None


__all__ = [
    "PLAYBOOK_SCHEMA_VERSION", "VERDICT_MAX_CHARS", "TENSION_MAX_CHARS", "RISK_LEVELS", "POLICY_PATHS",
    "Level", "Baseline", "JointCondition", "Leg", "Scenario", "Playbook",
    "from_dict", "to_dict", "validate", "to_schema_scenarios", "branch_for",
]
