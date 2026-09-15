# 探测证据文件格式(probe evidence format)

`probe.py`(M1)把对回放页的**分层只读探测**结果写成 `data/probe/probe_<room_id>_<YYYYmmdd_HHMMSS>.json`,供 M2 导出实现判读。本文件定义其格式(预定义、版本化);字段结构变更时递增 `schema_version`(当前 `1.0`,常量见 `probe.py:EVIDENCE_SCHEMA_VERSION`)。

> 诚实性约定:证据文件只记录"探测过程与结构发现(字段名/样例/来源)",**绝不伪造页面数据**;未命中层级时字段定义为 `null` 并附说明,不会凭空编造读数。

## 顶层结构

| 键 | 类型 | 必含内容 |
|----|------|----------|
| `schema_version` | str | 证据格式版本 |
| `probe_meta` | dict | room_id、回放页 URL、采集时间(UTC)、页面标题、场次状态提示 |
| `layer_hit` | str | `L0` / `L1` / `L3` / `none`(命中层级) |
| `field_definition` | dict | 时间字段名、**时间格式**、**该分钟金额字段名**、单位与语义说明 |
| `session_bounds` | dict | **整场起止时间**(时间轴首/尾点) |
| `rows_observed` | int | 观察到的行数 |
| `row_sample` | list | 前若干行样例 `[{time, gmv_min}, ...]`(不超过 `max_row_sample` 行) |
| `sources` | list | 数据来源说明(如 L1 命中的响应 URL) |
| `assumptions` | list | 实现假设(需人工复核) |
| `online_recheck_required` | list | **待在线复核清单** |

### probe_meta

```json
{
  "room_id": "123456789",
  "url": "https://eos.douyin.com/dp/liveScreen?room_id=123456789&tab=trend",
  "captured_at_utc": "2025-07-01T08:30:00Z",
  "page_title": "直播大屏",
  "session_state_hint": "ended"
}
```

`session_state_hint` 取值:`ended`(已结束/回放)、`live`(直播中)、`unknown`(无法判断)。仅依据页面可见文本尽力判断,不做网络假设。

### field_definition(口径预定义,命中后回填字段名)

```json
{
  "time_field": "xAxis.data(页面自带时间,字段名以命中来源为准)",
  "time_format": "MM-DD HH:MM",
  "gmv_min_field": "series.data / 响应中该分钟金额字段",
  "amount_unit": "yuan(元)",
  "note": "字段语义 = 该分钟新增成交金额;gmv 语义为结构推测,需在线人工复核;金额为 0 的分钟保留"
}
```

口径(既定方案,所有任务一致):

- `time`:使用**页面数据自带时间**,不自行换算;格式为 `MM-DD HH:MM`、`YYYY-MM-DD HH:MM` 或 epoch(以命中层实际输出为准,写入 `time_format`)。
- `gmv_min` = 该分钟**新增成交金额**(元);整场时间轴连续,**金额为 0 的分钟保留**。

### session_bounds

```json
{
  "observed_start": "07-01 19:00",
  "observed_end": "07-01 22:00",
  "note": "整场起止按时间轴首尾点回填;待在线复核与直播起止一致性"
}
```

未命中层级时为 `{"observed_start": null, "observed_end": null, "note": "..."}`。

## 分层命中说明

- **L0(页面图表数据数组)**:在页面 JS 上下文枚举 ECharts 实例(`[_echarts_instance_]`),读取 option 的 `xAxis.data`(时间)与 `series.data`(数值)。命中条件:存在时间外观 x 轴 + 等长数值 series。
- **L1(网络响应)**:被动监听页面自己发起的、URL 命中 `probe.network_url_hints` 且 content-type 为 JSON 的响应;判读阶段扫描响应体,寻找"等长的时间列表 + 数值列表"(≥5 点)。字段语义按键名启发式推测(`gmv/金额/成交` 优先),仍列入 `online_recheck_required`。
- **L3(逐点悬停 tooltip 兜底脚手架)**:默认关闭(`config.yaml` → `probe.try_l3: true` 才尝试)。原理:按分钟索引在图表上逐点移动鼠标、读取 tooltip 文本并解析。**该层强依赖页面布局与 tooltip DOM**,首次使用前需人工在目标页面标定:
  1. 打开回放页趋势图,确认每点悬停显示的文本是否含该分钟金额;
  2. 在 `_try_l3` 中标定图表容器与 tooltip 选择器、文本格式;
  3. 上线前小范围抽测与页面数字核对。
  未标定前 L3 不产出数据(避免伪造读数)。

## 示例(结构示意,非真实页面数据)

```json
{
  "schema_version": "1.0",
  "probe_meta": {
    "room_id": "123456789",
    "url": "https://eos.douyin.com/dp/liveScreen?room_id=123456789&tab=trend",
    "captured_at_utc": "2025-07-01T08:30:00Z",
    "page_title": "直播大屏",
    "session_state_hint": "ended"
  },
  "layer_hit": "L1",
  "field_definition": {
    "time_field": "trend_list.time",
    "time_format": "MM-DD HH:MM",
    "gmv_min_field": "trend_list.gmv",
    "amount_unit": "yuan(元)",
    "note": "字段语义 = 该分钟新增成交金额;gmv 语义为结构推测,需在线人工复核;金额为 0 的分钟保留"
  },
  "session_bounds": {
    "observed_start": "07-01 19:00",
    "observed_end": "07-01 22:00",
    "note": "整场起止按时间轴首尾点回填;待在线复核与直播起止一致性"
  },
  "rows_observed": 181,
  "row_sample": [
    { "time": "07-01 19:00", "gmv_min": 0 },
    { "time": "07-01 19:01", "gmv_min": 128.5 }
  ],
  "sources": [
    { "layer": "L1", "url": "https://eos.douyin.com/xxx/trend?room_id=123456789", "note": "..." }
  ],
  "assumptions": [
    "时间使用页面数据自带时间(格式见 field_definition.time_format)",
    "gmv_min = 该分钟内新增成交金额;金额为 0 的分钟应保留",
    "整场起止时间 = 时间轴首/尾点"
  ],
  "online_recheck_required": [
    "在线复核命中字段的语义(是否确为该分钟新增成交金额)",
    "在线核对整场时间轴是否连续、起止是否覆盖整场直播",
    "核对 M2 导出所用行与页面累计成交金额的一致性(校验项)"
  ]
}
```
