"""剧本渲染：固定版式顺序、枚举翻成人话、CLI 退出码；零网络、零模型。"""
import json
from pathlib import Path

from quant.core import macro_playbook as mp
from quant.report import macro_playbook_report as rpt
from quant.ops.tg_rich import compile_blocks

EXAMPLE = Path("trade/intelligence/examples/event-playbook.example.json")
CURRENT_EXAMPLE = Path("trade/intelligence/examples/macro-state.example.json")


def playbook():
    return mp.from_dict(json.loads(EXAMPLE.read_text(encoding="utf-8")))


def test_render_keeps_fixed_section_order():
    md = rpt.render(playbook())
    order = ["## 事件剧本｜", "### 矛盾", "### 三条路径", "### 冻结基准", "### 联合条件",
             "### 情景", "### 判别线位", "### 剩余分支", "### 组合含义", "### 仍未知"]
    positions = [md.index(h) for h in order]
    assert positions == sorted(positions)
    # 一句话判断紧跟标题，加粗成一行
    assert md.splitlines()[2].startswith("**") and md.splitlines()[2].endswith("**")
    assert md.rstrip().endswith("宏观方向先验，不是交易授权。")


def test_render_speaks_human_not_schema():
    md = rpt.render(playbook())
    assert "one_and_done" not in md            # 政策路径翻成中文
    assert "一次调整后看数据" in md
    assert "第一根（" in md and "第二根（" in md
    assert "- 接受 = " in md and "- 拒绝 = " in md
    assert "谁受伤 / 被迫调仓：" in md
    assert "| UST10Y | 4.83% |" in md          # 线位表带出处与复核


def test_render_compiles_to_rich_blocks_with_native_tables_and_headings():
    blocks = compile_blocks(rpt.render(playbook()))
    kinds = {b["type"] for b in blocks}
    assert {"heading", "table", "list", "paragraph"} <= kinds


def test_horizons_table_translates_enums():
    current = json.loads(CURRENT_EXAMPLE.read_text(encoding="utf-8"))
    table = rpt.render_horizons(current)
    assert table.startswith("| 期限 | 当前检验 | 方向 | 阶段 | 命题 | 截止 |")
    for internal in ("event_window", "short_1_10d", "medium_1_3m", "clearance_interval", "candidate_phase"):
        assert internal not in table
    assert "事件窗口" in table and "1–10 交易日" in table and "1–3 个月" in table


def test_cli_exit_codes_and_schema_export(tmp_path, capsys):
    out = tmp_path / "scenarios.json"
    assert rpt.main([str(EXAMPLE), "--schema-scenarios", str(out)]) == 0
    assert [s["id"] for s in json.loads(out.read_text())] == ["COOL", "MIDDLE", "HOT"]
    capsys.readouterr()

    broken = tmp_path / "broken.json"
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["scenarios"][0]["flip_to"] = "NOWHERE"
    broken.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert rpt.main([str(broken), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and any("flip_to" in e for e in result["errors"])
    assert result["network_send"] is False and result["authority"] == "macro_prior_only"

    garbage = tmp_path / "garbage.json"
    garbage.write_text('{"schema_version": "1.0.0"}', encoding="utf-8")
    assert rpt.main([str(garbage)]) == 1
