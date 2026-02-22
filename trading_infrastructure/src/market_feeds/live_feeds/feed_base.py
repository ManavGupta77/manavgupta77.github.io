# ==============================================================================
# MARKET FEEDS — ABSTRACT LIVE FEED
# src/market_feeds/live_feeds/feed_base.py
#
# Purpose:
#   Defines the interface contract that every live feed must implement.
#   ShoonyaLiveFeed (Sprint 8B) and any future feed (AngelLiveFeed, etc.)
#   must subclass AbstractLiveFeed and implement all abstract methods.
#
# Design decisions:
#   - Pure ABC with no state, no imports from broker_gateway, no threading.
#   - set_tick_callback() registers the function LiveSession will provide.
#     The callback signature is: fn(tick: MarketTick) -> None
#   - subscribe() and unsubscribe() accept lists of "EXCHANGE|TOKEN" strings
#     e.g. ["NSE|26000", "NFO|43215"] — Shoonya WebSocket subscription format.
#   - feed_name is an abstract property so each feed self-identifies in logs.
#   - TickReplayFeed (Sprint 8A, FROZEN) does NOT implement this interface.
#     LiveSession detects replay mode via hasattr(feed, 'get_ticks').
#
# Usage:
#   from market_feeds.live_feeds.feed_base import AbstractLiveFeed
#
# Sprint: 8B
# Frozen after: integration_test_sprint8b.py passes
# ==============================================================================

from abc import ABC, abstractmethod
from typing import Callable, List

from strategies.building_blocks.market_tick import MarketTick


class AbstractLiveFeed(ABC):
    """
    Interface contract for all live market data feeds.

    Subclasses must implement every abstract method and property.
    LiveSession accepts any AbstractLiveFeed implementor — it never
    references ShoonyaLiveFeed or any concrete class directly.
    """

    # ------------------------------------------------------------------
    # Abstract property — identity
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def feed_name(self) -> str:
        """
        Human-readable name of this feed.
        Used in logs and LiveSessionResult.feed_name.
        Example: "ShoonyaLiveFeed", "AngelLiveFeed"
        """

    # ------------------------------------------------------------------
    # Abstract methods — lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to the data source.
        For WebSocket feeds: open the socket and authenticate.
        Returns True if connected successfully, False otherwise.
        Must be idempotent — calling connect() on an already-connected
        feed should return True without reconnecting.
        """

    @abstractmethod
    def disconnect(self) -> None:
        """
        Cleanly close the connection and release resources.
        Must be safe to call multiple times (idempotent).
        Must be safe to call from any thread.
        After disconnect(), is_connected() must return False.
        """

    @abstractmethod
    def is_connected(self) -> bool:
        """
        Return True if the feed is currently connected and receiving data.
        Called by the LiveSession watchdog every 10 seconds.
        Must not block. Must not raise.
        """

    # ------------------------------------------------------------------
    # Abstract methods — subscription
    # ------------------------------------------------------------------

    @abstractmethod
    def subscribe(self, symbols: List[str]) -> None:
        """
        Subscribe to a list of instrument tokens.
        symbols: list of "EXCHANGE|TOKEN" strings
                 e.g. ["NSE|26000", "NFO|43215", "NFO|43216"]
        Must be called after connect() succeeds.
        Calling subscribe() before connect() is a caller error — implementations
        may raise RuntimeError or silently no-op; document which in subclass.
        """

    @abstractmethod
    def unsubscribe(self, symbols: List[str]) -> None:
        """
        Unsubscribe from a list of instrument tokens.
        symbols: same format as subscribe().
        Safe to call with symbols that were never subscribed (no-op for those).
        Called by LiveSession.stop() before disconnect().
        """

    # ------------------------------------------------------------------
    # Abstract method — callback registration
    # ------------------------------------------------------------------

    @abstractmethod
    def set_tick_callback(self, fn: Callable[[MarketTick], None]) -> None:
        """
        Register the function LiveSession will call on each completed 1-minute candle.

        fn: callable that accepts a single MarketTick argument.
            Signature: fn(tick: MarketTick) -> None

        The feed calls fn once per minute when a new 1-minute candle closes.
        Sub-second LTP ticks are aggregated internally — only the close price
        at each minute boundary is emitted as a MarketTick.

        fn is called on the WebSocket thread (for live feeds) or the main thread
        (for replay feeds). Implementations must document which thread fn runs on.

        Must be called before connect() so the callback is registered before
        any ticks can arrive.
        """
