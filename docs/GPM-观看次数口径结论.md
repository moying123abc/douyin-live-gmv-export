# GPM-观看次数口径结论(按小时千次观看成交金额 · 数据源定位探测)

> 探测日期:2026-09-07(复用本机已登录持久化 profile,有头只读探测;两场复核)。
> 目标页面:抖音生活服务/本地直播专业版直播数据大屏回放页
> `eos.douyin.com/dp/liveScreen?room_id=<id>&tab=trend`(已结束场次可回放)。
> 本次只做**数据源定位与口径对拍**,不实现聚合代码(见后续 GPM 实现任务)。
> 范围边界:只读本账号有权限数据、复用登录态、无登录绕过/签名逆向、
> 不收集商品/营销/违规事件带、样例脱敏、profile/凭据不提交。

## 0. 结论摘要(给下游实现)

| 项 | 结论 | 置信 |
|----|------|------|
| 页面"千次观看成交金额"(GPM)卡所在 | `key_index.data.GPM`(meta title=千次观看成交金额,unit=元) | 确证 |
| GPM 整场口径(数值对拍) | `GPM = PayGmv ÷ ServerWatchCntTd × 1000`;**两场对拍 diff=0.00** | 对拍通过 |
| "观看次数"对应字段(人次口径) | `key_index.data.ServerWatchCntTd`,name=累计看播次数(unit 空,人次) | 数值印证 |
| 同页易混字段(不可作 GPM 分母) | `LiveServerWatchUcnt`(累计观看/在线人数,UV 口径;若用它 GPM≠页面值) | 确证勿混用 |
| 分钟序列位置 | `life/api/live_screen/v5/room_minute_indicator`,行对象 `{x,y,time_stamp}` | 确证 |
| 默认分钟序列 | `pay_order_gmv_minute_trend`(成交金额/分钟)+ `pay_order_cnt_minute_trend`(成交订单数/分钟) | 确证(与 M2 一致) |
| UI 可切换出的观看系分钟序列 | `OnlineUCntTrend`(在线人数)、`EnterUCntTrend`(进入人数)、`LeaveUCntTrend`(离开人数)、`LikeCntTrend`(点赞次数) | 确证(点击可得) |
| 目录中存在、本次未取到的分钟 key | `WatchCntTrend`(直播间看播量)、`ShowCntTrend`(直播间曝光量)、`gpm`(千次观看成交金额分钟级)等 16 项 | **待对拍**(未在只读点击路径取到) |
| 小时粒度 | 页面**没有**现成"按小时桶"序列;仅整场均值 KPI(`WatchCntPerHour` 小时看播pv 50.0 等) | 确证(需自聚合) |
| 已结束场次复盘/分析页读取(t10) | 该 UI 内"复盘"为 liveScreen 的 tab,未发现 analysis/report/export 端点、无导出下载入口、序列仍仅 gmv/订单分钟(闭合);**无观看系小时/分钟序列** | 确证(阴性) |
| 回放页 gpm/看播量分钟序列(t11) | **用户手动切换趋势指标可取到** `gpm`(千次观看成交金额)与 `WatchCntTrend`(直播间看播量),均整场 597 行与 gmv 同轴;WatchCntTrend Σ494 vs ServerWatchCntTd 501(**未精确闭合,差 7**),是小时 GPM 分母最强候选,待复核 | 人工协助复现 |
| 回放页小时 GPM 分母接入(t12) | `exporter/watch_capture.py` 封装 齿轮→自定义指标→勾选→确定 自动/--assist 捕获;`export/daily --capture-watch` 写观看档并以 WatchCntTrend 为分母输出小时 GPM(semantics 内嵌 Σ/卡/diff,如实标注复核差);未闭合差(≈-7/-6)不冒充精确 | 已集成(分母=直播间观看量,复核差如实) |

## 1. 探测对象与方法

- 场次(已结束回放,各 = 独立 room_id):
  - `7000000000000000001`:2026-09-04 09:04:21–19:00:28,597 个分钟行(PayGmv 2000 元);
  - `7000000000000000002`:2026-09-02 11:16:17–19:00:58,465 个分钟行(PayGmv 1350 元)。
- 方式:L1 被动捕获页面自发的 JSON 响应 + 在"趋势分析"面板点击允许的指标文案
  (成交金额/成交订单数/在线人数/进入人数/离开人数/点赞次数)触发页面自身刷新后再次被动捕获;
  打开"流量"tab 记录 `flow_index`/`flow_entrance_trend`。对每次点击只做**只读观察**。
- 探测脚本(本仓库新增,仅用于定位/对拍):`gpm_probe.py`(支持 `--dry-run`);
  证据:`data/probe/gpm_probe_<room_id>_<时间戳>.json`(命中/样例/对拍,修剪后)。

## 2. 会话级口径(整场 KPI,已数值对拍)

`key_index` 响应(两场同构)关键卡:

