# -*- coding: utf-8 -*-
"""daily 多日期日报离线回归(不联网、不打开浏览器、不需要登录)。

覆盖:
  1. 空格分隔多个日期 -> 每天各一个 日报_<YYYYMMDD> 文件夹;
  2. 逗号分隔 / 重复传 --date 两种写法等价;
  3. 单日期回归:行为与旧版一致(且不打印多日汇总);
  4. 重复日期去重(不重复做两遍);
  5. 含非法日期 -> 整批拒绝(退出码 2),不产生任何"半截"成果;
  6. 混合场景:某天失败不中断其他天,退出码取第一个失败日的码;
  7. _parse_daily_dates 纯函数契约(各种写法/中文逗号/去重/None->今天)。

运行:python tests/test_daily_multi_date.py
"""
import csv
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as main_mod  # noqa: E402

GOOD_ROWS = (
    "room_id,session_date,time,gmv_min\n"
    "7000000000000000001,{d},09-01 09:00,0.0\n"
    "7000000000000000001,{d},09-01 09:01,160.0\n"
    "7000000000000000001,{d},09-01 10:00,40.0\n"
)
BAD_ROWS = "a,b\n1,2\n"  # 结构非法:精简表生成应被拒绝(退出码 3)


def _seed(outs: pathlib.Path, date_dash: str, rows: str = GOOD_ROWS):
    ymd = date_dash.replace("-", "")
    p = outs / f"live_{ymd}_7000000000000000001.csv"
    p.write_text(rows.format(d=date_dash), encoding="utf-8-sig")
    return p


def _cfg(tmp: pathlib.Path, outs: pathlib.Path) -> pathlib.Path:
    cfg = tmp / "config.yaml"
    cfg.write_text('export:\n  out_dir: "' + outs.as_posix() + '"\n', encoding="utf-8")
    return cfg


def _run(argv):
    return subprocess.run([sys.executable, "main.py"] + argv, cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _daily_args(cfg, *dates):
    """按"空格分隔多日期"构造 argv。"""
    return ["daily", "--date", *dates, "--config", str(cfg),
            "--no-open", "--no-capture-watch"]


def _report_dirs(outs: pathlib.Path):
    return sorted(p.name for p in outs.glob("日报_*") if p.is_dir())


def _assert_report_complete(folder: pathlib.Path):
    assert folder.is_dir(), f"缺少日报目录: {folder}"
    names = {p.name for p in folder.iterdir()}
    assert any(n.startswith("成交点_") for n in names), f"缺少成交点表: {names}"
    assert any(n.startswith("日报汇总_") for n in names), f"缺少日报汇总: {names}"


def test_space_separated_two_dates_create_two_folders():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td); outs = tmp / "outs"; outs.mkdir()
        _seed(outs, "2026-09-01"); _seed(outs, "2026-09-02")
        r = _run(_daily_args(_cfg(tmp, outs), "2026-09-01", "2026-09-02"))
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert _report_dirs(outs) == ["日报_20260901", "日报_20260902"]
        _assert_report_complete(outs / "日报_20260901")
        _assert_report_complete(outs / "日报_20260902")
        assert "共 2 日:成功 2 日,失败 0 日" in r.stdout


def test_comma_and_repeated_flag_equivalent():
    for form in ("comma", "repeat"):
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td); outs = tmp / "outs"; outs.mkdir()
            _seed(outs, "2026-09-01"); _seed(outs, "2026-09-02")
            cfg = _cfg(tmp, outs)
            if form == "comma":
                argv = ["daily", "--date", "2026-09-01,2026-09-02", "--config", str(cfg),
                        "--no-open", "--no-capture-watch"]
            else:
                argv = ["daily", "--date", "2026-09-01", "--date", "2026-09-02",
                        "--config", str(cfg), "--no-open", "--no-capture-watch"]
            r = _run(argv)
            assert r.returncode == 0, f"{form}: {r.stdout}\n{r.stderr}"
            assert _report_dirs(outs) == ["日报_20260901", "日报_20260902"], form


