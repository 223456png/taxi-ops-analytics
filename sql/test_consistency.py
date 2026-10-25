"""
口径一致性断言：三份内联清洗口径互相把关
==========================================
清洗口径的单一事实来源是 sql/00_cleaning.sql 的 clean_trips 视图，但
sql/01_build.sql 和 src/01_etl.py 因需要原始时间戳等列，各自内联了
同一套定义（全项目共三份拷贝）。历史事故：内联口径之间曾经不同步，
2024-02-13 少算 110 单，靠 SQL 对账才被发现。

本脚本让「改这里必须同步那里」从注释约定变成机器把关。三层各自独立
跑一遍完整口径，然后对 2024 部分做六方面断言：

  行数 / 金额与里程与时长合计 / 速度合计与非空行数 / 机场单数 /
  距离分层行数 / 逐支付方式行数

运行：python sql/test_consistency.py    （需 data/ 下有数据，约 2 分钟）
通过：打印三层数字并退出码 0；任何一层漂移都会退出码 1 并指出差异。
"""
import subprocess
import sys

import duckdb

REL_TOL = 1e-9   # 相对容差：920 万行浮点求和，累加顺序不同也会带来绝对漂移，
                 # 绝对容差会把相同的数误判成不一致（实测踩过）


def close(x, y):
    return abs(x - y) <= REL_TOL * max(abs(x), abs(y), 1.0)


def split_stmts(sql_text):
    """与 sql/run.py 相同的切分逻辑：按分号切，跳过纯注释段。"""
    return [s.strip() for s in sql_text.split(";") if s.strip() and
            any(not ln.strip().startswith("--") and ln.strip()
                for ln in s.splitlines())]


def run_sql_file(con, path):
    for st in split_stmts(open(path, encoding="utf-8").read()):
        con.execute(st)


def agg(con, relation, slice_2024):
    w = "WHERE date_key >= DATE '2024-01-01'" if slice_2024 else ""
    return con.execute(f"""
        SELECT COUNT(*) n,
               SUM(total_amount) amt,
               SUM(distance_mi) dist,
               SUM(duration_min) dur,
               SUM(speed_mph) speed_sum,
               COUNT(speed_mph) speed_n,
               SUM(is_airport) airport_n
        FROM {relation} {w}
    """).fetchone()


def by_band(con, relation, slice_2024):
    w = "WHERE date_key >= DATE '2024-01-01'" if slice_2024 else ""
    rows = con.execute(f"""
        SELECT distance_band, COUNT(*) FROM {relation} {w} GROUP BY 1
    """).fetchall()
    return {str(k): int(v) for k, v in rows}


def by_payment(con, relation, slice_2024):
    w = "WHERE date_key >= DATE '2024-01-01'" if slice_2024 else ""
    rows = con.execute(f"""
        SELECT payment_id, COUNT(*) FROM {relation} {w} GROUP BY 1
    """).fetchall()
    return {int(k): int(v) for k, v in rows}


def main():
    print("=" * 60)
    print("口径一致性断言：00_cleaning vs 01_build vs 01_etl")
    print("=" * 60)

    con = duckdb.connect()
    try:
        with open("/proc/meminfo") as f:
            total_gb = int(f.readline().split()[1]) / 1048576
    except Exception:
        total_gb = 10.0
    con.execute(f"SET memory_limit='{max(1.5, min(6.0, total_gb * 0.6)):.1f}GB'")

    run_sql_file(con, "sql/00_cleaning.sql")
    print("  [1/3] clean_trips 视图已建（sql/00_cleaning.sql）")
    run_sql_file(con, "sql/01_build.sql")
    print("  [2/3] fact_trips 表已建（sql/01_build.sql 内联口径）")

    pq = "powerbi/exports/fact_trips.parquet"
    print("  [3/3] 运行 src/01_etl.py 生成导出层（内联口径）...")
    r = subprocess.run([sys.executable, "src/01_etl.py"], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:], r.stderr[-1500:])
        sys.exit("01_etl.py 运行失败")
    con.execute(f"CREATE OR REPLACE VIEW etl_fact AS SELECT * FROM read_parquet('{pq}')")

    # A = 视图（单一事实来源），B = SQL 内联口径，C = Python/导出层内联口径
    a = agg(con, "clean_trips", True)
    b = agg(con, "fact_trips", False)
    c = agg(con, "etl_fact", False)
    for tag, row in [("A 00_cleaning(2024切片)", a), ("B 01_build       ", b), ("C 01_etl         ", c)]:
        print(f"  {tag} {row[0]:>10,}  金额 {row[1]:>15,.2f}  里程 {row[2]:>13,.2f}  时长 {row[3]:>12,.0f}"
              f"  均速合计 {row[4]:>14,.2f}")

    errors = []
    if not (a[0] == b[0] == c[0]):
        errors.append(f"行数不一致: A={a[0]:,} B={b[0]:,} C={c[0]:,}")
    for i, name in [(1, "金额合计"), (2, "里程合计"), (3, "时长合计"), (4, "速度合计")]:
        if not (close(a[i], b[i]) and close(b[i], c[i])):
            errors.append(f"{name}不一致: A={a[i]:,.2f} B={b[i]:,.2f} C={c[i]:,.2f}")
    for i, name in [(5, "速度非空行数"), (6, "机场单数")]:
        if not (a[i] == b[i] == c[i]):
            errors.append(f"{name}不一致: A={a[i]:,} B={b[i]:,} C={c[i]:,}")

    pa = by_payment(con, "clean_trips", True)
    pb = by_payment(con, "fact_trips", False)
    pc = by_payment(con, "etl_fact", False)
    for k in sorted(set(pa) | set(pb) | set(pc)):
        na, nb, nc = pa.get(k, 0), pb.get(k, 0), pc.get(k, 0)
        if not (na == nb == nc):
            errors.append(f"payment_id={k} 行数不一致: A={na:,} B={nb:,} C={nc:,}")

    ba = by_band(con, "clean_trips", True)
    bb = by_band(con, "fact_trips", False)
    bc = by_band(con, "etl_fact", False)
    for k in sorted(set(ba) | set(bb) | set(bc)):
        na, nb, nc = ba.get(k, 0), bb.get(k, 0), bc.get(k, 0)
        if not (na == nb == nc):
            errors.append(f"distance_band={k} 行数不一致: A={na:,} B={nb:,} C={nc:,}")

    if errors:
        print("\n  FAIL —— 三份内联口径出现漂移，改动没有同步到所有层：")
        for e in errors:
            print(f"    · {e}")
        sys.exit(1)

    print(f"\n  PASS —— 三层 {a[0]:,} 行完全一致（速度、机场单、{len(ba)} 个距离分层、{len(pa)} 类支付方式逐类对齐）")
    print("  说明：A 是 2024 切片与 B/C 对齐；视图/SQL 表/Python 导出三条链路互为对账。")


if __name__ == "__main__":
    main()
