# ==========================================
# DATA/BREEZE_OPTIONS_COLLECTOR.PY
# ==========================================
# Backfill expired Nifty/BankNifty options 1-min data
# from ICICI Breeze API into the options_ohlc table.
#
# Breeze gives ~3 years of expired options data - the
# only free API in India that does this.
#
# Usage:
#   # First get session token (daily, via browser login):
#   python data/breeze_options_collector.py --token
#
#   # Then backfill (e.g. last 6 months, ATM ± 10 strikes):
#   python data/breeze_options_collector.py --from 2025-08-01 --to 2026-02-14 --strikes 10
#
#   # Quick test (1 expiry, 3 strikes):
#   python data/breeze_options_collector.py --from 2026-02-10 --to 2026-02-10 --strikes 3
#
# Rate limits: 100 calls/min, 5000 calls/day
# Max rows per request: 1000 (~2.5 trading days of 1-min data)
# ==========================================

import os
import sys
import time
import json
import argparse
from datetime import datetime, timedelta, date
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(os.path.join("config", ".env"))

from config_loader.settings import INDEX_CONFIG
from trading_records.db_connector import db
from utilities.logger import get_logger

logger = get_logger("breeze_collector")

# ==========================================
# CONSTANTS
# ==========================================

from config_loader.settings import cfg
API_KEY = cfg.BREEZE_API_KEY
SECRET_KEY = cfg.BREEZE_SECRET_KEY

TOKEN_FILE = Path("data/breeze_session.json")

# Breeze uses different stock codes than NSE symbols
BREEZE_STOCK_CODES = {
    "NIFTY":     "NIFTY",
    "BANKNIFTY": "CNXBAN",
    "FINNIFTY":  "NIFFIN",
    "MIDCPNIFTY": "NIFMID",
}

# Nifty weekly expiry history:
# Before July 2024: Thursday
# After July 2024:  Tuesday (SEBI change)
EXPIRY_CHANGE_DATE = date(2024, 7, 12)  # Approximate switchover

# ==========================================
# TABLE SCHEMA (same as options_data_collector)
# ==========================================

OPTIONS_OHLC_SCHEMA = """
CREATE TABLE IF NOT EXISTS options_ohlc (
    timestamp       TEXT NOT NULL,
    instrument_key  TEXT NOT NULL,
    tradingsymbol   TEXT NOT NULL,
    instrument      TEXT NOT NULL,
    expiry          TEXT NOT NULL,
    strike          REAL NOT NULL,
    option_type     TEXT NOT NULL,
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    volume          INTEGER DEFAULT 0,
    oi              INTEGER DEFAULT 0,
    PRIMARY KEY (timestamp, instrument_key)
);

CREATE INDEX IF NOT EXISTS idx_opts_instrument_ts
    ON options_ohlc (instrument, timestamp);
CREATE INDEX IF NOT EXISTS idx_opts_expiry_strike
    ON options_ohlc (expiry, strike, option_type);
CREATE INDEX IF NOT EXISTS idx_opts_symbol
    ON options_ohlc (tradingsymbol);
"""


def ensure_table():
    conn = db._get_conn()
    for stmt in OPTIONS_OHLC_SCHEMA.split(';'):
        if stmt.strip():
            conn.execute(stmt)
    conn.commit()


# ==========================================
# SESSION MANAGEMENT
# ==========================================

