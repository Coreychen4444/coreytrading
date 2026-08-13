"""形态判读的不变量。

这个仓最熟的失败形状是「规则不进代码 = 不存在」，而形态判读还有一个**独有**的
失败形状：**没有出处的规则进了代码 = 更糟，因为它看起来有依据**。
所以这里钉的第一件事不是算得对不对，是**每条判读能不能追到它的出处**。
"""
import math

import pytest

from quant.core import patterns as P


# ── 合成序列工具 ────────────────────────────────────────────────

def _bars(closes, start="2026-01-01"):
    """把收盘序列变成 bar：high/low 各留 0.5% 影线，日期只要能排序。"""
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    out = []
    for i, c in enumerate(closes):
        out.append({
            "time": f"{(d0 + dt.timedelta(days=i)).isoformat()} 00:00:00",
            "open": c, "high": c * 1.005, "low": c * 0.995, "close": c,
            "volume": 1_000_000, "turnover": c * 1_000_000,
        })
    return out


def _zigzag(points, per_leg=8):
    """在给定的转折价之间线性插值，生成一条有明确摆动结构的序列。"""
    out = [points[0]]
    for a, b in zip(points, points[1:]):
        for k in range(1, per_leg + 1):
            out.append(a + (b - a) * k / per_leg)
    return out


# ══════════════════════════════════════════════════════════════
# 1. 出处纪律 —— 这一组是本文件存在的主要理由
# ══════════════════════════════════════════════════════════════

LO_TEN = {"HS", "IHS", "BTOP", "BBOT", "TTOP", "TBOT", "RTOP", "RBOT", "DTOP", "DBOT"}


def test_lo_family_is_exactly_the_published_ten():
    """Lo 族**由外部封死**—— 想加第 11 个形态就得改这个断言，改的时候会看见这条注释：
    族大小是对数据窥探的唯一诚实回应（7,846 条规则在 FDR 后无一存活）。"""
    assert set(P.LO_FAMILY) == LO_TEN


def test_every_lo_pattern_carries_published_p_value():
    """每个 Lo 族形态必须带已发表的 KS p 值 —— 「这像双顶」要能连着
    「这类形状历史上有多强」一起说出口。"""
    for name in P.LO_FAMILY:
        p = P.PATTERN_SPECS[name]["ks_p"]
        assert isinstance(p, float) and 0.0 <= p <= 1.0, name


def test_folk_patterns_never_borrow_lo_evidence():
    """族外形状（楔形）**不许**冒充有出处。这是本模块最严重的违规形式：
    把民间说法混进 Lo 族，读者会以为它也有 p 值。"""
    for name in P.FOLK_FAMILY:
        assert P.PATTERN_SPECS[name]["ks_p"] is None, name
        grade = P.evidence_grade(name)
        assert "民间" in grade
        assert "Lo" not in grade, f"{name} 的证据等级里不许出现 Lo"


def test_evidence_grade_marks_insignificant_ones_honestly():
    """NYSE 不显著的照说不显著 —— A3「文献结论为负的照写」。"""
    assert "不显著" in P.evidence_grade("TTOP")     # p=0.393
    assert "显著" in P.evidence_grade("RBOT")       # p=0.000
    assert "不显著" not in P.evidence_grade("HS")   # p=0.002


def test_lo_parameters_are_the_published_values():
    """容差与窗口是 Lo 给的，不是我们调的。改这些数 = 离开这篇论文的授权范围。"""
    assert (P.TOL_PCT, P.TOL_RECT_PCT, P.DTOP_MIN_SEP) == (0.015, 0.0075, 22)
    assert (P.LO_WINDOW, P.LO_LAG) == (35, 3)


# ══════════════════════════════════════════════════════════════
# 2. 平滑后取极值（Lo 明确反对在原始价上直接找极值）
# ══════════════════════════════════════════════════════════════