| key | 页面名(meta title / data.name) | 单位 | 09-04 场 | 09-02 场 |
|-----|-------------------------------|------|---------|---------|
| `PayGmv` | 直播间成交金额 / 累计支付金额 | 元 | 2000 | 1350 |
| `GPM` | 千次观看成交金额 | 元 | 4000.00 | 3000.00 |
| `ServerWatchCntTd` | 累计看播次数 | (人次) | 500 | 450 |
| `LiveServerWatchUcnt` | 累计观看人数(meta)/累计在线人数 | 人 | 380 | 360 |
| `PayOrderCnt` | 支付订单数 / 成交订单数 | 单 | 9 | 8 |
| `ClientLiveShowCntTd` | 累计曝光次数 | 次 | 8000 | (同结构) |

**对拍(两场均 exact):**
- `2000 / 500 × 1000 = 4000.00 = GPM 卡`;`1350 / 450 × 1000 = 3000.00 = GPM 卡` → diff=0.00。
- 旁证(定义一致性,均在 key_index 内):`LiveCtr = ServerWatchCntTd/ClientLiveShowCntTd`
  = 500/8000 = 6.3%(页卡 LiveCtr 6.3% ✓);`LiveCvr = PayOrderCnt/ServerWatchCntTd`
  = 9/500 = 1.8%(页卡 LiveCvr 1.8% ✓)。

**结论(GPM 整场口径,数值对拍):**
`千次观看成交金额 GPM = 直播间成交金额(PayGmv) ÷ 累计看播次数(ServerWatchCntTd) × 1000`。
"观看次数(人次口径)"对应字段即 `ServerWatchCntTd`(页面展示名"累计看播次数");
用户日常口语的"观看次数"与页面 GPM 分母一致的是**看播次数(人次,含同用户多次进入的回访)**,
不是"观看/在线人数(UV)"`LiveServerWatchUcnt`(用 UV 计算会得到 2000/380×1000≈5263≠4000.00)。

**易混口径提醒(同 tab 数值不一致,勿混用):**
- 概览 tab:卡"累计在线人数"=`LiveServerWatchUcnt`=380(09-04);
- 流量 tab:`flow_index.data.LiveServerWatchUcnt` 名为"整场累计看播人数"=**396**(09-04),
  与 380 不同名不同值 → 两处口径不同(具体差异原因未探明,列为待对拍);
  无论 380 还是 396 都不能还原页面 GPM,再次印证 GPM 分母只能是 500(看播次数人次)。

## 3. 分钟序列数据位置与字段

端点(两场一致):`https://eos.douyin.com/life/api/live_screen/v5/room_minute_indicator`
结构:`data[]:{ key, chart:[{x:"MM-DD HH:MM", y:<该分钟值>, time_stamp:"<epoch秒>"}, …] }`
(与 M2 已复核的 `pay_order_gmv_minute_trend` 同源同结构;时间轴按分钟连续,step=60 s,
两场 597/465 行,含值为 0 的分钟,行数与直播时长对齐)。

| 数据组 key | 页面 title | 语义(推测) | 09-04 分钟求和 | 获取方式 |
|-----------|-----------|-----------|--------------|---------|
| `pay_order_gmv_minute_trend` | 成交金额 | 该分钟新增成交金额(元),与 M2 一致 | 2000(=PayGmv) | 页面默认返回 |
| `pay_order_cnt_minute_trend` | 成交订单数 | 该分钟新增成交订单 | 9(=PayOrderCnt) | 页面默认返回 |
| `OnlineUCntTrend` | 在线人数 | 该分钟在线人数(瞬时) | 1240(瞬时和,无桶意义) | 趋势面板点"在线人数" |
| `EnterUCntTrend` | 进入人数 | 该分钟进入人数 | 470 | 趋势面板点"进入人数" |
| `LeaveUCntTrend` | 离开人数 | 该分钟离开人数 | 452 | 趋势面板点"离开人数" |
| `LikeCntTrend` | 点赞次数 | 该分钟点赞次数 | 18(=累计点赞 18 ✓) | 趋势面板点"点赞次数" |

说明:每次点一个指标文案,页面会重新请求 `room_minute_indicator`,返回“当前+上一个”两个数据组
(均 597/465 行、同一分钟轴);因此 `Enter/Online/Leave/Like` 四系与 `gmv` **天然同分钟行对齐**,可供小时桶聚合直接使用。

**观看系分钟序列与累计口径的闭合检查(两场):**

| 场次 | ΣEnterUCntTrend | ΣLeaveUCntTrend | ServerWatchCntTd(看播次数人次) | LiveServerWatchUcnt(人数) |
|------|-----------------|-----------------|------------------------------|--------------------------|
| 09-04 | 470 | 452 | 500 | 380 |
| 09-02 | 440 | 420 | 450 | 360 |

