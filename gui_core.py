# -*- coding: utf-8 -*-
"""gui_core.py — 图形界面(启动助手)的界面逻辑:纯函数,不依赖 tkinter,可离线测试。

设计原则:**界面只是 main.py 的一层壳**。所有规则——日期口径(含省略年份的
MM-DD)、多账号 profile 解析、内置求和校验、失败退出码——仍然只有命令行那一份
实现;界面只负责把参数拼成命令行、把输出实时显示出来、从输出里认出产物路径。

本模块不打开浏览器、不发网络请求;只有 run_cli 会启动 main.py 子进程。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import accounts as acctmod  # noqa: E402  (账号名校验 / profile 目录 / 账号列表)
import config as cfgmod  # noqa: E402
from extractor import sessions as S  # noqa: E402  (日期归一:与命令行同一份实现)

MAIN_PY = PROJECT_ROOT / "main.py"
PYTHON = sys.executable or "python"
BEIJING = timezone(timedelta(hours=8))
DEFAULT_ACCOUNT = acctmod.DEFAULT_ACCOUNT


class FormError(ValueError):
    """界面参数不合法(日期写错、账号名非法、文件不存在):提示用户,不往下走。"""


# ---------------- 日期 ----------------
def today_bj() -> str:
    """东八区今天(YYYY-MM-DD),与按天导出的口径一致。"""
    return datetime.now(BEIJING).strftime("%Y-%m-%d")


def shift_day(iso: str, days: int) -> str:
    """在 YYYY-MM-DD 上加减天数(界面上的"今天 / 昨天"按钮用)。"""
    base = datetime.strptime(check_date(iso, "日期"), "%Y-%m-%d")
    return (base + timedelta(days=int(days))).strftime("%Y-%m-%d")


def check_date(text: str, field: str = "日期") -> str:
    """校验并归一一个日期:YYYY-MM-DD,或省略年份的 MM-DD(年份取当前年份)。

    归一走 extractor/sessions.normalize_date_str —— 与命令行同一份实现,
    所以不会出现"界面能跑、命令行不认"的日期写法。
    """
    raw = (text or "").strip()
    if not raw:
        raise FormError("%s不能为空" % field)
    try:
        return S.normalize_date_str(raw)
    except ValueError as exc:
        raise FormError("%s:%s(可以写 2026-09-04,也可以省略年份写 09-04)" % (field, exc)) from None


def check_dates(text: str, field: str = "日期") -> List[str]:
    """把界面上的一行日期拆成规范化列表(空格 / 逗号 / 顿号 / 分号分隔;去重保序)。

    空字符串 -> [] ,表示不带 --date,由命令行取默认值(今天,东八区)。
    """
    raw = text or ""
    for sep in ("，", "、", ";", "；", "|"):
        raw = raw.replace(sep, ",")
    out: List[str] = []
    for part in re.split(r"[,\s]+", raw):
        part = part.strip()
        if not part:
            continue
        day = check_date(part, field)
        if day not in out:
            out.append(day)
    return out


# ---------------- 账号(多账号切换) ----------------
def load_cfg(config: Optional[str] = None) -> dict:
    """读配置(不套用 --account;只看 config.yaml 里写了什么时用)。"""
    return cfgmod.load_config(config)


def list_accounts(config: Optional[str] = None) -> List[Dict[str, object]]:
    """已知账号列表(默认账号恒在首位)。只读文件:不开浏览器、不建目录。"""
    return acctmod.list_accounts(load_cfg(config))


def account_names(config: Optional[str] = None) -> List[str]:
    return [str(row["account"]) for row in list_accounts(config)]


def account_profile_dir(name: str, config: Optional[str] = None) -> str:
    """该账号的持久化 profile 目录(只算路径,不创建目录)。"""
    prof, _ = acctmod.account_paths(load_cfg(config), name)
    return str(prof)


def account_summary(name: str, config: Optional[str] = None) -> str:
    """一句话说明这个账号的状态(页面账号名 / 尚未登录),用于界面提示。"""
    try:
        rows = list_accounts(config)
    except Exception:
        return ""
    for row in rows:
        if str(row["account"]) != str(name):
            continue
        meta = row.get("meta") or {}
        nick = str(meta.get("nickname") or "").strip()
        when = str(meta.get("logged_in_at") or "").strip()
        if nick:
            return "页面账号名:%s%s" % (nick, (" · 上次登录 " + when) if when else "")
        if row.get("ready"):
            return "有登录 profile(早于多账号功能,未登记页面账号名)"
        return "尚未登录过(点「登录」扫码)"
    return ""


def config_account(config: Optional[str] = None) -> str:
    """config.yaml 里 browser.account 写的账号名(空 = 默认账号)。"""
    browser = load_cfg(config).get("browser", {}) or {}
    return str(browser.get("account") or "").strip()


def cli_account(name: Optional[str]) -> Optional[str]:
    """界面选中的账号 -> 命令行 --account 的值。

    默认账号返回 None(不传 --account):这样 config.yaml 里没写 browser.account
    的老用户行为完全不变,默认 profile 目录也不会被改写。
    """
    n = (name or "").strip()
    if not n or n == DEFAULT_ACCOUNT:
        return None
    return n


def _account_args(account: Optional[str], config: Optional[str]) -> List[str]:
    """拼 --config / --account(账号名非法就地报错,不让子进程白跑一趟)。"""
    args: List[str] = []
    if config:
        args += ["--config", str(config)]
    name = cli_account(account)
    if name:
        try:
            acctmod.normalize_account_name(name)
        except acctmod.AccountError as exc:
            raise FormError("账号名不合法:%s" % exc) from None
        args += ["--account", name]
    return args


# ---------------- 命令行拼装 ----------------
def build_login_args(account: Optional[str] = None, config: Optional[str] = None) -> List[str]:
    """login:有头窗口扫码;已登录的账号会立刻复用会话,不要求重扫。"""
    return ["login"] + _account_args(account, config)


def build_sessions_args(date: Optional[str] = None, account: Optional[str] = None,
                        config: Optional[str] = None) -> List[str]:
    """sessions:只读列出某天(东八区)已结束场次。"""
    days = check_dates(date or "", "日期")
    if len(days) > 1:
        raise FormError("列出场次一次只查一天:请只填一个日期(当前填了 %d 个)" % len(days))
    args = ["sessions"]
    if days:
        args += ["--date", days[0]]
    return args + _account_args(account, config)


def build_export_args(date: Optional[str] = None, room_id: Optional[str] = None, *,
                      excel: bool = True, compact: bool = False, capture_watch: bool = False,
                      out: Optional[str] = None, account: Optional[str] = None,
                      config: Optional[str] = None) -> List[str]:
    """export:按天导出(--date)或指定场次(--room-id);CSV 始终输出,--excel 另存 xlsx。"""
    room = (room_id or "").strip()
    if room:
        args = ["export", "--room-id", room]
    else:
        days = check_dates(date or "", "日期")
        if len(days) > 1:
            raise FormError("按天导出一只能一天:多天请用「一键日报」(daily 支持一次多个日期)")
        args = ["export"] + (["--date", days[0]] if days else [])
    if excel:
        args.append("--excel")
    if compact:
        args.append("--compact")
    if capture_watch:
        args.append("--capture-watch")
    if out:
        args += ["--out", str(out).strip()]
    return args + _account_args(account, config)


def build_stats_args(date: Optional[str] = None, *, auto: bool = False, out: Optional[str] = None,
                     account: Optional[str] = None, config: Optional[str] = None) -> List[str]:
    """stats:读 data/outputs 里已导出的分钟量表做场次级汇总(--auto 缺失时在线补导出)。"""
    days = check_dates(date or "", "日期")
    if len(days) > 1:
        raise FormError("汇总统计一次只统计一天:请只填一个日期(当前填了 %d 个)" % len(days))
    args = ["stats"]
    if days:
        args += ["--date", days[0]]
    if auto:
        args.append("--auto")
    if out:
        args += ["--out", str(out).strip()]
    return args + _account_args(account, config)


def build_daily_args(date: Optional[str] = None, *, no_open: bool = False,
                     no_capture_watch: bool = False, account: Optional[str] = None,
                     config: Optional[str] = None) -> List[str]:
    """daily:一键日报(导出 + 两列表日报文件夹),日期可一次给多个(空格/逗号分隔)。"""
    days = check_dates(date or "", "日期")
    args = ["daily"]
    if days:
        args += ["--date"] + days
    if no_open:
        args.append("--no-open")
    if no_capture_watch:
        args.append("--no-capture-watch")
    return args + _account_args(account, config)


def build_trim_args(in_path: str, out: Optional[str] = None) -> List[str]:
    """trim:把已导出的完整分钟量表转成成交点精简表(本地文件操作,无 --account/--config)。"""
    src = (in_path or "").strip()
    if not src:
        raise FormError("请先选择要转换的完整分钟量表 CSV")
    if not Path(src).is_file():
        raise FormError("找不到文件:%s" % src)
    args = ["trim", "--in", src]
    if out and str(out).strip():
        args += ["--out", str(out).strip()]
    return args


def build_probe_args(room_id: Optional[str] = None, *, dry_run: bool = False, layer: str = "all",
                     account: Optional[str] = None, config: Optional[str] = None) -> List[str]:
    """probe:分层只读探测(证据写到 data/probe)。dry-run 不打开浏览器。"""
    args = ["probe"]
    room = (room_id or "").strip()
    if room:
        args += ["--room-id", room]
    if dry_run:
        args.append("--dry-run")
    if layer and layer != "all":
        args += ["--layer", str(layer)]
    return args + _account_args(account, config)


# ---------------- 运行与产物 ----------------
def full_command(args: Sequence[str], fixture: Optional[str] = None) -> List[str]:
    """把参数拼成可执行的命令行(python -u main.py ...),用于日志里回放。"""
    cmd = [PYTHON, "-u", str(MAIN_PY)]
    if fixture:
        cmd += ["--fixture", str(fixture)]
    return cmd + [str(a) for a in args]


def pretty_command(args: Sequence[str]) -> str:
    """给界面日志用的一行命令(相对路径、不带解释器全路径)。"""
    shown = []
    for a in args:
        try:
            rel = Path(str(a)).resolve().relative_to(PROJECT_ROOT)
            shown.append(str(rel))
        except Exception:
            shown.append(str(a))
    return "python main.py " + " ".join(shown)


_OUT_RE = re.compile(r"(?:已生成|完成):\s*(.+?)\s*$")


def output_path_of(line: str) -> Optional[str]:
    """从 CLI 输出里认出产物路径("[stats] 汇总表已生成: data/outputs/stats_x.csv"、"[trim] 完成: ...")。

    CLI 是在项目根目录下跑的,打印的可能是相对路径,而本进程的工作目录未必是
    项目根目录,所以相对路径一律按 PROJECT_ROOT 解析。
    """
    m = _OUT_RE.search(line or "")
    if not m:
        return None
    raw = re.split(r"\s{2,}[(\uFF08]", m.group(1), maxsplit=1)[0].strip()
    if not raw:
        return None
    path = Path(raw)
    return str(path if path.is_absolute() else (PROJECT_ROOT / path))


def default_output_dir() -> str:
    """默认产物目录(data/outputs;按 config.yaml 的 export.out_dir 解析)。"""
    try:
        rel = str((load_cfg().get("export", {}) or {}).get("out_dir") or "data/outputs")
    except Exception:
        rel = "data/outputs"
    path = Path(rel)
    return str(path if path.is_absolute() else (PROJECT_ROOT / path))


def display_path(path) -> str:
    """项目内的绝对路径显示成相对项目根的形式(仅用于打印)。"""
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except Exception:
        return str(path)


def safe_output_dir(path: str) -> Optional[str]:
    """只允许打开项目 data/ 下的目录,避免界面变成任意路径执行器。"""
    try:
        target = Path(str(path)).expanduser().resolve()
    except Exception:
        return None
    base = (PROJECT_ROOT / "data").resolve()
    if target == base or base in target.parents:
        return str(target if target.is_dir() else target.parent)
    return None


def run_cli(args: Sequence[str], on_line: Callable[[str], None], fixture: Optional[str] = None,
            cwd: Optional[str] = None, encoding: str = "utf-8") -> int:
    """跑 main.py,把每一行输出实时交给 on_line;返回退出码。

    用 pythonw 启动界面时不希望每跑一条命令都弹一个黑框,故在 Windows 上给子
    进程加 CREATE_NO_WINDOW。stderr 合并进 stdout,界面上不会漏掉警告。
    """
    cmd = full_command(args, fixture=fixture)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = encoding
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONUTF8"] = "1"
    kwargs: Dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc = subprocess.Popen(cmd, cwd=str(cwd or PROJECT_ROOT), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding=encoding,
                            errors="replace", bufsize=1, env=env, **kwargs)
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip("\r\n"))
    proc.wait()
    return proc.returncode or 0


EXIT_MEANING = {
    0: "成功",
    2: "当天没有可导出的场次 / 参数或前置条件不满足(看上面的提示,没有写入任何东西)",
    3: "未登录或登录已过期(先点「登录 / 检查登录状态」),或没有该场次的权限",
}


def explain_exit(code: int) -> str:
    return "退出码 %d:%s" % (code, EXIT_MEANING.get(code, "未知(看上面的输出)"))
