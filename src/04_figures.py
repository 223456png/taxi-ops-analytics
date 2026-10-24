"""
图表生成
========
6 张图，对应 README 的关键结论。
风格：去网格、结论写进标题、三色（蓝/红/灰）。

运行：python src/04_figures.py
输出：report/figures/*.png
"""
import os

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

OUT = "report/figures"
os.makedirs(OUT, exist_ok=True)
FACT = 'clean_trips'   # 单一事实来源：视图由 sql/00_cleaning.sql 定义
D = "DATE '2024-02-13'"

plt.rcParams.update({
    "font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "figure.dpi": 160, "savefig.dpi": 160,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.edgecolor": "#BBBBBB", "axes.linewidth": 0.8, "axes.grid": False,
    "font.size": 10.5, "text.color": "#222222",
    "axes.labelcolor": "#444444", "xtick.color": "#555555", "ytick.color": "#555555",
})
BLUE, RED, GREY, DARK = "#2C6E9B", "#B03A2E", "#9AA5AD", "#333333"
pct0 = FuncFormatter(lambda v, _: f"{v:.0%}")


def save(fig, name):
    fig.savefig(f"{OUT}/{name}.png", bbox_inches="tight", facecolor="white", pad_inches=0.2)
    plt.close(fig)
    print(f"  {OUT}/{name}.png")


def clean(ax):
    ax.yaxis.grid(True, color="#E9E9E9", lw=0.8)
    ax.set_axisbelow(True)


