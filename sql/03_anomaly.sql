-- ============================================================
-- 异动检测与归因
-- 方法：同星期基线（避免周末效应）+ 偏离度 + 维度拆解
--
-- 注意：基线必须在「同一星期几的多个日期」上聚合，
--      所以 CTE 里必须保留 date_key 到 GROUP BY，
--      否则会把多天合并成一行，聚合退化成总和（会把 -27% 算成 -94%）。
-- 口径约定（与 Python 侧 02_anomaly / 03_strategy 一致）：
--      基线 = 全部同星期几日（含异动日本身，13 个周二）的中位数。
--      异动日是极端值，落在中位数之外、不会拉偏基线；
--      曾有一版用 grp='base' 排除异动日取 12 天中位数，
--      与 Python 层的 13 天口径差出每天约 8 单，对账对不上。
-- ============================================================

-- ------------------------------------------------------------
-- A1 日度指标 + 同星期基线偏离
-- 基线 = 同星期几的行程量中位数（稳健，不被异动日本身拉偏）
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_daily_dev AS
WITH d AS (
    SELECT date_key, dow, is_weekend, trips, revenue, aov,
           avg_miles, avg_minutes, avg_speed_mph
    FROM v_daily
),
b AS (
    SELECT dow,
           MEDIAN(trips)         AS base_trips,
           MEDIAN(aov)           AS base_aov,
           MEDIAN(revenue)       AS base_rev,
           MEDIAN(avg_miles)     AS base_miles,
           MEDIAN(avg_speed_mph) AS base_speed
    FROM d GROUP BY dow
)
SELECT d.*, b.base_trips, b.base_aov, b.base_rev, b.base_miles, b.base_speed,
       ROUND(100.0 * (d.trips / b.base_trips - 1), 2)  AS trips_dev_pct,
       ROUND(d.trips - b.base_trips, 0)                AS trips_gap,
       -- 营收缺口用「实际营收 − 同星期营收中位数」，不是「缺口单量 × 客单价」：
       -- 暴雪日客单价本身也降了，后者会低估缺口（与 02_anomaly.py 同口径）。
       ROUND(d.revenue - b.base_rev, 0)                AS revenue_gap
FROM d JOIN b USING (dow);

-- ------------------------------------------------------------
-- A2 异动日排行
-- ------------------------------------------------------------
SELECT date_key AS 日期, dow AS 星期,
       trips AS 行程量, base_trips AS 同星期基线,
       trips_dev_pct AS 偏离_pct, trips_gap AS 缺口单量,
       revenue_gap AS 缺口营收,
       avg_miles AS 平均里程, avg_speed_mph AS 平均速度
FROM v_daily_dev
ORDER BY ABS(trips_dev_pct) DESC
LIMIT 10;

-- ------------------------------------------------------------
-- A3 异动日的时段拆解
-- 关键：GROUP BY 含 date_key，基线才是「日均」而不是「13 天累计」
-- ------------------------------------------------------------
WITH hourly AS (
    SELECT
        date_key,
        CASE WHEN date_key = DATE '2024-02-13' THEN 'storm' ELSE 'base' END AS grp,
        hour_of_day AS hr,
        COUNT(*)     AS trips
    FROM fact_trips
    WHERE dayofweek(date_key) = 2
    GROUP BY 1, 2, 3
)
SELECT
    hr AS 小时,
    SUM(CASE WHEN grp = 'storm' THEN trips END)                     AS 暴雪日,
    ROUND(MEDIAN(trips), 0)            AS 周二中位数_含异动日,
    ROUND(100.0 * (SUM(CASE WHEN grp = 'storm' THEN trips END)
          / NULLIF(MEDIAN(trips), 0) - 1), 1) AS 偏离_pct
FROM hourly
GROUP BY 1 ORDER BY 1;

-- ------------------------------------------------------------
-- A4 需求结构拆解：距离分层
-- 判断「需求总量下降」还是「出行结构改变」的核心证据
-- ------------------------------------------------------------
WITH band AS (
    SELECT
        date_key,
        CASE WHEN date_key = DATE '2024-02-13' THEN 'storm' ELSE 'base' END AS grp,
        distance_band,
        COUNT(*)            AS trips,
        AVG(duration_min)   AS mins
    FROM fact_trips
    WHERE dayofweek(date_key) = 2
    GROUP BY 1, 2, 3
)
SELECT
    distance_band AS 距离分层,
    ROUND(MEDIAN(trips), 0)  AS 基准日均,
    SUM(CASE WHEN grp = 'storm' THEN trips END)           AS 暴雪日,
    ROUND(100.0 * (SUM(CASE WHEN grp = 'storm' THEN trips END)
          / NULLIF(MEDIAN(trips), 0) - 1), 1) AS 偏离_pct,
    ROUND(AVG(mins), 1) AS 单均时长
FROM band
GROUP BY 1 ORDER BY 1;