→ `Enter/Leave` 分钟求和与 500/450(看播次数)或 380/360(人数)都**不闭合**
(可能因为进入/离开人数口径去重、回访剔除或统计起点不同,原因未探明)。
因此:**不能把 EnterUCntTrend 当作 GPM 分母("看播次数")的分钟分解来直接计算小时 GPM**;
若用它会被页面 GPM 对拍否决(整场 470≠500)。

**目录中存在、但本次只读点击未观察到返回的分钟 key(api_meta + RMI meta,共 16 项):**
`WatchCntTrend`(直播间看播量)、`WatchCntTrendBusiness`(直播间商业看播量)、
`WatchCntTrendNature`(直播间自然看播量)、`ShowCntTrend`(直播间曝光量)、
`ShowCntTrendBusiness/Nature`、`gpm`(千次观看成交金额,分钟级 dataKey)、
`card_show_pv_minute_trend`/`card_click_pv_minute_trend`(讲解卡曝光/点击)、
`CommentCntTrend`(评论次数)等。`WatchCntTrend` 若可得,其分钟和预期应等于
`ServerWatchCntTd`(500/450),是最贴近"观看次数分钟分解"的候选 —— **是否在回放页某入口
(图表指标下拉/大屏实时 tab/复盘报表)可取,本次只读探测未找到,列为待对拍,不臆测**。

## 4. 小时粒度结论

- 页面**没有**现成的"按小时桶"观看/成交序列端点:两场只观察到分钟序列 + 会话累计卡。
- 流量 tab 的 `flow_index` 提供**整场均值** KPI,不是小时桶:
  - `WatchCntPerHour` name=小时看播pv,unit 空,value=50.0(≈500/10h;自然 38.6 + 商业 11.4 ≈ 50.0 自洽);
  - `flow_entrance_trend` 返回各流量**渠道曝光**序列,键名形如
    `live_show_pv_hour_trend_社交分享`(含 hour 字样)但实际行数=分钟级(597/465 行,step 60s),
    内容为渠道曝光量,**非观看次数**,仅作对照。
- **结论:小时 GPM 必须由分钟序列自聚合得到;页面不直接提供小时桶读数供对拍。**
  小时桶归属建议(供后续实现参考,细则以其契约为准):
  - 桶键 `(session_date, HH)`,把分钟行按时间轴的整点归属(分钟 HH:MM → 桶 HH:00–HH:59);
  - 跨午夜场次按 `time_stamp`(epoch)转 CST 取日期归属,避免 x 无年份的歧义(沿用 M2/T8 处理);
  - 跨场叠加:默认"每场独立成表、同日期多场按桶各自输出";是否把同日多场同一小时的
    views/gmv 相加再算 GPM,须在分母口径同一的前提下由实现任务契约决定,避免不同口径桶相混。

## 4b. t6 补充探测:展开图表指标下拉找 gpm/直播间看播量分钟序列(2026-09-07,如实阴性)

> 目的:用户确认大屏可切换"千次观看成交金额(一分钟粒度)"指标曲线;t1 未展开图表指标
> 下拉。t6 针对性只读探测:在回放页展开/点击指标选择入口,寻找 (a) 千次观看成交金额
> (gpm) 分钟序列与 (b) 直播间看播量(WatchCntTrend) 分钟序列,并校验 Σ分钟 ≈
> ServerWatchCntTd。探测方式与边界同 t1(复用登录、只读、banned 端点仅记 URL 名、
> 不点商品/营销/违规带)。

**工具**:gpm_probe.py 扩展(`--tag gpm_probe2`,schema `gpm-1.1`):回到概览 → 指标选择器
文本扫描(含尾部装饰符 ▾/⌄/▼/… 回退)→ DOM 级目标词扫描(千次观看成交金额/看播量/观看次数
/GPM,记录可见性/坐标/是否横向可滚动)→ 白名单指标/选项点击(每轮观察 room_minute_indicator
是否新增数据组)→ 流量 tab 对照。

**结果(两场一致,均为阴性,如实记录)**:

| 场次 | 观测 | 结论 |
|------|------|------|
| 09-04(7000000000000000001) | 展开并点击白名单指标/选项约 20 次(直播间成交金额/当前在线人数/千次观看成交金额/累计在线人数/每分钟在线人数/人均观看时长/成交订单数/曝光量/看播用户等);DOM 中"千次观看成交金额"仅存在于**首页 KPI 卡**(4000.00 元,与 PayGmv/ServerWatchCntTd 对拍 diff=0),"看播次数"文本仅存在于隐藏的"主播成长任务激励"层;room_minute_indicator 未返回 gpm 或 WatchCntTrend 数据组 | **未取到 gpm/看播量分钟序列**;可切换分钟序列仍为 gmv/订单/进入/在线/离开/点赞(与 t1 一致) |
| 09-02(7000000000000000002) | 同上重复(证据 …_064247) | **未取到**(与 09-04 一致);GPM 卡 3000.00=1350/450×1000 diff=0 复核通过 |

