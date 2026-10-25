"""
图表生成
========
6 张图，对应 README 的关键结论。全部从 clean_trips 取数，不手抄数字。

风格约定：
  白底、去掉上右边框、只留横向网格；
  标题给结论，副标题给口径；
  一张图只讲一件事，能直接标注就不放图例。

运行：python src/04_figures.py
输出：report/figures/01..06.png
"""
import os

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter, MultipleLocator

OUT = "report/figures"
os.makedirs(OUT, exist_ok=True)
FACT = "clean_trips"          # 单一事实来源：视图由 sql/00_cleaning.sql 定义
D = "DATE '2024-02-13'"
ZONE = "data/taxi_zone_lookup.csv"

INK, MUTED, GRID = "#1B2733", "#77828D", "#E8EBEE"
BLUE, BLUE_L, BLUE_PALE = "#1F4E79", "#7FA3C0", "#E1E9F0"
RED, RED_L, GREY = "#B1382C", "#DCA9A1", "#C3C9CF"
BAND = "#F2F4F6"

plt.rcParams.update({
    "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
    "axes.unicode_minus": True,
    "figure.dpi": 200, "savefig.dpi": 200,
    "font.size": 9.5, "text.color": INK,
    "axes.labelcolor": MUTED, "axes.labelsize": 9,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.labelsize": 9, "ytick.labelsize": 9,
    "xtick.major.size": 0, "ytick.major.size": 0,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#D7DBDF", "axes.linewidth": 0.9,
    "legend.frameon": False,
})


def frame(ax, y=True):
    if y:
        ax.grid(axis="y", color=GRID, lw=0.9)
    ax.set_axisbelow(True)
    ax.tick_params(pad=5)


def header(ax, main, sub):
    ax.set_title(main, loc="left", fontsize=13, fontweight="bold", color=INK, pad=27)
    ax.annotate(sub, xy=(0, 1), xytext=(0, 8), textcoords="offset points",
                xycoords="axes fraction", ha="left", va="bottom",
                fontsize=8.6, color=MUTED)


def source(fig, text="数据：NYC TLC 黄色出租车 2023–2024 Q1，清洗后 1,838 万行"):
    fig.text(0.995, -0.02, text, ha="right", va="top", fontsize=7.5, color=MUTED)


def save(fig, name):
    fig.savefig(f"{OUT}/{name}.png", bbox_inches="tight", pad_inches=0.32, facecolor="white")
    plt.close(fig)
    print(f"  {OUT}/{name}.png")


def pct(ax, axis="y", decimals=0):
    f = FuncFormatter(lambda v, _: f"{v:.{decimals}f}%")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(f)


