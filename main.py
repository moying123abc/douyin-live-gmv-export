# -*- coding: utf-8 -*-
"""main.py — douyin-live-gmv-export CLI 入口(M1 骨架 + M2 导出)。

子命令:
  login    首次人工扫码登录,建立本地持久化浏览器会话(此后自动复用)
  probe    分层只读探测回放页数据来源,输出证据到 data/probe/*.json
  export   整场"每分钟新增成交金额"导出到 CSV(可另存 Excel),含内置校验

范围与边界(既定方案,勿越界):
  - 只在你已登录且有权限的页面会话内操作;
  - 不做登录绕过、不做 a_bogus 等签名逆向;
  - 不收集商品-营销-违规事件时间轴;
  - 浏览器 profile / 登录凭据只存本地(data/browser_profile),不提交进仓库。

示例:
  python main.py login
  python main.py probe --room-id 123456789 --dry-run
  python main.py probe --room-id 123456789
  # M2 离线自测(夹具驱动,无需登录):
  python main.py export --fixture data/samples/sample_ended_session_60min.json
  # M2 在线导出(需已登录且有权限):
  python main.py export --room-id 123456789 --excel
  # T10 按天:列出/导出某日已结束场次(自动解析当天每场 room_id):
  python main.py sessions --date 2026-09-04
  python main.py export --date 2026-09-04 --excel
  # 精简表:导出时加 --compact(另生成 _compact.csv:time 只含 HH:MM,只保留 gmv>0 行);
  # 或对已导出的完整表执行:
  python main.py trim --in data/outputs/live_20260904_7000000000000000001.csv
  # 某天场次级汇总统计(--auto 在分钟量表缺失时自动在线补导出;也可双击"统计当天.bat"):
  python main.py stats --date 2026-09-04
  python main.py stats --date 2026-09-04 --auto
"""
from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="douyin-live-gmv-export",
        description="导出抖音巨量百应直播数据大屏(eos.douyin.com/dp/liveScreen)整场分钟级数据(本地工具)。",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="命令")

    p_login = sub.add_parser("login", help="首次人工扫码登录(有头窗口),建立持久化会话")
    p_login.add_argument("--config", default=None, help="配置文件路径(默认读项目根 config.yaml)")
    p_login.set_defaults(func=cmd_login)

    p_probe = sub.add_parser("probe", help="分层只读探测回放页数据来源,输出 data/probe/*.json 证据")
    p_probe.add_argument("--room-id", default=None, help="直播间 room_id(探测目标)")
    p_probe.add_argument("--config", default=None, help="配置文件路径")
    p_probe.add_argument("--dry-run", action="store_true",
                         help="不打开浏览器、不采集数据,只打印探测计划与人工引导")
    p_probe.add_argument("--layer", choices=["all", "L0", "L1", "L3"], default="all",
                         help="限定尝试层级(默认 all:L0→L1→L3)")
    p_probe.set_defaults(func=cmd_probe)

    p_export = sub.add_parser("export", help="导出整场分钟成交金额序列为 CSV/Excel,含内置校验")
    p_export.add_argument("--room-id", default=None, help="直播间 room_id(已结束场次;在线导出用)")
    p_export.add_argument("--date", default=None,
                          help="按天导出 YYYY-MM-DD:自动解析当天每场 room_id 并各导出一个文件(默认今天,东八区)")
    p_export.add_argument("--config", default=None, help="配置文件路径")
    p_export.add_argument("--fixture", default=None,
                          help="离线夹具 JSON 路径(recorded sample;无登录/离线自测用)")
    p_export.add_argument("--out", default=None,
                          help="输出文件路径(不含扩展名,默认 data/outputs/<room_id 或夹具名>)")
    p_export.add_argument("--excel", action="store_true",
                          help="另存 Excel(.xlsx,需要 openpyxl;CSV 始终输出)")
    p_export.add_argument("--cumulative", default=None,
                          help="页面累计成交金额(元,覆盖夹具/配置;提供后启用求和校验)")
    p_export.add_argument("--compact", action="store_true",
                          help="除完整分钟量表外,另生成\"成交点精简表\"(_compact.csv:time 只含 HH:MM,只保留 gmv>0 的行)")
    p_export.add_argument("--capture-watch", action="store_true",
                          help="在线导出后自动尝试捕获回放页 直播间观看量(WatchCntTrend)分钟序列,"
                               "写观看档供小时 GPM 以真实看播量为分母输出(需已登录;失败优雅降级)")
    p_export.set_defaults(func=cmd_export)

    p_trim = sub.add_parser("trim", help="把已导出的完整分钟量表转换为\"成交点精简表\"(time 只含 HH:MM;只保留 gmv>0 行)")
    p_trim.add_argument("--in", dest="in_path", required=True, help="输入 CSV(标准导出的分钟量表)")
    p_trim.add_argument("--out", default=None, help="输出 CSV 路径(默认同目录 <原名>_compact.csv)")
    p_trim.set_defaults(func=cmd_trim)

    p_stats = sub.add_parser("stats", help="某天场次级汇总统计(读 data/outputs 已导出的分钟量表;--auto 缺失时自动在线补导出)")
    p_stats.add_argument("--date", default=None,
                         help="日期 YYYY-MM-DD(默认今天,东八区)")
    p_stats.add_argument("--auto", action="store_true",
                         help="当天分钟量表缺失时自动执行 export --date 在线补导出后再统计")
    p_stats.add_argument("--out", default=None, help="汇总 CSV 输出路径(默认 data/outputs/stats_<日期>.csv)")
    p_stats.add_argument("--config", default=None, help="配置文件路径")
    p_stats.set_defaults(func=cmd_stats)

    p_daily = sub.add_parser("daily", help="一键日报:导出某天全部已结束场次并生成\"两列表日报文件夹\"(成交点 HH:MM+gmv + 日报汇总)")
    p_daily.add_argument("--date", default=None,
                         help="日期 YYYY-MM-DD(默认今天,东八区)")
    p_daily.add_argument("--no-open", action="store_true",
                         help="生成后不自动打开结果文件夹(默认在 Windows 下自动打开)")
    p_daily.add_argument("--config", default=None, help="配置文件路径")
    p_daily.add_argument("--no-capture-watch", action="store_true",
                         help="关闭默认的观看档自动补捕获(默认:缺观看档时自动尝试捕获直播间观看量并生成"
                              "含 gpm 的成交点表/小时 GPM 表;需已登录,失败优雅降级为说明)")
    p_daily.add_argument("--capture-watch", action="store_true",
                         help="(兼容)显式开启观看档捕获——已是默认行为,无需再传")
    p_daily.set_defaults(func=cmd_daily)

    p_sessions = sub.add_parser("sessions", help="列出指定日期(东八区)该账号已结束场次(只读)")
    p_sessions.add_argument("--date", default=None,
                            help="日期 YYYY-MM-DD(默认今天,东八区)")
    p_sessions.add_argument("--config", default=None, help="配置文件路径")
    p_sessions.set_defaults(func=cmd_sessions)

    return parser


