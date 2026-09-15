# -*- coding: utf-8 -*-
"""gpm_probe.py — GPM/观看次数 数据源定位探测(只读真机,L1 被动捕获 + UI 芯片点击)。

背景:douyin-live-gmv-export 现有 M2 只导出“每分钟新增成交金额”。本探测为
“按小时千次观看成交金额(GPM)”功能定位观看次数数据来源与字段语义,并输出
docs/GPM-观看次数口径结论.md 所需的证据(命中层级/字段样例,脱敏)。

只读边界(与 probe.py / auth.py 一致):
- 复用持久化登录会话(有头窗口,人工登录在 auth.perform_login 完成);
- 被动捕获浏览器自己发起的 JSON 响应;不改写请求、不伪造响应;
- 页面指标切换只点击“流量/观看相关”UI 文案(成交金额/成交订单数/在线人数/
  进入人数/离开人数/点赞次数 等);绝不点击 商品/营销/违规/视频/广告 类带;
- URL 命中 product/marketing/ads/lamp/punish 的响应主体一律不落盘(仅记 URL 名);
- 不收集商品-营销-违规事件时间轴;证据样例做截断(前后各若干行),不整场转储。

主要输出:
- data/probe/gpm_probe_<room_id>_<时间戳>.json   证据(修剪后,含命中与样例)
- data/probe/gpm_probe_<room_id>_<时间戳>_watch_raw.json
  (可选 --raw-full:仅观看系分钟序列 + 成交金额分钟序列的本地原始备份,
   供后续按小时桶聚合与页面 GPM 对拍复核;不入库)

用法:
  python gpm_probe.py --room-id <id> --dry-run     # 不打开浏览器(计划/引导)
  python gpm_probe.py --room-id <id>               # 真机探测(需已登录)
  python gpm_probe.py --room-id <id> --raw-full    # 附加原始观看序列备份
  python gpm_probe.py --live --room-id <id> --dry-run   # 直播中采集计划(不打开浏览器)
  python gpm_probe.py --live --room-id <id>             # 直播中实时大屏周期扫描(需房间正在直播)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
import time as _time
from typing import Any, Dict, List, Optional

import config as cfgmod

SCHEMA_VERSION = "gpm-1.1"  # 1.1(t6):新增图表指标选择器展开探测 + gpm/看播量分钟序列闭合性
LIVE_SCHEMA_VERSION = "gpm-live-1.0"  # t9:直播中实时大屏周期扫描(证据段与 gpm_probe2 对齐)

# 直播中采集(t9)默认参数:周期扫描波次数、波间隔秒、总时长上限(分钟)
LIVE_DEFAULT_WAVES = 6
LIVE_DEFAULT_INTERVAL_SEC = 20
LIVE_DEFAULT_MAX_MINUTES = 15
# 每次直播中运行的 UI 白名单点击总上限(防止无限点击;纯观察触发)
LIVE_MAX_CLICKS_TOTAL = 26
LIVE_MAX_CLICKS_PER_WAVE = 6

# 响应主体一律不落盘的端点标记(商品/营销/广告/违规/惩罚等;只记 URL 名)
_EXCLUDED_BODY_URL_MARKS = (
    "product_trend", "product_ai_tip", "follow_product", "marketing_data",
    "local_ads_show_info", "punish_info", "roi2", "lamp", "advertising",
    "sales_tool", "product_explanation",
)
# UI 上绝不点击的文案(商品/营销/违规/视频/广告带)
_EXCLUDED_CLICK_HINTS = ("商品", "营销", "违规", "视频", "广告", "投流", "充值", "诊断")
# 观察型“观看次数”系列候选关键词(结构启发,仅供对拍用)
_WATCH_KEY_HINTS = ("watch", "view", "uv", "pv", "enter", "leave", "online",
                    "看播", "观看", "进入", "离开", "在线", "曝光", "人次")
# 交易侧(与 gmv/订单对齐,供 GPM 分子分母)
_GPM_COMPONENT_KEY_HINTS = ("gmv", "pay", "gpm", "成交", "订单")


def _now_utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


# ---------------------------------------------------------------------------
# 小工具(纯函数,离线自测友好)
# ---------------------------------------------------------------------------
def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _chart_keys(node: Any, out: Optional[List] = None) -> List[Dict[str, Any]]:
    """递归找出所有 {key, chart:[...]} 数据集形状,返回 [{key, len, row0}]。"""
    if out is None:
        out = []
    if isinstance(node, dict):
        if isinstance(node.get("chart"), list) and isinstance(node.get("key"), str):
            rows = node["chart"]
            out.append({
                "key": node["key"],
                "len": len(rows),
                "row0": rows[0] if rows and isinstance(rows[0], dict) else None,
            })
        for v in node.values():
            _chart_keys(v, out)
    elif isinstance(node, list):
        for v in node:
            _chart_keys(v, out)
    return out


def _row_stats(rows: List[Dict[str, Any]], x_key: str = "x", y_key: str = "y") -> Dict[str, Any]:
    """对行对象序列计算时间/数值统计与样例(前 3 后 3),样例即页面原始值(脱敏截断)。"""
    if not rows:
        return {"len": 0}
    xs = [str(r.get(x_key)) for r in rows]
    vals = [r.get(y_key) for r in rows]
    num = [v for v in vals if _numeric(v)]
    nonzero = [(xs[i], vals[i]) for i in range(len(vals))
               if _numeric(vals[i]) and vals[i] != 0]
    stat: Dict[str, Any] = {
        "len": len(rows),
        "time_key": x_key,
        "value_key": y_key,
        "time_format": _time_format_hint(xs[0]),
        "time_first": xs[0],
        "time_last": xs[-1],
        "step_seconds": _median_step_seconds(rows, x_key=x_key),
        "value_sum": round(sum(num), 4) if num else None,
        "value_min": min(num) if num else None,
        "value_max": max(num) if num else None,
        "nonzero_minutes": len(nonzero),
        "sample_head": [{"time": xs[i], "y": vals[i]} for i in range(min(3, len(xs)))],
        "sample_tail": [{"time": xs[i], "y": vals[i]} for i in range(max(0, len(xs) - 3), len(xs))],
    }
    if nonzero:
        stat["nonzero_head"] = [{"time": t, "y": v} for t, v in nonzero[:3]]
        stat["nonzero_tail"] = [{"time": t, "y": v} for t, v in nonzero[-3:]]
    return stat


def _median_step_seconds(rows, x_key: str = "x") -> Optional[int]:
    import probe as _probe
    steps = []
    prev = None
    for r in rows[:120]:
        ts = r.get("time_stamp")
        cur = None
        if isinstance(ts, str) and ts.strip().isdigit():
            cur = int(ts)
        elif isinstance(ts, (int, float)):
            cur = int(ts)
        if cur is not None:
            if prev is not None:
                steps.append(cur - prev)
            prev = cur
    if not steps:
        return None
    steps.sort()
    return steps[len(steps) // 2]


def _time_format_hint(sample: str) -> str:
    s = str(sample or "").strip()
    if len(s) >= 16 and s[4] == "-":
        return "YYYY-MM-DD HH:MM[:SS]"
    if len(s) >= 12 and s[2] == "-":
        return "MM-DD HH:MM"
    if s.replace(".", "", 1).isdigit():
        return "epoch"
    return "unknown"


def _guess_key_semantics(key: str) -> str:
    k = str(key).lower()
    if any(t in k for t in ("gmv", "amount", "成交", "pay", "gpm", "订单", "order")):
        return "gmv/订单/交易类(推测)"
    if any(t in k for t in ("watch", "view", "看播", "观看")):
        return "观看/看播类(推测)"
    if any(t in k for t in ("enter", "进入")):
        return "进入类(推测)"
    if any(t in k for t in ("leave", "离开")):
        return "离开类(推测)"
    if any(t in k for t in ("online", "在线")):
        return "在线类(推测)"
    if any(t in k for t in ("like", "点赞")):
        return "点赞类(推测)"
    if any(t in k for t in ("show", "曝光", "pv")):
        return "曝光类(推测)"
    return "未知(推测:结构识别,语义待对拍)"


def _matches_excluded_body(url: str) -> bool:
    u = (url or "").lower()
    return any(m in u for m in _EXCLUDED_BODY_URL_MARKS)


# ---------------------------------------------------------------------------
# 网络捕获器(被动;与 probe._ResponseRecorder 同源,扩展至所有 eos JSON)
# ---------------------------------------------------------------------------
class _Capture:
    def __init__(self):
        self.records: List[Dict[str, Any]] = []   # 元信息(含被排除 URL 名)
        self.bodies: List[Dict[str, Any]] = []    # 只含允许落盘的主体
        self.excluded_urls: List[str] = []
        self._read_idx = 0

    def on_response(self, response) -> None:
        try:
            url = response.url
            ct = (response.headers.get("content-type") or "") if hasattr(response, "headers") else ""
        except Exception:
            return
        if "json" not in ct.lower():
            return
        if "eos.douyin.com" not in url:
            return
        if _matches_excluded_body(url):
            self.excluded_urls.append(url)  # 仅记录 URL 名(不存主体)
            return
        self.records.append({"url": url, "_resp": response})

    def read_bodies(self) -> None:
        """只读新增记录(增量),避免重复入列。"""
        while self._read_idx < len(self.records):
            rec = self.records[self._read_idx]
            self._read_idx += 1
            try:
                data = rec["_resp"].json()
                if isinstance(data, (dict, list)):
                    self.bodies.append({"url": rec["url"], "json": data})
            except Exception:
                pass


# ---------------------------------------------------------------------------
# UI 交互(只点击允许的指标文案)
# ---------------------------------------------------------------------------
_CLICK_JS = r"""(label) => {
  const els = Array.prototype.slice.call(document.querySelectorAll(
    'div,span,li,a,button,p,h1,h2,h3,label'));
  for (const el of els) {
    if (el.childElementCount > 0) continue;
    const own = (el.innerText || el.textContent || '').trim();
    if (own === label) {
      const r = el.getBoundingClientRect();
      if (r.width > 2 && r.height > 2) { el.click(); return true; }
    }
  }
  return false;
}"""

# 概览“趋势分析”图表的指标切换文案(观测到可触发 room_minute_indicator 刷新)
_ALLOWED_TREND_CHIPS = [
    "成交金额", "成交订单数", "在线人数", "进入人数", "离开人数", "点赞次数",
]

# 候选附加标签(存在才点;命中即观察 RMI 新数据组;范围外文案一律跳过)
_WATCH_HINT_LABELS = ["直播间看播量", "看播量", "看播次数", "直播间曝光量", "曝光量",
                      "评论次数", "进入次数", "千次观看成交金额", "GPM"]

# t6:内容白名单关键词(仅用于定位图表指标选择器/选项;banned 文案另由 _tab_allowed 排除)
_METRIC_CONTENT_KEYWORDS = ("千次", "GPM", "看播", "观看", "进入", "离开", "在线",
                            "点赞", "评论", "曝光", "成交金额", "成交订单", "互动")
# 下拉/展开控件常见尾部装饰字符(文本去掉该字符后回退为指标名)
_SELECTOR_AFFORDANCE = ("▾", "⌄", "▼", "˅", "›", "…")


def _click_leaf(page, label: str) -> bool:
    try:
        return bool(page.evaluate(_CLICK_JS, label))
    except Exception:
        return False


def _leaf_texts(page, cap: int = 300) -> List[str]:
    js = """(cap) => {
      const out = [];
      const all = Array.prototype.slice.call(document.querySelectorAll(
        'div,span,li,a,button,p,h1,h2,h3,label'));
      const seen = new Set();
      for (const el of all) {
        if (el.childElementCount > 0) continue;
        const t = (el.innerText || el.textContent || '').trim();
        if (!t || t.length > 14 || seen.has(t)) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        seen.add(t); out.push(t);
        if (out.length >= cap) break;
      }
      return out;
    }"""
    try:
        return page.evaluate(js, cap)
    except Exception:
        return []


def _tab_allowed(label: str) -> bool:
    return not any(h in label for h in _EXCLUDED_CLICK_HINTS)


_METRIC_TARGET_SUBSTRINGS = ("千次观看成交金额", "千次观看", "直播间看播量",
                             "看播量", "看播次数", "观看次数", "GPM")


def _find_metric_dom_entries(page) -> List[Dict[str, Any]]:
    """只读扫描 DOM:找出文本中含 千次观看成交金额/看播量/观看次数 等目标词的元素。

    记录其 tag、裁剪文本、命中的目标词、可见性与是否横向可滚动(可滚动行说明
    存在折叠/溢出未展开的选项)。纯观察,不点击;结果由调用方决定是否点击。
    """
    js = """(subs) => {
      const out = [];
      const els = Array.prototype.slice.call(document.querySelectorAll(
        'div,span,li,a,button,p,h1,h2,h3,label,section,ul'));
      for (const el of els) {
        if (el.childElementCount > 60) continue;
        const txt = (el.innerText || el.textContent || '').replace(/[\\s\\u00a0]+/g, ' ').trim();
        if (!txt || txt.length > 80) continue;
        const hit = subs.filter((s) => txt.indexOf(s) >= 0);
        if (!hit.length) continue;
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        out.push({
          text: txt.slice(0, 80), subs: hit.slice(0, 3), tag: el.tagName,
          visible: r.width > 2 && r.height > 2 && cs.visibility !== 'hidden' && cs.display !== 'none',
          offscreen_x: r.right < 0 || r.x > (window.innerWidth + 4),
          scrollable_row: el.scrollWidth > el.clientWidth + 8,
          x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)
        });
        if (out.length >= 80) break;
      }
      return out;
    }"""
    try:
        return page.evaluate(js, list(_METRIC_TARGET_SUBSTRINGS))
    except Exception:
        return []


def _selector_candidate_texts(page, known_names) -> List[str]:
    """返回可能是“图表指标选择器(下拉触发/选项)”的可见叶子文本。

    规则:叶子文本 ≤18 字符,且文本本身或去掉尾部装饰字符(▾/⌄/▼…)后
    命中 known_names(已知指标名集合)。不含子元素的叶子才计入(避免容器)。
    纯观察,不改写页面;结果由调用方再过 _tab_allowed。
    """
    js = """(known, afford) => {
      const out = [];
      const seen = new Set();
      const els = Array.prototype.slice.call(document.querySelectorAll(
        'div,span,li,a,button,p,h1,h2,h3,label'));
      const norm = (t) => t.replace(/[\\s\\u00a0]+/g, ' ').trim();
      for (const el of els) {
        if (el.childElementCount > 0) continue;
        const t = norm(el.innerText || el.textContent || '');
        if (!t || t.length > 18 || seen.has(t)) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        let core = t;
        for (const a of afford) {
          if (core.endsWith(a)) { core = core.slice(0, -1).trim(); break; }
        }
        if (known.indexOf(core) >= 0) { seen.add(t); out.push(t); }
      }
      return out;
    }"""
    try:
        return page.evaluate(js, list(known_names), list(_SELECTOR_AFFORDANCE))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 证据组装
# ---------------------------------------------------------------------------
def _find_json_bodies(cap: "_Capture", url_hint: str) -> List[Dict[str, Any]]:
    return [b for b in cap.bodies if url_hint in (b.get("url") or "")]


def _card_lookup(key_index_json, key: str) -> Optional[Dict[str, Any]]:
    if not isinstance(key_index_json, dict):
        return None
    data = key_index_json.get("data")
    if not isinstance(data, dict):
        return None
    card = data.get(key)
    if isinstance(card, dict):
        return {
            "key": card.get("key") or key,
            "name": card.get("name"),
            "unit": card.get("unit"),
            "value": card.get("value"),
        }
    return None


def _meta_title_lookup(meta_list, data_key: str) -> Optional[str]:
    if not isinstance(meta_list, list):
        return None
    for m in meta_list:
        if isinstance(m, dict) and (m.get("dataKey") == data_key or m.get("dataIndex") == data_key):
            return m.get("title")
    return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _replay_url(cfg: dict, room_id: str) -> str:
    import probe as _probe
    return _probe._replay_url(cfg, room_id)


def run_gpm_probe(cfg: dict, room_id: str, *, raw_full: bool = False,
                  tag: str = "gpm_probe") -> int:
    import auth
    import probe as _probe  # noqa: F401(复用时间/排除常量,保证与主链路同源)

    url = _replay_url(cfg, room_id)
    print(f"[gpm_probe] 目标回放页: {url}")

    try:
        pw, context, page = auth.open_session(cfg, headful=True)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[gpm_probe] 打开浏览器失败: {exc}", file=sys.stderr)
        print("[gpm_probe] 人工引导: 先执行 python main.py login(有头窗口扫码),再重试。", file=sys.stderr)
        return 2

    evidence: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "probe_meta": {
            "room_id": str(room_id),
            "url": url,
            "captured_at_utc": _now_utc_iso(),
            "page_title": "",
            "session_state_hint": "unknown",
        },
        "mode": "L1 passive capture + UI chip clicks(只读;商品/营销/违规带不采集)",
        "endpoints_seen": [],
        "room_minute_indicator": {"default_groups": [], "watch_minute_candidates": []},
        "key_index_relevant_cards": {},
        "gpm_metric": {},
        "flow_tab": {},
        "hour_level_observations": [],
        "watch_series_checksum": [],
        "conclusions": [],
        "assumptions": [],
        "online_recheck_required": [],
        "boundaries": [],
    }

    try:
        if not auth.ensure_logged_in(cfg, context, page):
            print("[gpm_probe] 无登录会话。人工引导: python main.py login 完成扫码后重试。", file=sys.stderr)
            return 2
        cap = _Capture()
        page.on("response", cap.on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            print(f"[gpm_probe] 打开回放页失败(无权限/无网络?): {exc}", file=sys.stderr)
            return 2
        load_wait = int(cfg.get("probe", {}).get("load_wait_seconds", 35))
        for _ in range(max(1, load_wait // 5)):
            page.wait_for_timeout(5000)
        try:
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(2000)
        except Exception:
            pass
        cap.read_bodies()

        evidence["probe_meta"]["page_title"] = (page.title() or "")[:80]
        evidence["probe_meta"]["session_state_hint"] = _session_hint(page)

        # --- 逐 url 归属 ---
        for b in cap.bodies:
            u = b.get("url") or ""
            role = _role_of_url(u)
            evidence["endpoints_seen"].append({"url": u, "role": role})
        evidence["boundaries"].append(
            f"主体未落盘的端点(仅 URL 名): {len(cap.excluded_urls)} 个"
        )
        # 去重后的角色清单
        roles = sorted({r["role"] for r in evidence["endpoints_seen"]})

        # --- room_minute_indicator(默认组 + meta 目录) ---
        rmi_bodies = _find_json_bodies(cap, "room_minute_indicator")
        rmi_url = ""
        meta_catalog = []
        default_groups = []
        watch_minute: Dict[str, Dict[str, Any]] = {}
        _seen_default: set = set()
        for b in rmi_bodies:
            j = b["json"]
            rmi_url = b["url"]
            groups = _chart_keys(j)
            for g in groups:
                rows = _rows_of_group(j, g["key"])
                title = _title_of_group(j, g["key"])
                entry = {
                    "key": g["key"], "title": title,
                    "guess": _guess_key_semantics(g["key"]),
                    "stats": _row_stats(rows) if rows is not None else {"len": 0},
                }
                if g["key"] not in _seen_default:
                    _seen_default.add(g["key"])
                    default_groups.append(entry)
                if any(h in str(g["key"]).lower() for h in _WATCH_KEY_HINTS):
                    watch_minute[g["key"]] = entry
            for m in (j.get("meta") or []):
                if isinstance(m, dict) and m.get("dataKey"):
                    if m["dataKey"] not in {e["dataKey"] for e in meta_catalog}:
                        meta_catalog.append({
                            "dataKey": m.get("dataKey"), "title": m.get("title"),
                            "type": m.get("type"),
                        })
        evidence["room_minute_indicator"]["url"] = rmi_url
        evidence["room_minute_indicator"]["default_groups"] = default_groups
        evidence["room_minute_indicator"]["meta_catalog"] = meta_catalog
        if default_groups:
            evidence["probe_meta"]["session_state_hint"] = evidence["probe_meta"]["session_state_hint"]
            first = default_groups[0]["stats"]
            evidence["room_minute_indicator"]["time_axis"] = {
                "time_first": first.get("time_first"),
                "time_last": first.get("time_last"),
                "len": first.get("len"),
                "time_format": first.get("time_format"),
                "step_seconds": first.get("step_seconds"),
            }

        # --- 点击流量/观看相关 UI 指标,观察 RMI 新数据组 ---
        # (1) 回到概览后点允许的指标文案(每次点击后只观察“新增”响应主体)
        def observe_after_click(label: str) -> Dict[str, Any]:
            base = len(cap.bodies)
            ok = _click_leaf(page, label)
            page.wait_for_timeout(3500)
            cap.read_bodies()
            new_keys = []
            for b in cap.bodies[base:]:
                if "room_minute_indicator" not in (b.get("url") or ""):
                    continue
                for g in _chart_keys(b["json"]):
                    rows = _rows_of_group(b["json"], g["key"])
                    new_keys.append({
                        "key": g["key"],
                        "title": _title_of_group(b["json"], g["key"]),
                        "guess": _guess_key_semantics(g["key"]),
                        "stats": _row_stats(rows) if rows is not None else {"len": 0},
                    })
            # 去重(同一次点击可能多次请求同一对 key)
            seen_k: set = set()
            dedup = []
            for nk in new_keys:
                if nk["key"] not in seen_k:
                    seen_k.add(nk["key"])
                    dedup.append(nk)
            return {"label": label, "clicked": ok, "new_groups": dedup}

        chip_log = []
        clicked_any_watch = False
        for label in _ALLOWED_TREND_CHIPS:
            res = observe_after_click(label)
            chip_log.append(res)
            for ng in res["new_groups"]:
                if ng["key"] not in watch_minute:
                    watch_minute[ng["key"]] = ng
                    if any(h in str(ng["key"]).lower() for h in _WATCH_KEY_HINTS):
                        clicked_any_watch = True
        # (2) 命中带“看播/曝光/GPM”字样且不在禁止文案清单的叶子标签(存在才点;上限 8 个)
        labels = _leaf_texts(page, 300)
        attempts = 0
        for label in labels:
            if attempts >= 8:
                break
            if any(h in label for h in ("看播", "曝光", "观看", "进入次数", "GPM", "千次")):
                if not _tab_allowed(label):
                    continue
                attempts += 1
                res = observe_after_click(label)
                chip_log.append(res)
                for ng in res["new_groups"]:
                    if ng["key"] not in watch_minute:
                        watch_minute[ng["key"]] = ng

        # (3) 流量 tab:观察 flow_index / flow_entrance_trend
        flow_observed = {}
        if _click_leaf(page, "流量"):
            page.wait_for_timeout(6000)
            try:
                page.mouse.wheel(0, 300)
            except Exception:
                pass
            page.wait_for_timeout(2000)
            cap.read_bodies()
            for b in _find_json_bodies(cap, "flow_index"):
                j = b["json"]
                data = j.get("data")
                if isinstance(data, dict) and isinstance(data.get("data"), dict):
                    for k, card in data["data"].items():
                        if isinstance(card, dict):
                            flow_observed[k] = {
                                "name": card.get("name"),
                                "tooltip": card.get("tooltip"),
                                "value": card.get("value"),
                                "type": card.get("type"),
                            }
            ent_trend_bodies = _find_json_bodies(cap, "flow_entrance_trend")
            ent_shape = []
            for b in ent_trend_bodies[:1]:
                for g in _chart_keys(b["json"])[:3]:
                    rows = _rows_of_group(b["json"], g["key"])
                    ent_shape.append({
                        "key": g["key"],
                        "guess": _guess_key_semantics(g["key"]),
                        "stats": _row_stats(rows) if rows is not None else {"len": 0},
                    })
            evidence["flow_tab"] = {
                "kpis": flow_observed,
                "entrance_trend_group_sample": ent_shape,
                "note": "flow_entrance_trend 返回各流量渠道曝光序列(键名含 hour 字样,行数仍为分钟级,"
                        "已按实际行步长判定粒度);非观看次数本身,仅作对照。",
            }
        evidence["chip_log"] = chip_log

        # --- (2.6) t6:展开图表指标选择器(下拉/切换),找 gpm/直播间看播量(WatchCntTrend) 分钟序列 ---
        drop_series: Dict[str, Dict[str, Any]] = {}

        def _collect_drop_series(bodies) -> None:
            for _b in bodies:
                if "room_minute_indicator" not in (_b.get("url") or ""):
                    continue
                for _g in _chart_keys(_b["json"]):
                    if _g["key"] in drop_series:
                        continue
                    _rows = _rows_of_group(_b["json"], _g["key"])
                    drop_series[_g["key"]] = {
                        "key": _g["key"],
                        "title": _title_of_group(_b["json"], _g["key"]),
                        "guess": _guess_key_semantics(_g["key"]),
                        "stats": _row_stats(_rows) if _rows is not None else {"len": 0},
                    }

        _collect_drop_series(cap.bodies)
        known_titles = set(_ALLOWED_TREND_CHIPS) | set(_WATCH_HINT_LABELS)
        known_titles |= {str(e.get("title") or "").strip()
                         for e in default_groups if isinstance(e.get("title"), str)}
        known_titles |= {str((e.get("title") or "")).strip()
                         for e in meta_catalog if isinstance(e.get("title"), str)}
        known_titles = {t for t in known_titles
                        if t and len(t) <= 18 and _tab_allowed(t)}
        clicked_dd: Dict[str, int] = {str(r.get("label")): 1 for r in chip_log}
        clicked_dd.setdefault("流量", 1)
        clicked_dd.setdefault("概览", 0)
        dd_waves: List[Dict[str, Any]] = []
        dd_total_clicks = 0
        dd_key_log: set = set()
        # (a) 先回到概览,再做指标选择器扫描(避免把流量 tab 的 KPI 占比当图表选项)
        if _click_leaf(page, "概览"):
            clicked_dd["概览"] = clicked_dd.get("概览", 0) + 1
            page.wait_for_timeout(2600)
            cap.read_bodies()
        # (b) DOM 级扫描:目标词(千次观看成交金额/看播量/观看次数/GPM)是否存在、可见性
        dom_entries = _find_metric_dom_entries(page)
        visible_short = [e for e in dom_entries
                         if e.get("visible") and 2 <= len(e.get("text", "")) <= 18
                         and _tab_allowed(e["text"])]
        for e in dom_entries:  # 记录脱敏定位信息(供文档人工复核)
            for kk in ("x", "y", "w", "h"):
                e[kk] = int(e.get(kk) or 0)
            e["text"] = e["text"][:40]
        # (c) 定向点击可见短文本目标(千次/看播/观看 优先;仅白名单内,每个最多点 1 次)
        target_clicked: list = []
        for e in visible_short:
            label = e["text"]
            if dd_total_clicks >= 26 or clicked_dd.get(label, 0) >= 1:
                continue
            if not any(h in label for h in ("千次", "看播", "GPM", "观看次数")):
                continue
            clicked_dd[label] = clicked_dd.get(label, 0) + 1
            base = len(cap.bodies)
            ok = _click_leaf(page, label)
            page.wait_for_timeout(3000)
            cap.read_bodies()
            added = set(drop_series)
            _collect_drop_series(cap.bodies[base:])
            added = set(drop_series) - added
            if ok:
                dd_total_clicks += 1
                target_clicked.append({"label": label, "ok": True,
                                       "new_group_keys": sorted(added)})
                if added:
                    dd_key_log |= added
        dd_waves.append({"wave": "target", "clicks": target_clicked,
                         "dom_entries_found": len(dom_entries),
                         "new_group_keys": sorted(dd_key_log)})
        # (d) 选择器候选波次:已知指标名可重点(≤2 次,尝试打开下拉),新出现选项自动跟进
        for _wave in range(2, 6):
            cands = [t for t in _selector_candidate_texts(page, known_titles)
                     if _tab_allowed(t) and clicked_dd.get(t, 0) < 2]
            if not cands:
                for t in _leaf_texts(page, 400):
                    if len(t) > 18 or not _tab_allowed(t) or clicked_dd.get(t, 0) >= 2:
                        continue
                    if any(k in t for k in _METRIC_CONTENT_KEYWORDS):
                        cands.append(t)
            cands = cands[:7]
            if not cands or dd_total_clicks >= 26:
                break
            wave_log: Dict[str, Any] = {
                "wave": _wave, "candidates": cands, "clicked": [], "new_group_keys": [],
            }
            for label in cands:
                if dd_total_clicks >= 26:
                    break
                clicked_dd[label] = clicked_dd.get(label, 0) + 1
                base = len(cap.bodies)
                ok = _click_leaf(page, label)
                page.wait_for_timeout(2600)
                cap.read_bodies()
                added = set(drop_series)
                _collect_drop_series(cap.bodies[base:])
                added = set(drop_series) - added
                if ok:
                    wave_log["clicked"].append(label)
                    dd_total_clicks += 1
                if added:
                    wave_log["new_group_keys"].extend(sorted(added))
                    dd_key_log |= added
            dd_waves.append(wave_log)
            if not wave_log["clicked"]:
                break
        evidence["indicator_dropdown_probe"] = {
            "waves": dd_waves,
            "dom_metric_entries_seen": dom_entries,
            "series_keys_total": sorted(drop_series),
            "target_hits": {k: drop_series[k] for k in sorted(drop_series)
                            if "gpm" in k.lower()
                            or "watchcnttrend" in k.lower()
                            or any(h in k for h in ("看播", "WatchCnt"))},
            "note": "只读展开指标选择器/点击白名单内指标选项,触发页面自身 room_minute_indicator 请求;"
                    "商品/营销/违规/广告文案一律不点(denylist)。",
        }

        # --- key_index:看播/GPM/成交 相关卡片 ---
        ki_bodies = _find_json_bodies(cap, "key_index")
        ki_json = ki_bodies[0]["json"] if ki_bodies else None
        relevant_keys = ["PayGmv", "GPM", "ServerWatchCntTd", "LiveServerWatchUcnt",
                         "PayOrderCnt", "PayUvAll", "LiveCtr", "LiveCvr", "AcuTotalTd",
                         "PcuTotalTd", "ClientLiveShowCntTd", "CurrentUserCnt"]
        cards = {}
        for k in relevant_keys:
            card = _card_lookup(ki_json, k)
            if card:
                meta_title = _meta_title_lookup(ki_json.get("meta") if isinstance(ki_json, dict) else None, k)
                card["meta_title"] = meta_title
                cards[k] = card
        evidence["key_index_relevant_cards"] = cards

        # --- GPM 对拍 ---
        gpm_check = _cross_check_gpm(cards)
        evidence["gpm_metric"] = {
            "page_card": cards.get("GPM"),
            "cross_check": gpm_check,
            "note": "页面 GPM 卡(千次观看成交金额)= PayGmv / 看播次数(ServerWatchCntTd) * 1000 为候选口径;"
                    "是否与页面同口径以数值对拍为准(见 cross_check),口径未定时不下结论。",
        }

        # --- t6 闭合性:gpm/看播量 分钟序列 vs 会话累计(仅记录实际观测,不臆测) ---
        swcnt_value = cards.get("ServerWatchCntTd", {}).get("value")
        gmv_sum_value = None
        for _e in default_groups:
            if _e["key"] == "pay_order_gmv_minute_trend":
                gmv_sum_value = (_e.get("stats") or {}).get("value_sum")
        if gmv_sum_value is None:
            for _e in drop_series.values():
                if _e["key"] == "pay_order_gmv_minute_trend":
                    gmv_sum_value = (_e.get("stats") or {}).get("value_sum")
        closure_results: List[Dict[str, Any]] = []
        t6_watch_keys = [k for k in sorted(drop_series)
                         if "watchcnttrend" in k.lower()
                         or any(h in k for h in ("WatchCnt", "看播"))]
        t6_gpm_keys = [k for k in sorted(drop_series)
                       if "gpm" in k.lower() and "gmv" not in k.lower()
                       and ("trend" in k.lower() or k.lower() == "gpm")]
        for k in t6_watch_keys:
            s = (drop_series[k].get("stats") or {}).get("value_sum")
            entry = {"series": k, "title": drop_series[k].get("title"),
                     "kind": "watch_cnt_candidate", "minute_sum": s,
                     "ServerWatchCntTd": swcnt_value,
                     "note": "整场分钟求和若≈ServerWatchCntTd(累计看播次数)则口径闭合,"
                             "可作为小时 GPM 的真实分母候选;否则仅记录。"}
            if _numeric(s) and _numeric(swcnt_value):
                entry["diff"] = round(float(s) - float(swcnt_value), 4)
                entry["closed"] = abs(float(s) - float(swcnt_value)) <= 1.0
            closure_results.append(entry)
        for k in t6_gpm_keys:
            rows = _minute_rows_for_key(cap.bodies, k)
            gmv_rows = _minute_rows_for_key(cap.bodies, "pay_order_gmv_minute_trend")
            implied = _implied_views_minutes(gmv_rows or [], rows or []) if rows else {}
            entry = {"series": k, "title": drop_series[k].get("title"),
                     "kind": "gpm_rate_minute",
                     "stats": drop_series[k].get("stats"),
                     "note": "gpm 为千次观看成交金额(元/千次)分钟率,分钟求和不是闭合口径;"
                             "仅供与同轴 gmv_min 反推有成交分钟 views 的可行性评估。",
                     "implied_views": implied,
                     "ServerWatchCntTd": swcnt_value,
                     "gmv_minute_sum": gmv_sum_value}
            if _numeric(implied.get("implied_views_sum")) and _numeric(swcnt_value):
                entry["implied_vs_swcnt_note"] = (
                    f"反推有成交分钟 views 和={implied['implied_views_sum']} vs 累计看播次数"
                    f"{swcnt_value}:缺口来自 gmv=0 分钟无法反推及口径差异,属近似不闭合,"
                    "不能据此作为小时 GPM 分母。")
            closure_results.append(entry)
        evidence["indicator_dropdown_probe"]["closure"] = closure_results

        # --- 观看系分钟序列汇总(供后续小时聚合选分母) ---
        ordered = []
        order = ["EnterUCntTrend", "OnlineUCntTrend", "LeaveUCntTrend", "WatchCntTrend",
                 "ShowCntTrend", "LikeCntTrend", "CommentCntTrend"]
        for k in order:
            if k in watch_minute:
                ordered.append(watch_minute.pop(k))
        for k in sorted(watch_minute):
            ordered.append(watch_minute[k])
        evidence["room_minute_indicator"]["watch_minute_candidates"] = ordered

        # checksum:进入分钟序列求和 vs 会话累计看播口径(ServerWatchCntTd / 看播人数)
        totals = cards.get("ServerWatchCntTd", {}).get("value")
        ucnt = cards.get("LiveServerWatchUcnt", {}).get("value")
        checks = []
        for cand in ordered:
            s = (cand.get("stats") or {}).get("value_sum")
            checks.append({
                "key": cand["key"],
                "sum": s,
                "ServerWatchCntTd": totals,
                "LiveServerWatchUcnt": ucnt,
                "note": "若分钟求和 == 累计看播次数(ServerWatchCntTd)则高度疑似为“观看次数(看播)分钟增量”;"
                        "否则仅记录,语义待对拍。",
            })
        evidence["watch_series_checksum"] = checks

        # --- 结论文本 ---
        evidence["conclusions"] = _conclude(evidence)
        drop_probe_ev = evidence.get("indicator_dropdown_probe") or {}
        if drop_probe_ev:
            evidence["conclusions"].extend(_conclude_t6(drop_probe_ev))
        evidence["assumptions"] = [
            "行对象 {x,y,time_stamp}: x=页面自带时间(MM-DD HH:MM), y=该分钟值, time_stamp=epoch 秒",
            "key_index.GPM 卡语义=千次观看成交金额(meta title),分母候选=累计看播次数(ServerWatchCntTd)",
            "UI 指标点击仅用于触发页面自身请求,属被动观察;不改写请求、不伪造响应",
        ]
        evidence["online_recheck_required"] = [
            "人工复核: 概览/流量 tab 上“观看次数(看播量)”曲线的展示口径与 key_index 卡片对应关系",
            "人工复核: GPM 卡 value 与 PayGmv/ServerWatchCntTd 数值对拍结论(本证据 cross_check)",
            "人工复核: 流量 tab 的 flow_entrance_trend 系列是否含“看播次数”口径(当前仅曝光渠道序列)",
            "人工复核: 换账号/换场次后上述 key 与语义是否稳定(本证据仅覆盖本账号已结束场次)",
        ]
        if closure_results:
            evidence["online_recheck_required"].append(
                "人工复核(t6):指标下拉中找到的 gpm/看播量分钟序列在“直播中/其他场次”的可用性,"
                "以及其与 ServerWatchCntTd 闭合性是否随场次/账号变化"
            )
        evidence["boundaries"].extend([
            "只读本账号有权限的已结束场次回放页;复用登录会话;不做登录绕过/签名逆向",
            "商品(product_trend/follow_product/…)、营销(marketing_data/local_ads_show_info/…)、"
            "违规(punish_info)、广告(lamp/roi2)响应主体未采集(仅记 URL 名);UI 不点击相关文案",
            "样例截断(前 3 后 3),不整场转储;原始备份仅限观看系/成交金额分钟序列(本地,不入库)",
        ])
        if raw_full:
            raw = {"room_id": str(room_id), "captured_at_utc": _now_utc_iso(),
                   "note": "仅包含观看系候选与成交金额/成交订单数的分钟序列原始行(页面自带 x/y/time_stamp);"
                           "与证据文件同一会话同一次捕获;本地备份,不入库。"}
            wanted = {"pay_order_gmv_minute_trend", "pay_order_cnt_minute_trend"}
            wanted |= {c["key"] for c in ordered}
            wanted |= set((drop_probe_ev.get("target_hits") or {}).keys())
            groups: Dict[str, Any] = {}
            for b in cap.bodies:
                if "room_minute_indicator" not in (b.get("url") or ""):
                    continue
                j = b["json"]
                for g in _chart_keys(j):
                    key = g["key"]
                    if key in wanted and key not in groups:
                        rows = _rows_of_group(j, key)
                        if rows:
                            groups[key] = [{"x": r.get("x"), "y": r.get("y"),
                                            "time_stamp": r.get("time_stamp")} for r in rows]
            raw["minute_series"] = groups
            _write_raw(cfg, room_id, raw, tag=tag)
        out_path = _write_evidence(cfg, room_id, evidence, tag=tag)
        print(f"[gpm_probe] 证据文件: {out_path}")
        print(f"[gpm_probe] 命中层级: L1(room_minute_indicator/key_index/flow_*),"
              f" 端点角色: {roles}")
        print(f"[gpm_probe] 观看系分钟候选: {[c['key'] for c in ordered]}")
        print(f"[gpm_probe] GPM 对拍: {json.dumps(gpm_check, ensure_ascii=False)}")
        for c in checks:
            print(f"[gpm_probe]   序列 {c['key']}: 分钟求和={c['sum']}  vs "
                  f"ServerWatchCntTd={c['ServerWatchCntTd']} LiveServerWatchUcnt={c['LiveServerWatchUcnt']}")
        return 0
    finally:
        try:
            context.close()
        finally:
            pw.stop()


def _rows_of_group(json_obj, key: str):
    """在 json_obj 中按 key 找到 chart 数组(与 _chart_keys 同路径)。"""
    found = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("key") == key and isinstance(node.get("chart"), list):
                found.append(node["chart"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(json_obj)
    return found[0] if found else None


def _title_of_group(json_obj, key: str):
    """meta(dataIndex/dataKey==key).title 兜底 node key。"""
    if isinstance(json_obj, dict):
        for m in (json_obj.get("meta") or []):
            if isinstance(m, dict) and (m.get("dataKey") == key or m.get("dataIndex") == key):
                return m.get("title")
    return key


def _minute_rows_for_key(bodies, key: str):
    """在全部响应体里按 key 找第一组分钟行列表(与 _rows_of_group 同语义)。"""
    for b in bodies:
        if "room_minute_indicator" not in (b.get("url") or ""):
            continue
        rows = _rows_of_group(b["json"], key)
        if rows:
            return rows
    return None


def _implied_views_minutes(gmv_rows, gpm_rows) -> Dict[str, Any]:
    """用每分钟 gmv 与每分钟 gpm(千次观看成交金额)反推该分钟观看次数。

    反推式:views_i = gmv_i ÷ (gpm_i/1000)。只在 gmv_i>0 且 gpm_i>0 的分钟成立;
    gmv=0 的分钟无法反推(0/任意=0 无信息),gpm=0 且 gmv>0 的分钟也不成立。
    因此这是“仅覆盖有成交分钟”的近似,不构成完整观看口径——用于评估可行性,
    不做分母采用依据。两序列须同一分钟轴(按行下标对齐)。
    """
    out: Dict[str, Any] = {"method": "views_i = gmv_i / (gpm_i/1000),仅 gmv>0 且 gpm>0 分钟",
                           "aligned_minutes": 0, "invertible_minutes": 0,
                           "implied_views_sum": None, "skipped_gmv_zero": 0,
                           "skipped_gpm_zero_or_invalid": 0}
    if not gmv_rows or not gpm_rows:
        return out
    n = min(len(gmv_rows), len(gpm_rows))
    out["aligned_minutes"] = n
    implied_total = 0.0
    for i in range(n):
        gv = gmv_rows[i].get("y") if isinstance(gmv_rows[i], dict) else None
        gp = gpm_rows[i].get("y") if isinstance(gpm_rows[i], dict) else None
        if not _numeric(gv):
            out["skipped_gpm_zero_or_invalid"] += 1
            continue
        if float(gv) == 0.0:
            out["skipped_gmv_zero"] += 1
            continue
        if not _numeric(gp) or float(gp) <= 0:
            out["skipped_gpm_zero_or_invalid"] += 1
            continue
        implied = float(gv) / (float(gp) / 1000.0)
        if implied >= 0:
            implied_total += implied
            out["invertible_minutes"] += 1
    if out["invertible_minutes"]:
        out["implied_views_sum"] = round(implied_total, 2)
    out["caveat"] = ("近似而非完整观看口径:gmv=0 分钟无法反推(gpm 无分母信息),"
                     "反推和仅覆盖有成交分钟;若其与 ServerWatchCntTd 有缺口,"
                     "缺口来自无成交分钟的观看量或口径差异,不能据此断言完整闭合。")
    return out


def _cross_check_gpm(cards: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    pay = cards.get("PayGmv", {}).get("value")
    watch = cards.get("ServerWatchCntTd", {}).get("value")
    gpm_card = cards.get("GPM", {}).get("value")
    out: Dict[str, Any] = {
        "PayGmv": pay, "ServerWatchCntTd": watch, "GPM_card": gpm_card,
    }
    if _numeric(pay) and _numeric(watch) and watch != 0:
        computed = round(float(pay) / float(watch) * 1000.0, 2)
        out["computed_gmv_per_thousand_views"] = computed
        if _numeric(gpm_card):
            out["match_page_GPM_card"] = abs(computed - float(gpm_card)) < 0.01
            out["diff"] = round(computed - float(gpm_card), 4)
    return out


def _session_hint(page) -> str:
    try:
        text = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 1500)") or ""
        title = page.title() or ""
    except Exception:
        return "unknown"
    combo = title + text
    if any(k in combo for k in ("直播已结束", "回放", "已结束", "场次结束")):
        return "ended"
    if "正在直播" in combo or "直播中" in combo:
        return "live"
    return "unknown"


def _role_of_url(url: str) -> str:
    if "room_minute_indicator" in url:
        return "room_minute_indicator(分钟指标序列)"
    if "key_index" in url:
        return "key_index(会话累计 KPI 卡)"
    if "api_meta" in url:
        return "api_meta(分钟指标目录)"
    if "flow_index" in url:
        return "flow_index(流量 tab KPI)"
    if "flow_entrance_trend" in url:
        return "flow_entrance_trend(流量渠道序列)"
    if "conversion_funnel" in url:
        return "conversion_funnel(转化漏斗)"
    if "portrait" in url:
        return "portrait(观众画像)"
    if "room_info" in url:
        return "room_info(场次信息)"
    if "inspire_flow_indicator" in url:
        return "inspire_flow_indicator(激励流量)"
    if "check_permission" in url:
        return "check_permission(权限)"
    if "user/info" in url:
        return "user/info(账号信息)"
    return "other"


def _conclude_t6(drop_probe: Dict[str, Any]) -> List[str]:
    """把 t6 下拉展开探测结果转成结论文本(只写实际观测,不臆测)。"""
    out: List[str] = []
    closure = drop_probe.get("closure") or []
    hits = drop_probe.get("target_hits") or {}
    for e in closure:
        k = e.get("series")
        if e.get("kind") == "watch_cnt_candidate":
            if e.get("closed") is True:
                out.append(
                    f"t6 看播量分钟序列闭合: {k} 整场求和 {e.get('minute_sum')} == "
                    f"ServerWatchCntTd({e.get('ServerWatchCntTd')})(diff={e.get('diff')}) → "
                    "该序列可作为小时 GPM 的真实分母候选。"
                )
            else:
                out.append(
                    f"t6 观测到看播量分钟序列 {k}: 求和 {e.get('minute_sum')} vs "
                    f"ServerWatchCntTd({e.get('ServerWatchCntTd')}) diff={e.get('diff')},不闭合,"
                    "不作为分母采用(语义/统计起点待人工复核)。"
                )
        elif e.get("kind") == "gpm_rate_minute":
            implied = e.get("implied_views") or {}
            iv = implied.get("implied_views_sum")
            if iv is not None:
                out.append(
                    f"t6 观测到 gpm 分钟率序列 {k}:可用同轴 gmv_min 反推有成交分钟的 views"
                    f"(和≈{iv},反推覆盖 {implied.get('invertible_minutes')}/{implied.get('aligned_minutes')} 分钟,"
                    f"跳过 gmv=0 {implied.get('skipped_gmv_zero')} 分钟);这是近似而非完整观看口径,"
                    "仅作可行性评估,不作为分母。"
                )
            else:
                out.append(f"t6 观测到 gpm 分钟率序列 {k}(数据不足未反推),Σ 为分钟率无闭合意义。")
    if not closure and hits:
        out.append("t6 下拉探测命中目标候选 key,但缺少数值/累计条件,闭合性无法判定(如实记录)。")
    if not hits and not closure:
        waves = drop_probe.get("waves") or []
        n_clicked = sum(1 for w in waves for _ in w.get("clicked") or [])
        if n_clicked:
            out.append(
                f"t6 展开了指标选择器并点击白名单内指标/选项共 {n_clicked} 次,"
                "未观测到 gpm 或直播间看播量(WatchCntTrend) 分钟序列返回;"
                "仅首页 KPI 卡‘千次观看成交金额’可见。遗留建议:直播中实时大屏指标下拉、"
                "复盘/数据报表、图表右上更多菜单等入口再试(见口径结论文档 §4b)。"
            )
    waves = drop_probe.get("waves") or []
    if not any(w.get("clicked") for w in waves):
        out.append(
            "t6 本次展开探测未观测到新的 gpm/直播间看播量分钟序列返回(下拉内无该类选项,或选项点击未触发刷新);"
            "遗留建议:直播中实时大屏指标下拉、复盘报表、浏览器 Network 里对 room_minute_indicator 请求手动改"
            "data_key 观察(仅人工只读评估,不改写请求)。"
        )
    return out


def _conclude(ev: Dict[str, Any]) -> List[str]:
    out = []
    gpm = ev.get("gpm_metric") or {}
    cc = gpm.get("cross_check") or {}
    if cc.get("match_page_GPM_card") is True:
        out.append(
            f"对拍通过: GPM卡={cc.get('GPM_card')} == PayGmv({cc.get('PayGmv')})/"
            f"ServerWatchCntTd({cc.get('ServerWatchCntTd')})*1000 = {cc.get('computed_gmv_per_thousand_views')};"
            " 页面“千次观看成交金额”分母为累计看播次数(ServerWatchCntTd),非观看人数(LiveServerWatchUcnt)。"
        )
    else:
        out.append("对拍未通过/数据不足: GPM 分母口径待人工在线复核(key_index 各卡见证据)。")
    cands = ev.get("room_minute_indicator", {}).get("watch_minute_candidates") or []
    if cands:
        out.append(
            "分钟级观看系序列可得: room_minute_indicator 可经 UI 指标切换返回 "
            + ", ".join(c["key"] for c in cands) + " 等(与 gmv 同分钟轴)"
        )
    else:
        out.append("概览 UI 指标切换未观察到观看系分钟序列(仅目录 api_meta/meta 中存在),需人工复核其他入口。")
    ki = ev.get("key_index_relevant_cards") or {}
    if ki.get("ServerWatchCntTd"):
        out.append(f"会话累计看播次数(ServerWatchCntTd)={ki['ServerWatchCntTd'].get('value')} "
                   f"(页面可能显示为累计看播次数/观看次数);与 累计在线人数/看播人数 "
                   f"(LiveServerWatchUcnt={ki.get('LiveServerWatchUcnt', {}).get('value')}) 属不同口径。")
    return out


def _write_evidence(cfg: dict, room_id: str, payload: dict,
                    tag: str = "gpm_probe") -> pathlib.Path:
    out_rel = cfg.get("probe", {}).get("output_dir", "data/probe")
    out_dir = pathlib.Path(out_rel)
    if not out_dir.is_absolute():
        out_dir = cfgmod.PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{tag}_{room_id}_{_ts_stamp()}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def _write_raw(cfg: dict, room_id: str, payload: dict,
               tag: str = "gpm_probe") -> pathlib.Path:
    out_dir = pathlib.Path(cfg.get("probe", {}).get("output_dir", "data/probe"))
    if not out_dir.is_absolute():
        out_dir = cfgmod.PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{tag}_{room_id}_{_ts_stamp()}_watch_raw.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[gpm_probe] 观看系分钟序列原始备份(本地,不入库): {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# t9: 直播中采集模式(实时大屏周期扫描;只在房间正在直播时执行;本任务不真机运行)
# ---------------------------------------------------------------------------
def _collect_series_from_bodies(bodies, sink: Dict[str, Dict[str, Any]]) -> None:
    """把 room_minute_indicator 响应体里出现的全部 {key,chart} 并入 sink(增量去重)。

    结构与 gpm_probe2 的 drop_series 条目一致:{key,title,guess,stats};纯本地函数,
    无浏览器/网络依赖(供直播中周期扫描与离线自测共用)。
    """
    for b in bodies:
        if "room_minute_indicator" not in (b.get("url") or ""):
            continue
        j = b.get("json")
        for g in _chart_keys(j):
            key = g["key"]
            if key in sink:
                continue
            rows = _rows_of_group(j, key)
            sink[key] = {
                "key": key,
                "title": _title_of_group(j, key),
                "guess": _guess_key_semantics(key),
                "stats": _row_stats(rows) if rows is not None else {"len": 0},
            }


def live_evidence_skeleton(cfg: dict, room_id: str, url: str,
                           page_title: str = "", session_hint: str = "unknown") -> Dict[str, Any]:
    """直播中采集证据骨架(结构对齐 gpm_probe2 的关键段,供周期扫描逐段填充)。"""
    return {
        "schema_version": LIVE_SCHEMA_VERSION,
        "aligns_with": "gpm_probe2 证据段结构(gpm-1.1):waves/dom_metric_entries_seen/series_keys_total",
        "probe_meta": {
            "room_id": str(room_id),
            "url": url,
            "mode": "live periodic scan(直播中实时大屏,只读)",
            "captured_at_utc": _now_utc_iso(),
            "page_title": page_title[:80],
            "session_state_hint": session_hint,
        },
        "endpoints_seen": [],
        "indicator_dropdown_probe": {
            "waves": [],
            "dom_metric_entries_seen": [],
            "series_keys_total": [],
            "target_hits": {},
            "closure": [],
            "note": ("周期性扫描直播中大屏:每波读增量响应 + DOM 扫描 + 点选白名单指标/下拉选项,"
                     "记录全量 series keys(重点 WatchCntTrend/gpm/观看系);商品/营销/违规/广告文案"
                     "不点、其响应主体不落盘(仅记 URL 名)。"),
        },
        "room_minute_indicator": {"meta_catalog": [], "default_groups": [],
                                  "watch_minute_candidates": []},
        "key_index_relevant_cards": {},
        "gpm_metric": {},
        "watch_series_checksum": [],
        "conclusions": [],
        "assumptions": [
            "行对象 {x,y,time_stamp}: x=页面自带时间(MM-DD HH:MM), y=该分钟值, time_stamp=epoch 秒",
            "直播中采集复用同一 room_minute_indicator 结构;是否与回放一致以本次真机观测为准",
            "UI 指标点击仅用于触发页面自身请求,属被动观察;不改写请求、不伪造响应",
        ],
        "online_recheck_required": [
            "人工复核: 直播中采集到的 gpm/看播量(WatchCntTrend)序列是否与回放口径/累计看播次数一致",
            "人工复核: 直播中时段内分钟序列与直播结束后整场序列是否对齐(含跨场/中断场次)",
        ],
        "boundaries": [
            "只读本账号有权限、正在直播的房间大屏;复用登录会话;不做登录绕过/签名逆向",
            "商品/营销/违规/广告(product_trend/marketing_data/punish_info/lamp/roi2 等)"
            "响应主体不落盘(仅记 URL 名);UI 不点击相关文案",
            "样例截断(前 3 后 3),不整场转储;原始备份仅限观看系/成交金额分钟序列(本地,不入库)",
        ],
    }


def _trim_dom_entries(entries) -> List[Dict[str, Any]]:
    """把 DOM 扫描条目裁剪成可 JSON 序列化的精简副本(坐标取整、文本截断)。"""
    out = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        d = dict(e)
        for kk in ("x", "y", "w", "h"):
            d[kk] = int(d.get(kk) or 0)
        if isinstance(d.get("text"), str):
            d["text"] = d["text"][:40]
        out.append(d)
    return out


def run_gpm_probe_live(cfg: dict, room_id: str, *, waves: Optional[int] = None,
                       interval_sec: Optional[int] = None,
                       max_minutes: Optional[int] = None,
                       raw_full: bool = False, tag: Optional[str] = None) -> int:
    """直播中实时大屏周期扫描(只读;需房间正在直播 + 已登录有权限)。

    流程:打开直播中大屏 → 周期扫描 N 波:每波读增量响应、DOM 扫描指标文本、
    在白名单内点选指标/下拉选项(上限控制),把新增 room_minute_indicator 序列并入
    series_keys 全量;超时/异常优雅退出并保留已收集证据。
    """
    import auth

    waves = int(waves or LIVE_DEFAULT_WAVES)
    interval_sec = int(interval_sec or LIVE_DEFAULT_INTERVAL_SEC)
    max_minutes = int(max_minutes or LIVE_DEFAULT_MAX_MINUTES)
    tag = tag or "gpm_probe2"
    url = _replay_url(cfg, room_id)
    print(f"[gpm_probe][live] 目标直播中大屏: {url}")
    print(f"[gpm_probe][live] 参数: waves={waves} 间隔={interval_sec}s 上限={max_minutes}min "
          f"(总时长封顶 {max_minutes} 分钟,到点优雅退出)")
    try:
        pw, context, page = auth.open_session(cfg, headful=True)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[gpm_probe][live] 打开浏览器失败: {exc}", file=sys.stderr)
        print("[gpm_probe][live] 人工引导: 先 python main.py login(有头扫码),再重试。", file=sys.stderr)
        return 2

    evidence = live_evidence_skeleton(cfg, room_id, url)
    try:
        if not auth.ensure_logged_in(cfg, context, page):
            print("[gpm_probe][live] 无登录会话。人工引导: python main.py login 后重试。", file=sys.stderr)
            return 2
        cap = _Capture()
        page.on("response", cap.on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            print(f"[gpm_probe][live] 打开直播中大屏失败(无权限/无网络/房间未开播?): {exc}", file=sys.stderr)
            return 2
        load_wait = int(cfg.get("probe", {}).get("load_wait_seconds", 35))
        for _ in range(max(1, min(load_wait, 30) // 5)):
            page.wait_for_timeout(5000)
            if _session_hint(page) != "unknown":
                break
        try:
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(2000)
        except Exception:
            pass
        cap.read_bodies()

        page_title = page.title() or ""
        session_hint = _session_hint(page)
        evidence["probe_meta"]["page_title"] = page_title[:80]
        evidence["probe_meta"]["session_state_hint"] = session_hint
        if session_hint == "ended":
            print("[gpm_probe][live] 提示:页面呈'已结束/回放'状态——该房间当前不在直播;"
                  "直播中采集需在房间正在直播时执行(数据可能仍为回放形态)。", file=sys.stderr)
        else:
            print(f"[gpm_probe][live] 页面状态: {session_hint}")

        for b in cap.bodies:
            u = b.get("url") or ""
            evidence["endpoints_seen"].append({"url": u, "role": _role_of_url(u)})
        evidence["boundaries"].append(
            f"主体未落盘的端点(仅 URL 名): {len(cap.excluded_urls)} 个")

        # --- 首波:初始 series + meta 目录 ---
        series_total: Dict[str, Dict[str, Any]] = {}
        _collect_series_from_bodies(cap.bodies, series_total)
        meta_catalog = []
        seen_meta = set()
        for b in cap.bodies:
            if "room_minute_indicator" not in (b.get("url") or ""):
                continue
            j = b.get("json")
            if not isinstance(j, dict):
                continue
            for m in (j.get("meta") or []):
                if isinstance(m, dict) and m.get("dataKey") and m["dataKey"] not in seen_meta:
                    seen_meta.add(m["dataKey"])
                    meta_catalog.append({"dataKey": m.get("dataKey"), "title": m.get("title"),
                                        "type": m.get("type")})
        evidence["room_minute_indicator"]["meta_catalog"] = meta_catalog

        # --- 周期扫描波次 ---
        known_titles = set(_ALLOWED_TREND_CHIPS) | set(_WATCH_HINT_LABELS)
        known_titles |= {str(e.get("title") or "").strip()
                         for e in meta_catalog if isinstance(e.get("title"), str)}
        known_titles = {t for t in known_titles if t and len(t) <= 18 and _tab_allowed(t)}
        clicked_dd: Dict[str, int] = {}
        dom_all: List[Dict[str, Any]] = []
        wave_logs: List[Dict[str, Any]] = []
        total_clicks = 0
        deadline = _time.time() + max_minutes * 60

        for w in range(1, waves + 1):
            if _time.time() >= deadline:
                print(f"[gpm_probe][live] 达总时长上限 {max_minutes}min,提前结束扫描。")
                break
            wave_start = _time.time()
            cap.read_bodies()
            _collect_series_from_bodies(cap.bodies, series_total)
            dom = _trim_dom_entries(_find_metric_dom_entries(page))
            dom_all.extend(dom)
            # 候选:选择器已知指标名(每个名称全程最多点 1 次)
            cands = [t for t in _selector_candidate_texts(page, known_titles)
                     if _tab_allowed(t) and clicked_dd.get(t, 0) < 1]
            if not cands:
                for t in _leaf_texts(page, 400):
                    if len(t) > 18 or not _tab_allowed(t) or clicked_dd.get(t, 0) >= 1:
                        continue
                    if any(k in t for k in ("千次", "GPM", "看播", "观看次数", "观看")):
                        cands.append(t)
            cands = cands[:LIVE_MAX_CLICKS_PER_WAVE]
            wave_log: Dict[str, Any] = {"wave": w,
                                        "elapsed_sec": int(_time.time() - deadline + max_minutes * 60),
                                        "candidates": cands, "clicked": [],
                                        "new_group_keys": [], "dom_metric_entries_found": len(dom)}
            for label in cands:
                if total_clicks >= LIVE_MAX_CLICKS_TOTAL or _time.time() >= deadline:
                    break
                clicked_dd[label] = clicked_dd.get(label, 0) + 1
                base = len(cap.bodies)
                ok = _click_leaf(page, label)
                page.wait_for_timeout(2600)
                cap.read_bodies()
                before = set(series_total)
                _collect_series_from_bodies(cap.bodies[base:], series_total)
                added = sorted(set(series_total) - before)
                if ok:
                    total_clicks += 1
                    wave_log["clicked"].append(label)
                if added:
                    wave_log["new_group_keys"].extend(added)
            wave_logs.append(wave_log)
            # 波间等待(受总时长约束)
            if w < waves:
                remain = max(0.0, deadline - _time.time())
                wait_s = min(interval_sec, remain)
                if wait_s >= 1:
                    page.wait_for_timeout(int(wait_s * 1000))
                elif remain > 0:
                    break

        # --- 证据收口(结构对齐 gpm_probe2) ---
        evidence["indicator_dropdown_probe"]["waves"] = wave_logs
        evidence["indicator_dropdown_probe"]["dom_metric_entries_seen"] = dom_all
        evidence["indicator_dropdown_probe"]["series_keys_total"] = sorted(series_total)
        target = {k: series_total[k] for k in sorted(series_total)
                  if "gpm" in k.lower()
                  or "watchcnttrend" in k.lower()
                  or any(h in k for h in ("看播", "WatchCnt"))}
        evidence["indicator_dropdown_probe"]["target_hits"] = target

        # 观看系候选(按既有展示顺序)与 checksum
        ordered_keys = [k for k in ("WatchCntTrend", "EnterUCntTrend", "OnlineUCntTrend",
                                    "LeaveUCntTrend", "ShowCntTrend", "LikeCntTrend",
                                    "CommentCntTrend") if k in series_total]
        ordered_keys += [k for k in sorted(series_total) if k not in ordered_keys]
        watch_cands = [series_total[k] for k in ordered_keys
                       if any(h in k.lower() for h in _WATCH_KEY_HINTS)]
        evidence["room_minute_indicator"]["watch_minute_candidates"] = watch_cands
        if series_total:
            first = next(iter(series_total.values()))["stats"]
            evidence["room_minute_indicator"]["default_groups"] = [
                series_total[k] for k in ("pay_order_gmv_minute_trend",
                                          "pay_order_cnt_minute_trend")
                if k in series_total]
            evidence["room_minute_indicator"]["time_axis"] = {
                "time_first": first.get("time_first"), "time_last": first.get("time_last"),
                "len": first.get("len"), "time_format": first.get("time_format"),
                "step_seconds": first.get("step_seconds"),
            }

        # key_index 卡(若直播页返回)与 GPM 对拍
        ki_json = None
        for b in cap.bodies:
            if "key_index" in (b.get("url") or ""):
                ki_json = b.get("json")
                break
        relevant_keys = ["PayGmv", "GPM", "ServerWatchCntTd", "LiveServerWatchUcnt",
                         "PayOrderCnt", "LiveCtr", "LiveCvr", "ClientLiveShowCntTd"]
        cards = {}
        if isinstance(ki_json, dict):
            for k in relevant_keys:
                card = _card_lookup(ki_json, k)
                if card:
                    cards[k] = card
        evidence["key_index_relevant_cards"] = cards
        gpm_check = _cross_check_gpm(cards)
        evidence["gpm_metric"] = {"page_card": cards.get("GPM"),
                                  "cross_check": gpm_check,
                                  "note": "直播中 key_index 若可得则同口径对拍;不可得则仅记录。"
                                          "口径未定时不下结论。"}

        # 看播量/gpm 闭合检查(仅实际观测到才写;结构与 gpm_probe2 closure 一致)
        swcnt = cards.get("ServerWatchCntTd", {}).get("value")
        closure = []
        for k in sorted(target):
            s = (series_total[k].get("stats") or {}).get("value_sum")
            entry = {"series": k, "title": series_total[k].get("title"),
                     "kind": "watch_cnt_candidate" if "gpm" not in k.lower() else "gpm_rate_minute",
                     "minute_sum": s, "ServerWatchCntTd": swcnt,
                     "note": "直播中分钟求和若≈ServerWatchCntTd 则口径闭合,可作为分母候选;否则仅记录。"}
            if _numeric(s) and _numeric(swcnt):
                entry["diff"] = round(float(s) - float(swcnt), 4)
                entry["closed"] = abs(float(s) - float(swcnt)) <= 1.0
            closure.append(entry)
        evidence["indicator_dropdown_probe"]["closure"] = closure

        # checksum(观看系 vs 会话累计)
        ucnt = cards.get("LiveServerWatchUcnt", {}).get("value")
        checks = []
        for cand in watch_cands:
            s = (cand.get("stats") or {}).get("value_sum")
            checks.append({"key": cand["key"], "sum": s,
                           "ServerWatchCntTd": swcnt, "LiveServerWatchUcnt": ucnt,
                           "note": "直播中累计口径随直播进行而增长,只作过程记录;最终闭合以结束后整场为准。"})
        evidence["watch_series_checksum"] = checks

        # 结论(只写实际观测)
        concl = []
        if target:
            concl.append("直播中观测到目标候选序列: "
                         + ", ".join(sorted(target))
                         + " (是否可作为小时 GPM 分母,以与结束后整场累计对拍为准)")
        elif total_clicks:
            concl.append(f"直播中周期扫描共点选白名单指标/选项 {total_clicks} 次,"
                         "未观测到 gpm/直播间看播量(WatchCntTrend) 分钟序列返回;"
                         "遗留:尝试图表右上更多菜单/人工点选指标下拉(见 docs/直播中采集指引.md)。")
        else:
            concl.append("直播中首波未观测到新序列(页面可能仍在加载/房间未开播),如实记录。")
        if gpm_check.get("match_page_GPM_card") is True:
            concl.append(f"直播中 GPM 卡对拍通过(卡={gpm_check.get('GPM_card')} == "
                         f"PayGmv/ServerWatchCntTd*1000={gpm_check.get('computed_gmv_per_thousand_views')})")
        evidence["conclusions"] = concl

        if raw_full:
            wanted = {"pay_order_gmv_minute_trend", "pay_order_cnt_minute_trend"}
            wanted |= set(series_total)
            groups: Dict[str, Any] = {}
            for b in cap.bodies:
                if "room_minute_indicator" not in (b.get("url") or ""):
                    continue
                j = b["json"]
                for g in _chart_keys(j):
                    if g["key"] in wanted and g["key"] not in groups:
                        rows = _rows_of_group(j, g["key"])
                        if rows:
                            groups[g["key"]] = [{"x": r.get("x"), "y": r.get("y"),
                                                 "time_stamp": r.get("time_stamp")} for r in rows]
            raw = {"room_id": str(room_id), "mode": "live periodic scan",
                   "captured_at_utc": _now_utc_iso(),
                   "note": "直播中采集的分钟序列原始行(页面自带 x/y/time_stamp);本地备份,不入库。",
                   "minute_series": groups}
            _write_raw(cfg, room_id, raw, tag=tag)

        out_path = _write_evidence(cfg, room_id, evidence, tag=tag)
        print(f"[gpm_probe][live] 证据文件: {out_path}")
        print(f"[gpm_probe][live] series_keys_total={len(series_total)} 个,"
              f"目标候选={sorted(target)},点选 {total_clicks} 次")
        return 0
    finally:
        try:
            context.close()
        finally:
            pw.stop()


def gpm_probe_live_dry_run(cfg: dict, room_id: str) -> int:
    """直播中采集的 dry-run:打印计划与人工引导(不打开浏览器、不采集)。"""
    sample_url = _replay_url(cfg, room_id or "123456789")
    print("[gpm_probe][live] DRY-RUN:不会打开浏览器、不采集任何页面数据。")
    print(f"[gpm_probe][live] 目标直播中大屏(示例): {sample_url}")
    print("[gpm_probe][live] 前提(全部满足才执行):")
    print("  1) 本机已登录持久化 profile(先 python main.py login 完成扫码);")
    print("  2) 账号对目标直播间有查看权限;")
    print("  3) 目标房间当前正在直播(直播中采集只对直播中大屏有效)。")
    print("[gpm_probe][live] 步骤:")
    print("  1) 用下面一行命令打开直播中大屏并周期扫描(默认 6 波、间隔 20s、总时长上限 15min):")
    print("       python gpm_probe.py --live --room-id <直播中房间 room_id>")
    print("     可选参数: --waves N --interval-sec S --max-minutes M --raw-full --tag <前缀>")
    print("  2) 脚本每波:读增量响应 → DOM 扫描指标文本 → 在白名单内点选指标/下拉选项,"
          "并入 series_keys 全量(重点 WatchCntTrend/gpm/观看系);")
    print("  3) 证据输出: data/probe/<tag>_<room_id>_<时间戳>.json(tag 默认 gpm_probe2,"
          "schema_version=gpm-live-1.0,含 waves/dom_metric_entries_seen/series_keys_total);")
    print("     可选 --raw-full 另存分钟序列本地备份(不入库)。")
    print()
    print("[gpm_probe][live] 预计时长: 默认约 2~15 分钟(6 波 × 20s + 首波加载 + 点选等待);"
          "直播越长建议增加 --waves。")
    print("[gpm_probe][live] 失败时人工协助点:")
    print("  - 页面停在登录/无权限: 先 python main.py login;确认账号对房间有查看权限;")
    print("  - 页面显示'已结束/回放': 房间未在直播,需在直播进行时执行;")
    print("  - 指标下拉打不开或看不到 gpm/看播量: 人工点图表右上'更多菜单'图标或指标下拉,"
          "脚本会在下波自动记录新 series keys;")
    print("  - 长时间无新响应: 刷新页面重试(--waves 保持),或加大 --interval-sec/--max-minutes;")
    print("  - 脚本异常退出: 已收集证据仍保留在 data/probe/,把退出码与提示发给维护者。")
    print()
    print("[gpm_probe][live] 合规边界: 只读本账号有权限数据;复用登录态;不做登录绕过/签名逆向;")
    print("             不点商品/营销/违规/广告文案,其响应主体不落盘;样例脱敏;profile/凭据不提交。")
    return 0


# ---------------------------------------------------------------------------
# dry-run: 计划与人工引导(不打开浏览器)
# ---------------------------------------------------------------------------
def gpm_probe_dry_run(cfg: dict, room_id: str) -> int:
    sample_url = _replay_url(cfg, room_id or "123456789")
    print("[gpm_probe] DRY-RUN:不会打开浏览器、不采集任何页面数据。")
    print(f"[gpm_probe] 目标回放页(示例): {sample_url}")
    print("[gpm_probe] 步骤:")
    print("  1) 复用已登录持久化 profile,headful 打开回放页(已结束场次);")
    print("  2) L1 被动捕获 JSON(room_minute_indicator / key_index / api_meta / flow_*),")
    print("     记录端点与角色(商品/营销/违规/广告主体不落盘);")
    print("  3) 在概览趋势面板点击允许的指标文案(成交金额/在线人数/进入人数/离开人数/点赞次数),")
    print("     观察 room_minute_indicator 返回的观看系分钟序列;")
    print("  4) 展开图表指标选择器(下拉/切换),针对性找 千次观看成交金额(gpm) 与")
    print("     直播间看播量(WatchCntTrend) 分钟序列并校验与累计看播次数闭合性(t6);")
    print("  5) 进入流量 tab,记录 flow_index KPI 与 flow_entrance_trend 渠道序列;")
    print("  6) key_index.GPM 卡与 PayGmv/ServerWatchCntTd 数值对拍(口径验证);")
    print("  7) 证据输出: data/probe/<tag>_<room_id>_<时间戳>.json(--tag 指定前缀,默认 gpm_probe);")
    print("     可选 --raw-full 另存观看系/成交金额分钟序列本地备份(不入库)。")
    print()
    print("[gpm_probe] 上线(真机)步骤:")
    print("  1) pip install -r requirements.txt && python -m playwright install chromium")
    print("  2) python main.py login(有头人工扫码,一次即可;凭据仅存本地 profile)")
    print("  3) python gpm_probe.py --room-id <已结束场次 room_id>")
    print()
    print("[gpm_probe] 范围边界: 只读本账号有权限数据;复用登录态;不做登录绕过/签名逆向;")
    print("[gpm_probe]           不收集商品-营销-违规事件带;样例脱敏;profile/凭据不提交。")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gpm_probe",
        description="GPM/观看次数 数据源定位探测(只读真机):定位观看次数分钟序列与页面 GPM 口径(2026-09-07 两场复核)。",
    )
    p.add_argument("--room-id", default=None, help="已结束场次 room_id(探测目标)")
    p.add_argument("--config", default=None, help="配置文件路径(默认项目根 config.yaml)")
    p.add_argument("--dry-run", action="store_true",
                   help="不打开浏览器、不采集数据,只打印计划与人工引导")
    p.add_argument("--raw-full", action="store_true",
                   help="额外把观看系/成交金额分钟序列原始值备份到 data/probe(本地,不入库)")
    p.add_argument("--tag", default="gpm_probe",
                   help="证据文件前缀(默认 gpm_probe;t6 补充探测可用 gpm_probe2)")
    p.add_argument("--live", action="store_true",
                   help="直播中采集模式:打开指定房间直播中大屏,周期扫描/点选指标下拉,"
                        "记录 series_keys 全量(重点 WatchCntTrend/gpm/观看系;需房间正在直播)")
    p.add_argument("--waves", type=int, default=None,
                   help="直播中周期扫描波次数(默认 %d)" % LIVE_DEFAULT_WAVES)
    p.add_argument("--interval-sec", type=int, default=None,
                   help="直播中波间间隔秒数(默认 %d)" % LIVE_DEFAULT_INTERVAL_SEC)
    p.add_argument("--max-minutes", type=int, default=None,
                   help="直播中总时长上限(分钟,默认 %d;到点优雅退出)" % LIVE_DEFAULT_MAX_MINUTES)
    return p


def main(argv=None) -> int:
    cfgmod.setup_utf8_io()
    args = build_parser().parse_args(argv)
    cfg = cfgmod.load_config(args.config)
    if args.live:
        if args.dry_run:
            return gpm_probe_live_dry_run(cfg, args.room_id)
        if not args.room_id:
            print("[gpm_probe][live] 缺少 --room-id(直播中房间;或使用 --live --dry-run 查看计划)。",
                  file=sys.stderr)
            return 2
        return run_gpm_probe_live(cfg, args.room_id, waves=args.waves,
                                  interval_sec=args.interval_sec,
                                  max_minutes=args.max_minutes,
                                  raw_full=bool(args.raw_full),
                                  tag=args.tag if args.tag != "gpm_probe" else "gpm_probe2")
    if args.dry_run:
        return gpm_probe_dry_run(cfg, args.room_id)
    if not args.room_id:
        print("[gpm_probe] 缺少 --room-id(或使用 --dry-run 查看计划)。", file=sys.stderr)
        return 2
    return run_gpm_probe(cfg, args.room_id, raw_full=bool(args.raw_full), tag=args.tag)


if __name__ == "__main__":
    sys.exit(main())
