# ==============================================================================
# MARKET_FEEDS / LIVE_FEEDS / TICK_REPLAY.PY
# ==============================================================================
# Sprint 8A — TickReplayFeed
#
# Replays historical 1-min candles from SQLite as if they were a live tick
# stream. This is the ACCURACY BRIDGE: it proves that
#
#   PaperExecutionHandler + TickReplayFeed ≡ BacktestExecutionHandler
#
# by reproducing the Rs.-932.75 benchmark through the paper path.
#
# HOW IT WORKS:
#   1. Internally creates a BacktestExecutionHandler to load SQLite data
#   2. Iterates every timestamp, calling build_tick() to assemble MarketTicks
#   3. Transfers all ticks + symbol mappings into a PaperExecutionHandler
#      via preload_handler()
#   4. MarketSession.run() then drives the paper handler identically to
#      how it drives a backtest handler
#
# WHY USE BACKTEST HANDLER INTERNALLY:
#   BacktestExecutionHandler already has all the SQLite query logic, cache
#   management, symbol resolution, and Greeks computation. Duplicating that
#   would be error-prone and violate DRY. TickReplayFeed is a thin adapter
#   that converts BacktestHandler's pull-based data into PaperHandler's
#   buffer-based data.
#
# PHASE 8B NOTE:
#   TickReplayFeed will implement AbstractLiveFeed in Sprint 8B so that
#   LiveSession can use it interchangeably with ShoonyaLiveFeed. For now
#   it is standalone — used only by the Phase 8A integration test with
#   MarketSession.
#
# USAGE:
#   from market_feeds.live_feeds.tick_replay import TickReplayFeed
#   from execution import PaperExecutionHandler
#
#   feed = TickReplayFeed(
#       date="2026-02-11",
#       expiry="2026-02-17",
#       strikes=[25800, 26000, 26200],
#   )
#   ok = feed.load()       # Loads from SQLite via BacktestExecutionHandler
#   assert ok
#
#   handler = PaperExecutionHandler(date="2026-02-11", expiry="2026-02-17")
#   feed.preload_handler(handler)   # Transfers all ticks + symbols
#
#   # Now use handler with MarketSession as normal
#   session = MarketSession(date="2026-02-11", expiry="2026-02-17")
#   session.add_strategy(strategy, handler, strikes, ...)
#   results = session.run()
# ==============================================================================

from __future__ import annotations

import time as time_module
import logging
from datetime import datetime
from typing import List, Optional, Callable, Dict

from utilities.logger import get_logger

logger = logging.getLogger("tick_replay")


