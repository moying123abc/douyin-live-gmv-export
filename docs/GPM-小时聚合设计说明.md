# GPM-小时聚合设计说明(extractor/hourly_gpm.py)

> 关联文档:口径(观看次数 = 看播次数人次、GPM=PayGmv÷看播次数×1000 对拍)见
> `docs/GPM-观看次数口径结论.md`;本文件只描述**小时桶聚合的纯函数设计**。
> 本模块不裁决“观看次数”取哪个 key——按 T1 结论,分母口径在小时级仍标记
> “待对拍”;模块对观看输入形态中立(分钟级或小时级均可,由调用方按其口径提供),
> 并把 views=0 处理、守恒校验等规则写死,防止下游伪造。

## 1. 定位与边界

- `extractor/hourly_gpm.py`:**纯函数、离线可测、零第三方依赖**(仅 datetime 标准库)。
- 不改写既有 M2 成交金额分钟量表与语义;不发起网络、不读在线页面。
- 输出仍为内存结构(不写 CSV/Excel)——文件联动输出属于 t3(集成),本任务只交付
  聚合/换算能力与设计说明。

## 2. 输入与输出契约

输入(全部为内存 list[dict]):
1. **成交金额分钟行** `gmv_rows`:每行 `{time, gmv_min}`(+可选 `session_date`);
   `time` 支持页面自带时间外观:`MM-DD HH:MM`、`YYYY-MM-DD HH:MM[:SS]`、epoch 秒
   (int/float/数字字符串);`gmv_min` 必须数值(布尔/NaN/Inf/字符串一律结构化拒绝)。
   兼容 `exporter/schema.normalize_rows()` 的标准量表行(`from_standard_rows()` 便捷入口)。
2. **观看序列** `watch_rows`(可选):两种形态由 `watch_level` 指定/自动判定:
   - 分钟级:`{time, views}`(views 别名 views/view_min/watch/watch_min/y/value);
   - 小时级:`{hour, views}`,`hour` 为 `YYYY-MM-DD HH:00` 或 `MM-DD HH:00`。
3. `reference_date`(可选):为 MM-DD 短格式补年份(行内 `session_date` 优先)。

输出 report:
```
rows:   [{hour: "YYYY-MM-DD HH:00", views: int|float, gmv: float,
          gpm: float|None(两位小数), note: str}]        # hour 升序
totals: {gmv, views, gpm|None}                          # 整场合计 GPM(对拍锚点)
checks: {gmv_conserved, watch_conserved, gmv_input, gmv_bucket_total,
         gmv_diff, views_input, views_bucket_total, views_diff}
warnings: [...]                                          # 如年份兜底提示
```

## 3. 算法与规则

1. **归桶**:每行按“数据自带时间”所在整点小时归桶:桶 `[HH:00, HH:59:59]`,
   标签统一 `YYYY-MM-DD HH:00`(`bucket_hour()`)。HH:MM 只取整点、不做四舍五入。
2. **GMV 小时桶** = 该小时分钟行 `gmv_min` 求和;缺观看的小时桶若来自成交金额侧照常输出。
3. **views 小时桶** = 观看序列中落在该小时的分钟值求和(分钟级),或小时级行的 views 直取;
   桶集 = gmv 桶 ∪ watch 桶(并集,watch-only 小时桶以 gmv=0 + note 输出)。
4. **GPM** = `gmv/views*1000`,`round(…, 2)`(元/千次观看)。
   - `views>0` 才计算;`views=0`(含缺失)桶 **gpm=None(输出留空)**,note 标注
     `views=0: GPM 无定义,留空(不伪造)`,绝不产生 NaN/Inf,也不填 0 冒充。
   - 整场合计 GPM 同公式,作为 t3 与页面 GPM 卡(`key_index.GPM`)数值对拍的锚点。
