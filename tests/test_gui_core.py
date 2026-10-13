# -*- coding: utf-8 -*-
"""图形界面离线回归(不联网、不打开浏览器、不需要登录)。

覆盖:
  1. 界面日期口径与命令行同一份实现:YYYY-MM-DD、省略年份的 MM-DD、9.4 写法;
     非法写法(09/04、2026-02-29)一律 FormError;
  2. 多日期解析(空格/逗号/顿号分隔、去重、空 -> 不传 --date);
  3. 账号 -> --account 的映射:默认账号不传参(保住老用户零迁移);
     非法账号名在界面就地报错,不让子进程白跑一趟;
  4. 各子命令参数拼装:login / sessions / export / stats / daily / trim / probe,
     且 trim 绝不带 --account / --config(trim 子命令根本没有这两个参数);
  5. 产物路径识别:main.py 打印的六种"已生成:"行都能认出来并解析成绝对路径;
  6. safe_output_dir 只放行项目 data/ 下的目录(界面不是任意路径执行器);
  7. gui.py 可编译、可构造窗口;启动助手.bat 存在且是 GBK 编码(否则 cmd 会乱解析)。

运行:python tests/test_gui_core.py
"""
import pathlib
import py_compile
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gui_core as C  # noqa: E402

YEAR = C.today_bj()[:4]


def _raises_form_error(fn, *a, **kw) -> bool:
    try:
        fn(*a, **kw)
    except C.FormError:
        return True
    return False


def test_date_short_form():
    """省略年份的 MM-DD 取当前年份,与命令行 normalize_date_str 完全一致。"""
    assert C.check_date("09-04") == f"{YEAR}-09-04"
    assert C.check_date("9.4") == f"{YEAR}-09-04"
    assert C.check_date(" 09.04 ") == f"{YEAR}-09-04"
    assert C.check_date("2026-09-04") == "2026-09-04"
    assert C.check_date("2026.09.04") == "2026-09-04"


def test_date_invalid_rejected():
    """界面拦下命令行也会拒绝的写法(不放进子进程)。"""
    for bad in ("", "   ", "09/04", "09", "abc", "26-09-01", "2026-02-29", "2026-13-99"):
        assert _raises_form_error(C.check_date, bad), bad


def test_check_dates_multi():
    """多日期:空格/逗号/顿号/分号分隔,去重保序;空 -> []。"""
    assert C.check_dates("") == []
    assert C.check_dates("  ") == []
    assert C.check_dates("09-01 09-02") == [f"{YEAR}-09-01", f"{YEAR}-09-02"]
    assert C.check_dates("09-01,09-02，09-03、09-04;09-05") == [
        f"{YEAR}-09-01", f"{YEAR}-09-02", f"{YEAR}-09-03", f"{YEAR}-09-04", f"{YEAR}-09-05"]
    assert C.check_dates("2026-09-01 09-01") == ["2026-09-01"]
    assert _raises_form_error(C.check_dates, "09-01 09/02")


def test_today_and_shift_day():
    """今天/昨天按钮用的换算:跨月、跨年、闰年都对。"""
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", C.today_bj())
    assert C.shift_day("2026-03-01", -1) == "2026-02-28"
    assert C.shift_day("2024-03-01", -1) == "2024-02-29"
    assert C.shift_day("2026-12-31", 1) == "2027-01-01"
    assert C.shift_day(C.today_bj(), 0) == C.today_bj()


def test_account_arg_mapping():
    """默认账号不传 --account(config.yaml 与老 profile 行为原样保留)。"""
    assert C.cli_account(None) is None
    assert C.cli_account("") is None
    assert C.cli_account("default") is None
    assert C.cli_account(" spa2 ") == "spa2"


def test_build_login_and_sessions():
    assert C.build_login_args() == ["login"]
    assert C.build_login_args("spa2") == ["login", "--account", "spa2"]
    assert C.build_sessions_args() == ["sessions"]
    assert C.build_sessions_args("09-04") == ["sessions", "--date", f"{YEAR}-09-04"]
    assert C.build_sessions_args("09-04", account="spa2") == [
        "sessions", "--date", f"{YEAR}-09-04", "--account", "spa2"]


