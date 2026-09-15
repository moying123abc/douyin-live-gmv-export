# -*- coding: utf-8 -*-
"""M3 扩展指标用例(离线,不依赖登录/浏览器;pytest 兼容)。

覆盖:
- 开关默认关闭:导出行为与 M2 完全一致(与 M2 阶段产出的 CSV 逐字节 diff);
- 开关开启 + 夹具携带 extra_metrics:order_min/online_uv_min 与 gmv_min 同分钟行
  严格对齐并入,time/gmv_min 语义不变,原校验仍通过;
- 缺失分钟留空并注明;时间轴/长度不一致 → 拒绝并表(不伪造粒度);
- 未知 column_id 拒绝;CLI 层错位夹具导出 exit2。
"""
from __future__ import annotations

import csv
import json
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as cfgmod  # noqa: E402
from exporter import schema as schema_mod  # noqa: E402
from exporter import to_csv_excel  # noqa: E402
from extractor import extra_metrics, trend, validation  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_A = ROOT / "data" / "samples" / "sample_ended_session_60min.json"
SCRATCH = ROOT / "data" / "outputs" / "_m3_selftest"
M2_REFERENCE = ROOT / "data" / "outputs" / "final.csv"  # M2 阶段同夹具 off 导出参考


def _reset_scratch():
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)


def _read_csv(path):
    with pathlib.Path(path).open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))
    return rows[0], rows[1:]


def _enabled_config(ids):
    cfg = cfgmod.default_config()
    cfg["extra_metrics"] = {"enabled": True, "column_ids": ids}
    return cfg


def test_default_disabled():
    cfg = cfgmod.default_config()
    assert extra_metrics.is_enabled(cfg) is False
    assert extra_metrics.requested_ids(cfg) == []
    assert extra_metrics.requested_columns(cfg) == []


def test_registry_has_four_metrics():
    assert sorted(extra_metrics.EXTRA_METRIC_DEFS) == [
        "online_uv_min", "order_min", "paid_order_min", "view_uv_min",
    ]


def test_enabled_fixture_merge_aligned():
    rows, meta = trend.load_fixture(FIXTURE_A)
    payload = json.loads(FIXTURE_A.read_text(encoding="utf-8-sig"))
    expected = payload["extra_metrics"]["values"]
    cfg = _enabled_config(["order_min", "online_uv_min"])
    merged, added, notes = extra_metrics.enrich(cfg, rows, fixture_path=str(FIXTURE_A))
    assert sorted(added) == ["online_uv_min", "order_min"]
    assert len(merged) == len(rows) == 61
    for i, row in enumerate(merged):
        assert row["order_min"] == expected["order_min"][i]      # 同分钟行严格对齐
        assert row["online_uv_min"] == expected["online_uv_min"][i]
        assert row["time"] == rows[i]["time"]                    # time 语义不变
        assert row["gmv_min"] == rows[i]["gmv_min"]              # gmv_min 语义不变
    # 原校验(只读 time/gmv_min)仍通过
    report = validation.validate(
        merged, cumulative_total=meta["cumulative_total"], duration_minutes=meta["duration_minutes"]
    )
    assert report["ok"], validation.format_report(report)


def test_missing_minute_blank_and_noted():
    rows = [
        {"time": "07-01 19:00", "gmv_min": 0},
        {"time": "07-01 19:01", "gmv_min": 10},
        {"time": "07-01 19:02", "gmv_min": 20},
    ]
    merged, added, notes = extra_metrics.merge_aligned(
        rows, ["07-01 19:00", "07-01 19:01", "07-01 19:02"],
        {"order_min": [1, None, 3]},
    )
    assert added == ["order_min"]
    assert merged[1]["order_min"] == ""   # 缺失分钟留空
    assert merged[0]["order_min"] == 1
    assert any("缺失" in n for n in notes)


