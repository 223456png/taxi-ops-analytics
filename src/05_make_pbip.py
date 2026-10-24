"""
生成 Power BI 项目（PBIP）
==========================
读数据源 schema，生成一套双击就能打开的 Power BI 项目：
  - 5 张表 + 4 条关系（星型模型）
  - 27 个 DAX 度量值
  - 4 页 26 图表

结构（Power BI 2.159 商店版验证）：
  powerbi/TaxiOps.pbip
  powerbi/TaxiOps.SemanticModel/
      .platform
      definition.pbism
      model.bim              <- TMSL（Analysis Services 标准模型定义）
  powerbi/TaxiOps.Report/
      .platform
      definition.pbir
      definition/report.json
      definition/pages/{pages.json, page1/page.json}

运行：python src/05_make_pbip.py
"""
import json
import os
import uuid

import duckdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPORTS = os.path.join(ROOT, "powerbi", "exports")
OUT = os.path.join(ROOT, "powerbi")
PROJ = "TaxiOps"
# Power Query 里用的目录参数（正斜杠，末尾带 /）
DATA_DIR = EXPORTS.replace("\\", "/") + "/"

# DuckDB 类型 -> TMSL dataType
TYPE_MAP = {
    "BIGINT": "int64", "INTEGER": "int64", "SMALLINT": "int64",
    "DOUBLE": "double", "FLOAT": "double", "DECIMAL": "double",
    "VARCHAR": "string", "DATE": "dateTime", "TIMESTAMP": "dateTime",
    "BOOLEAN": "boolean",
}
# TMSL 里的列类型
M_TYPE = {
    "int64": "Int64.Type", "double": "type number",
    "string": "type text", "dateTime": "type datetime",
    "boolean": "type logical",
}

TABLES = [
    ("fact_trips", "fact_trips.parquet", True),
    ("dim_date", "dim_date.csv", False),
    ("dim_zone", "dim_zone.csv", False),
    ("dim_payment", "dim_payment.csv", False),
    ("dim_rate", "dim_rate.csv", False),
]

RELATIONSHIPS = [
    ("fact_trips", "date_key", "dim_date", "date_key"),
    ("fact_trips", "pu_zone_id", "dim_zone", "zone_id"),
    ("fact_trips", "payment_id", "dim_payment", "payment_id"),
    ("fact_trips", "rate_id", "dim_rate", "rate_id"),
]

