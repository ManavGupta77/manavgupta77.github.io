# ==============================================================================
# MARKET FEEDS — SHOONYA LIVE FEED
# src/market_feeds/live_feeds/shoonya_feed.py
#
# Purpose:
#   Implements AbstractLiveFeed using the Shoonya (Finvasia) NorenAPI WebSocket.
#   Receives sub-second LTP ticks, aggregates them into 1-minute candles,
#   and emits a MarketTick to LiveSession at each minute boundary.
#
# Design decisions:
#   1. CANDLE AGGREGATION
#      Raw ticks arrive sub-second. This feed holds the latest LTP per symbol
#      in _price_buffer. At each minute boundary (detected by HH:MM change),
#      a MarketTick is built from the buffer and emitted via the callback.
#      This matches backtest data granularity (1-min close prices) exactly.
#
#   2. SPOT PRICE FROM WEBSOCKET
#      The NIFTY spot index is subscribed alongside option tokens using
#      "NSE|26000" (INDEX_CONFIG["NIFTY"]["shoonya_token"] = "26000").
#      Spot is updated on every index tick — no REST polling during session.
#
#   3. THREADING
#      NorenApi.start_websocket() spawns its own internal thread.
#      All callbacks (_on_tick, _on_open, _on_close, _on_error) fire on
#      that thread. _price_buffer is protected by threading.Lock.
#      ShoonyaLiveFeed adds NO additional threads — threading is NorenApi's.
#
#   4. BROKER API ACCESS
#      ShoonyaBroker currently exposes only REST methods. This feed accesses
#      the underlying NorenApi object directly via shoonya_broker.api for
#      WebSocket operations (start_websocket, subscribe, unsubscribe).
#
#   5. CONNECT/DISCONNECT IDEMPOTENCY
#      connect() checks _connected before opening WebSocket — safe to call twice.
#      disconnect() checks _connected before unsubscribing — safe to call from
#      any thread (watchdog, main, or KeyboardInterrupt handler).
#
#   6. SUBSCRIPTION FORMAT
#      Shoonya WebSocket format: "EXCHANGE|TOKEN"
#      Examples: "NSE|26000" (spot), "NFO|43215" (option)
#      Caller (LiveSession) builds this list before calling subscribe().
#
#   7. TICK PAYLOAD FORMAT
#      {"t": "tk", "e": "NFO", "tk": "43215", "lp": "130.25", "ts": "09:30:01"}
#      "lp" (last price) is always a STRING — cast to float before use.
#      "ts" is HH:MM:SS IST — not always present, use system time as fallback.
#
# Usage (LiveSession handles this — do not call directly):
#   from market_feeds.live_feeds.shoonya_feed import ShoonyaLiveFeed
#   feed = ShoonyaLiveFeed(instrument="NIFTY", days_to_expiry=6.0)
#   feed.set_tick_callback(session._on_market_tick)
#   feed.connect()
#   feed.subscribe(["NSE|26000", "NFO|43215", ...])
#   # ... session runs ...
#   feed.disconnect()
#
# Sprint: 8B
# Frozen after: integration_test_sprint8b.py passes
# ==============================================================================

import threading
from datetime import datetime
from typing import Callable, Dict, List, Optional

from config_loader.settings import INDEX_CONFIG
from market_feeds.live_feeds.feed_base import AbstractLiveFeed
from strategies.building_blocks.market_tick import MarketTick
from utilities.logger import get_logger

logger = get_logger("shoonya_feed")

# Spot token for NIFTY index — confirmed from INDEX_CONFIG
# "NSE|26000" — subscribe this alongside option tokens to get live spot
_SPOT_TOKEN_MAP: Dict[str, str] = {
    instrument: cfg["shoonya_token"]
    for instrument, cfg in INDEX_CONFIG.items()
}
_SPOT_EXCHANGE_MAP: Dict[str, str] = {
    instrument: cfg["spot_exchange"]
    for instrument, cfg in INDEX_CONFIG.items()
}


