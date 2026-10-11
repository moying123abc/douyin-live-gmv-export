# -*- coding: utf-8 -*-
"""gui.py — 抖音直播数据导出助手(图形界面)。双击「启动助手.bat」即可运行。

界面只是 main.py 的一层壳:每个按钮拼出来的命令行都会原样打印在日志里,
可以复制到终端重放同一件事。日期口径(含省略年份的 MM-DD)、多账号 profile
解析、内置求和校验全部在命令行里,界面不自己造第二套规则,也绕不过任何守卫。

五个页签:一键日报 / 按天导出 / 汇总统计 / 账号 / 高级工具。
"""
from __future__ import annotations

import os
import platform
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable, List, Optional, Sequence

import gui_core as C

FONT = ("Microsoft YaHei UI", 10)
FONT_MONO = ("Consolas", 10)
HINT_COLOR = "#555555"
WARN_COLOR = "#B00020"
OK_COLOR = "#1B5E20"
CMD_COLOR = "#8FA1B3"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("抖音直播数据导出助手 · 巨量百应直播大屏")
        self.root.geometry("1140x800")
        self.root.minsize(960, 660)

        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.running = False
        self._closed = False
        self.last_output: Optional[str] = None
        self._accounts: List[dict] = []
        self.account_var = tk.StringVar(value=C.DEFAULT_ACCOUNT)
        self._all_buttons: List[ttk.Button] = []

        try:
            style = ttk.Style()
            style.configure(".", font=FONT)
            style.configure("TNotebook.Tab", padding=(14, 6))
        except tk.TclError:
            pass

        self._build_toolbar()
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._build_daily_tab()
        self._build_export_tab()
        self._build_stats_tab()
        self._build_account_tab()
        self._build_tools_tab()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_accounts(quiet=True)
        self.root.after(120, self._drain)

    def on_close(self):
        self._closed = True
        self.root.destroy()

    # ---------------- 公共部件 ----------------
    def _btn(self, parent, text: str, command: Callable[[], None], **kw) -> ttk.Button:
        btn = ttk.Button(parent, text=text, command=command, **kw)
        self._all_buttons.append(btn)
        return btn

    def _make_log(self, parent) -> scrolledtext.ScrolledText:
        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True, padx=10, pady=(4, 10))
        txt = scrolledtext.ScrolledText(wrap, height=16, font=FONT_MONO, background="#101418",
                                        foreground="#D8DEE9", insertbackground="#D8DEE9", wrap="none")
        txt.pack(fill="both", expand=True)
        txt.configure(state="disabled")
        return txt

    def _date_row(self, parent, var: tk.StringVar, hint: str):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="日期").pack(side="left")
        ttk.Entry(row, textvariable=var, width=22).pack(side="left", padx=4)
        self._btn(row, "今天", lambda: var.set(C.today_bj())).pack(side="left", padx=2)
        self._btn(row, "昨天", lambda: var.set(C.shift_day(C.today_bj(), -1))).pack(side="left", padx=2)
        ttk.Label(row, text=hint, foreground=HINT_COLOR).pack(side="left", padx=8)
        return row

    def _build_toolbar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Label(bar, text="抖音直播数据导出助手", font=(FONT[0], 13, "bold")).pack(side="left")
        ttk.Label(bar, text="巨量百应直播大屏 · 分钟级成交金额 / 小时 GPM",
                  foreground=HINT_COLOR).pack(side="left", padx=8)

        ttk.Label(bar, text="账号:").pack(side="left", padx=(18, 2))
        self.account_box = ttk.Combobox(bar, textvariable=self.account_var, width=16,
                                        state="readonly", values=[C.DEFAULT_ACCOUNT])
        self.account_box.pack(side="left")
        self.account_box.bind("<<ComboboxSelected>>", lambda _e: self.on_account_change())
        self._btn(bar, "刷新", self.refresh_accounts).pack(side="left", padx=4)
        self._btn(bar, "登录 / 检查登录状态", self.on_login).pack(side="left")
        self.status = ttk.Label(bar, text="就绪", foreground=HINT_COLOR)
        self.status.pack(side="right", padx=10)

        bar2 = ttk.Frame(self.root)
        bar2.pack(fill="x", padx=10, pady=(0, 6))
        self.account_info = ttk.Label(bar2, text="", foreground=HINT_COLOR)
        self.account_info.pack(side="left")

    def _busy(self, busy: bool):
        self.running = busy
        state = "disabled" if busy else "normal"
        for btn in self._all_buttons:
            try:
                btn.configure(state=state)
            except tk.TclError:
                pass

    # ---------------- 一键日报 ----------------
    def _build_daily_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="  一键日报  ")
        ttk.Label(tab, text="一步到位:导出当天(可多天)全部已结束场次,并生成「日报_日期」文件夹——每场一张成交点表(time + gmv)"
                            "加一张日报汇总表。\n首次使用请先在「账号」页签点「登录」扫码;之后会话会被记住,不用反复扫码。",
                  justify="left", foreground=HINT_COLOR).pack(anchor="w", padx=10, pady=(10, 6))

        box = ttk.LabelFrame(tab, text="1. 要出日报的日期")
        box.pack(fill="x", padx=10, pady=4)
        self.daily_date = tk.StringVar(value=C.today_bj())
        self._date_row(box, self.daily_date, "(2026-09-04 或省略年份的 09-04;多天:09-01 09-02 或 09-01,09-02)")

        box2 = ttk.LabelFrame(tab, text="2. 选项")
        box2.pack(fill="x", padx=10, pady=4)
        self.daily_no_open = tk.BooleanVar(value=False)
        self.daily_no_watch = tk.BooleanVar(value=False)
        ttk.Checkbutton(box2, text="生成后不自动打开结果文件夹(--no-open)",
                        variable=self.daily_no_open).pack(anchor="w", padx=8, pady=(6, 0))
        ttk.Checkbutton(box2, text="关闭观看量自动捕获(--no-capture-watch):不生成「小时 GPM(真实分母)」",
                        variable=self.daily_no_watch).pack(anchor="w", padx=8, pady=(0, 8))

        btns = ttk.Frame(tab)
        btns.pack(fill="x", padx=10, pady=6)
        self._btn(btns, text="① 生成日报", command=self.on_daily).pack(side="left")
        self._btn(btns, text="② 只列出该天场次(不导出)", command=self.on_sessions).pack(side="left", padx=6)
        self._btn(btns, text="打开输出文件夹", command=self.on_open_output).pack(side="left")

        self.daily_log = self._make_log(tab)

    # ---------------- 按天导出 ----------------
    def _build_export_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="  按天导出  ")
        ttk.Label(tab, text="只导出分钟量表(不整理成日报文件夹):一天一个文件,文件名带 room_id 与日期。\n"
                            "导出时程序会自己读回页面上的累计成交金额做求和校验,对不上会在日志里写明。",
                  justify="left", foreground=HINT_COLOR).pack(anchor="w", padx=10, pady=(10, 6))

        box = ttk.LabelFrame(tab, text="日期")
        box.pack(fill="x", padx=10, pady=4)
        self.export_date = tk.StringVar(value=C.today_bj())
        self._date_row(box, self.export_date, "(只能一天;多天请用「一键日报」)")

        box2 = ttk.LabelFrame(tab, text="选项")
        box2.pack(fill="x", padx=10, pady=4)
        self.export_excel = tk.BooleanVar(value=True)
        self.export_compact = tk.BooleanVar(value=False)
        self.export_watch = tk.BooleanVar(value=False)
        ttk.Checkbutton(box2, text="另存 Excel(.xlsx;CSV 始终会输出)", variable=self.export_excel).pack(anchor="w", padx=8, pady=(6, 0))
        ttk.Checkbutton(box2, text="另生成成交点精简表(_compact.csv:time 只含 HH:MM,只保留 gmv>0 的行)",
                        variable=self.export_compact).pack(anchor="w", padx=8)
        ttk.Checkbutton(box2, text="同时捕获直播间观看量(观看档,--capture-watch):失败会优雅降级,不影响成交金额表",
                        variable=self.export_watch).pack(anchor="w", padx=8, pady=(0, 8))

        btns = ttk.Frame(tab)
        btns.pack(fill="x", padx=10, pady=6)
        self._btn(btns, text="导出该天全部已结束场次", command=self.on_export).pack(side="left")
        self._btn(btns, text="打开输出文件夹", command=self.on_open_output).pack(side="left", padx=6)

        self.export_log = self._make_log(tab)

    # ---------------- 汇总统计 ----------------
    def _build_stats_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="  汇总统计  ")
        ttk.Label(tab, text="读 data/outputs 里已导出的分钟量表,做当天场次级汇总(每场一行 + 全天合计),输出 stats_<日期>.csv。\n"
                            "缺分钟量表时可以勾选「自动补导出」,程序会先在线导出再统计。",
                  justify="left", foreground=HINT_COLOR).pack(anchor="w", padx=10, pady=(10, 6))

        box = ttk.LabelFrame(tab, text="统计日期")
        box.pack(fill="x", padx=10, pady=4)
        self.stats_date = tk.StringVar(value=C.today_bj())
        self._date_row(box, self.stats_date, "(只能一天)")
        self.stats_auto = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="分钟量表缺失时自动在线补导出(--auto;需要已登录)",
                        variable=self.stats_auto).pack(anchor="w", padx=8, pady=(0, 8))

        btns = ttk.Frame(tab)
        btns.pack(fill="x", padx=10, pady=6)
        self._btn(btns, text="生成汇总统计", command=self.on_stats).pack(side="left")
        self._btn(btns, text="打开输出文件夹", command=self.on_open_output).pack(side="left", padx=6)

        self.stats_log = self._make_log(tab)

    # ---------------- 账号 ----------------
    def _build_account_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="  账号  ")
        ttk.Label(tab, text="一个账号 = 一个独立的登录 profile 目录:默认账号用 config.yaml 的 browser.profile_dir,"
                            "具名账号用 data/accounts/<名字>/browser_profile。\n"
                            "切到哪个账号,上面工具栏的「登录 / 检查登录状态」和所有导出命令就都用它——两边不会串。\n"
                            "还有一个账号要挂:在下面输入新名字 → 点「用该名字登录」→ 在弹出的浏览器窗口里扫码。",
                  justify="left", foreground=HINT_COLOR).pack(anchor="w", padx=10, pady=(10, 6))

        box = ttk.LabelFrame(tab, text="已有账号")
        box.pack(fill="both", expand=False, padx=10, pady=4)
        self.acc_text = scrolledtext.ScrolledText(box, height=11, font=FONT_MONO, background="#F5F6F8",
                                                  foreground="#101418", wrap="none")
        self.acc_text.pack(fill="both", expand=True, padx=8, pady=8)
        self.acc_text.configure(state="disabled")

        box2 = ttk.LabelFrame(tab, text="新账号 / 登录")
        box2.pack(fill="x", padx=10, pady=4)
        row = ttk.Frame(box2)
        row.pack(fill="x", padx=8, pady=8)
        ttk.Label(row, text="账号名").pack(side="left")
        self.new_account = tk.StringVar(value="")
        ttk.Entry(row, textvariable=self.new_account, width=20).pack(side="left", padx=6)
        self._btn(row, text="用该名字登录(扫码)", command=self.on_login_new).pack(side="left")
        ttk.Label(row, text="(字母/数字开头,可含 _ . -,最长 32;留空=登录当前选中账号)", foreground=HINT_COLOR).pack(side="left", padx=8)
        row2 = ttk.Frame(box2)
        row2.pack(fill="x", padx=8, pady=(0, 8))
        self._btn(row2, text="刷新账号列表", command=self.refresh_accounts).pack(side="left")
        self._btn(row2, text="打开账号目录", command=self.on_open_accounts_dir).pack(side="left", padx=6)
        self._btn(row2, text="登录当前选中账号", command=self.on_login).pack(side="left")

        self.account_log = self._make_log(tab)

    # ---------------- 高级工具 ----------------
    def _build_tools_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="  高级工具  ")
        ttk.Label(tab, text="平时用不到:精简表转换(本地文件,无需登录)与只读探测(排查数据来源)。",
                  justify="left", foreground=HINT_COLOR).pack(anchor="w", padx=10, pady=(10, 6))

        box = ttk.LabelFrame(tab, text="成交点精简表(trim:把完整分钟量表变成 time + gmv 两列,只留 gmv>0)")
        box.pack(fill="x", padx=10, pady=4)
        r1 = ttk.Frame(box)
        r1.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(r1, text="输入 CSV").pack(side="left")
        self.trim_in = tk.StringVar(value="")
        ttk.Entry(r1, textvariable=self.trim_in).pack(side="left", fill="x", expand=True, padx=6)
        self._btn(r1, text="浏览…", command=self.on_pick_trim_in).pack(side="left")
        r2 = ttk.Frame(box)
        r2.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Label(r2, text="输出 CSV").pack(side="left")
        self.trim_out = tk.StringVar(value="")
        ttk.Entry(r2, textvariable=self.trim_out, width=46).pack(side="left", padx=6)
        ttk.Label(r2, text="(留空 = 同目录 <原名>_compact.csv)", foreground=HINT_COLOR).pack(side="left")
        self._btn(r2, text="运行 trim", command=self.on_trim).pack(side="left", padx=6)

        box2 = ttk.LabelFrame(tab, text="只读探测(probe:证据写到 data/probe/*.json)")
        box2.pack(fill="x", padx=10, pady=4)
        r3 = ttk.Frame(box2)
        r3.pack(fill="x", padx=8, pady=(6, 2))
        ttk.Label(r3, text="room_id").pack(side="left")
        self.probe_room = tk.StringVar(value="")
        ttk.Entry(r3, textvariable=self.probe_room, width=26).pack(side="left", padx=6)
        ttk.Label(r3, text="层级").pack(side="left", padx=(8, 2))
        self.probe_layer = tk.StringVar(value="all")
        ttk.Combobox(r3, textvariable=self.probe_layer, width=6, state="readonly",
                     values=["all", "L0", "L1", "L3"]).pack(side="left")
        self.probe_dry = tk.BooleanVar(value=True)
        ttk.Checkbutton(r3, text="只打印计划、不打开浏览器(--dry-run)",
                        variable=self.probe_dry).pack(side="left", padx=10)
        self._btn(r3, text="运行 probe", command=self.on_probe).pack(side="left")

        ttk.Label(tab, text="运行环境:Python %s(tkinter 界面)· 项目根:%s" % (platform.python_version(), C.PROJECT_ROOT),
                  foreground=HINT_COLOR).pack(anchor="w", padx=10, pady=(0, 6))

        self.tools_log = self._make_log(tab)

    # ---------------- 账号列表 ----------------
    def refresh_accounts(self, quiet: bool = False):
        try:
            rows = C.list_accounts()
        except Exception as exc:
            rows = []
            if not quiet:
                messagebox.showerror("读账号失败", str(exc))
        self._accounts = rows
        names = [str(r["account"]) for r in rows] or [C.DEFAULT_ACCOUNT]
        self.account_box.configure(values=names)
        if self.account_var.get() not in names:
            self.account_var.set(names[0])
        self._render_accounts(rows)
        self.on_account_change()

    def _render_accounts(self, rows: List[dict]):
        if not hasattr(self, "acc_text"):
            return
        lines: List[str] = []
        for row in rows:
            name = str(row["account"])
            mark = "*" if name == C.DEFAULT_ACCOUNT else " "
            tail = "(默认账号,不传 --account 时使用)" if name == C.DEFAULT_ACCOUNT else ""
            lines.append("%s %-14s %s" % (mark, name, tail))
            summary = C.account_summary(name)
            if summary:
                lines.append("    %s" % summary)
            lines.append("    profile: %s" % C.display_path(row["profile_dir"]))
        cfg_acc = ""
        try:
            cfg_acc = C.config_account()
        except Exception:
            cfg_acc = ""
        lines.append("")
        lines.append("config.yaml 里的 browser.account = %s" % (cfg_acc or "(空 = 默认账号)"))
        lines.append("说明:界面选了具体账号会覆盖它;选 default 则沿用 config.yaml 的写法。")
        lines.append("提醒:同一个账号不要同时开两个进程(Playwright 的 profile 目录是独占的);换账号不用重扫码。")
        self.acc_text.configure(state="normal")
        self.acc_text.delete("1.0", "end")
        self.acc_text.insert("end", "\n".join(lines) + "\n")
        self.acc_text.configure(state="disabled")

    def on_account_change(self):
        name = self.account_var.get() or C.DEFAULT_ACCOUNT
        try:
            prof = C.display_path(C.account_profile_dir(name))
        except Exception:
            prof = "?"
        summary = C.account_summary(name) or ""
        self.account_info.configure(text="当前账号:%s → profile: %s%s"
                                         % (name, prof, (" · " + summary) if summary else ""))

    def on_open_accounts_dir(self):
        target = C.PROJECT_ROOT / "data" / "accounts"
        if not target.is_dir():
            messagebox.showinfo("还没有账号目录", "还没有 data/accounts 目录。\n"
                                                 "在「账号」页签输入一个账号名并登录,程序会自动创建它。")
            return
        self._open_dir(str(target))

    # ---------------- 事件 ----------------
    def on_login(self):
        self._login_with(self.account_var.get() or C.DEFAULT_ACCOUNT)

    def on_login_new(self):
        name = (self.new_account.get() or "").strip()
        if not name:
            messagebox.showwarning("填个账号名", "请在输入框里填一个新的账号名(如 spa2),再点这个按钮。")
            return
        self._login_with(name)

    def _login_with(self, name: str):
        try:
            args = C.build_login_args(name)
        except C.FormError as exc:
            messagebox.showwarning("账号名不对", str(exc))
            return
        if name != C.DEFAULT_ACCOUNT and name not in [str(r["account"]) for r in self._accounts]:
            if not messagebox.askyesno(
                    "新账号", "「%s」还不在账号列表里。\n\n将要创建 data/accounts/%s 并打开浏览器等它扫码登录。\n继续吗?"
                    % (name, name)):
                return
        self.nb.select(3)
        self.new_account.set("")
        self._run("登录 / 检查登录状态(账号 %s)" % name, args, self.account_log,
                  lambda code: self._after_login(code, name))

    def _after_login(self, code: int, name: str):
        self.refresh_accounts(quiet=True)
        if code == 0:
            msg = "已登录「%s」,登录状态已记住,后面不用再扫码。" % name
            self.log(self.account_log, msg, OK_COLOR)
            messagebox.showinfo("登录成功", msg)

    def on_daily(self):
        self._guarded("一键日报", lambda: C.build_daily_args(
            self.daily_date.get(), no_open=bool(self.daily_no_open.get()),
            no_capture_watch=bool(self.daily_no_watch.get()), account=self.account_var.get()),
            self.daily_log)

    def on_export(self):
        self._guarded("按天导出", lambda: C.build_export_args(
            self.export_date.get(), excel=bool(self.export_excel.get()),
            compact=bool(self.export_compact.get()), capture_watch=bool(self.export_watch.get()),
            account=self.account_var.get()), self.export_log)

    def on_sessions(self):
        self._guarded("列出该天场次", lambda: C.build_sessions_args(
            self.daily_date.get(), account=self.account_var.get()), self.daily_log)

    def on_stats(self):
        self._guarded("汇总统计", lambda: C.build_stats_args(
            self.stats_date.get(), auto=bool(self.stats_auto.get()),
            account=self.account_var.get()), self.stats_log)

    def on_trim(self):
        self._guarded("成交点精简表", lambda: C.build_trim_args(
            self.trim_in.get(), self.trim_out.get()), self.tools_log)

    def on_probe(self):
        self._guarded("只读探测", lambda: C.build_probe_args(
            self.probe_room.get(), dry_run=bool(self.probe_dry.get()),
            layer=self.probe_layer.get(), account=self.account_var.get()), self.tools_log)

    def _guarded(self, title: str, build: Callable[[], Sequence[str]], log_widget):
        try:
            args = list(build())
        except C.FormError as exc:
            messagebox.showwarning("参数不对", str(exc))
            self.log(log_widget, "[!] %s" % exc, WARN_COLOR)
            return
        self._run(title, args, log_widget, None)

    def on_pick_trim_in(self):
        path = filedialog.askopenfilename(title="选择完整分钟量表 CSV",
                                          filetypes=[("CSV 表", "*.csv"), ("所有文件", "*.*")])
        if path:
            self.trim_in.set(path)

    def on_open_output(self):
        base = self.last_output or C.default_output_dir()
        target = C.safe_output_dir(base)
        if not target:
            messagebox.showwarning("打不开", "输出目录不在项目 data/ 下,已拒绝打开。")
            return
        self._open_dir(target)

    def _open_dir(self, target: str):
        if not os.path.isdir(target):
            messagebox.showinfo("还没有输出", "还没有这个目录:\n%s\n\n先跑一次导出/日报,再回来打开。" % target)
            return
        try:
            os.startfile(target)  # noqa: S606 (Windows 专用)
        except Exception as exc:
            messagebox.showerror("打不开", str(exc))

    # ---------------- 运行与日志 ----------------
    def log(self, widget, text: str, color: Optional[str] = None):
        widget.configure(state="normal")
        widget.insert("end", text + "\n")
        if color:
            widget.tag_add("c", "end-2l", "end-1l")
            widget.tag_configure("c", foreground=color)
        widget.see("end")
        widget.configure(state="disabled")

    def _run(self, title: str, args: Sequence[str], log_widget, on_done):
        if self.running:
            messagebox.showinfo("正在运行", "上一条命令还没跑完,请等它结束。")
            return
        self._busy(True)
        self.log(log_widget, "=" * 92)
        self.log(log_widget, "▶ %s:%s" % (title, C.pretty_command(args)), CMD_COLOR)
        threading.Thread(target=self._worker, args=(args, log_widget, on_done), daemon=True).start()

    def _worker(self, args, log_widget, on_done):
        def on_line(line: str):
            self.q.put(("line", log_widget, line))
        try:
            code = C.run_cli(args, on_line=on_line)
        except Exception as exc:
            self.q.put(("line", log_widget, "启动子进程失败:%s" % exc))
            code = -1
        self.q.put(("done", log_widget, code, on_done))

    def _drain(self):
        if self._closed:
            return
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "line":
                    _, widget, line = item
                    path = C.output_path_of(line)
                    if path:
                        self.last_output = path
                    self.log(widget, line, WARN_COLOR if self._is_warn(line) else None)
                else:
                    _, widget, code, on_done = item
                    self.log(widget, "◀ %s" % C.explain_exit(code), OK_COLOR if code == 0 else WARN_COLOR)
                    self._busy(False)
                    self.status.configure(text=C.explain_exit(code), foreground=OK_COLOR if code == 0 else WARN_COLOR)
                    if on_done:
                        on_done(code)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    @staticmethod
    def _is_warn(line: str) -> bool:
        text = line or ""
        if text.startswith("[!]") or text.startswith("Traceback"):
            return True
        for word in ("失败", "未通过", "错误", "不匹配", "未登录", "无法", "拒绝"):
            if word in text:
                return True
        return False


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