-- ------------------------------------------------------------
-- A5 客群拆解：机场单 vs 市区单
-- 若两者跌幅接近，说明不是「机场需求单独消失」
-- ------------------------------------------------------------
WITH seg AS (
    SELECT
        date_key,
        CASE WHEN date_key = DATE '2024-02-13' THEN 'storm' ELSE 'base' END AS grp,
        is_airport,
        COUNT(*)            AS trips,
        AVG(total_amount)   AS amt
    FROM fact_trips
    WHERE dayofweek(date_key) = 2
    GROUP BY 1, 2, 3
)
SELECT
    CASE is_airport WHEN 1 THEN '机场单' ELSE '市区单' END AS 客群,
    ROUND(MEDIAN(trips), 0)  AS 基准日均,
    SUM(CASE WHEN grp = 'storm' THEN trips END)           AS 暴雪日,
    ROUND(100.0 * (SUM(CASE WHEN grp = 'storm' THEN trips END)
          / NULLIF(MEDIAN(trips), 0) - 1), 1) AS 偏离_pct,
    ROUND(AVG(amt), 2) AS 客单价
FROM seg
GROUP BY 1 ORDER BY 1;

-- ------------------------------------------------------------
-- A6 区域拆解：哪些行政区跌得最狠
-- 注意：不做 HAVING 过滤，全部行政区都列出——
-- 分解表必须能对回当日总量（8 区暴雪日合计 = 当日行程量），
-- 过滤小区域会造成"表加总 ≠ 大盘"的假象（Python 侧 03_strategy.py 同口径）。
-- ------------------------------------------------------------
WITH z AS (
    SELECT
        f.date_key,
        CASE WHEN f.date_key = DATE '2024-02-13' THEN 'storm' ELSE 'base' END AS grp,
        COALESCE(dz.borough, '未知') AS borough,
        COUNT(*) AS trips
    FROM fact_trips f
    LEFT JOIN dim_zone dz ON f.pu_zone_id = dz.zone_id
    WHERE dayofweek(f.date_key) = 2
    GROUP BY 1, 2, 3
)
SELECT
    borough AS 行政区,
    ROUND(MEDIAN(trips), 0) AS 基准日均,
    SUM(CASE WHEN grp = 'storm' THEN trips END)          AS 暴雪日,
    ROUND(100.0 * (SUM(CASE WHEN grp = 'storm' THEN trips END)
          / NULLIF(MEDIAN(trips), 0) - 1), 1) AS 偏离_pct
FROM z
GROUP BY 1
ORDER BY 4;

-- ------------------------------------------------------------
-- A7 供需紧张度代理：量、速、时长、里程一起看
-- 判读边界：量跌 + 速度不降 + 里程缩短，只能说明「不是拥堵」；
--           无法区分需求收缩与司机不出车（两者观测等价，数据无供给端指标）。
-- ------------------------------------------------------------
SELECT
    CASE WHEN date_key = DATE '2024-02-13' THEN '暴雪日 2-13' ELSE '其他周二' END AS 分组,
    COUNT(*)                          AS 天数,
    ROUND(AVG(trips), 0)              AS 日均行程量,
    ROUND(AVG(avg_speed_mph), 2)      AS 日均速度,
    ROUND(AVG(avg_minutes), 2)        AS 日均时长,
    ROUND(AVG(avg_miles), 2)          AS 日均里程
FROM v_daily
WHERE dow = 2
GROUP BY 1 ORDER BY 1;

-- ------------------------------------------------------------
-- A8 实时基线敏感性：回顾口径 vs 只用历史数据
-- 31.9% 使用了 2-13 之后的周二（未来信息），只适合回顾排名；
-- 生产监控只能用此前数据，基线更高、偏离更小（约 -28%~-31%）。
-- ------------------------------------------------------------
WITH d AS (
    SELECT date_key, COUNT(*) trips
    FROM fact_trips
    WHERE dayofweek(date_key) = 2
    GROUP BY 1
), s AS (
    SELECT MAX(CASE WHEN date_key = DATE '2024-02-13' THEN trips END) AS storm FROM d
)
SELECT '全部 13 个周二（回顾）' AS 口径, ROUND(MEDIAN(d.trips), 0) AS 基线,
       ROUND(100.0 * (s.storm / MEDIAN(d.trips) - 1), 1) AS 偏离_pct
FROM d CROSS JOIN s
GROUP BY s.storm
UNION ALL
SELECT '仅 2-13 之前周二（实时）', ROUND(MEDIAN(d.trips) FILTER (WHERE d.date_key < DATE '2024-02-13'), 0),
       ROUND(100.0 * (s.storm / MEDIAN(d.trips) FILTER (WHERE d.date_key < DATE '2024-02-13') - 1), 1)
FROM d CROSS JOIN s
GROUP BY s.storm
UNION ALL
SELECT '此前周二、剔除 1-02 节后', ROUND(MEDIAN(d.trips) FILTER (WHERE d.date_key < DATE '2024-02-13'
                                                            AND d.date_key <> DATE '2024-01-02'), 0),
       ROUND(100.0 * (s.storm / MEDIAN(d.trips) FILTER (WHERE d.date_key < DATE '2024-02-13'
                                                          AND d.date_key <> DATE '2024-01-02') - 1), 1)
FROM d CROSS JOIN s
GROUP BY s.storm
UNION ALL
SELECT '仅 1–2 月周二', ROUND(MEDIAN(d.trips) FILTER (WHERE month(d.date_key) <= 2), 0),
       ROUND(100.0 * (s.storm / MEDIAN(d.trips) FILTER (WHERE month(d.date_key) <= 2) - 1), 1)
FROM d CROSS JOIN s
GROUP BY s.storm
ORDER BY 偏离_pct;
