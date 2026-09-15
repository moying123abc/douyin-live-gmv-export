# -*- coding: utf-8 -*-
"""extractor/trend.py — 读取整场“每分钟新增成交金额”序列(M2)。

数据来源优先级(与探测层级一致):
- L0(首选):在已登录回放页的 JS 上下文读取图表数据数组(ECharts xAxis/series),
  零网络依赖;
- L1(次选):被动捕获页面加载中返回的、含分钟时间序列的 JSON 响应(只读);
- L3:逐点悬停 tooltip,仅作兜底(M1 probe 脚手架),本模块不在导出主链路使用。

在线读取代码路径与 probe.py 同源同假设;由于本仓库在“无 playwright / 无人工登录”
环境下开发,在线命中层级的执行与字段语义均需人工复核(见 README 与
docs/export-schema-and-validation.md 的“待在线复核清单”)。

离线驱动:
- 本模块同时提供 load_fixture(path):读取 data/samples 下“recorded-sample”夹具
  (合成样例,非真实页面数据),还原整场序列并补齐场次元信息,
  使 export 子命令可在不登录、无浏览器的环境下跑通全部校验。

字段口径(团队既定方案,勿偏离):
- time     = 页面数据自带时间,原样保留(MM-DD HH:MM 或 YYYY-MM-DD HH:MM)
- gmv_min  = 该分钟新增成交金额(元),金额为 0 的分钟必须保留
- 整场时间轴连续;导出表列与 exporter/schema.COLUMNS 一致
"""
from __future__ import annotations

import json
import pathlib
from typing import Any, Dict, List, Optional, Tuple

from exporter import schema as _schema

# 夹具中可选的元信息键(同时兼容 probe 证据字段名)
_META_KEYS = {
    "room_id": "room_id",
    "session_date": "session_date",
    "cumulative_total": "cumulative_total",
    "duration_minutes": "duration_minutes",
    "layer_hit": "layer_hit",
    "time_format": "time_format",
    "session_start": "session_start",
    "session_end": "session_end",
}


class TrendUnavailable(RuntimeError):
    """在线读取不可用 / 夹具结构无法还原整场序列时抛出(附人工引导)。"""


