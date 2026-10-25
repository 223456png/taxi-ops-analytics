# DAX 度量值清单（35 个）

> 全部建在 `fact_trips` 表下。分类按使用频率排，不是按功能。
> 基础指标口径与 `sql/02_metrics.sql` 对齐；异动类度量值（同星期基线/缺口营收）必须用下面的 **MEDIANX** 版本，旧版的 SUM 写法会把 13 个周二加总，得到约 128 万的假基线。

---

## 一、基础度量（7 个）

### 1. 行程量
```dax
行程量 = COUNTROWS ( fact_trips )
```
> 用 `COUNTROWS` 而不是 `COUNTROWS(fact_trips[trip_id])`，后者会多一次空值检查。

### 2. GMV
```dax
GMV = SUM ( fact_trips[total_amount] )
```

### 3. 客单价
```dax
客单价 = DIVIDE ( [GMV], [行程量] )
```
> 一律用 `DIVIDE` 不用 `/`。`/` 在分母为 0 时返回错误，会让整张报表显示错误图标。

### 4. 平均里程
```dax
平均里程 = AVERAGE ( fact_trips[distance_mi] )
```

### 5. 平均时长
```dax
平均时长_分钟 = AVERAGE ( fact_trips[duration_min] )
```

### 6. 平均速度
```dax
平均速度_mph = AVERAGE ( fact_trips[speed_mph] )
```
> `AVERAGE` 自动跳过 NULL，而 `speed_mph` 里就是靠在 ETL 阶段置 NULL 来排除异常值的，所以这里直接 `AVERAGE` 是对的。千万别写成 `SUM(speed)/COUNTROWS`，那会把 NULL 算成 0。

### 7. 单均小费
```dax
单均小费 = AVERAGE ( fact_trips[tip_amount] )
```

---

## 二、结构度量（6 个）

### 8. 机场单占比
```dax
机场单占比 =
DIVIDE (
    CALCULATE ( [行程量], fact_trips[is_airport] = 1 ),
    [行程量]
)
```

### 9. 短途占比
```dax
短途占比 =
DIVIDE (
    CALCULATE ( [行程量], fact_trips[distance_band] = "1_short" ),
    [行程量]
)
```

### 10. 小费覆盖率
```dax
小费覆盖率 =
DIVIDE (
    CALCULATE ( [行程量], fact_trips[tip_amount] > 0 ),
    [行程量]
)
```
> 这个比单均小费更能反映服务质量：小费金额受客单价影响，覆盖率只跟服务有关。

### 11. 信用卡支付占比
```dax
信用卡占比 =
DIVIDE (
    CALCULATE ( [行程量], dim_payment[payment_name] = "信用卡" ),
    [行程量]
)
```

### 12. 机场单客单价
```dax
机场单客单价 =
CALCULATE ( [客单价], fact_trips[is_airport] = 1 )
```

### 13. 市区单客单价
```dax
市区单客单价 =
CALCULATE ( [客单价], fact_trips[is_airport] = 0 )
```

---

## 三、异动对比（5 个）

### 14. 同星期基线
```dax
同星期基线 =
CALCULATE (
    MEDIANX ( VALUES ( 'dim_date'[date_key] ), [行程量] ),
    REMOVEFILTERS ( 'dim_date'[date_key] ),
    VALUES ( 'dim_date'[dow] )
)
```
> **这是整套报表最关键的度量值**。它按“星期几”重算基线：切到 2 月 13 日（周二）时，返回的是全部周二**日中位数**（98,450），不是全部周二的总和，也不是全月平均。旧写法 `CALCULATE([行程量], REMOVEFILTERS(date_key), VALUES(dow))` 会得到约 128 万，偏离度会算成 −94.7%。

### 15. 偏离度
```dax
偏离度 =
VAR 当前 = [行程量]
VAR 基线 = [同星期基线]
RETURN DIVIDE ( 当前 - 基线, 基线 )
```

### 16. 缺口单量
```dax
缺口单量 = [行程量] - [同星期基线]
```

