import sqlite3
import pandas as pd

db_path = r"C:\rajat\Algo_System\data\algo_trading.db"
conn = sqlite3.connect(db_path)

# Query to count candles per day for the 22 problematic dates
query = """
    SELECT 
        DATE(o.timestamp) as trading_date,
        COUNT(DISTINCT o.timestamp) as options_minutes,
        COUNT(DISTINCT m.timestamp) as spot_minutes,
        COUNT(DISTINCT o.timestamp) - COUNT(DISTINCT m.timestamp) as missing_spot_minutes
    FROM options_ohlc o
    LEFT JOIN market_data m ON o.timestamp = m.timestamp AND m.symbol = 'NIFTY_INDEX'
    WHERE DATE(o.timestamp) IN (
        SELECT DISTINCT DATE(o.timestamp)
        FROM options_ohlc o
        LEFT JOIN market_data m ON o.timestamp = m.timestamp AND m.symbol = 'NIFTY_INDEX'
        WHERE m.timestamp IS NULL
    )
    GROUP BY trading_date
    ORDER BY trading_date
"""

df = pd.read_sql(query, conn)
print(df.to_string(index=False))

conn.close()