def cmd_login(args) -> int:
    import auth
    import config as cfgmod

    cfg = cfgmod.load_config(args.config)
    return auth.perform_login(cfg)


def cmd_probe(args) -> int:
    import config as cfgmod
    import probe

    cfg = cfgmod.load_config(args.config)
    if args.dry_run:
        return probe._probe_dry_run(cfg, args.room_id)
    if not args.room_id:
        print("[probe] 缺少 --room-id(或使用 --dry-run 查看计划)。", file=sys.stderr)
        return 2
    return probe.run_probe(cfg, args.room_id, dry_run=False, layer=args.layer)


def cmd_sessions(args) -> int:
    import config as cfgmod
    from extractor import sessions as sessions_mod

    cfg = cfgmod.load_config(args.config)
    date_str = (args.date or _dt.date.today().isoformat()).strip()
    try:
        day = sessions_mod.parse_date(date_str)
    except ValueError as exc:
        print(f"[sessions] {exc}", file=sys.stderr)
        return 2
    try:
        sessions = sessions_mod.fetch_day_sessions(cfg, day.isoformat())
    except sessions_mod.DaySessionsUnavailable as exc:
        print(f"[sessions] {exc}", file=sys.stderr)
        return 2
    if not sessions:
        print(f"[sessions] {day.isoformat()} 无已结束场次(该日无直播或尚未生成回放)。")
        return 0
    print(f"[sessions] {day.isoformat()} 已结束场次 {len(sessions)} 场:")
    for s in sessions:
        print("  -", sessions_mod.format_session_line(s))
    return 0


def cmd_trim(args) -> int:
    """把已导出的标准分钟量 CSV 转换为"成交点精简表"(time 只含 HH:MM;只保留 gmv>0 行)。"""
    import csv as _csv

    from exporter import to_csv_excel

    in_path = pathlib.Path(args.in_path)
    if not in_path.is_file():
        print(f"[trim] 输入文件不存在: {in_path}", file=sys.stderr)
        return 2
    try:
        with in_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = _csv.DictReader(fh)
            if reader.fieldnames is None or "time" not in reader.fieldnames or "gmv_min" not in reader.fieldnames:
                print(f"[trim] 输入 CSV 缺少 time/gmv_min 列(表头: {reader.fieldnames})", file=sys.stderr)
                return 2
            rows = list(reader)
    except OSError as exc:
        print(f"[trim] 读取失败: {exc}", file=sys.stderr)
        return 2
    try:
        comp_rows = to_csv_excel.compact_rows(rows)
    except ValueError as exc:
        print(f"[trim] 转换被拒(结构错误): {exc}", file=sys.stderr)
        return 3
    if args.out:
        out_path = pathlib.Path(args.out)
    else:
        out_path = in_path.with_name(in_path.stem + "_compact.csv")
    comp_path = to_csv_excel.write_compact_csv(out_path, comp_rows)
    comp_sum = sum(float(r["gmv_min"]) for r in comp_rows)
    total = sum(float(r.get("gmv_min") or 0) for r in rows)
    print(f"[trim] 完成: {comp_path}")
    print(f"[trim]   输入 {len(rows)} 行(含 {len(rows) - len(comp_rows)} 个 0 金额分钟已剔除)"
          f",输出非0成交点 {len(comp_rows)} 行,金额合计 {comp_sum:.2f} 元(原表合计 {total:.2f} 元)")
    return 0


