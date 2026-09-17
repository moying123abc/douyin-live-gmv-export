# 抖音巨量百应直播大屏数据导出工具

**douyin-live-gmv-export** · 面向抖音直播运营、中控,导出抖音巨量百应**直播数据大屏**(`eos.douyin.com/dp/liveScreen?room_id=<id>&tab=trend`,已结束场次可回放)整场**每分钟新增成交金额**时间序列的本地 Python + Playwright 工具,最终输出 CSV(可另存 Excel)。

> 当前进度:**M1(骨架/登录/探测)+ M2(整场分钟成交金额导出 + 内置校验)+
> M3(订单数/在线人数等扩展指标:机制就绪 + 不可得结论 + 默认关闭开关)已交付**。
> `export` 可离线(夹具)与在线运行。
> **小时级 GPM(千次观看成交金额)**:T1 已真机定位整场 GPM 口径(GPM = 直播间成交金额 ÷
> 累计看播次数 × 1000,两场对拍 diff=0.00),T2/T3 交付小时桶聚合与导出联动;分钟观看
> 序列与小时分母口径仍“待对拍”时,在线链路默认优雅跳过并提示(详见下文“小时级 GPM”)。

## 范围与边界(请先阅读)

- 只在你**已登录且有权限**的页面会话内操作;涉及登录一律用**有头(headful)窗口人工扫码**,不做任何登录绕过。
- 不做 `a_bogus` 等**签名逆向**;不调用任何未授权接口。
- **不收集商品 / 营销 / 违规事件时间轴**(团队既定方案明确排除)。
- 浏览器 profile(含登录凭据)仅存本地 `data/browser_profile/`,已在 `.gitignore` 忽略,**严禁提交进代码或输出物**。
- 无人工配合或在线证据时,以离线夹具/文档驱动开发与自测,**绝不伪造页面数据**。

## 目录结构

```
douyin-live-gmv-export/
├── main.py                # CLI 入口:login / probe / export / trim / sessions / stats / daily
├── auth.py                # 登录态管理:持久化 profile + 人工扫码登录
├── probe.py               # 分层只读探测(L0/L1/L3),输出 data/probe/*.json 证据
├── config.py              # 轻量配置加载(标准库;PyYAML 可选)
├── config.example.yaml    # 配置样例(复制为 config.yaml 使用,不入库)
├── requirements.txt
├── .gitignore             # profile / config.yaml / 输出物不入库
├── extractor/             # 读取层:trend.py(整场序列)validation.py(内置校验)sessions.py(T10 按天场次清单)extra_metrics.py(M3)
├── exporter/              # 导出层:schema.py(表结构)to_csv_excel.py(CSV/Excel)
├── tests/
│   ├── selfcheck_probe.py # M1 离线自检(纯函数)
│   ├── test_m2_export.py  # M2 夹具导出回归 + 负向校验用例(可 pytest)
│   ├── cli_export_check.py# M2 CLI 层:负向拦截 + 正向 CSV 断言
│   ├── test_m3_extra_metrics.py  # M3:开关关闭=与 M2 diff 一致 / 开启=对齐并入 / 错位拒绝
│   ├── test_trend_row_series.py  # T8:行对象序列识别/GMV 优先/越界排除
│   └── test_sessions_date.py     # T10:按天解析(基于 t9 真实响应)与逐场流水线
├── docs/
│   ├── probe-evidence-format.md            # 证据文件格式(预定义)
│   ├── export-schema-and-validation.md     # M2 导出表结构/校验口径/待在线复核
│   ├── 指标可得性结论.md                   # M3:订单/人数等扩展指标探测结论与启用步骤
│   ├── 联调结论-2026-09-04.md              # T8 真机联调:room_minute_indicator 行对象序列/L1 命中/口径核对
│   ├── 场次枚举与按场次导出.md             # T9:账户可回放场次清单(每场=独立 room_id)+ 按场次 export 命令
│   └── 按天导出-日期口径.md                # T10:sessions/export --date 用法、CST 当日窗口与文件名规范
└── data/
    ├── browser_profile/   # 持久化登录 profile(自动创建,勿提交)
    ├── probe/             # 探测证据输出
    ├── outputs/           # 导出结果(自动创建,不入库)
    └── samples/           # 离线夹具(合成样例)与生成脚本
```

## 安装

要求 Python 3.9+。建议使用虚拟环境:

