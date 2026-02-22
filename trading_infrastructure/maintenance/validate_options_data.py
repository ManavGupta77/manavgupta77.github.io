# ==========================================
# DATA/VALIDATE_OPTIONS_DATA.PY
# ==========================================
# Quick health check on options_ohlc table
# Usage: python data/validate_options_data.py
# ==========================================

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_records.db_connector import db

db.connect()

print("=" * 70)
print("  OPTIONS DATA VALIDATION REPORT")
print("=" * 70)

# 1. Overall stats
stats = db.query_one("SELECT COUNT(*) as cnt, COUNT(DISTINCT expiry) as expiries FROM options_ohlc")
print(f"\n  Total rows:     {stats['cnt']:,}")
print(f"  Total expiries: {stats['expiries']}")

# 2. Per-expiry breakdown
print(f"\n  {'Expiry':<14} {'Rows':>8} {'Strikes':>8} {'Days':>6} {'Avg/Day':>8}  Status")
print(f"  {'-'*14} {'-'*8} {'-'*8} {'-'*6} {'-'*8}  {'-'*10}")

expiry_data = db.query("""
    SELECT expiry,
           COUNT(*) as rows,
           COUNT(DISTINCT strike) as strikes,
           COUNT(DISTINCT DATE(timestamp)) as days,
           ROUND(COUNT(*) * 1.0 / MAX(1, COUNT(DISTINCT DATE(timestamp))), 0) as avg_per_day
    FROM options_ohlc
    WHERE instrument = 'NIFTY'
    GROUP BY expiry
    ORDER BY expiry
""")

for e in expiry_data:
    status = "✅" if e['days'] >= 4 else "⚠️ LOW" if e['days'] >= 1 else "❌ EMPTY"
    print(f"  {e['expiry']:<14} {e['rows']:>8,} {e['strikes']:>8} {e['days']:>6} {int(e['avg_per_day']):>8}  {status}")

# 3. Missing expiries check (Tuesdays between Aug 2025 and Nov 2025)
print(f"\n  --- MISSING EXPIRY CHECK (Aug-Nov 2025) ---")
from datetime import date, timedelta
expected_expiries = []
d = date(2025, 8, 1)
while d <= date(2025, 11, 15):
    # With logic that uses Thursday for Aug and Tuesday for Sep+:
    if d < date(2025, 9, 1):
        if d.weekday() == 3:  # Thursday
            expected_expiries.append(d.strftime("%Y-%m-%d"))
    else:
        if d.weekday() == 1:  # Tuesday
            expected_expiries.append(d.strftime("%Y-%m-%d"))
    d += timedelta(days=1)

found_expiries = set(e['expiry'][:10] for e in expiry_data)
missing = [e for e in expected_expiries if e not in found_expiries]
if missing:
    print(f"  ⚠️ Missing expiries: {missing}")
else:
    print(f"  ✅ All expected Tuesdays present")

# 4. Data source breakdown
print(f"\n  --- DATA SOURCE ---")
sources = db.query("""
    SELECT
        CASE WHEN instrument_key LIKE 'BREEZE%' THEN 'Breeze'
             WHEN instrument_key LIKE 'NSE_FO%' THEN 'Upstox'
             ELSE 'Other' END as source,
        COUNT(*) as rows
    FROM options_ohlc
    GROUP BY source
""")
for s in sources:
    print(f"  {s['source']:<10} {s['rows']:>10,} rows")

# 5. Strike distribution
print(f"\n  --- STRIKE RANGE PER EXPIRY ---")
strike_range = db.query("""
    SELECT expiry,
           MIN(strike) as min_strike,
           MAX(strike) as max_strike,
           COUNT(DISTINCT strike) as unique_strikes
    FROM options_ohlc
    WHERE instrument = 'NIFTY'
    GROUP BY expiry
    ORDER BY expiry
""")
for s in strike_range:
    print(f"  {s['expiry'][:10]}  {int(s['min_strike']):>6} - {int(s['max_strike']):>6}  ({s['unique_strikes']} strikes)")

# 6. Sample data quality check (check for zero prices)
print(f"\n  --- DATA QUALITY ---")
zeros = db.query_one("""
    SELECT COUNT(*) as cnt FROM options_ohlc
    WHERE close = 0 OR close IS NULL
""")
print(f"  Rows with zero/null close: {zeros['cnt']}")

low_vol = db.query_one("""
    SELECT COUNT(*) as cnt FROM options_ohlc WHERE volume = 0
""")
total = db.query_one("SELECT COUNT(*) as cnt FROM options_ohlc")
pct = (low_vol['cnt'] / max(1, total['cnt'])) * 100
print(f"  Rows with zero volume:     {low_vol['cnt']:,} ({pct:.1f}%)")

# 7. Time coverage per day sample
print(f"\n  --- TIME COVERAGE SAMPLE (latest 3 trading days) ---")
recent_days = db.query("""
    SELECT DATE(timestamp) as day,
           MIN(TIME(timestamp)) as first_candle,
           MAX(TIME(timestamp)) as last_candle,
           COUNT(DISTINCT instrument_key) as contracts,
           COUNT(*) as candles
    FROM options_ohlc
    WHERE instrument = 'NIFTY'
    GROUP BY DATE(timestamp)
    ORDER BY day DESC
    LIMIT 3
""")
for r in recent_days:
    print(f"  {r['day']}  {r['first_candle']} - {r['last_candle']}  {r['contracts']} contracts  {r['candles']:,} candles")

print(f"\n{'='*70}")
db.close()