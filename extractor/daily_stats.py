# -*- coding: utf-8 -*-
"""extractor/daily_stats.py — 按天汇总统计(T11 便捷入口的数据层)。

输入:data/outputs 下某日已导出的"标准分钟量表"(live_YYYYMMDD_<room_id>.csv,
由 export --date / --room-id 生成,四列 room_id/session_date/time/gmv_min)。
输出:场次级统计——每场起止时间(取自 time 首末行,保留页面外观)、
总分钟数、成交分钟数(非0)、成交金额合计;并给出全天汇总。

只读本地文件,不做任何在线请求;文件缺失时由调用方决定是否在线补导出。
"""
from __future__ import annotations

import csv
import pathlib
import re
from typing import Dict, List, Sequence

CANONICAL_RE = re.compile(r"^live_(\d{8})_.+\.csv$")

SUMMARY_HEADER = [
    "session_date", "room_id", "start", "end",
    "total_minutes", "deal_minutes", "gmv_sum",
]
TOTAL_LABEL = "合计"


def day_files(out_dir: pathlib.Path, date_str: str) -> List[pathlib.Path]:
    """返回某日(YYYY-MM-DD)的标准分钟量表文件(排除 *_compact.csv 与 *_watch_min.csv)。

    命名约定 live_YYYYMMDD_<room_id>.csv(export --date / --room-id 产物);
    *_watch_min.csv 是小时 GPM 联动用的观看档,不是场次量表,必须排除,
    否则会被误当场次进入 cmd_daily 补精简表/统计/发布流程(F1 修复)。
    """
    ymd = date_str.replace("-", "")
    out = []
    if not out_dir.is_dir():
        return out
    for p in sorted(out_dir.glob(f"live_{ymd}_*.csv")):
        name = p.name
        if name.endswith("_compact.csv") or "_watch_min.csv" in name:
            continue
        if CANONICAL_RE.match(name):
            out.append(p)
    return out