- 证据:`data/probe/gpm_probe2_7000000000000000001_20260907_063522.json`、
  `data/probe/gpm_probe2_7000000000000000001_20260907_063928.json`、
  `data/probe/gpm_probe2_7000000000000000002_20260907_064247.json`
  (均含 `indicator_dropdown_probe.waves` / `dom_metric_entries_seen` / `series_keys_total`)
  及同名 `_watch_raw.json`(仅分钟序列,本地不入库)。
- 解释(只写观察到的事实):本 UI 构建的回放页中,"趋势"图表的可点击分钟指标仍是 t1 已点出的
  六项;含"千次观看成交金额/直播间看播量"的下拉/更多菜单未以可见文本形态暴露(未发现带
  ▾/⌄/▼/… 的指标切换控件,指标行亦无横向溢出),gpm/WatchCntTrend 仍只存在于接口
  api_meta/meta 目录(t1 已证)。

**未取到时的小时 GPM 分母处置**(维持 t1/t2/t3 口径纪律,不因本探测改变):
- 在线链路仍默认跳过小时 GPM 并提示"分母口径待对拍",不硬编码字段、不用 Enter/Online 代理;
- 若未来只取得 gpm 分钟率序列(无独立看播量):可用同轴分钟反推
  `views_i = gmv_i ÷ (gpm_i/1000)`,但**仅 gmv>0 且 gpm>0 的分钟成立**(gmv=0 分钟无法反推),
  得到的是"覆盖有成交分钟"的**近似观看口径,不是完整观看次数**,整场必有缺口(缺口=无成交分钟
  观看量或口径差异),只能用于可行性评估,不得作为小时 GPM 分母;相应纯函数
  `_implied_views_minutes()` 随 gpm_probe.py 提供(gmv=0 跳过计数经离线自测)。

**细化后的遗留建议(下次再试的入口/时机,按可行度排序)**:
1. **直播中(进行中)的实时大屏**:若"千次观看成交金额(一分钟粒度)"切换入口只在直播中大屏/
   实时 tab 渲染(回放页不渲染),需在下一场账号有权限的直播中用同一 gpm_probe.py 只读探测,
   并校验 WatchCntTrend 分钟求和与 ServerWatchCntTd 闭合(涉及直播场景与排期,另行评估后执行);
2. **图表右上"更多/⋮"菜单与悬停浮现控件**:多为图标而非文本,文本启发式不可达;可由人工在
   回放页图表标题区悬停/点击并截图复核(需人工配合时如实记录,不代劳、不臆测);
3. **复盘/经营报表**:若"复盘报表/数据报表"提供分钟或小时级看播与 GPM 表格,可人工导出后
   离线对拍(只读本账号数据)。

## 4c. t10 已结束场次"复盘/数据分析页"深探(2026-09-07,真机只读,阴性)

> 目的:跳过"直播中采集",直接尝试从**已结束场次**的复盘/数据分析路径读取 小时/分钟
> 观看(看播)次数与成交金额序列,或找到"导出/下载"报表入口——若成立,可为小时 GPM 提供
> 无需直播中采集的真实分母来源。探测只读、复用登录态、banned 端点仅记 URL 名。

**工具**:独立脚本 `replay_analysis_probe.py`(`--dry-run` 支持;证据
`data/probe/replay_analysis_probe_<room_id>_<时间戳>.json`,schema `replay-analysis-1.0`,
含 `pages_visited`/`endpoints_seen`/`series_candidates`/`export_entries_seen`/`closure_checks`)。
流程:打开已结束场次回放页(liveScreen)→ 被动捕获端点 → 只读扫描页面锚点与叶子文案 →
白名单内点"查看完整复盘/趋势分析/转化分析"等导航 → 记录导出/下载/报表入口(不点击下载)→
汇总序列候选与整场闭合校验。

**结果(09-04 场 7000000000000000001,如实阴性)**:

- 访问页 4(回放页 + 点"查看完整复盘/趋势分析/转化分析",URL 同 liveScreen 族);
- 端点角色 11 个(room_minute_indicator/key_index/api_meta/conversion_funnel/portrait/
  room_info/inspire_flow_indicator/check_permission 等;**未见 analysis/report/export 端点**);
- 序列候选仅 2 个:`pay_order_gmv_minute_trend`(Σ=2000=PayGmv,闭合 ✓)与
  `pay_order_cnt_minute_trend`(Σ=11=PayOrderCnt,闭合 ✓)——与 t1 已证一致,**无观看/看播
  系分钟或小时序列**;`hourly_or_minute_watch_series=[]`;
- 导出/下载/报表入口:DOM 内**未发现**(`export_entries_seen=[]`);页面锚点中也无
  analysis/report/data 链接候选(该 UI 内复盘入口为 liveScreen 内的 tab,不另开分析页);
- key_index 卡(PayGmv/GPM/ServerWatchCntTd/LiveServerWatchUcnt/PayOrderCnt/
  ClientLiveShowCntTd)照常可得,印证回放页自身仍可对拍整场 GPM。

