# -*- coding: utf-8 -*-
"""tests/test_export_day_resilience.py — export --date 逐场容错(跳过不中断)回归。

场景:某天 2 场,第 1 场在线无整场序列(TrendUnavailable),第 2 场正常。
预期:不再整体失败——成功导出第 2 场,rc=0,skipped 记录第 1 场。
运行:python tests/test_export_day_resilience.py
"""
import datetime as dt
import pathlib
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import main as main_mod  # noqa: E402
from extractor import sessions as sessions_mod  # noqa: E402
from extractor import trend  # noqa: E402

BAD_SESSION = {
    "room_id": "bad_room_1min", "title": "短场",
    "start_cst": "2026-09-02 11:14:59", "end_cst": "2026-09-02 11:15:48",
    "duration_min": 1,
}
OK_SESSION = {
    "room_id": "ok_room_main", "title": "主场",
    "start_cst": "2026-09-02 11:16:00", "end_cst": "2026-09-02 19:00:00",
    "duration_min": 464,
}
OK_ROWS = [
    {"room_id": "ok_room_main", "session_date": "2026-09-02",
     "time": "09-02 11:16", "gmv_min": 0.0},
    {"room_id": "ok_room_main", "session_date": "2026-09-02",
     "time": "09-02 11:17", "gmv_min": 0.0},
]
OK_META = {"cumulative_total": 0.0, "session_date": "2026-09-02",
           "duration_minutes": None, "time_format": "MM-DD HH:MM"}


def test_export_day_skips_bad_and_continues():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        cfg = {"export": {"out_dir": str(tmp)}, "validation": {}}

        def fake_read_live(cfg_, room):
            if room == BAD_SESSION["room_id"]:
                raise trend.TrendUnavailable("L0/L1 均未命中整场序列(测试夹具)")
            return OK_ROWS, OK_META

        with mock.patch.object(sessions_mod, "fetch_day_sessions",
                               return_value=[BAD_SESSION, OK_SESSION]), \
             mock.patch.object(trend, "read_live", side_effect=fake_read_live):
            code, info = main_mod._export_day(cfg, dt.date(2026, 9, 2))

        assert code == 0, f"至少成功一场应返回 0,实际 {code}"
        assert info["exported"] == ["ok_room_main"]
        assert len(info["skipped"]) == 1
        assert info["skipped"][0]["room_id"] == "bad_room_1min"
        assert "no_series" in info["skipped"][0]["reason"]
        ok_file = tmp / "live_20260902_ok_room_main.csv"
        bad_file = tmp / "live_20260902_bad_room_1min.csv"
        assert ok_file.is_file(), "主场应被成功导出"
        assert not bad_file.exists(), "被跳过场次不应产出文件"


def test_export_day_all_failed_returns_2():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        cfg = {"export": {"out_dir": str(tmp)}, "validation": {}}

        def fake_read_live(cfg_, room):
            raise trend.TrendUnavailable("无数据(测试夹具)")

        with mock.patch.object(sessions_mod, "fetch_day_sessions",
                               return_value=[BAD_SESSION]), \
             mock.patch.object(trend, "read_live", side_effect=fake_read_live):
            code, info = main_mod._export_day(cfg, dt.date(2026, 9, 2))
        assert code == 2
        assert info["exported"] == []
        assert len(info["skipped"]) == 1


if __name__ == "__main__":
    test_export_day_skips_bad_and_continues()
    test_export_day_all_failed_returns_2()
    print("test_export_day_resilience: 2/2 passed")
