"""
===========================================================================
IRON STRADDLE BACKTEST — DATA DIAGNOSTIC
===========================================================================
Run this BEFORE the backtest to understand your database structure.
Usage: python -m backtesting.data_diagnostic
===========================================================================
"""

from trading_records.db_connector import db


def run_diagnostics():
    db.connect()
    print(f"\n{'='*80}")
    print(f"🔍 DATABASE DIAGNOSTIC REPORT")
    print(f"{'='*80}")

    # ─────────────────────────────────────────────
    # 1. LIST ALL TABLES + ROW COUNTS
    # ─────────────────────────────────────────────
    tables = db.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    print(f"\n📋 TABLES IN DATABASE ({len(tables)} total):")
    print(f"{'─'*60}")
    for t in tables:
        tname = t['name']
        count = db.query_one(f"SELECT COUNT(*) as c FROM [{tname}]")
        print(f"  {tname:<30} → {count['c']:>12,} rows")

    # ─────────────────────────────────────────────
    # 2. IDENTIFY SPOT/INDEX DATA TABLE
    # ─────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"🔎 SEARCHING FOR SPOT / INDEX DATA")
    print(f"{'='*80}")

    # Check likely table names for spot data
    spot_candidates = ['market_data', 'nifty_data', 'spot_data', 'spot_ohlc', 'index_data']
    found_spot_tables = []

    for tname in [t['name'] for t in tables]:
        if any(kw in tname.lower() for kw in ['spot', 'nifty', 'market', 'index']):
            found_spot_tables.append(tname)

    if found_spot_tables:
        for tname in found_spot_tables:
            print(f"\n  📊 Table: {tname}")
            # Get columns
            cols = db.query(f"PRAGMA table_info([{tname}])")
            col_names = [c['name'] for c in cols]
            print(f"     Columns: {col_names}")

            # Sample rows
            samples = db.query(f"SELECT * FROM [{tname}] LIMIT 3")
            if samples:
                print(f"     Sample rows:")
                for s in samples:
                    print(f"       {dict(s)}")

            # Date range
            # Try common timestamp column names
            for ts_col in ['timestamp', 'datetime', 'date', 'time', 'Date', 'Timestamp', 'DateTime']:
                if ts_col in col_names:
                    range_q = db.query_one(f"SELECT MIN([{ts_col}]) as mn, MAX([{ts_col}]) as mx FROM [{tname}]")
                    print(f"     Date range ({ts_col}): {range_q['mn']} → {range_q['mx']}")

                    # Check for our target date
                    for target_date in ['2026-02-16', '2026-02-17', '2025-12-01', '2025-11-27']:
                        check = db.query_one(f"SELECT COUNT(*) as c FROM [{tname}] WHERE [{ts_col}] LIKE ?",
                                             [f"{target_date}%"])
                        if check['c'] > 0:
                            print(f"     ✅ Has data for {target_date}: {check['c']} rows")
                        else:
                            print(f"     ❌ No data for {target_date}")
                    break

            # Check for symbol column
            for sym_col in ['symbol', 'Symbol', 'tradingsymbol', 'instrument', 'ticker']:
                if sym_col in col_names:
                    syms = db.query(f"SELECT DISTINCT [{sym_col}] FROM [{tname}] LIMIT 20")
                    print(f"     Distinct {sym_col}s: {[s[sym_col] for s in syms]}")
                    break
    else:
        print("  ⚠️ No obvious spot data table found!")

    # ─────────────────────────────────────────────
    # 3. OPTIONS DATA TABLE
    # ─────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"🔎 SEARCHING FOR OPTIONS DATA")
    print(f"{'='*80}")

    options_candidates = []
    for tname in [t['name'] for t in tables]:
        if any(kw in tname.lower() for kw in ['option', 'ohlc', 'chain']):
            options_candidates.append(tname)

    if options_candidates:
        for tname in options_candidates:
            print(f"\n  📊 Table: {tname}")
            cols = db.query(f"PRAGMA table_info([{tname}])")
            col_names = [c['name'] for c in cols]
            print(f"     Columns: {col_names}")

            samples = db.query(f"SELECT * FROM [{tname}] LIMIT 3")
            if samples:
                print(f"     Sample rows:")
                for s in samples:
                    print(f"       {dict(s)}")

            # Expiry list
            for exp_col in ['expiry', 'expiry_date', 'Expiry', 'ExpiryDate']:
                if exp_col in col_names:
                    expiries = db.query(f"SELECT DISTINCT [{exp_col}] FROM [{tname}] ORDER BY [{exp_col}] DESC LIMIT 10")
                    print(f"     Recent expiries: {[e[exp_col] for e in expiries]}")
                    break

            # Timestamp format + range
            for ts_col in ['timestamp', 'datetime', 'date', 'Timestamp', 'DateTime']:
                if ts_col in col_names:
                    range_q = db.query_one(f"SELECT MIN([{ts_col}]) as mn, MAX([{ts_col}]) as mx FROM [{tname}]")
                    print(f"     Timestamp range: {range_q['mn']} → {range_q['mx']}")

                    # Sample timestamps to see format
                    ts_samples = db.query(f"SELECT DISTINCT [{ts_col}] FROM [{tname}] ORDER BY [{ts_col}] DESC LIMIT 5")
                    print(f"     Timestamp format samples: {[t[ts_col] for t in ts_samples]}")
                    break

            # Strike info
            for strike_col in ['strike', 'Strike', 'strike_price']:
                if strike_col in col_names:
                    strike_info = db.query_one(f"SELECT MIN([{strike_col}]) as mn, MAX([{strike_col}]) as mx, "
                                               f"COUNT(DISTINCT [{strike_col}]) as cnt FROM [{tname}]")
                    print(f"     Strikes: {strike_info['mn']} → {strike_info['mx']} ({strike_info['cnt']} distinct)")
                    break

            # Option types
            for ot_col in ['option_type', 'OptionType', 'opt_type', 'right']:
                if ot_col in col_names:
                    otypes = db.query(f"SELECT DISTINCT [{ot_col}] FROM [{tname}]")
                    print(f"     Option types: {[o[ot_col] for o in otypes]}")
                    break
    else:
        print("  ⚠️ No obvious options data table found!")

    # ─────────────────────────────────────────────
    # 4. DATE OVERLAP ANALYSIS
    # ─────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"📅 AVAILABLE TRADING DATES (last 20)")
    print(f"{'='*80}")

    # Try to find trading dates from the options table
    if options_candidates:
        tname = options_candidates[0]
        for ts_col in ['timestamp', 'datetime', 'date', 'Timestamp']:
            cols = [c['name'] for c in db.query(f"PRAGMA table_info([{tname}])")]
            if ts_col in cols:
                # Extract unique dates
                dates = db.query(f"""
                    SELECT DISTINCT SUBSTR([{ts_col}], 1, 10) as trade_date 
                    FROM [{tname}] 
                    ORDER BY trade_date DESC 
                    LIMIT 20
                """)
                print(f"\n  From {tname} ({ts_col}):")
                for d in dates:
                    print(f"    {d['trade_date']}")
                break

    # Also check spot table dates
    if found_spot_tables:
        tname = found_spot_tables[0]
        for ts_col in ['timestamp', 'datetime', 'date', 'Date', 'Timestamp']:
            cols = [c['name'] for c in db.query(f"PRAGMA table_info([{tname}])")]
            if ts_col in cols:
                dates = db.query(f"""
                    SELECT DISTINCT SUBSTR([{ts_col}], 1, 10) as trade_date 
                    FROM [{tname}] 
                    ORDER BY trade_date DESC 
                    LIMIT 20
                """)
                print(f"\n  From {tname} ({ts_col}):")
                for d in dates:
                    print(f"    {d['trade_date']}")
                break

    # ─────────────────────────────────────────────
    # 5. QUICK COMPATIBILITY CHECK
    # ─────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"🧪 BACKTEST COMPATIBILITY CHECK")
    print(f"{'='*80}")

    # Find a date that exists in BOTH spot and options
    if found_spot_tables and options_candidates:
        spot_table = found_spot_tables[0]
        opt_table = options_candidates[0]

        spot_cols = [c['name'] for c in db.query(f"PRAGMA table_info([{spot_table}])")]
        opt_cols = [c['name'] for c in db.query(f"PRAGMA table_info([{opt_table}])")]

        # Find the timestamp columns
        spot_ts_col = next((c for c in ['timestamp', 'datetime', 'date', 'Date', 'Timestamp'] if c in spot_cols), None)
        opt_ts_col = next((c for c in ['timestamp', 'datetime', 'date', 'Timestamp'] if c in opt_cols), None)

        if spot_ts_col and opt_ts_col:
            # Get last 5 dates from each
            spot_dates = db.query(f"SELECT DISTINCT SUBSTR([{spot_ts_col}], 1, 10) as d FROM [{spot_table}] ORDER BY d DESC LIMIT 30")
            opt_dates = db.query(f"SELECT DISTINCT SUBSTR([{opt_ts_col}], 1, 10) as d FROM [{opt_table}] ORDER BY d DESC LIMIT 30")

            spot_set = {d['d'] for d in spot_dates}
            opt_set = {d['d'] for d in opt_dates}
            overlap = sorted(spot_set & opt_set, reverse=True)

            if overlap:
                print(f"\n  ✅ OVERLAPPING DATES (spot + options both available):")
                for d in overlap[:10]:
                    print(f"    {d}")

                # For the most recent overlap date, do a sample query
                test_date = overlap[0]
                print(f"\n  🧪 SAMPLE DATA FOR {test_date}:")

                # Spot sample at ~09:30
                spot_sample = db.query(f"SELECT * FROM [{spot_table}] WHERE [{spot_ts_col}] LIKE ? LIMIT 3",
                                       [f"{test_date}%09:30%"])
                if not spot_sample:
                    spot_sample = db.query(f"SELECT * FROM [{spot_table}] WHERE [{spot_ts_col}] LIKE ? LIMIT 3",
                                           [f"{test_date}%"])
                print(f"    Spot ({spot_table}):")
                for s in spot_sample:
                    print(f"      {dict(s)}")

                # Options sample
                opt_sample = db.query(f"SELECT * FROM [{opt_table}] WHERE [{opt_ts_col}] LIKE ? LIMIT 5",
                                      [f"{test_date}%09:30%"])
                if not opt_sample:
                    opt_sample = db.query(f"SELECT * FROM [{opt_table}] WHERE [{opt_ts_col}] LIKE ? LIMIT 5",
                                          [f"{test_date}%"])
                print(f"    Options ({opt_table}):")
                for s in opt_sample:
                    print(f"      {dict(s)}")
            else:
                print(f"\n  ❌ NO OVERLAPPING DATES between {spot_table} and {opt_table}")
                print(f"     Spot dates: {sorted(spot_set, reverse=True)[:5]}")
                print(f"     Options dates: {sorted(opt_set, reverse=True)[:5]}")

    print(f"\n{'='*80}")
    print(f"📋 DIAGNOSTIC COMPLETE — Use this output to configure the backtest")
    print(f"{'='*80}\n")

    db.close()


if __name__ == "__main__":
    run_diagnostics()