def get_breeze_client(session_token=None):
    """Initialize and return authenticated BreezeConnect client."""
    from breeze_connect import BreezeConnect

    if not API_KEY or not SECRET_KEY:
        print("❌ BREEZE_API_KEY / BREEZE_SECRET_KEY missing in config/.env")
        sys.exit(1)

    breeze = BreezeConnect(api_key=API_KEY)

    # Try saved token first
    if session_token is None and TOKEN_FILE.exists():
        saved = json.loads(TOKEN_FILE.read_text())
        saved_date = saved.get("date")
        if saved_date == str(date.today()):
            session_token = saved.get("token")
            print(f"  [OK] Using saved session token from today")

    if session_token is None:
        print(f"\n  ❌ No session token. Generate one:")
        print(f"  1. Open: https://api.icicidirect.com/apiuser/login?api_key={API_KEY}")
        print(f"  2. Login with ICICI Direct credentials")
        print(f"  3. Copy 'code' from redirect URL")
        print(f"  4. Run: python data/breeze_options_collector.py --session YOUR_CODE")
        sys.exit(1)

    try:
        breeze.generate_session(api_secret=SECRET_KEY, session_token=session_token)
        # Save for reuse today
        TOKEN_FILE.write_text(json.dumps({
            "date": str(date.today()),
            "token": session_token
        }))
        print(f"  [OK] Breeze session active")
        return breeze
    except Exception as e:
        print(f"  ❌ Session failed: {e}")
        print(f"  Token may have expired. Generate a new one via browser.")
        sys.exit(1)


# ==========================================
# EXPIRY DATE CALCULATION
# ==========================================

def get_weekly_expiries(instrument, from_date, to_date):
    """
    Generate all weekly expiry dates for an instrument in a date range.
    Nifty: Thursday (pre-July 2024) → Tuesday (post-July 2024)
    BankNifty: Wednesday (pre-change) → Tuesday (post-change)
    """
    expiries = []
    current = from_date

    # Determine expiry weekday
    # Post July 2024: All indexes moved to Tuesday (weekday=1)
    # Pre July 2024: Nifty=Thursday(3), BankNifty=Wednesday(2)
    while current <= to_date:
        if current >= EXPIRY_CHANGE_DATE:
            expiry_weekday = 1  # Tuesday
        else:
            if instrument.upper() in ("NIFTY", "FINNIFTY"):
                expiry_weekday = 3  # Thursday
            elif instrument.upper() == "BANKNIFTY":
                expiry_weekday = 2  # Wednesday
            else:
                expiry_weekday = 3  # Default Thursday

        # Find next expiry day from current
        days_ahead = expiry_weekday - current.weekday()
        if days_ahead < 0:
            days_ahead += 7

        expiry = current + timedelta(days=days_ahead)

        if from_date <= expiry <= to_date and expiry not in expiries:
            expiries.append(expiry)

        current = expiry + timedelta(days=1)

    return sorted(expiries)


# ==========================================
# DATA FETCHING
# ==========================================

# Global call counter for rate limiting
_call_count = 0
_call_window_start = time.time()


def rate_limit():
    """Enforce 100 calls/min rate limit."""
    global _call_count, _call_window_start
    _call_count += 1

    elapsed = time.time() - _call_window_start
    if elapsed < 60 and _call_count >= 95:  # Leave 5 call buffer
        wait = 60 - elapsed + 1
        print(f"    ⏳ Rate limit pause: {wait:.0f}s...")
        time.sleep(wait)
        _call_count = 0
        _call_window_start = time.time()
    elif elapsed >= 60:
        _call_count = 0
        _call_window_start = time.time()


def fetch_option_day(breeze, stock_code, expiry_date, strike, right, trading_day):
    """
    Fetch 1-min candles for a single option contract on a single day.
    Handles the 1000-row limit by fetching full day in one call
    (375 candles < 1000 limit).

    Returns: list of dicts or empty list
    """
    expiry_str = expiry_date.strftime("%Y-%m-%dT07:00:00.000Z")
    from_str = trading_day.strftime("%Y-%m-%dT03:45:00.000Z")   # 9:15 IST = 03:45 UTC
    to_str = trading_day.strftime("%Y-%m-%dT10:00:00.000Z")     # 15:30 IST = 10:00 UTC

    rate_limit()

    try:
        data = breeze.get_historical_data_v2(
            interval="1minute",
            from_date=from_str,
            to_date=to_str,
            stock_code=stock_code,
            exchange_code="NFO",
            product_type="options",
            expiry_date=expiry_str,
            right=right.lower(),
            strike_price=str(int(strike))
        )

        if data and data.get("Status") == 200 and data.get("Success"):
            return data["Success"]
        return []

    except Exception as e:
        logger.warning("Breeze fetch error", strike=strike, right=right, error=str(e))
        return []


