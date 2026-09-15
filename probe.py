# -*- coding: utf-8 -*-
"""probe.py — 分层数据源探测(只读,不产生任何页面写操作)。

目标页面: eos.douyin.com/dp/liveScreen?room_id=<id>&tab=trend
          (已结束场次可回放;本探测只读取"你账号已有权限"的场次数据)

分层策略(命中即停,输出证据到 data/probe/*.json):
- L0: 在页面 JS 上下文中读取图表数据数组(ECharts 实例 option 里的 xAxis/series),
      不依赖网络日志;
- L1: 被动捕获页面加载过程中返回的、疑似"分钟时间序列"的网络 JSON 响应
      (仅读取浏览器自己发起的响应,不改写请求、不伪造响应);
- L3: 逐点悬停读取 tooltip 的兜底脚手架(默认关闭;仅当 L0/L1 未命中且
      config.probe.try_l3=true 时尝试,并提示需人工在线标定)。

范围边界(与既定方案一致):
- 不做登录绕过、不做 a_bogus 等签名逆向;
- 不收集商品-营销-违规事件时间轴;
- 探测证据只描述"数据来源、字段名、字段样例、时间格式、整场起止",
  证据文件里绝不写入伪造的页面数据。

无登录凭据 / 页面不可达 / 缺依赖时:打印明确的人工引导,优雅退出(不崩溃、不伪造)。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import re
import sys

import auth
import config as cfgmod

# 证据文件格式版本:修改证据结构时递增,便于下游(M2)判读
EVIDENCE_SCHEMA_VERSION = "1.0"

# 与 config.example.yaml 保持一致的默认探针参数(仅当配置缺失时使用)
_DEFAULT_LOAD_WAIT = 35
_DEFAULT_SCROLL_WAIT = 3
_DEFAULT_MAX_ROW_SAMPLE = 5
_DEFAULT_URL_HINTS = ["trend", "stats", "live", "room", "board", "gmv"]
# 捕获响应主体上限:分钟指标响应加载中后期才返回,上限过小会被早期响应挤掉
_MAX_RECORDED_BODIES = 64

# 时间字符串外观:页面数据自带时间多为 "MM-DD HH:MM" 或 "YYYY-MM-DD HH:MM[:SS]"
_RE_TIME_MMDD = re.compile(r"^\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$")
_RE_TIME_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")
_RE_EPOCH = re.compile(r"^\d{9,10}(\.\d+)?$")


# ---------------------------------------------------------------------------
# 证据文件格式(预定义;结构变更请同步 docs/probe-evidence-format.md)
# ---------------------------------------------------------------------------
def _evidence_doc() -> dict:
    """返回证据 JSON 的"字段说明"骨架(供文档与注释引用)。"""
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "probe_meta": {
            "room_id": "<直播间 room_id>",
            "url": "<回放页完整 URL>",
            "captured_at_utc": "ISO8601 UTC",
            "page_title": "<页面标题>",
            "session_state_hint": "ended|live|unknown",
        },
        "layer_hit": "L0|L1|L3|none",
        "field_definition": {
            "time_field": "<该分钟时间字段名,取自页面数据>",
            "time_format": "MM-DD HH:MM / YYYY-MM-DD HH:MM / epoch(以页面为准)",
            "gmv_min_field": "<该分钟新增成交金额字段名,取自页面数据>",
            "amount_unit": "yuan(元)",
            "note": "字段语义 = 该分钟新增成交金额;金额为 0 的分钟保留",
        },
        "session_bounds": {
            "observed_start": "<时间轴首点>",
            "observed_end": "<时间轴尾点>",
            "note": "整场起止以时间轴首尾为准;未命中层级时为 null",
        },
        "rows_observed": 0,
        "row_sample": [{"time": "...", "gmv_min": 0}],
        "sources": [
            {
                "layer": "L0|L1|L3",
                "url": "<命中数据来源 URL(仅 L1)>",
                "note": "<该来源说明>",
            }
        ],
        "assumptions": ["<实现假设,须逐条人工复核>"],
        "online_recheck_required": ["<待在线复核清单>"],
    }


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _now_utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _time_label(value) -> str | None:
    """识别时间外观:MM-DD HH:MM / 完整时间 / epoch 秒;无法识别返回 None。"""
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        if _RE_TIME_MMDD.match(v):
            return "MM-DD HH:MM"
        if _RE_TIME_FULL.match(v):
            return "YYYY-MM-DD HH:MM"
        return None
    if isinstance(value, (int, float)):
        s = str(int(value)) if isinstance(value, float) else str(value)
        if _RE_EPOCH.match(s):
            return "epoch"
        return None
    return None


def _is_numeric_list(x) -> bool:
    return isinstance(x, list) and len(x) > 0 and all(
        isinstance(i, (int, float)) and not isinstance(i, bool) for i in x
    )


def _guess_metric_from_key(key: str) -> str | None:
    """依据字段名猜测语义:优先 gmv/金额类,其次订单/人数类;返回提示或 None。"""
    k = str(key).lower()
    if any(t in k for t in ("gmv", "amount", "amt", "sale", "成交", "金额", "pay")):
        return "gmv/金额(推测)"
    if any(t in k for t in ("order", "订单", "pay_count", "paid")):
        return "订单数(推测)"
    if any(t in k for t in ("online", "viewer", "uv", "人数", "在线", "观众")):
        return "在线/观看人数(推测)"
    return None


# 商品/营销/违规类时间轴 URL 标记:一律不作为整场成交金额候选来源(不收集)
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


def _scan_row_objects(obj, max_matches: int = 8) -> list:
    """递归扫描 JSON,寻找“行对象序列”形状:
    [{x: "09-02 11:16", y: 0, time_stamp: "1788318960"}, ...](列表 ≥10 且元素为同构 dict,
    其中至少一个字符串时间外观字段、至少一个数值字段)。

    真实页面 room_minute_indicator 即为该形状(实测);返回候选形如:
    [{path, time_key, value_key, series_key, time_len, time_format,
      time_first, time_last, value_first, rows_sample, guess}],
    其中 series_key 来自容器对象的 'key'(如 pay_order_gmv_minute_trend)。
    """
    out: list = []
    _VALUE_KEY_PRIORITY = ("y", "value", "val", "gmv", "pay_gmv", "amount", "cnt")

    def walk(node, path: str) -> None:
        if len(out) >= max_matches:
            return
        if isinstance(node, dict):
            for key, val in node.items():
                if isinstance(val, list) and len(val) >= 10 and val and isinstance(val[0], dict):
                    first = val[0]
                    if not all(isinstance(x, dict) for x in val):
                        continue
                    time_key = None
                    numeric_keys = []
                    str_time_fields = []
                    for field in first:
                        samples = [item.get(field) for item in val]
                        s0 = samples[0]
                        if s0 is None:
                            continue
                        if isinstance(s0, str) and all(isinstance(x, str) for x in samples) \
                                and _time_label(s0) is not None:
                            str_time_fields.append(field)
                        elif isinstance(s0, (int, float)) and not isinstance(s0, bool) \
                                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in samples):
                            numeric_keys.append(field)
                    if not str_time_fields or not numeric_keys:
                        continue
                    # 时间字段优先选“格式化时间”(MM-DD HH:MM 等),其次才允许 epoch 数字串
                    time_key = next(
                        (f for f in str_time_fields if _time_label(val[0][f]) != "epoch"),
                        str_time_fields[0],
                    )
                    value_key = next((vk for vk in _VALUE_KEY_PRIORITY if vk in numeric_keys), numeric_keys[0])
                    tv = [item[time_key] for item in val]
                    vv = [item[value_key] for item in val]
                    fmt = _time_label(tv[0])
                    series_key = node.get("key") if isinstance(node.get("key"), str) else key
                    guess = _guess_metric_from_key(f"{series_key} {value_key}")
                    rows_sample = [
                        {"time": tv[i], "gmv_min": vv[i]}
                        for i in range(min(5, len(tv)))
                    ]
                    out.append({
                        "path": f"{path}.{key}",
                        "time_key": time_key,
                        "value_key": value_key,
                        "series_key": series_key,
                        "time_len": len(tv),
                        "time_format": fmt,
                        "time_first": tv[0],
                        "time_last": tv[-1],
                        "value_first": vv[0],
                        "rows_sample": rows_sample,
                        "guess": guess,
                    })
                    if len(out) >= max_matches:
                        return
                if isinstance(val, (dict, list)):
                    walk(val, f"{path}.{key}")
        elif isinstance(node, list):
            for i, val in enumerate(node):
                walk(val, f"{path}[{i}]")
                if len(out) >= max_matches:
                    return

    walk(obj, "$")
    return out


# ---------------------------------------------------------------------------
# L1: 网络 JSON 序列扫描(纯函数,便于离线自测)
# ---------------------------------------------------------------------------
def _scan_json_for_series(obj, max_matches: int = 8) -> list:
    """递归扫描 JSON,寻找“等长的 时间轴(字符串/epoch) + 数值数组”候选对。

    返回形如: [{path, time_key, value_key, time_len, value_len,
                time_format, time_first, time_last, value_first, guess}] 的列表。
    只做结构识别,不做语义假设;页面字段名以命中结果为准。
    """
    matches: list = []
    _walk(obj, "$", matches, max_matches)
    return matches


def _walk(node, path: str, out: list, cap: int) -> None:
    if len(out) >= cap:
        return
    if isinstance(node, dict):
        items = list(node.items())
        # 同一层内找时间列表 + 数值列表
        time_cands = []
        num_cands = []
        for key, val in items:
            if isinstance(val, list) and val:
                if all(isinstance(x, str) for x in val):
                    fmt = _time_label(val[0])
                    if fmt:
                        time_cands.append((key, val, fmt))
                elif _is_numeric_list(val):
                    num_cands.append((key, val))
        for tk, tv, fmt in time_cands:
            for vk, vv in num_cands:
                if len(tv) == len(vv) and len(tv) >= 5:
                    guess = _guess_metric_from_key(vk)
                    out.append(
                        {
                            "path": path,
                            "time_key": tk,
                            "value_key": vk,
                            "time_len": len(tv),
                            "value_len": len(vv),
                            "time_format": fmt,
                            "time_first": tv[0],
                            "time_last": tv[-1],
                            "value_first": vv[0],
                            "guess": guess,
                        }
                    )
                    if len(out) >= cap:
                        return
        # 递归子节点
        for key, val in items:
            _walk(val, f"{path}.{key}", out, cap)
            if len(out) >= cap:
                return
    elif isinstance(node, list):
        for i, val in enumerate(node):
            _walk(val, f"{path}[{i}]", out, cap)
            if len(out) >= cap:
                return


# ---------------------------------------------------------------------------
# L0: 页面内读取图表数据数组(JS)
# ---------------------------------------------------------------------------
_L0_JS = r"""
() => {
  const report = { instances: 0, series: [], notes: [] };
  try {
    const els = Array.prototype.slice.call(
      document.querySelectorAll('[_echarts_instance_]')
    );
    report.instances = els.length;
    let echarts = null;
    try {
      if (window.echarts) echarts = window.echarts;
      else if (window.require) echarts = window.require('echarts');
    } catch (e) { report.notes.push('resolve-echarts:' + String(e)); }
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
          if (!data) continue;
          const sItem = {
            name: String((s && s.name) || ''),
            type: String((s && s.type) || ''),
            dataLen: data.length,
            dataHead: data.slice(0, 3).map(function (d) {
              return (d && typeof d === 'object' && 'value' in d) ? d.value : d;
            }),
          };
          if (xData && xData.length > 0) {
            sItem.xLen = xData.length;
            sItem.xHead = xData.slice(0, 3);
            sItem.xLast = xData[xData.length - 1];
          }
          report.series.push(sItem);
        }
      } catch (e) { report.notes.push('chart-scan:' + String(e)); }
    }
  } catch (e) { report.notes.push('l0:' + String(e)); }
  return report;
}
"""


def _try_l0(page) -> dict:
    """L0 尝试:读取页面图表数据数组;返回 {hit, rows, series, note}。"""
    try:
        report = page.evaluate(_L0_JS)
    except Exception as exc:
        return {"hit": False, "reason": f"页面求值失败: {exc}", "report": None}
    if not isinstance(report, dict):
        return {"hit": False, "reason": "L0 返回结构异常", "report": report}

    series = report.get("series") or []
    best = None
    for s in series:
        head = s.get("dataHead") or []
        xhead = s.get("xHead") or []
        # 候选:数值数据 + 时间外观的 x 轴
        if len(head) >= 3 and all(isinstance(v, (int, float)) for v in head):
            if xhead and _time_label(xhead[0]):
                if best is None or (s.get("dataLen") or 0) > (best.get("dataLen") or 0):
                    best = s
    if best is None:
        return {
            "hit": False,
            "reason": f"未在图表实例中发现 时间x轴+数值series 的候选 (instances={report.get('instances')}, series={len(series)})",
            "report": report,
        }

    x_data_len = best.get("xLen")
    data_len = best.get("dataLen")
    fmt = _time_label(best["xHead"][0]) if best.get("xHead") else None
    return {
        "hit": True,
        "layer": "L0",
        "rows_observed": min(x_data_len or 0, data_len or 0),
        "time_format": fmt,
        "time_first": best["xHead"][0] if best.get("xHead") else None,
        "time_last": best.get("xLast"),
        "series_name": best.get("name"),
        "series_type": best.get("type"),
        "note": "字段名无法从 ECharts option 保证(如 时间=xAxis.data,金额=series.data);行语义需在线人工复核",
        "report_summary": {"instances": report.get("instances"), "series": len(series)},
    }


# ---------------------------------------------------------------------------
# L1: 网络响应捕获与判读
# ---------------------------------------------------------------------------
class _ResponseRecorder:
    """被动记录符合 URL 提示词的 JSON 响应(元信息先存,主体后读)。

    说明:handler 里不做网络读取,避免在事件回调中阻塞;进入判读阶段后
    再在主流程读取 response.json()。
    """

    def __init__(self, hints: list):
        self.hints = [h.lower() for h in hints if h]
        self.records = []  # [{url,status,content_type,_resp}]
        self.bodies = []   # [{url,status,json}] 判读阶段填充

    def _hint_match(self, url: str) -> bool:
        u = url.lower()
        return any(h in u for h in self.hints)

    def on_response(self, response) -> None:
        try:
            url = response.url
            status = response.status
            content_type = (response.headers.get("content-type") or "") if hasattr(response, "headers") else ""
        except Exception:
            return
        if not self._hint_match(url):
            return
        if "json" not in content_type.lower():
            return
        # 元信息先存,response 对象引用一并保留;主体在判读阶段(主流程)再读,
        # 避免在事件回调里做网络读取造成阻塞/死锁。
        self.records.append(
            {"url": url, "status": status, "content_type": content_type, "_resp": response}
        )

    def read_bodies(self) -> None:
        """主流程阶段读取各响应 JSON 主体(读取失败则跳过并说明)。

        响应数量上限取 _MAX_RECORDED_BODIES:分钟指标响应在页面加载中后期才返回,
        若上限过小(如 12)会被 monitor/marketing 等早期响应挤掉(2026-09 实测)。
        """
        for rec in self.records:
            if len(self.bodies) >= _MAX_RECORDED_BODIES:
                break
            try:
                resp = rec.get("_resp")
                if resp is None:
                    continue
                data = resp.json()
                if isinstance(data, (dict, list)):
                    self.bodies.append(
                        {"url": rec["url"], "status": rec.get("status"), "json": data}
                    )
            except Exception:
                pass


def _try_l1(recorder) -> dict:
    """L1 判读:在已捕获的响应 JSON 中扫描时间序列候选(等长数组 + 行对象两种形状)。"""
    matches = []
    for body in recorder.bodies:
        url = body.get("url") or ""
        if _url_is_excluded_series(url):
            continue  # 商品/营销类时间轴不作为整场成交金额来源(不收集)
        found = _scan_json_for_series(body["json"], max_matches=4)
        found.extend(_scan_row_objects(body["json"], max_matches=4))
        for f in found:
            f["url"] = url
            f["status"] = body.get("status")
        matches.extend(found)
    if not matches:
        return {"hit": False, "reason": f"捕获 {len(recorder.bodies)} 个 JSON 响应,未发现 时间+数值 等长数组/行对象 候选"}

    # 优先选 gmv/金额语义字段;否则选长度最大者(确定性排序)
    def sort_key(m):
        return (0 if (m.get("guess") or "").startswith("gmv") else 1, -(m.get("time_len") or 0))

    matches.sort(key=sort_key)
    best = matches[0]
    result = {
        "hit": True,
        "layer": "L1",
        "rows_observed": best.get("time_len"),
        "time_format": best.get("time_format"),
        "time_first": best.get("time_first"),
        "time_last": best.get("time_last"),
        "time_key": best.get("time_key"),
        "value_key": best.get("value_key"),
        "guess": best.get("guess"),
        "url": best.get("url"),
        "candidates_total": len(matches),
        "candidates": matches[:5],
        "note": "L1 命中的字段名/格式为页面响应自带的实际命名;gmv 语义为推测,需在线人工复核",
    }
    if best.get("series_key"):
        result["series_key"] = best["series_key"]
    if best.get("rows_sample"):
        result["rows_sample"] = best["rows_sample"]
    return result


# ---------------------------------------------------------------------------
# L3: 逐点悬停兜底脚手架
# ---------------------------------------------------------------------------
def _try_l3(page) -> dict:
    """L3 兜底(脚手架):逐点悬停读取 tooltip。

    该层强依赖页面布局(图表坐标、tooltip DOM 选择器、文本格式),在本机无人工
    在线标定前不可靠;因此脚手架只做“探测前置条件检查 + 返回需人工标定的结论”,
    绝不臆造读数。config.probe.try_l3=true 时才会被调用。
    """
    return {
        "hit": False,
        "layer": "L3",
        "reason": (
            "L3 为逐点悬停兜底脚手架:需要人工在线标定图表 canvas 与 tooltip "
            "选择器/文本格式后才能逐分钟读取;未标定前不产出数据(避免伪造读数)。"
        ),
        "note": "标定方法见 docs/probe-evidence-format.md 的 L3 章节",
    }


# ---------------------------------------------------------------------------
# 主探测流程
# ---------------------------------------------------------------------------
def _replay_url(cfg: dict, room_id: str) -> str:
    base = cfg.get("probe", {}).get("base_url", "https://eos.douyin.com/dp/liveScreen")
    return f"{base}?room_id={room_id}&tab=trend"


def _session_state_hint(page) -> str:
    """尽力判断场次状态(ended/live),仅依据页面可见文本;失败返回 unknown。"""
    try:
        title = page.title() or ""
        text_ok = True
    except Exception:
        return "unknown"
    try:
        body_text = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 2000)") or ""
    except Exception:
        body_text = ""
    combined = title + body_text
    if any(k in combined for k in ("直播已结束", "回放", "已结束", "场次结束")):
        return "ended"
    if any(k in combined for k in ("直播中", "正在直播")):
        return "live"
    return "unknown"


def _write_evidence(cfg: dict, room_id: str, payload: dict) -> pathlib.Path:
    out_rel = cfg.get("probe", {}).get("output_dir", "data/probe")
    out_dir = pathlib.Path(out_rel)
    if not out_dir.is_absolute():
        out_dir = cfgmod.PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"probe_{room_id}_{stamp}.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_path


def run_probe(cfg: dict, room_id: str, *, dry_run: bool = False, layer: str = "all") -> int:
    """执行探测;返回进程退出码(0=成功/已提供引导, 2=需要人工处理后重试)。"""
    if dry_run:
        return _probe_dry_run(cfg, room_id)

    url = _replay_url(cfg, room_id)
    print(f"[probe] 目标回放页: {url}")

    try:
        pw, context, page = auth.open_session(cfg, headful=True)
    except SystemExit:
        raise  # auth 已打印缺依赖引导
    except Exception as exc:
        print(f"[probe] 打开浏览器失败: {exc}", file=sys.stderr)
        print("[probe] 若为首次使用,请先: python main.py login", file=sys.stderr)
        return 2

    try:
        if not auth.ensure_logged_in(cfg, context, page):
            print("[probe] 无登录会话。人工引导:请先运行  python main.py login", file=sys.stderr)
            print("[probe] 在有头窗口完成扫码登录后,再运行  python main.py probe --room-id <id>", file=sys.stderr)
            return 2

        hints = cfg.get("probe", {}).get("network_url_hints") or _DEFAULT_URL_HINTS
        recorder = _ResponseRecorder(hints)
        page.on("response", recorder.on_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            print(f"[probe] 打开回放页失败(可能无权限/无网络): {exc}", file=sys.stderr)
            print("[probe] 请确认: 1) 已登录且账号对目标直播间有查看权限; 2) room_id 正确", file=sys.stderr)
            return 2

        load_wait = int(cfg.get("probe", {}).get("load_wait_seconds", _DEFAULT_LOAD_WAIT))
        scroll_wait = int(cfg.get("probe", {}).get("scroll_wait_seconds", _DEFAULT_SCROLL_WAIT))
        print(f"[probe] 等待图表渲染(最长约 {load_wait} 秒,期间被动捕获网络响应)…")
        # 分片等待,让事件回调有时间执行;末尾滚动一次触发懒加载
        for _ in range(max(1, load_wait // 5)):
            page.wait_for_timeout(5000)
        try:
            page.mouse.wheel(0, 800)
            page.wait_for_timeout(scroll_wait * 1000)
        except Exception:
            pass

        session_state = _session_state_hint(page)
        print(f"[probe] 场次状态(尽力判断): {session_state}")

        # ---- 分层命中 ----
        hit = None
        recorder.read_bodies()  # 判读阶段:读取响应主体(捕获阶段不读)
        layer_candidates = ["L0", "L1", "L3"] if layer == "all" else [layer]
        for lyr in layer_candidates:
            if lyr == "L0":
                res = _try_l0(page)
            elif lyr == "L1":
                res = _try_l1(recorder)
            else:
                if not bool(cfg.get("probe", {}).get("try_l3", False)):
                    print("[probe] L3 未启用(config.probe.try_l3=false),跳过。")
                    continue
                res = _try_l3(page)
            if res.get("hit"):
                hit = res
                print(f"[probe] 命中层级: {res['layer']}  (rows={res.get('rows_observed')}, 时间格式={res.get('time_format')})")
                break
            print(f"[probe] {lyr} 未命中: {res.get('reason', '')[:120]}")

        # ---- 组装证据 ----
        captured_at = _now_utc_iso()
        page_title = ""
        try:
            page_title = page.title() or ""
        except Exception:
            pass

        if hit is None:
            payload = {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "probe_meta": {
                    "room_id": room_id,
                    "url": url,
                    "captured_at_utc": captured_at,
                    "page_title": page_title,
                    "session_state_hint": session_state,
                },
                "layer_hit": "none",
                "field_definition": {
                    "time_field": None,
                    "time_format": None,
                    "gmv_min_field": None,
                    "amount_unit": "yuan(元)",
                    "note": "未命中层级:字段语义按既定方案预定义:time=页面数据自带时间;gmv_min=该分钟新增成交金额(0 分钟保留)。字段名待命中后回填。",
                },
                "session_bounds": {"observed_start": None, "observed_end": None,
                                   "note": "整场起止需命中层级后按时间轴首尾回填"},
                "rows_observed": 0,
                "row_sample": [],
                "sources": [
                    {"layer": "L1", "url": r["url"], "status": r.get("status"),
                     "note": "已捕获的 JSON 响应(未发现时间序列候选)"}
                    for r in recorder.records[:10]
                ],
                "assumptions": [
                    "时间使用页面数据自带时间(MM-DD HH:MM 或 YYYY-MM-DD HH:MM,以页面为准)",
                    "gmv_min = 该分钟内新增成交金额;金额为 0 的分钟应保留",
                    "整场起止时间 = 时间轴首/尾点",
                ],
                "online_recheck_required": [
                    "人工确认该场次为已结束场次且回放页趋势图可见",
                    "人工确认上述字段语义与页面显示一致(成交金额/订单/在线人数分开核对)",
                ],
            }
            out = _write_evidence(cfg, room_id, payload)
            print(f"[probe] L0/L1/L3 均未命中,已写出过程证据(供人工排查): {out}")
            print("[probe] 后续处理建议: 1) 人工在浏览器打开回放页确认趋势图存在;", file=sys.stderr)
            print("[probe]   2) 若存在但本工具未命中,请把 data/probe/*.json 与页面截图交给开发者调参。", file=sys.stderr)
            return 2

        # 命中层级:回填字段定义与行样例
        field_definition = {
            "time_field": hit.get("time_key") or "xAxis.data(页面自带时间,字段名以命中来源为准)",
            "time_format": hit.get("time_format") or "MM-DD HH:MM(以页面为准)",
            "gmv_min_field": hit.get("value_key") or "series.data/响应中该分钟金额字段",
            "amount_unit": "yuan(元)",
            "note": "字段语义 = 该分钟新增成交金额;gmv 语义为结构推测,需在线人工复核;金额为 0 的分钟保留",
        }
        if hit.get("guess"):
            field_definition["semantic_guess"] = hit["guess"]
            field_definition["note"] += f"; 当前命中值字段语义推测: {hit['guess']}"
        if hit.get("series_key"):
            field_definition["series_key"] = hit["series_key"]
            field_definition["note"] += f"; 页面序列 key: {hit['series_key']}"
        payload = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "probe_meta": {
                "room_id": room_id,
                "url": url,
                "captured_at_utc": captured_at,
                "page_title": page_title,
                "session_state_hint": session_state,
            },
            "layer_hit": hit["layer"],
            "field_definition": field_definition,
            "session_bounds": {
                "observed_start": hit.get("time_first"),
                "observed_end": hit.get("time_last"),
                "note": "整场起止按时间轴首尾点回填;待在线复核与直播起止一致性",
            },
            "rows_observed": hit.get("rows_observed") or 0,
            "row_sample": hit.get("rows_sample") or (
                [{"time": hit.get("time_first"), "gmv_min": hit.get("value_first")}]
                if hit.get("time_first") is not None else []
            ),
            "sources": [
                {"layer": hit["layer"], "url": hit.get("url"),
                 "note": (hit.get("note", "") + (f"; series_key={hit['series_key']}" if hit.get("series_key") else "")).strip()}
            ],
            "assumptions": [
                "时间使用页面数据自带时间(格式见 field_definition.time_format)",
                "gmv_min = 该分钟内新增成交金额;金额为 0 的分钟应保留",
                "整场起止时间 = 时间轴首/尾点",
            ],
            "online_recheck_required": [
                "在线复核命中字段的语义(是否确为该分钟新增成交金额)",
                "在线核对整场时间轴是否连续、起止是否覆盖整场直播",
                "核对 M2 导出所用行与页面累计成交金额的一致性(校验项)",
            ],
        }
        out = _write_evidence(cfg, room_id, payload)
        print(f"[probe] 证据文件已写出: {out}")
        print("[probe] 命中层级与字段样例已记录,可直接作为 M2 导出实现的输入依据。")
        return 0
    finally:
        try:
            context.close()
        finally:
            pw.stop()


def _probe_dry_run(cfg: dict, room_id: str) -> int:
    """--dry-run: 不打开浏览器、不采集任何数据,打印探测计划与人工引导。"""
    sample_url = _replay_url(cfg, room_id or "123456789")
    print("[probe] DRY-RUN:不会打开浏览器、不采集任何页面数据。")
    print(f"[probe] 探测目标(示例 URL): {sample_url}")
    print("[probe] 分层探测计划:")
    print("  L0  读取页面图表数据数组(ECharts xAxis/series),零网络依赖")
    print("  L1  被动捕获页面加载时返回的时间序列 JSON 响应(只读不改写)")
    print("  L3  逐点悬停读取 tooltip 的兜底脚手架(config.probe.try_l3=true 时启用)")
    print("[probe] 证据输出: data/probe/probe_<room_id>_<时间戳>.json(含时间格式/分钟金额字段样例/整场起止)")
    print()
    print("[probe] 上线(真机)步骤:")
    print("  1) 安装依赖并下载浏览器内核:")
    print("       pip install -r requirements.txt")
    print("       python -m playwright install chromium")
    print("  2) 首次登录(有头窗口,人工扫码/输入验证码;凭据仅存本地 profile):")
    print("       python main.py login")
    print("  3) 执行探测(需对目标直播间有查看权限):")
    print("       python main.py probe --room-id <room_id>")
    print()
    print("[probe] 范围边界: 只读取你账号已有权限的数据;不做登录绕过/签名逆向;")
    print("[probe]           不收集商品-营销-违规事件时间轴;不把 profile/凭据提交进代码或输出物。")
    return 0


# ---------------------------------------------------------------------------
# CLI(可直接执行: python probe.py --dry-run / --room-id ...)
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe",
        description="分层只读探测抖音巨量百应直播回放页的分钟成交金额数据来源(M1)。",
    )
    parser.add_argument("--room-id", default=None, help="直播间 room_id(探测目标)")
    parser.add_argument("--config", default=None, help="配置文件路径(默认读项目根 config.yaml)")
    parser.add_argument("--dry-run", action="store_true",
                        help="不打开浏览器/不采集数据,只打印探测计划与人工引导")
    parser.add_argument("--layer", choices=["all", "L0", "L1", "L3"], default="all",
                        help="限定尝试层级(默认 all:L0→L1→L3)")
    return parser


def main(argv=None) -> int:
    cfgmod.setup_utf8_io()
    args = build_parser().parse_args(argv)
    cfg = cfgmod.load_config(args.config)
    if args.dry_run:
        return _probe_dry_run(cfg, args.room_id)
    if not args.room_id:
        print("[probe] 缺少 --room-id(或使用 --dry-run 查看计划)。", file=sys.stderr)
        return 2
    return run_probe(cfg, args.room_id, dry_run=False, layer=args.layer)


if __name__ == "__main__":
    sys.exit(main())
