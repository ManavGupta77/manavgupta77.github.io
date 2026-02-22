import sqlite3
import pandas as pd

db_path = r"C:\rajat\Algo_System\data\algo_trading.db"
conn = sqlite3.connect(db_path)

print("Normalizing timestamps in market_data table...")
# Replace the space with a 'T' and append the '+05:30' IST offset
conn.execute("""
    UPDATE market_data 
    SET timestamp = REPLACE(timestamp, ' ', 'T') || '+05:30'
    WHERE timestamp NOT LIKE '%T%'
""")
conn.commit()

# Re-run the verification query
query = """
    SELECT DISTINCT DATE(o.timestamp) as missing_date
    FROM options_ohlc o
    LEFT JOIN market_data m ON o.timestamp = m.timestamp AND m.symbol = 'NIFTY_INDEX'
    WHERE m.timestamp IS NULL
    ORDER BY missing_date
"""
missing = pd.read_sql(query, conn)

if not missing.empty:
    print(f"\n[WARNING] Still missing Spot data for {len(missing)} timestamps.")
else:
    print("\n[SUCCESS] Formats normalized! Every Option candle now has a matching Spot candle.")

conn.close()