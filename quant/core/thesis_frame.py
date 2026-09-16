"""thesis.frame — 基本面判断框架的结构化形状。

写法规格 (怎么写、证据从哪来、什么时候写) = 可选方法 `agents/methods/fundamental-frame.md` (若配置);
本模块只管**形状**与**替换规则**, schema / 写卡 writer / agent_submit 提交边界共用这一个判别式。

为什么存在: 只有一段 `thesis.summary` 散文时, 三个后果:
  · summary 成了杂物间 —— 技术结构注记挤掉基本面论点;
  · 基本面证伪条件混进 `invalid_if` 与价位线并列, 而技术面优先 v3 下 invalid_if 只该装价格结构;
  · Iron Rule #2 v2 与谦逊锚都以「thesis 未证伪」为前提, 卡上却没有一处写着「什么算证伪」;
    财报前不写看点, 财报后的 thesis 更新只能事后讲故事。
骨架: 假设 → 驱动 → 空头与反驳 → 估值交叉验证 → 下一催化剂预案; 评级、入场点、目标价不取。

🚨 零授权效力: 只被 schema / writer / agent_submit import; sizing / entry_guard / exit_guard /
regime_transition 不读。frame 不改 zone / gate / cap / stop。

形状 (文本槽一律非空; 不知道写 FRAME_UNKNOWN「待核实」, 不许省略):
  as_of       本 frame 所引最新基本面证据的日期 YYYY-MM-DD (不是重画日)
  hypothesis  一句话工作假设; `thesis.summary` 由它派生 (单一真源)
  drivers     1–3 条 {name, mechanism, evidence, kpi}
  bear_case   {claim, rebuttal, falsifier}
  valuation   价格要求什么 (反向隐含) + 与驱动是否相容
  catalyst    {event, date (事件日 | 待核实), baseline, watch[1–5]}
  review      可选 {event, date, outcomes[{watch, result}]} = 上一版 catalyst 的逐条对账
  sources     出处, ≥1 条
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

FRAME_UNKNOWN = "待核实"
# writer `apply_notes` 的块分隔: frame 卡的 summary = hypothesis [+ NOTES_SEPARATOR + notes]
NOTES_SEPARATOR = "\n\n[notes] "

FRAME_KEYS = ("as_of", "hypothesis", "drivers", "bear_case", "valuation", "catalyst", "review", "sources")
FRAME_OPTIONAL = ("review",)
DRIVER_KEYS = ("name", "mechanism", "evidence", "kpi")
BEAR_KEYS = ("claim", "rebuttal", "falsifier")
CATALYST_KEYS = ("event", "date", "baseline", "watch")
REVIEW_KEYS = ("event", "date", "outcomes")
OUTCOME_KEYS = ("watch", "result")
MAX_DRIVERS = 3
MAX_WATCH = 5


def _day(value: Any) -> Optional[str]:
    """严格 YYYY-MM-DD → 原样返回; 其他 (含 2026-9-1 这类) → None。"""
    if not isinstance(value, str):
        return None
    try:
        return value if date.fromisoformat(value).isoformat() == value else None
    except ValueError:
        return None


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _obj(value: Any, keys: tuple, path: str, errs: list) -> bool:
    """键集必须恰好是 keys: 多一个 = 夹带 (评级 / 目标价之类), 少一个 = 把「不知道」省略掉了。"""
    if not isinstance(value, dict):
        errs.append(f"{path} must be an object with keys {list(keys)}")
        return False
    extra = sorted(set(value) - set(keys))
    missing = [k for k in keys if k not in value]
    if extra or missing:
        errs.append(f"{path} keys must be exactly {list(keys)} (extra {extra}, missing {missing})")
        return False
    return True


def _texts(value: dict, keys: tuple, path: str, errs: list) -> None:
    errs.extend(f"{path}.{k} must be non-empty text (不知道写「{FRAME_UNKNOWN}」)"
                for k in keys if not _text(value[k]))


def frame_errors(frame: Any, path: str = "thesis.frame") -> list[str]:
    """形状判别式。[] = 合格。"""
    if not isinstance(frame, dict):
        return [f"{path} must be an object"]
    errs: list[str] = []
    extra = sorted(set(frame) - set(FRAME_KEYS))
    missing = [k for k in FRAME_KEYS if k not in frame and k not in FRAME_OPTIONAL]
    if extra:
        errs.append(f"{path} has unknown keys {extra}")
    if missing:
        errs.append(f"{path} is missing {missing}")
    if "as_of" in frame and not _day(frame["as_of"]):
        errs.append(f"{path}.as_of must be YYYY-MM-DD (基本面证据截至日)")
    for key in ("hypothesis", "valuation"):
        if key in frame and not _text(frame[key]):
            errs.append(f"{path}.{key} must be non-empty text (不知道写「{FRAME_UNKNOWN}」)")
    if _text(frame.get("hypothesis")) and "[notes]" in frame["hypothesis"]:
        errs.append(f"{path}.hypothesis must not contain the [notes] marker (summary 由它派生)")
    if "drivers" in frame:
        drivers = frame["drivers"]
        if not isinstance(drivers, list) or not 1 <= len(drivers) <= MAX_DRIVERS:
            errs.append(f"{path}.drivers must list 1–{MAX_DRIVERS} drivers")
        else:
            for i, driver in enumerate(drivers):
                if _obj(driver, DRIVER_KEYS, f"{path}.drivers[{i}]", errs):
                    _texts(driver, DRIVER_KEYS, f"{path}.drivers[{i}]", errs)
    if "bear_case" in frame and _obj(frame["bear_case"], BEAR_KEYS, f"{path}.bear_case", errs):
        _texts(frame["bear_case"], BEAR_KEYS, f"{path}.bear_case", errs)
    if "catalyst" in frame and _obj(frame["catalyst"], CATALYST_KEYS, f"{path}.catalyst", errs):
        cat = frame["catalyst"]
        _texts(cat, ("event", "baseline"), f"{path}.catalyst", errs)
        if cat["date"] != FRAME_UNKNOWN and not _day(cat["date"]):
            errs.append(f"{path}.catalyst.date must be YYYY-MM-DD (事件日) or 「{FRAME_UNKNOWN}」")
        watch = cat["watch"]
        if (not isinstance(watch, list) or not 1 <= len(watch) <= MAX_WATCH
                or not all(_text(w) for w in watch) or len(set(watch)) != len(watch)):
            errs.append(f"{path}.catalyst.watch must list 1–{MAX_WATCH} distinct non-empty items")
    if "review" in frame and _obj(frame["review"], REVIEW_KEYS, f"{path}.review", errs):
        review = frame["review"]
        _texts(review, ("event",), f"{path}.review", errs)
        if not _day(review["date"]):
            errs.append(f"{path}.review.date must be YYYY-MM-DD")
        outcomes = review["outcomes"]
        if not isinstance(outcomes, list) or not outcomes:
            errs.append(f"{path}.review.outcomes must be a non-empty list")
        else:
            for i, outcome in enumerate(outcomes):
                if _obj(outcome, OUTCOME_KEYS, f"{path}.review.outcomes[{i}]", errs):
                    _texts(outcome, OUTCOME_KEYS, f"{path}.review.outcomes[{i}]", errs)
    if "sources" in frame:
        sources = frame["sources"]
        if not isinstance(sources, list) or not sources or not all(_text(s) for s in sources):
            errs.append(f"{path}.sources must be a non-empty list of text (出处)")
    return errs


def summary_base(summary: Any) -> str:
    """summary 去掉 writer [notes] 块后的正文 (frame 卡上它必须等于 hypothesis)。"""
    return str(summary or "").split(NOTES_SEPARATOR)[0].rstrip()


def thesis_errors(thesis: Any, path: str) -> list[str]:
    """卡片 thesis 块。无 frame = 旧散文卡, 不查 (存量卡重画时逐张替换); 有 frame = 形状 + 派生一致。"""
    if not isinstance(thesis, dict) or thesis.get("frame") is None:
        return []
    errs = frame_errors(thesis["frame"], f"{path}.frame")
    if not errs and summary_base(thesis.get("summary")) != thesis["frame"]["hypothesis"].strip():
        errs.append(f"{path}.summary must equal frame.hypothesis (summary 由 frame 派生, 两个真源会分叉)")
    return errs


def transition_errors(prior: Any, new: dict, today: str) -> list[str]:
    """写入一个形状合格的新 frame 时, 相对旧 frame 与今天 (ET 日历日) 的三条:

    1. `as_of` 不晚于今天 (证据截至日不能在未来);
    2. 新 `catalyst.date` 不早于今天 (看点只预注册还没发生的事件);
    3. 上一版 `catalyst.date` ≤ 今天 → 新 frame 必须带 review: event/date 与上一版一致,
       outcomes 逐字覆盖上一版每条 watch。结果拿不到写「待核实 (原因)」, 不许删看点。

    第 3 条与写卡拒擦 stop 同形: **消失比变松更严重**。预注册的看点被新 frame 静默
    换掉 = 事件后讲故事, 而且不会有任何读者发现。不打算对账 → 本次不传 frame (frame 可选,
    价位带照常写)。事件日当天就算到期: 当日盘后之前结果未出, 那就等结果出了再写。
    """
    errs: list[str] = []
    if _day(new.get("as_of")) and new["as_of"] > today:
        errs.append(f"thesis.frame.as_of {new['as_of']} is after today {today} (证据截至日不能在未来)")
    new_cat = new.get("catalyst") if isinstance(new.get("catalyst"), dict) else {}
    if _day(new_cat.get("date")) and new_cat["date"] < today:
        errs.append(f"thesis.frame.catalyst.date {new_cat['date']} is before today {today} "
                    f"(看点只预注册还没发生的事件; 下一事件日未知写「{FRAME_UNKNOWN}」)")
    prior_cat = prior.get("catalyst") if isinstance(prior, dict) else None
    prior_cat = prior_cat if isinstance(prior_cat, dict) else {}
    day = _day(prior_cat.get("date"))
    if not day or day > today:
        return errs
    review = new.get("review") if isinstance(new.get("review"), dict) else {}
    if review.get("event") != prior_cat.get("event") or review.get("date") != day:
        errs.append(f"上一版 catalyst「{prior_cat.get('event')} {day}」已到事件日: 新 frame 须带 review "
                    "(event/date 与上一版一致) 逐条对账看点; 结果未出就先别传 --thesis-frame")
        return errs
    covered = {o.get("watch") for o in review.get("outcomes") or [] if isinstance(o, dict)}
    missing = [w for w in prior_cat.get("watch") or [] if w not in covered]
    if missing:
        errs.append("review 未覆盖上一版看点: " + " / ".join(str(w) for w in missing))
    return errs
