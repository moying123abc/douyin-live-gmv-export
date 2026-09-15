# -*- coding: utf-8 -*-
"""tests/test_gpm_probe_live_ready.py — t9 直播中采集模式的离线就绪回归。

运行:python tests/test_gpm_probe_live_ready.py
覆盖(全部离线、无浏览器):
 1. gpm_probe.py 可离线导入且 --live 参数存在;
 2. 纯函数 _collect_series_from_bodies:合成 room_minute_indicator 响应体 →
    series_keys 全量记录(含 WatchCntTrend/gpm/观看系);
 3. live_evidence_skeleton 结构对齐 gpm_probe2:含 waves/dom_metric_entries_seen/
    series_keys_total/target_hits/closure;
 4. gpm_probe_live_dry_run 离线可跑(exit 0、打印计划与一行命令)。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as cfgmod  # noqa: E402
import gpm_probe as gp  # noqa: E402


def _rmi_body(keys_rows):
    """构造 room_minute_indicator 形态的合成响应体(meta + data[] chart 组)。"""
    data = []
    meta = []
    for key, (title, rows) in keys_rows.items():
        data.append({"key": key, "chart": rows})
        meta.append({"dataKey": key, "title": title, "type": "line"})
    return {"url": "https://eos.douyin.com/life/api/live_screen/v5/room_minute_indicator",
            "json": {"meta": meta, "data": data}}


def _rows(vals):
    return [{"x": f"09-04 {9:02d}:{i:02d}", "y": v, "time_stamp": 1756944000 + i * 60}
            for i, v in enumerate(vals)]


def test_module_importable_and_live_flag():
    parser = gp.build_parser()
    ns = parser.parse_args(["--live", "--room-id", "123", "--dry-run"])
    assert ns.live is True and ns.dry_run is True and ns.room_id == "123"
    ns2 = parser.parse_args(["--live", "--room-id", "123", "--waves", "3",
                             "--interval-sec", "10", "--max-minutes", "5"])
    assert ns2.waves == 3 and ns2.interval_sec == 10 and ns2.max_minutes == 5


def test_collect_series_from_bodies_records_watch_and_gpm():
    bodies = [
        _rmi_body({
            "pay_order_gmv_minute_trend": ("成交金额", _rows([1.0, 2.0, 3.0])),
            "WatchCntTrend": ("直播间看播量", _rows([10, 20, 30])),
            "gpm": ("千次观看成交金额", _rows([5.0, 5.0, 5.0])),
        }),
        _rmi_body({"EnterUCntTrend": ("进入人数", _rows([7, 8, 9]))}),
    ]
    sink = {}
    gp._collect_series_from_bodies(bodies, sink)
    assert "WatchCntTrend" in sink and "gpm" in sink and "EnterUCntTrend" in sink
    assert sink["pay_order_gmv_minute_trend"]["stats"]["value_sum"] == 6.0
    assert sink["WatchCntTrend"]["stats"]["value_sum"] == 60.0
    # 结构性去重:同一 key 不重复覆盖
    gp._collect_series_from_bodies(bodies, sink)
    assert len(sink) == 4


def test_live_evidence_skeleton_aligns_with_gpm_probe2():
    ev = gp.live_evidence_skeleton({}, "123", "https://example.invalid/?room_id=123")
    dp = ev["indicator_dropdown_probe"]
    for k in ("waves", "dom_metric_entries_seen", "series_keys_total",
              "target_hits", "closure"):
        assert k in dp, f"缺少 gpm_probe2 对齐段 {k}"
    assert ev["schema_version"] == gp.LIVE_SCHEMA_VERSION
    assert "boundaries" in ev and "conclusions" in ev
    assert ev["probe_meta"]["mode"].startswith("live periodic scan")


def test_live_dry_run_offline():
    cfg = cfgmod.load_config(None)
    assert gp.gpm_probe_live_dry_run(cfg, "123456789") == 0
    # 一行命令出现在 dry-run 文本里(把 stdout 重定向抓取)
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = gp.gpm_probe_live_dry_run(cfg, "123456789")
    assert code == 0
    text = buf.getvalue()
    assert "--live --room-id" in text and "直播" in text
    assert "DRY-RUN" in text and "不会打开浏览器" in text


def _run_all():
    tests = [
        test_module_importable_and_live_flag,
        test_collect_series_from_bodies_records_watch_and_gpm,
        test_live_evidence_skeleton_aligns_with_gpm_probe2,
        test_live_dry_run_offline,
    ]
    for fn in tests:
        fn()
    return len(tests)


if __name__ == "__main__":
    n = _run_all()
    print(f"test_gpm_probe_live_ready: {n}/{n} passed")