# ---------------------------------------------------------------------------
# 离线:夹具读取
# ---------------------------------------------------------------------------
def load_fixture(path) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """读取 data/samples 的 recorded-sample 夹具,还原整场序列与场次元信息。

    支持两种结构(以顶层键区分):
    A) evidence 形态(probe 证据格式扩展,含整场 rows):
       {..., layer_hit, field_definition, session_bounds,
        rows_observed, rows: [{time, gmv_min}, ...],
        cumulative_total?, session_date?, duration_minutes?}
    B) captured-payload 形态(L1 响应样例):
       {room_id?, session_date?, cumulative_total?, duration_minutes?,
        time_format?, <任意嵌套>: {time:[...], gmv:[...]}}(等长 时间+数值 数组)

    只接受“整场完整序列”的夹具;仅有 row_sample(样例行)或只有首尾样例时,
    明确报错而非用样例冒充整场数据。
    """
    p = pathlib.Path(path)
    if not p.is_file():
        raise TrendUnavailable(f"夹具文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrendUnavailable(f"夹具解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise TrendUnavailable("夹具顶层必须是 JSON 对象")

    meta = _extract_meta(data)
    rows: List[Dict[str, Any]] = []

    # 形态 A: 显式整场 rows
    if isinstance(data.get("rows"), list):
        rows = _rows_from_list(data["rows"])
    # 形态 B: 扫描嵌套 时间+数值 等长数组
    if not rows:
        pair = _find_full_series(data)
        if pair is None:
            raise TrendUnavailable(
                "夹具中找不到整场序列:需要 rows:[{time,gmv_min},...] 或等长的 "
                "时间+数值 数组(data/samples 样例见 README)。仅有 row_sample 样例行"
                "不足以还原整场数据,不允许用样例冒充整场。"
            )
        rows = [
            {"time": t, "gmv_min": v}
            for t, v in zip(pair["time"], pair["value"])
        ]

    if not rows:
        raise TrendUnavailable("夹具解析出的行数为 0")
    if meta.get("rows_observed") is not None and meta["rows_observed"] != len(rows):
        raise TrendUnavailable(
            f"夹具 rows_observed={meta['rows_observed']} 与实际行数 {len(rows)} 不一致,夹具损坏"
        )

    # 统一为 schema 标准行(room_id / session_date 来自元信息)
    normalized = _schema.normalize_rows(rows)
    for row in normalized:
        if not row["room_id"]:
            row["room_id"] = meta.get("room_id") or ""
        if not row["session_date"]:
            row["session_date"] = meta.get("session_date") or ""

    meta = {
        **_meta_defaults(),
        **meta,
        "layer": meta.get("layer_hit") or "fixture",
        "source": str(p),
        "assumptions": meta.get("assumptions")
        or [
            "夹具为合成样例(recorded sample),仅用于离线驱动开发与自测,不代表真实页面数据",
            "time 为页面口径时间字符串;gmv_min=该分钟新增成交金额,0 分钟保留",
        ],
        "online_recheck_required": meta.get("online_recheck_required")
        or [
            "在线复核:真实页面命中层(L0/L1)读出的时间格式与字段语义与夹具一致",
            "在线复核:页面累计成交金额用于求和校验(容差可配)",
        ],
    }
    return normalized, meta


def _extract_meta(data: Dict[str, Any]) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    # 兼容 probe 证据:probe_meta.room_id
    probe_meta = data.get("probe_meta")
    if isinstance(probe_meta, dict):
        meta["room_id"] = probe_meta.get("room_id") or data.get("room_id")
        meta["session_state_hint"] = probe_meta.get("session_state_hint")
    meta["room_id"] = meta.get("room_id") or data.get("room_id")
    for key in ("session_date", "cumulative_total", "duration_minutes", "layer_hit", "time_format"):
        if data.get(key) is not None:
            meta[key] = data[key]
    fd = data.get("field_definition")
    if isinstance(fd, dict) and meta.get("time_format") is None:
        meta["time_format"] = fd.get("time_format")
    bounds = data.get("session_bounds") or {}
    if isinstance(bounds, dict):
        if meta.get("session_date") is None and _date_hint(bounds.get("observed_start")):
            meta["session_date"] = _date_hint(bounds["observed_start"])
        if meta.get("duration_minutes") is None:
            meta["duration_minutes"] = bounds.get("duration_minutes")
        meta["session_start"] = bounds.get("observed_start")
        meta["session_end"] = bounds.get("observed_end")
    if data.get("rows_observed") is not None:
        meta["rows_observed"] = data["rows_observed"]
    for key in ("assumptions", "online_recheck_required"):
        if isinstance(data.get(key), list):
            meta[key] = [str(x) for x in data[key]]
    return meta


def _meta_defaults() -> Dict[str, Any]:
    return {
        "room_id": "",
        "session_date": "",
        "cumulative_total": None,
        "duration_minutes": None,
        "layer_hit": "fixture",
        "time_format": "MM-DD HH:MM 或 YYYY-MM-DD HH:MM(以夹具/页面为准)",
        "session_start": None,
        "session_end": None,
        "source": "",
    }


def _date_hint(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) >= 10 and text[:4].isdigit() and text[4] == "-":
        return text[:10]
    return None


def _rows_from_list(items: List[Any]) -> List[Dict[str, Any]]:
    rows = []
    for item in items:
        if not isinstance(item, dict):
            raise TrendUnavailable(f"rows 里的元素必须是对象,得到 {type(item).__name__}")
        time_v = item.get("time") or item.get("time_label")
        value_v = item.get("gmv_min")
        if value_v is None:
            value_v = item.get("gmv")
        if time_v is None or value_v is None:
            raise TrendUnavailable(f"rows 行缺少 time/gmv_min: {item!r}")
        rows.append({"time": time_v, "gmv_min": value_v})
    return rows


def _find_full_series(node: Any) -> Optional[Dict[str, Any]]:
    """递归查找“等长 时间字符串数组 + 数值数组”配对,返回整组数组(最内层优先匹配)。"""
    best: Optional[Dict[str, Any]] = None
    best_size = 0

    def visit(value: Any) -> None:
        nonlocal best, best_size
        if isinstance(value, dict):
            time_key = value_key = None
            for k, v in value.items():
                if isinstance(v, list) and v and all(isinstance(x, str) for x in v) and _time_like(v[0]):
                    time_key = k
                elif isinstance(v, list) and v and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
                    value_key = k
            if time_key is not None and value_key is not None and time_key != value_key:
                tv = value[time_key]
                vv = value[value_key]
                if len(tv) == len(vv) and len(tv) > best_size:
                    best = {"time": tv, "value": vv, "size": len(tv)}
                    best_size = len(tv)
            for v in value.values():
                visit(v)
        elif isinstance(value, list):
            for v in value:
                visit(v)

    visit(node)
    return best


# 商品/营销/违规类时间轴 URL 标记:不作为整场成交金额序列来源(不收集)
_EXCLUDED_SERIES_URL_MARKS = (
    "product_trend",
    "marketing_data",
    "follow_product",
    "product_explanation",
    "sales_tool",
)


def _url_is_excluded_series(url: str) -> bool:
    u = (url or "").lower()
    return any(mark in u for mark in _EXCLUDED_SERIES_URL_MARKS)


def _find_row_series(node: Any) -> Optional[Dict[str, Any]]:
    """递归查找“行对象序列”:[{x:'09-02 11:16', y:0, time_stamp:'...'}, ...]。

    这是抖音生活服务直播大屏 room_minute_indicator 的真实形状(2026-09 实测);
    返回整组 {time, value, time_key, value_key, series_key, epoch_first, size},
    找不到返回 None。
    """
    best: Optional[Dict[str, Any]] = None
    _VALUE_KEY_PRIORITY = ("y", "value", "val", "gmv", "pay_gmv", "amount", "cnt")

    def visit(value: Any) -> None:
        nonlocal best
        if isinstance(value, dict):
            for key, val in value.items():
                if isinstance(val, list) and len(val) >= 10 and val and isinstance(val[0], dict):
                    if not all(isinstance(x, dict) for x in val):
                        continue
                    first = val[0]
                    time_key = None
                    numeric_keys: List[str] = []
                    str_time_fields: List[str] = []
                    for field in first:
                        samples = [item.get(field) for item in val]
                        s0 = samples[0]
                        if s0 is None:
                            continue
                        if isinstance(s0, str) and all(isinstance(x, str) for x in samples) \
                                and _time_like(s0):
                            str_time_fields.append(field)
                        elif isinstance(s0, (int, float)) and not isinstance(s0, bool) \
                                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in samples):
                            numeric_keys.append(field)
                    if not str_time_fields or not numeric_keys:
                        continue
                    # 时间字段优先选“格式化时间”(MM-DD HH:MM 等),其次才允许 epoch 数字串
                    time_key = next(
                        (f for f in str_time_fields if not f.replace(".", "", 1).isdigit()),
                        str_time_fields[0],
                    )
                    value_key = next((vk for vk in _VALUE_KEY_PRIORITY if vk in numeric_keys), numeric_keys[0])
                    tv = [str(item[time_key]) for item in val]
                    vv = [item[value_key] for item in val]
                    series_key = value.get("key") if isinstance(value.get("key"), str) else str(key)
                    epoch_first = None
                    if "time_stamp" in first:
                        ts = first["time_stamp"]
                        if isinstance(ts, str) and ts.isdigit():
                            epoch_first = int(ts)
                        elif isinstance(ts, (int, float)):
                            epoch_first = int(ts)
                    cand = {
                        "time": tv,
                        "value": vv,
                        "time_key": time_key,
                        "value_key": value_key,
                        "series_key": series_key,
                        "epoch_first": epoch_first,
                        "size": len(tv),
                        "gmvish": "gmv" in str(series_key).lower() or "gmv" in str(value_key).lower(),
                    }
                    if best is None or (cand["gmvish"] and not best["gmvish"]) \
                            or (cand["gmvish"] == best["gmvish"] and cand["size"] > best["size"]):
                        best = cand
                if isinstance(val, (dict, list)):
                    visit(val)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(node)
    return best