def main():
    con = duckdb.connect(); con.execute("SET memory_limit='6GB'")
    con.execute(open('sql/00_cleaning.sql', encoding='utf-8').read())
    con.execute(f"CREATE VIEW f AS SELECT * FROM {FACT} WHERE year(date_key) = 2024")

    # 日度数据
    daily = con.execute("""
        SELECT date_key, dayofweek(date_key) dow, COUNT(*) trips
        FROM f GROUP BY 1,2 ORDER BY 1
    """).df()
    daily["base"] = daily.groupby("dow").trips.transform("median")
    daily["dev"] = daily.trips / daily.base - 1

    # ---------- 图 1 日度趋势 ----------
    fig, ax = plt.subplots(figsize=(10.5, 4.3))
    fig.subplots_adjust(bottom=0.22)
    x = pd.to_datetime(daily.date_key)
    ax.plot(x, daily.trips, lw=1.4, color=BLUE, zorder=3, label="实际行程量")
    ax.plot(x, daily.base, lw=1.1, color=GREY, ls="--", zorder=2, label="同星期基线")
    bad = daily[daily.dev < -0.18]
    ax.scatter(pd.to_datetime(bad.date_key), bad.trips, s=42, color=RED, zorder=5)
    ax.annotate("2-13 暴雪\n−31.9%", xy=(pd.Timestamp("2024-02-13"), 67088),
                xytext=(pd.Timestamp("2024-01-20"), 45000),
                fontsize=9.5, color=RED, fontweight="bold",
                arrowprops=dict(arrowstyle="->", color=RED, lw=1.2))
    ax.set_ylabel("日行程量", fontsize=9.5)
    ax.set_title("2024 Q1 日行程量：四次跌破基线 18% 以上", fontsize=11.5,
                 color=DARK, loc="left", pad=12)
    ax.legend(frameon=False, fontsize=9, loc="lower right")
    clean(ax)
    save(fig, "01_daily")

    # ---------- 图 2 分时段：量 vs 速度 ----------
    h = con.execute(f"""
        WITH t AS (
            SELECT date_key, hour_of_day hr, COUNT(*) trips, AVG(speed_mph) spd
            FROM f WHERE dayofweek(date_key)=2 GROUP BY 1,2
        )
        SELECT hr, SUM(CASE WHEN date_key={D} THEN trips END) s_trips,
               MEDIAN(trips) b_trips,
               AVG(CASE WHEN date_key={D} THEN spd END) s_spd,
               MEDIAN(CASE WHEN date_key<>{D} THEN spd END) b_spd
        FROM t GROUP BY 1 ORDER BY 1
    """).df()
    h["trip_dev"] = (h.s_trips / h.b_trips - 1) * 100
    h["spd_dev"] = (h.s_spd / h.b_spd - 1) * 100

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6.2), sharex=True,
                                   gridspec_kw={"height_ratios": [1, 1], "hspace": 0.18})
    fig.subplots_adjust(bottom=0.16)
    ax1.bar(h.hr, h.trip_dev, color=[RED if v < -30 else "#C98B7F" for v in h.trip_dev], width=0.72)
    ax1.axhline(0, color=DARK, lw=0.8)
    ax1.set_ylabel("行程量变化 %", fontsize=9.5)
    ax1.set_title("暴雪日分时段：量跌最多的时段，速度反而最快", fontsize=11.5,
                  color=DARK, loc="left", pad=10)
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    clean(ax1)

    ax2.bar(h.hr, h.spd_dev, color=[BLUE if v > 0 else GREY for v in h.spd_dev], width=0.72)
    ax2.axhline(0, color=DARK, lw=0.8)
    ax2.set_ylabel("平均速度变化 %", fontsize=9.5)
    ax2.set_xlabel("小时", fontsize=9.5)
    ax2.set_xticks(range(0, 24, 2))
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}%"))
    clean(ax2)
    save(fig, "02_hourly")

    # ---------- 图 3 速度分组 vs 量变化（核心证据）----------
    g = h.dropna()
    g["grp"] = np.where(g.spd_dev < -8, "A. 速度明显下降",
                        np.where(g.spd_dev > 0, "C. 速度反而上升", "B. 速度基本持平"))
    agg = g.groupby("grp").agg(n=("hr", "size"), trip=("trip_dev", "mean"),
                               spd=("spd_dev", "mean")).reset_index()

    fig, ax = plt.subplots(figsize=(8.4, 4.4))
    fig.subplots_adjust(bottom=0.2)
    xx = np.arange(len(agg))
    w = 0.36
    b1 = ax.bar(xx - w/2, agg.trip, w, color=RED, label="行程量变化", zorder=3)
    b2 = ax.bar(xx + w/2, agg.spd, w, color=BLUE, label="速度变化", zorder=3)
    ax.axhline(0, color=DARK, lw=0.9)
    for bars in (b1, b2):
        for b in bars:
            v = b.get_height()
            ax.text(b.get_x()+b.get_width()/2, v + (2.5 if v >= 0 else -2.5),
                    f"{v:+.1f}%", ha="center", va="bottom" if v >= 0 else "top",
                    fontsize=9.5, color=DARK)
    ax.set_xticks(xx)
    ax.set_xticklabels([f"{r.grp}\n({r.n} 个时段)" for _, r in agg.iterrows()], fontsize=9)
    ax.set_ylabel("相对平日的变化", fontsize=9.5)
    ax.set_title("路况越好，量跌得越多 —— 这不是供给问题", fontsize=11.5,
                 color=DARK, loc="left", pad=12)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:+.0f}%"))
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    ax.set_ylim(-42, 30)
    clean(ax)
    save(fig, "03_supply_vs_demand")

    # ---------- 图 4 距离分层 ----------
    band = con.execute(f"""
        WITH b AS (
            SELECT date_key, distance_band, COUNT(*) trips FROM f
            WHERE dayofweek(date_key)=2 GROUP BY 1,2
        )
        SELECT distance_band,
            MEDIAN(trips) base,
            SUM(CASE WHEN date_key={D} THEN trips END) storm
        FROM b GROUP BY 1 ORDER BY 1
    """).df()
    band["dev"] = (band.storm / band.base - 1) * 100
    label = {"1_short": "<2 mi", "2_mid": "2–5 mi", "3_long": "5–10 mi", "4_very_long": "10+ mi"}

    fig, ax = plt.subplots(figsize=(8.2, 4.3))
    fig.subplots_adjust(bottom=0.2)
    colors = [BLUE if v == band.dev.max() else "#B8CBD9" for v in band.dev]
    bars = ax.bar(range(len(band)), band.dev, color=colors, width=0.55, zorder=3)
    for b, v in zip(bars, band.dev):
        ax.text(b.get_x()+b.get_width()/2, v - 1.6, f"{v:.1f}%", ha="center",
                va="top", fontsize=10.5, color=DARK, fontweight="bold")
    ax.set_xticks(range(len(band)))
    ax.set_xticklabels([label[b] for b in band.distance_band], fontsize=9.5)
    ax.set_ylabel("相对平日的变化", fontsize=9.5)
    ax.set_title(f"5 英里以上跌得更狠（{band.dev.iloc[2]:.1f}%），短途基本跟大盘持平",
                 fontsize=11.5, color=DARK, loc="left", pad=12)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_ylim(band.dev.min()*1.25, 0)
    clean(ax)
    save(fig, "04_distance")

    # ---------- 图 5 区域 ----------
    z = con.execute(f"""
        WITH t AS (
            SELECT f.date_key, COALESCE(dz.borough,'未知') borough, COUNT(*) trips
            FROM f LEFT JOIN read_csv('data/taxi_zone_lookup.csv', header=true) dz
                 ON f.pu_zone_id = CAST(dz."LocationID" AS INT)
            WHERE dayofweek(f.date_key)=2 GROUP BY 1,2
        )
        SELECT borough,
            MEDIAN(trips) base,
            SUM(CASE WHEN date_key={D} THEN trips END) storm
        FROM t GROUP BY 1 HAVING storm > 100 ORDER BY 1
    """).df()
    z["dev"] = (z.storm / z.base - 1) * 100
    z = z.sort_values("dev")

    fig, ax = plt.subplots(figsize=(8, 3.9))
    fig.subplots_adjust(bottom=0.2, left=0.18)
    colors = [RED if v < -50 else BLUE for v in z.dev]
    bars = ax.barh(range(len(z)), z.dev, color=colors, height=0.55, zorder=3)
    for b, v, s in zip(bars, z.dev, z.storm):
        ax.text(v - 1.2, b.get_y()+b.get_height()/2, f"{v:.1f}%  ({s:,.0f} 单)",
                ha="right", va="center", fontsize=9.5, color=DARK)
    ax.set_yticks(range(len(z)))
    ax.set_yticklabels(z.borough, fontsize=9.5)
    ax.set_xlabel("相对平日的变化", fontsize=9.5)
    _b = z[z.borough == "Brooklyn"].dev.iloc[0] if (z.borough == "Brooklyn").any() else z.dev.min()
    ax.set_title(f"Brooklyn 跌 {abs(_b):.1f}%，接近 Manhattan 的两倍", fontsize=11.5,
                 color=DARK, loc="left", pad=12)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.set_xlim(z.dev.min()*1.35, 0)
    clean(ax)
    save(fig, "05_borough")

    # ---------- 图 6 超额缺口瀑布 ----------
    overall = 1 - 67088/98450
    seg = con.execute(f"""
        WITH s AS (
            SELECT date_key, is_airport, COUNT(*) trips FROM f
            WHERE dayofweek(date_key)=2 GROUP BY 1,2
        ), b AS (
            SELECT date_key, distance_band, COUNT(*) trips FROM f
            WHERE dayofweek(date_key)=2 GROUP BY 1,2
        )
        SELECT
          (SELECT MEDIAN(trips) FROM b WHERE distance_band='1_short') short_base,
          (SELECT SUM(CASE WHEN date_key={D} THEN trips END) FROM b WHERE distance_band='1_short') short_storm
    """).df().iloc[0]
    short_gap = seg.short_storm - seg.short_base*(1-overall)

    fig, ax = plt.subplots(figsize=(8.6, 4.5))
    fig.subplots_adjust(bottom=0.22)
    steps = ["当天总缺口", "Brooklyn\n超额缺口", "早高峰\n超额缺口", "可优化空间"]
    vals = [-31362, -190, -1303, -1493]
    colors = [GREY, BLUE, BLUE, RED]
    bottoms = [0, -31362, -31552, 0]
    heights = [-31362, -190, -1303, -1493]
    for i, (s, h, b, c) in enumerate(zip(steps, heights, bottoms, colors)):
        ax.bar(i, h, bottom=b, color=c, width=0.55, zorder=3)
        ax.text(i, b + h - 900 if h < 0 else b + h + 900,
                f"{h:,}".replace("-", "−"), ha="center",
                va="top" if h < 0 else "bottom", fontsize=9.5,
                color=DARK, fontweight="bold" if i == 3 else "normal")
    ax.set_xticks(range(4))
    ax.set_xticklabels(steps, fontsize=9)
    ax.axhline(0, color=DARK, lw=0.9)
    ax.set_ylabel("单量", fontsize=9.5)
    ax.set_title(f"可优化空间 {abs(vals[3]):,} 单，占当天缺口的 {abs(vals[3])/abs(vals[0]):.1%}",
                 fontsize=11.5, color=DARK, loc="left", pad=12)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
    clean(ax)
    save(fig, "06_gap")

    print("done")


if __name__ == "__main__":
    main()
