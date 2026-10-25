"""
需求预测与异常检测
==================
思路：运营提前预测不到暴雪（气象预报也不准），但能预测"这天应该有多少单"。
     用季节性模式算基线，再看实际偏离多少 → 偏离超过阈值就触发预案。

方法：
  特征只用日历信息（星期几 + 月份），不含任何当年才有的信息，
  所以能严格做时间外推验证：用 2023 Q1 训练，预测 2024 Q1。

  模型：对数线性 OLS（手写闭式解，不引 sklearn —— 90 天训练样本，线性模型够用，
        复杂模型反而过拟合）

  对比基线：
    B1 全体均值
    B2 同星期中位数（项目里现用的方法）

运行：python src/07_forecast.py
输出：report/forecast_report.md
"""
import duckdb
import numpy as np
import pandas as pd

pd.set_option("display.width", 200)
OUT = "report/forecast_report.md"
FIG = "report/figures/07_forecast.png"
md = ["# 需求预测与异常检测", ""]

INK, MUTED, GRID = "#1B2733", "#77828D", "#E8EBEE"
BLUE, RED, BAND = "#1F4E79", "#B1382C", "#EDF0F3"

# 2023 Q1 的法定假日（用于标记，不进模型特征，只做分析参考）
HOLIDAYS = {
    "2023-01-01", "2023-01-16", "2023-02-20",   # 元旦、马丁路德金日、总统日
    "2024-01-01", "2024-01-15", "2024-02-19",   # 同上
}
DOW_ZH = {0: "周日", 1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六"}


def load_daily():
    con = duckdb.connect(); con.execute("SET memory_limit='6GB'")
    con.execute(open('sql/00_cleaning.sql', encoding='utf-8').read())
    df = con.execute("""
    SELECT date_key AS dt,
           dayofweek(date_key) AS dow,
           month(date_key) AS mon,
           year(date_key) AS yr,
           COUNT(*) AS trips,
           SUM(total_amount) AS rev
    FROM clean_trips
    GROUP BY 1, 2, 3, 4 ORDER BY 1
    """).df()
    con.close()
    df["dt"] = pd.to_datetime(df["dt"])
    df["is_holiday"] = df["dt"].dt.strftime("%Y-%m-%d").isin(HOLIDAYS).astype(int)
    return df


def design_matrix(df):
    """特征：星期几 one-hot（**周日为基准**，dow=0 被省略）+ 月份 one-hot（1 月为基准）+ 截距
    刻意不含年份、不含趋势 —— 否则时间外推时模型"看到了未来"。
    """
    n = len(df)
    X = np.ones((n, 1))                      # 截距
    for d in range(1, 7):                    # dow 0=周日作为基准，只对 1-6 建哑变量
        X = np.hstack([X, (df["dow"].values == d).reshape(-1, 1).astype(float)])
    for m in [2, 3]:                         # 月份，1 月为基准
        X = np.hstack([X, (df["mon"].values == m).reshape(-1, 1).astype(float)])
    return X


def design_matrix_weekday(df):
    """消融基线：只保留星期几哑变量（+截距），用来判断提升到底来自星期几还是月份"""
    n = len(df)
    X = np.ones((n, 1))
    for d in range(1, 7):
        X = np.hstack([X, (df["dow"].values == d).reshape(-1, 1).astype(float)])
    return X


def ols(X, y, ridge=1e-6):
    """闭式解 + 极小岭项（防共线）"""
    A = X.T @ X + ridge * np.eye(X.shape[1])
    A[0, 0] -= ridge                         # 截距不正则化
    return np.linalg.solve(A, X.T @ y)


def evaluate(y_true, y_pred, label):
    mae = np.mean(np.abs(y_true - y_pred))
    mape = np.mean(np.abs((y_true - y_pred) / y_true)) * 100
    return {"模型": label, "MAE": round(mae, 0), "MAPE_%": round(mape, 2)}


def md_table(df):
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join(
            "" if pd.isna(v) else (f"{v:,.2f}" if isinstance(v, float) else f"{v:,}" if isinstance(v, (int, np.integer)) else str(v))
            for v in r) + " |")
    return "\n".join(out)


