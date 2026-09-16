"""转移达标条件渲染 — 合成收据, 不读 live 状态、不触网、不调模型。"""
import json

from quant.report import transition_report as tp


def _vol(**kw):
    base = {"ok": True, "note": "量比(rel SMH 0.51) 1.68", "required": 1.1, "actual": 1.68,
            "mode": "relative", "benchmark": "SMH", "benchmark_ratio": 0.51,
            "own_ratio": 0.855, "aligned_date": "2026-09-09", "aligned_streak_day": 3,
            "streak": 3, "degraded": False}
    return {**base, **kw}


def _up(**kw):
    tr = {"ticker": "SYN", "direction": "up", "level": 100.0, "days": 2,
          "closes": [101.25, 104.75], "date": "2026-09-09", "to": "R_突破_100",
          "fastlane": False, "reason": "散文",
          "confirm": {"mode": "close_x2", "days": 2, "level": 100.0, "streak": 3,
                      "volume_required": True,
                      "closes": [{"date": "2026-09-08", "close": 101.25},
                                 {"date": "2026-09-09", "close": 104.75}]},
          "volume": _vol()}
    tr.update(kw)
    return tr


def _build(*transitions, **data):
    return tp.build({"date": "2026-09-09", "transitions": list(transitions), **data})


def test_up_transition_states_the_confirmation_and_the_volume_leg():
    """确认口径 (×2) 与量腿类型 —— 两问都必须明确答出。"""
    out = _build(_up())
    assert "收盘 ×2 日 (非快车道)" in out and "连续站上 3 日" in out
    assert "09-08 $101.25 · 09-09 $104.75" in out
    assert "RelVR 1.68 ≥ 阈值 1.1 ✓" in out
    assert "相对口径 · 基准 SMH 当日量比 0.51" in out and "个股自身量比 0.85" in out
    assert "对齐日 09-09（streak 第 3 日）" in out


def test_volume_leg_aligned_on_an_earlier_streak_day_names_that_day():
    """量腿对齐日 = streak 内任一日 —— 哪一日过必须写出来。"""
    out = _build(_up(volume=_vol(actual=1.32, aligned_date="2026-09-08", aligned_streak_day=1)))
    assert "对齐日 09-08（streak 第 1 日）" in out


def test_fastlane_shows_the_amplitude_substitute_and_its_domain():
    out = _build(_up(fastlane=True, confirm={
        "mode": "fastlane", "days": 1, "level": 628.3, "over_n": 1.18, "threshold_n": 0.5,
        "domain": "域=①修复(1次破位在120日窗内)", "volume_required": True,
        "closes": [{"date": "2026-09-09", "close": 653.69}]}))
    assert "⚡快车道 — 首日收盘超 gate +0.5N 替代第二日确认 (实际超出 1.18N)" in out
    assert "域=①修复(1次破位在120日窗内)" in out and "  ⚡" in out


def test_stop_break_is_an_exit_receipt_without_a_volume_leg():
    out = _build({"ticker": "TST", "direction": "down", "level": 100.34, "days": 1,
                  "date": "2026-09-09", "reason": "散文",
                  "confirm": {"mode": "stop_break", "days": 1, "level": 100.34,
                              "volume_required": False,
                              "closes": [{"date": "2026-09-09", "close": 99.67}]}})
    assert "⬇ 100.34 破 stop" in out and "退出侧, 量腿不适用" in out
    assert "不适用（退出侧）" in out and "RelVR" not in out


def test_absolute_fallback_is_never_silent():
    """基准缺当日 → 绝对量比兜底; 降级必须说出口 (volume_leg docstring 的硬约束)。"""
    out = _build(_up(volume=_vol(mode="absolute_fallback", benchmark_ratio=None,
                                note="量比(abs兜底,SMH缺2026-09-09) 1.68")))
    assert "**绝对量比兜底**" in out and "降级不静默" in out


def test_card_without_a_volume_threshold_says_the_leg_was_skipped():
    out = _build(_up(volume=_vol(mode="skipped", required=None, actual=None, ok=True)))
    assert "卡片未设 `volume_ratio_min` — 整条量腿跳过" in out