def test_single_date_backward_compatible():
    """回归:单日期行为与旧版一致(不出现多日汇总输出)。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td); outs = tmp / "outs"; outs.mkdir()
        _seed(outs, "2026-09-01")
        r = _run(_daily_args(_cfg(tmp, outs), "2026-09-01"))
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert _report_dirs(outs) == ["日报_20260901"]
        assert "共 1 个日期" not in r.stdout and "---- 汇总 ----" not in r.stdout


def test_duplicate_dates_deduped():
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td); outs = tmp / "outs"; outs.mkdir()
        _seed(outs, "2026-09-01")
        r = _run(_daily_args(_cfg(tmp, outs), "2026-09-01", "2026-09-01"))
        assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
        assert _report_dirs(outs) == ["日报_20260901"]
        assert "共 2 个日期" not in r.stdout, "重复日期应去重为 1 天"


def test_invalid_date_rejects_whole_batch():
    """含非法日期时整批拒绝:不得对合法日期做"半截"生成。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td); outs = tmp / "outs"; outs.mkdir()
        _seed(outs, "2026-09-01")
        r = _run(_daily_args(_cfg(tmp, outs), "2026-09-01", "2026-13-99"))
        assert r.returncode == 2, f"{r.stdout}\n{r.stderr}"
        assert _report_dirs(outs) == [], "非法日期应整批拒绝,不应生成任何日报"
        assert "2026-13-99" in r.stderr


def test_mixed_success_and_failure_continues():
    """某天失败不中断其他天;退出码取第一个失败日的码。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td); outs = tmp / "outs"; outs.mkdir()
        _seed(outs, "2026-09-01")
        _seed(outs, "2026-09-03", rows=BAD_ROWS)
        r = _run(_daily_args(_cfg(tmp, outs), "2026-09-01", "2026-09-03"))
        assert r.returncode == 3, f"{r.stdout}\n{r.stderr}"
        assert _report_dirs(outs) == ["日报_20260901"], _report_dirs(outs)
        _assert_report_complete(outs / "日报_20260901")
        assert "2026-09-03  失败(退出码 3)" in r.stdout
        assert "共 2 日:成功 1 日,失败 1 日" in r.stdout


def test_parse_daily_dates_contract():
    f = main_mod._parse_daily_dates
    # None -> 今天
    assert f(None) == [main_mod._dt.date.today().isoformat()]
    # 各种写法归一(含中文逗号/分号/多余分隔符)
    assert f([["2026-09-01", "2026-09-02"]]) == ["2026-09-01", "2026-09-02"]
    assert f(["2026-09-01,2026-09-02"]) == ["2026-09-01", "2026-09-02"]
    assert f(["2026-09-01，2026-09-02"]) == ["2026-09-01", "2026-09-02"]
    assert f([["2026-09-01"], ["2026-09-02"]]) == ["2026-09-01", "2026-09-02"]
    assert f(["2026-09-01;2026-09-02", "2026-09-03"]) == ["2026-09-01", "2026-09-02", "2026-09-03"]
    # 去重且保序
    assert f(["2026-09-02 2026-09-01 2026-09-02"]) == ["2026-09-02", "2026-09-01"]
    # 尾随分隔符/空白容忍
    assert f([" 2026-09-01, "]) == ["2026-09-01"]
    # 非法与空
    for bad in (["2026-13-99"], ["abc"], [""], ["  ,  "], [[]]):
        try:
            f(bad)
            raise AssertionError(f"应拒绝非法输入: {bad!r}")
        except ValueError:
            pass


def _run_all():
    tests = [(n, fn) for n, fn in sorted(globals().items())
             if n.startswith("test_") and callable(fn)]
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
    print(f"\n全部 {len(tests)} 个 multi-date 用例通过")


if __name__ == "__main__":
    _run_all()