def cmd_daily(args) -> int:
    """一键日报:确保某天已导出(可自动在线导出)并产出两列日报文件夹。"""
    import csv as _csv
    import os

    import config as cfgmod
    from extractor import daily_stats
    from exporter import to_csv_excel

    cfg = cfgmod.load_config(args.config)
    date_str = (args.date or _dt.date.today().isoformat()).strip()
    try:
        day = _dt.date.fromisoformat(date_str)
    except ValueError as exc:
        print(f"[daily] 日期格式应为 YYYY-MM-DD: {date_str!r}", file=sys.stderr)
        return 2
    out_rel = cfg.get("export", {}).get("out_dir", "data/outputs")
    out_dir = pathlib.Path(out_rel)
    if not out_dir.is_absolute():
        out_dir = cfgmod.PROJECT_ROOT / out_dir

    skipped_info: list = []
    # 默认自动补捕获观看档(缺观看档才开一次浏览器窗口;可用 --no-capture-watch 关闭)
    want_capture = not bool(getattr(args, "no_capture_watch", False))
    files = daily_stats.day_files(out_dir, date_str)
    if not files:
        print(f"[daily] {date_str} 尚无已导出的分钟量表,自动在线导出(--compact)…")
        code, info = _export_day(cfg, day, excel=False, compact=True,
                                 capture_watch=want_capture)
        skipped_info = info.get("skipped", [])
        if code != 0:
            print(f"[daily] 当天没有成功导出的场次(退出码 {code}),无法生成日报。", file=sys.stderr)
            return code
        files = daily_stats.day_files(out_dir, date_str)
    if not files:
        print(f"[daily] {date_str} 没有可用的分钟量表文件({out_dir}).", file=sys.stderr)
        print("[daily] 人工引导: 先 python main.py login,再 python main.py daily "
              f"--date {date_str}。", file=sys.stderr)
        return 2

    # 确保每场都有两列精简表(缺失时由本地分钟量表离线推导,不重复联网)
    for cf in files:
        comp = cf.with_name(cf.stem + "_compact.csv")
        if comp.is_file():
            continue
        with cf.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(_csv.DictReader(fh))
        try:
            comp_rows = to_csv_excel.compact_rows(rows)
        except ValueError as exc:
            print(f"[daily] {cf.name} 无法生成精简表(结构拒绝): {exc}", file=sys.stderr)
            return 3
        to_csv_excel.write_compact_csv(comp, comp_rows)
        print(f"[daily] 补生成精简表: {comp}")

    # 缺观看档时默认自动补捕获(有观看档则跳过,不开窗;失败优雅降级打印引导)
    import exporter.hourly_gpm_output as hgo
    if want_capture:
        for cf in files:
            room = cf.stem.split("_", 2)[-1] if cf.stem.count("_") >= 2 else cf.name
            watch_file = cf.with_name(cf.stem + hgo.WATCH_MIN_SUFFIX)
            if watch_file.is_file():
                continue
            print(f"[daily] 自动补捕获直播间观看量(room {room};会短暂打开浏览器,失败有引导提示)…")
            _try_capture_watch(cfg, out_dir, room, date_str)

    # 成交点 gpm 列:每成交分钟行的 gpm=其所属"小时"的小时 GPM(需观看档;无观看档场次留空并说明)
    enrich: dict = {}
    for cf in files:
        room = cf.stem.split("_", 2)[-1] if cf.stem.count("_") >= 2 else cf.name
        watch_file = cf.with_name(cf.stem + hgo.WATCH_MIN_SUFFIX)
        if not watch_file.is_file():
            continue
        with cf.open("r", encoding="utf-8-sig", newline="") as fh:
            crows = list(_csv.DictReader(fh))
        with watch_file.open("r", encoding="utf-8-sig", newline="") as fh:
            wrows = list(_csv.DictReader(fh))
        try:
            enrich[room] = hgo.hour_gpm_by_minute(crows, wrows, day)
        except Exception as exc:  # noqa: BLE001 — 单场映射失败不阻断整日
            print(f"[daily] 场次 {room} 小时 GPM 映射失败(该场 gpm 列留空): {exc}", file=sys.stderr)

    comp_files = daily_stats.compact_files(out_dir, date_str)
    folder = daily_stats.publish_day(out_dir, date_str, comp_files, files,
                                     gpm_by_room=(enrich or None))
    if skipped_info:
        note_path = folder / "_跳过说明.txt"
        with note_path.open("w", encoding="utf-8") as fh:
            fh.write(f"本日 {date_str} 导出时被跳过的场次(不影响其他场次):\n")
            for sk in skipped_info:
                fh.write(f"- room_id={sk.get('room_id')}  {sk.get('start') or '?'}~"
                         f"{sk.get('end') or '?'}\n  原因: {sk.get('reason')}\n")
        print(f"[daily]   注意: {len(skipped_info)} 场被跳过,明细见 {note_path.name}")
    print(f"[daily] 日报已生成: {folder}")
    print("[daily]   每场: 成交点_<room_id>.csv(time 只含 HH:MM;只保留 gmv>0 行;"
          "有观看档时含 gpm 列=所属小时 GPM,无则 gpm 留空并在 成交点_gpm_说明.txt 说明)")
    print(f"[daily]   汇总: 日报汇总_{date_str.replace('-', '')}.csv(场次级统计+合计)")

    # 小时 GPM 表(跨同日多场按小时叠加;观看档在上一段已补捕获/复用)
    hres = hgo.publish_day_hourly(out_dir, date_str, folder)
    if hres.get("ok") and hres.get("csv"):
        print(f"[daily] 小时 GPM 表(跨场按小时叠加): {hres['csv']}")
    else:
        print(f"[daily] 小时 GPM 降级说明(观看序列不可得/口径待对拍;不影响日报): {hres.get('note')}")
    if not getattr(args, "no_open", False) and sys.platform.startswith("win"):
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
            print("[daily] 已在资源管理器中打开结果文件夹。")
        except OSError:
            pass
    return 0