def stat_csv(path: pathlib.Path) -> Dict:
    """读一个标准分钟量 CSV,返回场次统计(不联网)。"""
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        need = {"room_id", "session_date", "time", "gmv_min"}
        if reader.fieldnames is None or not need.issubset(set(reader.fieldnames)):
            raise ValueError(f"{path.name} 缺少必需列(需要 {sorted(need)}): {reader.fieldnames}")
        for row in reader:
            rows.append(row)
    if not rows:
        raise ValueError(f"{path.name} 为空(无分钟行)")
    first = str(rows[0].get("time") or "").strip()
    last = str(rows[-1].get("time") or "").strip()
    total_minutes = len(rows)
    deal_minutes = 0
    gmv_sum = 0.0
    for row in rows:
        try:
            v = float(row.get("gmv_min"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{path.name} 含非数值 gmv_min: {row.get('gmv_min')!r}") from exc
        if v > 0:
            deal_minutes += 1
            gmv_sum += v
    return {
        "session_date": str(rows[0].get("session_date") or "").strip(),
        "room_id": str(rows[0].get("room_id") or "").strip(),
        "start": first,
        "end": last,
        "total_minutes": total_minutes,
        "deal_minutes": deal_minutes,
        "gmv_sum": round(gmv_sum, 2),
    }


def summarize(paths: Sequence[pathlib.Path]) -> Dict:
    """汇总多场:返回 {stats:[每场 dict], totals:{...}}。"""
    stats = [stat_csv(p) for p in paths]
    totals = {
        "sessions": len(stats),
        "total_minutes": sum(s["total_minutes"] for s in stats),
        "deal_minutes": sum(s["deal_minutes"] for s in stats),
        "gmv_sum": round(sum(s["gmv_sum"] for s in stats), 2),
    }
    return {"stats": stats, "totals": totals}


def write_summary_csv(path, summary: Dict) -> pathlib.Path:
    """写汇总 CSV(UTF-8 BOM);末行为'合计'。"""
    out = pathlib.Path(path)
    if out.suffix.lower() != ".csv":
        out = out.with_suffix(".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(SUMMARY_HEADER)
        for s in summary["stats"]:
            writer.writerow([
                s["session_date"], s["room_id"], s["start"], s["end"],
                s["total_minutes"], s["deal_minutes"], s["gmv_sum"],
            ])
        t = summary["totals"]
        writer.writerow([
            TOTAL_LABEL, "", "", "",
            t["total_minutes"], t["deal_minutes"], t["gmv_sum"],
        ])
    return out


def format_stats_line(s: Dict) -> str:
    return (f"{s['room_id']}  {s['start']}~{s['end']}  "
            f"分钟 {s['total_minutes']} | 成交分钟 {s['deal_minutes']} | "
            f"成交金额 {s['gmv_sum']:.2f} 元")


def compact_files(out_dir: pathlib.Path, date_str: str) -> List[pathlib.Path]:
    """返回某日的成交点精简表文件(live_YYYYMMDD_<room>_compact.csv)。"""
    ymd = date_str.replace("-", "")
    if not out_dir.is_dir():
        return []
    return sorted(out_dir.glob(f"live_{ymd}_*_compact.csv"))


def publish_day(out_dir: pathlib.Path, date_str: str,
                compact_files_: Sequence[pathlib.Path],
                canonical_files: Sequence[pathlib.Path],
                *,
                gpm_by_room: Dict[str, Dict[str, object]] | None = None) -> pathlib.Path:
    """把某日的导出结果整理成"日报文件夹"并返回其路径。

    规则(用户需求:一键运行后拿到的就是两列表):
      - 新建目录:日报_<YYYYMMDD>(位于 out_dir 下);
      - 每场从精简表(live_YYYYMMDD_<room>_compact.csv)复制/重写为
        `成交点_<room>.csv`(time=HH:MM / gmv,只保留 gmv>0 的行);
        若提供 gpm_by_room(room → {HH:MM: 小时GPM 或 None}),则成交点扩为三列
        time/gmv_min/gpm:gpm=该行分钟所属"小时"的小时 GPM(该小时各成交分钟行同值);
        无观看档的场次不在映射内 → gpm 列留空,并生成 成交点_gpm_说明.txt 说明;
      - 写入 `日报汇总_<YYYYMMDD>.csv`(场次级统计 + 合计行)。
    精简表缺失或表头不符的场次会被跳过并在汇总中说明;不做在线请求。
    """
    folder = out_dir / f"日报_{date_str.replace('-', '')}"
    folder.mkdir(parents=True, exist_ok=True)
    skipped = []
    no_watch_rooms = []
    for cf in compact_files_:
        # live_YYYYMMDD_<room_id>_compact → room_id(room_id 本身不含下划线)
        tokens = cf.stem.split("_")
        room = "_".join(tokens[2:-1]) if len(tokens) >= 4 else cf.stem
        dest = folder / f"成交点_{room}.csv"
        if gpm_by_room is not None and room not in gpm_by_room:
            no_watch_rooms.append(room)
        try:
            _write_deal_file(cf, dest, gpm_by_room=gpm_by_room, room=room)
        except ValueError as exc:
            skipped.append(f"{cf.name}: {exc}")
            continue
    if gpm_by_room is not None:
        _write_deal_note(folder, no_watch_rooms)
    summary: Dict = {"stats": [], "totals": {
        "sessions": 0, "total_minutes": 0, "deal_minutes": 0, "gmv_sum": 0.0,
    }}
    for cfile in canonical_files:
        try:
            s = stat_csv(cfile)
        except ValueError as exc:
            skipped.append(f"{cfile.name}: {exc}")
            continue
        summary["stats"].append(s)
        summary["totals"]["total_minutes"] += s["total_minutes"]
        summary["totals"]["deal_minutes"] += s["deal_minutes"]
        summary["totals"]["gmv_sum"] = round(
            summary["totals"]["gmv_sum"] + s["gmv_sum"], 2)
    summary["totals"]["sessions"] = len(summary["stats"])
    if skipped:
        print(f"[publish] 跳过 {len(skipped)} 个异常文件:")
        for item in skipped:
            print(f"  - {item}")
    write_summary_csv(folder / f"日报汇总_{date_str.replace('-', '')}.csv", summary)
    return folder


def _write_deal_file(src: pathlib.Path, dest: pathlib.Path, *,
                     gpm_by_room: Dict[str, Dict[str, object]] | None,
                     room: str) -> None:
    """读精简表(time,gmv_min)写成交点文件;可选追加 gpm 列(所属小时 GPM)。

    表头不符抛 ValueError;gpm 缺失/None → 留空(不伪造)。
    """
    with src.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = list(reader.fieldnames or [])
        if cols != ["time", "gmv_min"]:
            raise ValueError(f"精简表表头应为 ['time','gmv_min'],实际 {cols}")
        rows = [dict(r) for r in reader]
    gpm_map = (gpm_by_room or {}).get(room) or {}
    out_cols = ["time", "gmv_min", "gpm"] if gpm_by_room is not None else ["time", "gmv_min"]
    table = []
    for r in rows:
        if gpm_by_room is None:
            table.append([r.get("time", ""), r.get("gmv_min", "")])
            continue
        val = gpm_map.get(r.get("time", ""), None)
        cell = "" if val is None else (f"{val:.2f}" if isinstance(val, (int, float)) else str(val))
        table.append([r.get("time", ""), r.get("gmv_min", ""), cell])
    with dest.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(out_cols)
        writer.writerows(table)


def _write_deal_note(folder: pathlib.Path, no_watch_rooms: Sequence[str]) -> None:
    """成交点 gpm 列说明(有观看数据才生成 gpm 列时)。"""
    lines = [
        "成交点表的 gpm 列说明",
        "- gpm = 该行成交分钟所属『小时』的小时级千次观看成交金额(GPM=该小时成交金额÷该小时",
        "  直播间观看量×1000);同一小时内各成交分钟行显示同一小时 GPM(小时口径稳定,非分钟瞬时值)。",
        "- gmv>0 的行保留(成交点);gpm 留空 = 该行小时 views=0 或暂无观看数据(不伪造)。",
        "- 对应的小时明细见同目录 小时GPM_*.csv(如已生成)。",
    ]
    if no_watch_rooms:
        lines.append("- 无观看档场次(本表 gpm 列留空): " + ", ".join(no_watch_rooms))
        lines.append("  如需 gpm 列,请对相应场次执行观看捕获(自动优先,见 README 小时级 GPM 章节)。")
    (folder / "成交点_gpm_说明.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
