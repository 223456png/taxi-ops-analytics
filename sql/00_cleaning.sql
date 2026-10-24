-- ============================================================
-- 清洗口径：单一事实来源
-- run.py 最先执行本文件，建出 clean_trips 视图；
-- 01_build.sql / 01_etl.py / 02-04 号脚本一律从 clean_trips 读数。
-- ============================================================
-- 历史教训：
--   1) 01_etl.py 曾只写 duration_min <= 1440 漏了 >= 1，2-13 比其他脚本多 110 单；
--   2) 三个分析脚本曾各自内联这组 WHERE，口径漂移靠人肉同步。
-- 本 view 就是为终结漂移而建：改口径只改这里，所有脚本立即生效。
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
        trip_distance AS distance_mi,
        date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime) AS duration_min,
        fare_amount, tip_amount, tolls_amount,
        total_amount,
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
    distance_mi, duration_min,
    ROUND(distance_mi / (duration_min / 60.0), 2) AS speed_mph,
    fare_amount, tip_amount, tolls_amount, total_amount,
    CASE WHEN rate_id IN (2, 3) THEN 1 ELSE 0 END AS is_airport,   -- 2=JFK 3=Newark 标准费率码
    CASE WHEN distance_mi <  2 THEN '1_short'
         WHEN distance_mi <  5 THEN '2_mid'
         WHEN distance_mi < 10 THEN '3_long'
         ELSE '4_very_long' END    AS distance_band
FROM base;
