# -*- coding: utf-8 -*-
"""extractor/validation.py — 内置校验(M2,三项核心校验)。

对"整场分钟级时间序列表"做校验,任一项失败都必须报错并给出明细,
绝不允许静默产出结果文件。

三项校验(口径与团队既定方案一致):
1. 时间轴严格递增且按分钟连续 —— 相邻两行 time 差必须恰好 1 分钟
   (严格递增是其必然结果;时间戳重复/跳变/乱序都会在这里被拦下);
2. 行数 ≈ 直播时长(分钟)+ 1 —— duration_minutes 未给出时按首尾时间跨度推算
   (跨度分钟 + 1);两者相差超过 row_tolerance_minutes 即失败;
3. 非零分钟之和 ≈ 页面累计成交金额 —— 由于金额为 0 的分钟贡献为 0,
   全量求和即等价于"非零分钟之和";与累计值比较使用可配置容差
   (默认:相对 0.1% 或绝对 1 元,满足其一即通过)。

行为约定:
- validate() 返回 ValidationReport(供测试/导出做结构化判断);
- assert_valid() 在失败时抛出 ValidationError(内含逐项明细文本);
- cumulative_total 未提供时,求和校验记作 skipped 并在报告/控制台明示
  "待在线复核",不静默假装通过。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional

_MINUTE = _dt.timedelta(minutes=1)

# 校验失败时的默认容差(可在 config.validation.* 覆盖)
DEFAULT_SUM_ABS_TOLERANCE = 1.0   # 元
DEFAULT_SUM_REL_TOLERANCE = 0.001  # 相对 0.1%
DEFAULT_ROW_TOLERANCE = 0         # 行数允差(行),默认严格相等


class ValidationError(ValueError):
    """校验失败异常:message 为可读明细,details 为结构化明细。"""

    def __init__(self, message: str, details: Optional[List[str]] = None):
        super().__init__(message)
        self.details = details or [message]


# ---------------------------------------------------------------------------
# 时间解析(只在本模块内用于校验;导出表 time 仍保留页面原样字符串)
# ---------------------------------------------------------------------------
def parse_time(text: Any, reference_date: Optional[_dt.date] = None) -> _dt.datetime:
    """把页面时间外观解析为 datetime(用于校验比较)。

    支持: "MM-DD HH:MM[:SS]"、"YYYY-MM-DD HH:MM[:SS]"、"YYYY-MM-DDTHH:MM[:SS]"、
    epoch 秒(int/float/数字字符串)。
    MM-DD 缺少年份时,用 reference_date 的年份补全(跨年边界按月份推断年份)。
    """
    if text is None:
        raise ValueError("time 为空")
    if isinstance(text, (int, float)):
        return _epoch_to_datetime(float(text))
    raw = str(text).strip()
    ref_year = reference_date.year if reference_date is not None else 2000
    # 完整格式: YYYY-MM-DD 或 YYYY-MM-DDTHH:MM...
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return _dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    # 短格式: MM-DD HH:MM[:SS](用参考年份补全;跨年用月份推断)
    for fmt in ("%m-%d %H:%M:%S", "%m-%d %H:%M"):
        try:
            parsed = _dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
        year = ref_year
        if reference_date is not None and parsed.month < reference_date.month:
            year = ref_year + 1  # 参考日期之后的新年
        return parsed.replace(year=year)
    # epoch 秒的数字字符串
    if raw.replace(".", "", 1).isdigit() and len(raw.split(".")[0]) >= 9:
        return _epoch_to_datetime(float(raw))
    raise ValueError(f"无法解析的 time 外观: {raw!r}")


def _epoch_to_datetime(value: float) -> _dt.datetime:
    """epoch 秒 → aware(UTC)datetime;越界等异常统一转 ValueError(不外抛)。"""
    try:
        return _dt.datetime.fromtimestamp(value, tz=_dt.timezone.utc)
    except (ValueError, OSError, OverflowError) as exc:
        raise ValueError(f"epoch 秒越界/无法转换: {value!r}({exc})") from exc


def minute_span_minutes(first_time: Any, last_time: Any, reference_date: Optional[_dt.date] = None) -> int:
    """首尾时间差(分钟),要求分钟对齐;用于推算“跨度分钟数”。

    首尾混用 naive/aware(如 MM-DD 字符串与 epoch 秒)等不可比较情况统一转成
    ValueError(绝不外抛 TypeError),由调用方按“跨度不可得”处理。
    """
    first = parse_time(first_time, reference_date)
    last = parse_time(last_time, reference_date)
    try:
        seconds = int(round((last - first).total_seconds()))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"首尾时间外观混用/不可比较: {first_time!r} → {last_time!r}({exc})"
        ) from exc
    minutes = seconds / 60.0
    if abs(minutes - round(minutes)) > 1e-6:
        raise ValueError(f"时间轴首尾不是整分钟对齐: {first_time!r} → {last_time!r}")
    return int(round(minutes))


# ---------------------------------------------------------------------------
# 校验实现
# ---------------------------------------------------------------------------
def validate(
    rows: List[Dict[str, Any]],
    *,
    cumulative_total: Optional[float] = None,
    duration_minutes: Optional[int] = None,
    reference_date: Optional[_dt.date] = None,
    sum_tolerance_abs: float = DEFAULT_SUM_ABS_TOLERANCE,
    sum_tolerance_rel: float = DEFAULT_SUM_REL_TOLERANCE,
    row_tolerance_minutes: int = DEFAULT_ROW_TOLERANCE,
) -> Dict[str, Any]:
    """执行三项校验;返回结构化报告 {ok, checks:[...], total, ...}。"""
    checks: List[Dict[str, Any]] = []
    if not rows:
        return {"ok": False, "checks": [{
            "name": "row_count", "ok": False, "expected": 1,
            "actual": 0, "detail": "行数为 0,无数据可校验",
        }], "total_gmv_min": 0.0}

    times = [str(r.get("time")) for r in rows]
    total_gmv = sum(float(r.get("gmv_min") or 0.0) for r in rows)

    # 1) 时间轴严格递增且按分钟连续
    continuity_detail = []
    first_dt = last_dt = None
    continuity_ok = True
    for i in range(1, len(times)):
        prev_text = times[i - 1]
        curr_text = times[i]
        try:
            prev = parse_time(prev_text, reference_date)
            curr = parse_time(curr_text, reference_date)
        except (ValueError, TypeError) as exc:  # 解析失败:记录明细,绝不外抛
            continuity_ok = False
            continuity_detail.append(f"第 {i - 1}/{i} 行时间解析失败: {exc}")
            continue
        # naive(日期字符串)与 aware(epoch 秒)混用无法直接比较 → 判为时间轴失败
        if (prev.tzinfo is None) != (curr.tzinfo is None):
            continuity_ok = False
            continuity_detail.append(
                f"第 {i - 1}→{i} 行时间外观混用/不可比较: {prev_text!r} → {curr_text!r}"
            )
            continue
        try:
            diff = (curr - prev).total_seconds()
        except (TypeError, ValueError) as exc:  # 防御:任何不可比较情况都不外抛
            continuity_ok = False
            continuity_detail.append(
                f"第 {i - 1}→{i} 行时间外观混用/不可比较: {prev_text!r} → {curr_text!r} ({exc})"
            )
            continue
        if diff <= 0:
            continuity_ok = False
            continuity_detail.append(
                f"第 {i - 1}→{i} 行时间未递增: {prev_text!r} → {curr_text!r} (差 {diff} 秒)"
            )
        elif abs(diff - 60.0) > 1e-6:
            continuity_ok = False
            continuity_detail.append(
                f"第 {i - 1}→{i} 行时间跳变: {prev_text!r} → {curr_text!r} (差 {diff / 60.0:.2f} 分钟,应为 1)"
            )
    try:
        first_dt = parse_time(times[0], reference_date)
        last_dt = parse_time(times[-1], reference_date)
    except (ValueError, TypeError):
        pass
    if len(continuity_detail) > 8:
        continuity_detail = continuity_detail[:8] + [f"... 共 {len(continuity_detail)} 处问题"]
    checks.append({
        "name": "time_axis",
        "ok": continuity_ok,
        "detail": "时间轴严格递增且按分钟连续" if continuity_ok else ("时间轴不连续/未递增:\n  " + "\n  ".join(continuity_detail)),
    })

    # 2) 行数 ≈ 直播时长(分钟)+ 1
    span_minutes = None
    if first_dt is not None and last_dt is not None:
        try:
            span_minutes = minute_span_minutes(times[0], times[-1], reference_date)
        except (ValueError, TypeError):
            span_minutes = None
    expected_rows = None
    if duration_minutes is not None:
        expected_rows = int(duration_minutes) + 1
        basis = "meta.duration_minutes(直播时长)"
    elif span_minutes is not None:
        expected_rows = span_minutes + 1
        basis = "首尾时间跨度"
    rows_ok = expected_rows is None or abs(len(rows) - expected_rows) <= row_tolerance_minutes
    checks.append({
        "name": "row_count",
        "ok": rows_ok,
        "expected": expected_rows,
        "actual": len(rows),
        "detail": (
            f"行数 {len(rows)} = 直播时长/跨度 {expected_rows - 1 if expected_rows is not None else '?'} 分钟 + 1"
            if rows_ok else
            f"行数 {len(rows)} 与期望 {expected_rows}(依据 {basis})差 {len(rows) - expected_rows} 行,超出容差 {row_tolerance_minutes}"
        ),
    })

    # 3) 非零分钟之和 ≈ 页面累计成交金额(金额为 0 的分钟贡献为 0,等价于全量求和)
    if cumulative_total is None:
        checks.append({
            "name": "sum_against_cumulative",
            "ok": True,
            "skipped": True,
            "detail": "未提供页面累计成交金额(cumulative_total),求和校验跳过——需在线复核补齐",
        })
    else:
        cumulative = float(cumulative_total)
        diff = total_gmv - cumulative
        ok = abs(diff) <= sum_tolerance_abs or abs(diff) <= sum_tolerance_rel * abs(cumulative)
        checks.append({
            "name": "sum_against_cumulative",
            "ok": ok,
            "expected": cumulative,
            "actual": total_gmv,
            "detail": (
                f"非零分钟之和 {total_gmv:.4f} ≈ 累计成交金额 {cumulative:.4f}(差 {diff:.4f},"
                f"容差 abs={sum_tolerance_abs} 或 rel={sum_tolerance_rel})"
                if ok else
                f"非零分钟之和 {total_gmv:.4f} 与累计成交金额 {cumulative:.4f} 差 {diff:.4f},"
                f"超出容差 abs={sum_tolerance_abs} 或 rel={sum_tolerance_rel}"
            ),
        })

    ok_all = all(c.get("ok") is True for c in checks)
    return {
        "ok": ok_all,
        "checks": checks,
        "total_gmv_min": round(total_gmv, 4),
        "rows": len(rows),
        "time_first": times[0] if times else None,
        "time_last": times[-1] if times else None,
    }


def format_report(report: Dict[str, Any]) -> str:
    """把校验报告格式化为多行文本(控制台/异常明细)。"""
    lines = []
    for check in report["checks"]:
        status = "PASS" if check.get("ok") else "FAIL"
        if check.get("skipped"):
            status = "SKIP"
        lines.append(f"[{status}] {check['name']}: {check['detail']}")
    if not report["ok"]:
        lines.insert(0, f"校验未通过({sum(1 for c in report['checks'] if not c.get('ok'))} 项失败),拒绝输出:")
    return "\n".join(lines)


def assert_valid(report: Dict[str, Any]) -> None:
    """校验失败时抛 ValidationError(含逐项明细);通过时静默返回。"""
    if not report.get("ok"):
        details = [f"[{c.get('name')}] {c.get('detail')}" for c in report["checks"] if not c.get("ok")]
        raise ValidationError(format_report(report), details)
