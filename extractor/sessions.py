# -*- coding: utf-8 -*-
"""extractor/sessions.py — 按天场次清单(只读,T10)。

复用 T9 实测摸清的账号级只读接口(抖音生活服务/本地直播专业版大屏):
  GET https://eos.douyin.com/data/life/live/room/paged/
      ?start_time=<当日 00:00:00 CST epoch>
      &end_time=<当日 23:59:59 CST epoch>&page_size=50&order=2&scene=1
返回 data.room_infos[]: {room_id, live_id, status(=4 已结束), start_time, end_time,
title, ...};每行=该账号一个直播场次(每场独立 room_id,T9 实测)。

本模块只做:
- 东八区“本地日”epoch 窗口构造与解析(纯函数,可离线测);
- room/paged 真实响应解析(status=4 过滤、room_id/live_id/start_time/end_time 抽取);
- 导出文件名规范 live_<YYYYMMDD>_<room_id>.csv/.xlsx;
- 在线按天拉取(已登录会话内同源只读 fetch,不做绕过);失败给出人工引导,不崩溃不伪造。

边界:只读本账号有权限数据;不收集商品/营销/违规事件时间轴;不做登录绕过/签名逆向。
"""
from __future__ import annotations

import datetime as _dt
import pathlib
from typing import Any, Dict, List, Optional

_CST = _dt.timezone(_dt.timedelta(hours=8))
_PAGED_PATH = "https://eos.douyin.com/data/life/live/room/paged/"
# status=4 表示直播已结束(实测,room/paged)
STATUS_ENDED = 4


class DaySessionsUnavailable(RuntimeError):
    """在线拉取不可用/登录不可用/响应异常;message 内含人工引导。"""


# ---------------------------------------------------------------------------
# 时间窗口与解析(纯函数)
# ---------------------------------------------------------------------------
def parse_date(date_str: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat(str(date_str).strip())
    except ValueError as exc:
        raise ValueError(
            f"日期格式应为 YYYY-MM-DD,得到 {date_str!r}"
        ) from exc


def day_range_epochs(date_str: str) -> tuple:
    """返回东八区当日 [00:00:00, 23:59:59] 的 epoch 秒窗口 (start, end)。"""
    day = parse_date(date_str)
    start_dt = _dt.datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=_CST)
    end_dt = start_dt + _dt.timedelta(days=1) - _dt.timedelta(seconds=1)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def _fmt_cst(epoch: Any) -> str:
    return _dt.datetime.fromtimestamp(int(epoch), tz=_CST).strftime("%Y-%m-%d %H:%M:%S")


def parse_paged_rows(raw_rows: List[Any], *, date_str: Optional[str] = None,
                     strict_day: bool = True) -> List[Dict[str, Any]]:
    """把 room/paged 的 data.room_infos 解析为场次清单。

    - status == 4(已结束)才保留;
    - 抽取 room_id/live_id/start_time/end_time 并派生 start_cst/end_cst/时长(分钟);
    - strict_day=True 时只保留 start_time 落在给定东八区当日的行(防御脏数据)。
    """
    day_start, day_end = (day_range_epochs(date_str) if date_str else (None, None))
    sessions: List[Dict[str, Any]] = []
    for raw in raw_rows or []:
        if not isinstance(raw, dict):
            continue
        status = raw.get("status")
        if status != STATUS_ENDED:
            continue
        room_id = raw.get("room_id")
        start_ts = raw.get("start_time")
        end_ts = raw.get("end_time")
        if room_id is None or start_ts is None:
            continue
        if strict_day and day_start is not None:
            if not (day_start <= int(start_ts) <= day_end):
                continue
        duration_min = None
        if end_ts is not None:
            try:
                duration_min = max(0, int(round((int(end_ts) - int(start_ts)) / 60.0)))
            except (TypeError, ValueError):
                duration_min = None
        sessions.append({
            "room_id": str(room_id),
            "live_id": raw.get("live_id"),
            "status": status,
            "start_epoch": int(start_ts),
            "end_epoch": int(end_ts) if end_ts is not None else None,
            "start_cst": _fmt_cst(start_ts),
            "end_cst": _fmt_cst(end_ts) if end_ts is not None else None,
            "duration_min": duration_min,
            "title": str(raw.get("title") or ""),
        })
    # 按开播时间排序
    sessions.sort(key=lambda s: s["start_epoch"])
    return sessions