def cmd_stats(args) -> int:
    """某天场次级汇总统计:默认只读本地已导出分钟量表;--auto 缺失时在线补导出。"""
    import config as cfgmod
    from extractor import daily_stats

    cfg = cfgmod.load_config(args.config)
    date_str = (args.date or _dt.date.today().isoformat()).strip()
    try:
        _dt.date.fromisoformat(date_str)
    except ValueError as exc:
        print(f"[stats] 日期格式应为 YYYY-MM-DD: {date_str!r}", file=sys.stderr)
        return 2
    out_rel = cfg.get("export", {}).get("out_dir", "data/outputs")
    out_dir = pathlib.Path(out_rel)
    if not out_dir.is_absolute():
        out_dir = cfgmod.PROJECT_ROOT / out_dir

    files = daily_stats.day_files(out_dir, date_str)
    if not files and getattr(args, "auto", False):
        print(f"[stats] {date_str} 暂无已导出的分钟量表,自动在线补导出(--auto)…")
        day = _dt.date.fromisoformat(date_str)
        code, _ = _export_day(cfg, day, excel=False, compact=False)
        if code != 0:
            print(f"[stats] 自动补导出失败(退出码 {code}),无法统计。", file=sys.stderr)
            return code
        files = daily_stats.day_files(out_dir, date_str)
    if not files:
        print(f"[stats] {date_str} 没有已导出的分钟量表文件({out_dir}).", file=sys.stderr)
        print("[stats] 人工引导: python main.py export --date "
              f"{date_str}(需已登录)后重试;或 python main.py stats --date {date_str} --auto 自动补导出。",
              file=sys.stderr)
        return 2

    try:
        summary = daily_stats.summarize(files)
    except ValueError as exc:
        print(f"[stats] 统计失败(数据异常): {exc}", file=sys.stderr)
        return 3
    t = summary["totals"]
    print(f"[stats] {date_str} 已结束场次统计(源文件 {len(files)} 个):")
    for s in summary["stats"]:
        print("  -", daily_stats.format_stats_line(s))
    print(f"[stats] 合计: {t['sessions']} 场 | 直播分钟 {t['total_minutes']} | "
          f"成交分钟 {t['deal_minutes']} | 成交金额 {t['gmv_sum']:.2f} 元")
    if args.out:
        out_path = pathlib.Path(args.out)
    else:
        out_path = out_dir / f"stats_{date_str.replace('-', '')}.csv"
    written = daily_stats.write_summary_csv(out_path, summary)
    print(f"[stats] 汇总表已生成: {written}")
    return 0


