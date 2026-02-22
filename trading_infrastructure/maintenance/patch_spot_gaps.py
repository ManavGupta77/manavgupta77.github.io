import sqlite3
import pandas as pd
import time
import os
import json
from dotenv import load_dotenv
from breeze_connect import BreezeConnect

# 1. Setup Paths
project_root = r"C:\rajat\Algo_System"
db_path = os.path.join(project_root, "data", "algo_trading.db")
env_path = os.path.join(project_root, "config", ".env")
session_path = os.path.join(project_root, "data", "breeze_session.json")

# 2. Load Credentials
load_dotenv(env_path)
api_key = os.getenv("BREEZE_API_KEY")
secret_key = os.getenv("BREEZE_SECRET_KEY")

with open(session_path, "r") as f:
    session_token = json.load(f).get("session_token")

# 3. Initialize Breeze API
breeze = BreezeConnect(api_key=api_key)
breeze.generate_session(api_secret=secret_key, session_token=session_token)

# 4. Connect to Database & Find Macro Gaps
conn = sqlite3.connect(db_path)

# Query specifically for days missing more than 10 candles
query = """
    SELECT DATE(o.timestamp) as missing_date
    FROM options_ohlc o
    LEFT JOIN market_data m ON o.timestamp = m.timestamp AND m.symbol = 'NIFTY_INDEX'
    GROUP BY missing_date
    HAVING (COUNT(DISTINCT o.timestamp) - COUNT(DISTINCT m.timestamp)) > 10
"""
missing_dates_df = pd.read_sql(query, conn)
dates_to_fetch = missing_dates_df['missing_date'].tolist()

print(f"Found {len(dates_to_fetch)} days requiring spot data completion. Fetching remaining candles...")

# 5. Fetch and Insert Loop
total_inserted = 0

for target_date in dates_to_fetch:
    print(f"Completing NIFTY Spot for {target_date}...")
    
    try:
        # THE FIX: Using IST hours for the Cash API endpoint
        from_date = f"{target_date}T09:15:00.000Z"
        to_date = f"{target_date}T15:30:00.000Z"
        
        res = breeze.get_historical_data_v2(
            interval="1minute",
            from_date=from_date,
            to_date=to_date,
            stock_code="NIFTY",
            exchange_code="NSE",
            product_type="cash"
        )
        
        if res.get('Status') == 200 and 'Success' in res:
            df = pd.DataFrame(res['Success'])
            
            if not df.empty:
                df['timestamp'] = df['datetime'].str.replace(' ', 'T') + '+05:30'
                
                insert_data = []
                for _, row in df.iterrows():
                    insert_data.append((
                        row['timestamp'],
                        'NIFTY_INDEX',
                        float(row['open']),
                        float(row['high']),
                        float(row['low']),
                        float(row['close']),
                        int(row.get('volume', 0)),
                        0
                    ))
                
                cursor = conn.cursor()
                # INSERT OR IGNORE will naturally skip the 46 candles we already have
                # and only insert the missing 329 candles.
                cursor.executemany("""
                    INSERT OR IGNORE INTO market_data 
                    (timestamp, symbol, open, high, low, close, volume, oi) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, insert_data)
                
                conn.commit()
                rows_added = cursor.rowcount
                total_inserted += rows_added
                print(f"  -> Success: Added {rows_added} missing candles.")
        else:
            print(f"  -> Error from API: {res.get('Error', 'Unknown error')}")
            
    except Exception as e:
        print(f"  -> Exception occurred: {e}")
        
    time.sleep(1)

print(f"\n[COMPLETE] Backfill finished. Total new spot candles inserted: {total_inserted}")
conn.close()