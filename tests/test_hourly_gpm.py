# -*- coding: utf-8 -*-
"""tests/test_hourly_gpm.py — 按小时桶聚合与 GPM 计算的离线单测(纯标准库,无第三方)。

运行:python tests/test_hourly_gpm.py   (也可用 pytest 收集)
覆盖(按任务契约):
  1. 常规聚合:分钟级成交金额 + 分钟级观看 → 每小时 [hour, views, gmv, gpm]
     (GMV 桶合计守恒、gpm=gmv/views*1000 两位小数);
  2. 跨场同小时叠加:先合并再算 GPM(非均值);
  3. views=0 边界:不留 NaN,输出留空并标注;
  4. 两种观看粒度:分钟级观看 与 小时级观看 输入形态结果一致;
  5. 负向:非法数值/非法时间被结构化拒绝;空输入被拒绝。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from extractor import hourly_gpm  # noqa: E402
from extractor.hourly_gpm import HourlyGpmError, bucket_hour, hourly_gpm  # noqa: E402

REF = None  # 夹具全部使用完整日期,不需补年


# ---------------------------------------------------------------------------
# 1) 常规聚合(分钟级观看)
# ---------------------------------------------------------------------------
def test_basic_hourly_aggregation_minute_watch():
    gmv_rows = [
        {"time": "2026-09-04 09:04", "gmv_min": 160.0},
        {"time": "2026-09-04 09:23", "gmv_min": 168.0},
        {"time": "2026-09-04 10:00", "gmv_min": 0.0},
        {"time": "2026-09-04 10:45", "gmv_min": 40.0},
    ]
    watch_rows = [
        {"time": "2026-09-04 09:04", "views": 55},
        {"time": "2026-09-04 09:23", "views": 77},
        {"time": "2026-09-04 10:00", "views": 30},
        {"time": "2026-09-04 10:45", "views": 40},
    ]
    rep = hourly_gpm(gmv_rows, watch_rows)
    assert rep["rows"][0]["hour"] == "2026-09-04 09:00"
    assert rep["rows"][0]["views"] == 132 and rep["rows"][0]["gmv"] == 328.0
    assert rep["rows"][0]["gpm"] == 2484.85  # 328/132*1000 两位小数
    assert rep["rows"][1]["hour"] == "2026-09-04 10:00"
    assert rep["rows"][1]["views"] == 70 and rep["rows"][1]["gmv"] == 40.0
    assert rep["rows"][1]["gpm"] == 571.43
    assert len(rep["rows"]) == 2
    # 守恒校验
    assert rep["checks"]["gmv_conserved"] is True
    assert rep["checks"]["watch_conserved"] is True
    assert rep["checks"]["gmv_input"] == 368.0
    assert abs(rep["checks"]["gmv_diff"]) <= 1e-6
    # 整场合计 GPM(供与页面 GPM 卡对拍的锚)
    assert rep["totals"]["gmv"] == 368.0 and rep["totals"]["views"] == 202.0
    assert rep["totals"]["gpm"] == 1821.78  # 368/202*1000


# ---------------------------------------------------------------------------
# 2) 跨场同小时叠加:先合并再算 GPM(绝不是两场 gpm 均值)
# ---------------------------------------------------------------------------
def test_cross_session_same_hour_merge_not_average():
    session_a_gmv = [
        {"time": "2026-09-04 09:04", "gmv_min": 160.0},
        {"time": "2026-09-04 09:23", "gmv_min": 0.0},
        {"time": "2026-09-04 10:01", "gmv_min": 5.0},
    ]
    session_a_watch = [
        {"time": "2026-09-04 09:04", "views": 80},
        {"time": "2026-09-04 10:01", "views": 10},
    ]
    session_b_gmv = [
        {"time": "2026-09-04 09:05", "gmv_min": 20.0},
        {"time": "2026-09-04 11:00", "gmv_min": 7.0},
    ]
    session_b_watch = [
        {"time": "2026-09-04 09:05", "views": 40},
        {"time": "2026-09-04 11:00", "views": 14},
    ]
    # 两场拼接 → 同一天 09:00 桶合并(views 80+40=120, gmv 160+20=180)
    rep = hourly_gpm(session_a_gmv + session_b_gmv,
                     session_a_watch + session_b_watch)
    hours = [r["hour"] for r in rep["rows"]]
    assert hours == ["2026-09-04 09:00", "2026-09-04 10:00", "2026-09-04 11:00"], hours
    row09 = rep["rows"][0]
    assert row09["views"] == 120 and row09["gmv"] == 180.0
    # 合并后 GPM = 180/120*1000 = 1500.0;A 场 gpm=2000,B 场 gpm=500,均值 1250 → 必须不等于均值
    assert row09["gpm"] == 1500.0
    assert row09["gpm"] != 1250.0
    assert rep["checks"]["gmv_conserved"] is True
    assert rep["totals"]["gmv"] == 192.0


# ---------------------------------------------------------------------------
# 3) views=0 边界:不留 NaN;输出留空 + note 标注
# ---------------------------------------------------------------------------
def test_zero_views_hour_no_nan():
    gmv_rows = [
        {"time": "2026-09-04 08:05", "gmv_min": 120.0},
        {"time": "2026-09-04 09:05", "gmv_min": 90.0},
    ]
    # 09:05 无对应观看行(整小时 views=0);08:05 有观看行且 views=0
    watch_rows = [{"time": "2026-09-04 08:05", "views": 0}]
    rep = hourly_gpm(gmv_rows, watch_rows)
    text = repr(rep)
    assert "nan" not in text.lower() and "inf" not in text.lower()
    row08 = rep["rows"][0]
    row09 = rep["rows"][1]
    assert row08["views"] == 0 and row08["gmv"] == 120.0
    assert row08["gpm"] is None
    assert "views=0" in row08["note"]
    assert row09["views"] == 0 and row09["gmv"] == 90.0
    assert row09["gpm"] is None and "views=0" in row09["note"]
    assert rep["checks"]["gmv_conserved"] is True
    assert rep["totals"]["views"] == 0 and rep["totals"]["gpm"] is None


# ---------------------------------------------------------------------------
# 4) 两种观看输入形态:分钟级 与 小时级 结果一致
# ---------------------------------------------------------------------------
def test_hour_level_watch_matches_minute_level():
    gmv_rows = [
        {"time": "2026-09-04 09:04", "gmv_min": 160.0},
        {"time": "2026-09-04 09:23", "gmv_min": 168.0},
        {"time": "2026-09-04 10:45", "gmv_min": 40.0},
    ]
    minute_watch = [
        {"time": "2026-09-04 09:04", "views": 55},
        {"time": "2026-09-04 09:23", "views": 77},
        {"time": "2026-09-04 10:45", "views": 40},
    ]
    hour_watch = [
        {"hour": "2026-09-04 09:00", "views": 132},
        {"hour": "2026-09-04 10:00", "views": 40},
    ]
    rep_min = hourly_gpm(gmv_rows, minute_watch, watch_level="minute")
    rep_hr = hourly_gpm(gmv_rows, hour_watch, watch_level="hour")
    assert rep_hr["watch_level_used"] == "hour"
    assert [tuple(r[k] for k in ("hour", "views", "gmv", "gpm"))
            for r in rep_min["rows"]] == \
        [tuple(r[k] for k in ("hour", "views", "gmv", "gpm"))
         for r in rep_hr["rows"]]
    assert rep_hr["checks"]["gmv_conserved"] is True
    assert rep_hr["checks"]["watch_conserved"] is True


# ---------------------------------------------------------------------------
# 5) MM-DD 短格式 + session_date 补年;跨午夜行按自身日历日归属
# ---------------------------------------------------------------------------
def test_mmdD_with_session_date_and_midnight_rollover():
    gmv_rows = [
        {"room_id": "r1", "session_date": "2026-09-01", "time": "09-01 22:30", "gmv_min": 100.0},
        # 跨午夜:行自带日期翻到 09-02(页面时间轴 MM-DD 已翻日)
        {"room_id": "r1", "session_date": "2026-09-01", "time": "09-02 00:05", "gmv_min": 25.0},
        {"room_id": "r1", "session_date": "2026-09-01", "time": "09-02 00:59", "gmv_min": 5.0},
    ]
    watch_rows = [
        {"session_date": "2026-09-01", "time": "09-01 22:30", "views": 50},
        {"session_date": "2026-09-01", "time": "09-02 00:05", "views": 10},
        {"session_date": "2026-09-01", "time": "09-02 00:59", "views": 2},
    ]
    rep = hourly_gpm(gmv_rows, watch_rows)
    hours = [r["hour"] for r in rep["rows"]]
    # 开播日 09-01 的 22 点桶与次日 00 点桶分离(不按开播日整体归到 09-01)
    assert hours == ["2026-09-01 22:00", "2026-09-02 00:00"], hours
    assert rep["rows"][0]["gmv"] == 100.0 and rep["rows"][0]["views"] == 50
    assert rep["rows"][1]["gmv"] == 30.0 and rep["rows"][1]["views"] == 12
    assert rep["rows"][1]["gpm"] == 2500.0  # 30/12*1000
    assert rep["checks"]["gmv_conserved"] is True


# ---------------------------------------------------------------------------
# 6) epoch 秒时间(东八区墙面)归桶
# ---------------------------------------------------------------------------
def test_epoch_time_bucket():
    # 2026-09-04 01:04:00 UTC = 2026-09-04 09:04 CST
    ts = 1788483840
    rep = hourly_gpm([{"time": ts, "gmv_min": 10.0}],
                     [{"time": ts, "views": 5}])
    assert rep["rows"][0]["hour"] == "2026-09-04 09:00"
    assert rep["rows"][0]["gpm"] == 2000.0
    # 数字字符串等价的 epoch 外观
    rep2 = hourly_gpm([{"time": str(ts), "gmv_min": 10.0}],
                      [{"time": str(ts), "views": 5}])
    assert rep2["rows"][0]["hour"] == "2026-09-04 09:00"


# ---------------------------------------------------------------------------
# 7) 负向:结构化拒绝
# ---------------------------------------------------------------------------
def test_negative_cases():
    # 空 gmv
    try:
        hourly_gpm([])
    except HourlyGpmError:
        pass
    else:
        raise AssertionError("空 gmv_rows 应被拒绝")
    # 非法数值
    try:
        hourly_gpm([{"time": "2026-09-04 09:00", "gmv_min": "abc"}])
    except HourlyGpmError:
        pass
    else:
        raise AssertionError("非数值 gmv_min 应被拒绝")
    # NaN 数值
    try:
        hourly_gpm([{"time": "2026-09-04 09:00", "gmv_min": float("nan")}])
    except HourlyGpmError:
        pass
    else:
        raise AssertionError("NaN gmv_min 应被拒绝")
    # 非法时间外观
    try:
        hourly_gpm([{"time": "not-a-time", "gmv_min": 1.0}])
    except HourlyGpmError:
        pass
    else:
        raise AssertionError("非法时间外观应被结构化拒绝")
    # 缺 views 数值键
    try:
        hourly_gpm([{"time": "2026-09-04 09:00", "gmv_min": 1.0}],
                   [{"time": "2026-09-04 09:00"}])
    except HourlyGpmError:
        pass
    else:
        raise AssertionError("观看行缺 views 应被拒绝")


# ---------------------------------------------------------------------------
# 8) bucket_hour 工具基本行为
# ---------------------------------------------------------------------------
def test_bucket_hour_basics():
    assert bucket_hour("2026-09-04 09:59") == "2026-09-04 09:00"
    assert bucket_hour("2026-09-04 10:00") == "2026-09-04 10:00"
    assert bucket_hour("09-04 09:04", None) == "2000-09-04 09:00"  # 无参考年兜底
    assert bucket_hour("09-04 09:04", _date("2026-09-04")) == "2026-09-04 09:00"
    try:
        bucket_hour("13:00", None)
    except HourlyGpmError:
        pass
    else:
        raise AssertionError("无日期 HH:MM 无法归桶,应拒绝")


def _date(text):
    import datetime as _dt
    return _dt.date.fromisoformat(text)


# ---------------------------------------------------------------------------
def _run_all():
    tests = [
        test_basic_hourly_aggregation_minute_watch,
        test_cross_session_same_hour_merge_not_average,
        test_zero_views_hour_no_nan,
        test_hour_level_watch_matches_minute_level,
        test_mmdD_with_session_date_and_midnight_rollover,
        test_epoch_time_bucket,
        test_negative_cases,
        test_bucket_hour_basics,
    ]
    for fn in tests:
        fn()
    return len(tests)


if __name__ == "__main__":
    n = _run_all()
    print(f"test_hourly_gpm: {n}/{n} passed")
