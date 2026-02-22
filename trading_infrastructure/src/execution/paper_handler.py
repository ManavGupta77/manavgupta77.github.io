# ==============================================================================
# EXECUTION / PAPER_HANDLER.PY
# ==============================================================================
# Sprint 8A — PaperExecutionHandler
#
# Simulates order fills against a buffered tick stream. Implements the
# SAME public interface as BacktestExecutionHandler so that MarketSession,
# strategies, and PositionBook cannot tell the difference.
#
# DATA SOURCE:
#   BacktestExecutionHandler pulls prices from SQLite (options_ohlc).
#   PaperExecutionHandler receives prices via ingest_tick() from:
#     - TickReplayFeed (Sprint 8A — SQLite replay for accuracy proof)
#     - ShoonyaLiveFeed (Sprint 8B — live WebSocket)
#     - Any AbstractLiveFeed implementation
#
# FILL PRICE RULE:
#   Uses the CLOSE price from the MarketTick's option_prices dict at the
#   signal timestamp — identical to BacktestExecutionHandler._get_option_price().
#   Forward-fill fallback is implemented to match BacktestHandler's behaviour
#   when a symbol is missing from a specific tick.
#
# ACCURACY CONTRACT:
#   When fed identical tick data (via TickReplayFeed replaying SQLite),
#   PaperExecutionHandler MUST produce the same fill prices as
#   BacktestExecutionHandler. The Rs.-932.75 benchmark is the proof.
#
# SAFETY:
#   This handler NEVER places real orders. The execute() method creates
#   LegFill objects with mode=PAPER. There is no code path to any broker
#   order placement API.
#
# USAGE (Phase 8A — replay accuracy proof):
#   from market_feeds.live_feeds.tick_replay import TickReplayFeed
#   from execution import PaperExecutionHandler
#
#   feed = TickReplayFeed(date="2026-02-11", expiry="2026-02-17",
#                         strikes=[25800, 26000, 26200])
#   feed.load()
#
#   handler = PaperExecutionHandler(date="2026-02-11", expiry="2026-02-17")
#   feed.preload_handler(handler)
#
#   session = MarketSession(date="2026-02-11", expiry="2026-02-17")
#   session.add_strategy(IronStraddleStrategy(), handler, strikes, ...)
#   results = session.run()   # Rs.-932.75 must reproduce
#
# USAGE (Phase 8B — live paper trading):
#   handler = PaperExecutionHandler(date="2026-02-11", expiry="2026-02-17")
#   # LiveSession calls handler.ingest_tick(tick) on each WebSocket event
#   # handler.execute() fills at the current tick's price
# ==============================================================================

from __future__ import annotations

import math
import logging
from typing import List, Optional, Dict

from strategies.building_blocks.options_leg  import OptionsLeg, LegStatus
from strategies.building_blocks.trade_signal import TradeSignal, SignalType
from strategies.building_blocks.leg_fill     import LegFill, FillStatus, ExecutionMode
from strategies.building_blocks.market_tick  import MarketTick, CandleBar

from utilities.logger import get_logger


