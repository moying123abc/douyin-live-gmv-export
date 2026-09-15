# -*- coding: utf-8 -*-
"""tests/test_daily_publish.py — 一键日报"两列表文件夹"发布离线回归。

运行:python tests/test_daily_publish.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from extractor import daily_stats  # noqa: E402
from exporter import to_csv_excel  # noqa: E402

ROWS = [
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:04", "gmv_min": 0.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 09:50", "gmv_min": 168.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 15:03", "gmv_min": 178.0},
    {"room_id": "r1", "session_date": "2026-09-04", "time": "09-04 19:00", "gmv_min": 0.0},
]


def _make_bundle(tmp: pathlib.Path):
    canonical = to_csv_excel.write_csv(tmp / "live_20260904_r1", ROWS)
    comp = to_csv_excel.write_compact_csv(
        tmp / "live_20260904_r1_compact", to_csv_excel.compact_rows(ROWS)
    )
    return canonical, comp


def test_publish_day_creates_two_col_files():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        canonical, comp = _make_bundle(tmp)
        folder = daily_stats.publish_day(tmp, "2026-09-04", [comp], [canonical])
        assert folder.name == "日报_20260904"
        deal = folder / "成交点_r1.csv"
        summ = folder / "日报汇总_20260904.csv"
        assert deal.is_file() and summ.is_file()
        lines = deal.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0] == "time,gmv_min"
        assert lines[1] == "09:50,168.0"
        assert lines[2] == "15:03,178.0"
        assert len(lines) == 3  # 表头 + 2 个非0成交点(0 值行已剔除)
        sum_lines = summ.read_text(encoding="utf-8-sig").splitlines()
        assert sum_lines[0].startswith("session_date,room_id")
        assert sum_lines[-1].startswith(daily_stats.TOTAL_LABEL + ",")
        assert sum_lines[-1].endswith("346.0")  # 168 + 178


def test_publish_skips_bad_compact_header():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        canonical, _ = _make_bundle(tmp)
        bad = tmp / "live_20260904_r2_compact.csv"
        bad.write_text("a,b\n1,2\n", encoding="utf-8-sig")
        folder = daily_stats.publish_day(tmp, "2026-09-04", [bad], [canonical])
        assert not (folder / "成交点_r2.csv").exists()
        assert (folder / "日报汇总_20260904.csv").exists()


def test_compact_files_pattern():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _make_bundle(tmp)
        files = daily_stats.compact_files(tmp, "2026-09-04")
        assert [p.name for p in files] == ["live_20260904_r1_compact.csv"]


def test_day_files_excludes_watch_min():
    """F1 修复:观看档(live_<ymd>_<room>_watch_min.csv)不得被按天发现规则捕获。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        _make_bundle(tmp)
        watch = tmp / "live_20260904_r1_watch_min.csv"
        watch.write_text("time,views\n09:00,10\n09:01,12\n", encoding="utf-8-sig")
        files = daily_stats.day_files(tmp, "2026-09-04")
        assert [p.name for p in files] == ["live_20260904_r1.csv"], (
            f"观看档不应被捕获,实际 {[p.name for p in files]}"
        )


def test_publish_day_with_gpm_enrich_three_cols():
    """成交点 3 列化:提供 gpm_by_room 时输出 time,gmv_min,gpm(所属小时 GPM),并写说明文件。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        canonical, comp = _make_bundle(tmp)
        gpm_map = {"r1": {"09:50": 3200.00, "15:03": None}}  # 15:03 小时 views=0 → None
        folder = daily_stats.publish_day(tmp, "2026-09-04", [comp], [canonical],
                                         gpm_by_room=gpm_map)
        deal = folder / "成交点_r1.csv"
        lines = deal.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0] == "time,gmv_min,gpm"
        assert "09:50,168.0,3200.00" in lines, lines
        row_1503 = next(ln for ln in lines[1:] if ln.startswith("15:03,"))
        assert row_1503.endswith(","), f"None 小时应留空,实际 {row_1503!r}"
        note = folder / "成交点_gpm_说明.txt"
        assert note.is_file()
        assert "gpm" in note.read_text(encoding="utf-8")


def test_publish_day_default_two_cols_unchanged():
    """未提供 gpm_by_room 时仍输出两列(旧路径不变)。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        canonical, comp = _make_bundle(tmp)
        folder = daily_stats.publish_day(tmp, "2026-09-04", [comp], [canonical])
        deal = folder / "成交点_r1.csv"
        lines = deal.read_text(encoding="utf-8-sig").splitlines()
        assert lines[0] == "time,gmv_min"
        assert not (folder / "成交点_gpm_说明.txt").exists()


if __name__ == "__main__":
    test_publish_day_creates_two_col_files()
    test_publish_skips_bad_compact_header()
    test_compact_files_pattern()
    test_day_files_excludes_watch_min()
    test_publish_day_with_gpm_enrich_three_cols()
    test_publish_day_default_two_cols_unchanged()
    print("test_daily_publish: 6/6 passed")
