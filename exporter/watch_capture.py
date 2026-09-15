# -*- coding: utf-8 -*-
"""exporter/watch_capture.py — 回放页自动捕获"直播间看播量(WatchCntTrend)"分钟序列(t12)。

背景:t11 人工协助已证:已结束回放页经 趋势图右上**齿轮 → 自定义指标 → 勾选 直播间观看量
(直播间看播量) → 确定**,room_minute_indicator 会返回 WatchCntTrend(与 gmv 同分钟轴,
整场行)。本模块把该入口做成**可自动/可 assist 的捕获**并落成观看档:

- 自动模式(auto):DOM 定位齿轮(趋势图右上,如 DIV.cls=iIFiN@(868,341) 一带)→ 点击 →
  等「自定义指标」弹窗可见 → 按文本定位选项行(直播间观看量/看播量)点击勾选 → 点「确定」
  → 若 WatchCntTrend 未即返回:自动点按趋势图上方**图例芯片**(直播间观看量/直播间看播量,
  必要时千次观看成交金额)使曲线真实显示(t13;文本定位优先,无法文本定位且配置了
  export.watch_capture_chip_anchor 则坐标标定一次并记录;芯片开关态不可判时不盲点,
  仍失败转 assist 人工点一次)→ 轮询 room_minute_indicator 直至出现 WatchCntTrend 整场行
  → 同刻读 key_index;
- 若自动弹窗/勾选失败:返回 not-ok 并打印 assist 人工步骤(不阻断调用方,由调用方优雅降级);
- 输出:观看档 live_<ymd>_<room>_watch_min.csv(表头 time,views,semantics;time=MM-DD HH:MM,
  与成交分钟同轴)+ 证据 watch_capture_<room>_<ts>.json(含 Σ/ServerWatchCntTd 同刻卡/diff/说明)。

只读边界:复用登录会话;不改写请求/响应;只点 齿轮/自定义指标 白名单交互;不点 商品/营销/
违规/广告;样例脱敏;profile/凭据不提交。
"""
from __future__ import annotations

import csv
import datetime as _dt
import json
import pathlib
import time as _time
from typing import Any, Dict, List, Optional

import config as cfgmod
import gpm_probe as _gp  # 复用捕获/DOM/序列助手(顶层仅标准库,离线可导入)

WATCH_MIN_SUFFIX = "_watch_min.csv"
SEMANTICS_TEMPLATE = (
    "直播间观看量(直播间看播量)分钟序列;dataKey=WatchCntTrend,来自 room_minute_indicator;"
    "整场 Σ={sum} vs 同刻 key_index.ServerWatchCntTd={card}(diff={diff});复核差如实标注,"
    "未冒充精确闭合;小时 GPM 分母采用本序列并附复核差说明。"
)

# 回放页趋势图同屏指标上限(用户确认:自定义指标弹窗提示 最少1/最多6)
MAX_CHART_METRICS = 6
# gmv(成交金额)是小时 GPM 分子,必须始终保留在趋势图上(用户约束)
REQUIRED_METRIC = "成交金额"
# 默认保留集合(恰好 6 项):成交金额、成交订单数、在线人数、进入人数、
# 直播间观看量(直播间看播量)、千次观看成交金额
DEFAULT_KEEP_METRICS = ["成交金额", "成交订单数", "在线人数", "进入人数",
                        "直播间观看量", "千次观看成交金额"]

# ---- t13:图例芯片(趋势图上方指标标签,点按切换曲线显隐) ----
# 实测(09-04 回放页):芯片行文本 y≈332、芯片项容器 y≈331–347、x 从 ~200 起至 ~760;
# 右侧 ~849 为齿轮(iIFiN),其右 y≈300 为 视频/商品/营销/违规 popper —— 扫描带须避让。
CHIP_BAND = {"y_min": 318, "y_max": 375, "x_min": 150, "x_max": 895}
# 目标芯片:直播间观看量(直播间看播量)= WatchCntTrend 曲线(必需);
# 千次观看成交金额 = gpm 曲线(可选,命中 off 才点按,缺/态未知不阻塞 watch)
WATCH_CHIP_METRIC = "直播间观看量"
OPTIONAL_CHIP_METRICS = ["千次观看成交金额"]
# 成交金额(gmv 分子)芯片永不点按(防止误隐藏分子曲线);呈 off 视为异常转 assist
CHIP_FORBIDDEN_TOGGLE = REQUIRED_METRIC  # "成交金额"
CHIP_MAX_CLICKS = 2      # 同一轮最多点按 2 个芯片
CHIP_MAX_RETRIES = 1     # 点按未生效时最多再重扫/点按 1 次(不循环)

# 齿轮/弹窗交互所用 JS ----------------------------------------------------------
_GEAR_JS = r"""() => {
  // 趋势图右上齿轮(自定义指标入口):优先 class 命中,退化为小图标区域扫描
  const byClass = Array.prototype.slice.call(document.querySelectorAll('div'));
  for (const el of byClass) {
    const cls = (el.className && typeof el.className === 'string') ? el.className : '';
    if (!/iIFiN/.test(cls)) continue;
    const r = el.getBoundingClientRect();
    if (r.width >= 10 && r.width <= 90 && r.height >= 10 && r.height <= 60) {
      return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
              w: Math.round(r.width), h: Math.round(r.height), how: 'class-iIFiN'};
    }
  }
  const smalls = Array.prototype.slice.call(document.querySelectorAll('div,span,i,svg,button'));
  for (const el of smalls) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.width > 60 || r.height > 60) continue;
    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
    if (cx < 700 || cx > 1150 || cy < 250 || cy > 380) continue;
    const own = (el.childElementCount > 0) ? '' : (el.innerText || el.textContent || '').trim();
    if (own) continue;
    return {x: Math.round(cx), y: Math.round(cy), w: Math.round(r.width),
            h: Math.round(r.height), how: 'zone-small'};
  }
  return null;
}"""