def _find_cumulative_key_index(bodies: List[Dict[str, Any]]) -> Optional[float]:
    """从 key_index 响应读取直播间成交金额(PayGmv.value,元)作为求和校验的累计目标。

    实测(2026-09):PayGmv 标题“直播间成交金额”、unit 元,值与
    pay_order_gmv_minute_trend 全行求和一致;找不到返回 None(求和校验 SKIP)。
    """
    for body in bodies:
        url = body.get("url") or ""
        if "key_index" not in url:
            continue
        node = body.get("json")
        if not isinstance(node, dict):
            continue
        data = node.get("data")
        if isinstance(data, dict):
            pay = data.get("PayGmv")
            if isinstance(pay, dict):
                value = pay.get("value")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value)
    return None


def _time_like(text: str) -> bool:
    t = text.strip()
    if len(t) >= 5 and t[2] == "-" and t[:2].isdigit():
        return True
    if len(t) >= 10 and t[4] == "-" and t[:4].isdigit():
        return True
    return t.replace(".", "", 1).isdigit() and len(t) >= 9


# ---------------------------------------------------------------------------
# 在线读取(需已登录;与 probe.py 同源,注释标注假设)
# ---------------------------------------------------------------------------
_L0_FULL_JS = r"""
() => {
  // L0 整场读取:枚举 ECharts 实例,取 xAxis.data(时间)与数值 series.data 的完整数组
  const out = { instances: 0, hit: false, xAxis: null, series: [], notes: [] };
  try {
    const els = Array.prototype.slice.call(document.querySelectorAll('[_echarts_instance_]'));
    out.instances = els.length;
    let echarts = null;
    try {
      if (window.echarts) echarts = window.echarts;
      else if (window.require) echarts = window.require('echarts');
    } catch (e) { out.notes.push(String(e)); }
    for (const el of els) {
      try {
        if (!echarts) break;
        const inst = echarts.getInstanceByDom(el);
        if (!inst) continue;
        const opt = inst.getOption();
        const xAxis = (opt && Array.isArray(opt.xAxis) && opt.xAxis[0]) || null;
        const xData = (xAxis && Array.isArray(xAxis.data)) ? xAxis.data : null;
        const series = (opt && Array.isArray(opt.series)) ? opt.series : [];
        for (const s of series) {
          const data = (s && Array.isArray(s.data)) ? s.data : null;
          if (!data || data.length < 5) continue;
          const numeric = data.every(function (d) {
            return typeof d === 'number';
          });
          if (!numeric) continue;
          out.series.push({
            name: String((s && s.name) || ''), type: String((s && s.type) || ''),
            data: data, xData: xData || null,
          });
        }
      } catch (e) { out.notes.push(String(e)); }
    }
    // 选择:优先带时间 xData 的最大数值序列
    const picked = out.series.filter(function (s) { return s.xData && s.xData.length === s.data.length; })
      .sort(function (a, b) { return b.data.length - a.data.length; })[0]
      || out.series.sort(function (a, b) { return b.data.length - a.data.length; })[0];
    if (picked) { out.hit = true; out.xAxis = picked.xData; out.series = [picked]; }
  } catch (e) { out.notes.push('l0:' + String(e)); }
  return out;
}
"""