def session_output_name(date_str: str, room_id: Any, suffix: str = ".csv") -> str:
    """输出文件名规范: live_<YYYYMMDD>_<room_id>.<ext>。"""
    day = parse_date(date_str)
    return f"live_{day.strftime('%Y%m%d')}_{str(room_id)}{suffix}"


def day_paged_url(date_str: str) -> str:
    start, end = day_range_epochs(date_str)
    return (f"{_PAGED_PATH}?start_time={start}&end_time={end}"
            f"&page_size=50&order=2&scene=1")


# ---------------------------------------------------------------------------
# 在线拉取(需已登录会话;同源只读 fetch)
# ---------------------------------------------------------------------------
def _guidance_text(date_str: str) -> str:
    return (
        f"按天拉取场次需要已登录且对数据有权限。人工引导:"
        f"1) python main.py login(有头扫码,一次性);"
        f"2) python main.py sessions --date {date_str}"
        f" / python main.py export --date {date_str}。"
        f"无登录/网络不可达时本命令不会伪造数据。"
    )


def fetch_day_sessions(cfg: dict, date_str: str) -> List[Dict[str, Any]]:
    """在线拉取指定日期的已结束场次清单(浏览器已登录同源 fetch)。

    失败抛 DaySessionsUnavailable(含人工引导),绝不伪造。
    """
    day = parse_date(date_str)
    day_str = day.strftime("%Y-%m-%d")
    url = day_paged_url(day_str)
    try:
        import auth
        pw, context, page = auth.open_session(cfg, headful=True)
    except SystemExit:
        raise DaySessionsUnavailable(_guidance_text(day_str)) from None
    except Exception as exc:  # noqa: BLE001
        raise DaySessionsUnavailable(f"打开浏览器失败: {exc}\n{_guidance_text(day_str)}") from exc
    try:
        if not auth.ensure_logged_in(cfg, context, page):
            raise DaySessionsUnavailable(
                "无登录会话。\n" + _guidance_text(day_str)
            )
        try:
            page.goto("https://eos.douyin.com/dp/liveScreen", wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:  # noqa: BLE001
            raise DaySessionsUnavailable(
                f"打开 eos 页面失败(网络/权限): {exc}\n{_guidance_text(day_str)}"
            ) from exc
        page.wait_for_timeout(2000)
        payload = page.evaluate(
            """async (u) => {
                const r = await fetch(u, {credentials: 'include'});
                return await r.json();
            }""",
            url,
        )
        if not isinstance(payload, dict):
            raise DaySessionsUnavailable(f"room/paged 响应异常(非对象)。{_guidance_text(day_str)}")
        status_code = payload.get("status_code")
        if status_code not in (0, "0", None):
            raise DaySessionsUnavailable(
                f"room/paged 返回 status_code={status_code} msg={payload.get('status_msg')!r}。"
                + _guidance_text(day_str)
            )
        infos = payload.get("room_infos") or []
        return parse_paged_rows(infos, date_str=day_str, strict_day=True)
    finally:
        try:
            context.close()
        finally:
            pw.stop()


# ---------------------------------------------------------------------------
# 简易行展示(供 sessions 子命令)
# ---------------------------------------------------------------------------
def format_session_line(s: Dict[str, Any]) -> str:
    end = s.get("end_cst") or "?"
    dur = f"{s['duration_min']}m" if s.get("duration_min") is not None else "?"
    return (f"room_id={s['room_id']}  {s.get('start_cst')} ~ {end}  ({dur})"
            + (f"  {s.get('title')[:40]}" if s.get("title") else ""))