def _try_capture_watch(cfg: dict, out_dir: pathlib.Path, room_id: str,
                       session_date: str) -> dict:
    """尝试在线捕获回放页 直播间观看量(WatchCntTrend)分钟序列并写观看档。

    永不抛出/永不阻断:失败仅打印降级说明,供调用方继续。返回 watch_capture 结果 dict。
    """
    from exporter import watch_capture
    try:
        res = watch_capture.capture_watch_minutes(
            cfg, room_id, session_date, out_dir=out_dir,
            auto=True, poll_seconds=int(cfg.get("probe", {}).get("watch_capture_poll_seconds", 90)))
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "reason": f"捕获调用异常: {exc}"}
    if res.get("ok") and res.get("watch_file"):
        print(f"[export] 观看档已生成: {res['watch_file']}")
        cl = res.get("closure") or {}
        print(f"[export]   WatchCntTrend Σ={cl.get('sum')} vs ServerWatchCntTd="
              f"{cl.get('ServerWatchCntTd')} diff={cl.get('diff')}(如实标注,见口径文档)")
        if res.get("evidence_file"):
            print(f"[export]   证据: {res['evidence_file']}")
    else:
        print(f"[export] 小时 GPM:观看序列捕获失败,降级({res.get('reason') or 'unknown'});"
              "不影响成交金额导出。如需人工协助,运行: python -m exporter.watch_capture "
              f"--room-id {room_id} --date {session_date} --assist")
    return res


def _export_day(cfg: dict, day: _dt.date, *, excel: bool = False,
                compact: bool = False,
                cumulative_override=None, capture_watch: bool = False) -> tuple:
    """按天逐场导出(容错版):单场"无序列数据/导出失败"只跳过并继续,不中断全天。

    返回 (退出码, info);info = {exported:[room], skipped:[{room_id,start,end,reason}],
    total}。退出码:0 = 至少成功一场;2 = 当天一场都没导出成功(或场次查询失败,
    此时 info 含 error)。调用方(export --date / stats --auto / daily)据此处理。
    """
    import config as cfgmod
    from extractor import sessions as sessions_mod
    from extractor import trend

    date_str = day.isoformat()
    info: dict = {"exported": [], "skipped": [], "total": 0, "error": None}
    try:
        sessions = sessions_mod.fetch_day_sessions(cfg, date_str)
    except sessions_mod.DaySessionsUnavailable as exc:
        print(f"[export] {exc}", file=sys.stderr)
        info["error"] = str(exc)
        return 2, info
    info["total"] = len(sessions)
    if not sessions:
        print(f"[export] {date_str} 无已结束场次,无输出。")
        return 0, info
    out_rel = cfg.get("export", {}).get("out_dir", "data/outputs")
    out_dir = pathlib.Path(out_rel)
    if not out_dir.is_absolute():
        out_dir = cfgmod.PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[export] {date_str} 待导出 {len(sessions)} 场 → {out_dir}")

    for s in sessions:
        room = s["room_id"]
        line = sessions_mod.format_session_line(s)
        print(f"[export] 场次: {line}")
        try:
            rows, meta = trend.read_live(cfg, room)
        except trend.TrendUnavailable as exc:
            reason = f"无整场序列数据(no_series): {exc}"
            info["skipped"].append({"room_id": room, "start": s.get("start_time"),
                                    "end": s.get("end_time"), "reason": reason})
            print(f"[export]   跳过该场(不影响其他场次): {reason}", file=sys.stderr)
            continue
        code = _finalize_day_session(cfg, rows, meta, out_dir, date_str, room,
                                     cumulative_override=cumulative_override,
                                     excel=excel, compact=compact)
        if code != 0:
            reason = f"导出被拒(校验/写盘失败, exit={code})"
            info["skipped"].append({"room_id": room, "start": s.get("start_time"),
                                    "end": s.get("end_time"), "reason": reason})
            print(f"[export]   跳过该场(不影响其他场次): {reason}", file=sys.stderr)
            continue
        info["exported"].append(room)
        # T12:在线导出历史场次后,可选自动捕获直播间观看量分钟序列(真实分母;失败优雅降级)
        if capture_watch:
            print(f"[export]   尝试捕获直播间观看量(WatchCntTrend)…")
            _try_capture_watch(cfg, out_dir, room, date_str)
    print(f"[export] {date_str} 按天导出结果:成功 {len(info['exported'])} 场,"
          f"跳过 {len(info['skipped'])} 场,共 {info['total']} 场。")
    for sk in info["skipped"]:
        print(f"[export]   - 跳过 room_id={sk['room_id']}: {sk['reason']}")
    if info["exported"]:
        print("[export] 小时 GPM:观看序列仅在 --capture-watch 时捕获;未启用则维持降级"
              "(不影响成交金额导出,日报里可查看小时 GPM 说明)。")
    return (0 if info["exported"] else 2), info


def cmd_export_date(args) -> int:
    import config as cfgmod
    from extractor import sessions as sessions_mod

    cfg = cfgmod.load_config(args.config)
    date_str = (args.date or _dt.date.today().isoformat()).strip()
    try:
        day = sessions_mod.parse_date(date_str)
    except ValueError as exc:
        print(f"[export] {exc}", file=sys.stderr)
        return 2
    cumulative_override = None
    if args.cumulative is not None:
        try:
            cumulative_override = float(args.cumulative)
        except ValueError:
            print(f"[export] --cumulative 不是数值: {args.cumulative!r}", file=sys.stderr)
            return 2
    code, _ = _export_day(cfg, day, excel=bool(args.excel),
                          compact=bool(getattr(args, "compact", False)),
                          cumulative_override=cumulative_override,
                          capture_watch=bool(getattr(args, "capture_watch", False)))
    return code


