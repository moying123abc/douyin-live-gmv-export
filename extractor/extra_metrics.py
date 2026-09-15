# -*- coding: utf-8 -*-
"""extractor/extra_metrics.py — M3 扩展指标(订单数/在线人数等)并表机制。

设计原则(M3 契约):
- “同一分钟粒度、同一分钟行严格对齐”是并表的唯一允许方式;缺失分钟留空("")并在
  报告中注明,任何“用汇总值冒充分钟值 / 用其他粒度强行并表”的行为都会直接拒绝
  (抛 ExtraMetricsError),绝不伪造粒度;
- 真实数据层面,本仓库交付环境(无 playwright、无人工登录)拿不到回放页在线证据,
  故指标开关默认关闭(extra_metrics.enabled=false,见 config.example.yaml);
  探测过程与“不可得”结论见 docs/指标可得性结论.md;
- 机制层已就绪并以“合成夹具(recorded sample)”演示:启用开关 + 夹具携带 extra_metrics
  段时,按 time 逐行对齐并入同表,导出仍通过 M2 原有校验;
- 在线探测(probe_online)与 probe/trend 同源合规:已登录会话内 L0/L1 被动读取,
  不做登录绕过/签名逆向;未命中时返回 reasons(不崩溃、不伪造)。
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, Iterable, List, Optional, Tuple

# 指标注册表:id → 导出列/语义/单位。列名与数据均为“该分钟”粒度(per_minute)。
# 真实并入前必须先真机验证页面确提供该分钟序列(见 docs/指标可得性结论.md)。
EXTRA_METRIC_DEFS: Dict[str, Dict[str, str]] = {
    "order_min": {
        "column": "order_min",
        "label": "订单数(该分钟新增)",
        "unit": "单",
        "semantics": "该分钟新增订单数;口径以页面/真机复核为准",
    },
    "paid_order_min": {
        "column": "paid_order_min",
        "label": "支付订单数(该分钟新增)",
        "unit": "单",
        "semantics": "该分钟新增支付订单数;口径以页面/真机复核为准",
    },
    "online_uv_min": {
        "column": "online_uv_min",
        "label": "在线人数(该分钟)",
        "unit": "人",
        "semantics": "该分钟在线人数(在线 UV);口径以页面/真机复核为准",
    },
    "view_uv_min": {
        "column": "view_uv_min",
        "label": "观看人数(该分钟)",
        "unit": "人",
        "semantics": "该分钟观看人数/观看 UV;口径以页面/真机复核为准",
    },
}


class ExtraMetricsError(RuntimeError):
    """并表不可行(时间轴不一致/粒度不符/夹具结构异常)时抛出,拒绝伪造。"""


def metric_column(metric_id: str) -> str:
    if metric_id not in EXTRA_METRIC_DEFS:
        raise ExtraMetricsError(
            f"未知指标 id: {metric_id}(可选: {', '.join(EXTRA_METRIC_DEFS)})"
        )
    return EXTRA_METRIC_DEFS[metric_id]["column"]


def is_enabled(cfg: dict) -> bool:
    return bool(cfg.get("extra_metrics", {}).get("enabled", False))


def requested_ids(cfg: dict) -> List[str]:
    """从配置读取要并入的指标 id(去重、校验存在性;乱序保留)。"""
    raw = cfg.get("extra_metrics", {}).get("column_ids") or []
    ids: List[str] = []
    for item in raw:
        metric_id = str(item).strip()
        if not metric_id:
            continue
        metric_column(metric_id)  # 未知 id → 抛错,避免静默漏列
        if metric_id not in ids:
            ids.append(metric_id)
    return ids


def requested_columns(cfg: dict) -> List[str]:
    return [metric_column(i) for i in requested_ids(cfg)]


# ---------------------------------------------------------------------------
# 并表核心:严格分钟行对齐
# ---------------------------------------------------------------------------
def merge_aligned(
    rows: List[Dict[str, Any]],
    extra_times: List[Any],
    extra_values: Dict[str, List[Any]],
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """把 {column: [该分钟值,...]} 与 gmv 行按同一分钟行严格对齐并表。

    约束(不满足即抛错,绝不近似/错位/伪造):
    - extra_times 长度必须 == rows 长度,且逐行 time 完全一致;
    - extra_values 中每个 column 的长度必须 == rows 长度;
    - 某分钟缺失(值为 None)时该单元格写 ""(空),并计入 notes 注明;
    - 返回 (rows(已并入), 并入列名列表, notes)。
    """
    if not rows:
        raise ExtraMetricsError("行数为 0,无法并表")
    if extra_times is None or len(extra_times) != len(rows):
        raise ExtraMetricsError(
            f"额外指标时间轴长度 {len(extra_times) if extra_times is not None else 0} "
            f"与成交金额行数 {len(rows)} 不一致,拒绝并表(不伪造粒度)"
        )
    base_times = [str(r.get("time")) for r in rows]
    for i, (left, right) in enumerate(zip(base_times, [str(t) for t in extra_times])):
        if left != right:
            raise ExtraMetricsError(
                f"第 {i} 行时间不一致:成交金额={left!r} vs 扩展指标={right!r};"
                "必须同一分钟行严格对齐,拒绝近似并表"
            )
    added_columns: List[str] = []
    notes: List[str] = []
    missing: Dict[str, List[str]] = {}
    for column, values in extra_values.items():
        if not isinstance(values, list):
            raise ExtraMetricsError(f"{column} 的值不是数组")
        if len(values) != len(rows):
            raise ExtraMetricsError(
                f"{column} 长度 {len(values)} 与行数 {len(rows)} 不一致,拒绝并表"
            )
        added_columns.append(str(column))
        miss = []
        for i, value in enumerate(values):
            if value is None or value == "":
                rows[i][str(column)] = ""
                miss.append(str(rows[i].get("time")))
            else:
                rows[i][str(column)] = float(value) if isinstance(value, bool) else value
        if miss:
            missing[str(column)] = miss
    for column, miss_times in missing.items():
        notes.append(
            f"{column}: {len(miss_times)} 个分钟缺失(留空): {', '.join(miss_times[:5])}"
            + (" …" if len(miss_times) > 5 else "")
        )
    if not added_columns:
        notes.append("未并入任何扩展指标列(extra_metrics 段为空或未请求)")
    return rows, added_columns, notes


# ---------------------------------------------------------------------------
# 夹具并表(离线可得性演示;extra_metrics 段与 rows 同源生成)
# ---------------------------------------------------------------------------
def merge_from_fixture_file(fixture_path, rows: List[Dict[str, Any]], cfg: dict):
    """从夹具 JSON 的 extra_metrics 段读取并对齐并入(仅演示机制,不涉真机数据)。

    夹具段结构:
      "extra_metrics": {
          "time":   [<与 rows[].time 完全一致的分钟标签>],
          "values": {"order_min": [...], "online_uv_min": [...]}
      }
    未携带该段 → 返回 (rows, [], ["夹具未携带 extra_metrics 段…"])。
    """
    p = pathlib.Path(fixture_path)
    if not p.is_file():
        raise ExtraMetricsError(f"夹具文件不存在: {p}")
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    section = data.get("extra_metrics") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return rows, [], ["夹具未携带 extra_metrics 段,无可并入扩展指标"]
    extra_times = section.get("time")
    values = section.get("values")
    if not isinstance(extra_times, list) or not isinstance(values, dict):
        raise ExtraMetricsError("夹具 extra_metrics 段结构异常(需要 time 与 values 对象)")
    # 只并入配置请求且夹具携带的列
    selected: Dict[str, List[Any]] = {}
    for metric_id in requested_ids(cfg):
        column = metric_column(metric_id)
        if column in values:
            selected[column] = values[column]
    if not selected:
        return rows, [], [
            f"未请求/未携带可并入指标(请求={requested_ids(cfg) or '空'})"
        ]
    return merge_aligned(rows, extra_times, selected)


# ---------------------------------------------------------------------------
# 在线探测(启用开关且在线导出时调用;与 probe/trend 同源合规)
# ---------------------------------------------------------------------------
def probe_online(cfg: dict, room_id: str) -> Dict[str, Any]:
    """在线探测回放页是否提供订单/在线人数等分钟序列(合规只读)。

    返回 {available: bool, metrics: [...], reasons: [...]};任何一步不可用都记入
    reasons 并返回 available=False——本函数绝不返回臆测的可得性。
    (本仓库交付环境无 playwright/人工登录,此函数只能在真机执行。)
    """
    reasons: List[str] = []
    try:
        import probe
    except Exception as exc:  # pragma: no cover
        return {"available": False, "metrics": [], "reasons": [f"缺少依赖: {exc}"]}
    try:
        import auth
        pw, context, page = auth.open_session(cfg, headful=True)
    except SystemExit:
        return {"available": False, "metrics": [], "reasons": ["未登录/缺少 playwright,请先 python main.py login"]}
    except Exception as exc:  # pragma: no cover
        return {"available": False, "metrics": [], "reasons": [f"打开浏览器失败: {exc}"]}
    try:
        if not auth.ensure_logged_in(cfg, context, page):
            return {"available": False, "metrics": [], "reasons": ["无登录会话,请先 python main.py login"]}
        hints = cfg.get("probe", {}).get("network_url_hints") or []
        recorder = probe._ResponseRecorder(hints)
        page.on("response", recorder.on_response)
        page.goto(probe._replay_url(cfg, room_id), wait_until="domcontentloaded", timeout=60_000)
        load_wait = int(cfg.get("probe", {}).get("load_wait_seconds", 25))
        for _ in range(max(1, load_wait // 5)):
            page.wait_for_timeout(5000)
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(3000)
        recorder.read_bodies()
        found = _scan_extra_series(recorder.bodies)
        if found:
            return {"available": True, "metrics": found, "reasons": []}
        return {
            "available": False,
            "metrics": [],
            "reasons": [
                f"捕获 {len(recorder.bodies)} 个 JSON 响应,未发现与成交金额同分钟粒度的 "
                "订单/在线人数序列(或字段无法确认语义),需人工在线复核",
            ],
        }
    except Exception as exc:  # pragma: no cover
        return {"available": False, "metrics": [], "reasons": [f"在线探测异常: {exc}"]}
    finally:
        try:
            context.close()
        finally:
            pw.stop()


def _scan_extra_series(bodies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """在捕获响应里找“时间列表 + 多个等长数值列表”的对象,按键名启发式识别指标。

    纯结构识别 + 键名语义猜测;识别结果只作为“真机复核候选”,不是并入结论。
    """
    candidates: List[Dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            time_key = None
            time_list: Optional[List[Any]] = None
            numeric: Dict[str, List[Any]] = {}
            for key, val in node.items():
                if isinstance(val, list) and val:
                    if all(isinstance(x, str) for x in val) and _time_like(val[0]):
                        time_key, time_list = key, val
                    elif all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in val):
                        numeric[key] = val
            if time_key is not None and len(numeric) >= 2 and time_list is not None:
                n = len(time_list)
                equal = [k for k, v in numeric.items() if len(v) == n]
                if len(equal) >= 2:
                    candidates.append(
                        {
                            "time_key": time_key,
                            "n_rows": n,
                            "series_keys": equal,
                            "guesses": {k: _guess_metric(k) for k in equal},
                            "note": "键名语义为启发式推测,并入前需真机复核",
                        }
                    )
            for val in node.values():
                visit(val)
        elif isinstance(node, list):
            for val in node:
                visit(val)

    for body in bodies:
        visit(body.get("json") if isinstance(body, dict) else body)
    return candidates


def _guess_metric(key: str) -> str:
    k = str(key).lower()
    if any(t in k for t in ("order", "订单", "pay", "paid", "支付")):
        return "订单/支付订单类(推测)"
    if any(t in k for t in ("online", "在线", "uv", "viewer", "观众", "watch")):
        return "在线/观看人数类(推测)"
    if any(t in k for t in ("gmv", "amount", "amt", "成交", "金额")):
        return "成交金额类(已有 gmv_min,不重复并入)"
    return f"未知语义({key})"


def _time_like(text: str) -> bool:
    t = text.strip()
    if len(t) >= 5 and t[2] == "-" and t[:2].isdigit():
        return True
    if len(t) >= 10 and t[4] == "-" and t[:4].isdigit():
        return True
    return t.replace(".", "", 1).isdigit() and len(t) >= 9


# ---------------------------------------------------------------------------
# 统一入口(供 main.py export 调用)
# ---------------------------------------------------------------------------
def enrich(cfg: dict, rows: List[Dict[str, Any]], *, fixture_path: Optional[str] = None,
           room_id: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    """启用 extra_metrics 开关时尝试并入扩展指标;未启用则原样返回(与 M2 一致)。

    返回 (rows, 并入列名列表, notes)。任何并表失败都抛 ExtraMetricsError,
    由调用方按“拒绝伪造”处理;在线探测不可得只记 notes(不崩溃、不伪造)。
    """
    if not is_enabled(cfg):
        return rows, [], []
    notes: List[str] = []
    if fixture_path:
        rows, added, merge_notes = merge_from_fixture_file(fixture_path, rows, cfg)
        notes.extend(merge_notes)
        return rows, added, notes
    if room_id:
        result = probe_online(cfg, room_id)
        if result.get("available"):
            notes.append(
                "在线探测到可能与成交金额同分钟粒度的扩展序列(键名见上),"
                "但字段语义仍待人工复核;本版本不自动并入(避免错配列)。"
            )
        notes.extend(result.get("reasons") or [])
        notes.append("扩展指标不可得(本环境):详见 docs/指标可得性结论.md;成交金额导出不受影响")
        return rows, [], notes
    notes.append("启用扩展指标需要 --fixture(夹具演示)或 --room-id(在线);当前无可并入数据")
    return rows, [], notes
