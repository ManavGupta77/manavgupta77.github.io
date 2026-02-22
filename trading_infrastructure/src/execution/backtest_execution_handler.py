# ==============================================================================
# EXECUTION / BACKTEST_EXECUTION_HANDLER.PY
# ==============================================================================
# Simulates order fills during backtesting by querying your SQLite database.
#
# HOW IT WORKS:
#   1. Strategy emits a TradeSignal
#   2. PortfolioCoordinator passes signal here
#   3. For each leg in the signal, this handler queries options_ohlc for the
#      close price at the signal's timestamp
#   4. Returns a LegFill for each leg — identical in structure to what
#      PaperExecutionHandler and LiveExecutionHandler return
#   5. Strategy receives fills via on_order_update() — it never knows
#      whether the fill came from SQLite, a live feed, or a real broker
#
# FILL PRICE RULE:
#   Uses the CLOSE price of the candle at the signal timestamp.
#   This is the price your iron_straddle_v2.py already uses — so backtest
#   results will match your verified Rs.-932.75 benchmark exactly.
#
# DATABASE:
#   Primary table : options_ohlc  (options fills)
#   Secondary table: market_data  (spot price replay)
#   Connection     : via your existing db_connector — no new DB setup needed
#
# SCHEMA USED (confirmed from your database):
#   options_ohlc:
#     timestamp TEXT, instrument_key TEXT, tradingsymbol TEXT,
#     expiry TEXT, strike REAL, option_type TEXT,
#     open REAL, high REAL, low REAL, close REAL, volume INTEGER, oi INTEGER
#
#   market_data:
#     timestamp DATETIME, symbol TEXT,
#     open REAL, high REAL, low REAL, close REAL, volume INTEGER, oi INTEGER
#
# USAGE:
#   handler = BacktestExecutionHandler(date="2026-02-11", expiry="2026-02-17")
#   fills = handler.execute(signal, timestamp="09:30")
#   for fill in fills:
#       strategy.on_order_update(fill)
# ==============================================================================

from typing import List, Optional
from utilities.logger import get_logger
from trading_records.db_connector import db

from strategies.building_blocks.options_leg  import OptionsLeg, LegStatus
from strategies.building_blocks.trade_signal import TradeSignal, SignalType
from strategies.building_blocks.leg_fill     import LegFill, FillStatus, ExecutionMode
from strategies.building_blocks.market_tick  import MarketTick, CandleBar, GreekSnapshot
from strategies.building_blocks.greeks_calculator import GreeksCalculator