def _finalize_day_session(cfg: dict, rows, meta: dict, out_dir: pathlib.Path,
                          date_str: str, room_id: str, *,
                          cumulative_override=None, excel: bool = False,
                          compact: bool = False) -> int:
    """按天导出单场收尾:校验 → 写 live_<YYYYMMDD>_<room_id>.csv(.xlsx[, _compact.csv])。

    与 --room-id 单场使用同一套口径与校验;校验失败返回 3 且不产出文件。
    (独立函数便于离线单测覆盖 export --date 的逐场流水线。)
    """
    import config as cfgmod
    from extractor import sessions as sessions_mod
    from extractor import validation
    from exporter import to_csv_excel

    vcfg = cfg.get("validation", {})
    cumulative = meta.get("cumulative_total") if cumulative_override is None else cumulative_override
    reference_date = None
    if meta.get("session_date"):
        try:
            reference_date = _dt.date.fromisoformat(str(meta["session_date"]))
        except ValueError:
            reference_date = None
    try:
        report = validation.validate(
            rows,
            cumulative_total=cumulative,
            duration_minutes=meta.get("duration_minutes"),
            reference_date=reference_date,
            sum_tolerance_abs=float(vcfg.get("sum_abs_tolerance", validation.DEFAULT_SUM_ABS_TOLERANCE)),
            sum_tolerance_rel=float(vcfg.get("sum_rel_tolerance", validation.DEFAULT_SUM_REL_TOLERANCE)),
            row_tolerance_minutes=int(vcfg.get("row_tolerance_minutes", validation.DEFAULT_ROW_TOLERANCE)),
        )
    except Exception as exc:  # noqa: BLE001 — 校验实现异常也拒绝输出,不 traceback
        print(f"[export] 校验执行异常(拒绝输出): {exc}", file=sys.stderr)
        return 3
    if not report["ok"]:
        print(f"[export] 场次 {room_id} 内置校验未通过,拒绝输出:", file=sys.stderr)
        print(validation.format_report(report), file=sys.stderr)
        return 3
    base = out_dir / sessions_mod.session_output_name(date_str, room_id, "")
    csv_path = to_csv_excel.write_csv(base, rows)
    summary = (f"[export]   [ok] {csv_path}  校验通过(行数={report['rows']},"
               f"合计={report['total_gmv_min']:.2f} 元"
               + (f",累计={cumulative:.2f})" if cumulative is not None else ",累计SKIP待复核)"))
    print(summary)
    if compact:
        try:
            comp_rows = to_csv_excel.compact_rows(rows)
        except ValueError as exc:
            print(f"[export] 生成精简表失败(结构拒绝): {exc}", file=sys.stderr)
            return 3
        comp_path = to_csv_excel.write_compact_csv(
            csv_path.with_name(csv_path.stem + "_compact"), comp_rows
        )
        comp_sum = sum(float(r["gmv_min"]) for r in comp_rows)
        print(f"[export]   [ok] 成交点精简表: {comp_path}  (非0分钟 {len(comp_rows)} 行,合计 {comp_sum:.2f} 元)")
    if excel:
        try:
            xlsx_path = to_csv_excel.write_xlsx(base, rows)
            print(f"[export]   [ok] {xlsx_path}")
        except to_csv_excel.XlsxUnavailable as exc:
            print(f"[export] {exc}(CSV 已正常输出)")
    return 0


