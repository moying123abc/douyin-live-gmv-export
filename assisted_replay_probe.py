# -*- coding: utf-8 -*-
"""assisted_replay_probe.py — 人工协助复现:回放页"千次观看成交金额/直播间看播量"分钟序列入口(t11)。

背景:用户确认 已结束回放页 中"千次观看成交金额(按分钟)"选项**可见**,但自动化探测
(t1 指标点击 / t6 下拉展开 / t10 复盘路径)两次未命中其点击入口——需要人工协助复现
准确点击路径。本脚本提供 **assisted-replay** 捕获模式:

- 有头打开已结束回放页(09-04 room 7000000000000000001 等),复用已登录 profile;
- 进入**长窗口增量监听**(默认 6 分钟,可调):周期读入 room_minute_indicator 新增
  响应 + DOM 只读扫描(指标选项文本/可见性/坐标),记录 waves;
- 在终端/日志**周期性打印给用户的人工操作提示**(如"请在趋势图上把指标切换为
  千次观看成交金额/直播间看播量");
- 用户手动切换后:若页面自发生成新 series key(gpm/WatchCntTrend/观看系),脚本记录其
  dataKey/title/单位并做整场闭合校验(Σ vs key_index ServerWatchCntTd/PayGmv);
- 若窗口内仍无新序列:记录 DOM 中所见指标选项名与位置 + 用户操作观察
  (新出现/变化文案),作为可复现入口证据;不臆测、不伪造。

只读边界:不改写请求/响应;不点 商品/营销/违规/广告 文案;banned 端点主体不落盘;
样例截断;profile/凭据不提交。证据:data/probe/assisted_replay_<room_id>_<时间戳>.json。

用法:
  python assisted_replay_probe.py --room-id <id> --dry-run      # 不打开浏览器
  python assisted_replay_probe.py --room-id <id>               # 真机监听(需人工配合切换指标)
  python assisted_replay_probe.py --room-id <id> --minutes 10 --interval-sec 15
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
import gpm_probe as _gp  # 复用被动捕获/DOM/序列解析(顶层仅标准库,离线可导入)

SCHEMA_VERSION = "assisted-replay-1.0"

# 默认监听窗口与采样间隔
DEFAULT_WINDOW_MINUTES = 6
DEFAULT_INTERVAL_SEC = 10

# 给用户的人工操作提示(周期性打印)
_USER_PROMPT_LINES = [
    "人工协助提示(assisted-replay): 请在已打开的浏览器回放页里操作——",
    "  1) 找到趋势图(成交金额/成交订单数 曲线)标题区或其右上角的指标切换控件;",
    "  2) 把曲线指标切换为『千次观看成交金额』(如选项名含 GPM/千次观看);",
    "  3) 若另有『直播间看播量/观看次数』类选项,也请逐个选中试一次;",
    "  4) 脚本正在监听 room_minute_indicator 与页面 DOM——每选中一个新指标,"
    "     页面会自发刷新数据,脚本会自动记录新出现的 series key 与指标选项。",
]

_METRIC_TEXT_KEYWORDS = ("千次", "GPM", "看播", "观看", "进入", "离开", "在线",
                         "点赞", "评论", "曝光", "成交金额", "成交订单", "互动")


def _now_utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _replay_url(cfg: dict, room_id: str) -> str:
    base = cfg.get("probe", {}).get("base_url", "https://eos.douyin.com/dp/liveScreen")
    return f"{base}?room_id={room_id}&tab=trend"


def _session_hint(page) -> str:
    return _gp._session_hint(page)


def _trim_dom(entries) -> List[Dict[str, Any]]:
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


def _metric_leaf_delta(before: set, after: set) -> List[str]:
    """两次叶子文本快照间"新出现"且含指标关键词的文案(可提示用户点了什么)。"""
    return sorted(t for t in (after - before) if any(k in t for k in _METRIC_TEXT_KEYWORDS))


def run_assisted_replay(cfg: dict, room_id: str, *, minutes: Optional[int] = None,
                        interval_sec: Optional[int] = None,
                        tag: str = "assisted_replay") -> int:
    """有头打开已结束回放页并进入长窗口监听,配合用户人工切换指标捕获分钟序列。"""
    import auth

    minutes = int(minutes or DEFAULT_WINDOW_MINUTES)
    interval_sec = int(interval_sec or DEFAULT_INTERVAL_SEC)
    url = _replay_url(cfg, room_id)
    deadline = _time.time() + minutes * 60
    print(f"[assisted_replay] 目标回放页: {url}")
    print(f"[assisted_replay] 监听窗口: {minutes} 分钟,采样间隔 {interval_sec}s "
          f"(到点优雅退出;全程只读)")
    try:
        pw, context, page = auth.open_session(cfg, headful=True)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[assisted_replay] 打开浏览器失败: {exc}", file=sys.stderr)
        print("[assisted_replay] 人工引导: 先 python main.py login(有头扫码),再重试。",
              file=sys.stderr)
        return 2

    evidence: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "probe_meta": {"room_id": str(room_id), "url": url,
                       "captured_at_utc": _now_utc_iso(),
                       "page_title": "", "session_state_hint": "unknown",
                       "window_minutes": minutes, "interval_sec": interval_sec},
        "mode": "人工协助复现(assisted-replay):长窗口监听 room_minute_indicator 增量 + DOM 指标选项;"
                "用户手动把趋势指标切换为 千次观看成交金额/看播量",
        "user_prompt": _USER_PROMPT_LINES,
        "waves": [],
        "series_keys_total": [],
        "target_hits": {},
        "dom_metric_entries_seen": [],
        "user_action_observed": [],
        "closure_checks": [],
        "key_index_cards_found": {},
        "conclusions": [],
        "assumptions": [
            "用户切换指标会触发页面自发 room_minute_indicator 请求(与 t1/t6 观测一致)",
            "新出现的 series key(gpm/WatchCntTrend/观看系)与同轴 gmv 行可作分母/分子候选",
            "DOM 中指标选项名与坐标仅为可复现入口证据;不臆测其触发方式",
        ],
        "online_recheck_required": [
            "人工复核: 用户实际点击的控件(图表卡标题/下拉/更多菜单/图例)与选项列表(截图留档)",
            "若捕获到 gpm/看播量:复核该序列在 直播中/其他场次 的可用性与闭合稳定性",
        ],
        "boundaries": [
            "只读本账号有权限的已结束场次;复用登录会话;不做登录绕过/签名逆向",
            "不改写请求/响应;脚本不做任何指标点击(切换由用户人工完成)",
            "banned 端点(product/marketing/punish/lamp/roi2 等)仅记 URL 名,主体不落盘",
            "样例截断;profile/凭据不提交",
        ],
    }

    try:
        if not auth.ensure_logged_in(cfg, context, page):
            print("[assisted_replay] 无登录会话。人工引导: python main.py login 后重试。",
                  file=sys.stderr)
            return 2
        cap = _gp._Capture()
        page.on("response", cap.on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            print(f"[assisted_replay] 打开回放页失败(无权限/无网络/房间号错误?): {exc}",
                  file=sys.stderr)
            return 2
        load_wait = int(cfg.get("probe", {}).get("load_wait_seconds", 35))
        for _ in range(max(1, min(load_wait, 25) // 5)):
            page.wait_for_timeout(5000)
        try:
            page.mouse.wheel(0, 400)
            page.wait_for_timeout(1500)
        except Exception:
            pass
        cap.read_bodies()
        evidence["probe_meta"]["page_title"] = (page.title() or "")[:80]
        evidence["probe_meta"]["session_state_hint"] = _session_hint(page)
        print(f"[assisted_replay] 页面状态: {evidence['probe_meta']['session_state_hint']}")

        series_total: Dict[str, Dict[str, Any]] = {}
        _gp._collect_series_from_bodies(cap.bodies, series_total)
        prev_leafs: set = set(_gp._leaf_texts(page, 600))
        dom_all: List[Dict[str, Any]] = []
        user_actions: List[Dict[str, Any]] = []
        wave_logs: List[Dict[str, Any]] = []
        prompt_printed = False

        def print_prompt() -> None:
            for ln in _USER_PROMPT_LINES:
                print(f"[assisted_replay] {ln}")

        print_prompt()
        prompt_printed = True

        wave_no = 0
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                print("[assisted_replay] 到达监听窗口上限,优雅退出。")
                break
            wave_no += 1
            wave_start = _time.time()
            # 增量读入新增响应体
            pre = len(cap.bodies)
            cap.read_bodies()
            new_bodies = cap.bodies[pre:]
            before_keys = set(series_total)
            _gp._collect_series_from_bodies(new_bodies, series_total)
            added_keys = sorted(set(series_total) - before_keys)
            # DOM 指标选项扫描 + 叶子文本 delta(检测用户操作)
            dom = _trim_dom(_gp._find_metric_dom_entries(page))
            dom_all.extend(dom)
            leafs_now = set(_gp._leaf_texts(page, 600))
            new_metric_leafs = _metric_leaf_delta(prev_leafs, leafs_now)
            prev_leafs = leafs_now
            if new_metric_leafs:
                user_actions.append({"wave": wave_no,
                                     "elapsed_sec": int(minutes * 60 - max(0.0, remaining)),
                                     "new_metric_texts": new_metric_leafs,
                                     "note": "监听期间页面新出现/变化的指标相关文案"
                                             "(可能由用户点击产生;原样记录)"})
                print(f"[assisted_replay] 观察到页面文案变化: {new_metric_leafs}")
            wave_logs.append({
                "wave": wave_no,
                "elapsed_sec": int(minutes * 60 - max(0.0, remaining)),
                "new_group_keys": added_keys,
                "series_keys_total": len(series_total),
                "dom_metric_entries_found": len(dom),
                "user_action": new_metric_leafs or None,
            })
            if added_keys:
                print(f"[assisted_replay] 新 series key: {added_keys}")
                for k in added_keys:
                    print(f"[assisted_replay]   {k} | title={series_total[k].get('title')} "
                          f"| guess={series_total[k].get('guess')}")
            # 周期性重打印提示(每约 60s)
            if prompt_printed and int(minutes * 60 - max(0.0, remaining)) >= 60 \
                    and (wave_no % max(1, 60 // max(1, interval_sec)) == 0):
                print_prompt()
            wait_s = min(interval_sec, max(0.0, deadline - _time.time()))
            if wait_s >= 1:
                page.wait_for_timeout(int(wait_s * 1000))

        # ---- 证据收口 ----
        evidence["waves"] = wave_logs
        evidence["dom_metric_entries_seen"] = dom_all
        evidence["user_action_observed"] = user_actions
        evidence["series_keys_total"] = sorted(series_total)
        target = {k: series_total[k] for k in sorted(series_total)
                  if "gpm" in k.lower()
                  or "watchcnttrend" in k.lower()
                  or any(h in k for h in ("看播", "WatchCnt"))}
        evidence["target_hits"] = target

        # key_index 卡与闭合校验(仅实际观测到才写)
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
        for k, entry in target.items():
            rows = _gp._minute_rows_for_key(cap.bodies, k)
            s = (entry.get("stats") or {}).get("value_sum")
            if s is None and rows:
                s = round(sum(float(r.get("y")) for r in rows
                              if isinstance(r.get("y"), (int, float))), 4)
            is_watch = any(h in k.lower() for h in ("watch", "view", "看播", "观看"))
            target_card = "ServerWatchCntTd" if is_watch else "PayGmv"
            card_val = cards.get(target_card, {}).get("value")
            rec = {"series": k, "title": entry.get("title"), "sum": s,
                   "compared_to": target_card, "card_value": card_val,
                   "note": "人工切换后捕获;Σ vs 会话累计卡;闭合(差值≈0)则序列可作"
                           "小时 GPM 分母/分子候选(另复核单位/口径)"}
            if isinstance(s, (int, float)) and isinstance(card_val, (int, float)):
                rec["diff"] = round(float(s) - float(card_val), 4)
                rec["closed"] = abs(float(s) - float(card_val)) <= 1.0
            closure.append(rec)
        evidence["closure_checks"] = closure

        # 结论
        concl: List[str] = []
        if target:
            concl.append("人工协助监听期间捕获到目标序列: "
                         + ", ".join(sorted(target))
                         + ";闭合校验见 closure_checks(小时 GPM 分母可行性以其为准,"
                         "单位/口径仍需与页面文案复核)。")
        elif user_actions:
            concl.append("监听期间观察到页面指标文案变化(见 user_action_observed),"
                         "但未捕获到新的 gpm/看播量分钟序列 key;如实记录,"
                         "用户点击路径与所见选项作为可复现入口证据,供再次复核。")
        else:
            concl.append("监听窗口内未捕获新 series key,也未观察到页面文案变化"
                         "(用户可能未在窗口内完成切换,或入口不在文本 DOM 中);"
                         "如实阴性。可复现入口证据=dom_metric_entries_seen 与 user_prompt,"
                         "等待一次有用户配合的会话重跑。")
        evidence["conclusions"] = concl

        out_path = _write_evidence(cfg, room_id, evidence, tag=tag)
        print(f"[assisted_replay] 证据文件: {out_path}")
        print(f"[assisted_replay] 波次 {len(wave_logs)} | series_keys_total="
              f"{len(series_total)} | 目标候选={sorted(target)}")
        for c in concl:
            print(f"[assisted_replay]   - {c}")
        return 0
    finally:
        try:
            context.close()
        finally:
            pw.stop()


def _write_evidence(cfg: dict, room_id: str, payload: dict,
                    tag: str = "assisted_replay") -> pathlib.Path:
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
def assisted_replay_dry_run(cfg: dict, room_id: str) -> int:
    sample_url = _replay_url(cfg, room_id or "7000000000000000001")
    print("[assisted_replay] DRY-RUN:不会打开浏览器、不采集任何页面数据。")
    print(f"[assisted_replay] 目标(已结束场次回放页): {sample_url}")
    print("[assisted_replay] 流程:")
    print("  1) 有头打开回放页并复用已登录 profile;进入监听(默认 6 分钟,可 --minutes 调整);")
    print("  2) 脚本只读监听 room_minute_indicator 增量 + DOM 指标选项,不自动点击指标;")
    print("  3) 请在已打开的浏览器中**人工**把趋势图指标切换为『千次观看成交金额』,"
          "或试『直播间看播量/观看次数』类选项;")
    print("  4) 每切换一次,页面会自发刷新——脚本记录新 series key 与 DOM 变化;")
    print("  5) 捕获到 gpm/WatchCntTrend/观看系 → 做整场闭合校验(Σ vs key_index);")
    print("     仍无 → 记录 DOM 中所见选项名/位置 + 用户操作观察,作为可复现入口证据;")
    print("  6) 证据输出: data/probe/assisted_replay_<room_id>_<时间戳>.json"
          "(schema assisted-replay-1.0,含 waves/series_keys_total/target_hits/"
          "dom_metric_entries_seen/user_action_observed)。")
    print()
    print("[assisted_replay] 上线(真机,需人工配合)步骤:")
    print("  1) pip install -r requirements.txt && python -m playwright install chromium")
    print("  2) python main.py login(有头人工扫码,一次即可)")
    print("  3) python assisted_replay_probe.py --room-id <已结束场次 room_id> --minutes 10")
    print()
    print("[assisted_replay] 边界: 只读本账号有权限数据;复用登录态;不改写请求/响应;")
    print("             不点 商品/营销/违规/广告 文案;样例脱敏;profile/凭据不提交。")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="assisted_replay_probe",
        description="人工协助复现:定位回放页'千次观看成交金额/直播间看播量'分钟指标入口并捕获序列(t11)。",
    )
    p.add_argument("--room-id", default=None, help="已结束场次 room_id(默认提示 09-04 场)")
    p.add_argument("--config", default=None, help="配置文件路径")
    p.add_argument("--dry-run", action="store_true", help="不打开浏览器,打印计划与人工提示")
    p.add_argument("--minutes", type=int, default=None,
                   help="监听窗口分钟数(默认 %d)" % DEFAULT_WINDOW_MINUTES)
    p.add_argument("--interval-sec", type=int, default=None,
                   help="采样间隔秒数(默认 %d)" % DEFAULT_INTERVAL_SEC)
    p.add_argument("--tag", default="assisted_replay", help="证据文件前缀")
    return p


def main(argv=None) -> int:
    cfgmod.setup_utf8_io()
    args = build_parser().parse_args(argv)
    cfg = cfgmod.load_config(args.config)
    if args.dry_run:
        return assisted_replay_dry_run(cfg, args.room_id)
    if not args.room_id:
        print("[assisted_replay] 缺少 --room-id(已结束场次;或使用 --dry-run 查看计划)。",
              file=sys.stderr)
        return 2
    return run_assisted_replay(cfg, args.room_id, minutes=args.minutes,
                               interval_sec=args.interval_sec, tag=args.tag)


if __name__ == "__main__":
    sys.exit(main())
