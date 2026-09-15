# -*- coding: utf-8 -*-
"""tests/test_compact.py — 成交点精简表转换的离线回归(无第三方依赖,直接运行)。

运行:python tests/test_compact.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from exporter import to_csv_excel  # noqa: E402

ROWS = [
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:04", "gmv_min": 0.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:23", "gmv_min": 160.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:50", "gmv_min": 168.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:51", "gmv_min": 0.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "2026-09-04 15:03", "gmv_min": 178.0},
]


def test_compact_rows_shape_and_filter():
    comp = to_csv_excel.compact_rows(ROWS)
    assert len(comp) == 3, f"应只保留 3 个非0成交点,得到 {len(comp)}"
    assert comp[0] == {"time": "09:23", "gmv_min": 160.0}
    assert comp[1] == {"time": "09:50", "gmv_min": 168.0}
    # 完整日期前缀同样被剥成 HH:MM
    assert comp[2] == {"time": "15:03", "gmv_min": 178.0}
    total = sum(r["gmv_min"] for r in comp)
    assert abs(total - 506.0) < 1e-9, f"金额合计应守恒 506.0,得到 {total}"


def test_write_compact_csv():
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "x.csv"
        written = to_csv_excel.write_compact_csv(out, to_csv_excel.compact_rows(ROWS))
        text = written.read_text(encoding="utf-8-sig")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert lines[0] == "time,gmv_min"
        assert len(lines) == 4  # 表头 + 3 行
        assert "09-04 09:50" not in text and "09:50,168.0" in text


def test_bad_time_rejected():
    bad = [{"time": "not-a-time", "gmv_min": 1.0}]
    try:
        to_csv_excel.compact_rows(bad)
    except ValueError:
        return
    raise AssertionError("无法解析的 time 外观应被结构化拒绝")


def test_bad_amount_rejected():
    bad = [{"time": "09:50", "gmv_min": "abc"}]
    try:
        to_csv_excel.compact_rows(bad)
    except ValueError:
        return
    raise AssertionError("非数值 gmv_min 应被结构化拒绝")


if __name__ == "__main__":
    test_compact_rows_shape_and_filter()
    test_write_compact_csv()
    test_bad_time_rejected()
    test_bad_amount_rejected()
    print("test_compact: 4/4 passed")