def store_candles(candles, instrument, expiry_str, strike, option_type):
    """Store fetched candles into options_ohlc table."""
    if not candles:
        return 0

    conn = db._get_conn()
    inserted = 0

    # Build a synthetic instrument_key for Breeze data
    # Format: BREEZE|NIFTY|25800|CE|2026-02-11
    inst_key = f"BREEZE|{instrument}|{int(strike)}|{option_type}|{expiry_str}"

    # Build tradingsymbol (approximate NSE format)
    exp_date = datetime.strptime(expiry_str, "%Y-%m-%d")
    exp_fmt = exp_date.strftime("%d%b%y").upper()  # e.g. "11FEB26"
    symbol = f"{instrument}{exp_fmt}{int(strike)}{option_type}"

    for c in candles:
        try:
            # Breeze returns datetime as "2025-02-03 09:15:00"
            ts = c.get("datetime", "")
            # Normalize to ISO format with timezone
            if "+" not in ts and "T" not in ts:
                ts = ts.replace(" ", "T") + "+05:30"

            conn.execute(
                "INSERT OR IGNORE INTO options_ohlc "
                "(timestamp, instrument_key, tradingsymbol, instrument, "
                "expiry, strike, option_type, open, high, low, close, volume, oi) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ts,
                    inst_key,
                    symbol,
                    instrument,
                    expiry_str,
                    float(strike),
                    option_type,
                    float(c.get("open", 0)),
                    float(c.get("high", 0)),
                    float(c.get("low", 0)),
                    float(c.get("close", 0)),
                    int(c.get("volume", 0)),
                    int(c.get("open_interest", 0)),
                ]
            )
            inserted += 1
        except Exception as e:
            logger.warning("Insert failed", error=str(e))

    conn.commit()
    return inserted


def get_trading_days(from_date, to_date):
    """Generate weekdays between two dates."""
    days = []
    current = from_date
    while current <= to_date:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def data_exists(instrument, expiry_str, strike, option_type, trading_day):
    """Check if data already exists for this contract+day."""
    day_str = trading_day.strftime("%Y-%m-%d")
    inst_key = f"BREEZE|{instrument}|{int(strike)}|{option_type}|{expiry_str}"
    row = db.query_one(
        "SELECT COUNT(*) as cnt FROM options_ohlc "
        "WHERE instrument_key = ? AND timestamp LIKE ?",
        [inst_key, f"{day_str}%"]
    )
    return row and row["cnt"] > 0


# ==========================================
# MAIN COLLECTOR
# ==========================================