class ShoonyaLiveFeed(AbstractLiveFeed):
    """
    Live market data feed via Shoonya (Finvasia) NorenAPI WebSocket.

    Aggregates sub-second LTP ticks into 1-minute MarketTick objects
    and delivers them to LiveSession via a registered callback.

    Thread safety:
        _price_buffer is written by the NorenApi WebSocket thread and
        read during candle emission (also on the WebSocket thread).
        _connected is a plain bool — GIL protects single-assignment reads.
        _lock protects _price_buffer during dict iteration at candle emit.
    """

    def __init__(
        self,
        instrument: str = "NIFTY",
        days_to_expiry: float = 0.0,
    ):
        """
        Args:
            instrument:      Index name — "NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"
            days_to_expiry:  Calendar days from session date to expiry.
                             Passed through to every MarketTick for Greeks calcs.
        """
        if instrument.upper() not in INDEX_CONFIG:
            raise ValueError(
                f"Unknown instrument '{instrument}'. "
                f"Valid: {list(INDEX_CONFIG.keys())}"
            )

        self._instrument      = instrument.upper()
        self._days_to_expiry  = float(days_to_expiry)

        # Spot token for this instrument (e.g. "26000" for NIFTY)
        self._spot_token      = _SPOT_TOKEN_MAP[self._instrument]
        self._spot_exchange   = _SPOT_EXCHANGE_MAP[self._instrument]
        # Full subscription string for the spot index (e.g. "NSE|26000")
        self._spot_sub        = f"{self._spot_exchange}|{self._spot_token}"

        # Connection and subscription state
        self._connected: bool              = False
        self._subscribed_symbols: List[str] = []

        # Tick callback registered by LiveSession
        self._tick_callback: Optional[Callable[[MarketTick], None]] = None

        # Price buffer: maps subscription token string -> latest float LTP
        # e.g. {"43215": 130.25, "43216": 115.50, "26000": 25977.20}
        self._price_buffer: Dict[str, float] = {}
        self._spot: float = 0.0

        # Minute boundary tracking — detect when HH:MM changes
        self._current_minute: str = ""

        # Thread lock protecting _price_buffer during candle emission
        self._lock = threading.Lock()

        # Underlying NorenApi object — accessed directly for WebSocket ops
        # Import here (not at module level) to avoid eager broker auth
        # at import time when running tests that don't need a live connection.
        self._api = None

        logger.info(
            "ShoonyaLiveFeed initialised",
            instrument=self._instrument,
            days_to_expiry=self._days_to_expiry,
            spot_sub=self._spot_sub,
        )

    # ------------------------------------------------------------------
    # AbstractLiveFeed: identity
    # ------------------------------------------------------------------

    @property
    def feed_name(self) -> str:
        return "ShoonyaLiveFeed"

    # ------------------------------------------------------------------
    # AbstractLiveFeed: lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Open the Shoonya WebSocket. Authenticates via existing ShoonyaBroker session.
        Returns True if connected successfully, False on any error.
        Idempotent — safe to call on an already-connected feed.
        """
        if self._connected:
            logger.info("Already connected — skipping", feed=self.feed_name)
            return True

        try:
            # Import broker singleton here — deferred to avoid eager TOTP auth
            # during module import in test environments
            from broker_gateway.broker_shoonya.connector import broker as shoonya_broker

            if not shoonya_broker.is_connected():
                logger.error(
                    "ShoonyaBroker session not valid — cannot open WebSocket",
                    feed=self.feed_name,
                )
                return False

            # Access the underlying NorenApi object for WebSocket operations
            self._api = shoonya_broker.api

            # Register WebSocket callbacks and open the connection.
            # start_websocket() spawns an internal thread — all callbacks
            # fire on that thread, NOT the main thread.
            self._api.start_websocket(
                order_update_callback = self._on_order_update,
                subscribe_callback    = self._on_tick,
                socket_open_callback  = self._on_open,
                socket_close_callback = self._on_close,
                socket_error_callback = self._on_error,
            )

            # _on_open sets _connected = True asynchronously.
            # Block briefly to give the socket time to handshake.
            # LiveSession's connect() call does not need to be instant —
            # the session will call subscribe() after this returns.
            import time
            deadline = time.time() + 10.0          # 10 second timeout
            while not self._connected and time.time() < deadline:
                time.sleep(0.1)

            if self._connected:
                logger.info(
                    "WebSocket connected",
                    feed=self.feed_name,
                    instrument=self._instrument,
                )
            else:
                logger.error(
                    "WebSocket did not connect within 10 seconds",
                    feed=self.feed_name,
                )

            return self._connected

        except Exception as e:
            logger.error("connect() failed", feed=self.feed_name, error=str(e))
            self._connected = False
            return False

    def disconnect(self) -> None:
        """
        Unsubscribe all symbols and close the WebSocket connection.
        Safe to call multiple times. Safe to call from any thread.
        After disconnect(), is_connected() returns False.
        """
        if not self._connected:
            return

        try:
            if self._subscribed_symbols and self._api:
                try:
                    self._api.unsubscribe(self._subscribed_symbols)
                except Exception as e:
                    logger.warning(
                        "unsubscribe error during disconnect",
                        feed=self.feed_name,
                        error=str(e),
                    )

            # NorenApi does not expose an explicit close() — setting
            # _connected = False is sufficient for our purposes.
            # The WebSocket thread will terminate when the process exits
            # or when the server closes the connection.
            self._connected = False
            self._subscribed_symbols = []

            logger.info("Disconnected", feed=self.feed_name)

        except Exception as e:
            logger.error("disconnect() error", feed=self.feed_name, error=str(e))
            self._connected = False

    def is_connected(self) -> bool:
        """
        Returns True if the WebSocket is open and receiving data.
        Called by LiveSession watchdog every 10 seconds.
        Does not block. Does not raise.
        """
        return self._connected

    # ------------------------------------------------------------------
    # AbstractLiveFeed: subscription
    # ------------------------------------------------------------------

    def subscribe(self, symbols: List[str]) -> None:
        """
        Subscribe to a list of instrument tokens.
        symbols: list of "EXCHANGE|TOKEN" strings — e.g. ["NSE|26000", "NFO|43215"]

        The spot index subscription ("NSE|26000" for NIFTY) should be included
        in this list by LiveSession so the feed can track spot price from the
        WebSocket without REST polling.

        Raises RuntimeError if called before connect() succeeds.
        """
        if not self._connected:
            raise RuntimeError(
                "subscribe() called before connect(). "
                "Call connect() first and verify is_connected() == True."
            )
        if not symbols:
            logger.warning("subscribe() called with empty symbol list", feed=self.feed_name)
            return

        try:
            self._api.subscribe(symbols)
            self._subscribed_symbols = list(symbols)
            logger.info(
                "Subscribed",
                feed=self.feed_name,
                count=len(symbols),
                symbols=symbols,
            )
        except Exception as e:
            logger.error(
                "subscribe() failed",
                feed=self.feed_name,
                error=str(e),
            )

    def unsubscribe(self, symbols: List[str]) -> None:
        """
        Unsubscribe from a list of instrument tokens.
        Safe to call with symbols never subscribed (no-op for those).
        Called by LiveSession.stop() before disconnect().
        """
        if not self._connected or not self._api:
            return
        if not symbols:
            return

        try:
            self._api.unsubscribe(symbols)
            self._subscribed_symbols = [
                s for s in self._subscribed_symbols if s not in symbols
            ]
            logger.info(
                "Unsubscribed",
                feed=self.feed_name,
                count=len(symbols),
            )
        except Exception as e:
            logger.warning(
                "unsubscribe() error",
                feed=self.feed_name,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # AbstractLiveFeed: callback registration
    # ------------------------------------------------------------------

    def set_tick_callback(self, fn: Callable[[MarketTick], None]) -> None:
        """
        Register the function LiveSession will call on each 1-minute candle.
        fn is called on the NorenApi WebSocket thread — keep it fast.
        Must be called before connect().
        """
        self._tick_callback = fn
        logger.info("Tick callback registered", feed=self.feed_name)

    # ------------------------------------------------------------------
    # Internal: NorenApi WebSocket callbacks
    # ------------------------------------------------------------------

    def _on_open(self) -> None:
        """Called by NorenApi when the WebSocket handshake completes."""
        self._connected = True
        logger.info("WebSocket open", feed=self.feed_name)

    def _on_close(self) -> None:
        """Called by NorenApi when the WebSocket connection closes."""
        self._connected = False
        logger.warning("WebSocket closed", feed=self.feed_name)

    def _on_error(self, message: str) -> None:
        """Called by NorenApi on WebSocket error."""
        logger.error("WebSocket error", feed=self.feed_name, message=str(message))

    def _on_order_update(self, message: dict) -> None:
        """Called by NorenApi on order status updates. Not used by this feed."""
        pass

    def _on_tick(self, tick: dict) -> None:
        """
        Called by NorenApi on every incoming LTP tick.
        Fires on the NorenApi WebSocket thread.

        tick dict format:
            {"t": "tk", "e": "NFO", "tk": "43215", "lp": "130.25", "ts": "09:30:01"}

        Logic:
            1. Extract token, exchange, and last price from tick dict.
            2. If this is the spot index token, update _spot.
            3. Otherwise update _price_buffer[token] with latest LTP.
            4. Extract HH:MM from tick timestamp (or system clock as fallback).
            5. If HH:MM has advanced from _current_minute, emit a MarketTick
               for the completed minute and update _current_minute.
        """
        try:
            # Only process quote ticks (t="tk"), ignore depth (t="df")
            if tick.get("t") != "tk":
                return

            token    = tick.get("tk", "")
            exchange = tick.get("e", "")
            lp_raw   = tick.get("lp")

            if not token or lp_raw is None:
                return

            try:
                lp = float(lp_raw)
            except (ValueError, TypeError):
                return

            # --- Update spot or option price buffer ---

            if token == self._spot_token and exchange == self._spot_exchange:
                # This is the spot index tick (e.g. NSE|26000 for NIFTY)
                self._spot = lp
            else:
                # Option tick — update price buffer under lock
                with self._lock:
                    self._price_buffer[token] = lp

            # --- Detect minute boundary ---

            # Extract HH:MM from tick timestamp field if present,
            # otherwise fall back to current system time (IST assumed).
            ts_field = tick.get("ts", "")
            if ts_field and len(ts_field) >= 5:
                # ts format: "HH:MM:SS"
                hhmm = ts_field[:5]
            else:
                hhmm = datetime.now().strftime("%H:%M")

            if not self._current_minute:
                # First tick of the session — initialise minute tracker
                self._current_minute = hhmm
                return

            if hhmm != self._current_minute:
                # Minute has advanced — emit a candle for the completed minute
                self._emit_candle(self._current_minute)
                self._current_minute = hhmm

        except Exception as e:
            # Never let a tick processing error crash the WebSocket thread
            logger.error(
                "Tick processing error",
                feed=self.feed_name,
                error=str(e),
                tick=str(tick)[:120],
            )

    # ------------------------------------------------------------------
    # Internal: candle emission
    # ------------------------------------------------------------------

    def _emit_candle(self, minute: str) -> None:
        """
        Build a MarketTick from the current price buffer and emit it
        via the registered callback.

        Called from _on_tick() on the WebSocket thread when HH:MM advances.
        minute: the completed minute string e.g. "09:30"

        The MarketTick.option_prices dict uses the subscription token as key
        (e.g. "43215") — LiveSession maps tokens back to full symbols before
        passing to PaperExecutionHandler. This matches the pattern established
        by TickReplayFeed which uses full trading symbols as keys.

        IMPORTANT: To maintain full symbol compatibility with PaperExecutionHandler
        and BacktestExecutionHandler (which use tradingsymbol strings as keys),
        LiveSession is responsible for building the token->symbol reverse map
        and translating before calling handler.ingest_tick(). The feed emits
        token-keyed prices — translation happens in LiveSession, not here.
        """
        if self._tick_callback is None:
            return

        if self._spot <= 0.0:
            # No spot price yet — cannot build a valid tick
            logger.warning(
                "Candle suppressed — spot price not yet received",
                feed=self.feed_name,
                minute=minute,
            )
            return

        # Snapshot the price buffer under lock
        with self._lock:
            option_prices = dict(self._price_buffer)

        if not option_prices:
            # No option prices yet — too early in session
            return

        # Build ISO-style timestamp consistent with backtest format
        # e.g. "2026-02-11T09:30:00+05:30"
        today = datetime.now().strftime("%Y-%m-%d")
        timestamp_iso = f"{today}T{minute}:00+05:30"

        tick = MarketTick(
            timestamp    = timestamp_iso,
            spot         = self._spot,
            option_prices = option_prices,
            days_to_expiry = self._days_to_expiry,
        )

        logger.info(
            "Candle emitted",
            feed=self.feed_name,
            minute=minute,
            spot=self._spot,
            symbols=len(option_prices),
        )

        try:
            self._tick_callback(tick)
        except Exception as e:
            logger.error(
                "Tick callback raised an exception",
                feed=self.feed_name,
                minute=minute,
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Public read-only properties (used by tests and LiveSession)
    # ------------------------------------------------------------------

    @property
    def instrument(self) -> str:
        """Index name this feed is tracking (e.g. 'NIFTY')."""
        return self._instrument

    @property
    def days_to_expiry(self) -> float:
        """Days to expiry passed at construction."""
        return self._days_to_expiry

    @property
    def spot(self) -> float:
        """Latest received spot price. 0.0 until first spot tick arrives."""
        return self._spot

    @property
    def spot_subscription(self) -> str:
        """Full subscription string for the spot index (e.g. 'NSE|26000')."""
        return self._spot_sub

    @property
    def subscribed_symbols(self) -> List[str]:
        """List of currently subscribed instrument strings."""
        return list(self._subscribed_symbols)

    def __repr__(self) -> str:
        return (
            f"ShoonyaLiveFeed("
            f"instrument={self._instrument!r}, "
            f"connected={self._connected}, "
            f"spot={self._spot:.2f}, "
            f"subscribed={len(self._subscribed_symbols)})"
        )
