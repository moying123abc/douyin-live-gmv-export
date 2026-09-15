# -*- coding: utf-8 -*-
"""T10 按天 sessions/export 离线测试(pytest 兼容)。

- 基于 T9 记录的真实 room/paged 响应(data/probe/t9_room_paged_raw.json)验证:
  东八区当日窗口构造、status=4 过滤、room_id/live_id/start_time/end_time 解析;
  文件不存在时回退内置合成样本(结构同真实响应),测试仍可离线运行;
- 导出文件名规范 live_<YYYYMMDD>_<room_id>.csv;
- _finalize_day_session(export --date 的逐场流水线)用夹具行离线跑通并通过校验;
- 校验失败 → 拒绝输出(exit code 3 语义),不产出文件。
"""
from __future__ import annotations

import csv
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as cfgmod  # noqa: E402
from extractor import sessions as sessions_mod  # noqa: E402
from extractor import trend  # noqa: E402
import main as main_mod  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "probe" / "t9_room_paged_raw.json"
SCRATCH = ROOT / "data" / "outputs" / "_t10_selftest"

# 内置合成回退样本(结构同真实响应;labeled synthetic)
_FALLBACK = {
    "09-01": [
        {"room_id": "7000000000000000003", "live_id": 1, "status": 4,
         "start_time": 1788221243, "end_time": 1788222050, "title": "示例直播间(合成演示)"},
        {"room_id": "7000000000000000004", "live_id": 1, "status": 3,  # 非 ended → 应被过滤
         "start_time": 1788222147, "end_time": 1788267635, "title": "ing"},
    ],
    "09-04": [
        {"room_id": "7000000000000000001", "live_id": 1, "status": 4,
         "start_time": 1788483861, "end_time": 1788519628, "title": "示例直播间(合成演示)"},
    ],
}


def _day_items(day):
    if RAW.is_file():
        data = json.loads(RAW.read_text(encoding="utf-8-sig"))
        return [r["item"] for r in data if r.get("day") == day]
    return _FALLBACK[day]


def test_day_range_epochs_cst():
    start, end = sessions_mod.day_range_epochs("2026-09-04")
    assert start == 1788451200, start   # 2026-09-04 00:00:00 CST
    assert end == 1788537599, end       # 2026-09-04 23:59:59 CST
    assert (end - start) == 86399
    try:
        sessions_mod.day_range_epochs("2026/09/04")
        raise AssertionError("非法日期应抛 ValueError")
    except ValueError:
        pass


def test_parse_real_payload_filter_and_fields():
    items = _day_items("09-04")
    rows = sessions_mod.parse_paged_rows(items, date_str="2026-09-04")
    assert len(rows) == 1, rows
    s = rows[0]
    # room_id 取自数据源本身(真实证据文件为本机本地数据、不入库;此处不断言具体 id)
    assert s["room_id"] == str(items[0]["room_id"]) and s["room_id"].isdigit()
    assert s["status"] == sessions_mod.STATUS_ENDED == 4
    assert s["start_cst"] == "2026-09-04 09:04:21"
    assert s["end_cst"] == "2026-09-04 19:00:28"
    assert s["start_epoch"] == 1788483861 and s["end_epoch"] == 1788519628


def test_parse_status_filter_and_strict_day():
    # 含 status=3(直播中)与另一日期的行 → 过滤
    mixed = _day_items("09-01") + [
        {"room_id": "x", "live_id": 1, "status": 4,
         "start_time": 1788483861 - 86400, "end_time": 1788519628, "title": "昨天"},
    ]
    rows = sessions_mod.parse_paged_rows(mixed, date_str="2026-09-01", strict_day=True)
    assert all(r["status"] == 4 for r in rows)
    assert all("昨天" not in r["title"] for r in rows)
    if RAW.is_file():
        # 真实 09-01 原始 3 条均为 status=4
        assert len(rows) == 3


def test_output_name():
    assert sessions_mod.session_output_name("2026-09-04", "7000000000000000001") \
        == "live_20260904_7000000000000000001.csv"
    assert sessions_mod.session_output_name("2026-09-04", "7000000000000000001", ".xlsx") \
        == "live_20260904_7000000000000000001.xlsx"


def test_finalize_day_session_writes_validated_csv_offline():
    """export --date 的逐场流水线:用夹具行离线跑通,产物通过校验且命名确定。"""
    fixture = ROOT / "data" / "samples" / "sample_ended_session_60min.json"
    rows, meta = trend.load_fixture(fixture)
    meta["room_id"] = "123456789"
    for row in rows:
        row["room_id"] = "123456789"
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    code = main_mod._finalize_day_session(
        cfgmod.default_config(), rows, meta, SCRATCH,
        "2026-09-04", "123456789", cumulative_override=meta["cumulative_total"],
    )
    assert code == 0
    csv_path = SCRATCH / "live_20260904_123456789.csv"
    assert csv_path.is_file()
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        table = list(csv.reader(fh))
    assert table[0] == ["room_id", "session_date", "time", "gmv_min"]
    assert len(table) - 1 == 61
    total = sum(float(r[3]) for r in table[1:])
    assert abs(total - float(meta["cumulative_total"])) < 1e-6


def test_finalize_day_session_refuses_bad_data():
    fixture = ROOT / "data" / "samples" / "sample_ended_session_60min.json"
    rows, meta = trend.load_fixture(fixture)
    meta["room_id"] = "999"
    for row in rows:
        row["room_id"] = "999"
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    code = main_mod._finalize_day_session(
        cfgmod.default_config(), rows, meta, SCRATCH,
        "2026-09-04", "999", cumulative_override=999999.0,  # 求和必失败
    )
    assert code == 3
    assert not (SCRATCH / "live_20260904_999.csv").exists()


def test_guidance_text_mentions_login_and_example():
    txt = sessions_mod._guidance_text("2026-09-04")
    assert "python main.py login" in txt
    assert "sessions --date 2026-09-04" in txt
    assert "export --date 2026-09-04" in txt


def _run_all():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures.append((name, exc))
            print(f"FAIL {name}: {exc}")
    if failures:
        print(f"\n{len(failures)}/{len(tests)} 用例失败")
        sys.exit(1)
    print(f"\n全部 {len(tests)} 个 T10 用例通过")


if __name__ == "__main__":
    _run_all()
