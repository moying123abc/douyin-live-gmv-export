# -*- coding: utf-8 -*-
"""tests/test_watch_capture_link.py — t12:回放页观看捕获→观看档→小时 GPM 真实分母链路(离线)。

覆盖:
 1. watch_capture 模块可离线导入 + SEMANTICS_TEMPLATE 用真实复核数(Σ494 vs ServerWatchCntTd
    501,diff=-7)格式化出如实口径说明;
 2. 捕获模块产出的观看档格式(表头 time,views,semantics)可被 daily 链路读取;
 3. 全链路:含观看档(真实语义串)场次的 daily publish_day_hourly → 小时GPM_<ymd>.csv 以
    watch views 为分母输出、semantics 透传、views=0 桶留空不伪造;
 4. 无观看档 → 优雅降级说明(既有行为不回退)。

运行:python tests/test_watch_capture_link.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from exporter import hourly_gpm_output as hgo  # noqa: E402
from exporter import to_csv_excel  # noqa: E402
from exporter import watch_capture  # noqa: E402

ROWS_R1 = [
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:04", "gmv_min": 160.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:23", "gmv_min": 168.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 10:45", "gmv_min": 40.0},
]
# 观看分钟与成交分钟同轴(09:04/09:23 → 09 桶;10:45 → 10 桶)
WATCH_MIN = [("09-04 09:04", 80), ("09-04 09:23", 80), ("09-04 10:45", 10)]


def test_semantics_template_reflects_real_recheck():
    s = watch_capture.SEMANTICS_TEMPLATE.format(sum=494, card=501, diff=-7)
    assert "直播间观看量" in s and "WatchCntTrend" in s
    assert "494" in s and "501" in s and "-7" in s
    assert "未冒充精确" in s or "如实" in s
    # 另一场次稳定值(500)也如实可标注
    s2 = watch_capture.SEMANTICS_TEMPLATE.format(sum=494, card=500, diff=-6)
    assert "-6" in s2


def test_capture_watch_file_format_readable_by_daily(tmp: pathlib.Path):
    # 模拟 watch_capture 写观看档的格式
    semantics = watch_capture.SEMANTICS_TEMPLATE.format(sum=494, card=501, diff=-7)
    watch_p = tmp / f"live_20260904_r1{watch_capture.WATCH_MIN_SUFFIX}"
    import csv as _csv
    with watch_p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["time", "views", "semantics"])
        for t, v in WATCH_MIN:
            w.writerow([t, v, semantics])
    got = hgo._read_watch_min(watch_p)
    assert got["semantics"] == semantics
    assert [r["time"] for r in got["rows"]] == [t for t, _ in WATCH_MIN]
    assert len(got["rows"]) == 3


def test_day_publish_uses_watch_views_as_gpm_denominator():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        to_csv_excel.write_csv(tmp / "live_20260904_r1", ROWS_R1)
        to_csv_excel.write_compact_csv(
            tmp / "live_20260904_r1_compact", to_csv_excel.compact_rows(ROWS_R1))
        semantics = watch_capture.SEMANTICS_TEMPLATE.format(sum=170, card=170, diff=0)
        p = tmp / "live_20260904_r1_watch_min.csv"
        import csv as _csv
        with p.open("w", encoding="utf-8-sig", newline="") as fh:
            w = _csv.writer(fh)
            w.writerow(["time", "views", "semantics"])
            for t, v in WATCH_MIN:
                w.writerow([t, v, semantics])
        folder = tmp / "日报_20260904"
        res = hgo.publish_day_hourly(tmp, "2026-09-04", folder)
        assert res["ok"] is True, res
        csv_path = folder / "小时GPM_20260904.csv"
        assert csv_path.is_file()
        header, body = (lambda tb: (tb[0], tb[1]))(_read_csv(csv_path))
        rows_out = [dict(zip(header, r)) for r in body]
        hours = [r["hour"] for r in rows_out]
        assert hours == ["2026-09-04 09:00", "2026-09-04 10:00"], hours
        row09 = rows_out[0]
        # 09 桶:gmv 160+168=328,views 80+80=160 → gpm=2050.0(非整场卡 501;以分钟和为分母)
        assert row09["views"] == "160" and row09["gmv"] == "328.0"
        assert row09["gpm"] == "2050.0"
        assert "直播间观看量" in row09["watch_semantics"]
        note = (folder / "小时GPM_20260904_说明.txt").read_text(encoding="utf-8-sig")
        assert "GMV 合计 368.00" in note  # 328 + 40


def test_no_watch_file_degrades():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        to_csv_excel.write_csv(tmp / "live_20260904_r1", ROWS_R1)
        folder = tmp / "日报_20260904"
        res = hgo.publish_day_hourly(tmp, "2026-09-04", folder)
        assert res["ok"] is False
        assert not (folder / "小时GPM_20260904.csv").exists()
        note = folder / "小时GPM_20260904_说明.txt"
        assert note.is_file()


def _read_csv(path):
    import csv as _csv
    with pathlib.Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = _csv.reader(fh)
        table = list(reader)
    return table[0], table[1:]


def test_fixture_cli_watchcnt_real_annotation_produces_hourly():
    """全链路:export --fixture(sample_ended_session_gpm_watchcnt.json,含 t11 真实复核差语义)
    → 主 CSV 不变 + _hourly.csv 以观看分钟为分母、watch_semantics 透传真实口径说明。"""
    import subprocess
    ROOT = pathlib.Path(__file__).resolve().parent.parent
    fixture = ROOT / "data" / "samples" / "sample_ended_session_gpm_watchcnt.json"
    assert fixture.is_file()
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "wc"
        run = subprocess.run([sys.executable, "main.py", "export", "--fixture",
                              str(fixture), "--out", str(out)],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
        base = pathlib.Path(str(out) + ".csv")
        hourly = pathlib.Path(str(out) + "_hourly.csv")
        assert base.is_file() and hourly.is_file()
        header, body = _read_csv(hourly)
        assert header == ["hour", "views", "gmv", "gpm", "note", "watch_semantics"]
        assert "直播间观看量" in body[0][5] or "WatchCntTrend" in body[0][5]
        assert "494" in body[0][5] and "501" in body[0][5]


def test_plan_metric_changes_respects_max6_and_gmv_kept():
    """UI 约束:趋势图同屏 ≤6 指标且 成交金额(gmv 分子)必留(用户确认)。"""
    rows = [
        {"text": "直播间观看量", "checked": False},
        {"text": "成交金额", "checked": True},
        {"text": "成交订单数", "checked": True},
        {"text": "在线人数", "checked": True},
        {"text": "进入人数", "checked": True},
        {"text": "离开人数", "checked": True},
        {"text": "点赞次数", "checked": True},
        {"text": "千次观看成交金额", "checked": False},
    ]
    plan = watch_capture.plan_metric_changes(rows)
    assert plan["ok"] is True, plan.get("reason")
    assert watch_capture.REQUIRED_METRIC in plan["desired"]
    assert len(plan["desired"]) <= watch_capture.MAX_CHART_METRICS
    assert watch_capture.REQUIRED_METRIC not in plan["to_uncheck"]  # gmv 永不被取消
    # 应取消 离开人数/点赞次数(默认非必要),勾选 直播间观看量/千次观看成交金额
    assert "离开人数" in plan["to_uncheck"] and "点赞次数" in plan["to_uncheck"]
    assert "直播间观看量" in plan["to_check"] and "千次观看成交金额" in plan["to_check"]


def test_plan_requires_gmv_in_dialog():
    """若弹窗扫描看不到 成交金额(gmv)行 → 拒绝自动确认,转 assist(不冒险破坏分子)。"""
    rows = [{"text": "直播间观看量", "checked": True},
            {"text": "点赞次数", "checked": True}]
    plan = watch_capture.plan_metric_changes(rows)
    assert plan["ok"] is False
    assert "成交金额" in plan.get("reason", "")


def test_plan_checked_unknown_rows_not_force_unchecked():
    """勾选态未知的未保留行:保守不强行取消(避免误关 gmv),转 assist 由人工确认。"""
    rows = [{"text": "直播间观看量", "checked": True},
            {"text": "成交金额", "checked": True},
            {"text": "离开人数", "checked": None}]  # 态未知
    plan = watch_capture.plan_metric_changes(rows)
    # 离开人数 不在保留集合但态未知 → ok=False,reason 提示需人工
    assert plan["ok"] is False
    assert "勾选态未知" in plan.get("reason", "") or "离开人数" in str(plan.get("reason", ""))


def _run_all():
    tests = [
        test_semantics_template_reflects_real_recheck,
        lambda: test_capture_watch_file_format_readable_by_daily(pathlib.Path(tempfile.mkdtemp())),
        test_day_publish_uses_watch_views_as_gpm_denominator,
        test_no_watch_file_degrades,
        test_fixture_cli_watchcnt_real_annotation_produces_hourly,
        test_plan_metric_changes_respects_max6_and_gmv_kept,
        test_plan_requires_gmv_in_dialog,
        test_plan_checked_unknown_rows_not_force_unchecked,
    ]
    for fn in tests:
        fn()
    return len(tests)


if __name__ == "__main__":
    n = _run_all()
    print(f"test_watch_capture_link: {n}/{n} passed")