def test_extrema_are_far_fewer_than_raw_fractals():
    """平滑的意义就在这里：同一段噪音序列，原始价 3-bar 分形会报出一堆极值。

    ⚠️ 变异测试确认过：把 `extrema` 的带宽写死成 0.3（= cv_bandwidth 在日线上的
    退化值）这条会红 —— 那正是  实测到的病（35 根出 16 个极值）。
    """
    closes = [100 + 8 * math.sin(i / 6) + (2.5 if i % 3 == 0 else -2.5) for i in range(120)]
    bars = _bars(closes)
    raw = sum(1 for i in range(1, len(closes) - 1)
              if (closes[i] > closes[i - 1] and closes[i] > closes[i + 1])
              or (closes[i] < closes[i - 1] and closes[i] < closes[i + 1]))
    assert len(P.extrema(bars)) < raw / 2


def _walk(n, seed=7, sigma=1.5):
    """确定性随机游走 —— 比正弦更像真实价格（真实序列才是密度目标要服务的对象）。"""
    import random
    rnd = random.Random(seed)
    p, out = 100.0, []
    for _ in range(n):
        p *= 1 + rnd.gauss(0, sigma / 100)
        out.append(p)
    return out


def test_extrema_density_lands_in_target_band_on_random_walk():
    """密度目标跨尺度不变：35 / 120 / 250 根上都该落进 [5, 12]。

    用随机游走而不是正弦：正弦的转折数由它自己的周期决定，任何带宽都可能
    够不到目标带 —— 那测的是合成数据的性质，不是本函数的性质。
    """
    bars = _bars(_walk(400))
    for w in (P.LO_WINDOW, P.SWING_WINDOW, 250):
        n = len(P.extrema(bars[-w:]))
        assert P.DENSITY_MIN <= n <= P.DENSITY_MAX, f"window={w} 得到 {n} 个极值"


def test_bandwidth_falls_back_when_target_unreachable():
    """目标带够不到时**不许崩**，退最接近的一档（且必须是网格上的值）。"""
    flat = _bars([100.0] * 60)
    assert P.bandwidth_for_density(flat) in P.H_GRID or P.bandwidth_for_density(flat) == 2.0


def test_flat_stretch_produces_no_extrema():
    """横盘段不该被读成「每根 bar 一个极值」—— 浮点噪音守卫。

    ⚠️ 变异测试：删掉 `_raw_extrema` 里的 eps 判断，这条会红（实测造出 5 个假极值）。
    """
    assert P.extrema(_bars([100.0] * 80)) == []


def test_extrema_prices_exist_on_the_tape():
    """平滑只用来**定位**；报出去的价必须是真实 high/low —— 否则画出来的线
    在 K 线图上找不到对应物。"""
    closes = [100 + 10 * math.sin(i / 7) for i in range(150)]
    bars = _bars(closes)
    highs = {round(float(b["high"]), 6) for b in bars}
    lows = {round(float(b["low"]), 6) for b in bars}
    for e in P.extrema(bars):
        pool = highs if e["kind"] == "max" else lows
        assert round(e["price"], 6) in pool


def test_extrema_strictly_alternate():
    closes = [100 + 12 * math.sin(i / 8) for i in range(200)]
    kinds = [e["kind"] for e in P.extrema(_bars(closes))]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))


# ══════════════════════════════════════════════════════════════
# 3. 形态识别
# ══════════════════════════════════════════════════════════════

def test_head_and_shoulders_on_synthetic():
    """左肩 100 / 谷 90 / 头 115 / 谷 90.5 / 右肩 100.3 —— 肩与谷各自在 1.5% 内。

    ⚠️ 起点取 92 而不是 70：从远处一路拉上来的陡腿会把**左肩**的平滑极值往下拽
    （70 起步实测左肩被压到 97.15，与右肩 100.39 差 3.3% > 1.5% 容差，HS 判不出）。
    这不是 bug —— 是平滑的固有性质，也正是 Lo 的容差在管的事；测试要给一个
    公平的形状，而不是把容差调松到能过。
    """
    closes = _zigzag([92, 100, 90, 115, 90.5, 100.3, 94], per_leg=9)
    hits = {h["name"] for h in P.detect(_bars(closes), window=len(closes))}
    assert "HS" in hits


