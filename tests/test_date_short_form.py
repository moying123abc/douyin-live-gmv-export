# -*- coding: utf-8 -*-
"""--date 短写法(省略年份的 MM-DD)离线回归(不联网、不打开浏览器、不需要登录)。

覆盖:
  1. normalize_date_str:完整写法(含 . 分割/单位数月日)不变形;
  2. normalize_date_str:MM-DD / M.D → 年份取"当前年份"(显式 today 保证确定性);
  3. 跨年边界:1 月传入 12-31 取的是**当年**而不是去年;
  4. 非法写法(09/04、26-09-01、09、abc、空串)一律 ValueError;
  5. 日历校验:2026-13-99 / 2026-02-30 / 非闰年 02-29 报"日期不存在",闰年 02-29 通过;
  6. parse_date 返回 date;默认 today 取东八区"今天"所在年;
  7. main._normalize_date_token / _parse_daily_dates 集成(多日期混用短写法);
  8. CLI 端到端:sessions / stats 传 09-04 时**不**报"日期格式"错误。

运行:python tests/test_date_short_form.py
"""
import datetime as _dt
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as main_mod  # noqa: E402
from extractor import sessions as sessions_mod  # noqa: E402

NORM = sessions_mod.normalize_date_str
PARSE = sessions_mod.parse_date


def _raises(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except ValueError:
        return True
    return False


def test_full_form_unchanged():
    """原有 YYYY-MM-DD / YYYY.M.D 行为必须完全不变。"""
    assert NORM("2026-09-04") == "2026-09-04"
    assert NORM("2026.09.04") == "2026.09.04".replace(".", "-")
    assert NORM("2026.9.4") == "2026-09-04"
    assert NORM(" 2026-9-4 ") == "2026-09-04"
    assert NORM("2026-09-04", today="2030-01-01") == "2026-09-04"


def test_short_form_uses_given_today_year():
    """MM-DD 省略的年份取 today 所在年(显式传入 → 结果确定,与真实时钟无关)。"""
    assert NORM("09-04", today="2026-01-05") == "2026-09-04"
    assert NORM("09.04", today="2026-12-31") == "2026-09-04"
    assert NORM("9.4", today="2026-05-05") == "2026-09-04"
    assert NORM(" 09-04 ", today="2026-05-05") == "2026-09-04"
    assert NORM("12-31", today=_dt.date(2026, 1, 5)) == "2026-12-31"
    assert NORM("01-01", today=_dt.date(2030, 12, 31)) == "2030-01-01"


def test_short_form_default_today_is_cst_today():
    """不传 today 时,年份 = 东八区"今天"所在年(直接对接用户诉求)。"""
    today_cst = _dt.datetime.now(sessions_mod._CST).date()
    assert NORM("09-04") == f"{today_cst.year}-09-04"
    assert PARSE("09-04") == _dt.date(today_cst.year, 9, 4)


def test_short_form_rejects_bad_shapes():
    """歧义或非法写法必须拒绝:斜杠、两位年份、缺月缺日、非数字、空。"""
    for bad in ("09/04", "26-09-01", "09-04-01", "09", "04", "abc", "", "  ", "09-", "-04", "2026"):
        assert _raises(NORM, bad, today="2026-05-05"), f"应拒绝 {bad!r}"


def test_calendar_validation():
    """月/日越界或该日不存在 → 报错,但报的是"日期不存在"而不是"格式"错。"""
    for bad in ("2026-13-99", "2026-02-30", "13-01", "00-10", "10-00", "10-32"):
        assert _raises(NORM, bad, today="2026-05-05"), f"应拒绝 {bad!r}"
    try:
        NORM("2026-13-99")
        raise AssertionError("应拒绝 2026-13-99")
    except ValueError as exc:
        assert "日期不存在" in str(exc), exc
    # 闰年规则:短写法同样要遵守(年份来自 today)
    assert NORM("02-29", today="2024-06-01") == "2024-02-29"
    assert _raises(NORM, "02-29", today="2025-06-01")


def test_parse_date_returns_date():
    assert PARSE("2026-09-04") == _dt.date(2026, 9, 4)
    assert PARSE("09-04", today="2026-01-05") == _dt.date(2026, 9, 4)
    assert PARSE("9.4", today="2026-01-05") == _dt.date(2026, 9, 4)
    assert _raises(PARSE, "09/04")
    assert _raises(PARSE, "2026-02-30")


def test_normalize_date_token_contract():
    f = main_mod._normalize_date_token
    assert f("2026-09-04") == "2026-09-04"
    assert f("09-04", today="2026-01-05") == "2026-09-04"
    assert f("9.4", today="2026-01-05") == "2026-09-04"
    assert _raises(f, "09/04")
    # 无 today 时用东八区今天所在年
    assert f("09-04") == f"{_dt.datetime.now(sessions_mod._CST).date().year}-09-04"


def test_parse_daily_dates_mixes_short_and_full():
    """daily 多日期可混用两种格式;today 显式传入时结果确定。"""
    f = main_mod._parse_daily_dates
    assert f([["09-01", "2026-09-02"]], today="2026-01-05") == ["2026-09-01", "2026-09-02"]
    assert f(["09-01,09-02"], today="2026-01-05") == ["2026-09-01", "2026-09-02"]
    assert f(["09-01", "09-01"], today="2026-01-05") == ["2026-09-01"]
    assert f(None, today="2026-01-05") == ["2026-01-05"]
    # 旧契约不回归:None -> 真实今天
    assert f(None) == [main_mod._dt.date.today().isoformat()]
    for bad in (["09/01"], ["26-09-01"], ["09"], [""], [[]]):
        assert _raises(f, bad, today="2026-01-05"), f"应拒绝 {bad!r}"


def _cli(*argv):
    return subprocess.run([sys.executable, str(ROOT / "main.py"), *argv],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def test_cli_short_date_is_accepted():
    """端到端:短写法必须被 CLI 当作合法日期(不能报"日期格式"错)。"""
    for argv in (("sessions", "--date", "09-04"), ("stats", "--date", "09-04"),
                 ("stats", "--date", "9.4")):
        r = _cli(*argv)
        out = (r.stdout or "") + (r.stderr or "")
        assert "日期格式" not in out, f"{argv} 不应报日期格式错:\n{out}"


def test_cli_bad_date_still_rejected():
    """端到端:真正的非法写法仍然退出码 2(短写法没有放松校验)。"""
    r = _cli("sessions", "--date", "09/04")
    assert r.returncode == 2, f"{r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "日期格式" in (r.stdout + r.stderr)


def test_watch_capture_cli_rejects_bad_date():
    """watch_capture 的 --date 与主 CLI 同一口径:非法写法直接拒绝(不进入浏览器)。"""
    r = subprocess.run([sys.executable, "-m", "exporter.watch_capture",
                        "--room-id", "7000000000000000001", "--date", "09/04"],
                       cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 2, f"{r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "日期格式" in (r.stdout + r.stderr), r.stdout + r.stderr


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
    print(f"\n全部 {len(tests)} 个短日期用例通过")


if __name__ == "__main__":
    _run_all()
