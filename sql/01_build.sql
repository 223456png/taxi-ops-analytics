-- ============================================================
-- NYC Yellow Taxi 2024 Q1 · 数据层
-- 引擎：DuckDB（直接读 parquet + csv，无需导入数据库）
--
-- 运行：python sql/run.py  （或 duckdb < sql/01_build.sql）
-- 依赖：data/yellow_2024-*.parquet, data/taxi_zone_lookup.csv
-- ============================================================

-- ------------------------------------------------------------
-- 0. 原始层：9,554,778 行，直接挂在 parquet 上，不落盘
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW raw AS
SELECT * FROM read_parquet([
    'data/yellow_2024-01.parquet',
    'data/yellow_2024-02.parquet',
    'data/yellow_2024-03.parquet'
]);

-- ------------------------------------------------------------
-- 1. 清洗规则（写清楚为什么这么筛，后面报告要引用）
--    a. total_amount > 0        负金额和 0 金额是计价异常，占比 1.23%
--    b. trip_distance > 0       0 里程多为司机误操作，占比 2.26%
--    c. dropoff > pickup        时间倒挂 2,801 行
--    d. 时长 1 分钟–24 小时   两端都含；0 分钟瞬时取消和超 24 小时记录属数据缺陷
--    e. pickup 落在 2024 Q1     有 21 行日期越界（不在 2024 Q1）
--    f. 里程 >100 mi          极端异常值（最大 17.7 万英里），截断为 100，不删行
--    行过滤与派生列口径与 sql/00_cleaning.sql 一致（单一事实来源在那边，
--    本文件因需要原始时间戳等列而内联同款定义——改那边必须同步这里）。
-- ------------------------------------------------------------
CREATE OR REPLACE TABLE fact_trips AS
WITH base AS (
    SELECT
        *,
        date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime) AS duration_min,
        CAST(tpep_pickup_datetime AS DATE) AS pickup_date,
        EXTRACT(hour FROM tpep_pickup_datetime)::INT AS pickup_hour
    FROM raw
    WHERE total_amount > 0
      AND trip_distance > 0
      AND tpep_dropoff_datetime > tpep_pickup_datetime
      AND tpep_pickup_datetime >= TIMESTAMP '2024-01-01 00:00:00'
      AND tpep_pickup_datetime <  TIMESTAMP '2024-04-01 00:00:00'
)
SELECT
    ROW_NUMBER() OVER ()                        AS trip_id,
    pickup_date                                 AS date_key,
    pickup_hour                                 AS hour_of_day,
    tpep_pickup_datetime                        AS pickup_ts,
    tpep_dropoff_datetime                       AS dropoff_ts,
    PULocationID                                AS pu_zone_id,
    DOLocationID                                AS do_zone_id,
    payment_type                                AS payment_id,
    COALESCE(RatecodeID, 99)                    AS rate_id,
    LEAST(trip_distance, 100.0)                 AS distance_mi,
    duration_min                                AS duration_min,
    -- 速度做合理性过滤：推算速度 >60 mph 视为记录缺陷，置 NULL（行保留）
    -- 时长 <1 分钟已在 WHERE 排除；速度用原始距离计算，避免截断后把异常行程算成慢速
    CASE WHEN duration_min >= 1
              AND trip_distance / (duration_min / 60.0) <= 60
         THEN ROUND(trip_distance / (duration_min / 60.0), 2) END AS speed_mph,
    passenger_count                             AS pax,
    fare_amount, tip_amount, tolls_amount,
    congestion_surcharge, airport_fee,
    total_amount,
    -- 业务标签：机场单（JFK/Newark 费率 或 机场附加费）
    CASE WHEN RatecodeID IN (2, 3) OR airport_fee > 0 THEN 1 ELSE 0 END AS is_airport,
    -- 业务标签：距离分层（便于分层归因）
    CASE WHEN trip_distance < 2  THEN '1_short'
         WHEN trip_distance < 5  THEN '2_mid'
         WHEN trip_distance < 10 THEN '3_long'
         ELSE '4_very_long' END                 AS distance_band
FROM base
WHERE duration_min BETWEEN 1 AND 1440;   -- 口径定义见 cleaning.sql，改这里要同步 src/01_etl.py   -- 与其他分析脚本口径一致

-- ------------------------------------------------------------
-- 2. 维表
-- ------------------------------------------------------------
CREATE OR REPLACE TABLE dim_date AS
SELECT DISTINCT
    date_key,
    EXTRACT(year  FROM date_key)::INT  AS yr,
    EXTRACT(month FROM date_key)::INT  AS mon,
    EXTRACT(day   FROM date_key)::INT  AS dom,
    EXTRACT(dow   FROM date_key)::INT  AS dow,          -- 0=周日
    strftime(date_key, '%Y-%m')        AS year_month,
    CASE WHEN EXTRACT(dow FROM date_key) IN (0, 6) THEN 1 ELSE 0 END AS is_weekend
FROM fact_trips;