def test_double_top_requires_22_bar_separation():
    """间隔 ≤22 个交易日不算双顶（Lo 沿用 Edwards & Magee）。"""
    near = _zigzag([80, 100, 92, 100.3, 85], per_leg=5)     # 峰距 ~10
    far = _zigzag([80, 100, 92, 100.3, 85], per_leg=16)     # 峰距 ~32
    names_near = {h["name"] for h in P.detect(_bars(near), window=len(near))}
    names_far = {h["name"] for h in P.detect(_bars(far), window=len(far))}
    assert "DTOP" not in names_near
    assert "DTOP" in names_far


def test_wedge_requires_convergence_not_just_same_direction():
    """两条边同向但**发散**= 通道，不给楔形标签（不收敛就不是楔形）。"""
    diverging = _zigzag([100, 96, 106, 90, 112, 84, 118], per_leg=7)
    assert not (set(h["name"] for h in P.detect(_bars(diverging),
                                                window=len(diverging)))
                & set(P.FOLK_FAMILY))


# ══════════════════════════════════════════════════════════════
# 4. 四态语义 + 可证伪（A5）
# ══════════════════════════════════════════════════════════════

def test_every_hit_carries_confirmation_and_falsification():
    """**每条判读都必须能被推翻**—— 没有证伪条件的形态判读就是占卜。"""
    for closes in (_zigzag([70, 100, 90, 115, 90.5, 100.5, 95], per_leg=9),
                   _zigzag([80, 100, 92, 100.3, 85], per_leg=16),
                   _zigzag([120, 100, 108, 96, 104, 94, 100], per_leg=7)):
        for h in P.detect(_bars(closes), window=len(closes)):
            assert h["confirm_if"].strip(), h["name"]
            assert h["invalidate_if"].strip(), h["name"]
            assert h["state"] in P.STATE_CN


def test_shaped_is_not_triggered():
    """「形状已成立」≠「确认条件已满足」。混掉这两个词，读者会把
    「头顶有个双顶轮廓」读成「双顶破位了」。"""
    closes = _zigzag([80, 100, 92, 100.3, 96], per_leg=16)   # 收在颈线之上
    hits = {h["name"]: h for h in P.detect(_bars(closes), window=len(closes))}
    if "DTOP" in hits:
        assert hits["DTOP"]["state"] == "shaped"


def test_state_order_and_display_names_are_single_source():
    """渲染层不许自己再写一份状态映射（两处映射迟早分叉）。"""
    assert set(P.STATE_CN) == set(P.STATE_ORDER) == {
        "triggered", "shaped", "forming", "invalidated"}


def _spike_bars():
    """慢摆动 + 一根被平滑器抹平的尖顶（真顶落在转折点之外 4 根）。"""
    seq = _zigzag([100, 130, 105, 135, 100, 128, 102], per_leg=14)
    seq[45] = 152.0
    return _bars(seq)


def test_snap_radius_scales_with_bandwidth_not_one_bar():
    """**搜索半径必须够得着平滑器自己造成的位移**（由 ±1 改）。

    旧的 ±1 是结构性错的：带宽中位 5.0，而搜索半径只有 1 根 —— 比它要纠正的
    误差还小，于是全书 223 个极值里 38% 报出的价与真实极值差 >2%。
    源案例 MSFT：平滑顶在 5/27，真顶 6/01 466.32，±1 够不着 → 捞回 5/28 的
    429.49，双底颈线错 2.6N。
    """
    bars = _spike_bars()
    ext = P.extrema(bars)
    tps = dict(P._turning_points([b["close"] for b in bars],
                                 P.bandwidth_for_density(bars)))
    peak = max(ext, key=lambda e: e["price"])
    assert peak["price"] == pytest.approx(max(b["high"] for b in bars))
    # 真顶离它自己的转折点 >1 根 —— 也就是 ±1 那版**结构上不可能**捞到它
    assert min(abs(peak["idx"] - t) for t in tps) > 1