### 17. 缺口营收
```dax
缺口营收 =
VAR 实际 = [GMV]
VAR 基准 =
    CALCULATE (
        MEDIANX ( VALUES ( 'dim_date'[date_key] ), [GMV] ),
        REMOVEFILTERS ( 'dim_date'[date_key] ),
        VALUES ( 'dim_date'[dow] )
    )
RETURN 实际 - 基准
```
> 用同星期营收中位数做基准，不是“缺口单量 × 当日客单价”。暴雪日客单价本身也会下降，用当期价会低估营收缺口；Python/SQL 层用的是同一个口径。

### 18. 速度偏离
```dax
速度偏离 =
VAR 当前 = [平均速度_mph]
VAR 基线 =
    CALCULATE (
        MEDIANX ( VALUES ( 'dim_date'[date_key] ), [平均速度_mph] ),
        REMOVEFILTERS ( dim_date[date_key] ),
        VALUES ( dim_date[dow] )
    )
RETURN DIVIDE ( 当前 - 基线, 基线 )
```
> 与 Python / SQL 层统一为「同星期几的每日均速中位数」，不是按行程加权、也不是每日均速的简单平均。三种算法差异可达 3–4 个百分点，别混用。

---

## 四、时间智能（5 个）

> 前提：dim_date 已标记为日期表。

### 19. 上月同期
```dax
上月行程量 =
CALCULATE ( [行程量], DATEADD ( dim_date[date_key], -1, MONTH ) )
```

### 20. 环比
```dax
行程量环比 =
VAR 上月 = [上月行程量]
RETURN DIVIDE ( [行程量] - 上月, 上月 )
```

### 21. 季度累计
```dax
QTD 行程量 = TOTALQTD ( [行程量], dim_date[date_key] )
```

### 22. 近 7 日均值
```dax
近7日日均 =
AVERAGEX (
    DATESINPERIOD ( dim_date[date_key], MAX ( dim_date[date_key] ), -7, DAY ),
    [行程量]
)
```

### 23. 近 7 日波动率
```dax
近7日波动率 =
VAR 均值 = [近7日日均]
VAR 标准差 =
    STDEVX.P (
        DATESINPERIOD ( dim_date[date_key], MAX ( dim_date[date_key] ), -7, DAY ),
        [行程量]
    )
RETURN DIVIDE ( 标准差, 均值 )
```
> 波动率突然放大是异动的前兆。这张卡放在监控页顶部，比单看绝对值更早报警。

---

## 五、占比与排名（4 个）

### 24. 区域行程占比
```dax
区域占比 =
DIVIDE (
    [行程量],
    CALCULATE ( [行程量], REMOVEFILTERS ( dim_zone[borough] ) )
)
```

### 25. 区域排名
```dax
区域排名 =
RANKX ( ALL ( dim_zone[borough] ), [行程量], , DESC, DENSE )
```


### 26. 区域跌幅排名
```dax
区域跌幅排名 =
RANKX ( ALL ( dim_zone[borough] ), [偏离度], , ASC, DENSE )
```
> 升序排：跌幅最大的排第一。用来找"最该调整的区域"。

### 27. 累计贡献（帕累托）
```dax
累计贡献 =
VAR 当前量 = [行程量]
VAR 累计 =
    CALCULATE (
        [行程量],
        FILTER (
            ALL ( dim_zone[zone_name] ),
            [行程量] >= 当前量
        )
    )
RETURN DIVIDE ( 累计, CALCULATE ( [行程量], ALL ( dim_zone[zone_name] ) ) )
```
> 帕累托分析：几个区域吃掉 80% 的行程量。用来看运力该重点保障哪里。

---

## 六、事件度量（8 个）

> 页面 2–4 没有日期切片器；下面 8 个度量把日期写死为 2-13，保证不筛选也能得到正确值。换异动日时把 `DATE ( 2024, 2, 13 )` 换掉即可。