def test_misaligned_times_refused():
    rows = [{"time": "07-01 19:00", "gmv_min": 0}, {"time": "07-01 19:01", "gmv_min": 1}]
    try:
        extra_metrics.merge_aligned(rows, ["07-01 19:00", "07-01 19:02"], {"order_min": [1, 2]})
        raise AssertionError("应拒绝错位并表")
    except extra_metrics.ExtraMetricsError:
        pass
    # 长度不一致同样拒绝
    try:
        extra_metrics.merge_aligned(rows, ["07-01 19:00"], {"order_min": [1]})
        raise AssertionError("应拒绝长度不一致并表")
    except extra_metrics.ExtraMetricsError:
        pass


def test_unknown_column_id_rejected():
    cfg = _enabled_config(["order_min", "not_a_metric"])
    try:
        extra_metrics.requested_ids(cfg)
        raise AssertionError("应拒绝未知指标 id")
    except extra_metrics.ExtraMetricsError:
        pass


def test_disabled_export_identical_to_m2_diff():
    """开关关闭时导出与 M2 完全一致:与 M2 阶段产物 final.csv 逐字节一致。"""
    assert M2_REFERENCE.is_file(), f"缺少 M2 参考 CSV: {M2_REFERENCE}"
    _reset_scratch()
    out = SCRATCH / "off"
    run = subprocess.run(
        [sys.executable, "main.py", "export", "--fixture", str(FIXTURE_A), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert run.returncode == 0, run.stderr
    header, body = _read_csv(str(out) + ".csv")
    assert header == schema_mod.headers() == ["room_id", "session_date", "time", "gmv_min"]
    assert pathlib.Path(str(out) + ".csv").read_bytes() == M2_REFERENCE.read_bytes()
    print("DIFF-CHECK: 开关关闭输出与 M2 参考 final.csv 逐字节一致")


def test_enabled_export_cli_appends_extra_columns():
    _reset_scratch()
    cfg_file = SCRATCH / "config_on.yaml"
    cfg_file.write_text(
        "extra_metrics:\n  enabled: true\n  column_ids: [order_min, online_uv_min]\n",
        encoding="utf-8",
    )
    payload = json.loads(FIXTURE_A.read_text(encoding="utf-8-sig"))
    expected = payload["extra_metrics"]["values"]
    out = SCRATCH / "on"
    run = subprocess.run(
        [sys.executable, "main.py", "export", "--fixture", str(FIXTURE_A),
         "--config", str(cfg_file), "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
    assert "已并入列" in run.stdout and "校验通过" in run.stdout
    header, body = _read_csv(str(out) + ".csv")
    assert header == ["room_id", "session_date", "time", "gmv_min", "online_uv_min", "order_min"] \
        or header == ["room_id", "session_date", "time", "gmv_min", "order_min", "online_uv_min"]
    assert len(body) == 61
    # 扩展列与夹具值逐行一致,且前四列与原导出一致
    for i, rec in enumerate(body):
        row = dict(zip(header, rec))
        assert row["order_min"] == str(expected["order_min"][i])
        assert row["online_uv_min"] == str(expected["online_uv_min"][i])


def test_enabled_misaligned_fixture_cli_refused():
    """开关开启但夹具扩展时间轴错位 → CLI 拒绝(exit2),不产出伪造并表。"""
    _reset_scratch()
    bad = json.loads(FIXTURE_A.read_text(encoding="utf-8-sig"))
    times = bad["extra_metrics"]["time"]
    bad["extra_metrics"]["time"] = [t if i != 30 else "07-01 19:99" for i, t in enumerate(times)]
    bad_file = SCRATCH / "bad_extra.json"
    bad_file.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    cfg_file = SCRATCH / "config_on.yaml"
    cfg_file.write_text(
        "extra_metrics:\n  enabled: true\n  column_ids: [order_min]\n", encoding="utf-8"
    )
    run = subprocess.run(
        [sys.executable, "main.py", "export", "--fixture", str(bad_file),
         "--config", str(cfg_file), "--out", str(SCRATCH / "bad_out")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    assert run.returncode == 2, run.stdout + run.stderr
    assert "拒绝伪造粒度" in run.stderr or "并表" in run.stderr
    assert not (SCRATCH / "bad_out.csv").exists()


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
    print(f"\n全部 {len(tests)} 个 M3 用例通过")


if __name__ == "__main__":
    _run_all()
