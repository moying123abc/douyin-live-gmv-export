# -*- coding: utf-8 -*-
"""按账号分目录的离线回归(不联网、不开浏览器)。

修复的真实缺陷(2026-11-16):
  产物目录原本是**扁平**的 `data/outputs/live_<日期>_<room_id>.csv` 与 `日报_<日期>/`,
  文件名里没有账号维度。后果有两层:
    1) 两个账号导出同一天 → 同一个 日报_<日期>/ 里混着两家店的数据;
    2) 更严重:`daily` 归集当天文件时只按日期 glob,会把**别的账号已导出的场次**
       也扫进来,等于把别人店铺的数据算进本次日报。

修复约定(本用例锁定):
  - 写入一律 `<export.out_dir>/<账号名>`(默认账号 = 子目录 `default`);
  - 读取优先账号目录;当天在那里没有文件时,才回落到旧版扁平目录(只读兜底,并打印提示);
  - 账号目录里已有文件时**不得**再回落,否则会重复计入历史文件。

运行:python tests/test_output_per_account.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import accounts as acctmod  # noqa: E402
import config as cfgmod  # noqa: E402
from extractor import daily_stats as D  # noqa: E402

DAY = "2026-10-23"
YMD = "20261023"


def _cfg(account="", out_dir="data/outputs"):
    return {"browser": {"account": account}, "export": {"out_dir": out_dir}}


def _write(dirpath: pathlib.Path, room: str, day_ymd: str = YMD):
    dirpath.mkdir(parents=True, exist_ok=True)
    p = dirpath / f"live_{day_ymd}_{room}.csv"
    p.write_text("time,gmv_min\n10-23 08:00,1\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# 1. 目录解析
# --------------------------------------------------------------------------
def test_export_root_is_per_account():
    root = pathlib.Path(cfgmod.PROJECT_ROOT) / "data" / "outputs"
    assert acctmod.export_root(_cfg()) == root / acctmod.DEFAULT_ACCOUNT
    assert acctmod.export_root(_cfg("1")) == root / "1"
    assert acctmod.export_root(_cfg("spa2")) == root / "spa2"


def test_export_root_name_overrides_cfg():
    """显式名字优先于 cfg(界面按下拉框传账号时用)。"""
    assert acctmod.export_root(_cfg("1"), name="spa2").name == "spa2"


def test_export_root_respects_absolute_out_dir():
    with tempfile.TemporaryDirectory() as td:
        got = acctmod.export_root(_cfg("1", out_dir=td))
        assert got == pathlib.Path(td) / "1"


def test_legacy_root_is_the_flat_dir():
    root = pathlib.Path(cfgmod.PROJECT_ROOT) / "data" / "outputs"
    assert acctmod.legacy_export_root(_cfg("1")) == root


# --------------------------------------------------------------------------
# 2. 两个账号互不串台(核心回归)
# --------------------------------------------------------------------------
def test_two_accounts_do_not_see_each_other():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        _write(base / "A", "7000000000000000001")
        _write(base / "A", "7000000000000000002")
        _write(base / "B", "7000000000000000003")
        a_files = [p.name for p in D.day_files(base / "A", DAY)]
        b_files = [p.name for p in D.day_files(base / "B", DAY)]
        assert len(a_files) == 2 and len(b_files) == 1, (a_files, b_files)
        assert "7000000000000000003" not in "".join(a_files), "A 不应看到 B 的文件"
        assert "7000000000000000001" not in "".join(b_files), "B 不应看到 A 的文件"


def test_same_room_in_two_accounts_does_not_collide():
    """同一 room_id 在两个账号下各存一份,互不覆盖。"""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        pa = _write(base / "A", "7000000000000000001")
        pb = _write(base / "B", "7000000000000000001")
        assert pa != pb and pa.exists() and pb.exists()


# --------------------------------------------------------------------------
# 3. 旧版扁平目录的读兜底
# --------------------------------------------------------------------------
def test_fallback_when_account_dir_empty():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        _write(base, "7000000000000000009")           # 旧版遗留:在扁平根目录
        got = D.day_files(base / "1", DAY, fallback_dir=base)
        assert [p.name for p in got] == [f"live_{YMD}_7000000000000000009.csv"]


def test_no_fallback_when_account_dir_has_files():
    """账号目录已有当天文件 → 不得再回落,否则会把历史文件重复计入。"""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        _write(base, "7000000000000000009")           # 旧文件
        _write(base / "1", "7000000000000000001")     # 新文件
        got = D.day_files(base / "1", DAY, fallback_dir=base)
        assert [p.name for p in got] == [f"live_{YMD}_7000000000000000001.csv"]


def test_no_fallback_for_other_days():
    """旧目录里不是当天的文件,不能被兜底捡进来。"""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        _write(base, "7000000000000000009", day_ymd="20261001")
        assert D.day_files(base / "1", DAY, fallback_dir=base) == []


def test_fallback_ignores_compact_and_watch_files():
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        (base / f"live_{YMD}_7000000000000000001_compact.csv").write_text("x\n", encoding="utf-8")
        (base / f"live_{YMD}_7000000000000000001_watch_min.csv").write_text("x\n", encoding="utf-8")
        assert D.day_files(base / "1", DAY, fallback_dir=base) == []


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
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
    print(f"\n全部 {len(tests)} 个按账号分目录用例通过")


if __name__ == "__main__":
    _run_all()