CREATE OR REPLACE TABLE dim_zone AS
SELECT
    CAST("LocationID" AS INT) AS zone_id,
    "Borough"                 AS borough,
    "Zone"                    AS zone_name,
    "service_zone"            AS service_zone
FROM read_csv('data/taxi_zone_lookup.csv', header=true);

CREATE OR REPLACE TABLE dim_payment AS
SELECT * FROM (VALUES
    (0, 'Flex Fare'),
    (1, '信用卡'),
    (2, '现金'),
    (3, '免费'),
    (4, '争议'),
    (5, '未知'),
    (6, '作废')
) AS t(payment_id, payment_name);

CREATE OR REPLACE TABLE dim_rate AS
SELECT * FROM (VALUES
    (1,  '标准费率'),
    (2,  'JFK 机场'),
    (3,  'Newark 机场'),
    (4,  'Nassau/Westchester'),
    (5,  '协商价'),
    (6,  '团体价'),
    (99, '缺失')
) AS t(rate_id, rate_name);

-- ------------------------------------------------------------
-- 3. 汇总视图：给 BI 和后续分析直接用
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_daily AS
SELECT
    f.date_key,
    d.dow, d.is_weekend,
    COUNT(*)                          AS trips,
    ROUND(SUM(f.total_amount), 2)     AS revenue,
    ROUND(AVG(f.total_amount), 2)     AS aov,
    ROUND(AVG(f.distance_mi), 3)      AS avg_miles,
    ROUND(AVG(f.duration_min), 2)     AS avg_minutes,
    ROUND(AVG(f.speed_mph), 2)        AS avg_speed_mph,
    ROUND(100.0 * AVG(f.is_airport), 2) AS airport_share_pct,
    ROUND(AVG(f.tip_amount), 2)       AS avg_tip
FROM fact_trips f
JOIN dim_date d USING (date_key)
GROUP BY 1, 2, 3;

CREATE OR REPLACE VIEW v_hourly AS
SELECT
    f.hour_of_day,
    COUNT(*)                          AS trips,
    ROUND(AVG(f.total_amount), 2)     AS aov,
    ROUND(AVG(f.speed_mph), 2)        AS avg_speed_mph,
    ROUND(AVG(f.duration_min), 2)     AS avg_minutes
FROM fact_trips f
GROUP BY 1;

-- 数据自检：清洗前后各丢了什么
-- 说明：五条规则按顺序执行，dropped_* 是「前面规则都通过后」的增量，
--       因此五列可以相加，合计 = raw_rows − clean_rows；drop_overlap 应为 0。
--       早期版本混用了「单规则独立命中」和「顺序增量」，相加会多出 1.3 万行。
CREATE OR REPLACE TABLE qc_report AS
WITH s1 AS (
    SELECT * FROM raw WHERE total_amount <= 0
), s2 AS (
    SELECT * FROM raw WHERE total_amount > 0 AND trip_distance <= 0
), s3 AS (
    SELECT * FROM raw
    WHERE total_amount > 0 AND trip_distance > 0
      AND tpep_dropoff_datetime <= tpep_pickup_datetime
), s4 AS (
    SELECT * FROM raw
    WHERE total_amount > 0 AND trip_distance > 0
      AND tpep_dropoff_datetime > tpep_pickup_datetime
      AND (tpep_pickup_datetime < TIMESTAMP '2024-01-01'
        OR tpep_pickup_datetime >= TIMESTAMP '2024-04-01')
), s5 AS (
    SELECT * FROM raw
    WHERE total_amount > 0 AND trip_distance > 0
      AND tpep_dropoff_datetime > tpep_pickup_datetime
      AND tpep_pickup_datetime >= TIMESTAMP '2024-01-01'
      AND tpep_pickup_datetime <  TIMESTAMP '2024-04-01'
      AND (date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime) < 1
        OR date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime) > 1440)
), c AS (
    SELECT
        (SELECT COUNT(*) FROM raw)          AS raw_rows,
        (SELECT COUNT(*) FROM fact_trips)   AS clean_rows,
        (SELECT COUNT(*) FROM s1)           AS d1,
        (SELECT COUNT(*) FROM s2)           AS d2,
        (SELECT COUNT(*) FROM s3)           AS d3,
        (SELECT COUNT(*) FROM s4)           AS d4,
        (SELECT COUNT(*) FROM s5)           AS d5
)
SELECT
    raw_rows,
    clean_rows,
    d1 AS dropped_nonpos_amount,
    d2 AS dropped_zero_distance,
    d3 AS dropped_time_inverted,
    d4 AS dropped_out_of_range,
    d5 AS dropped_duration,
    raw_rows - clean_rows AS dropped_total,
    (d1 + d2 + d3 + d4 + d5) - (raw_rows - clean_rows) AS drop_overlap,
    (SELECT COUNT(*) FROM fact_trips WHERE speed_mph IS NULL) AS speed_null,
    (SELECT COUNT(*) FROM fact_trips WHERE distance_mi = 100) AS distance_capped
FROM c;
