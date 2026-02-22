import sqlite3
import pandas as pd

# Connect to DB using the absolute path
db_path = r"C:\rajat\Algo_System\data\algo_trading.db"
conn = sqlite3.connect(db_path)

# 1. Get Date Range of Options
opt_range = conn.execute("""
    SELECT MIN(timestamp), MAX(timestamp) 
    FROM options_ohlc
""").fetchone()

# 2. Get Date Range of Spot (Market Data)
spot_range = conn.execute("""
    SELECT MIN(timestamp), MAX(timestamp) 
    FROM market_data 
    WHERE symbol = 'NIFTY_INDEX'
""").fetchone()

print(f"Options Data Range: {opt_range[0]} to {opt_range[1]}")
print(f"Spot Data Range:    {spot_range[0]} to {spot_range[1]}")

# 3. Check for Spot Gaps on Options Trading Days
query = """
    SELECT DISTINCT DATE(o.timestamp) as missing_date
    FROM options_ohlc o
    LEFT JOIN market_data m ON o.timestamp = m.timestamp AND m.symbol = 'NIFTY_INDEX'
    WHERE m.timestamp IS NULL
    ORDER BY missing_date
"""
missing = pd.read_sql(query, conn)
if not missing.empty:
    print(f"\n[WARNING] Missing Spot data for {len(missing)} timestamps where Options exist!")
    print(missing.head())
else:
    print("\n[SUCCESS] Perfect alignment! Every Option candle has a matching Spot candle.")

conn.close()