_DIALOG_ROWS_JS = r"""() => {
  // 1) 先定位「自定义指标」弹窗容器(标题含 自定义指标 的 arco-modal / [role=dialog]);
  //    只在其内部扫指标行 —— 页面其它区域的同名文本(KPI 卡子标签,如 '成交金额Top1')
  //    不是弹窗选项,绝不参与 reconcile(否则会误点关弹窗)。
  let scope = null;
  const modalCss = '.arco-modal, [class*=modal], [role=dialog]';
  const cands = Array.prototype.slice.call(document.querySelectorAll(modalCss));
  for (const c of cands) {
    const txt = (c.innerText || '').replace(/[\s\u00a0]+/g, '');
    if (txt.includes('自定义指标')) { scope = c; break; }
  }
  if (!scope) {
    // 标题文本叶子 → 向上找 modal 容器(降级)
    const all = Array.prototype.slice.call(document.querySelectorAll('div,span,p'));
    for (const el of all) {
      if (el.childElementCount > 0) continue;
      if ((el.innerText || el.textContent || '').trim() !== '自定义指标') continue;
      const anc = el.closest ? el.closest(modalCss) : null;
      if (anc) { scope = anc; break; }
    }
  }
  if (!scope) return [];
  const els = Array.prototype.slice.call(scope.querySelectorAll('div,span,li,label,p'));
  const out = []; const seen = new Set();
  for (const el of els) {
    if (el.childElementCount > 0) continue;
    const t = (el.innerText || el.textContent || '').replace(/[\s\u00a0]+/g, ' ').trim();
    if (!t || t.length > 30) continue;
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    // 指标名特征:含 千次/GPM/观看/看播/在线/进入/离开/点赞/评论/成交/订单/互动/曝光 等
    if (!/(千次|GPM|直播间|观看|看播|在线|进入|离开|点赞|评论|成交|订单|互动|曝光|当前)/.test(t)) continue;
    // 勾选态:先取自身所在行(LABEL.arco-checkbox 优先),读 input.checked / aria / class
    let checked = null;
    let cur = el;
    for (let up = 0; up < 5 && cur; up++) {
      const row = cur.parentElement;
      if (!row) break;
      const aria = row.getAttribute && row.getAttribute('aria-checked');
      if (aria !== null && aria !== undefined && aria !== '') { checked = aria === 'true'; break; }
      const cls = (row.className && typeof row.className === 'string') ? row.className : '';
      const isArco = row.tagName === 'LABEL' || /arco-checkbox/.test(cls);
      const input = row.querySelector && row.querySelector('input[type=checkbox]');
      if (isArco || input) {
        if (input && input.checked !== undefined) { checked = !!input.checked; }
        else if (/(?:^|[\s-])(checked|selected|active)/.test(cls)) { checked = true; }
        if (checked !== null) break;
      }
      cur = row;
    }
    const key = t + '|' + Math.round(r.x) + '|' + Math.round(r.y);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({text: t.slice(0, 30), x: Math.round(r.x + r.width / 2),
              y: Math.round(r.y + r.height / 2), left: Math.round(r.x),
              top: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height),
              checked: checked});
    if (out.length >= 80) break;
  }
  return out;
}"""

_CONFIRM_JS = r"""() => {
  // 1) 叶子文本精确命中(span/div/button/p 文本 == 确定/保存/完成)
  const els = Array.prototype.slice.call(document.querySelectorAll('div,span,button,p'));
  const want = ['确定', '保存', '完成'];
  for (const el of els) {
    if (el.childElementCount > 0) continue;
    const t = (el.innerText || el.textContent || '').trim();
    if (!want.includes(t)) continue;
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
            text: t, how: 'leaf-text'};
  }
  // 2) 回退:主按钮(primary)容器含 确定 文本 → 点按钮中心(弹窗重渲染/结构变化时仍可命中)
  for (const el of els) {
    if (el.tagName !== 'BUTTON') continue;
    const cls = (el.className && typeof el.className === 'string') ? el.className : '';
    if (!/(?:btn-primary|primary)/i.test(cls)) continue;
    const t = (el.innerText || el.textContent || '').replace(/[\s\u00a0]+/g, '');
    if (!/^(确定|保存|完成)$/.test(t)) continue;
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2),
            text: t.slice(0, 4), how: 'btn-primary'};
  }
  return null;
}"""

# 图例芯片带扫描:弹窗确定后,趋势图上方图例行(文本叶子)的指标标签。
# 每个标签 = 一个可点按芯片(点按切换该曲线显隐,即 RMI 数据组是否推送);
# 记录文本/中心坐标/开关态(开关态仅由 aria 或容器 class 判定,不可判=unknown,保守不点)。
_CHIP_SCAN_JS = r"""() => {
  const BAND = {ymin: 318, ymax: 375, xmin: 150, xmax: 895};  // 与 python CHIP_BAND 对齐
  const els = Array.prototype.slice.call(document.querySelectorAll('span,div'));
  const out = []; const seen = new Set();
  for (const el of els) {
    if (el.childElementCount > 0) continue;
    const t = (el.innerText || el.textContent || '').replace(/[\s\u00a0]+/g, ' ').trim();
    if (!t || t.length > 24) continue;
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
    if (cy < BAND.ymin || cy > BAND.ymax || cx < BAND.xmin || cx > BAND.xmax) continue;
    // 只收 指标类 芯片文本(商品/视频/营销/违规 popper 在 y≈300,不在带内;KPI 卡 y<200)
    if (!/(直播间|千次观看成交金额|成交金额|成交订单数|在线人数|进入人数|离开人数|点赞次数|GPM)/.test(t)) continue;
    const key = t + '|' + Math.round(r.x) + '|' + Math.round(r.y);
    if (seen.has(key)) continue; seen.add(key);
    // 开关态:aria(checked/pressed/selected)优先,其次容器 class(ON:active/selected/checked/on;
    // OFF:off/disabled/inactive/dim);均无 → unknown(保守不点按,避免误关已显示曲线)
    let state = null, how = 'unknown';
    let cur = el;
    for (let up = 0; up < 5 && cur; up++) {
      const row = cur.parentElement; if (!row) break;
      for (const attr of ['aria-checked', 'aria-pressed', 'aria-selected']) {
        const v = row.getAttribute && row.getAttribute(attr);
        if (v !== null && v !== undefined && v !== '') { state = (v === 'true'); how = attr; break; }
      }
      if (state !== null) break;
      const cls = (row.className && typeof row.className === 'string') ? row.className : '';
      if (!/(radio-select-item|legend|chip|indicator)/i.test(cls)) { cur = row; continue; }
      if (/active|selected|checked|on/i.test(cls)) { state = true; how = 'class:' + cls.slice(0, 60); break; }
      if (/off|disabled|inactive|dim|hide/i.test(cls)) { state = false; how = 'class:' + cls.slice(0, 60); break; }
      cur = row;
    }
    out.push({text: t.slice(0, 24), x: Math.round(cx), y: Math.round(cy),
              left: Math.round(r.x), top: Math.round(r.y), w: Math.round(r.width),
              h: Math.round(r.height),
              state: state === null ? null : (state ? 'on' : 'off'), how: how});
    if (out.length >= 40) break;
  }
  return out;
}"""


