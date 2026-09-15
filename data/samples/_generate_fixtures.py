# -*- coding: utf-8 -*-
"""一次性生成 data/samples 离线夹具(合成样例,非真实页面数据)。

两个夹具:
  A) sample_ended_session_60min.json        — time 用 "MM-DD HH:MM",整场 61 行(60 分钟)
  B) sample_ended_session_30min_fullfmt.json— time 用 "YYYY-MM-DD HH:MM",整场 31 行(30 分钟)
数值为确定性公式生成,含金额为 0 的分钟;cumulative_total = 各行之和(取两位小数)。
"""
import json
import math
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]  # 本文件位于 <root>/data/samples/ 下
OUT = ROOT / "data" / "samples"


def build_a():
    from datetime import datetime, timedelta
    t0 = datetime(2025, 7, 1, 19, 0)
    rows = []
    for i in range(61):  # 19:00 .. 20:00 共 61 个分钟点(60 分钟时长)
        t = (t0 + timedelta(minutes=i)).strftime("%m-%d %H:%M")
        if i in (0, 7, 30, 60):  # 保留金额为 0 的分钟(仅这 4 处为 0)
            v = 0
        else:
            v = max(1, int(35 + 25 * math.sin(i / 3.1) + 12 * math.sin(i / 11.0)))  # 非零
        rows.append({"time": t, "gmv_min": float(v)})
    total = round(sum(r["gmv_min"] for r in rows), 2)
    times = [r["time"] for r in rows]
    # M3 演示段:与 rows 完全同分钟对齐的扩展指标(合成样例;机制演示用)
    zero_idx = (0, 7, 30, 60)
    order_min = [0 if i in zero_idx else max(1, int(3 + 2 * math.sin(i / 4.3))) for i in range(61)]
    online_uv_min = [max(1, int(300 + 120 * math.sin(i / 9.7))) for i in range(61)]
    return {
        "kind": "recorded-sample-fixture(合成样例,非真实页面数据;仅供离线驱动与自测)",
        "schema_version": "1.0",
        "probe_meta": {
            "room_id": "123456789",
            "url": "https://eos.douyin.com/dp/liveScreen?room_id=123456789&tab=trend",
            "session_state_hint": "ended",
        },
        "layer_hit": "L1",
        "field_definition": {
            "time_field": "rows[].time",
            "time_format": "MM-DD HH:MM",
            "gmv_min_field": "rows[].gmv_min",
            "amount_unit": "yuan(元)",
            "note": "夹具字段名与页面语义的映射见 data/samples 说明;真机需在线复核",
        },
        "session_date": "2025-07-01",
        "session_bounds": {
            "observed_start": "07-01 19:00",
            "observed_end": "07-01 20:00",
            "duration_minutes": 60,
        },
        "cumulative_total": total,
        "rows_observed": len(rows),
        "rows": rows,
        # M3 扩展指标演示段:extra_metrics 开关启用 + column_ids 请求后按 time 对齐并入
        "extra_metrics": {
            "time": times,
            "values": {"order_min": order_min, "online_uv_min": online_uv_min},
            "note": "合成对齐样例;真实并入前需真机验证页面提供同分钟序列(见 docs/指标可得性结论.md)",
        },
        "assumptions": [
            "time=该分钟时间(页面口径 MM-DD HH:MM);gmv_min=该分钟新增成交金额,0 分钟保留",
            "session_date=2025-07-01;整场 60 分钟 → 61 行(含首尾)",
            "cumulative_total 由本夹具各行求和得到(合成一致,校验应通过)",
        ],
        "online_recheck_required": [
            "真机复核:页面回放页实际分钟时间格式与字段语义与夹具一致",
            "真机复核:页面累计成交金额与求和校验的容差是否适用",
        ],
    }


def build_b():
    from datetime import datetime, timedelta
    t0 = datetime(2025, 7, 2, 14, 0)
    rows = []
    for i in range(31):  # 14:00 .. 14:30 共 31 个分钟点(30 分钟时长)
        t = (t0 + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M")
        if i in (0, 5, 15, 30):
            v = 0.0
        else:
            v = round(max(0.0, 20 + 18 * math.sin(i / 2.7) + 6 * math.sin(i / 7.0)) * 4) / 4.0
        rows.append({"time": t, "gmv_min": v})
    total = round(sum(r["gmv_min"] for r in rows), 2)
    return {
        "kind": "recorded-sample-fixture(合成样例,非真实页面数据;仅供离线驱动与自测)",
        "schema_version": "1.0",
        "room_id": "223344556",
        "session_date": "2025-07-02",
        "layer_hit": "L0",
        "field_definition": {
            "time_field": "rows[].time",
            "time_format": "YYYY-MM-DD HH:MM",
            "gmv_min_field": "rows[].gmv_min",
        },
        "session_bounds": {
            "observed_start": "2025-07-02 14:00",
            "observed_end": "2025-07-02 14:30",
            "duration_minutes": 30,
        },
        "cumulative_total": total,
        "rows_observed": len(rows),
        "rows": rows,
        "assumptions": [
            "time 使用完整日期时间格式(YYYY-MM-DD HH:MM)时可直接得到 session_date",
            "gmv_min=该分钟新增成交金额,0 分钟保留;整场 30 分钟 → 31 行",
        ],
        "online_recheck_required": ["真机复核字段语义与页面口径"],
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("sample_ended_session_60min.json", build_a()),
        ("sample_ended_session_30min_fullfmt.json", build_b()),
    ):
        target = OUT / name
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {target}  rows={len(payload['rows'])}  cumulative={payload['cumulative_total']}")


if __name__ == "__main__":
    main()
