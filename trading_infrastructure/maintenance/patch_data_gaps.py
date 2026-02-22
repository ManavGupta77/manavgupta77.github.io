import pandas as pd
import time
from datetime import datetime, timedelta

# 1. Import Infrastructure
from trading_records.db_connector import db
from broker_gateway.broker_upstox.connector import broker

# --- CONFIGURATION ---
SYMBOL_TAG = "NIFTY_INDEX"
INSTRUMENT_KEY = "NSE_INDEX|Nifty 50"

# The specific "Problem Periods" identified from your logs
GAPS_TO_PATCH = [
    ("2023-02-19", "2023-03-21"), # Gap 1 (2023)
    ("2024-02-26", "2024-03-27"), # Gap 2 (2024)
    ("2025-02-01", "2025-03-03")  # Gap 3 (2025)
]

def patch_data():
    print(f"🚑 Starting Surgical Patch for {SYMBOL_TAG}...")
    
    if not broker.is_connected():
        print("❌ Broker not connected. Please login first.")
        return

    db.connect()
    
    total_patched_candles = 0

    # Loop through each identified gap
    for start_str, end_str in GAPS_TO_PATCH:
        print(f"\n💉 Patching Gap: {start_str} -> {end_str}")
        
        start_date = datetime.strptime(start_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_str, "%Y-%m-%d")
        
        current_date = start_date
        gap_candles = []

        # Micro-Chunking: Day by Day
        while current_date <= end_date:
            day_str = current_date.strftime("%Y-%m-%d")
            
            # Print less noise, just dots for progress
            print(f".", end="", flush=True)
            
            try:
                # Fetch just ONE day
                candles = broker.get_historical_data(INSTRUMENT_KEY, day_str, day_str, interval="1minute")
                
                if candles:
                    gap_candles.extend(candles)
            
            except Exception as e:
                # If a specific day fails (holiday/error), we just skip it and keep going!
                # print(f"x", end="", flush=True) 
                pass

            current_date += timedelta(days=1)
            time.sleep(0.1) # Be gentle

        # Save the recovered data for this gap
        if gap_candles:
            print(f"\n   ✅ Recovered {len(gap_candles)} candles for this period.")
            save_to_db(gap_candles)
            total_patched_candles += len(gap_candles)
        else:
            print("\n   ⚠️ No data found even with micro-patching.")

    print(f"\n🏁 PATCH COMPLETE. Total Recovered: {total_patched_candles} candles.")
    db.close()

def save_to_db(candles):
    if not candles: return

    df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
    
    data_to_insert = []
    for _, row in df.iterrows():
        data_to_insert.append((
            row['timestamp'].strftime("%Y-%m-%d %H:%M:%S"),
            SYMBOL_TAG,
            row['open'], row['high'], row['low'], row['close'],
            row['volume'], 0
        ))

    conn = db.connection
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN TRANSACTION;")
        cursor.executemany("""
            INSERT OR IGNORE INTO market_data 
            (timestamp, symbol, open, high, low, close, volume, oi)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, data_to_insert)
        conn.commit()
    except Exception as e:
        print(f"   ❌ DB Error: {e}")
        conn.rollback()

if __name__ == "__main__":
    patch_data()