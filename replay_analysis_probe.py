# -*- coding: utf-8 -*-
"""replay_analysis_probe.py — 已结束场次"复盘/数据分析页"深探(只读真机,t10)。

背景:t1/t6 已确认 大屏趋势页(room_minute_indicator)能取到 分钟成交金额 与
进入/在线等观看系,但 gpm(千次观看成交金额)/WatchCntTrend(直播间看播量)分钟
序列在回放大屏该 UI 下取不到(t1/t6 如实阴性)。t10 改探 **已结束场次的复盘/
数据分析路径**(如复盘/数据中心/数据报表/导出下载入口),确认是否存在:
  (a) 按小时/分钟的 观看/看播次数 + 成交金额 序列;
  (b) "导出/下载"报表入口(Excel/CSV,表头/粒度)。
并把发现落成证据(data/probe/replay_analysis_probe_*.json)与口径结论。

只读边界(与本仓库其他探测一致):
- 复用持久化登录 profile(有头;人工扫码在 auth.perform_login 完成);
- 被动捕获页面自身发起的 JSON 响应;DOM/网络只读扫描;不伪造请求/响应;
- UI 点击仅限白名单导航/文案(复盘/数据中心/数据分析/报表/导出 等数据查看类),
  绝点 商品/营销/违规/视频/广告 类带;
- 导出入口只记录入口位置与文案(href/可见文本),**不点击下载**(避免落盘不明文件);
- 端点角色收录仅 URL 名;banned 端点主体不落盘;样例脱敏;profile/凭据不提交。

用法:
  python replay_analysis_probe.py --room-id <id> --dry-run        # 不打开浏览器
  python replay_analysis_probe.py --room-id <id> [--tag 前缀]     # 真机只读探测
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional

import config as cfgmod

SCHEMA_VERSION = "replay-analysis-1.0"

# 复盘/数据分析页导航与入口的"可点击白名单"关键词(仅数据查看类)
_NAV_ALLOWED_HINTS = ("复盘", "数据中心", "数据分析", "数据报表", "报表", "数据",
                      "分析", "概况", "直播数据", "导出", "下载", "Excel", "CSV")
# 导航文案里出现即整条跳过的禁止词(商品/营销/违规/广告带)
_NAV_EXCLUDED_HINTS = ("商品", "营销", "违规", "视频", "广告", "投流", "充值", "诊断",
                       "货架", "店铺", "粉丝群", "评论管理", "消息")
# 出口(导出/下载)入口关键词
_EXPORT_HINTS = ("导出", "下载", "Excel", "CSV", "报表", "xlsx")
# 链接 href 关键词(复盘/分析/报表/数据中心)
_LINK_HINTS = ("analysis", "report", "data", "replay", "liveScreen", "board")
# banned 端点主体标记(与 gpm_probe 同源)
_EXCLUDED_BODY_URL_MARKS = (
    "product_trend", "product_ai_tip", "follow_product", "marketing_data",
    "local_ads_show_info", "punish_info", "roi2", "lamp", "advertising",
    "sales_tool", "product_explanation",
)
_WATCH_KEY_HINTS = ("watch", "view", "uv", "pv", "enter", "leave", "online",
                    "看播", "观看", "进入", "离开", "在线", "曝光", "人次")


def _now_utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_excluded_body(url: str) -> bool:
    u = (url or "").lower()
    return any(m in u for m in _EXCLUDED_BODY_URL_MARKS)


# ---------------------------------------------------------------------------
# 网络捕获器(被动,同 gpm_probe._Capture)
# ---------------------------------------------------------------------------
class _Capture:
    def __init__(self):
        self.records: List[Dict[str, Any]] = []
        self.bodies: List[Dict[str, Any]] = []
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
        if "douyin.com" not in url:
            return
        if _matches_excluded_body(url):
            self.excluded_urls.append(url)
            return
        self.records.append({"url": url, "_resp": response})

    def read_bodies(self) -> None:
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
# JSON/DOM 只读分析(纯函数/离线友好)
# ---------------------------------------------------------------------------
def _chart_keys(node: Any, out: Optional[List] = None) -> List[Dict[str, Any]]:
    if out is None:
        out = []
    if isinstance(node, dict):
        if isinstance(node.get("chart"), list) and isinstance(node.get("key"), str):
            rows = node["chart"]
            out.append({"key": node["key"], "len": len(rows),
                        "row0": rows[0] if rows and isinstance(rows[0], dict) else None})
        for v in node.values():
            _chart_keys(v, out)
    elif isinstance(node, list):
        for v in node:
            _chart_keys(v, out)
    return out


def _rows_of_group(json_obj, key: str):
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


def _title_of_group(json_obj, key: str) -> str:
    if isinstance(json_obj, dict):
        for m in (json_obj.get("meta") or []):
            if isinstance(m, dict) and (m.get("dataKey") == key or m.get("dataIndex") == key):
                return m.get("title") or key
    return key


def _time_format_hint(sample) -> str:
    s = str(sample or "").strip()
    if len(s) >= 16 and s[4] == "-":
        return "YYYY-MM-DD HH:MM[:SS]"
    if len(s) >= 8 and s[2] == "-":
        return "MM-DD HH:MM"
    if s.replace(".", "", 1).isdigit():
        return "epoch"
    return "unknown"


def _row_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"len": 0}
    xs = [str(r.get("x")) for r in rows]
    vals = [r.get("y") for r in rows]
    num = [v for v in vals if _numeric(v)]
    stat: Dict[str, Any] = {
        "len": len(rows),
        "time_first": xs[0] if xs else None,
        "time_last": xs[-1] if xs else None,
        "time_format": _time_format_hint(xs[0] if xs else None),
        "value_sum": round(sum(num), 4) if num else None,
        "value_min": min(num) if num else None,
        "value_max": max(num) if num else None,
        "sample_head": [{"time": xs[i], "y": vals[i]} for i in range(min(2, len(xs)))],
    }
    return stat


def _granularity_hint(stat: Dict[str, Any], key: str) -> str:
    tf = stat.get("time_format")
    n = stat.get("len") or 0
    if tf == "MM-DD HH:MM" or tf == "YYYY-MM-DD HH:MM[:SS]":
        return "minute(分钟)" if n and n > 24 else "hour_or_minute(需结合时长判定)"
    if tf == "epoch":
        return "epoch(秒级;粒度需结合 step 判定)"
    k = str(key).lower()
    if "hour" in k:
        return "hour(键名含 hour)"
    if "minute" in k:
        return "minute(键名含 minute)"
    return "unknown"


def _role_of_url(url: str) -> str:
    u = (url or "").lower()
    for frag, role in (("room_minute_indicator", "room_minute_indicator(分钟指标)"),
                       ("key_index", "key_index(KPI 卡)"),
                       ("api_meta", "api_meta(指标目录)"),
                       ("flow_index", "flow_index(流量 KPI)"),
                       ("flow_entrance_trend", "flow_entrance_trend(渠道序列)"),
                       ("analysis", "analysis(复盘/分析)"),
                       ("report", "report(报表)"),
                       ("export", "export(导出)"),
                       ("download", "download(下载)"),
                       ("board", "board(看板)"),
                       ("live_screen", "live_screen(大屏)"),
                       ("sessions", "sessions(场次列表)"),
                       ("room_info", "room_info(场次信息)")):
        if frag in u:
            return role
    return "other"


# ---------------------------------------------------------------------------
# DOM 只读扫描(页面内;纯观察,不改写)
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

_LINKS_JS = r"""(hints) => {
  const out = [];
  const els = Array.prototype.slice.call(document.querySelectorAll('a[href]'));
  const seen = new Set();
  for (const a of els) {
    const href = a.href || '';
    const txt = (a.innerText || a.textContent || '').replace(/[\\s\\u00a0]+/g, ' ').trim();
    if (!href || href.startsWith('javascript:')) continue;
    const low = href.toLowerCase();
    if (!hints.some((h) => low.indexOf(h) >= 0)) continue;
    const r = a.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const key = href + '|' + txt;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({href: href.slice(0, 200), text: txt.slice(0, 40),
              x: Math.round(r.x), y: Math.round(r.y)});
    if (out.length >= 60) break;
  }
  return out;
}"""

_LEAF_TEXT_JS = r"""(cap) => {
  const out = [];
  const seen = new Set();
  const els = Array.prototype.slice.call(document.querySelectorAll(
    'div,span,li,a,button,p,h1,h2,h3,label'));
  for (const el of els) {
    if (el.childElementCount > 0) continue;
    const t = (el.innerText || el.textContent || '').trim();
    if (!t || t.length > 24 || seen.has(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    seen.add(t); out.push(t);
    if (out.length >= cap) break;
  }
  return out;
}"""


def _click_leaf(page, label: str) -> bool:
    try:
        return bool(page.evaluate(_CLICK_JS, label))
    except Exception:
        return False


def _leaf_texts(page, cap: int = 400) -> List[str]:
    try:
        return page.evaluate(_LEAF_TEXT_JS, cap)
    except Exception:
        return []


def _page_links(page) -> List[Dict[str, Any]]:
    try:
        return page.evaluate(_LINKS_JS, list(_LINK_HINTS))
    except Exception:
        return []


def _nav_allowed(label: str) -> bool:
    if any(h in label for h in _NAV_EXCLUDED_HINTS):
        return False
    return any(h in label for h in _NAV_ALLOWED_HINTS)


def _session_hint(page) -> str:
    try:
        title = page.title() or ""
        text = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 1500)") or ""
    except Exception:
        return "unknown"
    combo = title + text
    if any(k in combo for k in ("直播已结束", "回放", "已结束", "场次结束")):
        return "ended"
    if any(k in combo for k in ("正在直播", "直播中")):
        return "live"
    return "unknown"


def _export_entries_from_dom(page, texts: List[str]) -> List[Dict[str, Any]]:
    """把含 导出/下载/Excel/CSV/报表 的可见叶子文本整理为导出入口观察记录(不点击)。"""
    out = []
    for t in texts:
        if any(h in t for h in _EXPORT_HINTS):
            out.append({"text": t[:40], "note": "DOM 可见导出/下载/报表文案(入口候选;脚本不点击下载)"})
            if len(out) >= 20:
                break
    return out


def _replay_url(cfg: dict, room_id: str) -> str:
    base = cfg.get("probe", {}).get("base_url", "https://eos.douyin.com/dp/liveScreen")
    return f"{base}?room_id={room_id}&tab=trend"


# ---------------------------------------------------------------------------
# 主流程(只读真机)
# ---------------------------------------------------------------------------
def run_replay_analysis_probe(cfg: dict, room_id: str, *,
                              tag: str = "replay_analysis_probe") -> int:
    import auth

    url = _replay_url(cfg, room_id)
    print(f"[replay_analysis] 目标回放页: {url}")
    try:
        pw, context, page = auth.open_session(cfg, headful=True)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[replay_analysis] 打开浏览器失败: {exc}", file=sys.stderr)
        print("[replay_analysis] 人工引导: 先 python main.py login(有头扫码),再重试。", file=sys.stderr)
        return 2

    evidence: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "probe_meta": {"room_id": str(room_id), "url": url,
                       "captured_at_utc": _now_utc_iso(),
                       "page_title": "", "session_state_hint": "unknown"},
        "mode": "已结束场次复盘/数据分析路径只读探测(大屏+复盘/数据中心/导出入口)",
        "endpoints_seen": [],
        "pages_visited": [],
        "export_entries_seen": [],
        "series_candidates": [],
        "closure_checks": [],
        "hourly_or_minute_watch_series": [],
        "conclusions": [],
        "assumptions": [
            "复盘/数据分析页若存在,优先找 按小时/分钟 的观看(看播)次数与成交金额序列",
            "导出/下载入口仅记录位置与文案,不点击下载(避免不明落盘)",
            "行对象 {x,y,time_stamp} 语义沿用大屏结论;粒度按时间格式与行数提示,不臆测",
        ],
        "online_recheck_required": [],
        "boundaries": [
            "只读本账号有权限的已结束场次;复用登录会话;不做登录绕过/签名逆向",
            "banned 端点(product/marketing/punish/lamp/roi2 等)仅记 URL 名,主体不落盘",
            "UI 只点 复盘/数据中心/数据分析/报表 等数据查看类文案;不点商品/营销/违规/广告",
            "样例截断;profile/凭据不提交",
        ],
    }

    try:
        if not auth.ensure_logged_in(cfg, context, page):
            print("[replay_analysis] 无登录会话。人工引导: python main.py login 后重试。", file=sys.stderr)
            return 2
        cap = _Capture()
        page.on("response", cap.on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            print(f"[replay_analysis] 打开回放页失败(无权限/无网络/房间号错误?): {exc}", file=sys.stderr)
            return 2
        load_wait = int(cfg.get("probe", {}).get("load_wait_seconds", 35))
        for _ in range(max(1, min(load_wait, 30) // 5)):
            page.wait_for_timeout(5000)
        try:
            page.mouse.wheel(0, 500)
            page.wait_for_timeout(2000)
        except Exception:
            pass
        cap.read_bodies()
        page_title = page.title() or ""
        state = _session_hint(page)
        evidence["probe_meta"]["page_title"] = page_title[:80]
        evidence["probe_meta"]["session_state_hint"] = state
        print(f"[replay_analysis] 页面状态: {state}")
        evidence["pages_visited"].append({"url": url, "page_title": page_title[:60],
                                          "session_state_hint": state, "source": "replay(liveScreen)"})

        # ---- 汇总端点角色 ----
        for b in cap.bodies:
            u = b.get("url") or ""
            entry = {"url": u, "role": _role_of_url(u)}
            if entry not in evidence["endpoints_seen"]:
                evidence["endpoints_seen"].append(entry)
        evidence["boundaries"].append(f"主体未落盘的端点(仅 URL 名): {len(cap.excluded_urls)} 个")

        # ---- 页面链接扫描(复盘/分析/报表/数据中心 候选入口,只读记录) ----
        links = _page_links(page)
        evidence["analysis_link_candidates"] = [
            {"href": l["href"], "text": l.get("text", ""),
             "note": "页面锚点中的复盘/分析/报表/数据中心候选(仅记录,点击范围见 visited)"}
            for l in links]
        for l in links:
            t = l.get("text") or ""
            if _nav_allowed(t):
                print(f"[replay_analysis] 发现导航候选: {t or l['href']}")

        # ---- 在可见叶子文案白名单内点 复盘/数据中心/数据分析/报表/数据 导航(每项至多 1 次) ----
        leafs = _leaf_texts(page, 500)
        clicked: List[str] = []
        for label in leafs:
            if len(clicked) >= 6:
                break
            if not _nav_allowed(label):
                continue
            if label in ("导出", "下载") or any(h in label for h in _EXPORT_HINTS):
                continue  # 导出入口只记录不点击
            if _click_leaf(page, label):
                clicked.append(label)
                page.wait_for_timeout(4000)
                cap.read_bodies()
                ev_page = {"url": page.url[:200], "nav_clicked": label,
                           "page_title": (page.title() or "")[:60],
                           "session_state_hint": _session_hint(page)}
                if ev_page not in evidence["pages_visited"]:
                    evidence["pages_visited"].append(ev_page)
                print(f"[replay_analysis] 点击白名单导航: {label} → {page.url[:150]}")
        evidence["nav_clicks"] = clicked

        # ---- 导出/下载 入口观察(不点击) ----
        texts_now = _leaf_texts(page, 500)
        export_entries = _export_entries_from_dom(page, texts_now)
        for l in links:
            t = l.get("text") or ""
            if any(h in t for h in _EXPORT_HINTS):
                export_entries.append({"text": t[:40], "href": l["href"][:200],
                                       "note": "导出/下载锚点(记录不点击)"})
        # 去重
        seen_exp = set()
        dedup_exp = []
        for e in export_entries:
            key = (e.get("text"), e.get("href", ""))
            if key in seen_exp:
                continue
            seen_exp.add(key)
            dedup_exp.append(e)
        evidence["export_entries_seen"] = dedup_exp[:20]

        # ---- 汇总全部响应里的序列候选(小时/分钟观看系与成交) ----
        series_seen: Dict[str, Dict[str, Any]] = {}
        for b in cap.bodies:
            j = b["json"]
            for g in _chart_keys(j):
                key = g["key"]
                if key in series_seen:
                    continue
                rows = _rows_of_group(j, key)
                stat = _row_stats(rows) if rows else {"len": 0}
                series_seen[key] = {
                    "key": key,
                    "title": _title_of_group(j, key),
                    "granularity_hint": _granularity_hint(stat, key),
                    "stats": stat,
                    "source_url_role": _role_of_url(b.get("url") or ""),
                }
        ordered = sorted(series_seen)
        evidence["series_candidates"] = [series_seen[k] for k in ordered]
        watch_keys = [k for k in ordered
                      if any(h in str(k).lower() for h in _WATCH_KEY_HINTS)]
        gmv_keys = [k for k in ordered if any(h in str(k).lower()
                                              for h in ("gmv", "成交", "pay", "order"))]
        evidence["hourly_or_minute_watch_series"] = [
            series_seen[k] for k in watch_keys
            if series_seen[k]["granularity_hint"].startswith(("minute", "hour", "epoch"))]

        # ---- 整场闭合校验:Σ 小时/分钟 观看/成交 vs 会话累计卡(key_index 卡值) ----
        cards: Dict[str, Any] = {}
        for b in cap.bodies:
            j = b["json"]
            if not isinstance(j, dict) or "key_index" not in (b.get("url") or ""):
                continue
            d = j.get("data")
            if not isinstance(d, dict):
                continue
            for k in ("PayGmv", "GPM", "ServerWatchCntTd", "LiveServerWatchUcnt",
                      "PayOrderCnt", "ClientLiveShowCntTd"):
                c = d.get(k)
                if isinstance(c, dict) and k not in cards:
                    cards[k] = {"key": c.get("key") or k, "name": c.get("name"),
                                "unit": c.get("unit"), "value": c.get("value")}
        evidence["key_index_cards_found"] = cards
        closure: List[Dict[str, Any]] = []
        for k in ordered:
            s = (series_seen[k].get("stats") or {}).get("value_sum")
            if not _numeric(s):
                continue
            low = str(k).lower()
            if any(h in low for h in ("watch", "view", "看播", "观看", "enter", "uv")):
                target_card = "ServerWatchCntTd"
            elif "gmv" in low or "成交" in k or (("pay" in low or "amount" in low) and "order" not in low):
                target_card = "PayGmv"
            elif "order" in low or "cnt" in low:
                target_card = "PayOrderCnt"
            else:
                continue
            card_val = cards.get(target_card, {}).get("value")
            entry = {"series": k, "sum": s, "compared_to": target_card,
                     "card_value": card_val,
                     "note": "Σ序列 vs 会话累计卡;闭合(差值≈0)则序列可作小时 GPM 分母/分子候选"}
            if _numeric(card_val):
                entry["diff"] = round(float(s) - float(card_val), 4)
                entry["closed"] = abs(float(s) - float(card_val)) <= 1.0
            closure.append(entry)
        evidence["closure_checks"] = closure

        # ---- 结论(只写实际观测) ----
        concl: List[str] = []
        if watch_keys:
            concl.append("复盘/分析路径下观测到观看系序列候选: "
                         + ", ".join(watch_keys)
                         + ";闭合校验见 closure_checks(小时 GPM 分母可行性以其为准)。")
        elif cap.bodies:
            concl.append("复盘/分析路径下未观测到新的观看系分钟/小时序列候选"
                         "(仅记录既有端点;如实阴性,不臆测)。")
        else:
            concl.append("页面未返回可解析 JSON 响应(可能未登录权限/入口需人工点开);如实记录。")
        if dedup_exp:
            concl.append("发现导出/下载/报表入口(仅记录,未点击下载): "
                         + "; ".join(e["text"] for e in dedup_exp[:5]))
        if not cards and cap.bodies:
            concl.append("未捕获 key_index 卡(该路径可能无会话累计卡或端点不同),闭合校验以可得卡为准。")
        evidence["conclusions"] = concl
        evidence["online_recheck_required"] = [
            "人工复核: 复盘/数据中心各 tab 与'更多菜单'图标的真实入口与导出报表的表头/粒度",
            "人工复核: 若导出报表可取,核对 小时/分钟 列口径与累计看播次数(ServerWatchCntTd)闭合性",
        ]

        out_path = _write_evidence(cfg, room_id, evidence, tag=tag)
        print(f"[replay_analysis] 证据文件: {out_path}")
        print(f"[replay_analysis] 端点角色 {len(evidence['endpoints_seen'])} 个 | "
              f"序列候选 {len(evidence['series_candidates'])} | "
              f"导出入口 {len(dedup_exp)} | 访问页 {len(evidence['pages_visited'])}")
        for c in concl:
            print(f"[replay_analysis]   - {c}")
        return 0
    finally:
        try:
            context.close()
        finally:
            pw.stop()


def _write_evidence(cfg: dict, room_id: str, payload: dict,
                    tag: str = "replay_analysis_probe") -> pathlib.Path:
    out_rel = cfg.get("probe", {}).get("output_dir", "data/probe")
    out_dir = pathlib.Path(out_rel)
    if not out_dir.is_absolute():
        out_dir = cfgmod.PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{tag}_{room_id}_{_ts_stamp()}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------
def replay_analysis_dry_run(cfg: dict, room_id: str) -> int:
    sample_url = _replay_url(cfg, room_id or "123456789")
    print("[replay_analysis] DRY-RUN:不会打开浏览器、不采集任何页面数据。")
    print(f"[replay_analysis] 目标(已结束场次回放页): {sample_url}")
    print("[replay_analysis] 步骤:")
    print("  1) 复用已登录 profile,headful 打开已结束场次回放页(先 main.py login);")
    print("  2) 被动捕获 JSON 响应,记录端点角色(room_minute_indicator/key_index/analysis/report 等);")
    print("  3) 只读扫描页面锚点与叶子文案,白名单内点 复盘/数据中心/数据分析/报表 等导航,"
          "观察新 URL/新序列;")
    print("  4) 记录 导出/下载/报表(Excel/CSV)入口的位置与文案(不点击下载);")
    print("  5) 汇总 小时/分钟 观看(看播)次数与成交金额序列候选,做整场闭合校验"
          "(Σ vs 累计看播次数/成交);")
    print("  6) 证据输出: data/probe/replay_analysis_probe_<room_id>_<时间戳>.json。")
    print()
    print("[replay_analysis] 上线(真机)步骤:")
    print("  1) pip install -r requirements.txt && python -m playwright install chromium")
    print("  2) python main.py login(有头人工扫码,一次即可)")
    print("  3) python replay_analysis_probe.py --room-id <已结束场次 room_id>")
    print()
    print("[replay_analysis] 边界: 只读本账号有权限数据;复用登录态;不点商品/营销/违规/广告;")
    print("             导出入口只记录不点击;样例脱敏;profile/凭据不提交。")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="replay_analysis_probe",
        description="已结束场次复盘/数据分析页只读探测(t10):找 小时/分钟 观看次数+成交金额序列或导出报表入口。",
    )
    p.add_argument("--room-id", default=None, help="已结束场次 room_id")
    p.add_argument("--config", default=None, help="配置文件路径")
    p.add_argument("--dry-run", action="store_true", help="不打开浏览器,打印计划")
    p.add_argument("--tag", default="replay_analysis_probe", help="证据文件前缀")
    return p


def main(argv=None) -> int:
    cfgmod.setup_utf8_io()
    args = build_parser().parse_args(argv)
    cfg = cfgmod.load_config(args.config)
    if args.dry_run:
        return replay_analysis_dry_run(cfg, args.room_id)
    if not args.room_id:
        print("[replay_analysis] 缺少 --room-id(或使用 --dry-run 查看计划)。", file=sys.stderr)
        return 2
    return run_replay_analysis_probe(cfg, args.room_id, tag=args.tag)


if __name__ == "__main__":
    sys.exit(main())