MEASURES = [
    ("行程量", "COUNTROWS ( 'fact_trips' )", "#,0"),
    ("GMV", "SUM ( 'fact_trips'[total_amount] )", ""),
    ("客单价", "DIVIDE ( [GMV], [行程量] )", ""),
    ("平均里程", "AVERAGE ( 'fact_trips'[distance_mi] )", ""),
    ("平均时长_分钟", "AVERAGE ( 'fact_trips'[duration_min] )", ""),
    ("平均速度_mph", "AVERAGE ( 'fact_trips'[speed_mph] )", ""),
    ("单均小费", "AVERAGE ( 'fact_trips'[tip_amount] )", ""),
    ("机场单占比", "DIVIDE ( CALCULATE ( [行程量], 'fact_trips'[is_airport] = 1 ), [行程量] )", "0.00%"),
    ("短途占比", 'DIVIDE ( CALCULATE ( [行程量], \'fact_trips\'[distance_band] = "1_short" ), [行程量] )', "0.00%"),
    ("小费覆盖率", "DIVIDE ( CALCULATE ( [行程量], 'fact_trips'[tip_amount] > 0 ), [行程量] )", "0.00%"),
    ("信用卡占比", 'DIVIDE ( CALCULATE ( [行程量], \'dim_payment\'[payment_name] = "信用卡" ), [行程量] )', "0.00%"),
    ("机场单客单价", "CALCULATE ( [客单价], 'fact_trips'[is_airport] = 1 )", ""),
    ("市区单客单价", "CALCULATE ( [客单价], 'fact_trips'[is_airport] = 0 )", ""),
    # 基线必须是「同星期几的日均值」，不是总和。
    # 原写法 CALCULATE([行程量], REMOVEFILTERS(date_key), VALUES(dow)) 会把所有周二加总，
    # 基线变成 128 万而不是 9.8 万，偏离度会算出 -94.7%（实际 -31.9%）。
    ("同星期基线",
     "CALCULATE ( MEDIANX ( VALUES ( 'dim_date'[date_key] ), [行程量] ), "
     "REMOVEFILTERS ( 'dim_date'[date_key] ), VALUES ( 'dim_date'[dow] ) )", "#,0"),
    # DAX 的 VAR 必须各自换行，挤在一行会报「标记无效」
    ("偏离度",
     "VAR cur = [行程量]\nVAR base = [同星期基线]\nRETURN DIVIDE ( cur - base, base )", "0.0%"),
    ("缺口单量", "[行程量] - [同星期基线]", "#,0"),
    # 和 Python 侧口径统一：用「基准营收 − 实际营收」，
    # 不用「缺口单量 × 当日客单价」——暴雪日客单价本身也降了，那样会低估缺口。
    ("缺口营收",
     "VAR 实际 = [GMV]\n"
     "VAR 基准 = CALCULATE ( MEDIANX ( VALUES ( 'dim_date'[date_key] ), [GMV] ), "
     "REMOVEFILTERS ( 'dim_date'[date_key] ), VALUES ( 'dim_date'[dow] ) )\n"
     "RETURN 实际 - 基准", ""),
    ("速度偏离",
     "VAR cur = [平均速度_mph]\n"
     "VAR base = CALCULATE ( AVERAGEX ( VALUES ( 'dim_date'[date_key] ), [平均速度_mph] ), "
     "REMOVEFILTERS ( 'dim_date'[date_key] ), VALUES ( 'dim_date'[dow] ) )\n"
     "RETURN DIVIDE ( cur - base, base )", "0.0%"),
    ("上月行程量", "CALCULATE ( [行程量], DATEADD ( 'dim_date'[date_key], -1, MONTH ) )", "#,0"),
    ("行程量环比",
     "VAR prev = [上月行程量]\nRETURN DIVIDE ( [行程量] - prev, prev )", "0.0%"),
    ("QTD 行程量", "TOTALQTD ( [行程量], 'dim_date'[date_key] )", "#,0"),
    ("近7日日均",
     "AVERAGEX ( DATESINPERIOD ( 'dim_date'[date_key], MAX ( 'dim_date'[date_key] ), -7, DAY ), "
     "[行程量] )", "#,0"),
    ("近7日波动率",
     "VAR m = [近7日日均]\n"
     "VAR s = STDEVX.P ( DATESINPERIOD ( 'dim_date'[date_key], MAX ( 'dim_date'[date_key] ), -7, DAY ), "
     "[行程量] )\nRETURN DIVIDE ( s, m )", "0.00%"),
    ("区域占比",
     "DIVIDE ( [行程量], CALCULATE ( [行程量], REMOVEFILTERS ( 'dim_zone'[borough] ) ) )", "0.0%"),
    ("区域排名", "RANKX ( ALL ( 'dim_zone'[borough] ), [行程量], , DESC, DENSE )", "#,0"),
    ("区域跌幅排名", "RANKX ( ALL ( 'dim_zone'[borough] ), [偏离度], , ASC, DENSE )", "#,0"),
    ("累计贡献",
     "VAR cur = [行程量]\n"
     "VAR cum = CALCULATE ( [行程量], FILTER ( ALL ( 'dim_zone'[zone_name] ), [行程量] >= cur ) )\n"
     "RETURN DIVIDE ( cum, CALCULATE ( [行程量], ALL ( 'dim_zone'[zone_name] ) ) )", "0.0%"),
]


