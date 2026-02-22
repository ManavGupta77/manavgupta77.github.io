from trading_records.db_connector import db
import pandas as pd

def check_market_data_status():
    db.connect()
    print("📊 --- MARKET DATA VOLUME & RECENT HISTORY ---")

    # 1. Fetch Total Row Count
    count_res = db.query_one("SELECT COUNT(*) as total FROM market_data")
    total_rows = count_res['total']
    print(f"✅ Total Rows in 'market_data': {total_rows:,}")

    # 2. Fetch Last 10 Rows
    print("\n⌛ --- LAST 10 ROWS IN DATABASE ---")
    last_10 = db.query("SELECT * FROM market_data ORDER BY timestamp DESC LIMIT 10")
    
    if last_10:
        df = pd.DataFrame(last_10)
        # Re-ordering columns for better readability
        cols = ['timestamp', 'symbol', 'open', 'high', 'low', 'close']
        print(df[cols].to_string(index=False))
    else:
        print("❌ The 'market_data' table appears to be empty.")

    db.close()
    print("\n--- END OF STATUS CHECK ---")

if __name__ == "__main__":
    check_market_data_status()