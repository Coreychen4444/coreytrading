"""结构豁免仓: 不受管理 ⇒ 不进任何 task 输出 (用户)。

"完全不用关心"的仓位不该占用注意力 —— 但  之前 role 判据被抄在 5 个
模块 8 处, 于是第 9 处 (portfolio_brief.load_rows_from_state) 漏了, 那张卡照样
天天出现在简报的 position_cards 与行列表里。两条守卫:

  ① 源码棘轮: role 字符串只允许出现在 holdings.py (第 9 处复制 = 红)
  ② 行为: rows 是简报唯一入口, 豁免仓不进 rows ⇒ 下游全段消失
"""
import pathlib

from quant.core.holdings import UNMANAGED_ROLES, is_managed, live_tickers, managed_tickers
from quant.report import portfolio_brief as pb

ROOT = pathlib.Path(__file__).resolve().parents[2]
ROLE = next(iter(UNMANAGED_ROLES))


def _doc():
    return {"tickers": {
        "SPCX": {"instrument": {"type": "stock", "role": ROLE},
                 "broker": {"qty": 1.0, "current_price": 109.11, "change_pct": 9.43,
                            "avg_cost": 160.0}},
        "NBIS": {"instrument": {"type": "stock"},
                 "broker": {"qty": 1.0, "current_price": 201.69, "change_pct": -7.9,
                            "avg_cost": 188.43}},
        "MU": {"instrument": {"type": "stock"}, "broker": {"qty": 0.0}},   # 已平仓
    }}


def test_role_literal_lives_in_exactly_one_module():
    """源码棘轮 (同 today_group 的 DEL token 禁令): 判据不许再被抄走。"""
    offenders = []
    for p in (ROOT / "quant").rglob("*.py"):
        if p.name == "holdings.py":
            continue
        if f'"{ROLE}"' in p.read_text(encoding="utf-8"):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, (f"{ROLE!r} 字面量散落到 {offenders} — "
                           "用 holdings.UNMANAGED_ROLES / is_managed(), 别再抄第 N 份")


def test_is_managed_is_orthogonal_to_live():
    """两个判据别混: live 判'是不是持仓'(qty), managed 判'要不要管/要不要播报'(role)。"""
    doc = _doc()
    assert live_tickers(doc) == {"SPCX", "NBIS"}      # 豁免仓**仍然是持仓**(钱在账上)
    assert managed_tickers(doc) == {"NBIS"}
    assert not is_managed(doc["tickers"]["SPCX"])
    assert is_managed(doc["tickers"]["NBIS"])
    assert is_managed({})                             # 无 instrument = 默认受管


def test_unmanaged_position_never_reaches_the_brief(tmp_path, monkeypatch):
    """rows 是简报唯一入口 —— 挡在这里, position_cards / top_movers / zone 行 /
    budgets / alerts 全段一并消失, 不靠下游各挡各的。"""
    import json
    path = tmp_path / "positions.json"
    path.write_text(json.dumps(_doc()), encoding="utf-8")
    rows, key = pb.load_rows_from_state(path)

    assert key == "代码"
    assert [r["代码"] for r in rows] == ["NBIS"]
    assert not any("SPCX" in json.dumps(r, ensure_ascii=False) for r in rows)