def stable_id(key):
    """确定性 UUID：同样的 key 永远得到同样的 id（保证生成可复现）"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"taxiops/{key}"))


def get_schema(name, fname, is_parquet):
    """读真实列名和类型。CSV 也必须推断，否则 date_key 会是 text 配不上 datetime。"""
    path = os.path.join(EXPORTS, fname).replace("\\", "/")
    con = duckdb.connect()
    if is_parquet:
        rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{path}')").fetchall()
    else:
        rows = con.execute(
            f"DESCRIBE SELECT * FROM read_csv('{path}', header=true, sample_size=-1)").fetchall()
    con.close()
    return [(r[0], r[1]) for r in rows]


def m_expression(fname, is_parquet, cols):
    """生成 Power Query M 文本（TMSL 的 expression 是一个字符串）"""
    if is_parquet:
        load = f'Parquet.Document(File.Contents(DataDir & "{fname}"))'
        prev = "Source"
    else:
        load = (f'Csv.Document(File.Contents(DataDir & "{fname}"), '
                f'[Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv])')
        prev = "Headers"

    types = []
    for cname, ctype in cols:
        types.append(f'{{"{cname}", {M_TYPE.get(TYPE_MAP.get(ctype.upper(), "string"))}}}')

    lines = ["let"]
    lines.append(f'    Source = {load},')
    if not is_parquet:
        lines.append('    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),')
    lines.append(f'    Typed = Table.TransformColumnTypes({prev}, {{')
    lines.append("        " + ",\n        ".join(types))
    lines.append("    })")
    lines.append("in")
    lines.append("    Typed")
    return "\n".join(lines)


def build_model_bim(schemas):
    """TMSL 模型定义"""
    tables = []
    for name, fname, is_parquet in TABLES:
        cols = []
        for cname, ctype in schemas[name]:
            dt = TYPE_MAP.get(ctype.upper(), "string")
            col = {"name": cname, "dataType": dt, "sourceColumn": cname}
            # summarizeBy 只对数值列设；字符串列设了非法，会让分类轴图表空白
            if dt != "string":
                col["summarizeBy"] = "none"
            cols.append(col)
        tbl = {
            "name": name,
            "columns": cols,
            "partitions": [{
                "name": name,
                "mode": "import",
                "source": {"type": "m", "expression": m_expression(fname, is_parquet, schemas[name])},
            }],
        }
        if name == "fact_trips":
            ms = []
            for mname, expr, fmt in MEASURES:
                m = {"name": mname, "expression": expr}
                if fmt:
                    m["formatString"] = fmt
                ms.append(m)
            tbl["measures"] = ms
        tables.append(tbl)

    rels = [{
        "name": f"{ft}_{fc}_{dt}_{dc}",
        "fromTable": ft, "fromColumn": fc,
        "toTable": dt, "toColumn": dc,
    } for ft, fc, dt, dc in RELATIONSHIPS]

    return {
        "name": PROJ,
        "compatibilityLevel": 1604,
        "model": {
            "culture": "zh-CN",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "tables": tables,
            "relationships": rels,
            "expressions": [{
                "name": "DataDir",
                "kind": "m",
                # 换电脑后必须改这个参数，否则所有图表空白
                "expression": f'"{DATA_DIR}" meta [IsParameterQuery=true, Type="Text", '
                              f'IsParameterQueryRequired=true, '
                              f'Description="数据文件目录。换电脑后改成新路径，否则图表全空。"]',
            }],
        },
    }


def main():
    print("=" * 64)
    print("生成 Power BI 项目（PBIP / TMSL）")
    print("=" * 64)

    schemas = {name: get_schema(name, fname, pq) for name, fname, pq in TABLES}
    for name in schemas:
        print(f"  {name:<12} {len(schemas[name]):>3} 列")

    sm = os.path.join(OUT, f"{PROJ}.SemanticModel")
    rp = os.path.join(OUT, f"{PROJ}.Report")
    os.makedirs(sm, exist_ok=True)
    os.makedirs(f"{rp}/definition/pages/page1", exist_ok=True)

    # 清掉旧版 TMDL 结构（会和 model.bim 冲突）
    old = os.path.join(sm, "definition")
    if os.path.isdir(old):
        for base, _, files in os.walk(old, topdown=False):
            for f in files:
                os.remove(os.path.join(base, f))
            os.rmdir(base)
        print("  已清理旧版 TMDL 目录")

    def w(path, text):
        open(path, "w", encoding="utf-8", newline="\n").write(text)

    # ---------- .pbip ----------
    w(os.path.join(OUT, f"{PROJ}.pbip"), json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{PROJ}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    }, ensure_ascii=False, indent=2) + "\n")

    # ---------- SemanticModel ----------
    w(f"{sm}/.platform", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": PROJ},
        "config": {"version": "2.0", "logicalId": stable_id("semanticmodel")},
    }, ensure_ascii=False, indent=2) + "\n")

    w(f"{sm}/definition.pbism", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "1.0",
        "settings": {},
    }, ensure_ascii=False, indent=2) + "\n")

    model = build_model_bim(schemas)
    w(f"{sm}/model.bim", json.dumps(model, ensure_ascii=False, indent=2) + "\n")

    # ---------- Report ----------
    # 结构照 Power BI 2.159 实际保存的来：
    #   definition.pbir           只有 version 4.0 + datasetReference（没有 $schema）
    #   definition/version.json   versionMetadata 1.0.0
    #   definition/report.json    report/3.3.0 + themeCollection
    #   definition/pages/pages.json                pagesMetadata 1.1.0
    #   definition/pages/<pid>/page.json           page/2.1.0（不含 visuals）
    #   definition/pages/<pid>/visuals/<vid>/visual.json   visualContainer/2.14.0
    # 主题文件由 Power BI 生成，这里不覆盖
    def pid(key):
        return uuid.uuid5(uuid.NAMESPACE_URL, f"taxiops/page/{key}").hex[:20]

    def vid(key):
        return uuid.uuid5(uuid.NAMESPACE_URL, f"taxiops/visual/{key}").hex[:20]

    def pm(entity, prop):
        return {"field": {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
                "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}

    def pc(entity, prop):
        return {"field": {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}},
                "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}

    # 每个图表的标题（不是字段名，而是人话，说明这个图在看什么）
    TITLES = {
        "p1_trips": "总行程量", "p1_gmv": "总营收", "p1_aov": "客单价", "p1_spd": "平均速度 (mph)", "p1_air": "机场单占比",
        "p1_zone": "各行政区行程量", "p1_trend": "日行程量 vs 同星期基线",
        "p1_band": "距离分层结构", "p1_pay": "支付方式分布",
        "p2_dev": "当日偏离度", "p2_gap": "缺口单量", "p2_rev": "缺口营收", "p2_spd": "速度偏离", "p2_vol": "近 7 日波动率",
        "p2_daily": "日行程量趋势", "p2_speed": "日平均速度趋势",
        "p2_scatter": "量 vs 速度：异动落在哪个象限",
        "p3_hour": "分时段行程量分布", "p3_band": "各距离段偏离度",
        "p3_zone": "各行政区偏离度", "p3_air": "机场单 vs 市区单客单价",
        "p4_zone": "各行政区缺口单量", "p4_band": "各距离段单均时长",
        "p4_share": "各距离段平均速度",
        "p4_speed": "各行政区平均速度（路况）", "p4_trips": "分时段需求量（运力投放依据）",
    }

    def expr(val):
        """Power BI 的 Literal 值：文本要带单引号，布尔用 'true'/'false'"""
        return {"expr": {"Literal": {"Value": val}}}

    def vis(key, vtype, x, y, wd, ht, state, tab=0):
        v = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.14.0/schema.json",
            "name": vid(key),
            "position": {"x": x, "y": y, "z": 0, "height": ht, "width": wd, "tabOrder": tab},
            "visual": {"visualType": vtype, "query": {"queryState": state},
                       "drillFilterOtherVisuals": True},
        }
        # 标题：左对齐、加粗，深灰不抢眼
        title = TITLES.get(key)
        if title:
            v["visual"]["visualContainerObjects"] = {
                "title": [{"properties": {
                    "show": expr("true"),
                    "text": expr(f"'{title}'"),
                    "bold": expr("true"),
                    "alignment": expr("'left'"),
                    "fontSize": expr("12D"),
                }}]
            }
        # 柱形/条形/折线：开数据标签 + 统一主色
        if vtype in ("clusteredColumnChart", "clusteredBarChart", "lineChart"):
            v["visual"]["objects"] = {
                "labels": [{"properties": {"show": expr("true")}}],
                "dataPoint": [{"properties": {
                    "fill": {"solid": {"color": {"expr": {"Literal": {"Value": "'#2C6E9B'"}}}}},
                }}],
            }
        return v

    # 报表页定义：page_key -> (显示名, [visual...])
    PAGES = [
        ("p1", "运营总览", [
            # 5 个 KPI 均分整行宽度，避免右侧留大片空白
            vis("p1_trips", "card", 20, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "行程量")]}}),
            vis("p1_gmv", "card", 392, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "GMV")]}}),
            vis("p1_aov", "card", 764, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "客单价")]}}),
            vis("p1_spd", "card", 1136, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "平均速度_mph")]}}),
            vis("p1_air", "card", 1508, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "机场单占比")]}}),
            vis("p1_zone", "clusteredBarChart", 20, 150, 420, 400,
                {"Category": {"projections": [pc("dim_zone", "borough")]},
                 "Y": {"projections": [pm("fact_trips", "行程量")]}}, 1),
            vis("p1_trend", "lineChart", 460, 150, 860, 400,
                {"Category": {"projections": [pc("dim_date", "date_key")]},
                 "Y": {"projections": [pm("fact_trips", "行程量"), pm("fact_trips", "同星期基线")]}}, 2),
            vis("p1_band", "clusteredColumnChart", 20, 570, 620, 400,
                {"Category": {"projections": [pc("fact_trips", "distance_band")]},
                 "Y": {"projections": [pm("fact_trips", "行程量")]}}, 3),
            vis("p1_pay", "clusteredColumnChart", 660, 570, 660, 400,
                {"Category": {"projections": [pc("dim_payment", "payment_name")]},
                 "Y": {"projections": [pm("fact_trips", "行程量")]}}, 4),
        ]),
        ("p2", "异动监控", [
            # 5 个 KPI 均分整行（原来 4 个只占到 x=880，右边 1000px 是空的）
            vis("p2_dev", "card", 20, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "偏离度")]}}),
            vis("p2_gap", "card", 392, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "缺口单量")]}}),
            vis("p2_rev", "card", 764, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "缺口营收")]}}),
            vis("p2_spd", "card", 1136, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "速度偏离")]}}),
            vis("p2_vol", "card", 1508, 20, 352, 110, {"Values": {"projections": [pm("fact_trips", "近7日波动率")]}}),
            vis("p2_daily", "lineChart", 20, 150, 1880, 300,
                {"Category": {"projections": [pc("dim_date", "date_key")]},
                 "Y": {"projections": [pm("fact_trips", "行程量")]}}, 1),
            vis("p2_speed", "lineChart", 20, 470, 900, 280,
                {"Category": {"projections": [pc("dim_date", "date_key")]},
                 "Y": {"projections": [pm("fact_trips", "平均速度_mph")]}}, 2),
            # 量 vs 速度散点：异动日落在这张图的哪个象限，直接决定是需求问题还是供给问题
            vis("p2_scatter", "scatterChart", 940, 470, 960, 280,
                {"X": {"projections": [pm("fact_trips", "平均速度_mph")]},
                 "Y": {"projections": [pm("fact_trips", "行程量")]},
                 "Category": {"projections": [pc("dim_date", "date_key")]}}, 3),
        ]),
        ("p3", "异动下钻", [
            vis("p3_hour", "clusteredColumnChart", 20, 20, 900, 440,
                {"Category": {"projections": [pc("fact_trips", "hour_of_day")]},
                 "Y": {"projections": [pm("fact_trips", "行程量")]}}, 0),
            vis("p3_band", "clusteredColumnChart", 940, 20, 780, 440,
                {"Category": {"projections": [pc("fact_trips", "distance_band")]},
                 "Y": {"projections": [pm("fact_trips", "偏离度")]}}, 1),
            vis("p3_zone", "clusteredBarChart", 20, 480, 900, 460,
                {"Category": {"projections": [pc("dim_zone", "borough")]},
                 "Y": {"projections": [pm("fact_trips", "偏离度")]}}, 2),
            vis("p3_air", "clusteredColumnChart", 940, 480, 780, 460,
                {"Category": {"projections": [pc("fact_trips", "is_airport")]},
                 "Y": {"projections": [pm("fact_trips", "客单价")]}}, 3),
        ]),
        ("p4", "策略看板", [
            vis("p4_zone", "clusteredBarChart", 20, 20, 900, 620,
                {"Category": {"projections": [pc("dim_zone", "borough")]},
                 "Y": {"projections": [pm("fact_trips", "缺口单量")]}}, 0),
            vis("p4_band", "clusteredColumnChart", 940, 20, 780, 300,
                {"Category": {"projections": [pc("fact_trips", "distance_band")]},
                 "Y": {"projections": [pm("fact_trips", "平均时长_分钟")]}}, 1),
            # 原来这里放「短途占比」，但该度量值是 短途单量/总单量，
            # 按 distance_band 分组后只有短途那组有值、其余为 0，图无意义。
            # 改成各距离段的平均速度，和「短途周转快」的结论呼应。
            vis("p4_share", "clusteredColumnChart", 940, 340, 780, 300,
                {"Category": {"projections": [pc("fact_trips", "distance_band")]},
                 "Y": {"projections": [pm("fact_trips", "平均速度_mph")]}}, 2),
            # 补两个：运力该往哪投、投多少
            vis("p4_speed", "clusteredBarChart", 20, 660, 900, 380,
                {"Category": {"projections": [pc("dim_zone", "borough")]},
                 "Y": {"projections": [pm("fact_trips", "平均速度_mph")]}}, 3),
            vis("p4_trips", "clusteredColumnChart", 940, 660, 780, 380,
                {"Category": {"projections": [pc("fact_trips", "hour_of_day")]},
                 "Y": {"projections": [pm("fact_trips", "行程量")]}}, 4),
        ]),
    ]

    w(f"{rp}/.platform", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": PROJ},
        "config": {"version": "2.0", "logicalId": stable_id("report")},
    }, ensure_ascii=False, indent=2) + "\n")

    w(f"{rp}/definition.pbir", json.dumps({
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{PROJ}.SemanticModel"}},
    }, ensure_ascii=False, indent=2) + "\n")

    w(f"{rp}/definition/version.json", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/versionMetadata/1.0.0/schema.json",
        "version": "2.0.0",
    }, ensure_ascii=False, indent=2) + "\n")

    # 清掉 pages 下所有旧页面目录（Power BI 自己生成的和以前版本的），
    # 只保留随后重新生成的，避免多出莫名其妙的空白页
    pages_root = f"{rp}/definition/pages"
    if os.path.isdir(pages_root):
        for d in os.listdir(pages_root):
            full = os.path.join(pages_root, d)
            if os.path.isdir(full):
                for base, _, fs in os.walk(full, topdown=False):
                    for f in fs:
                        os.remove(os.path.join(base, f))
                    os.rmdir(base)

    w(f"{rp}/definition/pages/pages.json", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json",
        "pageOrder": [pid(k) for k, _, _ in PAGES],
        "activePageName": pid(PAGES[0][0]),
    }, ensure_ascii=False, indent=2) + "\n")

    for pkey, pname, visuals in PAGES:
        pdir = f"{rp}/definition/pages/{pid(pkey)}"
        os.makedirs(pdir, exist_ok=True)
        w(f"{pdir}/page.json", json.dumps({
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json",
            "name": pid(pkey),
            "displayName": pname,
            "displayOption": "FitToPage",
            "height": 1080,
            "width": 1920,
        }, ensure_ascii=False, indent=2) + "\n")
        for i, v in enumerate(visuals):
            v["position"]["tabOrder"] = i          # 按页面内顺序编号，避免重复
            vdir = f"{pdir}/visuals/{v['name']}"
            os.makedirs(vdir, exist_ok=True)
            w(f"{vdir}/visual.json", json.dumps(v, ensure_ascii=False, indent=2) + "\n")

    # report.json 沿用 Power BI 生成的主题配置（主题文件不覆盖）
    theme = "Fluent2-CY26SU10"
    theme_path = f"{rp}/StaticResources/SharedResources/BaseThemes/{theme}.json"
    report_json = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/3.3.0/schema.json",
        "settings": {
            "useStylableVisualContainerHeader": True,
            "defaultDrillFilterOtherVisuals": True,
            "useEnhancedTooltips": True,
        },
    }
    if os.path.exists(theme_path):
        report_json["themeCollection"] = {
            "baseTheme": {
                "name": theme,
                "reportVersionAtImport": {"visual": "2.14.0", "report": "3.4.0", "page": "2.3.1"},
                "type": "SharedResources",
            }
        }
        report_json["resourcePackages"] = [{
            "name": "SharedResources", "type": "SharedResources",
            "items": [{"name": theme, "path": f"BaseThemes/{theme}.json", "type": "BaseTheme"}],
        }]
    w(f"{rp}/definition/report.json", json.dumps(report_json, ensure_ascii=False, indent=2) + "\n")

    print(f"  报表：{len(PAGES)} 页，{sum(len(v) for _, _, v in PAGES)} 个图表")

    # ---------- 汇总 ----------
    total = 0
    files = 0
    for base, _, fs in os.walk(os.path.join(OUT, PROJ + ".SemanticModel")):
        for f in fs:
            total += os.path.getsize(os.path.join(base, f))
            files += 1
    for base, _, fs in os.walk(os.path.join(OUT, PROJ + ".Report")):
        for f in fs:
            total += os.path.getsize(os.path.join(base, f))
            files += 1
    total += os.path.getsize(os.path.join(OUT, PROJ + ".pbip"))
    files += 1

    print(f"\n  生成 {files} 个文件，{total/1024:.0f} KB")
    print(f"  双击打开：{os.path.join(OUT, PROJ + '.pbip')}")
    print("\n  打开后检查：")
    print("    - 左侧「数据」面板 5 张表")
    print("    - fact_trips 下有 28 个带 fx 的度量值")
    print("    - 「模型」视图 4 条关系连线")
    print("\n  打不开就删掉 TaxiOps.pbip 和两个目录，走 powerbi/START_HERE.md 手动搭。")


if __name__ == "__main__":
    main()
