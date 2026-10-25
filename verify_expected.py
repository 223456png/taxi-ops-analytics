"""Print the values the PBIP cards should show, computed from the exported fact table."""
import os
import sys

import duckdb

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = ROOT if os.path.exists(os.path.join(ROOT, "powerbi", "exports", "fact_trips.parquet")) \
    else os.path.join(ROOT, "TaxiOps")
PQ = os.path.join(REPO, "powerbi", "exports", "fact_trips.parquet")
ZONE = os.path.join(REPO, "data", "taxi_zone_lookup.csv")

if not os.path.exists(PQ):
    print("fact_trips.parquet not found. Run run_taxiops.cmd first.")
    sys.exit(1)

con = duckdb.connect()
pq = PQ.replace("\\", "/")
zone = ZONE.replace("\\", "/")

row = con.execute(f"""
WITH f AS (SELECT * FROM read_parquet('{pq}')),
     d AS (
       SELECT date_key, COUNT(*) n, SUM(total_amount) rev, AVG(speed_mph) spd
       FROM f WHERE dayofweek(date_key) = 2 GROUP BY 1
     )
SELECT
  MAX(CASE WHEN date_key = DATE '2024-02-13' THEN n END) AS storm,
  MEDIAN(n) AS base,
  MAX(CASE WHEN date_key = DATE '2024-02-13' THEN rev END) AS storm_rev,
  MEDIAN(rev) AS base_rev,
  MAX(CASE WHEN date_key = DATE '2024-02-13' THEN spd END) AS storm_spd,
  MEDIAN(spd) AS base_spd
FROM d
""").fetchone()
storm, base, storm_rev, base_rev, storm_spd, base_spd = row
dev = storm / base - 1
gap = storm - base
rev_gap = storm_rev - base_rev
spd_dev = storm_spd / base_spd - 1

manh = con.execute(f"""
WITH f AS (SELECT * FROM read_parquet('{pq}')),
     z AS (SELECT * FROM read_csv_auto('{zone}')),
     fz AS (
       SELECT f.*, z.Borough AS borough
       FROM f LEFT JOIN z ON f.pu_zone_id = CAST(z."LocationID" AS INTEGER)
     ),
     d AS (
       SELECT date_key, borough, COUNT(*) n
       FROM fz WHERE dayofweek(date_key) = 2 GROUP BY 1, 2
     )
SELECT
  MEDIAN(n) FILTER (WHERE borough = 'Manhattan') AS base,
  MAX(n) FILTER (WHERE date_key = DATE '2024-02-13' AND borough = 'Manhattan') AS storm
FROM d
""").fetchone()
m_base, m_storm = manh
m_raw = m_storm - m_base
m_excess = m_storm - m_base * (1 + dev)

print("Expected PBIP values (2024-02-13, Tuesday baseline):")
print(f"  2-13 trips                 : {storm:,.0f}")
print(f"  2-13 Tuesday baseline      : {base:,.0f}")
print(f"  2-13 deviation             : {dev:.2%}   (PBIP shows ~ -31.9%)")
print(f"  2-13 raw gap               : {gap:,.0f}")
print(f"  2-13 revenue gap           : {rev_gap:,.0f}")
print(f"  2-13 speed deviation       : {spd_dev:+.2%}")
print(f"  Manhattan raw gap          : {m_raw:,.0f}")
print(f"  Manhattan excess gap       : {m_excess:,.1f}   (PBIP shows ~ -563)")
print()
print("If Power BI shows different numbers:")
print("  +9045% or 8,200,565 -> old PBIP / old measures; re-run run_taxiops.cmd")
print("  blank                -> DataDir not set or data not refreshed")
print("  -28.4% / -30.8%      -> trailing baseline, not the latest PBIP")
print("  -30.7%               -> same-month baseline, not the latest PBIP")
