# -*- coding: utf-8 -*-
"""离线自检:probe.py 纯函数(时间识别 + L1 JSON 扫描 + 证据格式键)。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import probe  # noqa: E402

# 1) 时间外观识别
assert probe._time_label("07-01 19:00") == "MM-DD HH:MM"
assert probe._time_label("2025-07-01 19:00:00") == "YYYY-MM-DD HH:MM"
assert probe._time_label(1719903600) == "epoch"
assert probe._time_label("abc") is None

# 2) L1 扫描:发现 时间+数值 等长候选
sample = {
    "data": {
        "trend_list": {
            "time": ["07-01 19:%02d" % m for m in range(60)],
            "gmv": [float(m) for m in range(60)],
        },
        "other": "x",
    }
}
hits = probe._scan_json_for_series(sample)
assert hits, "no candidate found"
best = sorted(hits, key=lambda m: 0 if (m.get("guess") or "").startswith("gmv") else 1)[0]
assert best["time_key"] == "time" and best["value_key"] == "gmv", best
assert best["time_format"] == "MM-DD HH:MM" and best["time_len"] == 60
assert best["guess"] == "gmv/金额(推测)", best["guess"]

# 3) 负向:无数值列表 → 无候选
assert probe._scan_json_for_series({"a": [1, 2, 3], "b": ["x", "y"]}) == []

# 4) 证据文档必备键(口径预定义:时间格式/分钟金额字段/整场起止)
doc = probe._evidence_doc()
for k in ("schema_version", "probe_meta", "layer_hit", "field_definition",
          "session_bounds", "rows_observed", "row_sample", "sources"):
    assert k in doc, k
fd = doc["field_definition"]
for k in ("time_field", "time_format", "gmv_min_field"):
    assert k in fd, k
for k in ("observed_start", "observed_end"):
    assert k in doc["session_bounds"], k

print("OFFLINE SCANNER SELF-CHECK: all assertions passed")