class TickReplayFeed:
    """
    Replays historical tick data from SQLite for accuracy testing.

    Uses BacktestExecutionHandler internally to load data and build
    MarketTick objects, then transfers them into a PaperExecutionHandler.
    This guarantees that the tick content is bit-for-bit identical to
    what BacktestHandler produces.

    Args:
        date    : Session date as 'YYYY-MM-DD'.
        expiry  : Weekly expiry date as 'YYYY-MM-DD'.
        strikes : List of strike prices to load.
        speed   : Delay between ticks in seconds during replay().
                  0.0 = instant (for tests). 0.5 = visual debugging.
    """

    def __init__(
        self,
        date:    str,
        expiry:  str,
        strikes: List[int],
        speed:   float = 0.0,
    ) -> None:
        self.date    = date
        self.expiry  = expiry
        self.strikes = strikes
        self.speed   = speed
        self.logger  = get_logger("tick_replay")

        # ── Internal State ───────────────────────────────────────────────────
        self._ticks:      list = []         # List[MarketTick] in timestamp order
        self._timestamps: List[str] = []    # ISO timestamps in order
        self._symbol_map: Dict[tuple, str] = {}   # (strike, opt_type) → symbol
        self._spot_cache: Dict[str, float] = {}   # HH:MM → spot price
        self._loaded:     bool = False
        self._days_to_expiry: float = 7.0

    # ======================================================================
    # LOADING
    # ======================================================================

    def load(self) -> bool:
        """
        Load historical data from SQLite and build all MarketTick objects.

        Internally creates a BacktestExecutionHandler, loads data, and
        iterates every timestamp to build ticks. Also resolves symbol
        mappings for all loaded strikes.

        Returns:
            True if data loaded successfully, False if no data found.
        """
        # Import here to avoid circular imports at module level
        from execution import BacktestExecutionHandler

        self.logger.info(
            "Loading replay data",
            date=self.date,
            expiry=self.expiry,
            strikes=self.strikes,
        )

        # ── Create internal backtest handler and load data ───────────────
        backtest = BacktestExecutionHandler(self.date, self.expiry)
        ok = backtest.load_data(self.strikes)
        if not ok:
            self.logger.error(
                "BacktestExecutionHandler failed to load data",
                date=self.date,
                expiry=self.expiry,
                strikes=self.strikes,
            )
            return False

        # ── Compute days-to-expiry (matches MarketSession logic) ─────────
        self._days_to_expiry = self._compute_days_to_expiry(
            self.date, self.expiry
        )

        # ── Build all ticks via backtest handler ─────────────────────────
        all_timestamps = backtest.get_timestamps()
        self._ticks = []
        self._timestamps = []

        for ts_iso in all_timestamps:
            tick = backtest.build_tick(
                timestamp=ts_iso,
                expiry_date=self.expiry,
                days_to_expiry=self._days_to_expiry,
            )
            if tick is not None:
                self._ticks.append(tick)
                self._timestamps.append(ts_iso)

        if not self._ticks:
            self.logger.error("No ticks built from loaded data")
            return False

        # ── Resolve symbol mappings ──────────────────────────────────────
        self._symbol_map = {}
        for strike in self.strikes:
            for opt_type in ("CE", "PE"):
                sym = backtest.find_symbol(strike, opt_type)
                if sym:
                    self._symbol_map[(int(strike), opt_type)] = sym

        # ── Copy spot cache ──────────────────────────────────────────────
        self._spot_cache = dict(backtest._spot_cache)

        self._loaded = True
        self.logger.info(
            "Replay data loaded",
            ticks=len(self._ticks),
            symbols=len(self._symbol_map),
            spot_candles=len(self._spot_cache),
            days_to_expiry=self._days_to_expiry,
            time_range=(
                f"{self._timestamps[0][11:16]}->{self._timestamps[-1][11:16]}"
                if self._timestamps else "empty"
            ),
        )
        return True

    # ======================================================================
    # HANDLER PRELOADING  (Phase 8A — batch transfer to PaperHandler)
    # ======================================================================

    def preload_handler(self, handler) -> None:
        """
        Transfer all ticks, symbol mappings, and spot data into a
        PaperExecutionHandler so that MarketSession can drive it.

        Must call load() first. Raises RuntimeError if not loaded.

        Args:
            handler : PaperExecutionHandler instance to populate.
        """
        if not self._loaded:
            raise RuntimeError(
                "TickReplayFeed.preload_handler() called before load(). "
                "Call load() first."
            )

        # ── Ingest all ticks ─────────────────────────────────────────────
        for tick in self._ticks:
            handler.ingest_tick(tick)

        # ── Register symbol mappings ─────────────────────────────────────
        for (strike, opt_type), symbol in self._symbol_map.items():
            handler.register_symbol(strike, opt_type, symbol)

        self.logger.info(
            "Handler preloaded",
            ticks=len(self._ticks),
            symbols=len(self._symbol_map),
        )

    # ======================================================================
    # REPLAY  (Phase 8B — callback-based for LiveSession)
    # ======================================================================

    def replay(self, callback: Callable) -> None:
        """
        Replay all ticks, calling callback(tick) for each.

        Used in Phase 8B when TickReplayFeed is wired to LiveSession:
            feed.replay(on_tick=live_session._on_tick)

        For Phase 8A with MarketSession, use preload_handler() instead.

        Args:
            callback : Callable that accepts a MarketTick.
                       Typically LiveSession._on_tick().
        """
        if not self._loaded:
            raise RuntimeError(
                "TickReplayFeed.replay() called before load(). "
                "Call load() first."
            )

        self.logger.info(
            "Replay starting",
            ticks=len(self._ticks),
            speed=self.speed,
        )

        for tick in self._ticks:
            callback(tick)
            if self.speed > 0:
                time_module.sleep(self.speed)

        self.logger.info("Replay complete", ticks=len(self._ticks))

    # ======================================================================
    # ACCESSORS
    # ======================================================================

    def get_ticks(self) -> list:
        """Return all loaded MarketTick objects in timestamp order."""
        return list(self._ticks)

    def get_timestamps(self) -> List[str]:
        """Return all ISO timestamps in order."""
        return list(self._timestamps)

    def get_symbol_map(self) -> Dict[tuple, str]:
        """Return the (strike, opt_type) → symbol mapping dict."""
        return dict(self._symbol_map)

    def get_spot_cache(self) -> Dict[str, float]:
        """Return the HH:MM → spot price mapping dict."""
        return dict(self._spot_cache)

    @property
    def tick_count(self) -> int:
        """Number of ticks loaded."""
        return len(self._ticks)

    @property
    def days_to_expiry(self) -> float:
        """Computed days-to-expiry used for tick building."""
        return self._days_to_expiry

    @property
    def is_loaded(self) -> bool:
        """True if load() has been called successfully."""
        return self._loaded

    # ======================================================================
    # PRIVATE HELPERS
    # ======================================================================

    @staticmethod
    def _compute_days_to_expiry(date: str, expiry: str) -> float:
        """
        Calendar days from session date to expiry. Minimum 0.5.

        Matches MarketSession._compute_days_to_expiry() exactly.
        """
        try:
            d1   = datetime.strptime(date,   "%Y-%m-%d")
            d2   = datetime.strptime(expiry, "%Y-%m-%d")
            days = max((d2 - d1).days, 0)
            return max(float(days), 0.5)
        except Exception:
            return 7.0

    # ======================================================================
    # DISPLAY
    # ======================================================================

    def print_summary(self) -> None:
        """Print a summary of loaded replay data."""
        print(f"\n{'='*60}")
        print(f"  TickReplayFeed — Summary")
        print(f"{'='*60}")
        print(f"  Date          : {self.date}")
        print(f"  Expiry        : {self.expiry}")
        print(f"  Strikes       : {self.strikes}")
        print(f"  Days to Exp   : {self._days_to_expiry}")
        print(f"  Ticks Loaded  : {len(self._ticks)}")
        print(f"  Spot Candles  : {len(self._spot_cache)}")
        if self._timestamps:
            print(f"  Time Range    : {self._timestamps[0][11:16]} "
                  f"-> {self._timestamps[-1][11:16]}")
        print(f"  Symbols       : {len(self._symbol_map)}")
        for (strike, opt_type), sym in sorted(self._symbol_map.items()):
            print(f"    ({strike}, {opt_type}) -> {sym}")
        print(f"  Speed         : {self.speed}s per tick")
        print(f"{'='*60}\n")

    def __repr__(self) -> str:
        return (
            f"TickReplayFeed("
            f"date={self.date} expiry={self.expiry} "
            f"loaded={self._loaded} "
            f"ticks={len(self._ticks)} "
            f"symbols={len(self._symbol_map)})"
        )
