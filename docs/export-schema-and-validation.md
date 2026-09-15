# 导出表结构与内置校验口径(M2)

本文定义 `main.py export` 产出的表结构、校验规则与离线夹具约定。字段口径与
[team 既定方案] 一致,导出代码(extractor/trend.py、extractor/validation.py、
exporter/schema.py、exporter/to_csv_excel.py)以本文为唯一口径来源。

## 1. 导出表结构(与 exporter/schema.py 一致)

| 列 | 类型 | 口径 |
|----|------|------|
| `room_id` | str | 直播间 room_id(字符串,不做数值计算) |
| `session_date` | str | 场次日期 `YYYY-MM-DD`;页面/夹具未给出时为空串(不臆造) |
| `time` | str | 页面数据自带时间,**原样保留**:`MM-DD HH:MM` 或 `YYYY-MM-DD HH:MM`(以页面/夹具为准,不做自行换算) |
| `gmv_min` | float | 该分钟**新增成交金额**(元);金额为 0 的分钟保留 |

时间轴要求:整场连续(相邻分钟差 1 分钟),行数 = 直播时长(分钟)+ 1(含首尾点)。

## 2. 内置校验(extractor/validation.py)

| # | 校验 | 规则 | 失败行为 |
|---|------|------|----------|
| 1 | 时间轴严格递增且按分钟连续 | 相邻两行时间差必须恰为 1 分钟(重复/跳变/乱序都会被拦下) | 报错并列出前若干处问题行 |
| 2 | 行数 ≈ 直播时长(分钟)+ 1 | `duration_minutes` 未给出时按首尾时间跨度推算;与期望差超过 `row_tolerance_minutes`(默认 0,严格)即失败 | 报错并给出期望/实际行数 |
| 3 | 非零分钟之和 ≈ 页面累计成交金额 | 0 分钟贡献为 0,故全量求和等价于非零分钟之和;容差可配:绝对 ≤ `sum_abs_tolerance`(默认 1 元)或相对 ≤ `sum_rel_tolerance`(默认 0.1%),满足其一即通过 | 报错并给出和/累计/差值 |

配置项(config.yaml → `validation:`):

```yaml
validation:
  sum_abs_tolerance: 1.0        # 元
  sum_rel_tolerance: 0.001      # 相对 0.1%
  row_tolerance_minutes: 0      # 行数容差(行),默认严格
```

约定:任一校验失败 → 打印明细并 **拒绝输出结果文件**(不静默产出);
`cumulative_total`(页面累计成交金额)未提供时求和校验记为 **SKIP** 并在控制台
明示“待在线复核”,绝不假装通过。

## 3. 读取层与数据来源(extractor/trend.py)

- **L0(首选)**:已登录回放页内读取 ECharts 图表数据数组(xAxis/series),零网络依赖;
- **L1(次选)**:被动捕获页面加载返回的、含分钟时间序列的 JSON 响应;
- L3 逐点悬停仅作兜底脚手架(M1 probe),**不在导出主链路使用**。
- 在线读取路径与 probe 探测同源,字段语义为结构推测,须真机人工复核;
  本仓库在无 playwright/无人工登录环境下开发,在线路径未在本机执行。

### 离线夹具(data/samples,recorded sample)

夹具为**合成样例(非真实页面数据)**,仅用于不登录、无浏览器地驱动导出与自测:

- `sample_ended_session_60min.json` —— 61 行(60 分钟),time=`MM-DD HH:MM`,
  4 个 0 金额分钟,`cumulative_total` 与各行求和一致;
- `sample_ended_session_30min_fullfmt.json` —— 31 行(30 分钟),
  time=`YYYY-MM-DD HH:MM`,同样含 0 金额分钟。

夹具必需携带整场完整 `rows:[{time,gmv_min},...]`(或等长时间+数值数组);
**只有 row_sample 样例行的文件会被拒绝**(不允许以样例冒充整场)。
生成脚本:`data/samples/_generate_fixtures.py`(确定性,可复现)。

## 4. 用法

```powershell
# 离线自测(夹具驱动,无需登录;CSV 始终输出)
python main.py export --fixture data\samples\sample_ended_session_60min.json
python main.py export --fixture data\samples\sample_ended_session_60min.json --excel

# 在线导出(需 python main.py login 后有权限的会话)
python main.py export --room-id <room_id> --excel
python main.py export --room-id <room_id> --cumulative 123456.78
```

## 5. 待在线复核清单(真机执行后回填)

1. 回放页趋势图的实际时间格式(`MM-DD HH:MM` 或 `YYYY-MM-DD HH:MM`?)与字段语义
   (是否确为“该分钟新增成交金额”,与累计成交金额口径一致);
2. L0/L1 在线命中层的字段名与整场起止是否与页面显示一致;
3. 页面累计成交金额(cumulative_total)的取值位置与容差(默认 abs 1 元 / rel 0.1%)
   是否适用;
4. 直播起止是否与时间轴首尾一致(跨午夜/多天场次的时间标注方式)。
