# ==========================================
# CORE/OPTION_CHAIN.PY
# ==========================================
# Purpose:
#   Reads the Angel One instrument master file and resolves
#   option contracts for a given index, expiry, and spot price.
#   Cached in memory — loads master file once per session.
#
# Usage:
#   from instruments.derivatives.options_chain import option_chain
#   option_chain.load_master()
#
#   expiry = option_chain.get_next_expiry("NIFTY")
#   contracts = option_chain.get_chain("NIFTY", expiry, spot_price=23450)
#   atm_pair = option_chain.get_atm_contracts("NIFTY", expiry, spot_price=23450)
#   single = option_chain.get_contract("NIFTY", expiry, 23450, "CE")
#
# Important Notes:
#   - Angel One stores strikes multiplied by 100 (25400 → 2540000.000000)
#   - Expiry format in master: DDMMMYYYY uppercase (e.g., "18FEB2026")
#   - Master file must be downloaded daily via download_master.py
#
# Cloud Migration:
#   Replace JSON file with database table or API call.
#   Same method signatures. Zero strategy changes.
# ==========================================

import pandas as pd
import datetime
from config_loader.settings import cfg
from config_loader.settings import INDEX_CONFIG
from utilities.logger import get_logger

logger = get_logger("option_chain")


class OptionChain:
    """
    Contract resolution from Angel One instrument master.
    Loads master file once, caches in memory for fast lookups.
    """

    def __init__(self):
        self._master_df = None
        self._is_loaded = False

    # ------------------------------------------
    # MASTER FILE
    # ------------------------------------------

    def load_master(self, filepath=None):
        """
        Load instrument master JSON into memory.
        Call once at startup. Subsequent calls skip unless force=True.

        Args:
            filepath (str): Override path. Default: from config.
        """
        if self._is_loaded and self._master_df is not None:
            logger.info("Master already loaded, skipping",
                        records=len(self._master_df))
            return True

        filepath = filepath or str(cfg.INSTRUMENT_MASTER)

        try:
            df = pd.read_json(filepath)
            df.columns = df.columns.str.strip()
            df['expiry'] = df['expiry'].astype(str).str.strip()
            df['name'] = df['name'].str.strip()

            # Pre-compute real strike values (Angel One stores × 100)
            df['strike_value'] = df['strike'].astype(float) / 100

            self._master_df = df
            self._is_loaded = True

            logger.info("Master file loaded",
                        path=filepath,
                        records=len(df))
            return True

        except FileNotFoundError:
            logger.error("Master file not found", path=filepath)
            return False
        except Exception as e:
            logger.error("Master file load failed", error=str(e))
            return False

    def reload_master(self, filepath=None):
        """Force reload master file (e.g., after downloading new one)."""
        self._master_df = None
        self._is_loaded = False
        return self.load_master(filepath)

    def _ensure_loaded(self):
        """Auto-load if not loaded. Raises if load fails."""
        if not self._is_loaded:
            if not self.load_master():
                raise RuntimeError("Instrument master not loaded")

    # ------------------------------------------
    # EXPIRY CALCULATION
    # ------------------------------------------

    def get_next_expiry(self, instrument, from_date=None):
        """
        Calculate the next expiry date for an instrument.
        NIFTY/FINNIFTY = Tuesday, BANKNIFTY = Wednesday, SENSEX = Thursday.

        Args:
            instrument (str): "NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"
            from_date (date): Optional. Defaults to today.

        Returns:
            str: Expiry in DDMMMYYYY format (e.g., "18FEB2026"), or None
        """
        config = INDEX_CONFIG.get(instrument.upper())
        if not config:
            logger.error("Unknown instrument for expiry", instrument=instrument)
            return None

        today = from_date or datetime.date.today()
        target_weekday = config["expiry_day"]

        # Days until next expiry (0 = today IS expiry day)
        days_ahead = (target_weekday - today.weekday()) % 7
        next_expiry = today + datetime.timedelta(days=days_ahead)

        expiry_str = next_expiry.strftime("%d%b%Y").upper()

        logger.info("Expiry calculated",
                    instrument=instrument,
                    expiry=expiry_str,
                    days_ahead=days_ahead)
        return expiry_str

    def get_expiry_date(self, instrument, from_date=None):
        """
        Same as get_next_expiry but returns a date object.
        Useful for date arithmetic.
        """
        config = INDEX_CONFIG.get(instrument.upper())
        if not config:
            return None

        today = from_date or datetime.date.today()
        target_weekday = config["expiry_day"]
        days_ahead = (target_weekday - today.weekday()) % 7
        return today + datetime.timedelta(days=days_ahead)

    # ------------------------------------------
    # CONTRACT RESOLUTION
    # ------------------------------------------

    def get_chain(self, instrument, expiry, spot_price, strikes_above=5, strikes_below=5):
        """
        Get option chain: all CE/PE contracts within a strike range.

        Args:
            instrument (str): "NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"
            expiry (str): DDMMMYYYY format (e.g., "18FEB2026")
            spot_price (float): Current spot price
            strikes_above (int): Number of strikes above ATM
            strikes_below (int): Number of strikes below ATM

        Returns:
            list: List of contract dicts with keys:
                  symbol, token, strike, option_type, exchange, expiry
        """
        self._ensure_loaded()

        config = INDEX_CONFIG.get(instrument.upper())
        if not config:
            logger.error("Unknown instrument", instrument=instrument)
            return []

        gap = config["strike_gap"]
        exchange = config["option_exchange"]
        prefix = config["option_prefix"]

        # Calculate ATM and range
        atm = round(spot_price / gap) * gap
        lower = atm - (strikes_below * gap)
        upper = atm + (strikes_above * gap)

        # Filter master data
        df = self._master_df
        mask = (
            (df['name'] == prefix)
            & (df['expiry'] == expiry)
            & (df['exch_seg'] == exchange)
            & (df['strike_value'] >= lower)
            & (df['strike_value'] <= upper)
        )

        chain = df[mask].sort_values(by=['strike_value', 'symbol'])

        if chain.empty:
            logger.warning("No contracts found",
                           instrument=instrument,
                           expiry=expiry,
                           range=f"{lower}-{upper}")
            return []

        # Build clean contract list
        contracts = []
        for _, row in chain.iterrows():
            symbol = row['symbol']
            contracts.append({
                "symbol":      symbol,
                "token":       str(row['token']),
                "strike":      int(row['strike_value']),
                "option_type": "CE" if symbol.endswith("CE") else "PE",
                "exchange":    exchange,
                "expiry":      expiry,
            })

        logger.info("Chain resolved",
                    instrument=instrument,
                    expiry=expiry,
                    atm=atm,
                    contracts=len(contracts))
        return contracts

    def get_atm_contracts(self, instrument, expiry, spot_price):
        """
        Get exactly the ATM CE + PE pair. Used for straddle entry.

        Args:
            instrument (str): "NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"
            expiry (str): DDMMMYYYY format
            spot_price (float): Current spot price

        Returns:
            dict: {"CE": contract, "PE": contract, "atm_strike": int}
                  or None on failure
        """
        contracts = self.get_chain(
            instrument, expiry, spot_price,
            strikes_above=0, strikes_below=0
        )

        if not contracts:
            logger.error("No ATM contracts found",
                         instrument=instrument, expiry=expiry)
            return None

        config = INDEX_CONFIG.get(instrument.upper())
        gap = config["strike_gap"]
        atm = round(spot_price / gap) * gap

        ce = None
        pe = None
        for c in contracts:
            if c["option_type"] == "CE":
                ce = c
            elif c["option_type"] == "PE":
                pe = c

        if not ce or not pe:
            logger.error("Missing CE or PE at ATM",
                         instrument=instrument,
                         atm=atm,
                         found_ce=ce is not None,
                         found_pe=pe is not None)
            return None

        logger.info("ATM pair resolved",
                    instrument=instrument,
                    atm=atm,
                    ce_symbol=ce["symbol"],
                    pe_symbol=pe["symbol"])

        return {
            "CE": ce,
            "PE": pe,
            "atm_strike": int(atm),
        }

    def get_contract(self, instrument, expiry, strike, option_type):
        """
        Get a single specific contract.
        Used for rolling, hedging, or any targeted contract lookup.

        Args:
            instrument (str): "NIFTY", "BANKNIFTY", etc.
            expiry (str): DDMMMYYYY format
            strike (int): Strike price (e.g., 25400)
            option_type (str): "CE" or "PE"

        Returns:
            dict: Contract dict, or None if not found
        """
        self._ensure_loaded()

        config = INDEX_CONFIG.get(instrument.upper())
        if not config:
            logger.error("Unknown instrument", instrument=instrument)
            return None

        exchange = config["option_exchange"]
        prefix = config["option_prefix"]

        df = self._master_df
        mask = (
            (df['name'] == prefix)
            & (df['expiry'] == expiry)
            & (df['exch_seg'] == exchange)
            & (df['strike_value'] == strike)
        )

        matches = df[mask]

        # Filter for CE or PE
        suffix = option_type.upper()
        for _, row in matches.iterrows():
            if row['symbol'].endswith(suffix):
                contract = {
                    "symbol":      row['symbol'],
                    "token":       str(row['token']),
                    "strike":      int(row['strike_value']),
                    "option_type": suffix,
                    "exchange":    exchange,
                    "expiry":      expiry,
                }
                logger.info("Contract resolved",
                            symbol=contract["symbol"],
                            token=contract["token"])
                return contract

        logger.warning("Contract not found",
                       instrument=instrument,
                       strike=strike,
                       option_type=option_type,
                       expiry=expiry)
        return None

    def get_strikes_around(self, instrument, expiry, spot_price, count=5):
        """
        Get list of strike values around ATM.
        Useful for building strangles, iron flies, etc.

        Args:
            instrument (str): Index name
            expiry (str): DDMMMYYYY format
            spot_price (float): Spot price
            count (int): Number of strikes on each side

        Returns:
            list: Sorted list of strike ints, e.g., [25200, 25250, ..., 25600]
        """
        config = INDEX_CONFIG.get(instrument.upper())
        if not config:
            return []

        gap = config["strike_gap"]
        atm = round(spot_price / gap) * gap

        strikes = []
        for i in range(-count, count + 1):
            strikes.append(int(atm + (i * gap)))

        return strikes


