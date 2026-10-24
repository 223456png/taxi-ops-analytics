"""
策略测算
========
用「超额跌幅」拆出运营可优化的部分，再算策略收益。

逻辑：
  整体跌幅 = 外部冲击（不可抗）
  某维度跌幅 − 整体跌幅 = 超额部分（运营能动）

运行：python src/03_strategy.py
输出：report/strategy_report.md
"""
import duckdb
import pandas as pd

pd.set_option("display.width", 200)
FACT = 'clean_trips'   # 单一事实来源：视图由 sql/00_cleaning.sql 定义
D = "DATE '2024-02-13'"
AOV = 25.33         # 暴雪日客单价（实测 2-13 当天，与 02_anomaly 输出一致）
OUT = "report/strategy_report.md"
md = ["# 策略测算", ""]


def df_to_md(df):
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in df.iterrows():
        out.append("| " + " | ".join(
            "" if pd.isna(v) else (f"{v:,.2f}" if isinstance(v, float) else str(v))
            for v in row) + " |")
    return "\n".join(out)


def sec(title, df, note=None):
    print(f"\n{'='*64}\n{title}\n{'='*64}")
    print(df.to_string(index=False))
    if note:
        print(f"  {note}")
    md.extend([f"## {title}\n", df_to_md(df), ""] + ([f"> {note}\n"] if note else []))


def main():
    global md
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")
    con.execute(open('sql/00_cleaning.sql', encoding='utf-8').read())
    con.execute("CREATE VIEW f AS SELECT * FROM clean_trips WHERE year(date_key) = 2024")

    # 整体跌幅
    # 基线 = 全部同星期几（含异动日本身）的中位数。
    # 中位数对单个极端值不敏感，所以异动日不会明显拉偏基线；
    # 这样定义和 02_anomaly.py 的 pandas median 完全一致。
    tot = con.execute(f"""
        WITH d AS (
          SELECT date_key, COUNT(*) trips FROM f
          WHERE dayofweek(date_key)=2 GROUP BY 1
        )
        SELECT MEDIAN(trips) base,
               MAX(CASE WHEN date_key={D} THEN trips END) storm
        FROM d
    """).df().iloc[0]
    overall = tot.storm / tot.base - 1
    gap_total = tot.storm - tot.base
    print(f"整体：基准日均 {tot.base:,.0f} → 暴雪日 {tot.storm:,.0f}，跌幅 {overall:.2%}，缺口 {gap_total:,.0f} 单")
    md += [f"整体：基准日均 {tot.base:,.0f} 单 → 暴雪日 {tot.storm:,.0f} 单，"
           f"跌幅 **{overall:.2%}**，缺口 **{gap_total:,.0f} 单**\n"]

    # 区域超额缺口
    z = con.execute(f"""
        WITH t AS (
          SELECT f.date_key, COALESCE(dz.borough,'未知') b, COUNT(*) trips
          FROM f LEFT JOIN read_csv('data/taxi_zone_lookup.csv', header=true) dz
               ON f.pu_zone_id = CAST(dz."LocationID" AS INT)
          WHERE dayofweek(f.date_key)=2 GROUP BY 1,2
        )
        SELECT b 行政区,
          MEDIAN(trips) 基准日均,
          SUM(CASE WHEN date_key={D} THEN trips END) 暴雪日
        FROM t GROUP BY 1 ORDER BY 1
    """).df()
    z["实际偏离"] = z.暴雪日 / z.基准日均 - 1
    z["按整体应有"] = z.基准日均 * (1 + overall)
    z["超额缺口"] = (z.暴雪日 - z.按整体应有).round(0).fillna(0).astype(int)
    z = z.sort_values("超额缺口")
    sec("1. 区域超额缺口", z.round(3).rename(columns={"基准日均": "基准", "暴雪日": "实际"})
        .assign(实际偏离=lambda d: (d.实际偏离*100).round(1),
                超额缺口=lambda d: d.超额缺口),
        f"Brooklyn 跌幅 {z.实际偏离.min()*100:.1f}% 远超整体 {overall*100:.1f}%，这部分差额才是运营能动的")

    # 时段超额缺口
    h = con.execute(f"""
        WITH t AS (
          SELECT date_key, hour_of_day hr, COUNT(*) trips FROM f
          WHERE dayofweek(date_key)=2 GROUP BY 1,2
        )
        SELECT hr 小时, MEDIAN(trips) 基准日均,
          SUM(CASE WHEN date_key={D} THEN trips END) 暴雪日
        FROM t GROUP BY 1 ORDER BY 1
    """).df()
    h["实际偏离"] = h.暴雪日 / h.基准日均 - 1
    h["超额缺口"] = (h.暴雪日 - h.基准日均 * (1 + overall)).round(0)
    peak = h[h.小时.isin([7, 8, 9, 10])]
    sec("2. 早高峰（7–10 点）超额缺口",
        peak.round(3).assign(实际偏离=lambda d: (d.实际偏离*100).round(1)),
        f"四个小时合计超额缺口 {peak.超额缺口.sum():,.0f} 单")

    # 距离分层
    b = con.execute(f"""
        WITH t AS (
          SELECT date_key, distance_band, COUNT(*) trips FROM f
          WHERE dayofweek(date_key)=2 GROUP BY 1,2
        )
        SELECT distance_band 距离分层, MEDIAN(trips) 基准日均,
          SUM(CASE WHEN date_key={D} THEN trips END) 暴雪日
        FROM t GROUP BY 1 ORDER BY 1
    """).df()
    b["实际偏离"] = b.暴雪日 / b.基准日均 - 1
    b["超额缺口"] = (b.暴雪日 - b.基准日均 * (1 + overall)).round(0)
    sec("3. 距离分层超额缺口", b.round(3).assign(实际偏离=lambda d: (d.实际偏离*100).round(1)),
        "短途的超额缺口为正 → 短途比预期抗跌，运力往短途倾斜有依据")

    # 策略汇总
    brook = int(z[z.行政区 == "Brooklyn"].超额缺口.iloc[0])
    peak_gap = int(peak.超额缺口.sum())
    total_opt = brook + peak_gap
    ratio = total_opt / abs(gap_total)

    # 只按 2-13 这一次算。1-02/1-03/1-07 是节假日效应，不是外生冲击，
    # 把它们和暴雪一起外推是不成立的（README 里也承认只有 2-13 是外生冲击）。
    # 所以这里不再做「Q1 四次事件」的乘法。

    sec("4. 策略汇总", pd.DataFrame([
        {"项目": "当天总缺口", "数值": f"{gap_total:,.0f} 单", "说明": "外部冲击，不可抗"},
        {"项目": "Brooklyn 超额缺口", "数值": f"{brook:,} 单", "说明": "策略二：区域差异化调度"},
        {"项目": "早高峰超额缺口", "数值": f"{peak_gap:,} 单", "说明": "策略一：时段运力调整"},
        {"项目": "可优化合计（上限）", "数值": f"{total_opt:,} 单",
         "说明": f"占当天缺口 {ratio:.1%}；区域与时段可能重叠，未去重，是上限不是准确值"},
        {"项目": "折合营收（上限）", "数值": f"${total_opt*AOV:,.0f}",
         "说明": f"客单价 ${AOV}；仅 2-13 单次，不外推"},
        {"项目": "折合营收（保守 50%）", "数值": f"${total_opt*AOV*0.5:,.0f}", "说明": "考虑执行损耗"},
    ]))

    open(OUT, "w", encoding="utf-8").write("\n".join(md))
    print(f"\n写入 {OUT}")


if __name__ == "__main__":
    main()
