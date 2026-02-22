import pandas as pd
import sqlite3
from datetime import datetime, timedelta

# Configuration - Update this path if your DB is elsewhere
DB_PATH = "data/algo_trading.db"

def fetch_and_resample(symbol_tag, days_back=365, timeframe='5min'):
    """
    1. Connects to SQLite DB
    2. Fetches 1-min data for the last X days
    3. Resamples it to the target timeframe (e.g., 5min, 15min)
    """
    print(f"🔌 Connecting to DB to fetch {days_back} days of {symbol_tag}...")
    
    try:
        conn = sqlite3.connect(DB_PATH)
    except Exception as e:
        print(f"❌ DB Connection Failed: {e}")
        return None
    
    # Calculate Start Date
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    
    # Query: Fetch only what we need
    query = f"""
        SELECT timestamp, open, high, low, close, volume 
        FROM market_data 
        WHERE symbol = '{symbol_tag}' 
        AND timestamp >= '{start_date}' 
        ORDER BY timestamp ASC
    """
    
    try:
        df = pd.read_sql_query(query, conn)
    except Exception as e:
        print(f"❌ Query Failed: {e}")
        conn.close()
        return None

    conn.close()
    
    if df.empty:
        print("❌ No data found in DB for this range.")
        return None

    # Pre-processing
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)
    
    print(f"   ✅ Loaded {len(df)} raw 1-min candles.")

    # RESAMPLING ENGINE (The Magic)
    # This converts 1-min data -> 5-min candles mathematically
    # Rule: Open=First, High=Max, Low=Min, Close=Last, Volume=Sum
    resampled_df = df.resample(timeframe).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    })
    
    # Drop rows where no trade happened (e.g. market holidays causing empty rows)
    resampled_df.dropna(subset=['close'], inplace=True)
    
    # Reset index to make 'datetime' a column again (for your strategy)
    resampled_df.reset_index(inplace=True)
    resampled_df.rename(columns={'timestamp': 'datetime'}, inplace=True)
    
    print(f"   📊 Resampled to {len(resampled_df)} {timeframe} candles.")
    
    return resampled_df

# TEST BLOCK (Only runs if you execute this file directly)
if __name__ == "__main__":
    df = fetch_and_resample("NIFTY_INDEX", days_back=5, timeframe='5min')
    if df is not None:
        print(df.head())