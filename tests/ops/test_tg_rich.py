"""tg_rich —— 报告文本 → Telegram Rich Message blocks 的编译器（Bot API 10.1）。

它替代的是「把 Markdown 交给 Telegram 解析」：报告里 `$1,137 vs $233B` 会被 Rich Markdown 当
行内公式、`mixed_transition` 的 `_` 会被当斜体、legacy 子集 `*粗体*` 在那边是斜体。所以钉死：

  1. 认得的语法才变结构，其余逐字：`$`/`_`/单个 `*` 永远字面量
  2. 表格：GFM 管道表 → 表头 + 对齐 + `\\|` 转义 + 参差补齐；>20 列回落 pre 不丢字
  3. 官方上限（32,768 字符 / 500 块含表格行与列表项）由 split_blocks 代码级兜住，
     单块超限也能切（表格重复表头），标题不孤零零收尾
  4. 可选尾行 = footer 块，落最后一条 / 装不下单独一条 / 正文已有不重复（与 tg_notify.append_motto 同合同）
  5. 类型为 "True" 的字段（is_header / is_checked / is_open / is_bordered…）只在真时出现，
     永不传 False —— Telegram 会拒
  6. blocks_to_text 是 rich 被拒时的回落：粗体/链接保留为 legacy 子集，一个字不丢
"""
import json

from quant.core import motto
from quant.ops import tg_rich as R


def _types(blocks):
    return [b["type"] for b in blocks]


def _plain(rt):
    return R.rich_text_plain(rt)


# ── 1. 行内：认得的才解析 ─────────────────────────────────────────────

def test_inline_bold_both_syntaxes_code_and_link_with_parens():
    rt = R.parse_inline("a **粗** b *旧粗* c `x_y` d [源](https://e.com/a_(b)?x=1&y=2) e")
    kinds = [x if isinstance(x, str) else (x["type"], _plain(x)) for x in rt]
    assert kinds == ["a ", ("bold", "粗"), " b ", ("bold", "旧粗"), " c ", ("code", "x_y"),
                     " d ", ("url", "源"), " e"]
    assert rt[7]["url"] == "https://e.com/a_(b)?x=1&y=2"


def test_inline_dollar_underscore_and_lone_star_are_literal():
    s = "MU $233B/$144 vs TTM $90B/$45 · mixed_transition · z2_breach_buy_freeze · 5*3 · a * b"
    assert R.parse_inline(s) == s            # 原样一个字不动 —— 没有任何 entity


def test_inline_bold_never_crosses_lines_and_needs_non_space_edges():
    assert R.parse_inline("*a\nb*") == "*a\nb*"
    assert R.parse_inline("* 不是粗体 *") == "* 不是粗体 *"


# ── 块级 ──────────────────────────────────────────────────────────────

def test_headings_offset_and_clamp():
    b = R.compile_blocks("# 一\n## 二\n###### 六")
    assert [(x["type"], x["size"]) for x in b] == [("heading", 2), ("heading", 3), ("heading", 6)]
    assert _plain(b[0]["text"]) == "一"


def test_paragraph_keeps_inner_newlines_and_blank_line_splits():
    b = R.compile_blocks("  ▫️ 133 收复线 [0/2]\n  ⏳ 145 修复线 [1/2]\n\n下一段")
    assert _types(b) == ["paragraph", "paragraph"]
    assert _plain(b[0]["text"]) == "  ▫️ 133 收复线 [0/2]\n  ⏳ 145 修复线 [1/2]"


def test_table_header_alignment_escape_and_ragged_rows():
    b = R.compile_blocks("| 票 | 判定 | 说明 |\n|:--|:-:|--:|\n| AAOI | PLAN | z1 \\| 首档 |\n| MU |")
    t = b[0]
    assert t["type"] == "table" and t["is_bordered"] is True and t["is_compact"] is True
    hdr, r1, r2 = t["cells"]
    assert all(c["is_header"] is True for c in hdr)
    assert [c["align"] for c in hdr] == ["left", "center", "right"]
    assert _plain(r1[2]["text"]) == "z1 | 首档"
    assert len(r2) == 3 and _plain(r2[1]["text"]) == ""          # 参差行补齐
    assert "is_header" not in r1[0]                               # 只在真时出现


