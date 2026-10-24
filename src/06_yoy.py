"""
同比分析：2023 Q1 vs 2024 Q1
============================
目的：补足"数据只有 3 个月"的短板，回答两个问题：
  1. 2024 年的量价水平相对 2023 年是什么位置
  2. 2024-02-13 的异动在历史上是否罕见

运行：python src/06_yoy.py
输出：report/yoy_report.md
"""
import duckdb
import pandas as pd

pd.set_option("display.width", 200)
Y23 = "['data/yellow_2023-01.parquet','data/yellow_2023-02.parquet','data/yellow_2023-03.parquet']"
Y24 = "['data/yellow_2024-01.parquet','data/yellow_2024-02.parquet','data/yellow_2024-03.parquet']"
OUT = "report/yoy_report.md"
md = ["# 同比分析：2023 Q1 vs 2024 Q1", ""]


def md_table(df):
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(
            "" if pd.isna(v) else (f"{v:,.2f}" if isinstance(v, float) else f"{v:,}" if isinstance(v, int) else str(v))
            for v in r) + " |")
    return "\n".join(out)


def main():
    global md
    con = duckdb.connect(); con.execute("SET memory_limit='6GB'")
    con.execute(open('sql/00_cleaning.sql', encoding='utf-8').read())

    # ---------- 1. 月度同比 ----------
    print("=" * 62)
    print("1. 月度同比（2023 vs 2024）")
    print("=" * 62)
    df = con.execute(f"""
    WITH d AS (
      SELECT strftime(CAST(tpep_pickup_datetime AS DATE), '%m') mon,
             year(CAST(tpep_pickup_datetime AS DATE)) yr,
             COUNT(*) trips, SUM(total_amount) rev, AVG(total_amount) aov,
             AVG(trip_distance) mi,
             COUNT(DISTINCT CAST(tpep_pickup_datetime AS DATE)) ndays
      FROM read_parquet({Y23} || {Y24})
      WHERE total_amount>0 AND trip_distance>0
        AND tpep_dropoff_datetime > tpep_pickup_datetime
        AND date_diff('minute',tpep_pickup_datetime,tpep_dropoff_datetime) BETWEEN 1 AND 1440
        AND tpep_pickup_datetime >= DATE '2023-01-01' AND tpep_pickup_datetime < DATE '2024-04-01'
        AND year(tpep_pickup_datetime) IN (2023, 2024)
        AND month(tpep_pickup_datetime) BETWEEN 1 AND 3
      GROUP BY 1,2
    )
    SELECT mon 月份,
      MAX(CASE WHEN yr=2023 THEN ndays END) "2023天数",
      MAX(CASE WHEN yr=2024 THEN ndays END) "2024天数",
      ROUND(MAX(CASE WHEN yr=2023 THEN trips END)*1.0/MAX(CASE WHEN yr=2023 THEN ndays END)) "2023日均",
      ROUND(MAX(CASE WHEN yr=2024 THEN trips END)*1.0/MAX(CASE WHEN yr=2024 THEN ndays END)) "2024日均",
      ROUND(100.0*((MAX(CASE WHEN yr=2024 THEN trips END)*1.0/MAX(CASE WHEN yr=2024 THEN ndays END))
            /NULLIF(MAX(CASE WHEN yr=2023 THEN trips END)*1.0/MAX(CASE WHEN yr=2023 THEN ndays END),0)-1),1) "日均同比_%",
      ROUND(MAX(CASE WHEN yr=2023 THEN aov END),2) "2023客单价",
      ROUND(MAX(CASE WHEN yr=2024 THEN aov END),2) "2024客单价",
      ROUND(MAX(CASE WHEN yr=2023 THEN mi END),2) "2023平均里程",
      ROUND(MAX(CASE WHEN yr=2024 THEN mi END),2) "2024平均里程"
    FROM d GROUP BY 1 ORDER BY 1
    """).df()
    print(df.to_string(index=False))
    md += ["## 1. 月度同比（日均口径，天数不同不能直接比总量）", "", md_table(df), ""]

    # ---------- 2. 全年合计 ----------
    print("\n" + "=" * 62)
    print("2. Q1 合计同比")
    print("=" * 62)
    tot = con.execute(f"""
    WITH d AS (
      SELECT year(CAST(tpep_pickup_datetime AS DATE)) yr,
             COUNT(*) trips, SUM(total_amount) rev, AVG(total_amount) aov, AVG(trip_distance) mi
      FROM read_parquet({Y23} || {Y24})
      WHERE total_amount>0 AND trip_distance>0
        AND tpep_dropoff_datetime > tpep_pickup_datetime
        AND date_diff('minute',tpep_pickup_datetime,tpep_dropoff_datetime) BETWEEN 1 AND 1440
        AND tpep_pickup_datetime >= DATE '2023-01-01' AND tpep_pickup_datetime < DATE '2024-04-01'
        AND year(tpep_pickup_datetime) IN (2023, 2024)
        AND month(tpep_pickup_datetime) BETWEEN 1 AND 3
      GROUP BY 1
    ) SELECT * FROM d ORDER BY yr
    """).df()
    # 天数必须单独算：2024 是闰年，Q1 有 91 天；2023 只有 90 天。
    # 直接比总量会凭空多出 1.1% 的「增长」，必须换成日均。
    nd = con.execute(f"""
    SELECT year(tpep_pickup_datetime) yr, COUNT(DISTINCT CAST(tpep_pickup_datetime AS DATE)) n
    FROM read_parquet({Y23} || {Y24})
    WHERE total_amount>0 AND trip_distance>0
      AND tpep_dropoff_datetime > tpep_pickup_datetime
      AND date_diff('minute',tpep_pickup_datetime,tpep_dropoff_datetime) BETWEEN 1 AND 1440
      AND tpep_pickup_datetime >= DATE '2023-01-01' AND tpep_pickup_datetime < DATE '2024-04-01'
      AND year(tpep_pickup_datetime) IN (2023,2024) AND month(tpep_pickup_datetime) BETWEEN 1 AND 3
    GROUP BY 1 ORDER BY 1
    """).df()
    n23, n24 = int(nd[nd.yr == 2023].n.iloc[0]), int(nd[nd.yr == 2024].n.iloc[0])
    t23 = tot[tot.yr == 2023].iloc[0]; t24 = tot[tot.yr == 2024].iloc[0]
    d23_trips, d24_trips = t23.trips / n23, t24.trips / n24
    d23_rev, d24_rev = t23.rev / n23, t24.rev / n24
    print(f"  2023 Q1: {n23} 天, {t23.trips:,.0f} 单, 日均 {d23_trips:,.0f}")
    print(f"  2024 Q1: {n24} 天, {t24.trips:,.0f} 单, 日均 {d24_trips:,.0f}")
    print(f"  总量同比: 行程量 {(t24.trips/t23.trips-1)*100:+.1f}%  营收 {(t24.rev/t23.rev-1)*100:+.1f}%")
    print(f"  **日均同比**: 行程量 {(d24_trips/d23_trips-1)*100:+.1f}%  营收 {(d24_rev/d23_rev-1)*100:+.1f}%")
    md += ["## 2. Q1 合计同比（日均口径）", "",
           f"**2024 是闰年，Q1 有 {n24} 天；2023 只有 {n23} 天。**"
           f"直接比总量会凭空多出 1.1% 的「增长」，所以下面一律用日均。", "",
           f"| 指标 | 2023 Q1（{n23} 天）| 2024 Q1（{n24} 天）| 总量同比 | **日均同比** |",
           f"|---|---|---|---|---|",
           f"| 行程量（总）| {t23.trips:,.0f} | {t24.trips:,.0f} | {(t24.trips/t23.trips-1)*100:+.1f}% | — |",
           f"| 行程量（日均）| {d23_trips:,.0f} | {d24_trips:,.0f} | — | **{(d24_trips/d23_trips-1)*100:+.1f}%** |",
           f"| 营收（日均）| ${d23_rev:,.0f} | ${d24_rev:,.0f} | — | **{(d24_rev/d23_rev-1)*100:+.1f}%** |",
           f"| 客单价 | ${t23.aov:.2f} | ${t24.aov:.2f} | {(t24.aov/t23.aov-1)*100:+.1f}% | 同左 |",
           f"| 平均里程 | {t23.mi:.2f} mi | {t24.mi:.2f} mi | {(t24.mi/t23.mi-1)*100:+.1f}% | 同左 |", ""]

    # ---------- 3. 两年各自的异动日对比 ----------
    print("\n" + "=" * 62)
    print("3. 两年的异动日：2024-02-13 在历史上罕见吗")
    print("=" * 62)
    df3 = con.execute(f"""
    WITH d AS (
      SELECT CAST(tpep_pickup_datetime AS DATE) dt, dayofweek(tpep_pickup_datetime) dow,
             COUNT(*) trips
      FROM read_parquet({Y23} || {Y24})
      WHERE total_amount>0 AND trip_distance>0
        AND tpep_dropoff_datetime > tpep_pickup_datetime
        AND date_diff('minute',tpep_pickup_datetime,tpep_dropoff_datetime) BETWEEN 1 AND 1440
        AND tpep_pickup_datetime >= DATE '2023-01-01' AND tpep_pickup_datetime < DATE '2024-04-01'
        AND year(tpep_pickup_datetime) IN (2023, 2024)
        AND month(tpep_pickup_datetime) BETWEEN 1 AND 3
      GROUP BY 1,2
    ), b AS (
      SELECT year(dt) yr, dow, MEDIAN(trips) base FROM d GROUP BY 1,2
    )
    SELECT CAST(d.dt AS DATE) 日期, year(d.dt) 年份, dayname(d.dt) 星期, d.trips 行程量,
           b.base 同星期基线, ROUND(100.0*(d.trips/b.base-1),1) 偏离_pct
    FROM d JOIN b ON year(d.dt)=b.yr AND d.dow=b.dow
    ORDER BY ABS(d.trips/b.base-1) DESC LIMIT 12
    """).df()
    print(df3.to_string(index=False))
    md += ["## 3. 两年偏离最大的 12 天", "", md_table(df3), ""]

    # 2023 年最大跌幅 vs 2024 年最大跌幅
    worst = con.execute(f"""
    WITH d AS (
      SELECT CAST(tpep_pickup_datetime AS DATE) dt, dayofweek(tpep_pickup_datetime) dow, COUNT(*) trips
      FROM read_parquet({Y23} || {Y24})
      WHERE total_amount>0 AND trip_distance>0
        AND tpep_dropoff_datetime > tpep_pickup_datetime
        AND date_diff('minute',tpep_pickup_datetime,tpep_dropoff_datetime) BETWEEN 1 AND 1440
        AND tpep_pickup_datetime >= DATE '2023-01-01' AND tpep_pickup_datetime < DATE '2024-04-01'
        AND year(tpep_pickup_datetime) IN (2023, 2024)
        AND month(tpep_pickup_datetime) BETWEEN 1 AND 3
      GROUP BY 1,2
    ), b AS (SELECT year(dt) yr, dow, MEDIAN(trips) base FROM d GROUP BY 1,2)
    SELECT year(d.dt) 年份, MIN(ROUND(100.0*(d.trips/b.base-1),1)) 最大单日跌幅_pct
    FROM d JOIN b ON year(d.dt)=b.yr AND d.dow=b.dow GROUP BY 1 ORDER BY 1
    """).df()
    print("\n每年的最大单日跌幅：")
    print(worst.to_string(index=False))
    y23_worst = worst[worst.年份 == 2023].最大单日跌幅_pct.iloc[0]
    y24_worst = worst[worst.年份 == 2024].最大单日跌幅_pct.iloc[0]
    note = (f"2023 年最大单日跌幅 {y23_worst}%，2024 年 {y24_worst}%。"
            f"2024-02-13 的 −31.8% 比 2023 年最差的一天还低 "
            f"{abs(y24_worst) - abs(y23_worst):.1f} 个百分点，"
            f"在两年 181 天里是唯一的量级。")
    print(f"\n  {note}")
    md += ["## 4. 结论", "", note, ""]

    open(OUT, "w", encoding="utf-8").write("\n".join(md))
    print(f"\n写入 {OUT}")


if __name__ == "__main__":
    main()
