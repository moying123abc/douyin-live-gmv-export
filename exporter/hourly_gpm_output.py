# -*- coding: utf-8 -*-
"""exporter/hourly_gpm_output.py — 小时级 GPM 的导出联动层(T3,纯离线/标准库)。

职责(只做“输出编排”,口径裁决仍归调用方):
- 把 extractor/hourly_gpm.hourly_gpm() 的报告转成带“口径说明列”的 CSV 行;
- export --fixture:夹具顶层带 "gpm_watch" 段(观看序列 + 口径声明)时,
  生成 <主文件>_hourly.csv 及说明;不带/无效时返回 None(由调用方打印跳过提示,
  绝不阻断既有成交金额导出);
- daily:在日报_<日期> 文件夹生成:
  * 小时GPM_<YYYYMMDD>.csv(跨同日多场、按小时叠加 views/gmv 再算 GPM);
  * 小时GPM_<YYYYMMDD>_说明.txt(含口径与跳过场次说明;观看不可得时降级说明写于此)。
- 列:[hour, views, gmv, gpm, note, watch_semantics];views=0 桶 gpm 留空 + note 标注;
  只读本地文件,不做任何在线请求。

口径纪律(沿 T1 结论):观看序列的“口径声明”来自数据携带方(夹具段/每场 _watch_min.csv),
本模块不硬编码任何未获真机证据的页面字段;在线默认“观看序列不可得(口径待对拍)”,
由调用方明确跳过并提示。
"""
from __future__ import annotations

import csv
import json
import pathlib
import re
from typing import Any, Dict, List, Optional

from extractor import daily_stats  # 复用“按天找分钟量表”的只读约定
from extractor import hourly_gpm as _hourly  # t2 纯聚合模块

# 输出表列(稳定命名,供 README/验收引用)
HOURLY_GPM_HEADERS: List[str] = ["hour", "views", "gmv", "gpm", "note", "watch_semantics"]

# 每场观看分钟档(供 daily 跨场叠加):live_YYYYMMDD_<room>_watch_min.csv,表头 time,views,semantics
WATCH_MIN_SUFFIX = "_watch_min.csv"

# 日报文件夹内稳定命名
DAY_HOURLY_FILE = "小时GPM_{ymd}.csv"
DAY_NOTE_FILE = "小时GPM_{ymd}_说明.txt"

# 在线观看档缺失时的降级口径说明(沿 T1→T12/T13 结论更新:分母可由捕获获得)
DEFAULT_ONLINE_UNAVAILABLE_REASON = (
    "本场无观看档(live_<ymd>_<room>_watch_min.csv):未捕获到直播间观看量分钟序列"
    "(WatchCntTrend),无法计算真实分母 → 该场不并入小时 GPM。"
    "补齐方式:python main.py daily 默认自动补捕获(自动优先,失败见提示);"
    "或 python -m exporter.watch_capture --room-id <id> --date <ymd>(--assist)。"
    "口径说明见 docs/GPM-观看次数口径结论.md §4d。"
)


# ---------------------------------------------------------------------------
# 报告 → CSV 行
# ---------------------------------------------------------------------------
def report_rows(report: Dict[str, Any], watch_semantics: str) -> List[Dict[str, Any]]:
    """把 hourly_gpm 报告转成可写 CSV 的行(数值原样,gpm=None 写空串,不伪造)。"""
    out: List[Dict[str, Any]] = []
    for r in report.get("rows", []):
        gpm_text = r["gpm"] if r.get("gpm") is not None else ""
        out.append({
            "hour": r["hour"],
            "views": r["views"],
            "gmv": r["gmv"],
            "gpm": gpm_text,
            "note": r.get("note", ""),
            "watch_semantics": watch_semantics,
        })
    return out