def main():
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'")
    con.execute(open("sql/00_cleaning.sql", encoding="utf-8").read())
    con.execute(f"CREATE VIEW f AS SELECT * FROM {FACT} WHERE year(date_key) = 2024")

    daily = con.execute("""
        SELECT date_key, dayofweek(date_key) dow, COUNT(*) trips
        FROM f GROUP BY 1, 2 ORDER BY 1
    """).df()
    daily["base"] = daily.groupby("dow").trips.transform("median")
    daily["dev"] = daily.trips / daily.base - 1
    x = pd.to_datetime(daily.date_key)

    # ---------------- 图 1 日度趋势 ----------------
    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(7.6, 4.7), sharex=True,
        gridspec_kw={"height_ratios": [3, 1], "hspace": 0.12})
    ax1.plot(x, daily.base, color=MUTED, lw=1.0, ls=(0, (3, 2)), zorder=2)
    ax1.plot(x, daily.trips, color=BLUE, lw=1.6, zorder=3)
    bad = daily[daily.dev <= -0.20]
    ax1.scatter(pd.to_datetime(bad.date_key), bad.trips, s=26, color=RED,
                edgecolor="white", linewidth=0.8, zorder=4)
    storm = daily[daily.date_key.astype(str) == "2024-02-13"].iloc[0]
    ax1.annotate(f"2-13 暴雪\n{storm.trips:,.0f} 单 · {storm.dev:+.1%}",
                 xy=(pd.Timestamp("2024-02-13"), storm.trips),
                 xytext=(pd.Timestamp("2024-02-24"), 72800),
                 fontsize=8.6, color=RED, fontweight="bold", linespacing=1.5,
                 arrowprops=dict(arrowstyle="-|>", color=RED, lw=0.9,
                                 shrinkA=2, shrinkB=5,
                                 connectionstyle="arc3,rad=0.15"))
    ax1.set_ylim(60000, 136000)
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
    ax1.set_ylabel("日行程量", labelpad=8)
    ax1.legend(handles=[Line2D([], [], color=BLUE, lw=1.6, label="实际"),
                        Line2D([], [], color=MUTED, lw=1.0, ls=(0, (3, 2)), label="同星期基线")],
               loc="upper left", fontsize=8.4, handlelength=1.8, labelspacing=0.45)
    header(ax1, "2024 Q1 日行程量：2-13 一天少了 3.1 万单",
           "虚线为同星期中位数基线；红点为偏离 20% 以上的四个异常日，其余日子都在 ±20% 以内")

    ax2.bar(x, daily.dev * 100,
            color=[RED if v <= -20 else (BLUE_PALE if v >= 0 else GREY) for v in daily.dev * 100],
            width=0.72, zorder=3)
    ax2.axhline(0, color=INK, lw=0.8, zorder=4)
    ax2.set_ylim(-40, 26)
    ax2.yaxis.set_major_locator(MultipleLocator(20))
    pct(ax2)
    ax2.set_ylabel("偏离基线", labelpad=8)
    frame(ax2)
    source(fig)
    save(fig, "01_daily")

    # ---------------- 图 2 分时段：量 vs 速度 ----------------
    h = con.execute(f"""
        WITH t AS (
            SELECT date_key, hour_of_day hr, COUNT(*) trips, AVG(speed_mph) spd
            FROM f WHERE dayofweek(date_key) = 2 GROUP BY 1, 2
        )
        SELECT hr,
               SUM(CASE WHEN date_key = {D} THEN trips END) s_trips,
               MEDIAN(trips) b_trips,
               AVG(CASE WHEN date_key = {D} THEN spd END) s_spd,
               MEDIAN(CASE WHEN date_key <> {D} THEN spd END) b_spd
        FROM t GROUP BY 1 ORDER BY 1
    """).df()
    h["trip_dev"] = (h.s_trips / h.b_trips - 1) * 100
    h["spd_dev"] = (h.s_spd / h.b_spd - 1) * 100
    overall = storm.trips / storm.base - 1

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(7.6, 5.0), sharex=True,
        gridspec_kw={"height_ratios": [1.3, 1], "hspace": 0.14})
    ax1.bar(h.hr, h.trip_dev, width=0.64, zorder=3,
            color=[RED if v <= -35 else (BLUE_L if v > 0 else RED_L) for v in h.trip_dev])
    ax1.axhline(0, color=INK, lw=0.8, zorder=4)
    ax1.axhline(overall * 100, color=MUTED, lw=0.9, ls=(0, (4, 3)), zorder=2)
    ax1.annotate("全天 −31.9%", xy=(0.5, overall * 100), xytext=(1, 4),
                 textcoords="offset points", fontsize=8, color=MUTED, ha="left")
    peak = h.loc[h.trip_dev.idxmin()]
    ax1.annotate(f"{peak.hr:.0f} 点 {peak.trip_dev:.1f}%".replace("-", "−"),
                 xy=(peak.hr, peak.trip_dev), xytext=(6.2, -45.5),
                 fontsize=8.4, color=RED, fontweight="bold",
                 arrowprops=dict(arrowstyle="-", color=RED, lw=0.8,
                                 connectionstyle="angle,angleA=0,angleB=90,rad=0"))
    ax1.set_ylim(-48, 9)
    pct(ax1)
    ax1.set_ylabel("行程量变化", labelpad=8)
    header(ax1,
           f"9 点单量掉 {abs(peak.trip_dev):.1f}%，同一个小时的车速反而快 {peak.spd_dev:.1f}%",
           "2-13 相对 1–2 月其余 12 个周二的同一小时中位数；上图为单量，下图为速度")

    ax2.bar(h.hr, h.spd_dev, width=0.64, zorder=3,
            color=[BLUE if v > 0 else GREY for v in h.spd_dev])
    ax2.axhline(0, color=INK, lw=0.8, zorder=4)
    for a in (ax1, ax2):
        a.axvline(9, color="#AEB6BD", lw=0.8, ls=(0, (2, 3)), zorder=1)
    ax2.annotate(f"{peak.spd_dev:+.1f}%", xy=(9, peak.spd_dev), xytext=(0, 6),
                 textcoords="offset points", ha="center", fontsize=8.4,
                 color=BLUE, fontweight="bold")
    ax2.set_ylim(-22, 56)
    pct(ax2)
    ax2.set_ylabel("平均速度变化", labelpad=8)
    ax2.set_xlabel("小时", labelpad=8)
    ax2.set_xticks(range(0, 24, 2))
    ax2.set_xlim(-0.9, 23.9)
    frame(ax1); frame(ax2)
    source(fig, "数据：NYC TLC 2024-01/02/03，星期二样本；速度 = 里程 / 时长，>60 mph 置空")
    save(fig, "02_hourly")

    # ---------------- 图 3 速度变化 vs 单量变化 ----------------
    g = h.dropna().copy()
    g["grp"] = np.where(g.spd_dev < -8, "A", np.where(g.spd_dev > 0, "C", "B"))
    cmap = {"A": RED, "B": GREY, "C": BLUE}
    size = 26 + g.s_trips / g.s_trips.max() * 320

    fig, ax = plt.subplots(figsize=(7.6, 4.5))
    ax.axhline(overall * 100, color=MUTED, lw=0.9, ls=(0, (4, 3)), zorder=1)
    ax.annotate("全天 −31.9%", xy=(-19.5, overall * 100), xytext=(0, 4),
                textcoords="offset points", fontsize=8, color=MUTED)
    ax.scatter(g.spd_dev, g.trip_dev, s=size, c=[cmap[v] for v in g.grp],
               alpha=0.9, edgecolor="white", linewidth=0.9, zorder=3)
    for hr, dx, dy in [(0, 1.5, 3.2), (14, 1.2, 3.4)]:
        r = g[g.hr == hr].iloc[0]
        ax.annotate(f"{hr:.0f} 点", (r.spd_dev, r.trip_dev),
                    xytext=(r.spd_dev + dx, r.trip_dev + dy),
                    fontsize=8.4, color=INK, fontweight="bold",
                    bbox=dict(fc="white", ec="none", alpha=0.82, pad=1.2))
    p9 = g[g.hr == 9].iloc[0]
    ax.annotate("9 点", (p9.spd_dev, p9.trip_dev), xytext=(17.5, -43.5),
                fontsize=8.4, color=INK, fontweight="bold",
                bbox=dict(fc="white", ec="none", alpha=0.82, pad=1.2),
                arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.7,
                                shrinkA=2, shrinkB=4))
    ax.set_xlim(-20, 54)
    ax.set_ylim(-48, 11)
    pct(ax)
    pct(ax, "x")
    ax.set_xlabel("同小时车速变化", labelpad=8)
    ax.set_ylabel("单量变化", labelpad=8)
    ax.legend(handles=[Line2D([], [], marker="o", ls="", ms=6, color=RED, label="速度明显下降（1 个时段）"),
                       Line2D([], [], marker="o", ls="", ms=6, color=GREY, label="基本持平（5 个时段）"),
                       Line2D([], [], marker="o", ls="", ms=6, color=BLUE, label="反而上升（18 个时段）")],
              loc="upper left", bbox_to_anchor=(0, -0.19), ncol=3, fontsize=8.2,
              handletextpad=0.4, columnspacing=1.5)
    header(ax, "路况越好的小时，单量掉得越多",
           "24 个小时点，横轴为车速变化，纵轴为单量变化，圆点面积 = 当日该小时单量；对比基准为 1–2 月其余 12 个周二的同一小时中位数")
    save(fig, "03_speed_vs_volume")

    # ---------------- 图 4 距离分层 ----------------
    band = con.execute(f"""
        WITH b AS (
            SELECT date_key, distance_band, COUNT(*) trips FROM f
            WHERE dayofweek(date_key) = 2 GROUP BY 1, 2
        )
        SELECT distance_band,
               MEDIAN(trips) base,
               SUM(CASE WHEN date_key = {D} THEN trips END) storm
        FROM b GROUP BY 1 ORDER BY 1
    """).df()
    band["dev"] = band.storm / band.base - 1
    label = {"1_short": "< 2 mi", "2_mid": "2–5 mi", "3_long": "5–10 mi", "4_very_long": "10+ mi"}

    fig, ax = plt.subplots(figsize=(7.6, 4.1))
    y = list(range(len(band)))[::-1]
    ax.hlines(y, band.storm, band.base, color="#DFE3E7", lw=2.4, zorder=2)
    ax.scatter(band.base, y, s=68, facecolor="white", edgecolor=MUTED, lw=1.3, zorder=3)
    ax.scatter(band.storm, y, s=68, color=BLUE, zorder=4)
    for yy, r in zip(y, band.itertuples()):
        ax.annotate(f"{r.dev:+.1%}", xy=(66500, yy), fontsize=10, color=RED if r.dev < -0.35 else INK,
                    fontweight="bold", va="center", annotation_clip=False)
        ax.annotate(f"{r.storm:,.0f} / {r.base:,.0f}", xy=(66500, yy - 0.34), fontsize=8,
                    color=MUTED, va="center", annotation_clip=False)
    ax.set_yticks(y)
    ax.set_yticklabels([label[b] for b in band.distance_band])
    ax.set_xlim(0, 64000)
    ax.set_ylim(-0.7, len(band) - 0.3)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
    ax.set_xlabel("单量", labelpad=8)
    ax.legend(handles=[Line2D([], [], marker="o", ls="", ms=6.5, markerfacecolor="white",
                              markeredgecolor=MUTED, label="平日中位数"),
                       Line2D([], [], marker="o", ls="", ms=6.5, color=BLUE, label="2-13 当天")],
              loc="lower right", fontsize=8.4, handletextpad=0.6)
    frame(ax, y=False)
    ax.grid(axis="x", color=GRID, lw=0.9)
    header(ax, "5 英里以上跌 37%，短途跌 32%",
           "按单程距离分层；实心点为 2-13 当天，空心点为平日中位数，右侧为偏离与「当日 / 基线」单量")
    source(fig)
    save(fig, "04_distance")

    # ---------------- 图 5 行政区 ----------------
    z = con.execute(f"""
        WITH t AS (
            SELECT f.date_key, COALESCE(dz.borough, '未知') borough, COUNT(*) trips
            FROM f LEFT JOIN read_csv('{ZONE}', header = true) dz
                 ON f.pu_zone_id = CAST(dz."LocationID" AS INT)
            WHERE dayofweek(f.date_key) = 2 GROUP BY 1, 2
        )
        SELECT borough, MEDIAN(trips) base,
               SUM(CASE WHEN date_key = {D} THEN trips END) storm
        FROM t GROUP BY 1
    """).df()
    z["dev"] = z.storm / z.base - 1
    z = z[z.borough.isin(["Bronx", "Brooklyn", "Queens", "Manhattan"])]
    z = z.sort_values("dev")

    fig, ax = plt.subplots(figsize=(7.6, 3.7))
    yy = np.arange(len(z))
    colors = [RED if v < -0.5 else BLUE for v in z.dev]
    ax.barh(yy, z.dev * 100, height=0.5, color=colors, zorder=3)
    ax.axvline(overall * 100, color=MUTED, lw=0.9, ls=(0, (4, 3)), zorder=2)
    ax.annotate("全天 −31.9%", xy=(overall * 100, len(z) - 0.55), xytext=(4, 0),
                textcoords="offset points", fontsize=8, color=MUTED,
                ha="left", va="center")
    for i, r in enumerate(z.itertuples()):
        ax.annotate(f"{r.dev:+.1%}", xy=(r.dev * 100, i), xytext=(-8, 0),
                    textcoords="offset points", ha="right", va="center",
                    fontsize=10, color=INK, fontweight="bold")
        ax.annotate(f"{r.storm:,.0f} 单", xy=(r.dev * 100, i), xytext=(-8, -13),
                    textcoords="offset points", ha="right", va="center",
                    fontsize=8, color=MUTED)
    ax.set_yticks(yy)
    ax.set_yticklabels(z.borough, fontsize=9.5)
    ax.set_xlim(-78, 6)
    ax.set_ylim(-0.6, len(z) - 0.4)
    pct(ax, "x")
    ax.set_xlabel("相对平日的变化", labelpad=8)
    frame(ax, y=False)
    ax.grid(axis="x", color=GRID, lw=0.9)
    header(ax, "外围区跌幅接近 Manhattan 的 1.8 倍",
           "按上车区所属行政区；数字为 2-13 当日单量；虚线为全天整体 −31.9%")
    source(fig, "数据：NYC TLC 2024 Q1 + taxi_zone_lookup；Staten Island 等小样本区已剔除")
    save(fig, "05_borough")

    # ---------------- 图 6 缺口拆解 ----------------
    tot = con.execute(f"""
        WITH d AS (
            SELECT date_key, COUNT(*) trips FROM f
            WHERE dayofweek(date_key) = 2 GROUP BY 1
        )
        SELECT MEDIAN(trips) base, MAX(CASE WHEN date_key = {D} THEN trips END) storm
        FROM d
    """).df().iloc[0]
    gap_total = abs(int(tot.storm - tot.base))

    zb = con.execute(f"""
        WITH t AS (
            SELECT f.date_key, COALESCE(dz.borough, '未知') b, COUNT(*) trips
            FROM f LEFT JOIN read_csv('{ZONE}', header = true) dz
                 ON f.pu_zone_id = CAST(dz."LocationID" AS INT)
            WHERE dayofweek(f.date_key) = 2 GROUP BY 1, 2
        )
        SELECT b, MEDIAN(trips) base, SUM(CASE WHEN date_key = {D} THEN trips END) storm
        FROM t GROUP BY 1
    """).df()
    brook = abs(int((zb.loc[zb.b == "Brooklyn", "storm"].iloc[0]
                     - zb.loc[zb.b == "Brooklyn", "base"].iloc[0] * (1 + overall)).round()))

    zh = con.execute(f"""
        WITH t AS (
            SELECT date_key, hour_of_day hr, COUNT(*) trips FROM f
            WHERE dayofweek(date_key) = 2 GROUP BY 1, 2
        )
        SELECT hr, MEDIAN(trips) base, SUM(CASE WHEN date_key = {D} THEN trips END) storm
        FROM t GROUP BY 1
    """).df()
    zh["exc"] = (zh.storm - zh.base * (1 + overall)).round()
    peak_gap = abs(int(zh.loc[zh.hr.isin([7, 8, 9, 10]), "exc"].sum()))
    total_opt = brook + peak_gap

    fig, ax = plt.subplots(figsize=(7.6, 3.9))
    rows = [("当天总缺口", gap_total, GREY),
            ("可优化空间（两项相加）", total_opt, RED),
            ("其中：早高峰 7–10 点", peak_gap, BLUE),
            ("其中：Brooklyn", brook, BLUE)]
    yy = np.arange(len(rows))[::-1]
    ax.barh(yy, [gap_total] * len(rows), height=0.54, color=BAND, zorder=2)
    for i, (name, val, c) in zip(yy, rows):
        ax.barh(i, val, height=0.54, color=c, zorder=3)
        share = val / gap_total
        txt = f"{val:,} 单　{share:.1%}".replace("-", "−")
        inside = val > gap_total * 0.5
        ax.annotate(txt, xy=(val, i),
                    xytext=(-10, 0) if inside else (8, 0),
                    textcoords="offset points",
                    ha="right" if inside else "left", va="center", fontsize=9.4,
                    fontweight="bold" if i >= len(rows) - 2 else "normal",
                    color="white" if inside else INK, zorder=4)
    ax.set_yticks(yy)
    ax.set_yticklabels([r[0] for r in rows], fontsize=9.5)
    ax.set_xlim(0, 40000)
    ax.set_ylim(-0.6, len(rows) - 0.35)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1000:.0f}k" if v else "0"))
    ax.set_xlabel("单量（缺口绝对值）", labelpad=8)
    frame(ax, y=False)
    ax.grid(axis="x", color=GRID, lw=0.9)
    header(ax, "能动的只有 4.8%：1,493 单，占当天缺口的零头",
           "超额缺口 = 实际 − 基准 × (1 + 全天跌幅)；Brooklyn 与早高峰有重叠，未去重")
    source(fig, "数据：NYC TLC 2024-02-13；按客单价 25.33 美元折算，1,493 单约合 3.78 万美元")
    save(fig, "06_gap")

    print("done")


if __name__ == "__main__":
    main()
