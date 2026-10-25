-- ============================================================
-- 清洗口径：单一事实来源
-- run.py 最先执行本文件，建出 clean_trips 视图；
-- 02-04 / 06 / 07 号分析脚本一律从 clean_trips 读数。
-- 01_build.sql（SQL 对账层）和 01_etl.py（Power BI 导出层）需要原始
-- 时间戳等 clean_trips 未携带的列，各自内联同一套口径：
--   行过滤 + 派生列定义（speed 上限置空、distance 上限截断、is_airport 宽口径）
--   与本文件逐字一致——改本文件必须同步这两处，否则跨层对不上账。
-- ============================================================
-- 派生列口径（与 01_build.sql / 01_etl.py 一致，勿单方面改动）：
--   speed_mph  仅当 duration >= 1 且 <= 60 mph 时计算，否则置 NULL（置空不删行）
--   distance_mi 里程 >100 mi 截断为 100（不删行）：原始数据有 17 万英里的极端异常值，
--               直接求均值会把「平均里程」和「单位里程收入」整体带偏；速度仍用原始距离计算
--   is_airport RatecodeID IN (2,3)（JFK/Newark 标准费率）OR airport_fee > 0
--   distance_band <2 / 2-5 / 5-10 / 10+ 英里
-- 历史教训：
--   1) 01_etl.py 曾只写 duration_min <= 1440 漏了 >= 1，2-13 比其他脚本多 110 单；
--   2) 三个分析脚本曾各自内联这组 WHERE，口径漂移靠人肉同步；
--   3) clean_trips 曾把 speed 的 60 mph 上限和 is_airport 的 airport_fee 条件
--      静默删掉，导致分析层与 SQL/BI 层的机场单、基准速度对不上。
-- 本 view 就是为终结漂移而建：改口径只改这里（+同步两处内联），全链路生效。
-- ============================================================

CREATE OR REPLACE VIEW clean_trips AS
WITH raw_all AS (
    SELECT * FROM read_parquet(['data/yellow_2023-01.parquet','data/yellow_2023-02.parquet','data/yellow_2023-03.parquet',
                       'data/yellow_2024-01.parquet','data/yellow_2024-02.parquet','data/yellow_2024-03.parquet'],
                      union_by_name=true)
),
base AS (
    SELECT
        CAST(tpep_pickup_datetime AS DATE) AS date_key,
        EXTRACT(hour FROM tpep_pickup_datetime)  AS hour_of_day,
        EXTRACT(month FROM CAST(tpep_pickup_datetime AS DATE)) AS mon,
        EXTRACT(dow FROM CAST(tpep_pickup_datetime AS DATE))   AS dow,
        RatecodeID   AS rate_id,
        PULocationID AS pu_zone_id,
        payment_type AS payment_id,
        trip_distance AS raw_distance_mi,
        LEAST(trip_distance, 100.0) AS distance_mi,
        date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime) AS duration_min,
        fare_amount, tip_amount, tolls_amount,
        total_amount,
        airport_fee,
        tpep_pickup_datetime
    FROM raw_all
    WHERE year(tpep_pickup_datetime) IN (2023, 2024)
      AND month(CAST(tpep_pickup_datetime AS DATE)) BETWEEN 1 AND 3
      AND total_amount   > 0
      AND trip_distance  > 0
      AND tpep_dropoff_datetime > tpep_pickup_datetime
      AND date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime) BETWEEN 1 AND 1440
)
SELECT
    date_key, hour_of_day, mon, dow, rate_id, pu_zone_id, payment_id,
    distance_mi, raw_distance_mi, duration_min,
    CASE WHEN duration_min >= 1
          AND raw_distance_mi / (duration_min / 60.0) <= 60
         THEN ROUND(raw_distance_mi / (duration_min / 60.0), 2) END AS speed_mph,
    fare_amount, tip_amount, tolls_amount, total_amount,
    CASE WHEN rate_id IN (2, 3) OR airport_fee > 0 THEN 1 ELSE 0 END AS is_airport,
    CASE WHEN distance_mi <  2 THEN '1_short'
         WHEN distance_mi <  5 THEN '2_mid'
         WHEN distance_mi < 10 THEN '3_long'
         ELSE '4_very_long' END    AS distance_band
FROM base;
