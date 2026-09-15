# -*- coding: utf-8 -*-
"""exporter/schema.py — 导出表结构(既定方案,M2)。

整场分钟级时间序列表的规范化列定义,所有导出(CSV/Excel)与校验都以本模块为唯一
事实来源,保证表头一致:

| 列          | 类型          | 口径(与团队既定方案一致)                              |
|-------------|---------------|-------------------------------------------------------|
| room_id     | str           | 直播间 room_id(字符串,不作数值计算)                  |
| session_date| str           | 场次日期 YYYY-MM-DD;页面未给出时为 ""(不臆造)        |
| time        | str           | 页面数据自带时间,原样保留:MM-DD HH:MM 或 YYYY-MM-DD HH:MM |
| gmv_min     | float         | 该分钟新增成交金额(元);金额为 0 的分钟保留            |

约束:
- time 使用页面数据自带时间,不做自行换算/改写(避免口径漂移);
- gmv_min 为浮点数,空值在读取层即报错,不做静默填充。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

# 唯一事实来源:导出表头顺序
COLUMNS: List[str] = ["room_id", "session_date", "time", "gmv_min"]

# 便捷常量
ROOM_ID = COLUMNS[0]
SESSION_DATE = COLUMNS[1]
TIME = COLUMNS[2]
GMV_MIN = COLUMNS[3]


def headers() -> List[str]:
    """返回基础表头列表(与 schema.COLUMNS 完全一致;M3 指标未启用时即导出表头)。"""
    return list(COLUMNS)


def headers_with(extra_columns: Iterable[str]) -> List[str]:
    """基础表头 + M3 扩展指标列(去重,基础列永远在前)。

    仅当 extra_metrics 开关启用且真机验证可得后才应传入扩展列;
    未启用时调用方必须使用 headers()(保持与 M2 输出完全一致)。
    """
    result = list(COLUMNS)
    for column in extra_columns:
        column = str(column).strip()
        if column and column not in result:
            result.append(column)
    return result


def make_row(room_id: Any, session_date: Any, time_value: Any, gmv_min: Any) -> Dict[str, Any]:
    """构造一行标准表数据;键与 COLUMNS 顺序一致。"""
    return {
        ROOM_ID: str(room_id),
        SESSION_DATE: "" if session_date is None else str(session_date),
        TIME: str(time_value),
        GMV_MIN: _normalize_gmv(gmv_min),
    }


def normalize_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把读取层给出的行统一成 schema 标准行(键名/类型规整)。

    读取层(extractor/trend.py)允许给出 {time, gmv_min} 最小行,此处自动补齐
    room_id / session_date(来自场次元信息)。缺 gmv_min 或 time 直接抛错,不静默。
    """
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"行必须是 dict,得到 {type(row).__name__}")
        if row.get(TIME) is None:
            raise ValueError(f"行缺少 time: {row!r}")
        if row.get(GMV_MIN) is None:
            raise ValueError(f"行缺少 gmv_min: {row!r}")
        normalized.append(
            {
                ROOM_ID: str(row.get(ROOM_ID) if row.get(ROOM_ID) is not None else ""),
                SESSION_DATE: str(row.get(SESSION_DATE) if row.get(SESSION_DATE) is not None else ""),
                TIME: str(row[TIME]),
                GMV_MIN: _normalize_gmv(row[GMV_MIN]),
            }
        )
    return normalized


def _normalize_gmv(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"gmv_min 不是数值: {value!r}") from exc
    if number != number:  # NaN
        raise ValueError(f"gmv_min 为 NaN: {value!r}")
    return number
