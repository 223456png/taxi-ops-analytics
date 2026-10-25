-- ============================================================
-- 指标口径层
-- 每个指标写清：公式、分母、业务含义
-- 这张表就是「指标体系」的 SQL 落地，也直接对应 Power BI 的度量值
-- ============================================================

-- ------------------------------------------------------------
-- M1 大盘核心指标（月度）
-- 北极星：GMV。下面五个是拆解维度，任一个异动都能往下钻
-- 注意：小费覆盖率受支付结构影响（现金小费大多不记录），不能直接当服务质量；
--       按支付方式拆开看 M2。
-- ------------------------------------------------------------
SELECT
    d.year_month                                              AS 月份,
    COUNT(*)                                                  AS 行程量,
    ROUND(SUM(f.total_amount), 0)                             AS GMV,
    ROUND(AVG(f.total_amount), 2)                             AS 客单价,
    ROUND(AVG(f.duration_min), 2)                             AS 单均时长_分钟,
    ROUND(AVG(f.speed_mph), 2)                                AS 平均速度_mph,
    ROUND(100.0 * AVG(f.is_airport), 2)                       AS 机场单占比_pct,
    ROUND(100.0 * AVG(CASE WHEN f.tip_amount > 0 THEN 1 ELSE 0 END), 2) AS 小费覆盖率_pct,
    ROUND(AVG(f.tip_amount), 2)                               AS 单均小费
FROM fact_trips f
JOIN dim_date d USING (date_key)
GROUP BY 1 ORDER BY 1;

-- ------------------------------------------------------------
-- M2 支付方式结构（结构性指标，用来发现支付渠道迁移）
-- 注意：用 LEFT JOIN，不能丢 payment_type=0（2024 的 Flex Fare）或其他未映射类型；
--       M2 的行程量合计必须能对回 M1 的 9,217,207。
-- ------------------------------------------------------------
SELECT
    COALESCE(p.payment_name, '未知')                          AS 支付方式,
    COUNT(*)                                                  AS 行程量,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)        AS 占比_pct,
    ROUND(AVG(f.total_amount), 2)                             AS 客单价,
    ROUND(100.0 * AVG(CASE WHEN f.tip_amount > 0 THEN 1 ELSE 0 END), 2) AS 小费覆盖率_pct
FROM fact_trips f
LEFT JOIN dim_payment p USING (payment_id)
GROUP BY 1 ORDER BY 2 DESC;

-- ------------------------------------------------------------
-- M3 区域贡献 Top 10（用上车区域）
-- 同时给「累计占比」，回答「头部区域吃掉多少单」
-- ------------------------------------------------------------
WITH z AS (
    SELECT
        COALESCE(z.borough, '未知')  AS 行政区,
        COALESCE(z.zone_name, '未知') AS 上车区域,
        COUNT(*)                      AS 行程量,
        ROUND(SUM(f.total_amount), 0) AS GMV,
        ROUND(AVG(f.total_amount), 2) AS 客单价,
        ROUND(AVG(f.duration_min), 1) AS 单均时长_分钟
    FROM fact_trips f
    LEFT JOIN dim_zone z ON f.pu_zone_id = z.zone_id
    GROUP BY 1, 2
)
SELECT
    行政区, 上车区域, 行程量, GMV, 客单价, 单均时长_分钟,
    ROUND(100.0 * 行程量 / SUM(行程量) OVER (), 2)                    AS 行程占比_pct,
    ROUND(100.0 * SUM(行程量) OVER (ORDER BY 行程量 DESC)
          / SUM(行程量) OVER (), 2)                                   AS 累计占比_pct
FROM z
ORDER BY 行程量 DESC
LIMIT 10;

-- ------------------------------------------------------------
-- M4 时段分布（运营排班的直接依据）
-- ------------------------------------------------------------
SELECT
    hour_of_day                                               AS 小时,
    COUNT(*)                                                  AS 行程量,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)        AS 时段占比_pct,
    ROUND(AVG(total_amount), 2)                               AS 客单价,
    ROUND(AVG(speed_mph), 2)                                  AS 平均速度,
    ROUND(100.0 * AVG(CASE WHEN tip_amount > 0 THEN 1 ELSE 0 END), 2) AS 小费覆盖率_pct
FROM fact_trips
GROUP BY 1 ORDER BY 1;

-- ------------------------------------------------------------
-- M5 距离分层（需求结构指标）
-- 短途/中长途的占比变化，是判断「需求结构」还是「总量」问题的关键
-- ------------------------------------------------------------
SELECT
    CASE distance_band
         WHEN '1_short' THEN '1. 短途 <2mi'
         WHEN '2_mid'   THEN '2. 中途 2-5mi'
         WHEN '3_long'  THEN '3. 长途 5-10mi'
         ELSE                '4. 超长 10mi+' END              AS 距离分层,
    COUNT(*)                                                  AS 行程量,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)        AS 占比_pct,
    ROUND(AVG(total_amount), 2)                               AS 客单价,
    ROUND(AVG(duration_min), 1)                               AS 单均时长_分钟,
    ROUND(AVG(speed_mph), 2)                                  AS 平均速度
FROM fact_trips
GROUP BY 1 ORDER BY 1;

-- ------------------------------------------------------------
-- M6 行政区成交分布（不是供给/需求对比）
-- 注意：这里只能看「成交」，看不到未满足需求——报告里要写明这个边界
-- ------------------------------------------------------------
SELECT
    COALESCE(z.borough, '未知')                               AS 行政区,
    COUNT(*)                                                  AS 行程量,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)        AS 行程占比_pct,
    ROUND(SUM(f.total_amount), 0)                             AS GMV,
    ROUND(AVG(f.total_amount), 2)                             AS 客单价,
    ROUND(AVG(f.distance_mi), 2)                              AS 平均里程,
    ROUND(AVG(f.speed_mph), 2)                                AS 平均速度
FROM fact_trips f
LEFT JOIN dim_zone z ON f.pu_zone_id = z.zone_id
GROUP BY 1 ORDER BY 2 DESC;