def test_build_export_stats_daily():
    day = f"{YEAR}-09-04"
    assert C.build_export_args("09-04") == ["export", "--date", day, "--excel"]
    assert C.build_export_args("09-04", excel=False, compact=True, capture_watch=True) == [
        "export", "--date", day, "--compact", "--capture-watch"]
    assert C.build_export_args(room_id="7000000000000000001") == [
        "export", "--room-id", "7000000000000000001", "--excel"]
    assert C.build_stats_args("09-04") == ["stats", "--date", day]
    assert C.build_stats_args("09-04", auto=True) == ["stats", "--date", day, "--auto"]
    assert C.build_daily_args("09-04") == ["daily", "--date", day]
    assert C.build_daily_args("09-01 09-02", no_open=True, no_capture_watch=True) == [
        "daily", "--date", f"{YEAR}-09-01", f"{YEAR}-09-02", "--no-open", "--no-capture-watch"]
    assert C.build_daily_args("", account="spa2", config="my.yaml") == [
        "daily", "--config", "my.yaml", "--account", "spa2"]
    assert C.build_probe_args("123", dry_run=True, layer="L0") == [
        "probe", "--room-id", "123", "--dry-run", "--layer", "L0"]
    assert C.build_probe_args("123", layer="all") == ["probe", "--room-id", "123"]


def test_builders_reject_impossible_input():
    """一眼就不对的东西不进子进程:多天给单个命令、坏账号名、缺文件。"""
    assert _raises_form_error(C.build_sessions_args, "09-01 09-02")
    assert _raises_form_error(C.build_stats_args, "09-01 09-02")
    assert _raises_form_error(C.build_export_args, "09-01 09-02")
    assert _raises_form_error(C.build_daily_args, "09-04", account="a/b")
    assert _raises_form_error(C.build_login_args, "a b")
    assert _raises_form_error(C.build_trim_args, "")


def test_trim_args_have_no_account_or_config():
    """trim 是纯本地文件操作,子命令没有 --account/--config,界面也不能硬塞。"""
    with tempfile.TemporaryDirectory() as td:
        src = pathlib.Path(td) / "live_20260904_x.csv"
        src.write_text("time,gmv\n09:04,1.0\n", encoding="utf-8")
        args = C.build_trim_args(str(src))
        assert args == ["trim", "--in", str(src)]
        assert "--account" not in args and "--config" not in args
        out = str(pathlib.Path(td) / "c.csv")
        assert C.build_trim_args(str(src), out) == ["trim", "--in", str(src), "--out", out]


def test_output_path_of_real_lines():
    """"已生成:"六种真实输出行都能认出产物,并解析成项目内的绝对路径。"""
    cases = [
        "[daily] 日报已生成: data/outputs/日报_20260904",
        "[stats] 汇总表已生成: data/outputs/stats_20260904.csv",
        "[export] CSV 已生成: data/outputs/live_20260904_7000000000000000001.csv",
        "[export] 成交点精简表已生成: data/outputs/live_20260904_7000000000000000001_compact.csv  (非0分钟 12 行,合计 1350.00 元)",
        "[export] Excel 已生成: data/outputs/live_20260904_7000000000000000001.xlsx",
        "[export] 小时 GPM 已生成: data/outputs/gpm_hourly_20260904.csv  (小时桶 10 个,合计 1350.00 元)",
        "[export] 小时 GPM(真实分母)已生成: data/outputs/gpm_hourly_watch_20260904.csv  (小时桶 10 个)",
        "[export] 观看档已生成: data/outputs/watch_20260904_7000000000000000001.json",
        "[trim] 完成: data/outputs/live_20260904_7000000000000000001_compact.csv",
    ]
    for line in cases:
        got = C.output_path_of(line)
        assert got, line
        path = pathlib.Path(got)
        assert path.is_absolute(), got
        assert str(path).startswith(str(ROOT)), got
    assert C.output_path_of("[stats] 什么都没有") is None
    assert C.output_path_of("") is None


