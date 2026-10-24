-- ============================================================
-- 异动检测与归因
-- 方法：同星期基线（避免周末效应）+ 偏离度 + 维度拆解
--
-- 注意：基线必须在「同一星期几的多个日期」上取均值，
--      所以 CTE 里必须保留 date_key 到 GROUP BY，
--      否则会把多天合并成一行，AVG 退化成总和（会把 -27% 算成 -94%）。
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
           MEDIAN(avg_miles)     AS base_miles,
           MEDIAN(avg_speed_mph) AS base_speed
    FROM d GROUP BY dow
)
SELECT d.*, b.base_trips, b.base_aov, b.base_miles, b.base_speed,
       ROUND(100.0 * (d.trips / b.base_trips - 1), 2)  AS trips_dev_pct,
       ROUND(d.trips - b.base_trips, 0)                AS trips_gap,
       ROUND((d.trips - b.base_trips) * b.base_aov, 0) AS revenue_gap
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
-- 关键：GROUP BY 含 date_key，基线才是「日均」而不是「12 天累计」
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
    ROUND(MEDIAN(CASE WHEN grp = 'base' THEN trips END), 0)            AS 其他周二日均,
    ROUND(100.0 * (SUM(CASE WHEN grp = 'storm' THEN trips END)
          / NULLIF(MEDIAN(CASE WHEN grp = 'base' THEN trips END), 0) - 1), 1) AS 偏离_pct
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
    ROUND(MEDIAN(CASE WHEN grp = 'base' THEN trips END), 0)  AS 基准日均,
    SUM(CASE WHEN grp = 'storm' THEN trips END)           AS 暴雪日,
    ROUND(100.0 * (SUM(CASE WHEN grp = 'storm' THEN trips END)
          / NULLIF(MEDIAN(CASE WHEN grp = 'base' THEN trips END), 0) - 1), 1) AS 偏离_pct,
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
    ROUND(MEDIAN(CASE WHEN grp = 'base' THEN trips END), 0)  AS 基准日均,
    SUM(CASE WHEN grp = 'storm' THEN trips END)           AS 暴雪日,
    ROUND(100.0 * (SUM(CASE WHEN grp = 'storm' THEN trips END)
          / NULLIF(MEDIAN(CASE WHEN grp = 'base' THEN trips END), 0) - 1), 1) AS 偏离_pct,
    ROUND(AVG(amt), 2) AS 客单价
FROM seg
GROUP BY 1 ORDER BY 1;

-- ------------------------------------------------------------
-- A6 区域拆解：哪些行政区跌得最狠
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
    ROUND(MEDIAN(CASE WHEN grp = 'base' THEN trips END), 0) AS 基准日均,
    SUM(CASE WHEN grp = 'storm' THEN trips END)          AS 暴雪日,
    ROUND(100.0 * (SUM(CASE WHEN grp = 'storm' THEN trips END)
          / NULLIF(MEDIAN(CASE WHEN grp = 'base' THEN trips END), 0) - 1), 1) AS 偏离_pct
FROM z
GROUP BY 1
HAVING SUM(CASE WHEN grp = 'storm' THEN trips END) > 100
ORDER BY 4;

-- ------------------------------------------------------------
-- A7 供需紧张度代理：量、速、时长、里程一起看
-- 若「量跌 + 速度不降 + 里程缩短」，指向需求收缩而非供给不足
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
