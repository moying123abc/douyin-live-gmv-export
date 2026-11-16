# -*- coding: utf-8 -*-
"""无头模式(--headless / browser.headful)的离线回归(不联网、不开浏览器)。

功能契约:
  - 显示模式只有一个真相:`cfg["browser"]["headful"]`;
  - 命令行 `--headless` / `--headful` 只覆盖"本次",都不写则完全跟随配置文件;
  - **登录永远有头**:perform_login 必须显式 headful=True(扫码要看得见窗口);
  - 取数路径(场次/成交金额/观看量/M3)一律不写死,跟随 cfg;
  - probe 等排障脚本故意保持有头(窗口可见更好观察页面)。

运行:python tests/test_headless_mode.py
"""
import inspect
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import auth  # noqa: E402
import gui_core as C  # noqa: E402
import main as main_mod  # noqa: E402
from extractor import extra_metrics, sessions, trend  # noqa: E402
from exporter import watch_capture  # noqa: E402


# --------------------------------------------------------------------------
# 1. 配置解析:_headless_from 的优先级
# --------------------------------------------------------------------------
def test_config_decides_when_no_override():
    assert auth._headless_from({"browser": {"headful": True}}, None) is False
    assert auth._headless_from({"browser": {"headful": False}}, None) is True


def test_override_beats_config():
    """命令行 --headful 在有头被配置关掉时仍能强制可见(排障用)。"""
    assert auth._headless_from({"browser": {"headful": False}}, True) is False
    assert auth._headless_from({"browser": {"headful": True}}, False) is True


def test_missing_key_defaults_to_headful():
    """配置里没写 browser.headful 时默认有头(与 DEFAULT_CONFIG 一致)。"""
    assert auth._headless_from({}, None) is False
    assert auth._headless_from({"browser": {}}, None) is False


# --------------------------------------------------------------------------
# 2. 命令行开关
# --------------------------------------------------------------------------
def _parse(argv):
    return main_mod.build_parser().parse_args(argv)


def test_cli_headless_flag():
    ns = _parse(["daily", "--headless"])
    assert ns.headful is False


def test_cli_headful_flag():
    ns = _parse(["daily", "--headful"])
    assert ns.headful is True


def test_cli_no_flag_keeps_none():
    """不传开关时必须是 None,否则会把用户的配置文件覆盖掉。"""
    for argv in (["daily"], ["export"], ["stats"], ["sessions"]):
        assert _parse(argv).headful is None, argv


def test_cli_flags_are_mutually_exclusive():
    try:
        _parse(["daily", "--headless", "--headful"])
    except SystemExit:
        return
    raise AssertionError("--headless 与 --headful 不应能同时使用")


def test_flags_exist_on_data_commands_only():
    """取数命令有开关;login(恒有头)与 probe(排障)没有。"""
    for cmd in ("export", "daily", "stats", "sessions"):
        assert _parse([cmd, "--headless"]).headful is False, cmd
    for cmd in ("login", "probe"):
        try:
            _parse([cmd, "--headless"])
        except SystemExit:
            continue
        raise AssertionError(f"{cmd} 不应接受 --headless")


# --------------------------------------------------------------------------
# 3. _load_cfg 把开关写进 cfg(业务路径只认 cfg)
# --------------------------------------------------------------------------
class _Args:
    def __init__(self, headful):
        self.config = None
        self.account = None
        self.headful = headful


def test_load_cfg_applies_override():
    cfg = main_mod._load_cfg(_Args(False))
    assert cfg["browser"]["headful"] is False
    cfg = main_mod._load_cfg(_Args(True))
    assert cfg["browser"]["headful"] is True


def test_load_cfg_leaves_config_alone_when_unspecified():
    cfg = main_mod._load_cfg(_Args(None))
    import config as cfgmod
    assert cfg["browser"]["headful"] == cfgmod.DEFAULT_CONFIG["browser"]["headful"]


# --------------------------------------------------------------------------
# 4. 不变量:登录恒有头;取数路径不写死
# --------------------------------------------------------------------------
def test_login_is_always_headful():
    """扫码必须看得见窗口 —— perform_login 里必须是 headful=True。"""
    src = inspect.getsource(auth.perform_login)
    assert "open_session(cfg, headful=True)" in src, src


def test_data_paths_do_not_hardcode_headful():
    """四个取数入口必须跟随 cfg,不能再写死 headful=True(否则 --headless 失效)。"""
    for mod in (sessions, trend, watch_capture, extra_metrics):
        src = inspect.getsource(mod)
        assert "open_session(cfg, headful=True)" not in src, mod.__name__


def test_probe_style_scripts_stay_headful():
    """排障脚本保持有头(它们的价值就在于能看见页面)。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    for name in ("probe.py", "gpm_probe.py", "assisted_replay_probe.py",
                 "replay_analysis_probe.py"):
        src = (root / name).read_text(encoding="utf-8")
        assert "open_session(cfg, headful=True)" in src, name


# --------------------------------------------------------------------------
# 5. 界面参数拼装
# --------------------------------------------------------------------------
def test_gui_adds_headless_only_when_ticked():
    assert "--headless" not in C.build_daily_args("09-04")
    for args in (C.build_sessions_args("09-04", headless=True),
                 C.build_export_args("09-04", headless=True),
                 C.build_stats_args("09-04", headless=True),
                 C.build_daily_args("09-04", headless=True)):
        assert "--headless" in args, args


def test_gui_headless_position_before_account():
    """--headless 排在自己的参数之后、--account 之前(不影响既有解析)。"""
    args = C.build_daily_args("09-04", headless=True, account="spa2")
    assert args.index("--headless") < args.index("--account")


def test_gui_probe_has_no_headless():
    assert "--headless" not in C.build_probe_args("7000000000000000001")


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
    print(f"\n全部 {len(tests)} 个无头模式用例通过")


if __name__ == "__main__":
    _run_all()