def test_snap_never_steals_the_neighbouring_swing():
    """搜索窗不许越过相邻转折点，也不许超过一个带宽 —— 纯半周期实测能报出
    离转折点 14 根的价（CRWV），甚至报出**早于**转折点 13 根的高点（SNDK），
    那已经是隔壁摆动的极值了。"""
    for bars in (_spike_bars(),
                 _bars(_zigzag([120, 100, 108, 99.7, 118], per_leg=16)),
                 _bars(_zigzag([70, 100, 90, 115, 90.5, 100.5, 95], per_leg=9))):
        h = P.bandwidth_for_density(bars)
        tps = [t for t, _ in P._turning_points([b["close"] for b in bars], h)]
        if not tps:
            continue
        for e in P.extrema(bars):
            own = min(tps, key=lambda t: abs(t - e["idx"]))
            assert abs(e["idx"] - own) <= math.ceil(h), "超过一个带宽"
            nearer = [t for t in tps if t != own
                      and (own < t <= e["idx"] or e["idx"] <= t < own)]
            assert not nearer, "越过了相邻转折点 = 偷隔壁摆动的极值"


def test_stale_pattern_is_dropped():
    """三个月前走完的形状不该天天出现在「当前所处的形态」里。"""
    old = _zigzag([70, 100, 90, 115, 90.5, 100.5], per_leg=9)
    tail = [old[-1]] * 90
    assert not P.detect(_bars(old + tail), window=len(old) + 90)


def _dbot(tail):
    """双底夹具：两谷 100 / 99.7（差 0.3% 过 1.5% 容差）间隔 32 根，颈线 ≈108。
    窗口显式给全长 —— 默认 `LO_WINDOW` 会把第一只脚截掉。"""
    bars = _bars(_zigzag([120, 100, 108, 99.7], per_leg=16) + list(tail))
    return {h["name"]: h for h in P.detect(bars, window=len(bars))}


def test_resolved_pattern_reports_which_side_and_how_long_ago():
    """已破确认线的形态必须报出**破哪边、破了多久**—— 容器型形态的方向
    只由破哪边决定，不写就等于没判读（用户  抓的那三行）。"""
    d = _dbot(_zigzag([99.7, 118], per_leg=6)).get("DBOT")
    assert d and d["state"] == "triggered"
    assert d["resolved_side"] == "up"
    assert d["bars_since_trigger"] is not None


def test_trigger_age_counts_only_the_current_run():
    """中途走回线内 = 上一段结束了。破线年龄量的是**当前这一段**，
    不是历史上第一次越线 —— 否则一次假突破会永久污染这个数。"""
    whip = list(_zigzag([99.7, 112, 104, 120], per_leg=6))     # 破 → 回线内 → 再破
    d = _dbot(whip).get("DBOT")
    assert d and d["state"] == "triggered"
    assert d["bars_since_trigger"] < len(whip) // 2, "回到线内那一段不该被算进破线年龄"


def test_trigger_age_never_exceeds_pattern_age():
    """**这条不变量说明「按破线年龄再过滤一遍」是空转**—— 破线必然发生在形态
    完成之后，所以 `since_trigger ≤ bars_since` 恒成立，同一把尺量破线永远拒不掉
    任何东西。2026-08-13 我按这个思路写过一版过滤，实测一个都没拒掉。
    想拒就得新拍一个更紧的阈值 = 无出处自由度，而全书破线年龄最大 14 根，
    没有样本可校准 → 不加。改这里之前先读这段。"""
    for tail in (_zigzag([99.7, 118], per_leg=6),
                 _zigzag([99.7, 112, 104, 120], per_leg=6),
                 _zigzag([99.7, 130], per_leg=20)):
        for h in _dbot(tail).values():
            if h.get("bars_since_trigger") is not None:
                assert h["bars_since_trigger"] <= h["bars_since"], h["name"]