def plot_forecast(te, mu, sd):
    """预测 vs 实际：把「模型看不懂暴雪、但看得出异常」画出来"""
    import os

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter

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
    os.makedirs(os.path.dirname(FIG), exist_ok=True)

    dt = te["dt"]
    lo, hi = mu - 1.96 * sd, mu + 1.96 * sd
    fig, ax = plt.subplots(figsize=(7.6, 4.0))
    ax.fill_between(dt, te["pred"] * (1 + lo / 100), te["pred"] * (1 + hi / 100),
                    color=BAND, lw=0, zorder=1)
    ax.plot(dt, te["pred"], color=MUTED, lw=1.1, ls=(0, (3, 2)), zorder=2)
    ax.plot(dt, te["trips"], color=BLUE, lw=1.4, zorder=3)

    t = te[te["dt"] == pd.Timestamp("2024-02-13")].iloc[0]
    ax.scatter([t["dt"]], [t["trips"]], s=30, color=RED, edgecolor="white",
               linewidth=0.9, zorder=5)
    ax.annotate(f"2-13：预测 {t['pred']:,.0f}，实际 {t['trips']:,.0f}\n"
                f"残差 {t['resid_pct']:+.1f}%，z = {(t['resid_pct'] - mu) / sd:.1f}".replace("-", "\u2212"),
                xy=(t["dt"], t["trips"]), xytext=(24, -22), textcoords="offset points",
                fontsize=8.6, color=RED, fontweight="bold", linespacing=1.6,
                arrowprops=dict(arrowstyle="-|>", color=RED, lw=0.9,
                                shrinkA=2, shrinkB=5,
                                connectionstyle="arc3,rad=-0.18"))

    ax.set_ylim(55000, 140000)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v/1000:.0f}k"))
    ax.set_ylabel("日行程量", labelpad=8)
    ax.grid(axis="y", color=GRID, lw=0.9)
    ax.set_axisbelow(True)
    ax.tick_params(pad=5)
    ax.legend(handles=[Line2D([], [], color=BLUE, lw=1.4, label="实际"),
                       Line2D([], [], color=MUTED, lw=1.1, ls=(0, (3, 2)), label="模型预测"),
                       Line2D([], [], color="#DDE2E7", lw=7, label=f"残差 95% 区间（{lo:.0f}% ~ +{hi:.0f}%）")],
              loc="upper left", fontsize=8.2, handlelength=2.0, labelspacing=0.45)

    ax.set_title("日历特征模型：2-13 该有 10 万单，只有 6.7 万",
                 loc="left", fontsize=13, fontweight="bold", color=INK, pad=27)
    ax.annotate("训练 2023 Q1（90 天）→ 预测 2024 Q1（91 天）；特征只有星期几和月份，不含年份与趋势",
                xy=(0, 1), xytext=(0, 8), textcoords="offset points",
                xycoords="axes fraction", ha="left", va="bottom",
                fontsize=8.6, color=MUTED)
    fig.text(0.995, -0.02, "数据：NYC TLC 2023–2024 Q1，清洗后 1,838 万行",
             ha="right", va="top", fontsize=7.5, color=MUTED)
    fig.savefig(FIG, bbox_inches="tight", pad_inches=0.32, facecolor="white")
    plt.close(fig)
    print(f"写入 {FIG}")