class BacktestExecutionHandler:
    """
    Simulates order execution by querying SQLite options_ohlc close prices.

    One instance per backtest session (one trading date).
    Reuse the same instance across all signals for a given date —
    it caches price data after the first query to avoid repeated DB hits.

    Args:
        date        : Trading date as 'YYYY-MM-DD'. e.g. '2026-02-11'
        expiry      : Weekly expiry date as 'YYYY-MM-DD'. e.g. '2026-02-17'
        spot_symbol : Spot index symbol in market_data table. Default 'NIFTY_INDEX'
        lot_size    : Lot size for the instrument. Default 65 (Nifty)
        risk_free   : Risk-free rate for Greeks calculation. Default 0.07 (7%)
    """

    def __init__(self, date: str, expiry: str,
                 spot_symbol: str = "NIFTY_INDEX",
                 lot_size: int = 65,
                 risk_free: float = 0.07):

        self.date        = date
        self.expiry      = expiry
        self.spot_symbol = spot_symbol
        self.lot_size    = lot_size
        self.mode        = ExecutionMode.BACKTEST
        self.logger      = get_logger("backtest_handler")

        # Greeks calculator — Black-Scholes
        self._greeks_calc = GreeksCalculator(risk_free_rate=risk_free)

        # ── Price Cache ───────────────────────────────────────────────────────
        # Loaded once from SQLite, then accessed by timestamp lookup.
        # Structure: {timestamp_iso: {tradingsymbol: close_price}}
        self._option_price_cache: dict = {}

        # Full candle cache for MarketTick assembly
        # Structure: {timestamp_iso: {tradingsymbol: CandleBar}}
        self._option_candle_cache: dict = {}

        # Spot price cache: {time_str_HH:MM: close_price}
        self._spot_cache: dict = {}

        # All timestamps available for this date (sorted)
        self._timestamps: List[str] = []

        # Loaded flag — set True after first load_data() call
        self._data_loaded = False

    # ==========================================================================
    # DATA LOADING
    # ==========================================================================

    def load_data(self, strikes: List[int]) -> bool:
        """
        Load all price data for the session into memory.

        Call this once before starting the tick replay loop.
        Queries options_ohlc for all provided strikes and market_data
        for spot prices, caching everything for fast per-tick access.

        Args:
            strikes : List of strike prices to load.
                      e.g. [22800, 22850, 22900, 23050, 22650]
                      Include ATM + all hedge strikes.

        Returns:
            True if data loaded successfully, False if no data found.
        """
        self.logger.info("Loading backtest data",
                         date=self.date, expiry=self.expiry, strikes=strikes)

        ts_start = f"{self.date}T09:15:00+05:30"
        ts_end   = f"{self.date}T15:30:00+05:30"

        # ── Load options data ─────────────────────────────────────────────────
        placeholders = ",".join(["?" for _ in strikes])
        query = f"""
            SELECT timestamp, tradingsymbol, strike, option_type,
                   open, high, low, close, volume, oi
            FROM options_ohlc
            WHERE expiry = ?
              AND strike IN ({placeholders})
              AND timestamp >= ?
              AND timestamp <= ?
            ORDER BY timestamp ASC
        """
        params = [self.expiry] + [float(s) for s in strikes] + [ts_start, ts_end]
        rows = db.query(query, params)

        if not rows:
            self.logger.error("No options data found",
                              date=self.date, expiry=self.expiry, strikes=strikes)
            return False

        # Build price and candle caches keyed by timestamp
        timestamps_seen = set()
        for row in rows:
            ts  = row["timestamp"]
            sym = row["tradingsymbol"]
            timestamps_seen.add(ts)

            if ts not in self._option_price_cache:
                self._option_price_cache[ts]  = {}
                self._option_candle_cache[ts] = {}

            close = row["close"] or 0.0
            self._option_price_cache[ts][sym] = close
            self._option_candle_cache[ts][sym] = CandleBar(
                open=row["open"]   or 0.0,
                high=row["high"]   or 0.0,
                low=row["low"]     or 0.0,
                close=close,
                volume=row["volume"] or 0,
                oi=row["oi"]         or 0,
            )

        # ── Load spot data ─────────────────────────────────────────────────────
        spot_query = """
            SELECT timestamp, close
            FROM market_data
            WHERE symbol = ?
              AND timestamp >= ?
              AND timestamp <= ?
            ORDER BY timestamp ASC
        """
        spot_rows = db.query(spot_query, [self.spot_symbol, ts_start, ts_end])
        for row in spot_rows:
            time_str = row["timestamp"][11:16]  # Extract HH:MM
            self._spot_cache[time_str] = row["close"] or 0.0

        # ── Sort timestamps ───────────────────────────────────────────────────
        self._timestamps = sorted(timestamps_seen)
        self._data_loaded = True

        self.logger.info("Backtest data loaded",
                         candles=len(self._timestamps),
                         symbols=len(set(
                             sym for prices in self._option_price_cache.values()
                             for sym in prices
                         )),
                         spot_candles=len(self._spot_cache))
        return True

    # ==========================================================================
    # TICK REPLAY
    # ==========================================================================

    def get_timestamps(self, from_time: str = "09:15",
                       to_time: str = "15:25") -> List[str]:
        """
        Return all available timestamps for this session within the given window.

        Args:
            from_time : Start time as 'HH:MM'. Default '09:15'.
            to_time   : End time as 'HH:MM'. Default '15:25'.

        Returns:
            Sorted list of ISO timestamp strings within the window.
        """
        if not self._data_loaded:
            return []

        result = []
        for ts in self._timestamps:
            time_str = ts[11:16]
            if from_time <= time_str <= to_time:
                result.append(ts)
        return result

    def build_tick(self, timestamp: str, expiry_date: str,
                   days_to_expiry: float,
                   tracked_symbols: List[str] = None,
                   compute_greeks: bool = True) -> Optional[MarketTick]:
        """
        Build a MarketTick for a given timestamp from cached data.

        Called by the strategy runner on each tick to assemble the full
        data packet before passing it to strategy lifecycle hooks.

        Args:
            timestamp       : Full ISO timestamp. e.g. '2026-02-11T09:30:00+05:30'
            expiry_date     : Weekly expiry date string.
            days_to_expiry  : Float days remaining to expiry.
            tracked_symbols : List of tradingsymbols to include in option_prices.
                              If None, includes all available symbols at this tick.
            compute_greeks  : If True, compute Black-Scholes Greeks for all
                              tracked sell symbols.

        Returns:
            MarketTick or None if no data available at this timestamp.
        """
        if timestamp not in self._option_price_cache:
            return None

        time_str = timestamp[11:16]
        date_str = timestamp[:10]
        spot     = self._spot_cache.get(time_str, 0.0)

        prices  = self._option_price_cache[timestamp]
        candles = self._option_candle_cache.get(timestamp, {})

        # Filter to tracked symbols if specified
        if tracked_symbols:
            prices  = {s: p for s, p in prices.items()  if s in tracked_symbols}
            candles = {s: c for s, c in candles.items() if s in tracked_symbols}

        # Build Greeks if requested and spot is available
        greeks = {}
        if compute_greeks and spot > 0 and days_to_expiry > 0:
            contracts = []
            for sym, price in prices.items():
                if price > 0:
                    # Look up strike and option_type from candle cache
                    # We need to find the row data — search option cache
                    row = self._get_symbol_meta(sym)
                    if row:
                        contracts.append({
                            "symbol":      sym,
                            "strike":      int(row["strike"]),
                            "price":       price,
                            "option_type": row["option_type"],
                        })
            if contracts:
                greeks = self._greeks_calc.compute_batch(
                    spot=spot,
                    contracts=contracts,
                    days_to_expiry=days_to_expiry,
                )

        return MarketTick.from_backtest_row(
            timestamp=timestamp,
            spot=spot,
            option_prices=prices,
            option_candles=candles,
            greeks=greeks,
            expiry_date=expiry_date,
            days_to_expiry=days_to_expiry,
        )

    def _get_symbol_meta(self, symbol: str) -> Optional[dict]:
        """
        Get strike and option_type for a tradingsymbol.
        Queries once and caches results.
        """
        if not hasattr(self, "_symbol_meta_cache"):
            self._symbol_meta_cache = {}

        if symbol in self._symbol_meta_cache:
            return self._symbol_meta_cache[symbol]

        rows = db.query(
            "SELECT strike, option_type FROM options_ohlc "
            "WHERE tradingsymbol = ? AND expiry = ? LIMIT 1",
            [symbol, self.expiry]
        )
        if rows:
            self._symbol_meta_cache[symbol] = rows[0]
            return rows[0]
        return None

    # ==========================================================================
    # ORDER EXECUTION
    # ==========================================================================

    def execute(self, signal: TradeSignal,
                timestamp: str) -> List[LegFill]:
        """
        Process a TradeSignal and return fills for all legs.

        This is the main method called by PortfolioCoordinator when a
        strategy emits a signal. For each leg in the signal:
          - Closing legs: look up current close price → return closing fill
          - Opening legs: look up current close price → return opening fill

        Fill price = close price of the candle at the given timestamp.
        This matches iron_straddle_v2.py's get_price() logic exactly,
        ensuring the Rs.-932.75 benchmark is reproducible.

        Args:
            signal    : TradeSignal emitted by the strategy.
            timestamp : Full ISO timestamp when the signal was generated.

        Returns:
            List of LegFill objects — one per leg in the signal.
            Rejected fills are included if a price cannot be found.
        """
        fills = []

        # ── Process closes first (buy-before-sell discipline) ─────────────────
        # Closing legs are always processed before opening legs.
        # This matches the adjustment protocol in iron_straddle_v2.py:
        # "Buy legs always execute before sell legs"
        for leg in signal.legs_to_close:
            fill = self._fill_leg(leg, timestamp, is_opening=False)
            fills.append(fill)

        # ── Process opens second ──────────────────────────────────────────────
        for leg in signal.legs_to_open:
            fill = self._fill_leg(leg, timestamp, is_opening=True)
            fills.append(fill)

        return fills

    def _fill_leg(self, leg: OptionsLeg, timestamp: str,
                  is_opening: bool) -> LegFill:
        """
        Look up fill price for a single leg and return a LegFill.

        Args:
            leg        : The OptionsLeg to fill.
            timestamp  : Full ISO timestamp for price lookup.
            is_opening : True if this is an opening fill, False if closing.

        Returns:
            LegFill with status FILLED if price found, REJECTED if not.
        """
        time_str = timestamp[11:16]

        # Get price from cache at this timestamp
        price = self._get_option_price(leg.symbol, timestamp)

        if price <= 0:
            self.logger.warning("No price found for leg",
                                symbol=leg.symbol, timestamp=timestamp)
            return LegFill.rejected(
                leg=leg,
                fill_time=time_str,
                mode=self.mode,
                reason=f"No price data in options_ohlc for {leg.symbol} at {timestamp}",
            )

        if is_opening:
            return LegFill.filled_open(
                leg=leg,
                fill_price=price,
                fill_time=time_str,
                mode=self.mode,
            )
        else:
            return LegFill.filled_close(
                leg=leg,
                fill_price=price,
                fill_time=time_str,
                mode=self.mode,
            )

    def _get_option_price(self, symbol: str, timestamp: str) -> float:
        """
        Get option close price for a symbol at a timestamp.

        Looks up cache first. If exact timestamp not found, tries forward-fill
        (uses the most recent available price before this timestamp).

        Args:
            symbol    : tradingsymbol string.
            timestamp : Full ISO timestamp.

        Returns:
            Close price as float, or 0.0 if not found.
        """
        # Exact timestamp match (most common case)
        if timestamp in self._option_price_cache:
            price = self._option_price_cache[timestamp].get(symbol, 0.0)
            if price > 0:
                return price

        # Forward-fill fallback — find most recent price before this timestamp
        for ts in reversed(self._timestamps):
            if ts <= timestamp:
                price = self._option_price_cache.get(ts, {}).get(symbol, 0.0)
                if price > 0:
                    self.logger.debug("Forward-fill used",
                                      symbol=symbol,
                                      requested=timestamp,
                                      used=ts)
                    return price

        return 0.0

    def get_spot_price(self, time_str: str) -> float:
        """
        Get spot close price at a given time.

        Args:
            time_str : Time as 'HH:MM'. e.g. '09:30'

        Returns:
            Spot close price or 0.0 if not available.
        """
        return self._spot_cache.get(time_str, 0.0)

    # ==========================================================================
    # SYMBOL RESOLUTION
    # ==========================================================================

    def find_symbol(self, strike: int, option_type: str) -> Optional[str]:
        """
        Find the tradingsymbol for a given strike and option type.

        Queries the loaded cache first, falls back to DB if needed.
        Equivalent to find_symbol() in iron_straddle_v2.py.

        Args:
            strike      : Strike price as integer. e.g. 22850
            option_type : 'CE' or 'PE'

        Returns:
            tradingsymbol string or None if not found.
        """
        # Search cache first
        for ts in self._option_price_cache:
            for sym in self._option_price_cache[ts]:
                meta = self._get_symbol_meta(sym)
                if meta:
                    if (int(meta["strike"]) == strike and
                            meta["option_type"] == option_type):
                        return sym

        # Fallback to DB query
        rows = db.query(
            """SELECT tradingsymbol FROM options_ohlc
               WHERE expiry = ? AND strike = ? AND option_type = ?
               LIMIT 1""",
            [self.expiry, float(strike), option_type]
        )
        return rows[0]["tradingsymbol"] if rows else None

    def get_atm_strike(self, spot: float, strike_step: int = 50) -> int:
        """
        Calculate ATM strike by rounding spot to nearest strike_step.

        Equivalent to the ATM calculation in iron_straddle_v2.py:
            atm_strike = int(round(entry_spot / STRIKE_STEP) * STRIKE_STEP)

        Args:
            spot        : Current spot price.
            strike_step : Strike interval. Default 50 for Nifty.

        Returns:
            ATM strike as integer.
        """
        import math
        return int(math.floor(spot / strike_step + 0.5) * strike_step)

    # ==========================================================================
    # SUMMARY
    # ==========================================================================

    def print_session_summary(self) -> None:
        """Print a summary of loaded data for this backtest session."""
        print(f"\n{'='*60}")
        print(f"  BacktestExecutionHandler — Session Summary")
        print(f"{'='*60}")
        print(f"  Date         : {self.date}")
        print(f"  Expiry       : {self.expiry}")
        print(f"  Spot Symbol  : {self.spot_symbol}")
        print(f"  Timestamps   : {len(self._timestamps)}")
        print(f"  Spot Candles : {len(self._spot_cache)}")
        if self._timestamps:
            print(f"  Time Range   : {self._timestamps[0][11:16]} "
                  f"→ {self._timestamps[-1][11:16]}")
        symbols = set(
            sym for prices in self._option_price_cache.values()
            for sym in prices
        )
        print(f"  Symbols      : {len(symbols)}")
        print(f"{'='*60}\n")

    def __repr__(self) -> str:
        return (f"BacktestExecutionHandler("
                f"date={self.date} expiry={self.expiry} "
                f"loaded={self._data_loaded} "
                f"ticks={len(self._timestamps)})")