def test_legacy_prose_receipt_is_labelled_not_dressed_up():
    """结构化字段上线前的收据只有散文 —— 原样播报并标明, 不假装有字段。"""
    out = _build({"ticker": "ORCL", "direction": "up", "level": 159.8, "date": "2026-09-09",
                  "to": "R_收复_159.8", "reason": "收盘 161.63 > gate 159.8 连续 2 日; 量比(rel QQQ 0.87) 1.31"})
    assert "旧散文收据，无结构化字段" in out and "量比(rel QQQ 0.87) 1.31" in out
    assert "确认口径:" not in out


def test_zero_transition_day_still_emits_a_dead_man_line():
    assert "盘后：无 regime 转移。" in _build()


def test_pending_repaint_is_listed_with_its_authorization_meaning():
    out = _build(_up(), pending_repaint=[{"ticker": "META"}, "LITE"])
    assert "⏳ provisional 待补画（补画前不授权入场）: LITE, META" in out


def test_applied_count_mismatch_is_flagged_rather_than_smoothed_over():
    out = _build(_up(), _up(ticker="LITE"), applied=[_up()])
    assert "检测到 2 笔, 实际应用 1 笔" in out


def test_renderer_invents_no_numbers_when_fields_are_missing():
    out = _build({"ticker": "T", "direction": "up", "reason": "",
                  "confirm": {"mode": "close_x2", "days": 2, "closes": []}})
    assert "达标收盘: —" in out and "量腿: —" in out


def test_accepts_a_full_pipeline_document_or_the_regime_stage_alone():
    regime = {"date": "2026-09-09", "transitions": [_up()]}
    doc = {"stages": {"regime": {"data": regime}}}
    assert tp.regime_data(doc) == regime and tp.regime_data(regime) == regime
    assert tp.regime_data({"stages": {}}) == {}


def test_cli_reads_stdin_and_can_emit_json(capsys, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"date": "2026-09-09",
                                                            "transitions": [_up()]})))
    assert tp.main(["--json"]) == 0
    assert "RelVR 1.68" in json.loads(capsys.readouterr().out)["text"]


# ── 阶段失败 ≠ 零转移 ─────────────────────────────────────────────
#
# 失败形状: 盘后 regime 阶段 rc=1 (一张卡的空 cap 拒了整本 watchlist 的写入)。
# 阶段失败时 `stages.regime` 里根本没有 `data`, 若 `regime_data` 静默退化成 {},
# dead-man 那行照常印出「盘后：无 regime 转移」—— 而真相可能是多张卡
# 达标、一笔未落卡。**最该喊的那天最安静**, 这一条把它钉死。

def _failed_stage(**kw):
    stage = {"key": "regime", "ok": False, "rc": 1, "stdout": "",
             "stderr": "RuntimeError: watchlist.json failed schema validation", **kw}
    return {"stages": {"regime": stage}}


def test_a_failed_stage_is_never_rendered_as_a_zero_transition_day():
    out = tp.build(tp.regime_data(_failed_stage()))
    assert "regime 阶段本轮失败 (rc=1)" in out
    assert "盘后：无 regime 转移。" not in out
    assert "不代表市场没达标" in out
    assert "failed schema validation" in out


def test_a_genuine_zero_transition_day_keeps_the_dead_man_line():
    ok = {"stages": {"regime": {"ok": True, "rc": 0,
                                "data": {"date": "2026-09-09", "transitions": []}}}}
    out = tp.build(tp.regime_data(ok))
    assert "盘后：无 regime 转移。" in out and "阶段本轮失败" not in out


def test_rejected_cards_are_named_with_what_they_had_qualified_on():
    out = _build(rejected=[{"ticker": "SYN", "direction": "up", "level": 219.35,
                            "reason": "收盘 242.49 > gate 219.35 连续 2 日; 量比 1.68",
                            "error": "next_regime.cap_pct_nav 不是数字 (None)"}])
    assert "**SYN** 达标但**未落卡**" in out
    assert "cap_pct_nav 不是数字" in out
    assert "收盘 242.49 > gate 219.35" in out
    assert "cap / zones 维持原样" in out


def test_preexisting_card_errors_are_listed_as_what_to_fix_first():
    out = _build(_up(), preexisting_card_errors=[
        "watchlist[SYNB].agent.next_regime.cap_pct_nav must be numeric in [0, 0.4]"])
    assert "挡住本轮落库的**既存**卡片错误" in out
    assert "watchlist[SYNB]" in out
