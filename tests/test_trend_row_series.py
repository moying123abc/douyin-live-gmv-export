# -*- coding: utf-8 -*-
"""T8 真机结构适配的离线单元(行对象序列识别 / 越界排除 / key_index 累计)。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from extractor import trend  # noqa: E402
from probe import _scan_row_objects, _url_is_excluded_series  # noqa: E402


def _chart(minutes=465, base="09-02 11:16", value_fn=lambda i: i):
    import datetime as dt
    t0 = dt.datetime(2026, 9, 2, 11, 16)
    out = []
    ts0 = int(t0.replace(tzinfo=dt.timezone.utc).timestamp()) - 17
    for i in range(minutes):
        t = t0 + dt.timedelta(minutes=i)
        out.append({"x": t.strftime("%m-%d %H:%M"), "y": value_fn(i),
                    "time_stamp": str(ts0 + i * 60)})
    return out


def test_row_series_picks_gmv_and_excludes_product():
    # room_minute_indicator 形态(真机结构 2026-09)
    room_body = {
        "url": "https://eos.douyin.com/life/api/live_screen/v5/room_minute_indicator?room_id=1",
        "json": {"code": 0, "data": [
            {"key": "pay_order_cnt_minute_trend",
             "chart": _chart(value_fn=lambda i: 1 if i % 47 == 0 else 0)},
            {"key": "pay_order_gmv_minute_trend",
             "chart": _chart(value_fn=lambda i: (i * 3) % 100)},
        ]},
    }
    # product_trend(商品时间轴,范围外)与 room_body 结构相同但 URL 必须被排除
    product_body = {
        "url": "https://eos.douyin.com/life/api/live_screen/v5/product_trend?room_id=1",
        "json": {"data": {"999": {"key": "xx", "chart": _chart()}}},
    }
    assert _url_is_excluded_series(product_body["url"]) is True
    assert _url_is_excluded_series(room_body["url"]) is False

    # 模拟 read_live 的选择循环:排除 URL + gmv 优先
    best = None
    for body in (product_body, room_body):
        burl = body["url"]
        if _url_is_excluded_series(burl):
            continue
        cand = trend._find_full_series(body["json"])
        if cand is None:
            cand = trend._find_row_series(body["json"])
        if cand:
            cand["url"] = burl
            if best is None or (cand.get("gmvish") and not best.get("gmvish")) \
                    or (cand.get("gmvish") == best.get("gmvish")
                        and len(cand["time"]) > len(best["time"])):
                best = cand
    assert best is not None
    assert best["series_key"] == "pay_order_gmv_minute_trend"
    assert best["gmvish"] is True and len(best["time"]) == 465
    assert best["time"][0] == "09-02 11:16"
    assert best["epoch_first"] is not None

    # probe 侧行对象扫描同样命中且带 rows_sample
    hits = _scan_row_objects(room_body["json"])
    assert hits and hits[0]["series_key"] == "pay_order_cnt_minute_trend"  # cnt 先出现
    gmv_hits = [h for h in hits if h["series_key"] == "pay_order_gmv_minute_trend"]
    assert gmv_hits and len(gmv_hits[0]["rows_sample"]) == 5


def test_cumulative_from_key_index():
    body = {
        "url": "https://eos.douyin.com/life/api/live_screen/v5/key_index?room_id=1",
        "json": {"data": {"PayGmv": {"key": "PayGmv", "name": "累计支付金额",
                                     "unit": "元", "value": 1350}}},
    }
    assert trend._find_cumulative_key_index([body]) == 1350.0
    assert trend._find_cumulative_key_index([]) is None


def _run_all():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures.append((name, exc))
            print(f"FAIL {name}: {exc}")
    if failures:
        print(f"\n{len(failures)}/{len(tests)} 用例失败")
        sys.exit(1)
    print(f"\n全部 {len(tests)} 个 T8 结构适配用例通过")


if __name__ == "__main__":
    _run_all()