# ==========================================
# SINGLETON INSTANCE
# ==========================================
# Import this everywhere:  from instruments.derivatives.options_chain import option_chain
option_chain = OptionChain()


# ==========================================
# STANDALONE TEST
# ==========================================
if __name__ == "__main__":
    print("=" * 60)
    print("  OPTION CHAIN TEST")
    print("=" * 60)

    # Test 1: Load master
    print("\n--- Test: Load Master File ---")
    loaded = option_chain.load_master()
    print(f"   Loaded: {loaded}")

    if not loaded:
        print("\n❌ Cannot proceed. Run download_master.py first.")
        exit(1)

    # Test 2: Expiry calculation
    print("\n--- Test: Next Expiry Dates ---")
    for name in ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"]:
        expiry = option_chain.get_next_expiry(name)
        print(f"   {name}: {expiry}")

    # Test 3: Strikes around ATM
    print("\n--- Test: Strikes Around ATM ---")
    strikes = option_chain.get_strikes_around("NIFTY", "", 23456.7, count=3)
    print(f"   NIFTY (spot=23456.7): {strikes}")

    strikes = option_chain.get_strikes_around("BANKNIFTY", "", 49820.3, count=3)
    print(f"   BANKNIFTY (spot=49820.3): {strikes}")

    # Test 4: Full chain
    nifty_expiry = option_chain.get_next_expiry("NIFTY")
    print(f"\n--- Test: NIFTY Chain ({nifty_expiry}, spot=23450) ---")
    chain = option_chain.get_chain("NIFTY", nifty_expiry, 23450, strikes_above=2, strikes_below=2)
    print(f"   Found {len(chain)} contracts:")
    for c in chain:
        print(f"   {c['option_type']} {c['strike']} | {c['symbol']} | Token: {c['token']}")

    # Test 5: ATM pair
    print(f"\n--- Test: ATM Straddle Pair ---")
    atm = option_chain.get_atm_contracts("NIFTY", nifty_expiry, 23450)
    if atm:
        print(f"   ATM Strike: {atm['atm_strike']}")
        print(f"   CE: {atm['CE']['symbol']} | Token: {atm['CE']['token']}")
        print(f"   PE: {atm['PE']['symbol']} | Token: {atm['PE']['token']}")
    else:
        print(f"   ⚠️ No ATM contracts found for {nifty_expiry}")
        print(f"   (Master file may not contain this expiry)")

    # Test 6: Single contract
    print(f"\n--- Test: Single Contract Lookup ---")
    contract = option_chain.get_contract("NIFTY", nifty_expiry, 23450, "CE")
    if contract:
        print(f"   {contract['symbol']} | Token: {contract['token']}")
    else:
        print(f"   ⚠️ Contract not found for {nifty_expiry} 23450 CE")

    # Test 7: Verify second load is cached
    print(f"\n--- Test: Cache Check ---")
    option_chain.load_master()  # Should say "already loaded"

    print(f"\n✅ Option chain test complete")