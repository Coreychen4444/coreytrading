#!/usr/bin/env python3
"""情报层动态投资范围 —— 外部研究 / 情报 agent 每轮先跑这一条, 范围以输出为准.

判别式住在 `quant/core/holdings.intel_universe` (纯函数), 本模块只读两个 state 文件 + 打印:

  active     positions.json 里 broker.qty≠0 的行, 期权映射回正股 (linked_underlying 优先,
             否则 OCC 解析); 结构豁免层 ("不进任何 task 输出") 与参考票不进
  watchlist  watchlist.json 里 `is_candidate` 的卡 = status=watching **且** moomoo_listed≠false
             (membership 真源 = 券商侧候选分组: 移出只翻 moomoo_listed, 不删卡、不改 status);
             已在 active 的不重复
  excluded   被排除的 ticker → 原因 (两桶分开), 供审计; **不进报告**
  line       报告"覆盖窗口"行里"当前 active / watchlist"那一段, 原样粘贴
  as_of      持仓行情快照时间 (最新一条 broker.as_of), 让读报告的人知道范围有多新

为什么要有它: 外部 agent 此前按自己的提示词解读这两个 state 文件 —— "positions 只取
active 持仓" "watchlist 取 watching 标的" —— 两条读的都是 `status`, 而 `status` 在本仓是
手工字段 (holdings.py 顶部: 两个方向都出过事)。于是结构豁免仓、期权已平的残卡与已移出
候选分组的墓碑一起被当成覆盖范围, 而 `grep moomoo_listed quant/` 永远看不见提示词里的
那一份判别式。修法 = 判别式进代码 + 消费者只抄输出, 提示词层不再写字段过滤条件。

零交易语义: 只读, 不写任何文件, 不 import sizing/entry_guard/exit_guard; 输出里没有价格、
仓位、授权。系统 python3 即可跑 (外部 runner 的 shell 里未必有本项目的 venv)。

Usage:
    python3 quant/ops/intelligence_universe.py            # 一行, 直接贴进报告"覆盖窗口"行
    python3 quant/ops/intelligence_universe.py --json     # {"active", "watchlist", "excluded", "line", "as_of", ...}
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 直接 `python3 quant/<层>/<模块>.py` 时让 `quant.*` 可 import

import argparse
import json

from quant.core import paths
from quant.core.holdings import intel_universe
from quant.core.io_json import read_json


def render_line(universe: dict) -> str:
    """报告"覆盖窗口"行里"当前 active / watchlist"那一段。"""
    active = "、".join(universe["active"]) if universe["active"] else "无"
    wl = "、".join(universe["watchlist"]) if universe["watchlist"] else "无"
    return f"当前 active：{active}（期权映射回正股并去重）；watchlist：{wl}"


def _as_of(positions: dict):
    """最新一条在持行的 broker.as_of —— 范围有多新 (ISO 字符串可直接比大小)。"""
    stamps = [str((b.get("broker") or {}).get("as_of") or "")
              for b in (positions.get("tickers") or {}).values()
              if float((b.get("broker") or {}).get("qty") or 0)]
    return max(stamps, default="") or None


def _rel(p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(paths.ROOT))
    except ValueError:
        return str(p)


def build(positions_path: Path, watchlist_path: Path) -> dict:
    positions = read_json(positions_path)
    watchlist = read_json(watchlist_path)
    out = intel_universe(positions, watchlist)
    out["line"] = render_line(out)
    out["as_of"] = _as_of(positions)
    out["source"] = {"positions": _rel(positions_path), "watchlist": _rel(watchlist_path)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--positions", type=Path, default=paths.POSITIONS_PATH)
    ap.add_argument("--watchlist", type=Path, default=paths.WATCHLIST_PATH)
    ap.add_argument("--json", action="store_true", help="完整 JSON (含 excluded 与原因)")
    args = ap.parse_args(argv)
    out = build(args.positions, args.watchlist)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(out["line"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
