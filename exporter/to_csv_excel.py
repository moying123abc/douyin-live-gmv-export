# -*- coding: utf-8 -*-
"""exporter/to_csv_excel.py — 输出层(M2)。

- write_csv  : UTF-8 BOM CSV(stdlib csv,无第三方依赖,结果确定)
- write_xlsx : 可选 Excel(经 openpyxl 生成;openpyxl 未安装时给出明确提示并抛
  XlsxUnavailable,由调用方决定跳过——导出主产物始终是 CSV)
- 表头一律取自 exporter/schema.COLUMNS,保证与 schema.py 一致。

口径说明:
- CSV 第一列到第四列依次为 room_id / session_date / time / gmv_min;
- gmv_min 保留数值(两位小数以内的金额原样写出,不做舍入改写);
- 金额为 0 的分钟是正常行,原样保留。
"""
from __future__ import annotations

import csv
import pathlib
import re
from typing import Dict, List, Sequence

from exporter import schema as _schema


class XlsxUnavailable(RuntimeError):
    """openpyxl 缺失或 Excel 写入失败。"""


# 精简表(成交点)固定表头:time 只含 HH:MM,去掉日期;只保留有成交的行。
COMPACT_COLUMNS: List[str] = ["time", "gmv_min"]


def compact_rows(rows: Sequence[Dict]) -> List[Dict]:
    """把标准分钟行(room_id/session_date/time/gmv_min)转成"成交点精简行"。

    规则(用户需求):
      - time 只保留 HH:MM(去掉 MM-DD / YYYY-MM-DD 日期前缀);
      - 只保留 gmv_min > 0 的分钟(删除 0 金额行);
      - 保留 time 的字符串外观、gmv_min 数值原样(不做舍入改写)。
    时间外观无法解析成 HH:MM 或金额非数值时抛 ValueError(结构化拒绝,不静默)。
    """
    out: List[Dict] = []
    for row in rows:
        raw_time = str(row.get("time") or "").strip()
        token = raw_time.split()[-1] if raw_time else ""
        if not re.fullmatch(r"\d{1,2}:\d{2}", token):
            raise ValueError(f"无法从 time 字段提取 HH:MM 时间外观: {raw_time!r}")
        try:
            val = float(row.get("gmv_min"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"gmv_min 不是数值: {row.get('gmv_min')!r}") from exc
        if val > 0:
            out.append({"time": token, "gmv_min": val})
    return out


def write_compact_csv(path, compact_rows_: Sequence[Dict]) -> pathlib.Path:
    """写"成交点精简表"CSV(UTF-8 BOM,表头 time,gmv_min)。"""
    out = pathlib.Path(path)
    if out.suffix.lower() != ".csv":
        out = out.with_suffix(".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(COMPACT_COLUMNS)
        for row in compact_rows_:
            writer.writerow([row.get("time", ""), row.get("gmv_min", "")])
    return out


def build_rows(rows: Sequence[Dict], *, columns: Sequence[str] | None = None) -> List[List]:
    """把 schema 标准行转成与表头对齐的二维数组。

    columns 缺省为 schema.headers()(基础四列);传入含 M3 扩展列的表头时,
    逐行按该顺序取值,行中缺失的扩展列单元格以空串补位(不伪造、不改变
    time/gmv_min 语义)。
    """
    cols = list(columns) if columns is not None else _schema.headers()
    table: List[List] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"行必须是 dict,得到 {type(row).__name__}")
        base_missing = [c for c in _schema.headers() if c not in row]
        if base_missing:
            raise ValueError(f"行缺少基础字段 {base_missing}: {row!r}")
        table.append([row.get(c, "") for c in cols])
    return table


def write_csv(path, rows: Sequence[Dict], *, columns: Sequence[str] | None = None) -> pathlib.Path:
    """写 UTF-8 BOM CSV;columns 默认 schema.headers()(与 schema.py 一致)。"""
    cols = list(columns) if columns is not None else _schema.headers()
    out = pathlib.Path(path)
    if out.suffix.lower() != ".csv":
        out = out.with_suffix(".csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    table = build_rows(rows, columns=cols)
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        writer.writerows(table)
    return out


def write_xlsx(path, rows: Sequence[Dict], *, columns: Sequence[str] | None = None) -> pathlib.Path:
    """可选 Excel:经 openpyxl 写 .xlsx;openpyxl 缺失时抛 XlsxUnavailable。"""
    try:
        import openpyxl  # type: ignore
    except ImportError as exc:
        raise XlsxUnavailable(
            "生成 Excel 需要 openpyxl: pip install -r requirements.txt(CSV 已正常输出)"
        ) from exc
    cols = list(columns) if columns is not None else _schema.headers()
    out = pathlib.Path(path)
    if out.suffix.lower() != ".xlsx":
        out = out.with_suffix(".xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "分钟成交金额"
    ws.append(cols)
    for row in build_rows(rows, columns=cols):
        ws.append(row)
    wb.save(str(out))
    return out
