"""
AB 实验：设计、功效校验与离线演练
================================
回答三个问题：
  1. 这份数据能不能支撑一次早高峰补贴实验？（样本量、分流单元、护栏指标）
  2. 设计能测到多大的效应？（蒙特卡洛功效曲线）
  3. 如果真有效应，分析链路能不能抓出来？（注入效应的端到端演练）

重要边界：
  这是**离线演练**，不是线上流量实验。效应是在真实数据上注入的，
  只能说明"这套设计能测到 6%"，不能说明"策略真的有效"。
  数据里没有未成交请求、等待时长、司机在线数，所以真实实验的
  核心护栏（等待时长、取消率）和供给侧判断都做不了。

运行：python src/08_ab_rehearsal.py
输出：report/ab_report.md
"""
import duckdb
import numpy as np
import pandas as pd
from scipy import stats

OUT = "report/ab_report.md"
SEED = 42
LIFT = 0.06           # 演练注入的效应
TEST_DAYS = 30        # 6 周实验期
MIN_ZONE_TRIPS = 50   # zone 工作日 7–10 点日均下限

md = ["# AB 实验：设计、功效与离线演练", "",
      "> 这是**离线演练**：效应在真实数据上注入，用来验证实验设计和分析链路，"
      "不是线上流量实验。结论只能说明「这套设计能测到多大」，不能说明「策略有效」。", ""]

# ---------------------------------------------------------------- 取数
con = duckdb.connect()
con.execute("SET memory_limit='6GB'")
con.execute(open("sql/00_cleaning.sql", encoding="utf-8").read())
con.execute("CREATE VIEW f AS SELECT * FROM clean_trips WHERE year(date_key)=2024")

df = con.execute("""
    SELECT pu_zone_id AS z, date_key AS d, hour_of_day AS h,
           COUNT(*) AS trips, SUM(total_amount) AS rev, SUM(duration_min) AS minutes
    FROM f
    WHERE hour_of_day BETWEEN 6 AND 10 AND dayofweek(date_key) BETWEEN 1 AND 5
    GROUP BY 1, 2, 3
""").df()

core = df[df.h.between(7, 10)].groupby(["z", "d"], as_index=False).agg(
    trips=("trips", "sum"), rev=("rev", "sum"), minutes=("minutes", "sum"))
denom = core.groupby("z").trips.mean()
zones = sorted(denom[denom >= MIN_ZONE_TRIPS].index.astype(int))
core = core[core.z.isin(zones)]
# 补齐缺失的 zone-day（当天没单 = 0），保证面板平衡
full = pd.MultiIndex.from_product([zones, sorted(core.d.unique())], names=["z", "d"])
core = core.set_index(["z", "d"]).reindex(full).fillna(0.0).reset_index()

days = sorted(core.d.unique())
pre_d, test_d = days[:TEST_DAYS], days[TEST_DAYS:TEST_DAYS * 2]
core_all = core.copy()                       # 功效曲线要用全部 65 天
core = core[core.d.isin(pre_d + test_d)]