def test_table_without_alignment_row_has_no_header():
    t = R.compile_blocks("| a | b |\n| c | d |")[0]
    assert t["type"] == "table" and len(t["cells"]) == 2
    assert not any("is_header" in c for row in t["cells"] for c in row)


def test_table_over_20_columns_falls_back_to_pre_without_losing_text():
    row = "|" + "|".join(f" c{i} " for i in range(21)) + "|"
    sep = "|" + "---|" * 21
    b = R.compile_blocks(f"{row}\n{sep}\n{row}")
    assert _types(b) == ["pre"] and "c20" in b[0]["text"]


def test_lists_nested_ordered_and_tasks():
    """Telegram: list must be either ordered or unordered（ 首发实测 400）——
    同层有序/无序切换 = 另起一个 list 块；嵌套挂在上一项里。"""
    b = R.compile_blocks("- 一\n- 二\n  - 二点一\n1. 甲\n2. 乙\n- [ ] 待办\n- [x] 已办")
    assert _types(b) == ["list", "list", "list"]
    u, o, t = (x["items"] for x in b)
    assert _plain(u[1]["blocks"][0]["text"]) == "二"
    assert u[1]["blocks"][1]["type"] == "list"                    # 嵌套
    assert all("type" not in it for it in u)
    assert [(it["type"], it["value"]) for it in o] == [("1", 1), ("1", 2)]
    assert t[0]["has_checkbox"] is True and "is_checked" not in t[0]
    assert t[1]["is_checked"] is True


def test_every_list_block_is_uniformly_ordered_or_unordered():
    text = "1. a\n- b\n2. c\n  - d\n  1. e\n- f"
    for blk in R.compile_blocks(text):
        stack = [blk]
        while stack:
            x = stack.pop()
            if x.get("type") == "list":
                flags = {"type" in it for it in x["items"]}
                assert len(flags) == 1, x
                for it in x["items"]:
                    stack.extend(it["blocks"])


def test_negative_number_at_line_start_is_not_a_list():
    b = R.compile_blocks("-$527 现金\n-3.2% 回撤")
    assert _types(b) == ["paragraph"]


def test_blockquote_details_divider_and_fence():
    text = ("> 引用1\n> 引用2\n\n<details open><summary>AAOI 详情</summary>\n里面 **粗**\n"
            "| a | b |\n|---|---|\n| 1 | 2 |\n</details>\n\n---\n━━━━━━━━\n```python\nx = 1\n```")
    b = R.compile_blocks(text)
    assert _types(b) == ["blockquote", "details", "divider", "divider", "pre"]
    assert _plain(b[0]["blocks"][0]["text"]) == "引用1\n引用2"
    d = b[1]
    assert d["is_open"] is True and _plain(d["summary"]) == "AAOI 详情"
    assert _types(d["blocks"]) == ["paragraph", "table"]
    assert b[4]["language"] == "python" and b[4]["text"] == "x = 1"


def test_details_summary_on_next_line_and_nested_details():
    text = ("<details>\n<summary>外</summary>\n外文\n<details><summary>内</summary>\n内文\n</details>\n尾\n</details>")
    b = R.compile_blocks(text)
    assert _types(b) == ["details"] and "is_open" not in b[0]
    inner = b[0]["blocks"]
    assert _types(inner) == ["paragraph", "details", "paragraph"]
    assert _plain(inner[1]["summary"]) == "内" and _plain(inner[1]["blocks"][0]["text"]) == "内文"


def test_true_typed_flags_are_never_false():
    """Telegram 的 "True" 型字段传 False 会被拒 —— 编译产物里任何 is_* / has_* 都不许是 False。"""
    text = ("# h\n| a |\n|---|\n| 1 |\n- [ ] x\n- [x] y\n<details><summary>s</summary>t</details>\n"
            "<details open><summary>s</summary>t</details>")
    dumped = json.dumps(R.compile_blocks(text))
    assert ": false" not in dumped


# ── 3. 上限与拆条 ─────────────────────────────────────────────────────

def test_count_blocks_follows_official_rule_rows_and_items_count():
    b = R.compile_blocks("| a |\n|---|\n| 1 |\n| 2 |\n\n- x\n- y\n\n> q")
    # table(1)+3 rows, list(1)+2 items+2 paragraphs, blockquote(1)+1 paragraph
    assert R.count_blocks(b) == 4 + 5 + 2