**结论(口径,不臆测)**:
1. **本账号"已结束场次复盘/数据分析"路径(本 UI 构建)下,未取到 小时/分钟 观看(看播)
   序列,也未发现导出/下载报表入口** —— 与 t1/t6 一致,小时 GPM 分母维持"待对拍";
2. 已结束场次读取路径的候选序列仍是 分钟成交金额/订单数(闭合)与 进入/在线/离开/点赞
   (与累计不闭合,t1 已证),不能直接作为看播次数分母;
3. 复盘/导出若存在于**其它产品入口**(如巨量百应/数据中心报表页、liveScreen 之外的其他
   URL 域),本探测未发现入口;列入遗留,需人工在对应产品页确认后另测(不臆测 URL)。

**证据**:`data/probe/replay_analysis_probe_7000000000000000001_20260907_144631.json`
(pages_visited/nav_clicks/endpoints_seen/series_candidates/export_entries_seen/closure_checks,
样例截断;不入库)。

## 4d. t11 人工协助复现:回放页"千次观看成交金额"分钟序列入口(2026-09-07)

> 目的:用户确认 已结束回放页 中"千次观看成交金额(按分钟)"选项**可见**,但自动化探测
> (t1/t6/t10)两次未命中其点击入口——用**人工协助**方式复现准确点击路径:脚本只读监听,
> 用户在已打开浏览器里手动切换趋势指标,记录切换后是否出现新 series key。

**工具**:独立脚本 `assisted_replay_probe.py`(`--dry-run` 支持;schema `assisted-replay-1.0`;
证据 `data/probe/assisted_replay_<room_id>_<时间戳>.json`,含 `waves`/`series_keys_total`/
`target_hits`/`dom_metric_entries_seen`/`user_action_observed`/`closure_checks`)。模式:
有头打开回放页 → 长窗口监听(默认 6 分钟,可 `--minutes` 调整,到点优雅退出)→ 周期读入
room_minute_indicator 增量 + DOM 指标选项扫描 → **周期性在终端打印给用户的人工操作提示**
(把趋势指标切换为 千次观看成交金额/直播间看播量)。脚本**不做任何指标点击**(切换由用户
人工完成,保持只读、不猜入口)。

**本次真机窗口(09-04 场 7000000000000000001,2026-09-07 15:01,如实阴性)**:

- 监听 10 分钟 × 38 波次(`--minutes 10 --interval-sec 15`),页面状态 ended,登录会话自动复用;
- 期间**未观测到用户操作**(`user_action_observed=[]`),**未捕获新 series key**
  (`target_hits=[]`;series_keys_total 仍为 pay_order_gmv/cnt_minute_trend 两项);
- DOM 只读扫描(共记录 456 条)显示:文本"千次观看成交金额 4,000.00元"在页面**可见**
  (DIV,visible=true,x=462,y=123)——与用户"该文案在回放页可见"的说法一致,但该文本
  属于**首页 KPI 卡**(与 t1 已证一致),**不是趋势图指标切换选项**;
- 脚本在窗口内周期性输出人工提示(共 10 次),到点优雅退出,证据 JSON 正常落盘可解析。

**结论(口径,不臆测)**:
1. 本次人工协助窗口内用户未完成切换 → 无法据此判定"千次观看成交金额"分钟曲线的真实入口;
   自动化仍只观察到 KPI 卡文本,DOM 中未出现趋势指标切换选项文本;
2. 该功能入口**仍需一次"用户在场"的会话**复现:运行
   `python assisted_replay_probe.py --room-id <room_id> --minutes 10`,由用户在打开的
   浏览器中把趋势图指标切换为"千次观看成交金额"(或直播间看播量),脚本会自动记录;
   若用户能提供入口位置截图/点开后的选项列表,可直接据此确定选择器(免去猜测);
3. 在真实分母入口确认前,小时 GPM 分母维持"待对拍"(t1/t2/t3 纪律不变)。

**证据**:`data/probe/assisted_replay_7000000000000000001_20260907_150148.json`
(waves 38/series_keys_total/dom_metric_entries_seen 456 条含坐标/user_prompt 全文/
conclusions;样例截断;不入库)。

**attempt 2(2026-09-07,补充自动化定位,仍阴性)**:DOM 细粒度定位与候选点击
(scratch:switch_candidates.json/popup_leafs.json/截图 head.png、full.png)确认
"千次观看成交金额"仅 KPI 卡文本(x≈462,y≈125);趋势图例区仍为 成交金额/订单数/
在线人数/进入/离开/点赞 六项 + 视频/商品/营销/广告/违规 popper(禁点);对 more
图标(~849,331)/卡控件/图例芯片白名单点击均未新增 RMI 数据组(监听链路正常)。
→ 自动化文本/坐标启发无法命中,入口需用户在场。

**attempt 3(用户在场,2026-09-07 22:38,捕获成功)**:

- **入口(用户确认,权威)**:回放页趋势图**右上角齿轮图标 →「自定义指标」弹窗**(允许勾选
  1–6 项;列表含 千次观看成交金额、直播间观看量(看播量)、直播间自然观看量 等)→ 勾选后
  **确定**;趋势图随即显示多条曲线(含 千次观看成交金额、直播间观看量)。自动化两次未命中
  即因该入口是"齿轮图标 + 弹窗勾选",非趋势图图例文本可及;
- 用户在监听窗口内按该路径手动操作(第 16 波 ~22:32 捕获 **`gpm`**、第 17 波 ~22:33
  捕获 **`WatchCntTrend`**,证据 `assisted_replay_7000000000000000001_20260907_223820.json`,
  38 波,可解析)——证明该入口在**回放页回放会话**可用,捕获到即为真实页面自发 RMI 数据;
- 两序列均**整场 597 行分钟轴**(09-04 09:04~19:00,与 pay_order_gmv_minute_trend
  同轴):`WatchCntTrend` title=直播间看播量,Σ=494,min0/max7;`gpm` title=千次观看
  成交金额,Σ=87300(**分钟率求和,无闭合意义**,同 t6;仅可作 gmv>0 分钟反推近似);
- key_index(捕获时刻):PayGmv=2000、ServerWatchCntTd=501、GPM=3992.02
  (=2000/501×1000 自洽)、ClientLiveShowCntTd=7990;
- 闭合校验:`WatchCntTrend` Σ=494 vs ServerWatchCntTd=501 → **diff=-7(≈98.6%,接近但
  未精确闭合;对照 t1 稳定值 500 则 diff=-6≈98.8%)**。如实记录:WatchCntTrend 是
  "累计看播次数"分钟分解的**最强候选**,但存在 6–7 人次缺口(可能来自切换时点部分分钟
  未计入/统计口径边界/key_index 快照值随会话推进微变),按纪律**不把"未精确闭合"写成
  "已闭合"**,列为待复核项。

**t12 整场闭合复核(用上述 22:38 无中断 38 波捕获证据,首/末边界假设实测)**:
- 证据行统计:597 行全轴(09-04 09:04~19:00),`nonzero_head` 首非零在 **09:07**(09:04–09:06
  为 0),`sample_tail` 末行 19:00 为 0;会话 09:04:21 开播 → **首约 3 分钟(09:04–09:06)
  趋势为 0、末分钟为 0**;
- 假设检验:若把 501−494=7 归因于"首/末分钟边界未计入",则前 3 分钟应≈7 人次,但实测
  WatchCntTrend 前 3 分钟为 0 → **该缺口不能由首/末分钟边界单独解释**(疑为 口径差异:
  累计卡 ServerWatchCntTd 含开播瞬间/口径含 预进场次,或分钟序列统计起点略晚);
- 使用口径(如实标注,不冒充精确):小时 GPM 分母采用 **WatchCntTrend 分钟和**,输出
  `watch_semantics` 固定注明 "Σ494 vs ServerWatchCntTd 500–501,复核差 −6/−7(≈1%),口径
  非精确闭合";t12 实现以该语义串随观看档透传,用户/审计可见复核差。

**结论(t11 之后,口径更新)**:
1. **已结束回放页确可经"用户手动切换趋势指标"返回 gpm/WatchCntTrend 分钟序列**——
   自动化两次未命中是因该控件非文本可及;人工可复现,入口=趋势图指标切换(用户确认);
2. WatchCntTrend 与 gmv 同轴同窗,是**小时 GPM 真实分母的最强候选**;正式启用前需一次
   "切换后整场无中断监听"复核 Σ 与结束态 ServerWatchCntTd 精确闭合并核对单位/口径文案;
3. 口径纪律不变:t2/t3 仍不硬编码分母;复核通过后另立任务把 WatchCntTrend 接入在线/
   回放链路并解除"待对拍"。

**自动化可行性复核(2026-09-07,probe_gear 实测,scratch 不入库)**:在回放页复开会话,
按"齿轮图标"定位后**脚本可自动打开「自定义指标」弹窗**——齿轮候选区(~x731–869,y331–343)
中 `DIV.cls=iIFiN@(869,341)` 点击即弹出弹窗(DOM 可见「自定义指标」标题、选项行
`直播间看播量/直播间商业观看量/直播间自然观看量/千次观看成交金额`、底部 取消/确定 按钮),
与用户描述一致;同一批候选另含图例 radio(DIV.cls=hK_mR@(731,342),点击会增删 LikeCntTrend
等图例项,与自定义弹窗不同,勿混淆)。结论:该入口**具备脚本自动化的 DOM 路径**(点齿轮 →
弹窗内勾选目标行 → 确定),已固化为 `exporter/watch_capture.py`(t12/t13):自动模式优先尝试,
弹窗选项行按「自定义指标」modal 作用域扫描(排除页面同名 KPI/图例文本,t13 修复),多次自动
尝试仍失败时提供 `--assist`(用户点一次齿轮→勾选→确定),脚本轮询捕获。