```powershell
cd <你的项目目录>\douyin-live-gmv-export
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
```

> 说明:`main.py --help`、`probe.py --dry-run` 等命令在未安装 playwright 的环境也能运行(依赖为调用时导入);真正打开页面前才需要浏览器内核。

## 快速验证(不需要登录、不需要账号)

装完依赖后**先跑这三条**(全部离线:不联网、不打开浏览器、不用登录),用来确认工具本身可用:

```powershell
# 1) 整场分钟成交金额 → CSV(应看到"校验通过 ✓")
python main.py export --fixture data\samples\sample_ended_session_60min.json

# 2) 带观看序列的夹具 → 额外产出小时 GPM 表(_hourly.csv)
python main.py export --fixture data\samples\sample_ended_session_gpm.json

# 3) 成交点精简表(time 只留 HH:MM,只保留 gmv>0 的行)
python main.py trim --in data\outputs\sample_ended_session_60min.csv
```

- 产物统一写到 `data\outputs\`(已在 `.gitignore` 中排除,**不会**被提交)。
- 一次性跑全部离线自检(不需要登录):

```powershell
Get-ChildItem tests\*.py | ForEach-Object { python $_ }
```

> 若这三条都正常,说明代码与环境没问题;接下来只剩"登录"这一步需要你的账号。

## 登录(首次,一次即可)

```powershell
python main.py login
```

- 会以**有头窗口**打开 `https://eos.douyin.com/`,请在弹出的浏览器里**扫码 / 登录你的抖音账号**(该账号须对目标直播间有查看权限)。
- **登录成功的判定标准**:只有当浏览器里出现 `sessionid` / `sessionid_ss` / `sid_tt` /
  `uid_tt` / `sid_guard` 这类**登录后才会种下**的鉴权 Cookie 时,程序才判定成功并自动继续
  (成功时会把命中的 Cookie 名打印出来供你核对)。仅打开登录页就会产生的匿名 Cookie
  (如 `passport_csrf_token`)**不算登录成功**,以免出现"提示成功、实际未登录"的误导。
- 凭据仅保存在本地持久化 profile `data/browser_profile/`;后续 `probe` / `export` 自动复用,无需重复登录。
- **常见问题**:
  - 一直停在"仍在等待人工登录…"→ 确认那个**有头浏览器窗口**已经弹出且没有被其他窗口遮挡,在窗口内完成扫码;窗口若被关掉请重跑命令。
  - 提示登录成功、但之后命令报 `status_code=8 用户未登录`→ 会话已失效(或该账号无此数据权限):
    删除 `data\browser_profile\` 后重跑 `python main.py login` 重新扫码。
  - 换账号 / 换直播间权限:同样先删除 `data\browser_profile\` 再重新登录。
- 会话过期时重跑 `login` 即可;若需强制重新登录,删除 `data/browser_profile/` 后重跑。

## 探测(只读,定位数据来源)

```powershell
# 先看计划与人工引导(不打开浏览器、不采集数据)
python main.py probe --room-id 123456789 --dry-run

# 真机探测(需要已登录 + 有权限)
python main.py probe --room-id 123456789
```

分层策略(命中即停,结果写入 `data/probe/probe_<room_id>_<时间戳>.json`):

| 层 | 手段 | 说明 |
|----|------|------|
| L0 | 读取页面图表数据数组 | 在页面 JS 上下文取 ECharts 实例的 xAxis/series,零网络依赖 |
| L1 | 捕获网络响应 | 被动记录页面加载中返回的 JSON,扫描"时间+数值等长数组"候选 |
| L3 | 逐点悬停 tooltip | 兜底脚手架,默认关闭(`config.yaml` 里 `probe.try_l3: true` 才尝试),需人工在线标定 |

- 无登录凭据 / 页面不可达 / 缺依赖时,程序打印**明确的人工引导**后优雅退出(不崩溃、不伪造数据)。
- 证据文件只描述:命中层级、时间格式、该分钟金额字段名、行样例、整场起止、来源 URL 与待在线复核清单。字段语义(`time`=页面自带时间,`gmv_min`=该分钟新增成交金额,金额为 0 的分钟保留)是**既定口径**。

## 导出(M2:整场分钟成交金额 → CSV/Excel)

```powershell
# 1) 离线自测(夹具驱动,不依赖登录/浏览器;CSV 始终输出,加 --excel 另存 xlsx)
python main.py export --fixture data\samples\sample_ended_session_60min.json
python main.py export --fixture data\samples\sample_ended_session_60min.json --excel

