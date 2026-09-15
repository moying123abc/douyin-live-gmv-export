# -*- coding: utf-8 -*-
"""tests/test_hourly_gpm_export.py — 小时 GPM 联动导出(daily/export --fixture)离线回归。

运行:python tests/test_hourly_gpm_export.py
覆盖:
 1. export --fixture(夹具带 gpm_watch)→ 产出 <主>_hourly.csv(列 hour/views/gmv/gpm/note/
    watch_semantics),主 CSV(M2 四列)不受影响;
 2. 夹具不带/带无效 gpm_watch → 明确跳过提示,rc=0,不产出 _hourly.csv(不阻断);
 3. daily 联动:无观看档 → 只写 小时GPM_<ymd>_说明.txt(降级说明);
 4. daily 联动:多场同日 + 每场 _watch_min.csv → 小时GPM_<ymd>.csv 跨场按小时叠加
    views/gmv 再算 GPM(非均值),views=0 桶留空标注;
 5. 各场观看口径(semantics)声明不一致 → 拒绝强行叠加(降级说明)。
"""
import csv
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from exporter import to_csv_excel  # noqa: E402
from exporter import hourly_gpm_output as hgo  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
SCRATCH = ROOT / "data" / "outputs" / "_t3_selftest"
FIXTURE_GPM = SAMPLES / "sample_ended_session_gpm.json"      # 61 分钟行 + gpm_watch
FIXTURE_PLAIN = SAMPLES / "sample_ended_session_60min.json"   # 无 gpm_watch

SEM_S = "口径声明(测试):分钟观看序列语义与 GMV 同轴(合成演示,非在线对拍值)"


def _reset_scratch():
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)


def _run_export(fixture, out):
    return subprocess.run(
        [sys.executable, "main.py", "export", "--fixture", str(fixture),
         "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )


def _read_csv(path):
    with pathlib.Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        table = list(reader)
    return table[0], table[1:]


# ---------------------------------------------------------------------------
def test_fixture_with_watch_produces_hourly_and_base_intact():
    _reset_scratch()
    out = SCRATCH / "gpm_out"
    res = _run_export(FIXTURE_GPM, out)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert "小时 GPM 已生成" in res.stdout
    base = SCRATCH / "gpm_out.csv"
    hourly = SCRATCH / "gpm_out_hourly.csv"
    assert base.is_file() and hourly.is_file()
    # 主 CSV:仍是 M2 四列、61 行(联动不影响既有语义)
    header, body = _read_csv(base)
    assert header == ["room_id", "session_date", "time", "gmv_min"]
    assert len(body) == 61
    # 小时 GPM CSV
    hheader, hbody = _read_csv(hourly)
    assert hheader == ["hour", "views", "gmv", "gpm", "note", "watch_semantics"]
    assert len(hbody) == 2  # 19:00 与 20:00 两个整点小时
    row19 = dict(zip(hheader, hbody[0]))
    assert row19["hour"] == "2025-07-01 19:00"
    assert row19["views"] == "180" and row19["gmv"] == "2001.0"
    assert row19["gpm"] == "11116.67"  # 2001/180*1000 两位小数
    assert "演示夹具声明口径" in row19["watch_semantics"]
    row20 = dict(zip(hheader, hbody[1]))
    assert row20["hour"] == "2025-07-01 20:00"
    assert row20["gpm"] == "" and "views=0" in row20["note"]  # views=0 留空标注


def test_fixture_without_watch_skips_cleanly():
    _reset_scratch()
    res = _run_export(FIXTURE_PLAIN, SCRATCH / "plain")
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert "小时 GPM" in res.stdout and "跳过" in res.stdout
    assert not (SCRATCH / "plain_hourly.csv").exists()


def test_fixture_invalid_watch_skips_cleanly():
    _reset_scratch()
    payload = json.loads(FIXTURE_GPM.read_text(encoding="utf-8-sig"))
    payload["gpm_watch"] = {"level": "minute", "rows": []}
    bad = SCRATCH / "bad_watch.json"
    bad.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    res = _run_export(bad, SCRATCH / "badw")
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert "小时 GPM" in res.stdout and "跳过" in res.stdout
    assert not (SCRATCH / "badw_hourly.csv").exists()


# ---------------------------------------------------------------------------
# daily 联动:纯本地文件(不经 CLI,便于精确断言)
# ---------------------------------------------------------------------------
ROWS_R1 = [
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:04", "gmv_min": 160.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:23", "gmv_min": 168.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 10:45", "gmv_min": 40.0},
]
ROWS_R2 = [
    {"room_id": "r2", "session_date": "2026-09-04", "time": "09-04 09:05", "gmv_min": 20.0},
    {"room_id": "r2", "session_date": "2026-09-04", "time": "09-04 11:00", "gmv_min": 7.0},
]


def _session_bundle(tmp: pathlib.Path, rows, room: str, date="20260904"):
    canonical = to_csv_excel.write_csv(tmp / f"live_{date}_{room}", rows)
    comp = to_csv_excel.write_compact_csv(
        tmp / f"live_{date}_{room}_compact", to_csv_excel.compact_rows(rows))
    return canonical, comp


def _watch_file(tmp: pathlib.Path, room: str, minutes, semantics: str, date="20260904"):
    p = tmp / f"live_{date}_{room}_watch_min.csv"
    with p.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "views", "semantics"])
        for time_v, views in minutes:
            w.writerow([time_v, views, semantics])
    return p


