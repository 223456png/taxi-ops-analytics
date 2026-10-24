"""
ETL：清洗原始数据，导出 Power BI 数据源
=====================================
输入：data/yellow_2024-{01,02,03}.parquet, data/taxi_zone_lookup.csv
输出：powerbi/exports/*.csv（5 张表，星型模型）

运行：python src/01_etl.py
"""
import os
import time

import duckdb
import pandas as pd

OUT = "powerbi/exports"
os.makedirs(OUT, exist_ok=True)

RAW = """read_parquet(['data/yellow_2024-01.parquet',
                     'data/yellow_2024-02.parquet',
                     'data/yellow_2024-03.parquet'])"""


def main():
    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")

    print("=" * 60)
    print("1. 建事实表")
    print("=" * 60)
    con.execute(f"""
    CREATE OR REPLACE TABLE fact_trips AS
    WITH base AS (
        SELECT *,
            date_diff('minute', tpep_pickup_datetime, tpep_dropoff_datetime) AS duration_min,
            CAST(tpep_pickup_datetime AS DATE) AS date_key,
            EXTRACT(hour FROM tpep_pickup_datetime)::INT AS hour_of_day
        FROM {RAW}
        WHERE total_amount > 0
          AND trip_distance > 0
          AND tpep_dropoff_datetime > tpep_pickup_datetime
          AND tpep_pickup_datetime >= TIMESTAMP '2024-01-01'
          AND tpep_pickup_datetime <  TIMESTAMP '2024-04-01'
    )
    SELECT
        ROW_NUMBER() OVER ()  AS trip_id,
        date_key,
        hour_of_day,
        tpep_pickup_datetime  AS pickup_ts,
        tpep_dropoff_datetime AS dropoff_ts,
        PULocationID          AS pu_zone_id,
        DOLocationID          AS do_zone_id,
        payment_type          AS payment_id,
        COALESCE(RatecodeID, 99) AS rate_id,
        trip_distance         AS distance_mi,
        duration_min,
        CASE WHEN duration_min >= 1
                  AND trip_distance / (duration_min / 60.0) <= 60
             THEN trip_distance / (duration_min / 60.0) END AS speed_mph,
        passenger_count       AS pax,
        fare_amount, tip_amount, tolls_amount,
        congestion_surcharge, airport_fee,
        total_amount,
        CASE WHEN RatecodeID IN (2,3) OR airport_fee > 0 THEN 1 ELSE 0 END AS is_airport,
        CASE WHEN trip_distance < 2  THEN '1_short'
             WHEN trip_distance < 5  THEN '2_mid'
             WHEN trip_distance < 10 THEN '3_long'
             ELSE '4_very_long' END AS distance_band
    FROM base
    WHERE duration_min BETWEEN 1 AND 1440   -- 口径定义见 sql/cleaning.sql，改这里要同步 01_build.sql   -- 与 06_yoy/07_forecast 口径一致：排除 0 分钟与超 24 小时
    """)

    n_raw = con.execute(f"SELECT COUNT(*) FROM {RAW}").fetchone()[0]
    n_clean = con.execute("SELECT COUNT(*) FROM fact_trips").fetchone()[0]
    n_speed_null = con.execute("SELECT COUNT(*) FROM fact_trips WHERE speed_mph IS NULL").fetchone()[0]
    print(f"  原始 {n_raw:>10,} 行")
    print(f"  清洗 {n_clean:>10,} 行  保留 {n_clean/n_raw:.2%}")
    print(f"  速度置空 {n_speed_null:>8,} 行（时长<1分钟或推算>60mph）")

    print("\n" + "=" * 60)
    print("2. 建维表")
    print("=" * 60)
    con.execute("""
    CREATE OR REPLACE TABLE dim_date AS
    SELECT DISTINCT date_key,
        EXTRACT(year FROM date_key)::INT  AS yr,
        EXTRACT(month FROM date_key)::INT AS mon,
        EXTRACT(day FROM date_key)::INT   AS dom,
        EXTRACT(dow FROM date_key)::INT   AS dow,
        strftime(date_key, '%Y-%m')       AS year_month,
        CASE WHEN EXTRACT(dow FROM date_key) IN (0,6) THEN 1 ELSE 0 END AS is_weekend
    FROM fact_trips ORDER BY date_key
    """)
    con.execute("""
    CREATE OR REPLACE TABLE dim_zone AS
    SELECT CAST("LocationID" AS INT) AS zone_id, "Borough" AS borough,
           "Zone" AS zone_name, "service_zone" AS service_zone
    FROM read_csv('data/taxi_zone_lookup.csv', header=true)
    """)
    con.execute("""
    CREATE OR REPLACE TABLE dim_payment AS
    SELECT * FROM (VALUES (1,'信用卡'),(2,'现金'),(3,'免费'),(4,'争议'),(5,'未知'),(6,'作废'))
    AS t(payment_id, payment_name)
    """)
    con.execute("""
    CREATE OR REPLACE TABLE dim_rate AS
    SELECT * FROM (VALUES (1,'标准费率'),(2,'JFK 机场'),(3,'Newark 机场'),
        (4,'Nassau/Westchester'),(5,'协商价'),(6,'团体价'),(99,'缺失'))
    AS t(rate_id, rate_name)
    """)
    for t in ["dim_date", "dim_zone", "dim_payment", "dim_rate"]:
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<12} {n:>6,} 行")

    print("\n" + "=" * 60)
    print("3. 导出给 Power BI")
    print("=" * 60)
    # 事实表 923 万行，导 CSV 会到 1GB+ 且 Power BI 导入慢，用 parquet（约 150MB）
    con.execute(f"COPY fact_trips TO '{OUT}/fact_trips.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
    sz = os.path.getsize(f"{OUT}/fact_trips.parquet") / 1024 / 1024
    print(f"  {'fact_trips':<12} {n_clean:>10,} 行  {sz:>6.1f} MB  -> {OUT}/fact_trips.parquet (parquet)")

    # 维表都很小，CSV 即可，utf-8-sig 让 Excel / Power BI 不乱码
    for t in ["dim_date", "dim_zone", "dim_payment", "dim_rate"]:
        df = con.execute(f"SELECT * FROM {t}").df()
        path = f"{OUT}/{t}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  {t:<12} {len(df):>10,} 行  {os.path.getsize(path)/1024:>6.1f} KB -> {path} (csv)")

    print(f"\n完成，耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