5. **跨场同小时叠加**:调用方把多场(或同场多次读取)的 gmv/watch 行拼接后一次聚合;
   同 `hour` 桶的 views/gmv **各自加总后再算 GPM**(`(Σgmv)/(Σviews)*1000`),
   **绝不是各场 gpm 的均值**(有单测锁定:两场 gpm 2000/500 → 合并 1500,非 1250)。
6. **守恒校验(硬校验,失败抛 `HourlyGpmError`,拒绝输出)**:
   - GMV:Σ桶 gmv ≈ Σ输入分钟 gmv(容差 1e-6 元);
   - views:提供观看序列时,Σ桶 views ≈ Σ观看输入(容差 1e-6)。
7. **跨午夜场次**:每行按**自身日历日**归属(不一律按开播日):页面 MM-DD 已随午夜翻日时
   直接用行内日期;epoch 秒按东八区(UTC+8,`epoch_tz_hours=8`)转墙面后取小时——
   与现有“time 用页面自带时间、跨午夜以行 time_stamp 为准”的口径一致。
   MM-DD 无参考年时按 2000 兜底并写入 warnings(要求调用方传 session_date/reference_date)。

## 4. 与 T1 口径结论的对接(下游 t3 使用约束)

- **分子**:`pay_order_gmv_minute_trend` 分钟行(=现有 M2 量表)的 `gmv_min`;
  已验证:整场分钟求和 = PayGmv(2000/1350),小时桶合计与整场一致 → t3 可做
  “Σ小时 gmv == M2 整场 gmv”的自动对拍。
- **分母(小时观看次数)**:T1 只对拍了**整场**口径 —— GPM 卡 = PayGmv ÷
  `ServerWatchCntTd`(累计看播次数)×1000;**小时级分母口径仍“待对拍”**。
  t3 在取到可与整场看播次数闭合的分钟观看序列(如 `WatchCntTrend`)并对其求和自洽前,
  不得把 `EnterUCntTrend/OnlineUCntTrend` 等代理序列直接喂入并标称“观看次数”;
  本模块只负责给定输入后的忠实聚合与 views=0 标注,不做口径裁决。
- **分钟级观看输入建议形态**:`{time: 分钟行 x, views: 该分钟值, session_date: …}`
  (与 gmv 行同一次读取、同分钟轴);**小时级观看输入**:`{hour: "YYYY-MM-DD HH:00", views}`。
- **无观看输入**:允许(先出 gmv 小时轮廓),全部 views=0、GPM 留空并标注——不伪造。

## 5. 离线单测(本模块配套)

`tests/test_hourly_gpm.py`(纯合成夹具、无第三方;`python tests/test_hourly_gpm.py`):
1. 常规聚合:分钟级 gmv + 分钟级 views → 每小时 [hour,views,gmv,gpm],守恒与两位小数;
2. 跨场同小时叠加:先合并再算 GPM(非均值);
3. views=0 边界:gpm 留空(None)+ note 标注,无 NaN/Inf;
4. 分钟级与小时级两种观看输入形态结果一致;
5. MM-DD+session_date 补年、跨午夜行按自身日历日分桶;
6. epoch 秒(东八区墙面)归桶;
7. 负向:空输入 / 非数值 / NaN / 非法时间 / 缺 views 键 均结构化拒绝;
8. `bucket_hour` 工具行为。

## 6. 回归与既有语义

- 新增仅两个文件:`extractor/hourly_gpm.py`、`tests/test_hourly_gpm.py`;
  未改动 M2 读取/校验/导出、M3 extra_metrics、T8/T10/compact/stats/daily 任何既有模块。
- 全量离线回归保持全绿(实测):
  M1 selfcheck ✓ / M2 10 例 ✓ / M3 9 例 ✓ / compact 4 ✓ / cli_export_check ✓ /
  stats 4 ✓ / daily_publish 3 ✓ / export_day_resilience 2 ✓ / T8 2 例 ✓ / T10 7 例 ✓ /
  test_hourly_gpm 8/8 ✓;`python -m py_compile` 新模块与测试 exit 0。