def test_day_publish_degrades_when_no_watch():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _session_bundle(tmp, ROWS_R1, "r1")
        _session_bundle(tmp, ROWS_R2, "r2")
        folder = tmp / "日报_20260904"
        res = hgo.publish_day_hourly(tmp, "2026-09-04", folder)
        assert res["ok"] is False
        assert not (folder / "小时GPM_20260904.csv").exists()
        note = folder / "小时GPM_20260904_说明.txt"
        assert note.is_file()
        assert "观看" in note.read_text(encoding="utf-8-sig") or "降级" in note.read_text(encoding="utf-8-sig")


def test_day_publish_merges_sessions_and_recomputes_gpm():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _session_bundle(tmp, ROWS_R1, "r1")
        _session_bundle(tmp, ROWS_R2, "r2")
        # r1 观看:09:04=80、09:23=80(09 桶 160),10:45=10
        _watch_file(tmp, "r1", [("09-04 09:04", 80), ("09-04 09:23", 80), ("09-04 10:45", 10)], SEM_S)
        # r2 观看:09:05=40(09 桶 40),11:00=14
        _watch_file(tmp, "r2", [("09-04 09:05", 40), ("09-04 11:00", 14)], SEM_S)
        folder = tmp / "日报_20260904"
        res = hgo.publish_day_hourly(tmp, "2026-09-04", folder)
        assert res["ok"] is True, res
        csv_path = folder / "小时GPM_20260904.csv"
        assert csv_path.is_file() and res["csv"] == str(csv_path)
        header, body = _read_csv(csv_path)
        assert header == ["hour", "views", "gmv", "gpm", "note", "watch_semantics"]
        rows = [dict(zip(header, r)) for r in body]
        hours = [r["hour"] for r in rows]
        assert hours == ["2026-09-04 09:00", "2026-09-04 10:00", "2026-09-04 11:00"], hours
        # 09 桶跨场叠加:gmv 160+168+20=348;views 80+80+40=200 → gpm=1740.0(非均值)
        row09 = rows[0]
        assert row09["views"] == "200" and row09["gmv"] == "348.0"
        assert row09["gpm"] == "1740.0"
        assert row09["watch_semantics"] == SEM_S
        assert rows[1]["views"] == "10" and rows[1]["gmv"] == "40.0"
        assert rows[1]["gpm"] == "4000.0"
        assert rows[2]["views"] == "14" and rows[2]["gmv"] == "7.0"
        assert rows[2]["gpm"] == "500.0"
        # 说明文件包含关键信息
        note_text = (folder / "小时GPM_20260904_说明.txt").read_text(encoding="utf-8-sig")
        assert "GMV 合计 395.00" in note_text and "views 合计 224" in note_text


def test_day_publish_rejects_semantics_mismatch():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _session_bundle(tmp, ROWS_R1, "r1")
        _session_bundle(tmp, ROWS_R2, "r2")
        _watch_file(tmp, "r1", [("09-04 09:04", 80)], SEM_S)
        _watch_file(tmp, "r2", [("09-04 09:05", 40)], SEM_S + "(口径B)")
        folder = tmp / "日报_20260904"
        res = hgo.publish_day_hourly(tmp, "2026-09-04", folder)
        assert res["ok"] is False
        assert not (folder / "小时GPM_20260904.csv").exists()
        text = (folder / "小时GPM_20260904_说明.txt").read_text(encoding="utf-8-sig")
        assert "不一致" in text