class PaperExecutionHandler:
    """
    Simulates order execution against a buffered tick stream.

    Fills are instantaneous at the current market price from the tick
    buffer — identical to BacktestExecutionHandler's close-price logic.
    No real orders are ever placed. All fills are logged for audit.

    Public interface matches BacktestExecutionHandler exactly:
        load_data(strikes)                       → bool
        get_timestamps(from_time, to_time)       → List[str]
        build_tick(timestamp, expiry, dte, ...)  → Optional[MarketTick]
        get_spot_price(time_str)                 → float
        find_symbol(strike, option_type)         → Optional[str]
        get_atm_strike(spot, strike_step)        → int
        execute(signal, timestamp)               → List[LegFill]

    Additional paper-specific API:
        ingest_tick(tick)                        → None
        register_symbol(strike, opt_type, sym)   → None
        get_fill_log()                           → List[LegFill]

    Args:
        date        : Trading date as 'YYYY-MM-DD'.
        expiry      : Weekly expiry date as 'YYYY-MM-DD'.
        spot_symbol : Spot index symbol. Default 'NIFTY_INDEX'.
        lot_size    : Lot size for the instrument. Default 65.
    """

    def __init__(
        self,
        date:        str,
        expiry:      str,
        spot_symbol: str = "NIFTY_INDEX",
        lot_size:    int = 65,
    ) -> None:

        self.date        = date
        self.expiry      = expiry
        self.spot_symbol = spot_symbol
        self.lot_size    = lot_size
        self.mode        = ExecutionMode.PAPER
        self.logger      = get_logger("paper_handler")

        # ── Tick Buffer ──────────────────────────────────────────────────────
        # Populated by ingest_tick() or preload_handler().
        # Structure: {iso_timestamp: MarketTick}
        self._tick_buffer: Dict[str, MarketTick] = {}

        # ── Spot Cache ───────────────────────────────────────────────────────
        # Mirrors BacktestExecutionHandler._spot_cache: {HH:MM: close_price}
        self._spot_cache: Dict[str, float] = {}

        # ── Timestamps (sorted) ─────────────────────────────────────────────
        self._timestamps: List[str] = []

        # ── Symbol Mappings ──────────────────────────────────────────────────
        # (strike_int, option_type) → tradingsymbol
        # Populated by register_symbol() or TickReplayFeed.preload_handler()
        self._symbol_map: Dict[tuple, str] = {}

        # ── Fill Log ─────────────────────────────────────────────────────────
        # Every fill ever produced, for post-session audit and reconciliation.
        self._fill_log: List[LegFill] = []

        # ── State ────────────────────────────────────────────────────────────
        self._data_loaded: bool = False

    # ======================================================================
    # TICK INGESTION  (called by TickReplayFeed or LiveSession)
    # ======================================================================

    def ingest_tick(self, tick: MarketTick) -> None:
        """
        Buffer a MarketTick for later consumption by build_tick() and execute().

        In replay mode (Phase 8A): TickReplayFeed calls this for every
        historical tick before MarketSession.run() starts.

        In live mode (Phase 8B): LiveSession calls this once per WebSocket
        tick, just before routing to strategy hooks.

        Args:
            tick : A fully assembled MarketTick (from replay or live feed).
        """
        ts = tick.timestamp
        is_new = ts not in self._tick_buffer
        self._tick_buffer[ts] = tick

        # Update spot cache (keyed by HH:MM for get_spot_price())
        time_str = tick.time_str or ts[11:16]
        if tick.spot > 0:
            self._spot_cache[time_str] = tick.spot

        # Maintain sorted timestamp list (no duplicates)
        if is_new:
            self._timestamps.append(ts)
            # In replay mode ticks arrive in order — sort only if needed
            if (len(self._timestamps) > 1
                    and self._timestamps[-1] < self._timestamps[-2]):
                self._timestamps.sort()

    def register_symbol(
        self,
        strike:      int,
        option_type: str,
        symbol:      str,
    ) -> None:
        """
        Register a (strike, option_type) → tradingsymbol mapping.

        Called by TickReplayFeed.preload_handler() after resolving symbols
        from the backtest data. In live mode, called by LiveSession after
        querying the broker instrument master.

        Args:
            strike      : Strike price as integer (e.g. 26000).
            option_type : 'CE' or 'PE'.
            symbol      : Trading symbol (e.g. 'NIFTY17FEB2626000CE').
        """
        self._symbol_map[(int(strike), option_type)] = symbol

    # ======================================================================
    # DATA LOADING  (interface match: BacktestExecutionHandler.load_data)
    # ======================================================================

    def load_data(self, strikes: List[int]) -> bool:
        """
        Signal that all pre-load data is ready.

        In BacktestExecutionHandler, this queries SQLite and populates caches.
        In PaperExecutionHandler, data arrives via ingest_tick() before this
        call. load_data() simply verifies that ticks have been buffered.

        MarketSession.run() calls this for every handler. Returning True
        allows the session to proceed.

        Args:
            strikes : Strike list (used by backtest; ignored here since
                      tick data is already ingested).

        Returns:
            True if tick buffer is populated, False otherwise.
        """
        self._data_loaded = len(self._tick_buffer) > 0

        if self._data_loaded:
            self.logger.info(
                "Paper handler ready",
                date=self.date,
                expiry=self.expiry,
                ticks=len(self._timestamps),
                symbols=len(self._symbol_map),
                spot_candles=len(self._spot_cache),
            )
        else:
            self.logger.error(
                "Paper handler has no tick data — call ingest_tick() "
                "or TickReplayFeed.preload_handler() before load_data()",
                date=self.date,
                expiry=self.expiry,
            )

        return self._data_loaded

    # ======================================================================
    # TICK REPLAY  (interface match: BacktestExecutionHandler)
    # ======================================================================

    def get_timestamps(
        self,
        from_time: str = "09:15",
        to_time:   str = "15:25",
    ) -> List[str]:
        """
        Return all buffered timestamps within the given time window.

        Interface match for BacktestExecutionHandler.get_timestamps().
        MarketSession.run() calls this to build the tick iteration loop.

        Args:
            from_time : Start time as 'HH:MM'. Default '09:15'.
            to_time   : End time as 'HH:MM'. Default '15:25'.

        Returns:
            Sorted list of ISO timestamp strings within the window.
        """
        if not self._data_loaded and not self._tick_buffer:
            return []

        return [
            ts for ts in self._timestamps
            if from_time <= ts[11:16] <= to_time
        ]

    def build_tick(
        self,
        timestamp:       str,
        expiry_date:     str,
        days_to_expiry:  float,
        tracked_symbols: List[str] = None,
        compute_greeks:  bool = True,
    ) -> Optional[MarketTick]:
        """
        Return the buffered MarketTick for a given timestamp.

        Interface match for BacktestExecutionHandler.build_tick().
        MarketSession.run() calls this on every tick iteration.

        In BacktestHandler: builds a MarketTick from SQLite cache.
        In PaperHandler: returns the pre-built tick from the buffer.

        Args:
            timestamp       : Full ISO timestamp.
            expiry_date     : Weekly expiry date (used by backtest for Greeks).
            days_to_expiry  : Float days to expiry (used by backtest for Greeks).
            tracked_symbols : Symbol filter (applied if provided).
            compute_greeks  : Greeks flag (tick already has Greeks if computed).

        Returns:
            MarketTick or None if no tick at this timestamp.
        """
        tick = self._tick_buffer.get(timestamp)
        if tick is None:
            return None

        # Apply symbol filter if requested (matches BacktestHandler behaviour)
        if tracked_symbols and tick.option_prices:
            filtered_prices = {
                s: p for s, p in tick.option_prices.items()
                if s in tracked_symbols
            }
            filtered_candles = {
                s: c for s, c in tick.option_candles.items()
                if s in tracked_symbols
            }
            # Return a new tick with filtered data rather than mutating buffer
            return MarketTick(
                timestamp=tick.timestamp,
                time_str=tick.time_str,
                date_str=tick.date_str,
                spot=tick.spot,
                spot_candle=tick.spot_candle,
                option_prices=filtered_prices,
                option_candles=filtered_candles,
                indicators=tick.indicators,
                greeks=tick.greeks,
                tv_signal=tick.tv_signal,
                expiry_date=expiry_date,
                days_to_expiry=days_to_expiry,
                minutes_to_expiry=tick.minutes_to_expiry,
                is_live=tick.is_live,
            )

        return tick

    def get_spot_price(self, time_str: str) -> float:
        """
        Get spot price at a given time.

        Interface match for BacktestExecutionHandler.get_spot_price().
        MarketSession.run() calls this for opening_spot resolution.

        Args:
            time_str : Time as 'HH:MM'. e.g. '09:30'.

        Returns:
            Spot price or 0.0 if not available.
        """
        return self._spot_cache.get(time_str, 0.0)

    # ======================================================================
    # SYMBOL RESOLUTION  (interface match: BacktestExecutionHandler)
    # ======================================================================

    def find_symbol(self, strike: int, option_type: str) -> Optional[str]:
        """
        Find the tradingsymbol for a given strike and option type.

        Interface match for BacktestExecutionHandler.find_symbol().
        Strategies call this in on_market_open() to resolve ATM symbols.

        Args:
            strike      : Strike price as integer (e.g. 26000).
            option_type : 'CE' or 'PE'.

        Returns:
            Trading symbol string or None if not found.
        """
        # Direct lookup from registered symbol map
        result = self._symbol_map.get((int(strike), option_type))
        if result:
            return result

        # Fallback: scan tick buffer for a matching symbol
        # This handles cases where symbols weren't pre-registered
        for tick in self._tick_buffer.values():
            for sym in tick.option_prices:
                if str(int(strike)) in sym and option_type in sym:
                    # Cache it for future lookups
                    self._symbol_map[(int(strike), option_type)] = sym
                    return sym

        return None

    def get_atm_strike(self, spot: float, strike_step: int = 50) -> int:
        """
        Calculate ATM strike by rounding spot to nearest strike_step.

        Interface match for BacktestExecutionHandler.get_atm_strike().
        Identical arithmetic.

        Args:
            spot        : Current spot price.
            strike_step : Strike interval. Default 50 for Nifty.

        Returns:
            ATM strike as integer.
        """
        return int(math.floor(spot / strike_step + 0.5) * strike_step)

    # ======================================================================
    # ORDER EXECUTION  (interface match: BacktestExecutionHandler.execute)
    # ======================================================================

    def execute(
        self,
        signal:    TradeSignal,
        timestamp: str,
    ) -> List[LegFill]:
        """
        Process a TradeSignal and return simulated fills for all legs.

        Interface match for BacktestExecutionHandler.execute().
        MarketSession._execute_and_update() calls this when a strategy
        emits a signal.

        Fill price = option price from the buffered tick at the given
        timestamp. This matches BacktestHandler's close-price logic.

        Closes are processed before opens (buy-before-sell discipline),
        matching BacktestHandler exactly.

        Args:
            signal    : TradeSignal emitted by the strategy.
            timestamp : Full ISO timestamp when the signal was generated.

        Returns:
            List of LegFill objects — one per leg in the signal.
            Rejected fills included if price not found.
        """
        fills: List[LegFill] = []

        # ── Process closes first (buy-before-sell discipline) ────────────
        for leg in signal.legs_to_close:
            fill = self._fill_leg(leg, timestamp, is_opening=False)
            fills.append(fill)

        # ── Process opens second ─────────────────────────────────────────
        for leg in signal.legs_to_open:
            fill = self._fill_leg(leg, timestamp, is_opening=True)
            fills.append(fill)

        return fills

    def _fill_leg(
        self,
        leg:        OptionsLeg,
        timestamp:  str,
        is_opening: bool,
    ) -> LegFill:
        """
        Look up fill price for a single leg from tick buffer and return LegFill.

        Includes forward-fill fallback matching BacktestHandler._get_option_price():
        if the exact timestamp doesn't have a price for this symbol, scan
        backwards through the buffer for the most recent available price.

        Args:
            leg        : The OptionsLeg to fill.
            timestamp  : Full ISO timestamp for price lookup.
            is_opening : True for opening fill, False for closing.

        Returns:
            LegFill with status FILLED or REJECTED.
        """
        time_str = timestamp[11:16]

        # ── Primary: look up price from tick at exact timestamp ──────────
        price = 0.0
        tick = self._tick_buffer.get(timestamp)
        if tick is not None:
            price = tick.option_prices.get(leg.symbol, 0.0)

        # ── Forward-fill fallback (matches BacktestHandler behaviour) ────
        if price <= 0:
            for ts in reversed(self._timestamps):
                if ts <= timestamp:
                    prev_tick = self._tick_buffer.get(ts)
                    if prev_tick is not None:
                        p = prev_tick.option_prices.get(leg.symbol, 0.0)
                        if p > 0:
                            price = p
                            self.logger.debug(
                                "Forward-fill used",
                                symbol=leg.symbol,
                                requested=timestamp,
                                used=ts,
                            )
                            break

        # ── No price found → reject ─────────────────────────────────────
        if price <= 0:
            self.logger.warning(
                "No price found for leg",
                symbol=leg.symbol,
                timestamp=timestamp,
            )
            fill = LegFill.rejected(
                leg=leg,
                fill_time=time_str,
                mode=self.mode,
                reason=f"No price data for {leg.symbol} at {timestamp}",
            )
            self._fill_log.append(fill)
            return fill

        # ── Build fill ───────────────────────────────────────────────────
        if is_opening:
            fill = LegFill.filled_open(
                leg=leg,
                fill_price=price,
                fill_time=time_str,
                mode=self.mode,
            )
        else:
            fill = LegFill.filled_close(
                leg=leg,
                fill_price=price,
                fill_time=time_str,
                mode=self.mode,
            )

        self._fill_log.append(fill)
        return fill

    # ======================================================================
    # PAPER-SPECIFIC API
    # ======================================================================

    def get_fill_log(self) -> List[LegFill]:
        """
        Return all fills produced during this session for audit.

        Every call to execute() appends fills to this log.
        Use for post-session reconciliation and debugging.

        Returns:
            Ordered list of all LegFill objects (opens + closes + rejects).
        """
        return list(self._fill_log)

    def get_fill_count(self) -> int:
        """Number of fills produced (including rejects)."""
        return len(self._fill_log)

    def get_successful_fills(self) -> List[LegFill]:
        """Return only FILLED fills (excluding rejects)."""
        return [f for f in self._fill_log if f.is_filled]

    # ======================================================================
    # SUMMARY
    # ======================================================================

    def print_session_summary(self) -> None:
        """Print a summary of buffered data and fill activity."""
        print(f"\n{'='*60}")
        print(f"  PaperExecutionHandler — Session Summary")
        print(f"{'='*60}")
        print(f"  Date         : {self.date}")
        print(f"  Expiry       : {self.expiry}")
        print(f"  Mode         : PAPER")
        print(f"  Ticks        : {len(self._timestamps)}")
        print(f"  Spot Candles : {len(self._spot_cache)}")
        if self._timestamps:
            print(f"  Time Range   : {self._timestamps[0][11:16]} "
                  f"-> {self._timestamps[-1][11:16]}")
        print(f"  Symbols      : {len(self._symbol_map)}")
        print(f"  Fills        : {len(self._fill_log)} "
              f"({len(self.get_successful_fills())} filled, "
              f"{len(self._fill_log) - len(self.get_successful_fills())} rejected)")
        print(f"{'='*60}\n")

    def __repr__(self) -> str:
        return (
            f"PaperExecutionHandler("
            f"date={self.date} expiry={self.expiry} "
            f"loaded={self._data_loaded} "
            f"ticks={len(self._timestamps)} "
            f"fills={len(self._fill_log)})"
        )