def test_split_keeps_heading_with_its_body():
    b = R.compile_blocks("# H1\npara1\n# H2\npara2\n# H3\npara3")
    parts = R.split_blocks(b, max_chars=12, max_blocks=100)
    assert [[_plain(x.get("text", "")) for x in p] for p in parts] == \
        [["H1", "para1"], ["H2", "para2"], ["H3", "para3"]]


def test_split_by_block_count_and_oversized_table_repeats_header():
    rows = "\n".join(f"| r{i} | v{i} |" for i in range(30))
    b = R.compile_blocks(f"| k | v |\n|---|---|\n{rows}")
    parts = R.split_blocks(b, max_chars=10_000, max_blocks=12)
    assert len(parts) >= 3
    for p in parts:
        assert R.count_blocks(p) <= 12
        assert p[0]["type"] == "table" and p[0]["cells"][0][0]["is_header"] is True
    got = [_plain(row[0]["text"]) for p in parts for row in p[0]["cells"][1:]]
    assert got == [f"r{i}" for i in range(30)]                    # 行一条不丢、顺序不变


def test_split_oversized_paragraph_by_lines_and_no_character_lost():
    text = "\n".join("行" * 50 + str(i) for i in range(40))
    parts = R.split_blocks(R.compile_blocks(text), max_chars=600, max_blocks=100)
    assert len(parts) > 1
    joined = "\n".join(_plain(b["text"]) for p in parts for b in p)
    assert joined.replace("\n", "") == text.replace("\n", "")


def test_default_budgets_sit_under_official_limits():
    assert R.RICH_CHAR_BUDGET < R.RICH_CHAR_LIMIT == 32768
    assert R.RICH_BLOCK_BUDGET < R.RICH_BLOCK_LIMIT == 500
    assert R.RICH_TABLE_COLS == 20


# ── 4. 可选尾行 ─────────────────────────────────────────────────────────

def test_motto_footer_lands_on_last_part_or_its_own_part(monkeypatch):
    monkeypatch.setattr(motto, "MOTTO", "synthetic reminder line")
    monkeypatch.setattr(motto, "MOTTO_LINE", motto.PREFIX + "synthetic reminder line")
    MOTTO, MOTTO_LINE = motto.MOTTO, motto.MOTTO_LINE
    parts = R.split_blocks(R.compile_blocks("a\n\nb"))
    out = R.append_motto(parts)
    assert out[-1][-1] == {"type": "footer", "text": MOTTO_LINE}
    assert R.append_motto(out) == out                              # 幂等
    assert R.append_motto([[R.paragraph("尾 " + MOTTO)]])[0][-1]["type"] == "paragraph"
    full = [[R.paragraph("x" * 100)]]
    assert R.append_motto(full, max_chars=105)[-1] == [{"type": "footer", "text": MOTTO_LINE}]
    assert R.append_motto([[]]) == [[{"type": "footer", "text": MOTTO_LINE}]]


def test_no_footer_when_unconfigured(monkeypatch):
    monkeypatch.setattr(motto, "MOTTO", "")
    monkeypatch.setattr(motto, "MOTTO_LINE", "")
    parts = R.split_blocks(R.compile_blocks("a\n\nb"))
    assert R.append_motto(parts) == parts and R.append_motto([[]]) == [[]]
    assert not R.has_motto(parts)


# ── 6. 回落文本 ───────────────────────────────────────────────────────

def test_blocks_to_text_keeps_every_word_in_legacy_subset():
    text = ("# 标题\n段 **粗** [源](https://x.y) mixed_transition\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
            "- x\n1. y\n\n<details><summary>S</summary>\n体\n</details>\n\n---")
    out = R.blocks_to_text(R.compile_blocks(text))
    assert "*标题*" in out and "*粗*" in out and "[源](https://x.y)" in out
    assert "mixed_transition" in out and "a | b" in out and "1 | 2" in out
    assert "• x" in out and "1. y" in out and "▸ *S*" in out and "体" in out and "———" in out


def test_payload_disables_entity_autodetection_and_is_a_copy():
    blocks = R.compile_blocks("看 trade/intelligence/reports 与 2@98.7")
    pl = R.to_payload(blocks)
    assert pl["skip_entity_detection"] is True and pl["blocks"] == blocks and pl["blocks"] is not blocks