def write_hourly_csv(path_like, rows: List[Dict[str, Any]]) -> pathlib.Path:
    """写小时 GPM CSV(UTF-8 BOM,表头 hour,views,gmv,gpm,note,watch_semantics)。"""
    out = pathlib.Path(path_like)
    if out.suffix.lower() != ".csv":
        out = out.with_suffix(".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HOURLY_GPM_HEADERS)
        writer.writeheader()
        for row in rows:
            writer.writerow({h: row.get(h, "") for h in HOURLY_GPM_HEADERS})
    return out


def hourly_sibling_path(prefix_path) -> pathlib.Path:
    """<prefix>[.csv] → <prefix>_hourly.csv(稳定命名)。"""
    text = str(prefix_path)
    if text.lower().endswith(".csv"):
        text = text[: -len(".csv")]
    return pathlib.Path(text + "_hourly.csv")


# ---------------------------------------------------------------------------
# 夹具观看段(export --fixture 联动;纯读取夹具自身字段,不硬编码页面字段)
# ---------------------------------------------------------------------------
def read_fixture_watch(fixture_path) -> Optional[Dict[str, Any]]:
    """读夹具顶层 "gpm_watch" 段,结构非法返回 None(调用方打印提示后跳过)。

    段结构:
      "gpm_watch": {
          "level": "minute" | "hour",
          "rows": [{"time": "07-01 19:01", "views": 3} | {"hour": "2025-07-01 19:00", "views": 180}, ...],
          "series_key": "<序列 key,可选>",
          "semantics": "<口径声明文本,可选>"
      }
    """
    try:
        payload = json.loads(pathlib.Path(fixture_path).read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    section = payload.get("gpm_watch")
    if not isinstance(section, dict):
        return None
    level = section.get("level")
    rows = section.get("rows")
    if level not in ("minute", "hour") or not isinstance(rows, list) or not rows:
        return None
    clean = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        key = "hour" if level == "hour" else "time"
        if row.get(key) is None:
            return None
        value = next((row[k] for k in ("views", "view_min", "y", "value", "watch")
                      if row.get(k) is not None), None)
        if value is None:
            return None
        clean.append({"time" if level == "minute" else "hour": row[key],
                      "views": value})
    series_key = str(section.get("series_key") or "").strip()
    semantics = str(section.get("semantics") or "").strip()
    if not semantics:
        semantics = (f"夹具声明(series_key={series_key})" if series_key
                     else "夹具声明(口径未额外说明)")
    return {"level": level, "rows": clean, "semantics": semantics}


def fixture_hourly_report(fixture_path, gmv_rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """夹具驱动的小时 GPM 报告;观看段缺失/无效返回 None(不阻断主导出)。

    参考年:取 gmv 行首行 session_date(YYYY-MM-DD),供夹具中 MM-DD 时间的观看行
    与成交行归到同一年份/同一小时桶(夹具 gpm_watch 行通常不含 session_date)。
    """
    watch = read_fixture_watch(fixture_path)
    if watch is None:
        return None
    reference_date = None
    for row in gmv_rows:
        raw = str((row or {}).get("session_date") or "").strip()
        if len(raw) >= 10:
            try:
                reference_date = _date_of(raw)
            except ValueError:
                pass
        if reference_date is not None:
            break
    try:
        report = _hourly.hourly_gpm(
            gmv_rows, watch["rows"], watch_level=watch["level"],
            reference_date=reference_date,
        )
    except _hourly.HourlyGpmError as exc:
        raise _hourly.HourlyGpmError(f"夹具 gpm_watch 与成交行聚合失败: {exc}") from exc
    report["_watch_semantics"] = watch["semantics"]
    return report


def _date_of(text: str):
    import datetime as _dt
    return _dt.date.fromisoformat(text[:10])


# ---------------------------------------------------------------------------
# daily:跨同日多场按小时叠加 → 小时GPM_<date>.csv(同名 _说明.txt 同步)
# ---------------------------------------------------------------------------
def _read_canonical(out_dir: pathlib.Path, canonical: pathlib.Path) -> List[Dict[str, Any]]:
    with canonical.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        need = {"room_id", "session_date", "time", "gmv_min"}
        if reader.fieldnames is None or not need.issubset(set(reader.fieldnames)):
            raise ValueError(f"{canonical.name} 缺少必需列(需要 {sorted(need)})")
        return [dict(r) for r in reader]


def _read_watch_min(watch_file: pathlib.Path) -> Dict[str, Any]:
    """读每场观看分钟档(live_..._watch_min.csv):表头 time,views[,semantics]。

    语义列缺失时 semantics 为空(day 聚合会据此拒绝“无声明混叠”)。
    """
    rows: List[Dict[str, Any]] = []
    semantics = ""
    with watch_file.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or not {"time", "views"}.issubset(set(reader.fieldnames)):
            raise ValueError(f"{watch_file.name} 表头应为 time,views[,semantics]")
        for row in reader:
            text = str(row.get("time") or "").strip()
            if not text:
                continue
            rows.append({"time": text, "views": row.get("views")})
            if not semantics and row.get("semantics"):
                semantics = str(row["semantics"]).strip()
    return {"rows": rows, "semantics": semantics}


def _semantics_identity(text: str) -> str:
    """口径身份(归一化):剔除每场特有的复核差数值段后用于跨场一致性比较。

    观看档里的 semantics 由 watch_capture.SEMANTICS_TEMPLATE 生成,内含该场自身的
    复核差数值(整场 Σ=... vs ServerWatchCntTd=... diff=...)。复核差是每场证据,
    不属于口径身份;真实同日多场捕获时该数字天然不同,不应据此拒绝叠加。
    归一化仅把这三处数字替换为占位符,保留其余口径声明文本原样:
      - 直播间观看量(直播间看播量)分钟序列;dataKey=WatchCntTrend,来自
        room_minute_indicator;整场 Σ=<n> vs 同刻 key_index.ServerWatchCntTd=<n>
        (diff=<n>);复核差如实标注,未冒充精确闭合;小时 GPM 分母采用本序列...
      不同来源/不同口径(如夹具声明、其它 dataKey)的文本归一化后仍不相同,
      叠加前的一致性契约(口径身份一致才可跨场叠加)不变。
    """
    if not text:
        return ""
    # 只把三处"复核差数值(或 None)"替换为占位符;保留其余口径声明文本原样,
    # 避免 \S+ 误吞后续中文字符。
    t = re.sub(r"(Σ=|ServerWatchCntTd=|diff=)(None|[-+0-9.eE]+)", r"\1#", text)
    return t.strip()


def publish_day_hourly(out_dir: pathlib.Path, date_str: str,
                       folder: pathlib.Path) -> Dict[str, Any]:
    """在日报文件夹生成小时级 GPM 表(跨同日多场叠加)或降级说明。

    规则:
    - 只读本地;分钟量表 live_YYYYMMDD_*.csv(排除 _compact);
    - 每场观看档 live_YYYYMMDD_<room>_watch_min.csv(表头 time,views,semantics);
      无观看档的场次被跳过并在说明中列出,其 GMV 不混入 GPM 表(避免口径污染);
    - 有观看档的场次:gmv 分钟与观看分钟全部并入同一次 hourly_gpm 聚合,
      同小时桶 views/gmv 先叠加再算 GPM;要求各场"口径身份"一致(归一化 semantics:
      忽略每场复核差数值 Σ/ServerWatchCntTd/diff,该数值是单场证据而非口径差异),
      口径身份不同才拒绝叠加(降级说明);复核差数值仅不同时照常叠加并在说明中列全;
    - 产物:小时GPM_<YYYYMMDD>.csv + 小时GPM_<YYYYMMDD>_说明.txt;无任何观看档时
      只写说明.txt(降级说明),exit 语义不变。
    返回 {ok, csv?, note, sessions_with_watch, sessions_skipped, reasons}。
    """
    ymd = date_str.replace("-", "")
    canonical = daily_stats.day_files(out_dir, date_str)
    folder.mkdir(parents=True, exist_ok=True)
    note_path = folder / DAY_NOTE_FILE.format(ymd=ymd)
    reasons: List[str] = []
    used_sessions: List[str] = []
    skipped_sessions: List[str] = []
    semantics_set: List[str] = []
    gmv_minutes: List[Dict[str, Any]] = []
    watch_minutes: List[Dict[str, Any]] = []

    for cfile in canonical:
        stem = cfile.stem  # live_YYYYMMDD_<room>
        room = stem.split("_", 2)[-1] if stem.count("_") >= 2 else cfile.name
        watch_file = cfile.with_name(stem + WATCH_MIN_SUFFIX)
        try:
            rows = _read_canonical(out_dir, cfile)
        except ValueError as exc:
            reasons.append(f"{cfile.name}: {exc}")
            skipped_sessions.append(room)
            continue
        if not rows:
            reasons.append(f"{cfile.name}: 空分钟量表,跳过")
            skipped_sessions.append(room)
            continue
        if not watch_file.is_file():
            reasons.append(f"{room}: 无观看档 {watch_file.name}(观看序列不可得/口径待对拍),"
                           f"该场 GMV 不并入小时 GPM 表")
            skipped_sessions.append(room)
            continue
        try:
            watch = _read_watch_min(watch_file)
        except ValueError as exc:
            reasons.append(f"{room}: {exc};跳过该场")
            skipped_sessions.append(room)
            continue
        if not watch["rows"]:
            reasons.append(f"{room}: 观看档为空,跳过")
            skipped_sessions.append(room)
            continue
        gmv_minutes.extend(rows)
        watch_minutes.extend(watch["rows"])
        used_sessions.append(room)
        if watch["semantics"]:
            semantics_set.append(watch["semantics"])

    note_lines = [
        f"小时 GPM 说明(日期 {date_str})",
        "GPM=gmv/views*1000(元/千次观看);views=0 桶 gpm 留空不伪造;"
        "小时归属=数据自带时间的整点小时,跨午夜按行自身日历日。",
    ]
    if not used_sessions:
        note_lines.append(f"口径来源/约束: {DEFAULT_ONLINE_UNAVAILABLE_REASON}")
        note_lines.append("无任何含观看档的场次 → 不生成小时 GPM 表(降级,不影响既有成交金额日报)。")
        note_lines.append("已跳过场次明细:")
        note_lines.extend(f"- {r}" for r in reasons)
        note_path.write_text("\n".join(note_lines) + "\n", encoding="utf-8")
        return {"ok": False, "note": str(note_path), "sessions_with_watch": [],
                "sessions_skipped": skipped_sessions, "reasons": reasons}

    semantics_used = semantics_set[0] if semantics_set else "各场观看档未声明(语义未知,谨慎使用)"
    # 口径一致性以"归一化身份"判定:复核差数值(整场 Σ/ServerWatchCntTd/diff)是
    # 每场证据注记,不是口径差异 → 忽略后比较;真正不同的口径文本仍判不一致。
    identity_set = {_semantics_identity(s) for s in semantics_set}
    if len(identity_set) > 1:
        reasons.append(f"各场观看口径声明不一致: {sorted(set(semantics_set))};"
                       "不强行叠加(避免口径污染),降级为仅说明。")
        note_lines.append(reasons[-1])
        note_lines.append(f"含观看档场次: {', '.join(used_sessions)}")
        note_path.write_text("\n".join(note_lines) + "\n", encoding="utf-8")
        return {"ok": False, "note": str(note_path), "sessions_with_watch": used_sessions,
                "sessions_skipped": skipped_sessions, "reasons": reasons}
    if len(set(semantics_set)) > 1:
        # 口径身份一致、仅复核差数值不同 → 允许跨场叠加;把各场复核差写进说明,
        # watch_semantics 列改为"跨场合并口径文本"(不带单一闭合数值,不冒充单场闭合)。
        reasons.append("各场复核差数值不同但口径身份一致(仅闭合数字差异),已按口径身份叠加;"
                       "各场原始复核差声明见下方明细。")
        note_lines.append(reasons[-1])
        note_lines.append("各场观看口径原始声明(含各自复核差):")
        for s in sorted(set(semantics_set)):
            note_lines.append(f"- {s}")
        fixed = semantics_set[0].split("整场 Σ=", 1)[0].rstrip("; ").strip()
        semantics_used = (fixed + ";跨场按小时叠加 views/gmv 后算 GPM;"
                          "各场复核差(Σ vs ServerWatchCntTd)不同,见本说明文件明细,未冒充单一闭合。")

    try:
        reference_date = None
        try:
            import datetime as _dt
            reference_date = _dt.date.fromisoformat(date_str)
        except ValueError:
            reference_date = None
        report = _hourly.hourly_gpm(gmv_minutes, watch_minutes, watch_level="minute",
                                    reference_date=reference_date)
    except _hourly.HourlyGpmError as exc:
        reasons.append(f"聚合失败: {exc};降级为仅说明。")
        note_lines.append(reasons[-1])
        note_path.write_text("\n".join(note_lines) + "\n", encoding="utf-8")
        return {"ok": False, "note": str(note_path), "sessions_with_watch": used_sessions,
                "sessions_skipped": skipped_sessions, "reasons": reasons}

    rows_out = report_rows(report, semantics_used)
    csv_path = write_hourly_csv(folder / DAY_HOURLY_FILE.format(ymd=ymd), rows_out)
    t = report["totals"]
    note_lines.append(f"生成 {csv_path.name}:小时桶 {len(rows_out)} 个,"
                      f"全场 GMV 合计 {t['gmv']:.2f} 元,views 合计 {t['views']},"
                      f"整场 GPM 锚点={t['gpm'] if t['gpm'] is not None else 'N/A(views=0)'}")
    note_lines.append(f"含观看档场次(计入小时 GPM 表): {', '.join(used_sessions)}")
    if skipped_sessions:
        note_lines.append("跳过(不入表):")
        note_lines.extend(f"- {s}" for s in skipped_sessions)
    note_lines.append(f"watch_semantics 列: {semantics_used}")
    note_lines.append("口径来源: 分母=直播间观看量(直播间看播量,dataKey=WatchCntTrend)分钟序列,"
                      "整场 Σ/页面累计(ServerWatchCntTd)/复核差见 watch_semantics 列;"
                      "详见 docs/GPM-观看次数口径结论.md §4d(回放页免人工捕获,含人工一次兜底)。")
    note_path.write_text("\n".join(note_lines) + "\n", encoding="utf-8")
    return {"ok": True, "csv": str(csv_path), "note": str(note_path),
            "sessions_with_watch": used_sessions,
            "sessions_skipped": skipped_sessions, "reasons": reasons}


def hour_gpm_by_minute(canonical_rows, watch_rows, reference_date):
    """把每"分钟成交行"映射到其所属小时的"小时 GPM"。

    返回 {HH:MM: float|None}:HH:MM 为分钟行的页面时间外观的时间部分(如 '09:23');
    值为该分钟所属小时桶的 GPM(分子=该小时成交金额,分母=该小时直播间观看量,
    gpm=gmv/views*1000);小时 views=0/无观看档 → None(留空,不伪造)。
    供"成交点表追加 gpm 列(小时口径复制到成交分钟行)"使用。
    """
    from extractor import hourly_gpm as _hg

    report = _hg.hourly_gpm(
        list(canonical_rows), list(watch_rows) if watch_rows else None,
        watch_level="minute", reference_date=reference_date,
    )
    hour_gpm = {r["hour"]: r["gpm"] for r in report["rows"]}
    out = {}
    for row in canonical_rows:
        raw = str(row.get("time") or "").strip()
        hhmm = raw.split()[-1] if " " in raw else raw
        try:
            hk = _hg.bucket_hour(raw, reference_date)
        except Exception:  # noqa: BLE001 — 单行解析失败不阻断其余行
            continue
        out[hhmm] = hour_gpm.get(hk)
    return out
