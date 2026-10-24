"""
异动检测与归因
==============
1. 用「同星期基线」识别异动（避免周末效应污染）
2. 对最大异动日做四维拆解：时段 / 距离 / 客群 / 区域
3. 按速度变化分组：可排除拥堵，但无法区分需求收缩与司机主动不出车
   （两者观测等价；口径说明见 sql/cleaning.sql）

运行：python src/02_anomaly.py
输出：report/anomaly_report.md
"""
import duckdb
import pandas as pd

pd.set_option("display.width", 200)
FACT = 'clean_trips'   # 单一事实来源：视图由 sql/00_cleaning.sql 定义
OUT = "report/anomaly_report.md"
md = ["# 异动检测与归因结果", ""]


def df_to_md(df):
    """零依赖 markdown 表格（不依赖 tabulate）"""
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in df.iterrows():
        cells = []
        for v in row:
            if pd.isna(v):
                cells.append("")
            elif isinstance(v, float):
                cells.append(f"{v:,.2f}" if abs(v) < 1e5 else f"{v:,.0f}")
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def sec(title, df=None, note=None):
    print(f"\n{'='*66}\n{title}\n{'='*66}")
    md.append(f"## {title}\n")
    if df is not None:
        print(df.to_string(index=False))
        md.append(df_to_md(df))
        md.append("")
    if note:
        print(f"  {note}")
        md.append(f"> {note}\n")