def test_safe_output_dir():
    """只允许打开项目 data/ 下的目录(新克隆/新 worktree 里 data/outputs 可能还没生成)。"""
    out = ROOT / "data" / "outputs"
    out.mkdir(parents=True, exist_ok=True)
    assert C.safe_output_dir(str(out)) == str(out)
    assert C.safe_output_dir(str(out / "nope.csv")) == str(out)
    assert C.safe_output_dir(str(ROOT)) is None
    assert C.safe_output_dir(str(ROOT.parent)) is None
    assert C.safe_output_dir("C:\\Windows") is None
    assert C.safe_output_dir(str(ROOT / "data" / ".." / "gui.py")) is None


def test_default_output_dir_and_display_path():
    assert C.default_output_dir().endswith(str(pathlib.Path("data") / "outputs"))
    assert C.display_path(ROOT / "main.py") == "main.py"
    assert C.display_path("D:\\somewhere\\else") == "D:\\somewhere\\else"


def test_explain_exit():
    assert "成功" in C.explain_exit(0)
    assert "退出码 2" in C.explain_exit(2)
    assert "未登录" in C.explain_exit(3)
    assert "未知" in C.explain_exit(9)


def test_accounts_listing_is_read_only():
    """账号下拉的数据来源:默认账号恒在首位,且不打开浏览器。"""
    rows = C.list_accounts()
    assert rows, "至少要有一个账号(默认账号)"
    assert str(rows[0]["account"]) == "default"
    assert C.account_names()[0] == "default"
    prof = pathlib.Path(C.account_profile_dir("default"))
    assert prof.name == "browser_profile"
    assert isinstance(C.account_summary("default"), str)
    assert "playwright" not in sys.modules, "界面逻辑不该把 playwright 拉进来"


def test_gui_core_never_touches_browser():
    """界面只拼命令行:构造参数不该引入 playwright / 打开任何浏览器。"""
    C.build_daily_args("09-04", account="spa2")
    assert "playwright" not in sys.modules


def test_gui_module_and_launcher():
    """gui.py 能编译;双击入口 启动助手.pyw 存在且能编译;启动助手.bat 保持 GBK 编码。"""
    py_compile.compile(str(ROOT / "gui.py"), doraise=True)
    py_compile.compile(str(ROOT / "gui_core.py"), doraise=True)
    pyw = ROOT / "启动助手.pyw"
    assert pyw.is_file(), "双击入口 启动助手.pyw 必须在"
    py_compile.compile(str(pyw), doraise=True)
    pyw_text = pyw.read_text(encoding="utf-8")
    assert "gui.py" in pyw_text and "pythonw" in pyw_text
    launcher = ROOT / "启动助手.bat"
    assert launcher.is_file()
    assert not (ROOT / "统计当天.bat").exists(), "统计当天.bat 已合并进图形界面,不应再存在"
    text = launcher.read_bytes().decode("gbk")
    assert "gui.py" in text and "pythonw" in text
    assert "抖音直播数据导出助手" in text, "启动助手.bat 应保持 GBK 编码(否则 cmd 会乱解析)"


def test_gui_window_can_be_built():
    """真的建一次窗口(不显示、不联网):控件接线错会在这里暴露。"""
    try:
        import tkinter as tk
    except Exception as exc:  # noqa: BLE001
        print(f"skip(没有 tkinter):{exc}")
        return
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"skip(没有图形环境):{exc}")
        return
    try:
        root.withdraw()
        import gui
        app = gui.App(root)
        root.update()
        assert str(app.account_var.get()) in C.account_names()
        assert len(app._all_buttons) >= 15
        app.on_close()
    finally:
        try:
            root.destroy()
        except tk.TclError:
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
    print(f"\n全部 {len(tests)} 个图形界面用例通过")


if __name__ == "__main__":
    _run_all()
