# Power BI 数据模型设计

> 目标：打开 Power BI Desktop，按本文建好模型，粘贴 DAX，30 分钟能还原全部报表。

## 一、整体结构

星型模型，一张事实表 + 四张维表。

```
                    ┌──────────────┐
                    │  dim_date    │
                    │  date_key PK │
                    └──────┬───────┘
                           │ 1:*
┌──────────────┐    ┌──────┴──────────────┐    ┌────────────────┐
│  dim_zone    │    │     fact_trips       │    │  dim_payment   │
│  zone_id PK  ├───*│  trip_id        PK   │*───┤  payment_id PK │
└──────────────┘    │  date_key       FK   │    └────────────────┘
                    │  pu_zone_id     FK   │
                    │  do_zone_id     FK   │    ┌────────────────┐
                    │  payment_id     FK   │*───┤   dim_rate     │
                    │  rate_id        FK   │    │  rate_id   PK  │
                    │  ...measures         │    └────────────────┘
                    └──────────────────────┘
```

**为什么用星型而不是宽表**：事实表 923 万行，宽表会重复存储维表属性（行政区名、支付方式名），体积膨胀 3–4 倍，且维度变更要重刷全表。星型只存 ID，维表几行，DAX 用 RELATED 取值。

## 二、表定义

### fact_trips（事实表，923 万行）

粒度：**一行 = 一次完成的行程**

| 字段 | 类型 | 说明 |
|---|---|---|
| trip_id | Int64 | 主键，`ROW_NUMBER()` 生成 |
| date_key | Date | 上车日期，关联 dim_date |
| hour_of_day | Int8 | 上车小时 0–23 |
| pickup_ts / dropoff_ts | DateTime | 上下车时间 |
| pu_zone_id | Int16 | 上车区域，关联 dim_zone |
| do_zone_id | Int16 | 下车区域，关联 dim_zone（**不建活动关系**，见下） |
| payment_id | Int8 | 支付方式，关联 dim_payment |
| rate_id | Int8 | 费率类型，关联 dim_rate |
| distance_mi | Double | 里程（英里） |
| duration_min | Int32 | 时长（分钟） |
| speed_mph | Double | 速度，已过滤 >60 mph 的异常值（0.18%） |
| pax | Int8 | 乘客数 |
| fare_amount, tip_amount, tolls_amount | Double | 计价明细 |
| total_amount | Double | 总金额 |
| is_airport | Int8 | 1 = 机场单 |
| distance_band | Text | 1_short / 2_mid / 3_long / 4_very_long |

**注意 pu_zone_id 和 do_zone_id 的关系**：两张都指向 dim_zone，但 Power BI 不允许两条活动关系。只保留 pu_zone_id 的活动关系，do_zone_id 设为非活动（`USERELATIONSHIP` 按需激活）。

### dim_date（91 行）

| 字段 | 类型 | 说明 |
|---|---|---|
| date_key | Date | 主键 |
| yr / mon / dom | Int | 年 / 月 / 日 |
| dow | Int | 0 = 周日 |
| year_month | Text | `2024-01` 格式，用于横轴 |
| is_weekend | Int8 | 1 = 周末 |

**标记为日期表**：右键 dim_date → 标记为日期表 → 选 date_key。这样时间智能函数（MTD/YTD/DATEADD）才能用。

### dim_zone（265 行）

| 字段 | 类型 | 说明 |
|---|---|---|
| zone_id | Int16 | 主键，对应 TLC 的 LocationID |
| borough | Text | 行政区：Manhattan / Queens / Brooklyn / Bronx / Staten Island / EWR |
| zone_name | Text | 区域名，如 JFK Airport、Midtown Center |
| service_zone | Text | Yellow Zone / Boro Zone / EWR |

### dim_payment（6 行）/ dim_rate（7 行）

小维表，直接内嵌即可，不必单独建表（也可用 DAX 的 `SWITCH` 实现）。

## 三、关系

| 从 | 到 | 基数 | 筛选方向 | 状态 |
|---|---|---|---|---|
| fact_trips[date_key] | dim_date[date_key] | 多对一 | 单向 | 活动 |
| fact_trips[pu_zone_id] | dim_zone[zone_id] | 多对一 | 单向 | 活动 |
| fact_trips[do_zone_id] | dim_zone[zone_id] | 多对一 | 单向 | **非活动** |
| fact_trips[payment_id] | dim_payment[payment_id] | 多对一 | 单向 | 活动 |
| fact_trips[rate_id] | dim_rate[rate_id] | 多对一 | 单向 | 活动 |

**筛选方向一律单向**。双向筛选在这套模型里没有收益，只会拖慢查询。

## 四、导入步骤

1. **数据源**：用 `python/01_etl.py` 导出的 `powerbi/exports/*.csv`（Parquet 需要额外驱动，CSV 最省事）
2. **Power Query**：直接「获取数据 → 文本/CSV」，编码选 UTF-8，第一行作标题
3. **类型检查**：导入后确认 `date_key` 是日期、`total_amount` 是小数、`hour_of_day` 是整数
4. **建关系**：按上表在「模型」视图连线，注意 do_zone_id 建成非活动
5. **标记日期表**：dim_date → 标记为日期表
6. **建度量值**：把 `dax_measures.md` 里的度量值逐个粘贴到 fact_trips 表下

## 五、性能注意

923 万行对 Power BI 不算大，但几个地方要注意：

- **不要在事实表上用 `COUNTROWS` 做全表计数**，用 `COUNTROWS(fact_trips)` 在视觉对象里会被优化，但如果在度量值里再包一层 `FILTER` 就会退化成逐行扫描
- **避免在度量值里用 `SUMX` 遍历事实表**，能改写成 `SUM` + 已计算列就改写（比如 `distance_band` 已经是列，不要动态算）
- **速度指标一定要排除 NULL**：`AVERAGE` 会自动忽略空值，但 `SUM/count` 手写除法不会，会得到错误结果

## 六、和 SQL 层的对应

Power BI 的模型和 `sql/01_build.sql` 建的星型模型一一对应，字段名完全一致。这样 SQL 里验证过的口径可以直接搬到 DAX，不用二次翻译。

对账方式：SQL 跑出的月度 GMV 和 Power BI 里 `[GMV]` 度量值在月份切片下应该完全一致（`report/sql_results.md` 里有 SQL 侧的数字）。
