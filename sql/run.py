"""
SQL 执行器
==========
按文件名顺序执行 sql/*.sql，打印结果并导出 Markdown。
用途：验证 SQL 能跑通（不是写完没跑过），以及给报告提供可复现的证据。

运行：python sql/run.py
输出：report/sql_results.md
"""
import glob
import os
import time

import duckdb
import pandas as pd

os.makedirs("report", exist_ok=True)


def df_to_md(df, max_rows=40):
    if df.empty:
        return "_(空结果)_"
    cols = [str(c) for c in df.columns]
    out = ["| " + " | ".join(cols) + " |",
           "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, row in df.head(max_rows).iterrows():
        cells = []
        for v in row:
            if pd.isna(v):
                cells.append("")
            elif isinstance(v, float):
                cells.append(f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:,.0f}")
            elif isinstance(v, int):
                cells.append(f"{v:,}")
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    if len(df) > max_rows:
        out.append(f"\n_（仅显示前 {max_rows} 行，共 {len(df)} 行）_")
    return "\n".join(out)


def main():
    con = duckdb.connect()
    # 内存上限自适应：写死 6GB 在 4GB 笔记本会被 OOM killer 杀掉
    try:
        with open("/proc/meminfo") as f:
            total_gb = int(f.readline().split()[1]) / 1048576
    except Exception:
        total_gb = 10.0
    con.execute(f"SET memory_limit='{max(1.5, min(6.0, total_gb * 0.6)):.1f}GB'")
    con.execute("SET threads=8")

    files = sorted(glob.glob("sql/*.sql"))
    # 路径分隔符统一为 / ：否则 Windows 生成的报告在其他平台逐行 diff
    files_disp = [f.replace(os.sep, "/") for f in files]
    md = ["# SQL 执行结果", "", f"> 来源 `{', '.join(files_disp)}`，引擎 DuckDB", ""]

    for f in files:
        print(f"\n{'='*64}\n{f}\n{'='*64}")
        t0 = time.time()
        sql = open(f, encoding="utf-8").read()
        # 按分号切分，逐条执行以便定位错误
        stmts = [s.strip() for s in sql.split(";") if s.strip() and
                 any(not ln.strip().startswith("--") and ln.strip()
                     for ln in s.splitlines())]
        ok = fail = 0
        for i, st in enumerate(stmts, 1):
            head = next((ln.strip()[:70] for ln in st.splitlines()
                         if ln.strip() and not ln.strip().startswith("--")), "")
            try:
                res = con.execute(st)
                if res.description:
                    df = res.df()
                    print(f"  [{i}/{len(stmts)}] OK  {len(df)} 行 | {head}")
                    md += [f"## {os.path.basename(f)} · 语句 {i}", "",
                           "```sql", st[:400] + ("..." if len(st) > 400 else ""), "```", "",
                           df_to_md(df), ""]
                else:
                    print(f"  [{i}/{len(stmts)}] OK   | {head}")
                ok += 1
            except Exception as e:
                msg = str(e).splitlines()[0][:150]
                print(f"  [{i}/{len(stmts)}] FAIL | {head}\n        {msg}")
                md += [f"## {os.path.basename(f)} · 语句 {i} 执行失败", "", f"`{msg}`", ""]
                fail += 1
        print(f"  -> {ok} 成功 / {fail} 失败，耗时 {time.time()-t0:.1f}s")

    open("report/sql_results.md", "w", encoding="utf-8", newline="\n").write("\n".join(md))
    print(f"\n结果已写入 report/sql_results.md")

    # 关键核对
    print(f"\n{'='*64}\n数据核对\n{'='*64}")
    qc = con.execute("SELECT * FROM qc_report").df().to_string(index=False)
    print(qc)
    open("report/qc_report.txt", "w", encoding="utf-8", newline="\n").write("# 清洗自检\n\n```\n" + qc + "\n```\n")


if __name__ == "__main__":
    main()
