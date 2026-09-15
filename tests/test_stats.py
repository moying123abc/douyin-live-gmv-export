# -*- coding: utf-8 -*-
"""tests/test_stats.py — 按天汇总统计离线回归(无第三方依赖,直接运行)。

运行:python tests/test_stats.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from extractor import daily_stats  # noqa: E402
from exporter import to_csv_excel  # noqa: E402

ROWS_1 = [
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:04", "gmv_min": 0.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:50", "gmv_min": 168.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 15:03", "gmv_min": 178.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 19:00", "gmv_min": 0.0},
]
ROWS_2 = [
    {"room_id": "r2", "session_date": "2026-09-04", "time": "09-04 10:00", "gmv_min": 100.0},
    {"room_id": "r2", "session_date": "2026-09-04", "time": "09-04 10:01", "gmv_min": 200.5},
    {"room_id": "r2", "session_date": "2026-09-04", "time": "09-04 10:02", "gmv_min": 0.0},
]


def _make_day(tmp: pathlib.Path):
    f1 = to_csv_excel.write_csv(tmp / "live_20260904_r1", ROWS_1)
    f2 = to_csv_excel.write_csv(tmp / "live_20260904_r2", ROWS_2)
    return f1, f2


def test_stat_csv_single():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        f1, _ = _make_day(tmp)
        st = daily_stats.stat_csv(f1)
        assert st["start"] == "09-04 09:04" and st["end"] == "09-04 19:00"
        assert st["total_minutes"] == 4
        assert st["deal_minutes"] == 2
        assert abs(st["gmv_sum"] - 346.0) < 1e-9


def test_day_files_excludes_compact():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _make_day(tmp)
        to_csv_excel.write_compact_csv(
            tmp / "live_20260904_r1_compact",
            to_csv_excel.compact_rows(ROWS_1),
        )
        files = daily_stats.day_files(tmp, "2026-09-04")
        assert len(files) == 2, f"应只匹配 2 个标准量表,得到 {[p.name for p in files]}"
        assert all(not p.name.endswith("_compact.csv") for p in files)


def test_summarize_totals():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        files = list(_make_day(tmp))
        summary = daily_stats.summarize(files)
        t = summary["totals"]
        assert t["sessions"] == 2
        assert t["total_minutes"] == 7
        assert t["deal_minutes"] == 4
        assert abs(t["gmv_sum"] - 646.5) < 1e-9


def test_write_summary_csv_and_total_row():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        files = list(_make_day(tmp))
        summary = daily_stats.summarize(files)
        out = daily_stats.write_summary_csv(tmp / "s.csv", summary)
        lines = out.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0] == ",".join(daily_stats.SUMMARY_HEADER)
        assert len(lines) == 4  # 表头 + 2 场 + 合计行
        assert lines[-1].startswith(daily_stats.TOTAL_LABEL + ",")


if __name__ == "__main__":
    test_stat_csv_single()
    test_day_files_excludes_compact()
    test_summarize_totals()
    test_write_summary_csv_and_total_row()
    print("test_stats: 4/4 passed")