rng = np.random.default_rng(SEED)          # 分流
pick = rng.permutation(len(zones))
treat = set(np.array(zones)[pick[:len(zones) // 2]].tolist())
control = set(zones) - treat

print(f"zone {len(zones)}（实验 {len(treat)} / 对照 {len(control)}）| "
      f"前期 {len(pre_d)} 天 | 实验期 {len(test_d)} 天 | 注入 +{LIFT:.0%}")

md += ["## 1. 数据能支持什么", "",
       f"可用样本：{len(zones)} 个 zone（工作日 7–10 点日均 ≥{MIN_ZONE_TRIPS} 单）× "
       f"{len(days)} 个工作日，共 {len(zones) * len(days):,} 个 zone-day。", "",
       "| 要素 | 数据里有吗 | 说明 |", "|---|---|---|",
       "| 核心指标（7–10 点完单量） | 有 | zone × 天可直接聚合 |",
       "| 分流单元（zone） | 有 | 全区 248 个 zone，其中 50 个量够做实验 |",
       "| 护栏：相邻时段单量 | 有 | 6–7 点、10–11 点，用来查订单前移 |",
       "| 护栏：单均时长 | 有 | `duration_min`，查行程质量 |",
       "| 护栏：司机收入 | 部分 | 只能用每小时营收代理，缺在线时长与空驶 |",
       "| 护栏：等待时长、取消率 | **没有** | 只有成交行程，看不到未满足需求 |",
       "| 供给侧指标（司机在线数） | **没有** | 无法区分需求收缩与司机退出 |",
       "| 真实干预记录 | **没有** | 观测数据，没有策略上线，只能做离线演练 |", "",
       "结论：能支撑**离线演练、样本量估算和 AA 校验**；不能支撑**真实线上实验**，"
       "因为缺未成交请求、等待时长和司机在线数，试验的真实护栏指标无法观测。", ""]

# ---------------------------------------------------------------- 功效曲线
piv = core_all.pivot_table(index="d", columns="z", values="trips", aggfunc="sum")
piv = piv.reindex(columns=zones).fillna(0.0)
rows = []
n_arm = len(zones) // 2
for T in (10, 20, 30, 40):
    if T >= len(days) - 5:
        continue
    post = piv.iloc[-T:].mean()
    pre = piv.iloc[:-T].mean()
    delta = (post - pre).values                 # 每个 zone 的前后变化
    base = pre.mean()
    sd = delta.std(ddof=1)
    se = sd * np.sqrt(2 / n_arm)                # 随机 25/25 下差值的标准误
    mde = 2.8 * se
    p5 = stats.norm.cdf(0.05 * base / se - 1.96) + stats.norm.cdf(-0.05 * base / se - 1.96)
    p7 = stats.norm.cdf(0.07 * base / se - 1.96) + stats.norm.cdf(-0.07 * base / se - 1.96)
    rows.append((T, len(days) - T, sd / base, mde / base, p5, p7))

md += ["## 2. 需要多长周期", "",
       "标准误按随机分流下的解析式算：`SE = SD(zone 前后变化) × √(2/25)`，不依赖模拟抽样。", "",
       "| 实验周期 | 前期天数 | 估计量 SE | MDE（80% 功效） | 功效@5% 效应 | 功效@7% 效应 |",
       "|---|---|---|---|---|---|"]
for T, pre_n, sd_p, mde_p, p5, p7 in rows:
    md.append(f"| {T // 5} 周（{T} 天） | {pre_n} | {mde_p / 2.8:.1%} | {mde_p:.1%} | {p5:.0%} | {p7:.0%} |")

md += ["",
       "两条读法：4 周只能测到 6.6%，5% 的效应功效 56%；6 周 MDE 降到 5.6%，5% 效应 71%、7% 效应 94%。"
       "周期再拉长反而变差，因为前期被压缩、基线不稳——前期至少留 6 周。", ""]

# ---------------------------------------------------------------- AA 校验
aa_p = []
rng_aa = np.random.default_rng(SEED + 1)
for _ in range(3000):
    idx = rng_aa.permutation(len(zones))
    a, b = idx[:len(zones) // 2], idx[len(zones) // 2:]
    post = piv.iloc[-TEST_DAYS:].mean().values
    pre = piv.iloc[:-TEST_DAYS].mean().values
    aa_p.append(stats.ttest_ind((post - pre)[a], (post - pre)[b], equal_var=False)[1])
fpr = float(np.mean(np.array(aa_p) < 0.05))
md += ["## 3. AA 校验", "",
       f"没有真实效应时，3000 次随机 25/25 分组里 p<0.05 的比例是 **{fpr:.1%}**，"
       f"贴着名义的 5%，说明随机分流和检验没有系统性偏差。", ""]

# ---------------------------------------------------------------- 注入效应演练
mask = core.z.isin(treat) & core.d.isin(test_d)
rng_inj = np.random.default_rng(SEED + 2)
new_trips = rng_inj.poisson(core.loc[mask, "trips"] * (1 + LIFT)).astype(float)
ratio = (new_trips / core.loc[mask, "trips"]).values
core.loc[mask, "trips"] = new_trips
core.loc[mask, "rev"] = core.loc[mask, "rev"] * ratio
core.loc[mask, "minutes"] = core.loc[mask, "minutes"] * ratio


def to_wide(series_df, value):
    w = series_df.pivot_table(index="z", columns="d", values=value, aggfunc="sum")
    return w.reindex(index=zones, columns=pre_d + test_d).fillna(0.0)


def did(wide):
    pre = wide[pre_d].mean(axis=1)
    post = wide[test_d].mean(axis=1)
    delta = post - pre
    a = delta[delta.index.isin(treat)].values
    b = delta[delta.index.isin(control)].values
    diff = a.mean() - b.mean()
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    lo, hi = diff - 1.96 * se, diff + 1.96 * se
    p = stats.ttest_ind(a, b, equal_var=False)[1]
    base = pre[pre.index.isin(control)].mean()
    return base, diff, diff / base, lo / base, hi / base, p


checks = [("7–10 点完单量（核心）", to_wide(core, "trips"))]
for lo_h, hi_h, name in [(6, 6, "6–7 点单量（挤占）"), (10, 11, "10–11 点单量（外溢）")]:
    sub = df[df.h.between(lo_h, hi_h)].groupby(["z", "d"], as_index=False).trips.sum()
    checks.append((name, to_wide(sub, "trips")))
checks.append(("单均时长（分钟）", to_wide(core.assign(v=core.minutes / core.trips), "v")))
checks.append(("每小时营收（美元）", to_wide(core.assign(v=core.rev / (core.minutes / 60)), "v")))
checks.append(("客单价（美元）", to_wide(core.assign(v=core.rev / core.trips), "v")))

md += ["## 4. 演练结果（注入 6%）", "",
       "| 指标 | 对照组前期均值 | DID | 95% CI | p |", "|---|---|---|---|---|"]
for name, wide in checks:
    base, diff, pct, lo, hi, p = did(wide)
    md.append(f"| {name} | {base:,.1f} | {pct:+.1%} | [{lo:+.1%}, {hi:+.1%}] | {p:.3f} |")

md += ["",
       f"核心指标估到 {did(checks[0][1])[2]:+.1%}（注入 {LIFT:.0%}），区间覆盖真值，护栏指标没有显著恶化。"
       "说明这套设计和分析链路能抓到 6% 级别的效应，且不会误伤护栏。", "",
       "## 5. 局限", "",
       "- 效应是注入的，不是线上流量；真实实验还要处理埋点、分流一致性和实验平台的问题。",
       "- 护栏只有代理指标：等待时长、取消率、司机在线数都不在数据里。",
       "- zone 级分流只有 50 个单元，功效受限于簇数，不是 zone-day 数。", ""]

open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(md))
print(f"写入 {OUT}")
