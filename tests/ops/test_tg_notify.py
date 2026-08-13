"""tg_notify 的**拆条**逻辑 —— 唯一有分支、且失败方式是静默的那一段。

超 4096 的消息 Telegram 直接 400 拒收：不是截断，是**一个字都不到**。
 收盘判读改成"全部内容进一条总结"之后，这条限制从"详情才会碰到"
变成"票一多就会碰到"，所以拆条必须是代码，不是 prose 指令。
"""
from quant.ops import tg_notify as tg


def test_short_message_is_not_split():
    assert tg.split_message("hello") == ["hello"]


def test_splits_on_blank_lines_and_every_part_fits():
    blocks = [f"━━ T{i} ━━\n" + "字" * 500 for i in range(20)]
    parts = tg.split_message("\n\n".join(blocks))
    assert len(parts) > 1
    assert all(tg._tg_len(p) <= tg.TG_LIMIT for p in parts)
    # 边界落在空行处 = 每条都从一只票的块头开始，没有半只票
    assert all(p.startswith("━━") for p in parts)


def test_no_character_is_lost():
    text = "\n\n".join(f"块{i}\n" + "x" * 3000 for i in range(4))
    assert "".join(tg.split_message(text)).replace("\n", "") == text.replace("\n", "")


def test_a_single_oversized_line_is_hard_cut_not_dropped():
    """段落/行都断不开时只能硬切 —— 但**不许丢字**（丢字比拆条更难发现）。"""
    line = "y" * (tg.TG_LIMIT * 2 + 7)
    parts = tg.split_message(line)
    assert all(tg._tg_len(p) <= tg.TG_LIMIT for p in parts)
    assert "".join(parts) == line


def test_length_is_counted_in_utf16_units_not_characters():
    """emoji 在 Telegram 眼里占两格。按 len() 算会低估 → 刚好卡在边界上的消息被拒。"""
    assert tg._tg_len("🚨") == 2 and len("🚨") == 1
    parts = tg.split_message("🚨" * (tg.TG_LIMIT // 2 + 5))
    assert all(tg._tg_len(p) <= tg.TG_LIMIT for p in parts)