**t12 集成说明(2026-09-07)**:`exporter/watch_capture.py` 将上述入口封装为可调用捕获
(写观看档 `live_<ymd>_<room>_watch_min.csv`,表头 time,views,semantics;semantics 内嵌
Σ WatchCntTrend vs 同刻 ServerWatchCntTd 与 diff,如实标注);main.py 的
`export --capture-watch`(单场/按天)与 `daily --capture-watch` 自动尝试捕获,成功后
小时 GPM 以真实看播量为分母输出(单场 `_hourly.csv` / 日报 `小时GPM_<ymd>.csv`),
失败优雅降级(提示 `--assist` 命令);离线全链路用例与 README 同步(见
docs/直播中采集指引.md、README 小时级 GPM 章节)。

**UI 约束补充(用户确认,2026-09-07)**:回放页趋势图**同屏最多显示 6 个指标**(「自定义
指标」弹窗提示 最少1/最多6)。因此捕获自动化自配置不能只勾选目标项——必须先取消若干非必要
项使总数 ≤6(默认保留集合:成交金额、成交订单数、在线人数、进入人数、直播间观看量、
千次观看成交金额 = 恰好 6 项),且 **成交金额(gmv 分子)必须始终保留**(否则确定不生效/
分子缺失)。实现:`exporter/watch_capture.py` 提供 `plan_metric_changes()` 纯函数与
`_DIALOG_ROWS_JS`(读选项行勾选态)做 取消多余→勾选目标→确定 的 reconcile;保留集合可经
config `export.watch_capture_keep_metrics` 配置(成交金额强制置首);勾选态未知的未保留行
保守不强行取消(转 assist 人工确认),离线用例覆盖(≤6、gmv 必留、态未知三种)。

**t13 自动化收尾(2026-09-07,真机单窗口验证通过=免人工)**:把最后的“点图例芯片让曲线
显示”做成自动,并对弹窗交互两处稳定性做了修复:
- **弹窗行扫描改为「自定义指标」modal 作用域**:原全局扫描会把页面同名词文本
  (KPI 卡子标签如 “成交金额Top1”、图表图例芯片行)误当弹窗选项——勾选态判 unknown
  导致 reconcile 拒绝,或把“成交金额”误加入 to_check 点错坐标关掉弹窗。现在先定位含
  标题“自定义指标”的 `.arco-modal`,只在内部扫行(实测 22 行,两列 + 右侧当前指标区),
  勾选态由行内 `LABEL.arco-checkbox > input.checked` / class 判定;
- **「确定」按钮命中加固**:叶子文本精确匹配外,增加 primary 按钮容器回退,并在勾选动作后
  等重渲染 + 一次重扫;
- **确定后自动点按图例芯片**:若 WatchCntTrend 未即返回,扫描图表上方芯片带(实测 6 芯片
  y≈342,x 从 ~200 起:成交金额/订单数/在线/进入/千次观看成交金额/直播间观看量),只点按
  开关态明确 off 的目标芯片;态不可判(unknown)保守不盲点,仍缺时做**单次知情点按**(已文本
  定位芯片的一次切换,记录 `chip_fallback_click`,不循环),再转 assist 人工兜底
  (`chip_assist_hint`,如“人工点一次芯片”);文本定位失败且配置了
  `export.watch_capture_chip_anchor` 时坐标标定一次并记录;
- 纯函数决策 `plan_chip_clicks()` / `chip_assist_hint()` / `decide_watch_chip_fallback()`
  离线用例 17 项(tests/test_watch_capture_chip.py),与既有 8 项链路用例一起全绿;
- **真机验证(2026-09-07 23:57,单窗口、用户在位观察)**:自动完成 齿轮→弹窗 reconcile
  (取消 离开/点赞,勾选 千次观看成交金额/直播间观看量)→ 确定 →(序列未即返回)芯片带扫描
  6 芯片 → 单次知情点按 直播间观看量 芯片(x763,y342)→ 捕获 WatchCntTrend 整场 597 行,
  Σ494 vs 同刻 ServerWatchCntTd=501(diff=-7 如实标注),**全程零人工点击**
  (assist_steps=[]);证据 `data/probe/watch_capture_7000000000000000001_20260907_235720.json`。

## 5. 给后续实现(GPM 聚合/导出)的使用建议

1. 分子:小时成交金额 = 该小时桶内 `pay_order_gmv_minute_trend` 分钟值求和(与 M2 全行求和
   一致性已复核:Σ=gmv 分钟求和=PayGmv,两场成立)。与现有成交金额日报/按天导出天然联动。