def _replay_url(cfg: dict, room_id: str) -> str:
    base = cfg.get("probe", {}).get("base_url", "https://eos.douyin.com/dp/liveScreen")
    return f"{base}?room_id={room_id}&tab=trend"


# 弹窗指标名 → 语义别名(勾选态识别与去重用)
_METRIC_ALIASES = {
    "直播间观看量": "直播间观看量",
    "直播间看播量": "直播间观看量",
    "千次观看成交金额": "千次观看成交金额",
    "直播间成交金额": "成交金额",
    "成交金额": "成交金额",
    "成交订单数": "成交订单数",
    "在线人数": "在线人数",
    "进入人数": "进入人数",
    "离开人数": "离开人数",
    "点赞次数": "点赞次数",
    "直播间自然观看量": "直播间自然观看量",
}


def _norm_metric(text: str) -> Optional[str]:
    """把弹窗行文本归一为指标名;未识别返回 None(不参与勾选管理)。"""
    for key in sorted(_METRIC_ALIASES, key=len, reverse=True):
        if key in text:
            return _METRIC_ALIASES[key]
    return None


def plan_metric_changes(rows_seen: List[Dict[str, Any]], keep: Optional[List[str]] = None
                        ) -> Dict[str, Any]:
    """给定弹窗可见行({text,checked?}),规划 取消/勾选 动作,满足:
       - 保留集合 ⊆ 弹窗选项且 gmv(成交金额)必在保留集合;
       - 同屏总勾选数 ≤ MAX_CHART_METRICS。
    返回 {desired, to_uncheck:[文本], to_check:[文本], ok, reason}。纯函数,离线可测。
    """
    keep_list = list(keep) if keep else list(DEFAULT_KEEP_METRICS)
    if REQUIRED_METRIC not in keep_list:
        keep_list.insert(0, REQUIRED_METRIC)
    keep_set = {_norm_metric(k) for k in keep_list if _norm_metric(k)}
    # 弹窗选项(去重后带勾选态;勾选态未知的行不强行取消——保守起见仅取消已确认勾选且不在 keep 的)
    seen: Dict[str, Dict[str, Any]] = {}
    for row in rows_seen:
        norm = _norm_metric(str(row.get("text") or ""))
        if not norm or norm in seen:
            continue
        seen[norm] = row
    option_names = set(seen)
    # keep 中弹窗没有的项:无法勾选 → 仍应尽量保留(报告),但最终以“勾选 ≤6 且 gmv 在”为准
    missing = sorted(keep_set - option_names)
    # 取消:已勾选且不在 keep 的行(需知道勾选态);若勾选态未知则无法安全取消 → 报告
    to_uncheck = []
    uncheck_unknown = []
    for norm, row in seen.items():
        if norm in keep_set:
            continue
        chk = row.get("checked")
        if chk is True:
            to_uncheck.append(norm)
        elif chk is None:
            uncheck_unknown.append(norm)
    to_check = [n for n in sorted(keep_set) if n in option_names
                and seen[n].get("checked") is not True]
    total_after = len(keep_set & option_names)
    ok = total_after <= MAX_CHART_METRICS and REQUIRED_METRIC in option_names
    reason = ""
    if not ok:
        reason = (f"无法满足约束:保留集合在弹窗可见 {total_after} 项(上限 "
                  f"{MAX_CHART_METRICS});gmv(成交金额)在弹窗可见 = "
                  f"{REQUIRED_METRIC in option_names}")
    elif uncheck_unknown:
        reason = (f"存在勾选态未知的未保留项 {sorted(uncheck_unknown)},脚本不强行取消,"
                  "图表可能仍 >6 → 将转 assist 人工确认")
        ok = False
    return {"desired": sorted(keep_set & option_names), "to_uncheck": to_uncheck,
            "to_check": to_check, "ok": ok, "reason": reason,
            "missing_in_dialog": missing,
            "checked_unknown_rows": sorted(uncheck_unknown),
            "max_metrics": MAX_CHART_METRICS, "required_metric": REQUIRED_METRIC}


