import pandas as pd
import sqlite3
import os
import sys
from datetime import datetime

# Force Python to recognize the parent directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from trading_records.db_connector import db
from broker_gateway.broker_upstox.connector import broker

SYMBOL_TAG = "NIFTY_INDEX"
INSTRUMENT_KEY = "NSE_INDEX|Nifty 50"

def run_daily_spot_sync():
    print(f"🔄 DAILY SYNC: Checking for new Spot Data ({SYMBOL_TAG})...")
    
    if not broker.is_connected():
        print("❌ Broker not connected. Please login first.")
        return

    db.connect()
    conn = db.connection
    cursor = conn.cursor()
    
    try:
        cursor.execute(f"SELECT MAX(timestamp) FROM market_data WHERE symbol = '{SYMBOL_TAG}'")
        last_time_str = cursor.fetchone()[0]
        
        last_dt = pd.to_datetime(last_time_str)
        start_date = last_dt.date()
        end_date = datetime.now().date()
        
        if start_date > end_date:
            print("✅ Spot Data is already up to date.")
            return
            
        print(f"📅 Last DB Record: {last_dt}")
        print(f"🚀 Fetching updates from {start_date} to {end_date}...")

        str_from = start_date.strftime("%Y-%m-%d")
        str_to = end_date.strftime("%Y-%m-%d")
        
        candles = broker.get_historical_data(INSTRUMENT_KEY, str_from, str_to, interval="1minute")

        if not candles:
            print("⚠️ No new data returned from Broker.")
            return

        data_to_insert = []
        for candle in candles:
            c_dt = pd.to_datetime(candle[0])
            if c_dt > last_dt:
                formatted_timestamp = c_dt.strftime("%Y-%m-%dT%H:%M:%S+05:30")
                data_to_insert.append((
                    formatted_timestamp, SYMBOL_TAG,
                    float(candle[1]), float(candle[2]), float(candle[3]), float(candle[4]),
                    int(candle[5]), 0
                ))

        if data_to_insert:
            print(f"💾 Saving {len(data_to_insert)} new spot candles...")
            cursor.execute("BEGIN TRANSACTION;")
            cursor.executemany("""
                INSERT OR IGNORE INTO market_data 
                (timestamp, symbol, open, high, low, close, volume, oi)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, data_to_insert)
            conn.commit()
            print("✅ SPOT SYNC COMPLETE.")
        else:
            print("✅ Spot Database is already perfectly synced.")
            
    except Exception as e:
        print(f"❌ Sync Failed: {e}")
        conn.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    run_daily_spot_sync()