2. 分母(小时观看次数):**口径未定前不得假设**。已对拍证实的是整场 GPM 分母=`ServerWatchCntTd`
   (累计看播次数人次);其分钟分解候选 `WatchCntTrend`(直播间看播量)**已由 t11 人工协助
   确认可从回放页经"趋势指标切换"取到**(整场 597 行,与 gmv 同轴),但本捕获 Σ=494 vs
   ServerWatchCntTd=501 存在 7 人次缺口,**未精确闭合**(见 §4d)。
   - 待"切换后整场无中断监听"复核 Σ 与结束态 ServerWatchCntTd 精确闭合(并核对单位/
     口径文案)后,即可 小时 GPM = Σ小时gmv ÷ Σ小时看播量 × 1000,且整场自洽;
   - 在复核通过前,小时 GPM 输出应**明确标注"分母口径待复核"**,或仅输出 views/gmv 两列
     原始桶,严禁用 `EnterUCntTrend`/`OnlineUCntTrend` 等代理序列冒称"观看次数"。
3. 时间轴一致性:views 与 gmv 必须来自同一 `room_minute_indicator` 读取(同分钟轴),
   桶化前按行对齐校验(逐行 time 一致),沿用 M3 extra_metrics 的“严格对齐”思路。
4. 页面无小时桶可直接对拍 → 验收口径以"分钟求和自洽 + 整场页面 GPM 对拍"为锚。

## 6. 证据文件(本次产出,data/probe/ 不入库)

- `data/probe/gpm_probe_7000000000000000001_20260907_044430.json`(09-04 场:命中层级/字段样例/
  key_index 相关卡/GPM 对拍/观看系候选/checksum/结论);
- `data/probe/gpm_probe_7000000000000000002_20260907_044727.json`(09-02 场,同上,复核稳定性);
- `data/probe/gpm_probe_7000000000000000001_20260907_045114_watch_raw.json`
  (仅观看系/成交金额分钟序列的本地原始备份,供后续小时桶聚合与对拍复核;含行数统计,不入库)。
- 另:沿用既有 `probe.py` 常规探测的证据 `data/probe/probe_7000000000000000002_20260904_205812.json`
  (M2 同源),可作交叉引用。
- t6(补充探测)证据:见 §4b —— `data/probe/gpm_probe2_*.json` 与同名 `_watch_raw.json`(本地,不入库)。
- t10(复盘/分析页深探)证据:见 §4c —— `data/probe/replay_analysis_probe_7000000000000000001_20260907_144631.json`(本地,不入库)。
- t11(人工协助复现)证据:见 §4d —— `data/probe/assisted_replay_7000000000000000001_20260907_150148.json`(首轮阴性)与 `data/probe/assisted_replay_7000000000000000001_20260907_223820.json`(用户在场捕获 gpm/WatchCntTrend 成功;本地,不入库)。
- t13(自动化收尾)证据:见 §4d —— `data/probe/watch_capture_7000000000000000001_20260907_235720.json`(免人工单窗口验证:gear→modal reconcile→确定→单次知情点按 直播间观看量 芯片(x763,y342)→捕获,Σ494 vs ServerWatchCntTd=501;本地,不入库)。

## 7. 待人工复核清单(遗留,如实标注)

1. **WatchCntTrend/gpm 分钟序列已可由人工切换取到(t11),待精确闭合复核**:
   自动化(t1/t6/t10)因入口非文本可及而未命中;t11 人工协助确认入口=回放页趋势指标
   切换,捕获 WatchCntTrend/gpm 整场 597 行(§4d)。**该入口已自动化(t12+t13,2026-09-07
   真机单窗口实测免人工)**:t12 齿轮→「自定义指标」modal→≤6 reconcile→确定;t13 确定后
   自动点按图例芯片使曲线显示并捕获。遗留:
   - 一次"切换后整场无中断"监听,复核 ΣWatchCntTrend 与结束态 ServerWatchCntTd 精确
     闭合(diff≈0)并核对单位/口径文案;闭合后解除"分母待复核"并接入小时 GPM;
   - 直播中实时大屏指标下拉仍为交叉验证入口(准备见 docs/直播中采集指引.md)。
2. 复核 概览"累计在线人数"380 与 流量"整场累计看播人数"396 两处口径差异的页面位置与定义。
3. 复核小时 GPM 无页面直接读数时的验收锚点(建议用整场 GPM 卡对拍 + 分钟求和自洽)。
4. 换账号/换场次后:上述 key 名、`key_index.GPM` 语义、`ServerWatchCntTd` 可用性是否一致。

## 8. 合规与边界确认

- 全程复用已登录持久化会话,只读;未做登录绕过、未做 `a_bogus` 等签名逆向;
- 商品(`product_trend`/`follow_product`/`product_ai_tip`)、营销(`marketing_data`/
  `local_ads_show_info`)、违规/惩罚(`punish_info`)、广告(`lamp`/`roi2`)端点主体**未采集**
  (探测脚本仅记录 URL 名);UI 上不点击商品/营销/违规/视频/广告类文案;
- 证据样例截断(前后各 3 行),不整场转储;观看系原始备份仅限分钟数值序列(本地,不入库);
- profile/凭据未提交;证据与备份均在 `.gitignore` 覆盖的 `data/probe/` 内。