def main():
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")
    con.execute(open('sql/00_cleaning.sql', encoding='utf-8').read())
    con.execute(f"CREATE VIEW f AS SELECT * FROM {FACT} WHERE year(date_key) = 2024")

    # ---------- 1. 日度 + 同星期基线 ----------
    daily = con.execute("""
        SELECT date_key, dayofweek(date_key) dow,
               COUNT(*) trips, SUM(total_amount) revenue,
               AVG(total_amount) aov, AVG(distance_mi) mi,
               AVG(duration_min) mins, AVG(speed_mph) spd
        FROM f GROUP BY 1,2 ORDER BY 1
    """).df()
    base = daily.groupby("dow")["trips"].median().rename("base_trips")
    daily = daily.merge(base, on="dow")
    daily["dev"] = (daily.trips / daily.base_trips - 1) * 100
    daily["gap"] = daily.trips - daily.base_trips
    # 营收缺口用「基准营收 − 实际营收」：暴雪日客单价本身也降了（27.55 → 25.35），
    # 用「缺口单量 × 当日客单价」会低估缺口
    daily["base_rev"] = daily.groupby("dow")["revenue"].transform("median")
    daily["rev_gap"] = daily.revenue - daily.base_rev

    daily["date_key"] = pd.to_datetime(daily.date_key).dt.strftime("%Y-%m-%d")
    top = daily.reindex(daily.dev.abs().sort_values(ascending=False).index).head(8)
    sec("1. 偏离最大的 8 天", top[["date_key", "dow", "trips", "base_trips", "dev", "gap", "rev_gap"]]
        .round(2).rename(columns={"date_key": "日期", "dow": "星期", "trips": "行程量",
                                  "base_trips": "同星期基线", "dev": "偏离%",
                                  "gap": "缺口单量", "rev_gap": "缺口营收"}))

    # ---------- 2. 主异动日的拆解 ----------
    D = "DATE '2024-02-13'"

    hour = con.execute(f"""
        WITH h AS (
            SELECT date_key, hour_of_day hr, COUNT(*) trips, AVG(speed_mph) spd, AVG(distance_mi) mi
            FROM f WHERE dayofweek(date_key)=2 GROUP BY 1,2
        )
        SELECT hr 小时,
            MEDIAN(trips) 基准日均,
            SUM(CASE WHEN date_key={D} THEN trips END) 异动日,
            ROUND(100.0*(SUM(CASE WHEN date_key={D} THEN trips END)
                /NULLIF(MEDIAN(trips),0)-1),1) 量变化,
            MEDIAN(CASE WHEN date_key<>{D} THEN spd END) 基准速度,
            ROUND(AVG(CASE WHEN date_key={D} THEN spd END),2) 异动日速度
        FROM h GROUP BY 1 ORDER BY 1
    """).df()
    sec("2. 分时段拆解", hour)

    band = con.execute(f"""
        WITH b AS (
            SELECT date_key, distance_band, COUNT(*) trips, AVG(duration_min) m
            FROM f WHERE dayofweek(date_key)=2 GROUP BY 1,2
        )
        SELECT distance_band 距离分层,
            MEDIAN(trips) 基准日均,
            SUM(CASE WHEN date_key={D} THEN trips END) 异动日,
            ROUND(100.0*(SUM(CASE WHEN date_key={D} THEN trips END)
                /NULLIF(MEDIAN(trips),0)-1),1) 偏离,
            ROUND(AVG(m),1) 单均时长
        FROM b GROUP BY 1 ORDER BY 1
    """).df()
    sec("3. 距离分层拆解", band)

    seg = con.execute(f"""
        WITH s AS (
            SELECT date_key, is_airport, COUNT(*) trips, AVG(total_amount) amt
            FROM f WHERE dayofweek(date_key)=2 GROUP BY 1,2
        )
        SELECT CASE is_airport WHEN 1 THEN '机场单' ELSE '市区单' END 客群,
            MEDIAN(trips) 基准日均,
            SUM(CASE WHEN date_key={D} THEN trips END) 异动日,
            ROUND(100.0*(SUM(CASE WHEN date_key={D} THEN trips END)
                /NULLIF(MEDIAN(trips),0)-1),1) 偏离,
            ROUND(AVG(amt),2) 客单价
        FROM s GROUP BY 1 ORDER BY 1
    """).df()
    sec("4. 机场 vs 市区", seg,
        "两者跌幅接近 → 不是机场需求单独消失，而是整体需求等比例收缩")

    # ---------- 3. 需求侧 vs 供给侧 ----------
    judge = con.execute(f"""
        WITH h AS (
            SELECT date_key, hour_of_day hr, COUNT(*) trips, AVG(speed_mph) spd
            FROM f WHERE dayofweek(date_key)=2 GROUP BY 1,2
        ), cmp AS (
            -- 基准速度用 MEDIAN（和第 2 节分时段表同一口径；
            -- 早期版本这里用 AVG，导致分组边界和 A/B 组数量对不上）
            SELECT hr,
                SUM(CASE WHEN date_key={D} THEN trips END) s_trips,
                MEDIAN(trips) b_trips,
                MAX(CASE WHEN date_key={D} THEN spd END) s_spd,
                MEDIAN(CASE WHEN date_key<>{D} THEN spd END) b_spd
            FROM h GROUP BY 1
        )
        SELECT
            CASE WHEN (s_spd/b_spd-1) < -0.08 THEN 'A. 速度明显下降 (>8%)'
                 WHEN (s_spd/b_spd-1) >  0.00 THEN 'C. 速度反而上升'
                 ELSE 'B. 速度基本持平 (-8%~0)' END AS 速度分组,
            COUNT(*) AS 时段数,
            ROUND(AVG((s_trips/b_trips-1)*100),1) AS 平均量变化_pct,
            ROUND(SUM(s_trips-b_trips)/SUM(b_trips)*100,1) AS 量加权变化_pct,
            ROUND(AVG((s_spd/b_spd-1)*100),1)     AS 平均速度变化_pct
        FROM cmp WHERE s_trips IS NOT NULL AND b_trips > 0
              AND s_spd IS NOT NULL AND b_spd IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).df()
    sec("5. 分速度组对比", judge,
        "跌幅最大的时段集中在路况变好的时段 → 可排除「拥堵导致接不了单」，"
        "但无法区分需求收缩与司机主动不出车（两者观测等价，且数据无供给端指标）")
    open(OUT, "w", encoding="utf-8").write("\n".join(md))
    print(f"\n结果写入 {OUT}")


if __name__ == "__main__":
    main()
