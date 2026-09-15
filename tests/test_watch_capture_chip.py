# -*- coding: utf-8 -*-
"""tests/test_watch_capture_chip.py — t13:弹窗确定后“自动点按图例芯片使 watch 曲线显示”决策(离线)。

覆盖(纯函数,不触网/不开浏览器):
  1. 常量纪律:同轮点按 ≤ CHIP_MAX_CLICKS、重试 ≤ CHIP_MAX_RETRIES、芯片扫描带合理;
  2. 芯片文本归一:直播间看播量 → 直播间观看量;非指标文本不参与;
  3. watch 芯片 off → 单次点按使其显示(带坐标);on → 无需点按;
  4. watch 芯片开关态未知 → 保守不点按(避免误关已显示曲线),assist 提示兜底;
  5. watch 芯片缺失(文本定位失败)→ 不盲点,assist 提示 + 锚点(坐标标定)记录;
  6. 成交金额(gmv 分子)芯片永不点按,呈 off 记为异常(forbidden_off,ok=False);
  7. 千次观看成交金额 芯片 off 时顺带点按(在 cap 内),缺失/未知不阻塞 watch;
  8. 别名行去重:同一指标只点一次;点按集合绝不包含非白名单指标芯片。

运行:python tests/test_watch_capture_chip.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from exporter import watch_capture as wc  # noqa: E402

_CHIP = {"text": "直播间观看量", "state": "off", "x": 520, "y": 340}
_CHIP_ON = {"text": "直播间观看量", "state": "on", "x": 520, "y": 340}
_CHIP_UNKNOWN = {"text": "直播间观看量", "state": None, "x": 520, "y": 340}
_GMV = {"text": "成交金额", "state": "on", "x": 230, "y": 340}
_GPM_OFF = {"text": "千次观看成交金额", "state": "off", "x": 660, "y": 340}


def test_constants_limits_and_band():
    assert wc.CHIP_MAX_CLICKS == 2          # 同轮最多 2 个芯片(观看 + 千次观看成交金额)
    assert wc.CHIP_MAX_RETRIES == 1         # 重试 ≤1,不循环
    b = wc.CHIP_BAND
    assert b["y_min"] <= 341 <= b["y_max"]  # 实测芯片行中心 y≈341 在带内
    assert b["x_max"] < 900                 # 避让齿轮(iIFiN@~849)与 popper
    assert wc.WATCH_CHIP_METRIC == "直播间观看量"
    assert wc.CHIP_FORBIDDEN_TOGGLE == wc.REQUIRED_METRIC == "成交金额"


def test_chip_text_normalization_whitelist():
    assert wc._norm_metric("直播间看播量") == "直播间观看量"   # 别名 → watch 芯片
    assert wc._norm_metric("直播间观看量") == "直播间观看量"
    assert wc._norm_metric("千次观看成交金额") == "千次观看成交金额"
    assert wc._norm_metric("商品点击率") is None              # 非白名单不参与
    assert wc._norm_metric("违规") is None


def test_watch_chip_off_planned_single_click():
    plan = wc.plan_chip_clicks([_CHIP, _GMV])
    assert plan["ok"] is True, plan["reason"]
    assert plan["clicks"] == [{"metric": "直播间观看量", "x": 520, "y": 340}]
    assert plan["watch_missing"] is False
    assert "成交金额" not in [c["metric"] for c in plan["clicks"]]


def test_watch_chip_on_no_click_needed():
    plan = wc.plan_chip_clicks([_CHIP_ON, _GMV])
    assert plan["ok"] is True
    assert plan["clicks"] == []
    assert "直播间观看量" in plan["on_already"]


def test_watch_chip_unknown_state_conservative_no_click():
    plan = wc.plan_chip_clicks([_CHIP_UNKNOWN, _GMV])
    assert plan["ok"] is False                      # 态不可判 → 不自动闭环
    assert plan["clicks"] == []                     # 绝不盲点
    assert "直播间观看量" in plan["unknown_state"]
    hint = wc.chip_assist_hint(plan)
    assert hint and "直播间观看量" in hint and "人工点击" in hint


def test_watch_chip_missing_fallback_hint_and_anchor():
    plan = wc.plan_chip_clicks([_GMV])
    assert plan["watch_missing"] is True
    assert plan["ok"] is False
    assert plan["clicks"] == []
    hint = wc.chip_assist_hint(plan)
    assert hint and "未见" in hint
    # 文本定位失败时的坐标标定:锚点随 plan 记录,由调用方单次点按(不循环)
    plan_a = wc.plan_chip_clicks([_GMV], anchor={"x": 720, "y": 342})
    assert plan_a["anchor"] == {"x": 720, "y": 342}
    assert plan_a["clicks"] == []                  # 锚点动作在调用方执行,决策层不伪造“已点按”


def test_gmv_chip_never_clicked_even_off():
    gmv_off = dict(_GMV, state="off")
    plan = wc.plan_chip_clicks([_CHIP, gmv_off])
    # gmv 呈 off 是异常:ok=False,且永不在点按集合中
    assert plan["ok"] is False
    assert plan["forbidden_off"] == ["成交金额"]
    assert all(c["metric"] != "成交金额" for c in plan["clicks"])
    assert any(c["metric"] == "直播间观看量" for c in plan["clicks"])
    hint = wc.chip_assist_hint(plan)
    assert hint and "成交金额" in hint


def test_gpm_optional_off_added_within_cap():
    plan = wc.plan_chip_clicks([_CHIP, _GMV, _GPM_OFF])
    assert plan["ok"] is True
    assert [c["metric"] for c in plan["clicks"]] == ["直播间观看量", "千次观看成交金额"]
    assert len(plan["clicks"]) <= wc.CHIP_MAX_CLICKS
    # watch 已 on 时,gpm off 也顺带点按;gpm 缺失/态未知不阻塞 watch
    plan2 = wc.plan_chip_clicks([_CHIP_ON, _GMV, _GPM_OFF])
    assert plan2["ok"] is True
    assert [c["metric"] for c in plan2["clicks"]] == ["千次观看成交金额"]


def test_optional_gpm_missing_does_not_fail_watch():
    plan = wc.plan_chip_clicks([_CHIP_ON, _GMV])
    assert plan["ok"] is True
    assert "千次观看成交金额" in plan["missing"]   # 仅记录说明,不影响 watch 闭环


def test_alias_rows_dedup_single_click():
    chips = [_CHIP, {"text": "直播间看播量", "state": "off", "x": 500, "y": 341}, _GMV]
    plan = wc.plan_chip_clicks(chips)
    assert plan["ok"] is True
    watch_clicks = [c for c in plan["clicks"] if c["metric"] == "直播间观看量"]
    assert len(watch_clicks) == 1                   # 别名行去重,同指标只点一次


def test_clicks_capped_and_whitelist_only():
    chips = [_CHIP, _GPM_OFF,
             {"text": "点赞次数", "state": "off", "x": 720, "y": 340},
             {"text": "成交订单数", "state": "off", "x": 310, "y": 340},
             _GMV]
    plan = wc.plan_chip_clicks(chips, optional=["千次观看成交金额", "点赞次数", "成交订单数"])
    assert len(plan["clicks"]) == wc.CHIP_MAX_CLICKS  # 超过即截断,绝不超 cap
    metrics = [c["metric"] for c in plan["clicks"]]
    assert "成交金额" not in metrics
    # 默认 optional(仅 千次观看成交金额):点赞/订单数 芯片不进入点按集(非目标)
    plan_d = wc.plan_chip_clicks([_CHIP_ON, _GMV,
                                  {"text": "点赞次数", "state": "off", "x": 720, "y": 340}])
    assert plan_d["ok"] is True and plan_d["clicks"] == []


def test_assist_hint_empty_when_plan_ok():
    assert wc.chip_assist_hint(wc.plan_chip_clicks([_CHIP_ON, _GMV])) == ""


def test_resolve_chip_anchor_cfg():
    assert wc._resolve_chip_anchor({}) is None
    assert wc._resolve_chip_anchor({"export": {}}) is None
    assert wc._resolve_chip_anchor(
        {"export": {"watch_capture_chip_anchor": {"x": "720", "y": 342}}}) == {"x": 720, "y": 342}
    assert wc._resolve_chip_anchor(
        {"export": {"watch_capture_chip_anchor": {"x": 99999, "y": 342}}}) is None
    assert wc._resolve_chip_anchor(
        {"export": {"watch_capture_chip_anchor": {"x": "bad", "y": 342}}}) is None


def _plan_with(chip_row):
    rows = [chip_row] if chip_row else []
    rows.append(_GMV)
    return wc.plan_chip_clicks(rows)


def test_fallback_noop_when_watch_present():
    # watch 已就绪(序列已到)→ 不需要回退
    assert wc.decide_watch_chip_fallback(_plan_with(_CHIP_ON), still_absent=False) == "noop"


def test_fallback_single_informed_click_when_absent_and_located():
    # watch 缺失/未显示 + 已文本定位芯片(态 off/unknown)→ 单次知情点按
    p_off = _plan_with(_CHIP)
    assert wc.decide_watch_chip_fallback(p_off, still_absent=True, used=0) == "click-watch-once"
    p_unk = _plan_with(_CHIP_UNKNOWN)
    assert wc.decide_watch_chip_fallback(p_unk, still_absent=True, used=0) == "click-watch-once"


def test_fallback_no_repeat_and_missing_goes_assist():
    # 已用过一次回退 → 不再自动点(转人工);watch 芯片文本缺失 → 人工(不盲点)
    p_unk = _plan_with(_CHIP_UNKNOWN)
    assert wc.decide_watch_chip_fallback(p_unk, still_absent=True, used=1) == "assist"
    p_missing = wc.plan_chip_clicks([_GMV])
    assert wc.decide_watch_chip_fallback(p_missing, still_absent=True, used=0) == "assist"
    # watch 芯片已 on 但序列未到 → 不点按(避免把已显示曲线点没),等数据/转人工
    assert wc.decide_watch_chip_fallback(_plan_with(_CHIP_ON),
                                         still_absent=True, used=0) == "noop"


def test_fallback_after_auto_click_used_goes_assist():
    # 自动已点按过 watch(off→click 路径)→ 再缺失时不再叠加点按
    plan = wc.plan_chip_clicks([_CHIP, _GMV])  # clicks 含 watch
    assert plan["clicks"]
    assert wc.decide_watch_chip_fallback(plan, still_absent=True,
                                         used=1) == "assist"


def _run_all():
    tests = [
        test_constants_limits_and_band,
        test_chip_text_normalization_whitelist,
        test_watch_chip_off_planned_single_click,
        test_watch_chip_on_no_click_needed,
        test_watch_chip_unknown_state_conservative_no_click,
        test_watch_chip_missing_fallback_hint_and_anchor,
        test_gmv_chip_never_clicked_even_off,
        test_gpm_optional_off_added_within_cap,
        test_optional_gpm_missing_does_not_fail_watch,
        test_alias_rows_dedup_single_click,
        test_clicks_capped_and_whitelist_only,
        test_assist_hint_empty_when_plan_ok,
        test_resolve_chip_anchor_cfg,
        test_fallback_noop_when_watch_present,
        test_fallback_single_informed_click_when_absent_and_located,
        test_fallback_no_repeat_and_missing_goes_assist,
        test_fallback_after_auto_click_used_goes_assist,
    ]
    for fn in tests:
        fn()
    return len(tests)


if __name__ == "__main__":
    n = _run_all()
    print(f"test_watch_capture_chip: {n}/{n} passed")