def _watch_semantics_with_closure(sumv, card, diff):
    """模拟 watch_capture.SEMANTICS_TEMPLATE 生成的含复核差数值的语义文本。"""
    return ("直播间观看量(直播间看播量)分钟序列;dataKey=WatchCntTrend,来自 room_minute_indicator;"
            f"整场 Σ={sumv} vs 同刻 key_index.ServerWatchCntTd={card}(diff={diff});复核差如实标注,"
            "未冒充精确闭合;小时 GPM 分母采用本序列并附复核差说明。")


def test_day_publish_merges_when_only_closure_numbers_differ():
    """各场复核差数值(Σ/ServerWatchCntTd/diff)不同但口径身份一致 → 允许跨场叠加。

    真实捕获的观看档 semantics 内含每场自己的复核差数值;此前整串比对导致同日
    多场永远被判"口径声明不一致"而降级。修复后应以归一化口径身份判定(见 F1 回归)。
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _session_bundle(tmp, ROWS_R1, "r1")
        _session_bundle(tmp, ROWS_R2, "r2")
        _watch_file(tmp, "r1", [("09-04 09:04", 80), ("09-04 09:23", 80),
                                ("09-04 10:45", 10)],
                    _watch_semantics_with_closure(494.0, 501.0, -7.0))
        _watch_file(tmp, "r2", [("09-04 09:05", 40), ("09-04 11:00", 14)],
                    _watch_semantics_with_closure(880.0, 882.0, -2.0))
        folder = tmp / "日报_20260904"
        res = hgo.publish_day_hourly(tmp, "2026-09-04", folder)
        assert res["ok"] is True, res
        csv_path = folder / "小时GPM_20260904.csv"
        assert csv_path.is_file(), "复核差不同但口径一致的两场应叠加生成小时 GPM 表"
        header, body = _read_csv(csv_path)
        rows = [dict(zip(header, r)) for r in body]
        row09 = rows[0]
        # 09 桶跨场叠加不变:gmv 348.0;views 200 → gpm 1740.0
        assert row09["views"] == "200" and row09["gmv"] == "348.0"
        assert row09["gpm"] == "1740.0"
        # watch_semantics 不带单场复核差数值(不冒充单一闭合)
        assert "494.0" not in row09["watch_semantics"]
        note_text = (folder / "小时GPM_20260904_说明.txt").read_text(encoding="utf-8-sig")
        assert "复核差" in note_text and "494.0" in note_text  # 各场复核差列入说明


def test_zero_views_bucket_annotated_no_nan():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        rows = [
            {"room_id": "r3", "session_date": "2026-09-04", "time": "09-04 08:05", "gmv_min": 120.0},
            {"room_id": "r3", "session_date": "2026-09-04", "time": "09-04 09:05", "gmv_min": 90.0},
        ]
        _session_bundle(tmp, rows, "r3")
        _watch_file(tmp, "r3", [("09-04 08:05", 0)], SEM_S)  # 08 桶 views=0;09 桶无观看档行→views=0
        folder = tmp / "日报_20260904"
        res = hgo.publish_day_hourly(tmp, "2026-09-04", folder)
        assert res["ok"] is True, res
        header, body = _read_csv(folder / "小时GPM_20260904.csv")
        rows_out = [dict(zip(header, r)) for r in body]
        text = (folder / "小时GPM_20260904.csv").read_text(encoding="utf-8-sig").lower()
        assert "nan" not in text and "inf" not in text
        assert all(r["gpm"] == "" for r in rows_out)  # 两个桶 views=0 → GPM 全部留空
        assert all("views=0" in r["note"] for r in rows_out)


# ---------------------------------------------------------------------------
# F1 修复全链路回归:观看档与分钟量表同目录时,cmd_daily/cmd_stats 不得被
# *_watch_min.csv 干扰(含"有观看档但无 _compact"场景)→ 日报正常、小时 GPM 表
# 正常生成、说明/统计无伪场次。
# ---------------------------------------------------------------------------
def _write_cfg(tmp: pathlib.Path, out_dir: pathlib.Path) -> pathlib.Path:
    cfg = tmp / "config_chain.yaml"
    cfg.write_text(f'export:\n  out_dir: "{out_dir.as_posix()}"\n', encoding="utf-8")
    return cfg


def _run_main(args_list):
    return subprocess.run([sys.executable, "main.py"] + args_list,
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def test_f1_chain_cmd_daily_stats_with_watch_present():
    """观看档落盘(含无 _compact 场次)不再使 cmd_daily/cmd_stats 失败或列伪场次。"""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        outs = base / "outs"
        outs.mkdir()
        # r1:canonical + watch_min,但无 _compact(cmd_daily 需离线补精简表分支)
        _session_bundle_no_compact(outs, ROWS_R1, "r1")
        _watch_file(outs, "r1", [("09-04 09:04", 80), ("09-04 09:23", 80),
                                 ("09-04 10:45", 10)], SEM_S)
        # r2:canonical + _compact + watch_min(已备齐路径)
        _session_bundle(outs, ROWS_R2, "r2")
        _watch_file(outs, "r2", [("09-04 09:05", 40), ("09-04 11:00", 14)], SEM_S)
        # r3:canonical + _compact,无观看档(日报/统计仍应含该场,小时 GPM 跳过该场)
        _session_bundle(outs, [
            {"room_id": "r3", "session_date": "2026-09-04", "time": "09-04 12:00", "gmv_min": 5.0},
        ], "r3")
        cfg = _write_cfg(base, outs)

        # cmd_daily:rc=0、日报三件套、小时 GPM csv 生成、说明无伪场次
        # (离线用例须 --no-capture-watch:r3 无观看档,默认自动捕获会尝试开真机浏览器)
        run = _run_main(["daily", "--date", "2026-09-04", "--config", str(cfg),
                         "--no-open", "--no-capture-watch"])
        assert run.returncode == 0, f"{run.stdout}\n{run.stderr}"
        folder = outs / "日报_20260904"
        assert (folder / "成交点_r1.csv").is_file()          # 无 _compact 场次被离线补出
        assert (folder / "成交点_r2.csv").is_file()
        assert (folder / "成交点_r3.csv").is_file()
        hourly = folder / "小时GPM_20260904.csv"
        assert hourly.is_file(), "有观看档场次应生成小时 GPM 表"
        text = hourly.read_text(encoding="utf-8-sig").lower()
        assert "nan" not in text and "inf" not in text
        # 说明文件:r1/r2 计入,r3 因无观看档跳过;不得出现 *_watch_min 伪场次
        note = (folder / "小时GPM_20260904_说明.txt").read_text(encoding="utf-8-sig")
        assert "r1" in note and "r2" in note and "- r3" in note
        assert all("_watch_min" not in ln for ln in note.splitlines()
                   if ln.startswith("- ")), note

        # cmd_stats:rc=0、统计含 r1/r2/r3、不含伪场次
        stats = _run_main(["stats", "--date", "2026-09-04", "--config", str(cfg),
                           "--out", str(base / "stats.csv")])
        assert stats.returncode == 0, f"{stats.stdout}\n{stats.stderr}"
        sbody = (base / "stats.csv").read_text(encoding="utf-8-sig")
        assert "r1" in sbody and "r2" in sbody and "r3" in sbody, sbody
        assert "_watch_min" not in sbody


def _session_bundle_no_compact(tmp: pathlib.Path, rows, room: str, date="20260904"):
    """只写标准分钟量表(不写 _compact),复现 cmd_daily 补精简表离线分支。"""
    return to_csv_excel.write_csv(tmp / f"live_{date}_{room}", rows)


# ---------------------------------------------------------------------------
def _run_all():
    tests = [
        test_fixture_with_watch_produces_hourly_and_base_intact,
        test_fixture_without_watch_skips_cleanly,
        test_fixture_invalid_watch_skips_cleanly,
        test_day_publish_degrades_when_no_watch,
        test_day_publish_merges_sessions_and_recomputes_gpm,
        test_day_publish_rejects_semantics_mismatch,
        test_day_publish_merges_when_only_closure_numbers_differ,
        test_zero_views_bucket_annotated_no_nan,
        test_f1_chain_cmd_daily_stats_with_watch_present,
    ]
    for fn in tests:
        fn()
    return len(tests)


if __name__ == "__main__":
    n = _run_all()
    print(f"test_hourly_gpm_export: {n}/{n} passed")