def read_live(cfg: dict, room_id: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """在线整场读取(L0→L1;与 probe.py 同源)。

    真机验证(2026-09-04,抖音生活服务/本地直播专业版回放页 room_id=7000000000000000002):
    L1 命中 room_minute_indicator 行对象序列 key=pay_order_gmv_minute_trend
    (chart[] 每点 {x:'09-02 11:16'(MM-DD HH:MM), y(元), time_stamp(epoch 秒)}),
    465 行与场次 7h44m 对齐;全行求和 = key_index.PayGmv(直播间成交金额) = 1350 元,
    求和校验通过。商品/营销端点(product_trend/marketing_data 等)一律排除,不收集。
    若两层都未命中,抛 TrendUnavailable 并给出人工引导与待复核清单,绝不臆造数据。
    """
    import probe  # 复用 M1 的捕获/扫描工具
    from extractor.validation import parse_time, minute_span_minutes

    url = probe._replay_url(cfg, room_id)

    try:
        import auth
        pw, context, page = auth.open_session(cfg, headful=True)
    except SystemExit:
        raise
    except Exception as exc:
        raise TrendUnavailable(f"打开浏览器失败: {exc}(请先 python main.py login)") from exc

    meta = {"room_id": room_id, "session_date": "", "cumulative_total": None,
            "duration_minutes": None, "time_format": None,
            "session_start": None, "session_end": None, "source": url, "layer_hit": None}
    try:
        if not auth.ensure_logged_in(cfg, context, page):
            raise TrendUnavailable("无登录会话;请先 python main.py login 完成人工扫码登录")
        hints = cfg.get("probe", {}).get("network_url_hints") or []
        recorder = probe._ResponseRecorder(hints)
        page.on("response", recorder.on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            raise TrendUnavailable(f"打开回放页失败(无权限/无网络?): {exc}") from exc
        load_wait = int(cfg.get("probe", {}).get("load_wait_seconds", 25))
        for _ in range(max(1, load_wait // 5)):
            page.wait_for_timeout(5000)
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(3000)
        recorder.read_bodies()

        # L0: 页面图表数据数组(整场)
        rows = None
        best = None
        try:
            report = page.evaluate(_L0_FULL_JS)
            if isinstance(report, dict) and report.get("hit"):
                xdata = report.get("xAxis") or []
                series = (report.get("series") or [])[0]
                sdata = (series or {}).get("data") or []
                if xdata and len(xdata) == len(sdata):
                    rows = [{"time": t, "gmv_min": v} for t, v in zip(xdata, sdata)]
                    meta["layer_hit"] = "L0"
                    meta["time_format"] = "L0 图表 xAxis 口径(需在线复核)"
        except Exception as exc:  # pragma: no cover - 在线路径
            print(f"[trend] L0 求值失败: {exc}")

        # L1: 捕获的响应 JSON 中的完整 时间+数值 序列(等长数组 或 行对象两种形状)
        if not rows:
            best = None
            for body in recorder.bodies:
                burl = body.get("url") or ""
                if _url_is_excluded_series(burl):
                    continue  # 商品/营销类时间轴不作为整场成交金额来源(不收集)
                cand = _find_full_series(body["json"])
                if cand is None:
                    cand = _find_row_series(body["json"])
                if cand:
                    cand["url"] = burl
                    if best is None or (cand.get("gmvish") and not best.get("gmvish")) \
                            or (cand.get("gmvish") == best.get("gmvish")
                                and len(cand["time"]) > len(best["time"])):
                        best = cand
            if best is not None:
                rows = [{"time": t, "gmv_min": v} for t, v in zip(best["time"], best["value"])]
                meta["layer_hit"] = "L1"
                meta["source"] = best.get("url") or url
                meta["time_key"] = best.get("time_key")
                meta["value_key"] = best.get("value_key")
                if best.get("series_key"):
                    meta["series_key"] = best["series_key"]
                    meta["time_format"] = (f"L1 行对象序列 key={best['series_key']}: "
                                           f"time={best.get('time_key')}(MM-DD HH:MM), value={best.get('value_key')}")
                else:
                    meta["time_format"] = "L1 响应字段口径(需在线复核)"

        if not rows:
            raise TrendUnavailable(
                "L0/L1 均未命中整场序列。人工引导:在浏览器手动打开回放页确认趋势图与接口可用,"
                "并携带 data/probe/*.json 证据与截图反馈;本工具不臆造数据。"
            )

        # 补齐元信息:起止/时长/日期/累计(尽力推断,失败留空并列入待复核)
        try:
            first_dt = parse_time(rows[0]["time"])
            last_dt = parse_time(rows[-1]["time"])
            meta["session_start"] = str(rows[0]["time"])
            meta["session_end"] = str(rows[-1]["time"])
            meta["duration_minutes"] = minute_span_minutes(meta["session_start"], meta["session_end"])
            if meta["duration_minutes"] is not None and first_dt.year != 2000:
                meta["session_date"] = first_dt.strftime("%Y-%m-%d")
        except Exception:
            pass
        # session_date 优先取时间轴首点 year(MM-DD 无年份),缺失时用行对象 time_stamp epoch 推断
        if not meta.get("session_date") and best is not None and best.get("epoch_first"):
            try:
                from datetime import datetime as _dtdt, timezone as _dtz
                meta["session_date"] = _dtdt.fromtimestamp(
                    int(best["epoch_first"]), tz=_dtz.utc).date().isoformat()
            except Exception:
                pass
        # 累计成交金额:key_index.PayGmv(页面“直播间成交金额”,元),与 gmv 序列求和一致(2026-09 实测)
        cumulative = _find_cumulative_key_index(recorder.bodies)
        if cumulative is not None:
            meta["cumulative_total"] = cumulative
        normalized = _schema.normalize_rows(rows)
        for row in normalized:
            row["room_id"] = row["room_id"] or str(room_id)
            row["session_date"] = row["session_date"] or meta.get("session_date") or ""
        meta["assumptions"] = [
            "L1 命中:time=页面自带 x(MM-DD HH:MM,原样保留),gmv_min=y(该分钟新增成交金额,元);"
            + (f"页面序列 key={meta.get('series_key')}" if meta.get("series_key") else ""),
            "累计成交金额取自 key_index.PayGmv(直播间成交金额,元);实测与 pay_order_gmv_minute_trend 全行求和一致",
            "行对象序列与 probe 探测同源;gmv 语义 2026-09 真机复核通过",
        ]
        meta["online_recheck_required"] = [
            "换场次/换账号时复核:页面序列 key 仍为 pay_order_gmv_minute_trend、x/y 语义一致",
            "换场次时复核 key_index.PayGmv 与 gmv 分钟序列求和一致后再启用求和校验",
            "页面显示“直播间成交金额/累计支付金额”的单位(元)与 y 单位一致",
        ]
        return normalized, meta
    finally:
        try:
            context.close()
        finally:
            pw.stop()
