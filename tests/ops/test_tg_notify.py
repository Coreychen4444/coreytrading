"""tg_notify 的**拆条**逻辑 —— 唯一有分支、且失败方式是静默的那一段。

超 4096 的消息 Telegram 直接 400 拒收：不是截断，是**一个字都不到**。
 收盘判读改成"全部内容进一条总结"之后，这条限制从"详情才会碰到"
变成"票一多就会碰到"，所以拆条必须是代码，不是 prose 指令。
"""
import pytest

from quant.core import motto
from quant.ops import tg_notify as tg

FOOTER = "synthetic reminder line"


@pytest.fixture
def footer(monkeypatch):
    """Configured closing line; the default (unset) is exercised separately."""
    monkeypatch.setattr(motto, "MOTTO", FOOTER)
    monkeypatch.setattr(motto, "MOTTO_LINE", motto.PREFIX + FOOTER)
    return motto.MOTTO_LINE


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


def test_markdown_subset_to_html_keeps_identifiers_literal():
    rendered = tg.markdown_subset_to_html(
        "*阶段* mixed_transition / termination_warning\n"
        "[来源](https://example.com/a_(b)?x=1&y=2)"
    )
    assert rendered == (
        "<b>阶段</b> mixed_transition / termination_warning\n"
        '<a href="https://example.com/a_(b)?x=1&amp;y=2">来源</a>'
    )


def test_markdown_subset_to_html_escapes_untrusted_html():
    assert tg.markdown_subset_to_html("A < B & C > D") == "A &lt; B &amp; C &gt; D"


def test_load_bot_token_prefers_the_primary_config_path(tmp_path, monkeypatch):
    neutral = tmp_path / "neutral.env"
    legacy = tmp_path / "legacy.env"
    neutral.write_text("TELEGRAM_BOT_TOKEN=neutral-token\n", encoding="utf-8")
    legacy.write_text("TELEGRAM_BOT_TOKEN=legacy-token\n", encoding="utf-8")
    monkeypatch.setattr(tg, "TOKEN_ENV_PATHS", (str(neutral), str(legacy)))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "environment-token")
    assert tg.load_bot_token() == "neutral-token"


def test_load_bot_token_falls_back_to_environment(tmp_path, monkeypatch):
    missing = tmp_path / "missing.env"
    monkeypatch.setattr(tg, "TOKEN_ENV_PATHS", (str(missing),))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "environment-token")
    assert tg.load_bot_token() == "environment-token"


# ── rich 通道 (, Bot API 10.1 sendRichMessage) ──────────────────

class _RichSpy:
    def __init__(self, fail_at=None):
        self.sent, self.fail_at = [], fail_at

    def __call__(self, blocks):
        if self.fail_at is not None and len(self.sent) + 1 == self.fail_at:
            raise RuntimeError("Telegram API error 400 (no retry): Bad Request: bad block")
        self.sent.append(blocks)
        return {"ok": True}


class _LegacySpy:
    def __init__(self):
        self.sent = []

    def __call__(self, text, parse_mode="Markdown", retries=3):
        self.sent.append((text, parse_mode))
        return {"ok": True}


def test_rich_parts_compile_split_and_footer_motto(footer):
    parts = tg.rich_parts("# 报告\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert len(parts) == 1
    assert [b["type"] for b in parts[0]] == ["heading", "table", "footer"]
    assert parts[0][-1]["text"] == footer
    assert [b["type"] for b in tg.rich_parts("x", motto=False)[0]] == ["paragraph"]


def test_send_rich_uses_rich_channel_and_reports_mode():
    rich, legacy = _RichSpy(), _LegacySpy()
    res = tg.send_rich("# 报告\n正文", send_part=rich, legacy_send=legacy, sleep_fn=lambda s: None)
    assert res == {"mode": "rich", "parts": 1} and len(rich.sent) == 1 and not legacy.sent


def test_send_rich_rejected_part_falls_back_to_legacy_for_the_remainder(monkeypatch, footer):
    """第 2 条被 400 → 第 1 条已在手机上, 剩余内容走老路 (子集→HTML), 尾行仍只有一份。"""
    monkeypatch.setattr(tg.tg_rich, "RICH_CHAR_BUDGET", 60)
    text = "# 一\n" + "a" * 50 + "\n\n# 二\n" + "b" * 50 + " *粗* [l](https://x.y)"
    rich, legacy = _RichSpy(fail_at=2), _LegacySpy()
    res = tg.send_rich(text, send_part=rich, legacy_send=legacy, sleep_fn=lambda s: None)
    assert res["mode"] == "legacy_fallback" and res["rich_parts"] == 1 and res["fallback_from"] == 2
    assert len(rich.sent) == 1 and "a" * 50 in tg.tg_rich.blocks_plain(rich.sent[0])
    html = "\n".join(t for t, _ in legacy.sent)
    assert "<b>二</b>" in html and "b" * 50 in html and "<b>粗</b>" in html and 'href="https://x.y"' in html
    assert html.count(footer) == 1 and "a" * 50 not in html
    assert all(mode == "HTML" for _, mode in legacy.sent)


def test_cli_default_is_rich_dry_run_prints_blocks_and_never_sends(monkeypatch, capsys, footer):
    """不带任何旗 = rich; --rich 只是兼容旗。"""
    monkeypatch.setattr(tg, "send_rich_message", lambda blocks: (_ for _ in ()).throw(AssertionError("sent!")))
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent!")))
    for argv in (["--dry-run", "# 标题\n| a |\n|---|\n| 1 |"],
                 ["--rich", "--dry-run", "# 标题\n| a |\n|---|\n| 1 |"]):
        tg.main(argv)
        parts = __import__("json").loads(capsys.readouterr().out)
        assert [b["type"] for b in parts[0]] == ["heading", "table", "footer"]


def test_cli_legacy_flag_is_the_only_way_back_to_send_message(monkeypatch, capsys, footer):
    monkeypatch.setattr(tg, "send_rich_message", lambda blocks: (_ for _ in ()).throw(AssertionError("sent!")))
    monkeypatch.setattr(tg, "send_message", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sent!")))
    tg.main(["--legacy", "--plain", "--dry-run", "第一段\n\n第二段 *x*"])
    out = capsys.readouterr().out
    assert "<<<PART>>>" not in out and "*x*" in out and footer in out   # 一条, 原样文本 + 座右铭尾行
    with pytest.raises(SystemExit):
        tg.main(["--rich", "--legacy", "--dry-run", "x"])                    # 互斥


def test_no_footer_configured_means_nothing_is_appended(monkeypatch):
    monkeypatch.setattr(motto, "MOTTO", "")
    monkeypatch.setattr(motto, "MOTTO_LINE", "")
    assert [b["type"] for b in tg.rich_parts("x")[0]] == ["paragraph"]
    assert tg.append_motto(["a", "b"]) == ["a", "b"] and tg.append_motto([]) == []