def collect(instrument="NIFTY", from_date=None, to_date=None,
            num_strikes=10, spot_price=None, session_token=None):
    """
    Main backfill function.

    Args:
        instrument: "NIFTY" or "BANKNIFTY"
        from_date: start date
        to_date: end date
        num_strikes: strikes above/below ATM
        spot_price: approximate spot for ATM calculation
        session_token: Breeze session token
    """
    breeze = get_breeze_client(session_token)

    stock_code = BREEZE_STOCK_CODES.get(instrument.upper())
    if not stock_code:
        print(f"  ❌ Unknown instrument: {instrument}")
        return

    config = INDEX_CONFIG.get(instrument.upper())
    gap = config["strike_gap"] if config else 50

    today = date.today()
    if to_date is None:
        to_date = today - timedelta(days=1)
    if from_date is None:
        from_date = to_date - timedelta(days=30)
    if spot_price is None:
        spot_price = 25800  # Default; update for your instrument

    atm = round(spot_price / gap) * gap

    print(f"\n{'='*60}")
    print(f"  BREEZE OPTIONS BACKFILL COLLECTOR")
    print(f"{'='*60}")
    print(f"  Instrument:   {instrument} (Breeze code: {stock_code})")
    print(f"  Date Range:   {from_date} to {to_date}")
    print(f"  Spot (approx): {spot_price}")
    print(f"  ATM Strike:   {atm}")
    print(f"  Strike Range: {atm - num_strikes*gap} to {atm + num_strikes*gap}")
    print(f"  Strikes/side: {num_strikes} (= {(2*num_strikes+1)*2} contracts/expiry)")

    # Setup DB
    db.connect()
    ensure_table()

    # Generate strikes
    strikes = [atm + (i * gap) for i in range(-num_strikes, num_strikes + 1)]

    # Find expiries
    expiries = get_weekly_expiries(instrument, from_date, to_date)
    print(f"  Expiries:     {len(expiries)}")
    if expiries:
        print(f"                {expiries[0]} ... {expiries[-1]}")

    total_candles = 0
    total_api_calls = 0
    skipped = 0

    for exp_idx, expiry in enumerate(expiries):
        expiry_str = expiry.strftime("%Y-%m-%d")
        print(f"\n  [{exp_idx+1}/{len(expiries)}] Expiry: {expiry_str}")

        # Trading days for this expiry (typically the week before + expiry day)
        # Fetch data for 5 trading days before expiry through expiry day
        exp_start = expiry - timedelta(days=6)
        if exp_start < from_date:
            exp_start = from_date
        exp_end = min(expiry, to_date)

        trading_days = get_trading_days(exp_start, exp_end)
        if not trading_days:
            print(f"    No trading days in range")
            continue

        print(f"    Days: {trading_days[0]} to {trading_days[-1]} ({len(trading_days)} days)")
        print(f"    Contracts: {len(strikes)*2} (CE+PE)")

        expiry_candles = 0

        for day in trading_days:
            day_candles = 0

            for strike in strikes:
                for opt_type, right in [("CE", "call"), ("PE", "put")]:
                    # Skip if already fetched
                    if data_exists(instrument, expiry_str, strike, opt_type, day):
                        skipped += 1
                        continue

                    candles = fetch_option_day(
                        breeze, stock_code, expiry, strike, right, day
                    )
                    total_api_calls += 1

                    if candles:
                        count = store_candles(
                            candles, instrument, expiry_str, strike, opt_type
                        )
                        day_candles += count

            if day_candles > 0:
                print(f"      {day}: +{day_candles} candles")
            expiry_candles += day_candles

        total_candles += expiry_candles
        if expiry_candles > 0:
            print(f"    Subtotal: {expiry_candles} candles")

    # Summary
    print(f"\n{'='*60}")
    print(f"  BACKFILL COMPLETE")
    print(f"{'='*60}")
    print(f"  Expiries processed:  {len(expiries)}")
    print(f"  API calls made:      {total_api_calls}")
    print(f"  Skipped (existing):  {skipped}")
    print(f"  Candles inserted:    {total_candles}")

    stats = db.query_one("SELECT COUNT(*) as cnt FROM options_ohlc")
    print(f"  Total rows in DB:    {stats['cnt']}")

    db.close()


# ==========================================
# CLI
# ==========================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill Nifty/BankNifty options data from Breeze API"
    )
    parser.add_argument("--instrument", default="NIFTY",
                        help="NIFTY or BANKNIFTY")
    parser.add_argument("--from", dest="from_date",
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date",
                        help="End date YYYY-MM-DD")
    parser.add_argument("--strikes", type=int, default=10,
                        help="Strikes above/below ATM (default: 10)")
    parser.add_argument("--spot", type=float, default=None,
                        help="Approximate spot price for ATM calc")
    parser.add_argument("--session", default=None,
                        help="Breeze session token (from browser login)")
    parser.add_argument("--token", action="store_true",
                        help="Show session token generation instructions")

    args = parser.parse_args()

    if args.token:
        print(f"\n  Generate Breeze session token:")
        print(f"  1. Open: https://api.icicidirect.com/apiuser/login?api_key={API_KEY}")
        print(f"  2. Login with ICICI Direct credentials")
        print(f"  3. Copy 'code' from redirect URL")
        print(f"  4. Run with --session YOUR_CODE")
        sys.exit(0)

    fd = datetime.strptime(args.from_date, "%Y-%m-%d").date() if args.from_date else None
    td = datetime.strptime(args.to_date, "%Y-%m-%d").date() if args.to_date else None

    collect(
        instrument=args.instrument,
        from_date=fd,
        to_date=td,
        num_strikes=args.strikes,
        spot_price=args.spot,
        session_token=args.session,
    )