# -*- coding: utf-8 -*-
"""M2 夹具导出回归 + 负向校验用例(离线,不依赖登录/浏览器)。

等价自检入口:
    python tests/test_m2_export.py        # 直接运行全部 test_* 函数
    python -m pytest tests                # 如已安装 pytest 亦可

覆盖:
- 两个离线夹具(MM-DD HH:MM 与 YYYY-MM-DD HH:MM)完整导出并通过三项校验;
- CSV 表头/行数与夹具一致;金额为 0 的分钟保留;
- 负向用例:求和不符 / 时间跳变 / 行数不符 / 时间重复 都能被校验拦截报错;
- schema 表头与 to_csv_excel 输出一致。
"""
from __future__ import annotations

import csv
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from exporter import schema as schema_mod  # noqa: E402
from exporter import to_csv_excel  # noqa: E402
from extractor import trend, validation  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
SCRATCH = ROOT / "data" / "outputs" / "_m2_selftest"
FIXTURE_A = SAMPLES / "sample_ended_session_60min.json"
FIXTURE_B = SAMPLES / "sample_ended_session_30min_fullfmt.json"


def _reset_scratch():
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)


def _read_csv(path) -> tuple:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    return rows[0], rows[1:]  # header, data rows


def test_fixture_60min_full_export():
    rows, meta = trend.load_fixture(FIXTURE_A)
    assert len(rows) == 61
    assert meta["room_id"] == "123456789"
    assert meta["session_date"] == "2025-07-01"
    assert abs(meta["cumulative_total"] - round(sum(r["gmv_min"] for r in rows), 2)) < 1e-6
    assert meta["duration_minutes"] == 60
    report = validation.validate(
        rows, cumulative_total=meta["cumulative_total"], duration_minutes=meta["duration_minutes"]
    )
    assert report["ok"], validation.format_report(report)

    _reset_scratch()
    csv_path = to_csv_excel.write_csv(SCRATCH / "out_60min", rows)
    header, data = _read_csv(csv_path)
    assert header == schema_mod.headers() == ["room_id", "session_date", "time", "gmv_min"]
    assert len(data) == 61
    assert all(len(row) == 4 for row in data)


def test_fixture_30min_fullfmt_export():
    rows, meta = trend.load_fixture(FIXTURE_B)
    assert len(rows) == 31
    assert meta["session_date"] == "2025-07-02"
    report = validation.validate(
        rows, cumulative_total=meta["cumulative_total"], duration_minutes=meta["duration_minutes"]
    )
    assert report["ok"], validation.format_report(report)


def test_zero_minutes_preserved():
    rows, _meta = trend.load_fixture(FIXTURE_A)
    zero_times = sorted(r["time"] for r in rows if float(r["gmv_min"]) == 0.0)
    assert len(zero_times) == 4, zero_times  # 00/07/30/60 分钟
    _reset_scratch()
    csv_path = to_csv_excel.write_csv(SCRATCH / "out_zero", rows)
    _header, data = _read_csv(csv_path)
    data_rows = [dict(zip(["room_id", "session_date", "time", "gmv_min"], r)) for r in data]
    zeros_in_csv = [r["time"] for r in data_rows if float(r["gmv_min"]) == 0.0]
    assert sorted(zeros_in_csv) == zero_times


def test_negative_sum_mismatch_fails():
    rows, meta = trend.load_fixture(FIXTURE_A)
    report = validation.validate(rows, cumulative_total=999999.0, duration_minutes=meta["duration_minutes"])
    assert not report["ok"]
    names = {c["name"] for c in report["checks"] if not c.get("ok")}
    assert "sum_against_cumulative" in names
    try:
        validation.assert_valid(report)
        raise AssertionError("应抛出 ValidationError")
    except validation.ValidationError as exc:
        assert "累计" in str(exc) or "之和" in str(exc)


def test_negative_time_gap_fails():
    rows, meta = trend.load_fixture(FIXTURE_A)
    broken = [r for i, r in enumerate(rows) if i != 30]  # 挖掉一个中间分钟 → 时间跳变 2 分钟
    report = validation.validate(broken, cumulative_total=meta["cumulative_total"])
    assert not report["ok"]
    names = {c["name"] for c in report["checks"] if not c.get("ok")}
    assert "time_axis" in names
    assert "row_count" in names  # 行数也必然减少


def test_negative_duplicate_time_fails():
    rows, _meta = trend.load_fixture(FIXTURE_A)
    dup = [dict(rows[0]), *rows]  # 首行重复 → 未递增
    report = validation.validate(dup, cumulative_total=None)
    assert not report["ok"]
    names = {c["name"] for c in report["checks"] if not c.get("ok")}
    assert "time_axis" in names


def test_negative_row_count_mismatch_fails():
    rows, meta = trend.load_fixture(FIXTURE_B)
    # 声称 31 分钟(期望 32 行),实际 31 行 → 行数校验失败
    report = validation.validate(rows, cumulative_total=meta["cumulative_total"], duration_minutes=31)
    assert not report["ok"]
    names = {c["name"] for c in report["checks"] if not c.get("ok")}
    assert "row_count" in names


def test_missing_cumulative_is_skipped_not_faked():
    rows, _meta = trend.load_fixture(FIXTURE_A)
    report = validation.validate(rows, cumulative_total=None, duration_minutes=60)
    assert report["ok"]  # 其余校验通过
    skipped = [c for c in report["checks"] if c.get("skipped")]
    assert skipped and skipped[0]["name"] == "sum_against_cumulative"


def test_fixture_rejects_sample_only_rows():
    # 只有 row_sample(样例)而没有整场 rows → 必须报错,不允许以样例冒充整场
    bad = {
        "schema_version": "1.0",
        "probe_meta": {"room_id": "1"},
        "layer_hit": "L1",
        "rows_observed": 100,
        "row_sample": [{"time": "07-01 19:00", "gmv_min": 1}],
    }
    p = SCRATCH / "sample_only.json"
    _reset_scratch()
    p.write_text(__import__("json").dumps(bad), encoding="utf-8")
    try:
        trend.load_fixture(p)
        raise AssertionError("应拒绝只有样例行的夹具")
    except trend.TrendUnavailable:
        pass


def test_negative_mixed_time_representation_rejected_not_crash():
    """负向:MM-DD 字符串(naive)与 epoch 秒(aware)混用 → 干净拒绝,而非 TypeError 崩溃。

    (t6 repair 回归:naive/aware 混用必须作为 time_axis 失败记录明细,绝不外抛)
    """
    rows, _meta = trend.load_fixture(FIXTURE_A)
    mixed = [dict(r) for r in rows]
    mixed[30]["time"] = 1751365800  # 任意 epoch 秒(aware),与 MM-DD 字符串(naive)混用
    report = validation.validate(mixed, cumulative_total=None)  # 不应抛 TypeError
    assert not report["ok"]
    time_axis = next(c for c in report["checks"] if c["name"] == "time_axis")
    assert "时间外观混用" in time_axis["detail"] or "不可比较" in time_axis["detail"], time_axis["detail"]
    try:
        validation.assert_valid(report)
        raise AssertionError("应抛 ValidationError")
    except validation.ValidationError:
        pass


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
    print(f"\n全部 {len(tests)} 个 M2 用例通过")


if __name__ == "__main__":
    _run_all()