# 2) 在线导出(先 python main.py login;账号需对场次有查看权限)
python main.py export --room-id <room_id>
python main.py export --room-id <room_id> --cumulative 123456.78   # 显式给页面累计值,启用求和校验

# 3) 按天导入(自动解析当天每场 room_id 并逐场导出;默认今天,东八区)
python main.py sessions --date 2026-09-04        # 列出当天已结束场次(只读)
python main.py export --date 2026-09-04 --excel  # 当天每场各导出 CSV/Excel
# 产物: data\outputs\live_<YYYYMMDD>_<room_id>.csv(/xlsx),口径与 --room-id 完全一致
```

> 真机联调(2026-09-04,生活服务直播大屏 room_id=7000000000000000002)已通过:
> L1 命中 `room_minute_indicator` 行对象序列(`key=pay_order_gmv_minute_trend`,
> `x=MM-DD HH:MM`,`y=元`),465 行导出,全行求和 1350.00 = 页面 `key_index.PayGmv`
> (直播间成交金额),三条内置校验全通过;详见
> [docs/联调结论-2026-09-04.md](docs/联调结论-2026-09-04.md)。

产出表结构与口径:

| 列 | 口径 |
|----|------|
| `room_id` | 直播间 id(字符串) |
| `session_date` | 场次日期 YYYY-MM-DD;未知留空(不臆造) |
| `time` | 页面数据自带时间,原样保留(MM-DD HH:MM 或 YYYY-MM-DD HH:MM) |
| `gmv_min` | 该分钟**新增成交金额**(元);**金额为 0 的分钟保留** |

内置校验(任一项失败即**拒绝输出**并打印明细):
非零分钟之和 ≈ 页面累计成交金额(容差可配,默认 abs 1 元 / rel 0.1%);
行数 = 直播时长分钟 + 1;时间轴严格递增且按分钟连续。累计值缺省时求和校验
记为 SKIP 并在控制台提示“待在线复核”。详见
[docs/export-schema-and-validation.md](docs/export-schema-and-validation.md);
按天导入的日期口径见 [docs/按天导出-日期口径.md](docs/按天导出-日期口径.md)。

## 一键日报(daily:支持一次导出多天)

`daily` 把某天(或**多天**)的已结束场次整理成 `日报_<YYYYMMDD>` 文件夹
(每场成交点表 + 日报汇总,有观看档时另含小时 GPM 表)。

```powershell
# 单日(不传就是今天;与旧版行为完全一致)
python main.py daily
python main.py daily --date 2026-09-04

# 多日:下面三种写法完全等价,每个日期各生成一个 日报_<YYYYMMDD> 文件夹
python main.py daily --date 2026-09-01 2026-09-02 2026-09-03      # 空格分隔
python main.py daily --date 2026-09-01,2026-09-02,2026-09-03      # 逗号分隔(中文逗号/分号也行)
python main.py daily --date 2026-09-01 --date 2026-09-02          # 重复传参
```

- **逐日独立**:某天失败(当天无数据 / 无权限 / 校验拒绝)只跳过该天,不影响其他天;
- **退出码**:全部成功 = `0`;存在失败时返回**第一个失败日**的退出码,便于脚本判断;
- **重复日期自动去重**(保留传入顺序);**含非法日期时整批拒绝**(退出码 2),不会只做一半;
- **自动打开目录**(Windows):单日打开该日报目录;多日只打开输出根目录一次,避免弹出一堆窗口;
  加 `--no-open` 关闭该行为;
- 多日运行时逐日打印 `===== [i/N] YYYY-MM-DD =====`,末尾给成功/失败汇总,例如:

```
[daily] 共 3 个日期: 2026-09-01, 2026-09-02, 2026-09-03(逐日独立生成,单日失败不影响其他日)
[daily] ===== [1/3] 2026-09-01 =====
...
[daily] ---- 汇总 ----
[daily]   2026-09-01  成功  -> ...\日报_20260901
[daily]   2026-09-02  失败(退出码 2)
[daily] 共 3 日:成功 2 日,失败 1 日
```

> 说明:`sessions` / `stats` / `export --date` 仍是**单日期**参数;需要多天时用 `daily` 一次传入多个日期。
> 一键脚本 `统计当天.bat` 同样支持多天:`统计当天.bat 2026-09-01 2026-09-02`

## 配置

复制 `config.example.yaml` 为 `config.yaml`(已被 git 忽略)后按需修改;命令行参数优先。主要选项:

```yaml
browser:
  headful: true            # 登录必须有头;建议保持 true
  profile_dir: data/browser_profile
  wait_login_seconds: 1800 # 等待人工扫码上限