def plan_chip_clicks(chips_seen: List[Dict[str, Any]], *,
                     watch_metric: str = WATCH_CHIP_METRIC,
                     optional: Optional[List[str]] = None,
                     anchor: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    """弹窗「确定」后,自动点按图例芯片使 watch 曲线显示 的决策(纯函数,离线可测)。

    输入为 _CHIP_SCAN_JS 的产物:图表上方芯片行元素 {text, x, y, state('on'|'off'|None)}。
    规则(保守,与弹窗 reconcile 同一纪律):
      - 只认 _norm_metric 能归一的目标芯片(watch + optional),其它指标芯片(含 gmv 外的
        成交订单数/在线人数… )一律不点按;
      - 只点按开关态**明确为 off**(曲线隐藏)的目标芯片使其 ON;state 未知不点按
        (避免把已显示的曲线误关);state on 不需动作;
      - 成交金额(gmv 分子)芯片永不点按;若其呈 off 记录 forbidden_off 并判 ok=False
        (异常,转 assist,不冒险);
      - 文本定位失败(watch 芯片缺失)→ watch_missing=True;若提供 anchor(坐标标定)则
        记录之,由调用方单次点按(不循环);
      - 点按数上限 CHIP_MAX_CLICKS;超过即 ok=False。
    返回 {found:[{metric,x,y,state,how}], clicks:[{metric,x,y}], watch_missing: bool,
          on_already:[], unknown_state:[], missing:[], forbidden_off:[], anchor,
          cap, ok: bool, reason: str}。ok = watch 芯片在场(态 on/off)且无 forbidden_off
          且 clicks ≤ cap。
    """
    watch_norm = _norm_metric(watch_metric) or watch_metric
    opt_norms = []
    for m in (optional or OPTIONAL_CHIP_METRICS):
        n = _norm_metric(m)
        if n and n not in opt_norms:
            opt_norms.append(n)
    # 归一 + 去重(别名行,如 直播间看播量 → 直播间观看量,取先出现者)
    rows_by_norm: Dict[str, Dict[str, Any]] = {}
    found: List[Dict[str, Any]] = []
    for row in chips_seen:
        norm = _norm_metric(str(row.get("text") or ""))
        if not norm or norm in rows_by_norm:
            continue
        rows_by_norm[norm] = row
        found.append({"metric": norm, "x": row.get("x"), "y": row.get("y"),
                      "state": row.get("state"), "how": row.get("how") or ""})
    clicks: List[Dict[str, Any]] = []
    on_already: List[str] = []
    unknown_state: List[str] = []
    missing: List[str] = []
    for norm in [watch_norm] + [o for o in opt_norms if o != watch_norm]:
        row = rows_by_norm.get(norm)
        if row is None:
            missing.append(norm)
            continue
        st = row.get("state")
        if st == "on":
            on_already.append(norm)
        elif st == "off":
            if len(clicks) < CHIP_MAX_CLICKS:
                clicks.append({"metric": norm, "x": row.get("x"), "y": row.get("y")})
            else:
                missing.append(norm)  # 超过上限的 off 目标不点按,归入说明
        else:
            unknown_state.append(norm)
    forbidden_off = [n for n, row in rows_by_norm.items()
                     if n == CHIP_FORBIDDEN_TOGGLE and row.get("state") == "off"]
    watch_row = rows_by_norm.get(watch_norm)
    watch_actionable = watch_row is not None and watch_row.get("state") in ("on", "off")
    ok = bool(watch_actionable and not forbidden_off and len(clicks) <= CHIP_MAX_CLICKS)
    reason = (f"watch={watch_row.get('state') if watch_row else 'missing'}; "
              f"clicks={[c['metric'] for c in clicks]}; on={on_already}; "
              f"unknown={unknown_state}; missing={missing}; forbidden_off={forbidden_off}")
    if not watch_actionable:
        reason += "; watch 芯片缺失或开关态不可判 → 保守不盲点"
    elif forbidden_off:
        reason += f"; {REQUIRED_METRIC} 芯片呈 off(异常,不点按,转 assist)"
    return {"found": found, "clicks": clicks, "watch_missing": watch_row is None,
            "on_already": on_already, "unknown_state": unknown_state, "missing": missing,
            "forbidden_off": forbidden_off, "anchor": anchor, "cap": CHIP_MAX_CLICKS,
            "ok": ok, "reason": reason}


def chip_assist_hint(plan: Dict[str, Any]) -> str:
    """auto 芯片步骤无法闭环时的兜底提示(纯函数):请人工点一次图例芯片。"""
    if plan.get("ok"):
        return ""
    bits = []
    if plan.get("watch_missing"):
        bits.append("图表上方未见「直播间观看量/直播间看播量」图例芯片(弹窗勾选可能未生效)")
    elif plan.get("unknown_state"):
        bits.append("「直播间观看量」芯片开关态无法识别,脚本保守未自动点按(避免误隐藏曲线)")
    if plan.get("forbidden_off"):
        bits.append(f"「{REQUIRED_METRIC}」芯片呈隐藏态(异常,分子必须保留),脚本不会点按它")
    if not bits:
        return ""
    base = ("请在图表上方图例区:若「直播间观看量/直播间看播量」曲线未显示(芯片灰显),"
            "人工点击该芯片一次使其曲线显示,脚本会继续在同一窗口轮询捕获")
    return base + "(" + "；".join(bits) + ")"


def decide_watch_chip_fallback(chip_plan: Dict[str, Any], *, still_absent: bool,
                               used: int = 0) -> str:
    """auto 芯片安全点按/轮询后 watch 仍缺席时的处置决策(纯函数,离线可测)。

    背景:图例芯片点按=切换曲线显隐;开关态无法从 DOM 判读(unknown)时保守不自动点;
    但若已文本定位 watch 芯片且 watch 序列仍缺席,做**一次知情点按**(非盲点坐标,
    仍缺席即转 assist,不循环)。
    返回:
      - 'click-watch-once':watch 芯片已文本定位(态非 on)且仍缺席且未用过回退 → 单次点按;
      - 'assist':watch 芯片文本缺失(无法定位)/ 已点过一次仍缺席 → 人工兜底;
      - 'noop':watch 已就绪(无需回退)。
    """
    if not still_absent:
        return "noop"
    watch_row = next((f for f in (chip_plan.get("found") or [])
                      if f.get("metric") == WATCH_CHIP_METRIC), None)
    if watch_row is None or watch_row.get("state") == "on":
        return "assist" if watch_row is None else "noop"
    return "click-watch-once" if used == 0 else "assist"


def _resolve_chip_anchor(cfg: dict) -> Optional[Dict[str, int]]:
    """读取 config export.watch_capture_chip_anchor({x,y}),文本定位失败时的坐标标定。"""
    a = ((cfg.get("export") or {}).get("watch_capture_chip_anchor") or {})
    try:
        x, y = int(a["x"]), int(a["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= x <= 2000 and 0 <= y <= 2000):
        return None
    return {"x": x, "y": y}


def _resolve_keep_metrics(cfg: dict) -> List[str]:
    """解析配置的保留指标集合(默认 DEFAULT_KEEP_METRICS;gmv 成交金额必在首位)。"""
    raw = (cfg.get("export", {}) or {}).get("watch_capture_keep_metrics")
    if isinstance(raw, list) and raw:
        keep = [str(x) for x in raw if str(x).strip()]
    else:
        keep = list(DEFAULT_KEEP_METRICS)
    if REQUIRED_METRIC not in keep:
        keep = [REQUIRED_METRIC] + keep
    return keep


def _now_utc() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def capture_watch_minutes(cfg: dict, room_id: str, session_date: str,
                          out_dir: Optional[pathlib.Path] = None,
                          *, tag: str = "watch_capture",
                          auto: bool = True, poll_seconds: int = 60) -> Dict[str, Any]:
    """回放页自动捕获直播间观看量分钟序列并写观看档(只读;失败 not-ok 不抛)。

    返回 {ok, watch_file?, evidence_file?, semantics?, rows, closure, reason?, assist_steps?}。
    auto=True 先尝试 齿轮→自定义指标→勾选→确定;确定后 WatchCntTrend 未即返回时自动点按
    图例芯片使其曲线显示(t13);仍失败则填 assist_steps 供人工一次配合(含“点一次芯片”兜底)。
    """
    import auth

    ymd = str(session_date).replace("-", "")
    url = _replay_url(cfg, room_id)
    result: Dict[str, Any] = {"ok": False, "room_id": room_id, "session_date": session_date,
                              "captured_at_utc": _now_utc()}
    try:
        pw, context, page = auth.open_session(cfg, headful=True)
    except SystemExit:
        raise
    except Exception as exc:
        result["reason"] = f"打开浏览器失败: {exc}(先 python main.py login)"
        return result

    try:
        if not auth.ensure_logged_in(cfg, context, page):
            result["reason"] = "无登录会话(先 python main.py login)"
            return result
        cap = _gp._Capture()
        page.on("response", cap.on_response)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        for _ in range(8):
            page.wait_for_timeout(5000)
            if page.evaluate("() => (document.body && document.body.innerText || '').length > 300"):
                break
        cap.read_bodies()

        # ---- 齿轮 → 自定义指标 → ≤6 勾选(保留 gmv)→ 确定(自动;点击至多重试 1 次) ----
        assist_steps = []
        enabled = False
        series: Dict[str, Dict[str, Any]] = {}
        if auto:
            # 1) 先确认页面是否已含 WatchCntTrend(用户此前勾选状态可能仍在会话内)
            cap.read_bodies()
            _gp._collect_series_from_bodies(cap.bodies, series)
            if "WatchCntTrend" in series:
                enabled = True
                result["enabled_via"] = "already-present(会话内已勾选)"
                print("[watch_capture] 会话内已含 WatchCntTrend,直接进入轮询。")
            else:
                # 2) 齿轮点击(至多尝试 2 次=首次+1 次重试;命中弹窗即停,不无限循环)
                gear_cands = []
                rows = []
                for _try in range(2):
                    g = page.evaluate(_GEAR_JS)
                    if g:
                        gear_cands.append(g)
                        page.mouse.click(g["x"], g["y"])
                        page.wait_for_timeout(3000)
                        rows = page.evaluate(_DIALOG_ROWS_JS) or []
                        has_dialog = any("自定义" in (d.get("text") or "") for d in rows) or len(rows) > 0
                        if has_dialog:
                            print(f"[watch_capture] 齿轮命中尝试 {_try + 1}: {g} (弹窗行 {len(rows)})")
                            break
                        # 关闭可能误开的层再试一次
                        page.mouse.click(700, 700)
                        page.wait_for_timeout(800)
                result["gear_attempts"] = len(gear_cands)
                # 弹窗选项在页面中部偏下(y>=220):排除顶部 KPI 文本(y≈88-190)与
                # 图表上方的图例芯片带(y≈318-375 内是芯片非弹窗行,t13 修复)
                rows = [d for d in rows
                        if d.get("top", 0) >= 220
                        and not (CHIP_BAND["y_min"] <= d.get("top", 0) + (d.get("h") or 0) / 2
                                 <= CHIP_BAND["y_max"]
                                 and CHIP_BAND["x_min"] <= d.get("x", 0) <= CHIP_BAND["x_max"])]
                result["dialog_rows_seen"] = rows
                if rows and not enabled:
                    keep = _resolve_keep_metrics(cfg)
                    plan = plan_metric_changes(rows, keep=keep)
                    result["metric_plan"] = plan
                    if not plan["ok"]:
                        assist_steps.append(f"指标规划不满足约束({plan['reason']});请人工在弹窗内保留 "
                                            f"{REQUIRED_METRIC} 且总勾选 ≤{MAX_CHART_METRICS},"
                                            f"并勾选 直播间观看量/直播间看播量 后点确定")
                    else:
                        # 3a) 先取消多余项(已确认勾选且不在保留集合)
                        for norm in plan["to_uncheck"]:
                            row = next((d for d in rows if _norm_metric(d.get("text") or "") == norm), None)
                            if row:
                                page.mouse.click(row["left"] + 12, row["top"] + row["h"] // 2)
                                page.wait_for_timeout(500)
                        # 3b) 再勾选目标项(保留集合中未勾选者)
                        for norm in plan["to_check"]:
                            row = next((d for d in rows if _norm_metric(d.get("text") or "") == norm), None)
                            if row:
                                page.mouse.click(row["left"] + 12, row["top"] + row["h"] // 2)
                                page.wait_for_timeout(500)
                        result["metric_actions"] = {"uncheck": plan["to_uncheck"],
                                                    "check": plan["to_check"]}
                        # 勾选动作后等重渲染稳定,再找「确定」(一次重扫兜底)
                        page.wait_for_timeout(1200)
                        confirm = page.evaluate(_CONFIRM_JS)
                        if not confirm:
                            page.wait_for_timeout(2000)
                            confirm = page.evaluate(_CONFIRM_JS)
                        if confirm:
                            page.mouse.click(confirm["x"], confirm["y"])
                            page.wait_for_timeout(6000)
                            cap.read_bodies()
                            _gp._collect_series_from_bodies(cap.bodies, series)
                            enabled = "WatchCntTrend" in series
                            result["enabled_via"] = ("auto ≤6 reconcile→确定" if enabled
                                                     else "auto(已点确定,待确认)")
                            if not enabled:
                                # ---- t13:确定后自动点按图例芯片,使 watch 曲线真实显示 ----
                                anchor_cfg = _resolve_chip_anchor(cfg)
                                chips = page.evaluate(_CHIP_SCAN_JS) or []
                                chip_plan = plan_chip_clicks(chips, anchor=anchor_cfg)
                                result["chip_scan"] = {"band": CHIP_BAND,
                                                       "chips_seen": chips}
                                result["chip_plan"] = chip_plan
                                clicked: List[str] = []
                                # 1) 文本定位命中且目标芯片明确 off → 点按使其显示(安全集不含 gmv)
                                for c in chip_plan["clicks"][:CHIP_MAX_CLICKS]:
                                    page.mouse.click(c["x"], c["y"])
                                    page.wait_for_timeout(1200)
                                    clicked.append(c["metric"])
                                result["chip_clicks_auto"] = clicked
                                # 2) 文本未定位到 watch 芯片 → 单次坐标标定(须已配置锚点)并记录
                                if chip_plan["watch_missing"] and chip_plan["anchor"]:
                                    a = chip_plan["anchor"]
                                    page.mouse.click(a["x"], a["y"])
                                    result["chip_anchor_used"] = a
                                    clicked.append("chip-anchor")
                                    page.wait_for_timeout(4000)
                                if clicked:
                                    cap.read_bodies()
                                    _gp._collect_series_from_bodies(cap.bodies, series)
                                    enabled = "WatchCntTrend" in series
                                # 3) 至多 CHIP_MAX_RETRIES 次重扫/点按(不循环开窗、不无限点按)
                                retry_n = 0
                                while (not enabled and clicked
                                       and retry_n < CHIP_MAX_RETRIES):
                                    retry_n += 1
                                    chips2 = page.evaluate(_CHIP_SCAN_JS) or []
                                    plan2 = plan_chip_clicks(chips2, anchor=anchor_cfg)
                                    again = [c for c in plan2["clicks"]
                                             if c["metric"] in clicked
                                             or c["metric"] == WATCH_CHIP_METRIC]
                                    if not again:
                                        break
                                    page.mouse.click(again[0]["x"], again[0]["y"])
                                    page.wait_for_timeout(4000)
                                    cap.read_bodies()
                                    _gp._collect_series_from_bodies(cap.bodies, series)
                                    enabled = "WatchCntTrend" in series
                                result["chip_retries"] = retry_n
                                # 4) 兜底:仍无 watch 序列且已文本定位 watch 芯片(态非 on)
                                #    → 单次知情点按使曲线显示(不循环;仍无则转 assist)
                                chip_fb_used = False
                                if not enabled:
                                    # watch 芯片此前未被自动点按过才做单次知情点按(不叠加开关)
                                    fb = decide_watch_chip_fallback(
                                        chip_plan, still_absent=True,
                                        used=1 if (clicked or retry_n > 0) else 0)
                                    if fb == "click-watch-once":
                                        watch_row = next(
                                            (f for f in chip_plan["found"]
                                             if f["metric"] == WATCH_CHIP_METRIC), None)
                                        if (watch_row and watch_row.get("x") is not None
                                                and watch_row.get("y") is not None):
                                            page.mouse.click(watch_row["x"], watch_row["y"])
                                            result["chip_fallback_click"] = watch_row
                                            chip_fb_used = True
                                            page.wait_for_timeout(5000)
                                            cap.read_bodies()
                                            _gp._collect_series_from_bodies(cap.bodies, series)
                                            enabled = "WatchCntTrend" in series
                                if not enabled and chip_fb_used:
                                    assist_steps.append(
                                        "已自动点按一次「直播间观看量」图例芯片但仍未捕获:"
                                        "请在图表上方再人工点一次该芯片/确认弹窗勾选后确定"
                                        "(单窗口,不再自动循环)")
                                if enabled and (clicked or chip_fb_used):
                                    result["enabled_via"] = "auto ≤6 reconcile→确定→chip(曲线显示)"
                                if not enabled:
                                    # 已点确定但未捕获 → 短确认窗口(≤20s)后仍无则停止并上报(不长时间等人工)
                                    for _ in range(max(1, min(poll_seconds, 20) // 4)):
                                        cap.read_bodies()
                                        _gp._collect_series_from_bodies(cap.bodies, series)
                                        if "WatchCntTrend" in series:
                                            enabled = True
                                            break
                                        page.wait_for_timeout(4000)
                                    if not enabled:
                                        assist_steps.append(
                                            "已点确定但仍未捕获 WatchCntTrend:请人工确认弹窗内 直播间观看量/看播量 "
                                            "已勾选且总指标 ≤6(含成交金额)后点确定")
                                        hint = chip_assist_hint(chip_plan)
                                        if hint:
                                            assist_steps.append(hint)
                        else:
                            assist_steps.append("自定义指标弹窗已开但未找到「确定」:请人工点弹窗底部确定")
                elif not rows and not enabled:
                    assist_steps.append("点击齿轮后未在 DOM 发现指标选项文本:请人工确认是否已弹出"
                                        "「自定义指标」并保留 ≤6 项(含成交金额)且勾选 直播间观看量/看播量,"
                                        "再点确定")
        else:
            assist_steps.append("--assist 模式:请在已打开的页面点击 趋势图右上齿轮 → 自定义指标 →"
                                f"先确保保留 {REQUIRED_METRIC}(gmv 分子)且总勾选 ≤{MAX_CHART_METRICS},"
                                "再勾选 直播间观看量/直播间看播量 → 确定")

        # ---- 轮询 WatchCntTrend(整场行) ----
        # 规则:captain 授权的受控单窗口(auto + assist 兜底)= 若 auto reconcile 失败,
        # 保持同一窗口并轮询至 poll_seconds 等待用户人工勾选(不循环重开窗口);
        # 若 auto 成功(无 assist 步骤)则按 poll_seconds 收口;无 assist 步骤的纯失败尽快返回。
        poll_note = ""
        if not enabled:
            # auto 失败但产生了 assist 步骤 → 保持窗口等人工(单窗口,授权场景);
            # 无 assist 步骤的 auto 失败 → 短确认后尽快返回上报,不长时间空等。
            if assist_steps:
                print("[watch_capture] 等待人工协助(窗口保持,≤%ss): " % poll_seconds
                      + "; ".join(assist_steps))
                poll_note = "assist-await"
                deadline = _time.time() + poll_seconds
            else:
                deadline = _time.time() + min(poll_seconds, 20)
        else:
            deadline = _time.time() + poll_seconds
        while _time.time() < deadline:
            cap.read_bodies()
            _gp._collect_series_from_bodies(cap.bodies, series)
            if "WatchCntTrend" in series:
                enabled = True
                break
            if assist_steps and not poll_note:
                print("[watch_capture] 等待人工协助(窗口保持,≤%ss): " % poll_seconds
                      + "; ".join(assist_steps))
                poll_note = "assist-await"
            page.wait_for_timeout(4000)
        if "WatchCntTrend" not in series:
            result["reason"] = ("轮询期内未捕获到 WatchCntTrend"
                                + ("(auto reconcile 失败,单窗口等待人工后仍未完成)"
                                   if assist_steps else "(auto 失败即停止,上报 captain)"))
            result["assist_steps"] = assist_steps or [
                "请在页面手动:齿轮 → 自定义指标 → 保留 成交金额 且总勾选 ≤6,勾选 直播间观看量/看播量 "
                "→ 确定,然后重跑本捕获(--assist --poll-seconds 120)"]
            result["series_keys_seen"] = sorted(series)
            return result

        # ---- 组装观看档(与 gmv 同分钟轴) ----
        rows = _gp._minute_rows_for_key(cap.bodies, "WatchCntTrend") or []
        watch_rows = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            x = str(r.get("x") or "").strip()
            y = r.get("y")
            if not x:
                continue
            watch_rows.append({"time": x, "views": y})
        stats = series["WatchCntTrend"].get("stats") or {}
        total = stats.get("value_sum")
        watch_sum = round(float(total), 4) if isinstance(total, (int, float)) else None

        # 同刻 key_index.ServerWatchCntTd
        swcnt = None
        for b in cap.bodies:
            j = b.get("json")
            if not isinstance(j, dict) or "key_index" not in (b.get("url") or ""):
                continue
            d = j.get("data")
            if isinstance(d, dict) and isinstance(d.get("ServerWatchCntTd"), dict):
                v = d["ServerWatchCntTd"].get("value")
                if isinstance(v, (int, float)):
                    swcnt = float(v)
        diff = None
        if isinstance(watch_sum, (int, float)) and isinstance(swcnt, (int, float)):
            diff = round(watch_sum - swcnt, 4)
        semantics = SEMANTICS_TEMPLATE.format(sum=watch_sum, card=swcnt, diff=diff)

        # 写观看档
        out_base = out_dir if out_dir is not None else pathlib.Path(
            cfg.get("export", {}).get("out_dir", "data/outputs"))
        out_dir_p = pathlib.Path(out_base)
        if not out_dir_p.is_absolute():
            out_dir_p = cfgmod.PROJECT_ROOT / out_dir_p
        out_dir_p.mkdir(parents=True, exist_ok=True)
        watch_path = out_dir_p / f"live_{ymd}_{room_id}{WATCH_MIN_SUFFIX}"
        with watch_path.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "views", "semantics"])
            for r in watch_rows:
                w.writerow([r["time"], r["views"], semantics])

        # 证据 JSON
        evidence_dir = pathlib.Path(cfg.get("probe", {}).get("output_dir", "data/probe"))
        if not evidence_dir.is_absolute():
            evidence_dir = cfgmod.PROJECT_ROOT / evidence_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence_path = evidence_dir / f"{tag}_{room_id}_{_ts()}.json"
        evidence = {
            "schema_version": "watch-capture-1.0",
            "probe_meta": {"room_id": room_id, "session_date": session_date, "url": url,
                           "captured_at_utc": _now_utc(),
                           "mode": "replay auto gear→自定义指标→确定→chip(曲线显示)"
                           if enabled and (result.get("chip_clicks_auto")
                                           or result.get("chip_anchor_used")
                                           or result.get("chip_fallback_click"))
                           else ("replay auto gear→自定义指标→确定"
                                 if enabled else "replay (assist/已勾选状态)"),
                           "enabled_via": result.get("enabled_via")},
            "series": "WatchCntTrend(直播间观看量/直播间看播量)",
            "rows": len(watch_rows),
            "time_first": watch_rows[0]["time"] if watch_rows else None,
            "time_last": watch_rows[-1]["time"] if watch_rows else None,
            "watch_sum": watch_sum,
            "key_index_ServerWatchCntTd": swcnt,
            "diff_sum_minus_card": diff,
            "chip_plan": result.get("chip_plan"),
            "chip_clicks_auto": result.get("chip_clicks_auto"),
            "chip_anchor_used": result.get("chip_anchor_used"),
            "chip_retries": result.get("chip_retries"),
            "chip_fallback_click": result.get("chip_fallback_click"),
            "note": "整场闭合复核:Σ WatchCntTrend vs 同刻 ServerWatchCntTd;diff 成因(首尾分钟"
                    "边界/统计口径)见 docs/GPM-观看次数口径结论.md §4d/t12;如实标注不冒充精确。"
                    "t13:确定后若序列未即返回,自动点按图例芯片使 直播间观看量 曲线显示"
                    "(文本定位优先;无法文本定位且配置锚点则坐标一次并记录;不可判不盲点)。",
            "watch_file": str(watch_path),
        }
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

        result.update({"ok": True, "watch_file": str(watch_path),
                       "evidence_file": str(evidence_path), "semantics": semantics,
                       "rows": watch_rows, "closure": {
                           "sum": watch_sum, "ServerWatchCntTd": swcnt, "diff": diff},
                       "assist_steps": assist_steps or []})
        return result
    except Exception as exc:  # noqa: BLE001 — 捕获失败不阻断调用方
        result["reason"] = f"捕获执行异常: {exc}"
        return result
    finally:
        try:
            context.close()
        finally:
            pw.stop()


def dry_run(cfg: dict, room_id: str) -> int:
    sample_url = _replay_url(cfg, room_id or "7000000000000000001")
    print("[watch_capture] DRY-RUN:不会打开浏览器、不采集任何页面数据。")
    print(f"[watch_capture] 目标(已结束场次回放页): {sample_url}")
    print("[watch_capture] 流程:")
    print("  1) 复用登录 profile 有头打开回放页;被动捕获 room_minute_indicator/key_index;")
    print("  2) DOM 定位趋势图右上齿轮(DIV.iIFiN 或区域扫描)→ 点击打开「自定义指标」弹窗;")
    print(f"  3) 按约束 {REQUIRED_METRIC}(gmv)必留且总勾选 ≤{MAX_CHART_METRICS}:自动先取消多余项,"
          "再勾选 直播间观看量/直播间看播量(WatchCntTrend)→ 点「确定」;")
    print("  4) 确定后若 WatchCntTrend 未即返回:自动点按趋势图上方图例芯片(直播间观看量/")
    print("     直播间看播量,必要时 千次观看成交金额)使曲线真实显示 —— 文本定位优先;")
    print("     无法文本定位且配置 export.watch_capture_chip_anchor 时坐标标定一次并记录;")
    print("     芯片开关态不可判时不盲点,仍失败转 assist 人工点一次;")
    print("  5) 轮询至 WatchCntTrend 整场分钟序列出现(默认 ≤60s),同刻读 key_index;")
    print("  6) 写观看档 live_<ymd>_<room>_watch_min.csv(time,views,semantics,与 gmv 同轴)")
    print("     + 证据 watch_capture_<room>_<ts>.json(Σ vs ServerWatchCntTd/diff/芯片动作/说明);")
    print("  7) 供 export/daily 的 hourly GPM 以真实看播量为分母输出;失败优雅降级。")
    print()
    print("[watch_capture] 上线(真机)步骤:")
    print("  1) python main.py login(有头扫码一次)")
    print("  2) python main.py export --room-id <room_id> --date YYYY-MM-DD --capture-watch")
    print("     或 python main.py daily --date YYYY-MM-DD --capture-watch")
    print("  3) 单独捕获验证: python -m exporter.watch_capture --room-id <room_id>"
          " --date YYYY-MM-DD [--assist]")
    print()
    print("[watch_capture] 边界: 只读本账号数据;复用登录态;不点 商品/营销/违规/广告;样例脱敏;")
    print("             profile/凭据不提交。")
    return 0


def main(argv=None) -> int:
    import argparse
    import sys as _sys
    cfgmod.setup_utf8_io()
    p = argparse.ArgumentParser(prog="watch_capture",
                                description="回放页捕获直播间观看量(WatchCntTrend)分钟序列并写观看档(t12)。")
    p.add_argument("--room-id", default=None)
    p.add_argument("--date", default=None, help="session_date YYYY-MM-DD(写观看档命名)")
    p.add_argument("--config", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--assist", action="store_true", help="不自动点弹窗,输出人工步骤后进入轮询")
    p.add_argument("--poll-seconds", type=int, default=60)
    p.add_argument("--out-dir", default=None)
    args = p.parse_args(argv)
    cfg = cfgmod.load_config(args.config)
    if args.dry_run:
        return dry_run(cfg, args.room_id)
    if not args.room_id or not args.date:
        print("[watch_capture] 需要 --room-id 与 --date(或 --dry-run)。", file=_sys.stderr)
        return 2
    out_dir = pathlib.Path(args.out_dir) if args.out_dir else None
    res = capture_watch_minutes(cfg, args.room_id, args.date, out_dir=out_dir,
                                auto=not args.assist, poll_seconds=args.poll_seconds)
    print(json.dumps({k: res.get(k) for k in ("ok", "reason", "watch_file", "evidence_file",
                                              "semantics", "closure", "assist_steps")},
                     ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 2


if __name__ == "__main__":
    cfgmod.setup_utf8_io()
    raise SystemExit(main())