def test_summarize_never_stays_silent():
    """没有形态是最常见的真相，但**不许沉默**—— 沉默会被读成「没看」。"""
    assert P.summarize([]).strip()
    assert "常态" in P.summarize([])


# ══════════════════════════════════════════════════════════════
# 5. 多尺度
# ══════════════════════════════════════════════════════════════

def test_multi_window_marks_extrapolated_scale():
    """只有 35 日窗那一档是 Lo 检验过的口径；其余是**尺度外推**，
    p 值不自动跟着走 —— 必须打标。"""
    closes = _zigzag([60, 100, 88, 116, 89, 101, 96], per_leg=14)
    for h in P.detect_multi(_bars(closes)):
        assert h["lo_calibrated"] == (h["window"] == P.LO_WINDOW)


def test_kernel_smooth_is_monotone_in_bandwidth():
    """带宽越大越平滑（极值越少）—— `bandwidth_for_density` 的单调性前提。"""
    closes = [100 + 9 * math.sin(i / 5) + 2 * math.cos(i * 1.7) for i in range(160)]
    bars = _bars(closes)
    counts = [len(P._raw_extrema(bars, h)) for h in (1.0, 3.0, 8.0, 16.0)]
    assert counts == sorted(counts, reverse=True)


@pytest.mark.parametrize("h", [1.0, 4.0, 12.0])
def test_kernel_smooth_preserves_level(h):
    """平滑不许整体搬动价位（核权重归一）。"""
    closes = [100.0] * 60
    assert all(abs(v - 100.0) < 1e-9 for v in P.kernel_smooth(closes, h))


# ══════════════════════════════════════════════════════════════
# 6. 价值区（拍卖理论坐标系，B0-4）
# ══════════════════════════════════════════════════════════════

def _prof(vals, lo0=100.0, w=1.0):
    """[(lo, turnover)] → volume_profile 形状的 profile。"""
    tot = sum(vals) or 1.0
    return [{"lo": lo0 + i * w, "hi": lo0 + (i + 1) * w, "turnover": float(v),
             "share_pct": round(v / tot * 100, 1)} for i, v in enumerate(vals)]


def test_value_area_covers_at_least_the_target_fraction():
    from quant.core import technical as ta
    va = ta.value_area(_prof([1, 2, 5, 20, 5, 2, 1]))
    assert va["covered_pct"] >= 70.0


def test_value_area_centers_on_poc():
    from quant.core import technical as ta
    va = ta.value_area(_prof([1, 1, 1, 30, 1, 1, 1]))
    assert va["poc_lo"] == 103.0 and va["val"] <= 103.0 <= va["vah"]


def test_value_area_expands_toward_the_heavier_side():
    """扩张规则 = 每步并入成交额大的那一侧（Market Profile 标准做法）。"""
    from quant.core import technical as ta
    va = ta.value_area(_prof([0, 0, 1, 10, 9, 8, 0]))   # POC 右侧明显更重
    assert va["vah"] > 105.0 and va["val"] >= 102.0


def test_value_area_frac_is_the_auction_theory_constant():
    """70% 是**定义**不是拍的 —— 改它 = 离开拍卖理论的词汇表，那时它才是一个自由度。"""
    from quant.core import technical as ta
    assert ta.VALUE_AREA_FRAC == 0.70


def test_value_area_degrades_on_empty_or_zero_profile():
    from quant.core import technical as ta
    assert ta.value_area([]) is None
    assert ta.value_area(_prof([0, 0, 0])) is None


def test_value_area_does_not_change_volume_profile_shape():
    """加法不改形状 —— `volume_profile` 有 5 个消费者（含 gate 画线），
    改它的返回结构会一次打到全部。"""
    from quant.core import technical as ta
    bars = _bars(_walk(80))
    prof = ta.volume_profile(bars, 1.0)
    assert set(prof[0]) == {"lo", "hi", "turnover", "share_pct"}
