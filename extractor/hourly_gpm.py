# -*- coding: utf-8 -*-
"""extractor/hourly_gpm.py — 按小时桶聚合与 GPM(千次观看成交金额)计算(纯函数,离线)。

目标(与 T1 探测结论 docs/GPM-观看次数口径结论.md 对齐):
- 输入:
  * 成交金额分钟行(标准量表 / {time, gmv_min},time 为页面自带时间外观:
    "MM-DD HH:MM"、"YYYY-MM-DD HH:MM[:SS]" 或 epoch 秒);
  * 观看序列(T1 口径候选:分钟级观看序列 {time, views} 或 小时级观看序列
    {hour, views});观看次数口径由调用方按 T1 结论提供,本模块不做口径裁决;
  * 可选场次元信息(reference_date/session_date,为 MM-DD 时间补年份)。
- 输出:按小时桶 [hour, views, gmv, gpm, note] 的报告 + 校验(桶合计与输入合计一致)。

桶归属规则(写死并文档化,见 docs/GPM-小时聚合设计说明.md):
- 分钟/点行按“数据自带时间”所在整点小时归桶:HH:MM → 桶 [HH:00, HH:59:59],
  桶标签统一为 "YYYY-MM-DD HH:00";
- 跨午夜场次:每行按自身日历日归属(MM-DD 已随页面翻日;epoch 秒按东八区转换),
  不是一律按开播日归属 —— 与现有“时间用页面自带时间、跨午夜以行时间戳为准”的口径一致;
- 跨场同小时叠加:先合并再计算 GPM(分子分母各自加总),**不做 GPM 均值**。

不伪造规则:
- views=0(或缺失)的桶:GPM 输出留空(None/""),note 标注 "views=0",绝不产出 NaN/Inf;
- 桶的 GMV 与全输入分钟合计必须守恒(校验,超容差抛 HourlyGpmError);
- 纯标准库,无网络/第三方依赖;不改变既有 M2 成交序列语义。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

# 输出表列(与导出层联动时可直接引用)
HEADERS: List[str] = ["hour", "views", "gmv", "gpm", "note"]

# epoch 秒转“东八区墙面时间”的偏移(页面时间 = UTC+8;见 T1 证据)
DEFAULT_EPOCH_TZ_HOURS = 8.0

# GMV 守恒校验绝对容差(元):浮点加法顺序差异极小,容差按“分”级别足够
_GMV_TOLERANCE = 1e-6
# 观看数守恒校验绝对容差
_VIEWS_TOLERANCE = 1e-6

_WATCH_VALUE_KEYS = ("views", "view_min", "watch", "watch_min", "y", "value")
_WATCH_VALUE_ALIAS_TEXT = "views/view_min/watch/watch_min/y/value"


class HourlyGpmError(ValueError):
    """输入不合法 / 桶校验失败时抛出(结构化拒绝,绝不静默)。"""


# ---------------------------------------------------------------------------
# 时间外观小工具
# ---------------------------------------------------------------------------
def _is_epoch_text(text: str) -> bool:
    t = text.strip()
    if t.replace(".", "", 1).isdigit():
        return len(t.split(".")[0]) >= 9
    return False


def _parse_to_datetime(value: Any, reference_date: Optional[_dt.date]) -> _dt.datetime:
    """把页面时间外观解析为 datetime(naive=本地墙面;aware(epoch)=UTC)。"""
    from extractor import validation as _validation  # 复用项目时间口径解析
    return _validation.parse_time(value, reference_date)


def _wall_hour(dt_value: _dt.datetime, epoch_tz_hours: float) -> _dt.datetime:
    """统一到“墙面小时”:epoch 秒(aware UTC)转东八区墙面;naive 原样。"""
    if dt_value.tzinfo is not None:
        return (dt_value.astimezone(_dt.timezone.utc)
                + _dt.timedelta(hours=epoch_tz_hours)).replace(tzinfo=None)
    return dt_value


def _session_date_from_row(row: Dict[str, Any]) -> Optional[_dt.date]:
    raw = row.get("session_date")
    if raw is None:
        return None
    text = str(raw).strip()
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def bucket_hour(time_value: Any, reference_date: Optional[_dt.date] = None,
                epoch_tz_hours: float = DEFAULT_EPOCH_TZ_HOURS,
                missing_year: bool = True) -> str:
    """分钟/点时间 → 小时桶标签 "YYYY-MM-DD HH:00"。

    - 完整日期 / MM-DD(+reference_date 补年份)/ epoch 秒 均可;
    - MM-DD 无参考年时落到 2000 年并可由调用方收集 warning(见 missing_year);
    - 桶取整点小时:HH:MM → HH:00。
    """
    if time_value is None:
        raise HourlyGpmError("time/hour 为空,无法归桶")
    text = str(time_value).strip()
    try:
        if _is_epoch_text(text):
            parsed = _parse_to_datetime(float(text), None)  # aware UTC
            parsed = _wall_hour(parsed, epoch_tz_hours)
        else:
            parsed = _parse_to_datetime(text, reference_date)
            if parsed.tzinfo is not None:  # 防御:直接给 UTC aware datetime 对象
                parsed = _wall_hour(parsed, epoch_tz_hours)
    except HourlyGpmError:
        raise
    except (ValueError, TypeError) as exc:
        raise HourlyGpmError(f"无法解析的时间外观 {text!r}: {exc}") from exc
    return parsed.strftime("%Y-%m-%d %H:00")


def _to_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise HourlyGpmError(f"{field} 不是数值: {value!r}") from exc
    else:
        number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise HourlyGpmError(f"{field} 为 NaN/Inf,拒绝: {value!r}")
    return number


# ---------------------------------------------------------------------------
# 主聚合
# ---------------------------------------------------------------------------
def hourly_gpm(
    gmv_rows: List[Dict[str, Any]],
    watch_rows: Optional[List[Dict[str, Any]]] = None,
    *,
    watch_level: str = "auto",
    reference_date: Optional[_dt.date] = None,
    epoch_tz_hours: float = DEFAULT_EPOCH_TZ_HOURS,
) -> Dict[str, Any]:
    """按小时桶聚合成交金额与观看次数,并计算小时 GPM。

    参数:
      gmv_rows    : 分钟行,每行含 time(字符串/数字)与数值 gmv_min;
      watch_rows  : 观看序列,None=不提供(全部桶 views=0,GPM 留空并标注);
                    分钟级行:{time, views};小时级行:{hour, views}
                    (views 别名: views/view_min/watch/watch_min/y/value);
      watch_level : "auto"(按行键自动判) | "minute" | "hour";
      reference_date: MM-DD 时间补年份(行内 session_date 优先,其次此参数);
      epoch_tz_hours: epoch 秒转墙面的东八区偏移。

    返回 report:
      {"rows": [{hour, views, gmv, gpm, note}](按 hour 升序),
       "totals": {"gmv":..., "views":..., "gpm":...(整场合计或 None)},
       "checks": {"gmv_conserved": bool, "watch_conserved": bool,
                  "gmv_input":..., "gmv_bucket_total":...,
                  "views_input":..., "views_bucket_total":...},
       "warnings": [...],
       "hourly_gpm_note": "gpm=gmv/views*1000(元/千次观看);views=0 桶 gpm 留空不伪造"}
    """
    if not gmv_rows:
        raise HourlyGpmError("gmv_rows 为空:至少需要一行成交金额分钟数据")

    # ---- 1) 观看序列规格化 -> {hour: views(float)} ----
    watch_by_hour: Dict[str, float] = {}
    watch_minute_count = 0
    if watch_rows:
        first = watch_rows[0]
        if watch_level == "auto":
            if isinstance(first, dict) and "hour" in first:
                watch_level = "hour"
            else:
                watch_level = "minute"
        for row in watch_rows:
            if not isinstance(row, dict):
                raise HourlyGpmError(f"观看序列行必须是 dict,得到 {type(row).__name__}")
            value = next((row[k] for k in _WATCH_VALUE_KEYS if k in row and row[k] is not None), None)
            if value is None:
                raise HourlyGpmError(
                    f"观看序列行缺少数值键(可选 {_WATCH_VALUE_ALIAS_TEXT}): {row!r}"
                )
            views = _to_number(value, "views")
            ref = _session_date_from_row(row) or reference_date
            if watch_level == "hour":
                if "hour" not in row or row["hour"] is None:
                    raise HourlyGpmError(
                        f"hour 级观看行必须含 hour 键(可选别名 {_WATCH_VALUE_ALIAS_TEXT}): {row!r}"
                    )
                hk = bucket_hour(row["hour"], reference_date=ref,
                                 epoch_tz_hours=epoch_tz_hours)
            else:  # minute
                if "time" not in row or row["time"] is None:
                    raise HourlyGpmError(
                        f"minute 级观看行必须含 time 键: {row!r}"
                    )
                watch_minute_count += 1
                hk = bucket_hour(row["time"], reference_date=ref,
                                 epoch_tz_hours=epoch_tz_hours)
            watch_by_hour[hk] = watch_by_hour.get(hk, 0.0) + views

    # ---- 2) 成交金额按小时桶求和 ----
    gmv_by_hour: Dict[str, float] = {}
    gmv_total_input = 0.0
    missing_year_warnings = 0
    for row in gmv_rows:
        if not isinstance(row, dict):
            raise HourlyGpmError(f"成交金额行必须是 dict,得到 {type(row).__name__}")
        if row.get("time") is None:
            raise HourlyGpmError(f"成交金额行缺少 time: {row!r}")
        if row.get("gmv_min") is None:
            raise HourlyGpmError(f"成交金额行缺少 gmv_min: {row!r}")
        gmv = _to_number(row["gmv_min"], "gmv_min")
        ref = _session_date_from_row(row) or reference_date
        try:
            hk = bucket_hour(row["time"], reference_date=ref,
                             epoch_tz_hours=epoch_tz_hours)
        except HourlyGpmError:
            raise
        if hk.startswith("2000-") and ref is None:
            missing_year_warnings += 1
        gmv_by_hour[hk] = gmv_by_hour.get(hk, 0.0) + gmv
        gmv_total_input += gmv

    # ---- 3) 合并桶(并集;成交金额缺失的小时 gmv=0 属于正常,view-only 桶如实输出)----
    hours = sorted(set(gmv_by_hour) | set(watch_by_hour))
    rows_out: List[Dict[str, Any]] = []
    gmv_bucket_total = 0.0
    views_bucket_total = 0.0
    for hk in hours:
        gmv = gmv_by_hour.get(hk, 0.0)
        views = watch_by_hour.get(hk, 0.0)
        gmv_bucket_total += gmv
        views_bucket_total += views
        note = ""
        gpm: Optional[float]
        if views > 0:
            gpm = round(gmv / views * 1000.0, 2)
        else:
            gpm = None
            note = "views=0: GPM 无定义,留空(不伪造)"
        if hk not in gmv_by_hour:
            note = (note + "; " if note else "") + "该小时无成交金额分钟行(gmv=0)"
        rows_out.append({
            "hour": hk,
            "views": int(views) if float(views).is_integer() else views,
            "gmv": gmv,
            "gpm": gpm,
            "note": note,
        })

    # ---- 4) 守恒校验 ----
    gmv_diff = abs(gmv_bucket_total - gmv_total_input)
    checks = {
        "gmv_conserved": gmv_diff <= _GMV_TOLERANCE,
        "watch_conserved": True,
        "gmv_input": gmv_total_input,
        "gmv_bucket_total": gmv_bucket_total,
        "gmv_diff": gmv_diff,
        "views_input": None,
        "views_bucket_total": views_bucket_total,
        "views_diff": 0.0,
    }
    views_input = 0.0
    if watch_rows:
        views_input = sum(watch_by_hour.values())
        checks["views_input"] = views_input
        checks["views_diff"] = abs(views_bucket_total - views_input)
        checks["watch_conserved"] = checks["views_diff"] <= _VIEWS_TOLERANCE
    if not checks["gmv_conserved"]:
        raise HourlyGpmError(
            f"GMV 桶合计 {gmv_bucket_total:.6f} 与分钟输入合计 {gmv_total_input:.6f}"
            f" 不一致(差 {gmv_diff:.2e}),拒绝输出(不伪造)"
        )
    if watch_rows and not checks["watch_conserved"]:
        raise HourlyGpmError(
            f"观看桶合计 {views_bucket_total:.6f} 与观看输入合计 {views_input:.6f}"
            f" 不一致(差 {checks['views_diff']:.2e}),拒绝输出"
        )

    # ---- 5) 整场合计(供与页面 GPM 卡对拍的锚点)----
    totals_gpm: Optional[float] = None
    if views_bucket_total > 0:
        totals_gpm = round(gmv_bucket_total / views_bucket_total * 1000.0, 2)

    warnings: List[str] = []
    if watch_rows is None:
        warnings.append("未提供观看序列:全部桶 views=0,GPM 留空标注(供先出 gmv 桶轮廓)")
    if missing_year_warnings:
        warnings.append(
            f"{missing_year_warnings} 行 MM-DD 时间缺少年份来源(session_date/reference_date),"
            "桶年份按 2000 兜底,请调用方补 reference_date 后复核(不影响桶内守恒)"
        )

    return {
        "rows": rows_out,
        "totals": {"gmv": gmv_bucket_total, "views": views_bucket_total, "gpm": totals_gpm},
        "checks": checks,
        "warnings": warnings,
        "watch_level_used": ("minute" if (watch_rows and watch_minute_count) else
                             ("hour" if watch_rows else None)),
        "hourly_gpm_note": "gpm=gmv/views*1000(元/千次观看);views=0 桶 gpm 留空不伪造",
    }


# ---------------------------------------------------------------------------
# 便捷:标准量表行(与 exporter/schema 相同结构)直接喂给 hourly_gpm
# ---------------------------------------------------------------------------
def from_standard_rows(
    rows: List[Dict[str, Any]],
    *,
    reference_date: Optional[_dt.date] = None,
    epoch_tz_hours: float = DEFAULT_EPOCH_TZ_HOURS,
) -> Dict[str, Any]:
    """把 exporter/schema 标准量表行(room_id/session_date/time/gmv_min)聚合为小时 GMV。

    仅成交金额(无观看)时仍返回 hourly_gpm(...) 的报告;观看并入请走 hourly_gpm。
    (session_date 列自动作为 MM-DD 补年来源。)
    """
    gmv_rows = [
        {"time": r.get("time"), "gmv_min": r.get("gmv_min"),
         "session_date": r.get("session_date")}
        for r in rows
    ]
    return hourly_gpm(gmv_rows, None, reference_date=reference_date,
                      epoch_tz_hours=epoch_tz_hours)


if __name__ == "__main__":  # pragma: no cover - 演示入口(离线,无第三方依赖)
    import pathlib as _pathlib
    import sys as _sys
    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))
    demo_gmv = [
        {"time": "2026-09-04 09:04", "gmv_min": 160.0},
        {"time": "2026-09-04 09:23", "gmv_min": 168.0},
        {"time": "2026-09-04 10:00", "gmv_min": 0.0},
        {"time": "2026-09-04 10:45", "gmv_min": 40.0},
    ]
    demo_watch = [
        {"time": "2026-09-04 09:04", "views": 55},
        {"time": "2026-09-04 09:23", "views": 77},
        {"time": "2026-09-04 10:00", "views": 30},
        {"time": "2026-09-04 10:45", "views": 40},
    ]
    rep = hourly_gpm(demo_gmv, demo_watch)
    for r in rep["rows"]:
        print(r)
    print("totals:", rep["totals"])
    print("checks:", {k: rep["checks"][k] for k in ("gmv_conserved", "watch_conserved")})