def cmd_export(args) -> int:
    if getattr(args, "date", None):
        return cmd_export_date(args)
    import config as cfgmod
    from extractor import extra_metrics, trend, validation
    from exporter import schema as schema_mod
    from exporter import to_csv_excel

    cfg = cfgmod.load_config(args.config)
    if args.fixture:
        try:
            rows, meta = trend.load_fixture(args.fixture)
        except trend.TrendUnavailable as exc:
            print(f"[export] 夹具不可用: {exc}", file=sys.stderr)
            return 2
        print(f"[export] 数据来源: 离线夹具 {pathlib.Path(args.fixture).name}(合成样例,离线自测)")
    else:
        if not args.room_id:
            print("[export] 需要 --room-id(在线导出)或 --fixture <json>(离线自测)。", file=sys.stderr)
            return 2
        print(f"[export] 数据来源: 在线读取回放页 room_id={args.room_id}(需已登录且有权限)…")
        try:
            rows, meta = trend.read_live(cfg, args.room_id)
        except trend.TrendUnavailable as exc:
            print(f"[export] 在线读取不可用: {exc}", file=sys.stderr)
            print("[export] 人工引导: python main.py login 后重试;或先用 --fixture 离线自测。", file=sys.stderr)
            return 2

    # M3 扩展指标:仅在 extra_metrics.enabled=true 时尝试并表(默认关闭 → 与 M2 完全一致)
    extra_cols: list = []
    if extra_metrics.is_enabled(cfg):
        try:
            rows, extra_cols, extra_notes = extra_metrics.enrich(
                cfg, rows, fixture_path=args.fixture, room_id=args.room_id
            )
        except extra_metrics.ExtraMetricsError as exc:
            print(f"[export] 扩展指标并表失败(拒绝伪造粒度): {exc}", file=sys.stderr)
            return 2
        for note in extra_notes:
            print(f"[export] [extra_metrics] {note}")
        if extra_cols:
            print(f"[export] [extra_metrics] 已并入列: {extra_cols}")

    vcfg = cfg.get("validation", {})
    cumulative = meta.get("cumulative_total")
    if args.cumulative is not None:
        try:
            cumulative = float(args.cumulative)
        except ValueError:
            print(f"[export] --cumulative 不是数值: {args.cumulative!r}", file=sys.stderr)
            return 2
    duration_minutes = meta.get("duration_minutes")
    reference_date = None
    if meta.get("session_date"):
        try:
            reference_date = _dt.date.fromisoformat(str(meta["session_date"]))
        except ValueError:
            reference_date = None

    try:
        report = validation.validate(
            rows,
            cumulative_total=cumulative,
            duration_minutes=duration_minutes,
            reference_date=reference_date,
            sum_tolerance_abs=float(vcfg.get("sum_abs_tolerance", validation.DEFAULT_SUM_ABS_TOLERANCE)),
            sum_tolerance_rel=float(vcfg.get("sum_rel_tolerance", validation.DEFAULT_SUM_REL_TOLERANCE)),
            row_tolerance_minutes=int(vcfg.get("row_tolerance_minutes", validation.DEFAULT_ROW_TOLERANCE)),
        )
    except Exception as exc:  # noqa: BLE001 — 校验实现异常也走“拒绝输出”,绝不 traceback
        print("[export] 内置校验执行异常,拒绝输出(不静默产出):", file=sys.stderr)
        print(f"[export] 明细: {exc}", file=sys.stderr)
        return 3
    if not report["ok"]:
        print("[export] 内置校验未通过,拒绝输出(不静默产出坏数据):", file=sys.stderr)
        print(validation.format_report(report), file=sys.stderr)
        return 3

    # 校验通过 → 输出(扩展列仅在 M3 开关启用且并入成功时追加,保持基础四列语义不变)
    if extra_cols:
        output_columns = schema_mod.headers_with(extra_cols)
    else:
        output_columns = None
    if args.room_id:
        stem = str(args.room_id)
    elif args.fixture:
        stem = pathlib.Path(args.fixture).stem
    else:  # 不可达:上方已拦截
        stem = "export"
    if args.out:
        prefix = pathlib.Path(args.out)
    else:
        out_dir = pathlib.Path(cfg.get("export", {}).get("out_dir", "data/outputs"))
        if not out_dir.is_absolute():
            out_dir = cfgmod.PROJECT_ROOT / out_dir
        prefix = out_dir / str(stem)
    if output_columns is not None:
        csv_path = to_csv_excel.write_csv(prefix, rows, columns=output_columns)
    else:
        csv_path = to_csv_excel.write_csv(prefix, rows)  # 与 M2 完全一致(四列表头)
    print(f"[export] CSV 已生成: {csv_path}")
    if args.compact:
        try:
            comp_rows = to_csv_excel.compact_rows(rows)
        except ValueError as exc:
            print(f"[export] 生成精简表失败(结构拒绝): {exc}", file=sys.stderr)
            return 3
        comp_path = to_csv_excel.write_compact_csv(
            csv_path.with_name(csv_path.stem + "_compact"), comp_rows
        )
        comp_sum = sum(float(r["gmv_min"]) for r in comp_rows)
        print(f"[export] 成交点精简表已生成: {comp_path}  (非0分钟 {len(comp_rows)} 行,合计 {comp_sum:.2f} 元)")
    if args.excel:
        try:
            if output_columns is not None:
                xlsx_path = to_csv_excel.write_xlsx(prefix, rows, columns=output_columns)
            else:
                xlsx_path = to_csv_excel.write_xlsx(prefix, rows)
            print(f"[export] Excel 已生成: {xlsx_path}")
        except to_csv_excel.XlsxUnavailable as exc:
            print(f"[export] {exc}(CSV 已正常输出;如需 Excel 请安装 openpyxl)")

    # 校验通过的摘要(verify 要求打印“校验通过”)
    print("[export] 校验通过 ✓")
    print(f"         行数={report['rows']}  首={report['time_first']}  末={report['time_last']}")
    print(f"         分钟金额合计={report['total_gmv_min']:.2f} 元"
          + (f"  累计(页面/夹具)={cumulative:.2f} 元" if cumulative is not None else "  (未提供累计,求和校验 SKIP,待在线复核)"))
    print(f"         时间格式: {meta.get('time_format') or '(见夹具/页面)'}  层级: {meta.get('layer_hit')}")
    for item in (meta.get("assumptions") or [])[:5]:
        print(f"         [assumption] {item}")
    recheck = meta.get("online_recheck_required") or []
    if recheck:
        print("[export] 待在线复核清单:")
        for item in recheck:
            print(f"         - {item}")

    # 小时级 GPM 联动(T3):观看数据可得才产出,不可得/无效时明确跳过并提示,绝不阻断既有导出
    if args.fixture:
        import exporter.hourly_gpm_output as hgo
        try:
            report = hgo.fixture_hourly_report(args.fixture, rows)
        except Exception as exc:  # noqa: BLE001 — 夹具观看段结构/聚合异常 → 跳过提示
            print(f"[export] 小时 GPM:夹具 gpm_watch 无效或聚合失败,跳过({exc});"
                  "不影响成交金额导出。", file=sys.stderr)
            report = None
        if report is None:
            print("[export] 小时 GPM:夹具未携带有效 gpm_watch 观看序列,跳过(不影响成交金额导出)。")
        else:
            try:
                hpath = hgo.write_hourly_csv(
                    hgo.hourly_sibling_path(prefix),
                    hgo.report_rows(report, report.get("_watch_semantics", "")),
                )
                t = report["totals"]
                print(f"[export] 小时 GPM 已生成: {hpath}  (小时桶 {len(report['rows'])} 个,"
                      f"GMV 合计 {t['gmv']:.2f} 元,views 合计 {t['views']},"
                      f"整场 GPM 锚点={t['gpm']})")
            except Exception as exc:  # noqa: BLE001 — 写盘失败不阻断主 CSV
                print(f"[export] 小时 GPM 写盘失败(已跳过,不影响成交金额导出): {exc}", file=sys.stderr)
    elif args.room_id:
        if getattr(args, "capture_watch", False):
            # T12:在线单场导出后可选捕获直播间观看量分钟序列 → 真实分母小时 GPM
            session_date = None
            for r0 in (rows or [])[:1]:
                raw = str((r0 or {}).get("session_date") or "").strip()
                if len(raw) >= 10:
                    try:
                        session_date = _dt.date.fromisoformat(raw[:10]).isoformat()
                    except ValueError:
                        session_date = None
            if not session_date:
                session_date = args.date or _dt.date.today().isoformat()
            prefix_dir = pathlib.Path(prefix).parent
            out_cfg_dir = pathlib.Path(cfg.get("export", {}).get("out_dir", "data/outputs"))
            if not out_cfg_dir.is_absolute():
                out_cfg_dir = cfgmod.PROJECT_ROOT / out_cfg_dir
            target_dir = prefix_dir if str(prefix_dir).startswith(str(out_cfg_dir)) else out_cfg_dir
            print(f"[export] 尝试捕获直播间观看量(WatchCntTrend,room {args.room_id})…")
            res = _try_capture_watch(cfg, target_dir, args.room_id, session_date)
            if res.get("ok") and res.get("watch_file"):
                # 由观看档 + 本场 gmv 分钟生成真实分母 _hourly.csv(与夹具路径同构)
                import exporter.hourly_gpm_output as hgo
                try:
                    watch = hgo._read_watch_min(pathlib.Path(res["watch_file"]))
                    watch_rows = [{"time": r["time"], "views": r["views"]} for r in watch["rows"]]
                    rep = _hourly_report_from_rows(rows, watch_rows, session_date,
                                                   watch["semantics"])
                    hpath = hgo.write_hourly_csv(hgo.hourly_sibling_path(prefix),
                                                 hgo.report_rows(rep, rep["_watch_semantics"]))
                    t = rep["totals"]
                    print(f"[export] 小时 GPM(真实分母)已生成: {hpath}  (小时桶 "
                          f"{len(rep['rows'])} 个,GMV {t['gmv']:.2f} 元,views {t['views']},"
                          f"整场 GPM 锚点={t['gpm']})")
                except Exception as exc:  # noqa: BLE001 — 观看聚合失败不阻断主导出
                    print(f"[export] 小时 GPM 生成失败(已跳过,不影响成交金额导出): {exc}",
                          file=sys.stderr)
        else:
            print("[export] 小时 GPM:在线观看序列未捕获(加 --capture-watch 自动捕获;"
                  "或先 main.py login),已跳过;不影响成交金额导出。")
    return 0


def _hourly_report_from_rows(gmv_rows, watch_rows, session_date, semantics):
    """由标准分钟成交行 + 观看分钟行生成 hourly_gpm 报告(供单场 --capture-watch 输出)。"""
    from extractor import hourly_gpm as _hg
    ref = None
    if session_date:
        try:
            ref = _dt.date.fromisoformat(session_date)
        except ValueError:
            ref = None
    report = _hg.hourly_gpm(gmv_rows, watch_rows, watch_level="minute",
                            reference_date=ref)
    report["_watch_semantics"] = semantics
    return report


def main(argv=None) -> int:
    import config as cfgmod
    cfgmod.setup_utf8_io()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