def main():
    global md
    df = load_daily()
    tr = df[df.yr == 2023].copy()
    te = df[df.yr == 2024].copy()
    print("=" * 64)
    print(f"训练集 2023 Q1: {len(tr)} 天  测试集 2024 Q1: {len(te)} 天")
    print("=" * 64)
    md += [f"训练集 2023 Q1 共 {len(tr)} 天，测试集 2024 Q1 共 {len(te)} 天。"
           f"特征只用星期几和月份，**不含年份和趋势**，所以是严格的时间外推。", ""]

    # ---------- 训练 ----------
    Xtr, ytr_raw = design_matrix(tr), tr["trips"].values.astype(float)
    ytr = np.log(ytr_raw)                     # 对数变换：量级大且右偏
    w = ols(Xtr, ytr)

    # ---------- 预测 ----------
    Xte = design_matrix(te)
    pred = np.exp(Xte @ w)
    te = te.assign(pred=pred, resid=te["trips"] - pred,
                   resid_pct=(te["trips"] / pred - 1) * 100)

    # ---------- 对比基线 ----------
    rows = []
    # B1 全体均值（用训练集均值，避免泄漏）
    rows.append(evaluate(te["trips"].values, np.full(len(te), ytr_raw.mean()), "B1 训练集均值"))
    # B2 同星期中位数（训练集）
    dow_med = tr.groupby("dow")["trips"].median()
    b2 = te["dow"].map(dow_med).values
    rows.append(evaluate(te["trips"].values, b2, "B2 同星期中位数"))
    # M0 消融：只保留星期几，不含月份；如果它和 B2 差不多，说明提升来自月份而不是星期几
    Xtr0, Xte0 = design_matrix_weekday(tr), design_matrix_weekday(te)
    pred0 = np.exp(Xte0 @ ols(Xtr0, ytr))
    rows.append(evaluate(te["trips"].values, pred0, "M0 仅星期几"))
    # 模型
    rows.append(evaluate(te["trips"].values, pred, "M1 对数线性 OLS"))
    cmp_df = pd.DataFrame(rows)
    print("\n=== 模型对比（在 2024 Q1 上）===")
    print(cmp_df.to_string(index=False))
    md += ["## 1. 和基线比", "", md_table(cmp_df), "",
           "同星期中位数已经是个不错的基线（MAPE 优于全体均值）。"
           "**但 M0 消融显示：只保留星期几时，MAPE 与 B2 几乎持平；"
           "M1 相对 B2 的约 30% 提升几乎全部来自月份哑变量**——"
           "日行程量里可预测的部分主要是 1→3 月的季节水平，星期几只解释很小一部分。"
           "这也解释了为什么不需要复杂模型：把季节项加进去就够了。", ""]

    # ---------- 残差分布 ----------
    resid = te["resid_pct"].values
    mu, sd = resid.mean(), resid.std(ddof=1)
    print(f"\n=== 残差分布（2024 Q1 预测误差）===")
    print(f"  均值 {mu:.2f}%   标准差 {sd:.2f}%")
    print(f"  95% 区间 [{(mu-1.96*sd):.1f}%, {(mu+1.96*sd):.1f}%]")
    md += ["## 2. 残差分布与告警阈值", "",
           f"2024 Q1 的预测残差均值 {mu:.2f}%、标准差 {sd:.2f}%，"
           f"95% 区间为 **[{mu-1.96*sd:.1f}%, {mu+1.96*sd:.1f}%]**。",
           "",
           f"也就是说，正常情况下日行程量偏离预测值超过 ±{1.96*sd:.0f}% 的概率不到 5%——"
           f"这就是告警阈值的经验依据。", ""]

    # ---------- 2-13 的残差 ----------
    plot_forecast(te, mu, sd)
    target = te[te.dt == "2024-02-13"]
    if len(target):
        t = target.iloc[0]
        z = (t.resid_pct - mu) / sd
        mask = (te.dt != pd.Timestamp("2024-02-13")).values
        mu2, sd2 = resid[mask].mean(), resid[mask].std(ddof=1)
        z2 = (t.resid_pct - mu2) / sd2
        print(f"\n=== 2024-02-13 ===")
        print(f"  预测 {t.pred:,.0f}   实际 {t.trips:,.0f}   残差 {t.resid:+,.0f} ({t.resid_pct:+.1f}%)")
        print(f"  z-score = {z:.1f}")
        print(f"  剔除 2-13 后：均值 {mu2:.2f}% 标准差 {sd2:.2f}% |z| = {abs(z2):.1f}")
        md += ["## 3. 2-13 在模型眼里有多反常", "",
               f"| 项目 | 值 |", "|---|---|",
               f"| 模型预测 | {t.pred:,.0f} 单 |",
               f"| 实际 | {t.trips:,.0f} 单 |",
               f"| 残差 | {t.resid:+,.0f} 单（{t.resid_pct:+.1f}%）|",
               f"| z-score | **{z:.1f}** |",
               "",
               f"z-score {z:.1f} 意味着这个偏离相当于 {abs(z):.0f} 个标准差。"
               f"如果只看星期几和月份，这天「应该」有 {t.pred:,.0f} 单，实际只有 {t.trips:,.0f}——"
               f"**模型在这一天彻底失灵，而这正是它该失灵的地方**："
               f"外生冲击本来就不在日历特征的信息里。",
               "",
               f"**样本内标准化的保守性**：μ 和 σ 都含 2-13 本身，离群点会抬高 σ、缩小 |z|。"
               f"剔除 2-13 重算后 σ 从 {sd:.2f}% 降到 {sd2:.2f}%，|z| 从 {abs(z):.1f} 升到 {abs(z2):.1f}。", ""]

    # ---------- 异常检测能力 ----------
    print("\n=== 按 |z| 排序，模型认为最反常的 8 天 ===")
    te2 = te.assign(z=(te.resid_pct - mu) / sd).reindex(
        te.assign(z=(te.resid_pct - mu) / sd).z.abs().sort_values(ascending=False).index).head(8)
    show = te2[["dt", "dow", "is_holiday", "trips", "pred", "resid_pct", "z"]].copy()
    show["dt"] = show["dt"].dt.strftime("%Y-%m-%d")
    show = show.round(2)
    show.columns = ["日期", "星期", "节假日", "实际", "预测", "残差_%", "z"]
    show["星期"] = show["星期"].map(DOW_ZH)
    show["节假日"] = show["节假日"].map({0: "否", 1: "是"})
    print(show.to_string(index=False))
    md += ["## 4. 模型认为最反常的 8 天", "", md_table(show), "",
           "排第一的是 2-13（暴雪）；2-4 名都是元旦后的节后效应，总统日在第 6 位——"
           "**模型不需要知道「暴雪」这个概念，靠日历特征就能把异常日挑出来**。"
           "这是这套方法的实际用法：不预测冲击，只监控偏离。", ""]

    # ---------- 业务用法 ----------
    md += ["## 5. 怎么用（当前只做日总量）", "",
           "```",
           "T-1 日：用离线模型预测明天日总量 → 作为运力投放基准（当前为回测，未接生产调度）",
           "T 日收盘后：算 实际 / 预测 的比值",
           "  比值落在 ±{:.0f}% 内 → 正常".format(1.96 * sd),
           "  低于  -{:.0f}%     → 疑似需求异常，进入观察".format(1.96 * sd),
           "  高于  +{:.0f}%     → 疑似需求激增".format(1.96 * sd),
           "```", "",
           "**日内每小时监控是设计意图，不是已实现能力**——本脚本只做日总量预测，"
           "没有小时级模型，也没有日内累计占比曲线。"
           "要支持日内动态调整，需要另建时段级模型，属于下一步工作。", ""]

    # ---------- 局限 ----------
    md += ["## 6. 局限", "",
           "- 训练集只有 2023 Q1 的 90 天，样本量小，模型参数估计不稳。",
           "- 没有节假日特征进模型（只做了标记），节假日那几天会被判成异常。",
           "- 只预测日总量，没做分时段。实际排班需要小时级预测，得换成 24 个模型或加时段特征。",
           "- 2024 Q1 整体比 2023 低 0.6%，模型没有年际水平项，所以残差均值略偏负——"
           "真实部署时应该用滚动窗口重训（比如每月重训一次），而不是固定用去年数据。", ""]

    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(md))
    print(f"\n写入 {OUT}")


if __name__ == "__main__":
    main()