### 28. 2-13 行程量
```dax
2-13 行程量 = CALCULATE ( [行程量], 'dim_date'[date_key] = DATE ( 2024, 2, 13 ) )
```

### 29. 2-13 同星期基线
```dax
2-13 同星期基线 =
CALCULATE (
    MEDIANX ( VALUES ( 'dim_date'[date_key] ), [行程量] ),
    REMOVEFILTERS ( 'dim_date'[date_key] ),
    'dim_date'[dow] = 2
)
```
> 2-13 是周二（dow=2）。这里直接写死 dow，避免外层再套 `CALCULATE` 时被 `REMOVEFILTERS` 把星期上下文也清掉。

### 30. 2-13 偏离度
```dax
2-13 偏离度 =
VAR cur = [2-13 行程量]
VAR base = [2-13 同星期基线]
RETURN DIVIDE ( cur - base, base )
```

### 31. 2-13 缺口单量
```dax
2-13 缺口单量 = [2-13 行程量] - [2-13 同星期基线]
```

### 32. 2-13 缺口营收
```dax
2-13 缺口营收 =
VAR cur = CALCULATE ( [GMV], 'dim_date'[date_key] = DATE ( 2024, 2, 13 ) )
VAR base =
    CALCULATE (
        MEDIANX ( VALUES ( 'dim_date'[date_key] ), [GMV] ),
        REMOVEFILTERS ( 'dim_date'[date_key] ),
        'dim_date'[dow] = 2
    )
RETURN cur - base
```

### 33. 2-13 速度偏离
```dax
2-13 速度偏离 =
VAR cur = CALCULATE ( [平均速度_mph], 'dim_date'[date_key] = DATE ( 2024, 2, 13 ) )
VAR base =
    CALCULATE (
        MEDIANX ( VALUES ( 'dim_date'[date_key] ), [平均速度_mph] ),
        REMOVEFILTERS ( 'dim_date'[date_key] ),
        'dim_date'[dow] = 2
    )
RETURN DIVIDE ( cur - base, base )
```

### 34. 2-13 整体偏离度
```dax
2-13 整体偏离度 =
VAR cur = CALCULATE ( [2-13 行程量], REMOVEFILTERS ( 'dim_zone' ) )
VAR base = CALCULATE ( [2-13 同星期基线], REMOVEFILTERS ( 'dim_zone' ) )
RETURN DIVIDE ( cur - base, base )
```
> 去掉区域筛选，始终返回当天大盘的 -31.9%；供下面的「超额缺口」做反事实基准。

### 35. 2-13 超额缺口
```dax
2-13 超额缺口 =
[2-13 缺口单量] - [2-13 同星期基线] * [2-13 整体偏离度]
```
> 区域超额缺口 = 实际 − 基线 × (1 + 整体偏离度)。Manhattan 约 **−563 单**；不要用原始缺口 `[2-13 缺口单量]`（Manhattan 约 −29,270 单），两者差的是“大盘整体跌幅”应扣掉的部分。

---

## 附：几个容易写错的地方

**1. 别在度量值里用 `SUMX` 遍历 923 万行**
```dax
-- 错：逐行扫描，秒级变分钟级
平均速度_慢 = SUMX ( fact_trips, DIVIDE ( fact_trips[distance_mi], fact_trips[duration_min] / 60 ) )

-- 对：直接用已算好的列
平均速度_mph = AVERAGE ( fact_trips[speed_mph] )
```

**2. `[同星期基线]` 的 `VALUES(dim_date[dow])` 不能省**
省掉就退化成全月平均，周末效应会污染基线。这是这套模型里最容易错的一处。

**3. 机场单用 `is_airport` 而不是 `rate_id IN {2,3}`**
`is_airport` 在 ETL 里已经包含了"费率是 JFK/Newark **或** 有机场附加费"两种情况。只用 rate_id 会漏掉一部分。

**4. `DIVIDE` 的第三参数别写 0**
默认返回空值，在图表里是断点（不显示），比显示 0 更准确——0 会让人误以为"真的降到 0 了"。