probe:
  load_wait_seconds: 25    # 打开回放页后的等待(含网络捕获窗口)
  try_l3: false            # L0/L1 未命中时是否尝试 L3 兜底
```

## 证据文件格式

见 [docs/probe-evidence-format.md](docs/probe-evidence-format.md)(预定义,`schema_version` 递增管理;M2 导出实现以此为输入依据)。

## 扩展指标(M3:订单数 / 在线人数等)

`extractor/extra_metrics.py` 已就绪 **order_min / paid_order_min / online_uv_min /
view_uv_min** 四类分钟指标的同表并表机制(与 `gmv_min` 同一分钟行严格对齐、缺失分钟
留空注明、粒度不符拒绝)。**当前默认关闭**(`extra_metrics.enabled: false`),因为本
仓库交付环境无在线证据;探测过程、不可得原因、替代来源(如账号权限内的复盘导出)
与真机启用步骤见 [docs/指标可得性结论.md](docs/指标可得性结论.md)。

```yaml
# config.yaml(真机验证页面确提供分钟级序列后再启用)
extra_metrics:
  enabled: false
  column_ids: [order_min, online_uv_min]
```

开关关闭时 `export` 输出与 M2 完全一致(已有 diff 回归)。

## 小时级 GPM(千次观看成交金额)

按小时统计“千次观看成交金额”:`GPM(小时)= Σ小时成交金额 ÷ Σ小时观看次数 × 1000`
(输出列 `hour / views / gmv / gpm / note / watch_semantics`;views=0 的桶 GPM 留空并
标注,不伪造)。口径沿 [docs/GPM-观看次数口径结论.md](docs/GPM-观看次数口径结论.md):
**整场 GPM = 直播间成交金额(PayGmv)÷ 累计看播次数(ServerWatchCntTd)× 1000** 已两场真机
对拍 diff=0.00;小时级分母采用 **回放页“直播间观看量(直播间看播量)”分钟序列
(dataKey=WatchCntTrend,经趋势图右上齿轮 →「自定义指标」勾选后由 room_minute_indicator
返回,整场与成交分钟同轴)**。t11 人工协助捕获证据:`WatchCntTrend` Σ=494 vs 同刻
key_index.ServerWatchCntTd=501(diff=-7,≈98.6%;对照稳定值 500 则 diff=-6)——**接近未精确
闭合,差值与首/末分钟边界或统计口径有关,输出以 `watch_semantics` 与说明文件如实标注,
不冒充精确**。因此:

- 在线链路(`export --room-id/--date`,`daily`):默认跳过小时 GPM 并提示;
  **加 `--capture-watch`** 后自动尝试捕获直播间观看量并生成观看档
  (`live_<YYYYMMDD>_<room_id>_watch_min.csv`,表头 `time,views,semantics`),小时 GPM
  即以真实看播量为分母输出(单场 `_hourly.csv` / 日报 `小时GPM_<YYYYMMDD>.csv`);
  捕获失败优雅降级(提示 `python -m exporter.watch_capture --room-id ... --date ... --assist`),
  绝不硬编码未获真机证据字段,不阻断既有导出;
- 离线演示(夹具驱动,观看段来自夹具自带字段):
  ```powershell
  python main.py export --fixture data\samples\sample_ended_session_gpm.json --out data\outputs\gpm_demo
  # 产物: gpm_demo.csv(主表,与 M2 完全一致)+ gpm_demo_hourly.csv(小时 GPM 表)
  ```
  夹具顶层 `gpm_watch` 段:`{level: minute|hour, rows:[{time,views}|{hour,views}...],
  series_key?, semantics?}`(semantics 为口径声明,原样写入 `watch_semantics` 列);
- 一键日报(`main.py daily --date ... --capture-watch`)联动:在 `日报_<YYYYMMDD>` 文件夹内
  - 某场存在观看档 `live_<YYYYMMDD>_<room_id>_watch_min.csv`
    (表头 `time,views,semantics`,由 `--capture-watch` 在线捕获写入)时,与同日其他含观看档的
    场次一起**按小时叠加 views/gmv 后再算 GPM**,生成 `小时GPM_<YYYYMMDD>.csv`;
  - **成交点表新增 gpm 列**:`成交点_<room_id>.csv` 列=`time,gmv_min,gpm`,仅保留有成交
    (gmv>0)的分钟行;`gpm` 取该成交分钟**所属小时**的小时级千次观看成交金额(该小时各成交
    分钟行同值;口径稳定,非分钟瞬时值)。有观看档的场次 gpm 按真实数据填列;小时 views=0 或
    无观看档的场次 gpm 留空(不伪造),并在 `成交点_gpm_说明.txt` 中说明;
  - 无观看档 / 各场**口径身份**不一致(口径声明文本在剔除每场复核差数值
    Σ/ServerWatchCntTd/diff 后仍不同)/ 聚合失败时,**只写 `小时GPM_<YYYYMMDD>_说明.txt`
    降级说明**,原成交金额日报不受影响;各场复核差数值不同但口径身份一致时**正常跨场叠加**,
    各场复核差明细写入说明文件(复核差是单场证据,不是口径差异);
- 回放页观看捕获工具:`python -m exporter.watch_capture --room-id <room_id> --date YYYY-MM-DD`
  (`--assist` 由用户点一次齿轮→自定义指标→勾选→确定;自动模式优先尝试)。**UI 约束(用户
  确认)**:趋势图同屏最多 6 个指标,自动配置先取消非必要项(默认保留:成交金额、成交订单数、
  在线人数、进入人数、直播间观看量、千次观看成交金额 = 6 项),**成交金额(gmv 分子)始终
  保留**;可用 config `export.watch_capture_keep_metrics` 自定义保留集合(成交金额强制置首);
  证据 `data/probe/watch_capture_*.json` + `data/probe/assisted_replay2_*.json`(不入库);
  **t13(2026-09-07 真机单窗口验证)**:弹窗「确定」后若 WatchCntTrend 未即返回,自动点按
  趋势图上方图例芯片(直播间观看量/看播量,必要时千次观看成交金额)使曲线真实显示——
  文本定位优先,芯片开关态不可判时不盲点,仍缺时做**单次知情点按**再转 assist 人工兜底
  (不循环);弹窗行扫描按「自定义指标」modal 作用域定位(排除页面同名 KPI/图例文本),
  至多重试 1 次;实测自动流程**免人工**:用户无需点击即完成 齿轮→弹窗调整→确定→点芯片→
  捕获(证据 `watch_capture_7000000000000000001_20260907_235720.json`,Σ494 vs
  ServerWatchCntTd=501 diff=-7 如实标注);
- 聚合实现(纯函数、离线、零第三方):`extractor/hourly_gpm.py`;设计说明见
  [docs/GPM-小时聚合设计说明.md](docs/GPM-小时聚合设计说明.md)。

## 风险与说明

- 页面 DOM / 接口字段可能随前端改版而变化,命中层级字段语义需**在线人工复核**(证据文件附 `online_recheck_required` 清单)。
- 本工具只是"读取你已登录有权限页面里显示的数据",不是官方 API;数据以页面展示为准,导出结果仅供个人分析。
- 涉及真实账号数据时请遵守平台规则与当地法规,仅用于你有权限的数据。
- **数据脱敏说明**:仓库内示例 room_id、账号名/直播标题与业务数值均为**占位/合成值**
  (仅供演示与链路自测)。浏览器 profile(登录凭据)、`config.yaml`、`data/probe/` 与
  `data/outputs/` 均已在 `.gitignore` 中排除,不会进入版本库。

## 路线

- [x] **M1**:脚手架 + 登录态管理 + `probe.py` 分层探测 + 证据文件格式
- [x] **M2**:整场分钟成交金额序列导出(CSV/Excel)+ 内置校验(求和≈累计、行数≈分钟数、时间轴递增);夹具驱动离线回归通过,在线命中层语义列入待在线复核清单
- [x] **M3**:订单数/在线人数等扩展指标——机制层同表对齐并入就绪(夹具演示,缺失留空注明、错位拒绝);交付环境无在线证据 → 输出 docs/指标可得性结论.md + `extra_metrics` 默认关闭开关,不伪造粒度

## 许可证 / License

本项目基于 [MIT License](LICENSE) 开源,可自由使用、修改与分发(需保留版权声明)。
This project is licensed under the [MIT License](LICENSE).
