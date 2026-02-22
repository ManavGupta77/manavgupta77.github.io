# ==============================================================================
# BUILDING BLOCKS / MARKET_TICK.PY
# ==============================================================================
# The standardised data packet delivered to every strategy lifecycle hook.
#
# CORE DESIGN RULE:
#   A strategy never calls a data feed, broker API, or indicator library.
#   It receives a MarketTick and reads whatever it needs from it.
#   The PortfolioCoordinator assembles the tick before passing it down.
#
# WHAT IT CARRIES:
#   - Timestamp and session metadata
#   - Spot price (index level)
#   - Option prices for all tracked instruments (keyed by trading symbol)
#   - Pre-computed technical indicators (EMA, RSI, SuperTrend, etc.)
#   - Pre-computed Greeks (Delta, IV, Gamma) — Black-Scholes in backtest,
#     broker feed in live/paper
#   - TradingView webhook signal if one arrived on this tick
#   - Raw candle OHLCV for the current 1-min bar (for indicator computation)
#
# DATA SOURCE BY MODE:
#   Backtest  → All fields populated from SQLite replay + Black-Scholes
#   Paper     → option_prices from live WebSocket, Greeks from BS or broker
#   Live      → All fields from live broker WebSocket feed
#
# USAGE IN STRATEGY:
#   def on_entry_signal(self, tick: MarketTick):
#       if not tick.is_entry_window(self.config):
#           return None
#       if tick.indicators.get("EMA_9", 0) > tick.indicators.get("EMA_21", 0):
#           if tick.greeks.get("NIFTY24000CE", {}).get("iv", 0) < 15:
#               return TradeSignal.entry(...)
#       if tick.tv_signal == "ENTRY_SHORT":
#           return TradeSignal.entry(...)
# ==============================================================================

from dataclasses import dataclass, field
from typing import Dict, Optional, Any
from datetime import datetime, time as dt_time


# ── IST Market Session Constants ─────────────────────────────────────────────
_MARKET_OPEN  = dt_time(9, 15)
_MARKET_CLOSE = dt_time(15, 30)


@dataclass
class GreekSnapshot:
    """
    Options Greeks for a single contract at a point in time.

    In backtest mode: computed by GreeksCalculator (Black-Scholes).
    In live/paper mode: from broker WebSocket feed if available,
                        else computed by GreeksCalculator as fallback.
    """
    symbol:     str   = ""
    delta:      float = 0.0    # Range: -1.0 to +1.0
    gamma:      float = 0.0    # Rate of delta change per 1-pt spot move
    theta:      float = 0.0    # Daily time decay (negative for buyers)
    vega:       float = 0.0    # Sensitivity to 1% IV change
    iv:         float = 0.0    # Implied Volatility as percentage (e.g. 14.5 = 14.5%)
    intrinsic:  float = 0.0    # Max(spot - strike, 0) for CE; Max(strike - spot, 0) for PE
    time_value: float = 0.0    # Premium - intrinsic value
    source:     str   = "BS"   # "BS" = Black-Scholes calculated, "BROKER" = live feed

    def __repr__(self) -> str:
        return (f"Greeks({self.symbol} Δ={self.delta:.3f} γ={self.gamma:.4f} "
                f"θ={self.theta:.2f} IV={self.iv:.1f}% src={self.source})")


@dataclass
class CandleBar:
    """
    OHLCV data for the current 1-minute bar.
    Carried on every tick for indicator computation and logging.
    In tick-by-tick live mode, open/high/low/close converge as the bar forms.
    """
    open:   float = 0.0
    high:   float = 0.0
    low:    float = 0.0
    close:  float = 0.0
    volume: int   = 0
    oi:     int   = 0      # Open Interest — available in options_ohlc


@dataclass
class MarketTick:
    """
    Standardised data packet delivered to every strategy lifecycle hook.

    Assembled by PortfolioCoordinator before each call to:
      on_entry_signal(), on_adjustment(), on_exit_signal(), on_market_close()

    The strategy reads from this object — it never modifies it.

    Fields:
        timestamp       : Full ISO 8601 timestamp with IST offset.
                          e.g. '2026-02-11T09:30:00+05:30'
        time_str        : Short IST time string for display. e.g. '09:30'
        date_str        : Trading date. e.g. '2026-02-11'
        spot            : Current spot price of the index (e.g. Nifty level).
        spot_candle     : OHLCV bar for the spot index at this timestamp.
        option_prices   : Dict of {trading_symbol: current_close_price}.
                          Keys match OptionsLeg.symbol exactly.
                          e.g. {"NIFTY26FEB24000CE": 145.0, "NIFTY26FEB24000PE": 138.5}
        option_candles  : Dict of {trading_symbol: CandleBar} — full OHLCV per option.
        indicators      : Pre-computed technical indicators on spot data.
                          Keys are indicator names, values are floats or strings.
                          e.g. {"EMA_9": 23100.5, "EMA_21": 23050.0,
                                "RSI_14": 42.5, "SUPERTREND": "BULLISH",
                                "SUPERTREND_LEVEL": 22800.0}
        greeks          : Dict of {trading_symbol: GreekSnapshot}.
                          e.g. {"NIFTY26FEB24000CE": GreekSnapshot(delta=0.52, iv=14.5)}
        tv_signal       : TradingView Pine Script alert string if one arrived
                          on this tick. None if no webhook signal.
                          e.g. "ENTRY_SHORT" or "EXIT_ALL"
        expiry_date     : Current weekly expiry date. e.g. '2026-02-13'
        days_to_expiry  : Calendar days remaining to expiry (float, e.g. 2.5).
        minutes_to_expiry: Minutes remaining to expiry (float).
        is_live         : True if this tick comes from a live/paper feed.
                          False for backtest replay. For logging only —
                          strategies must not branch logic on this flag.
    """

    # ── Core ─────────────────────────────────────────────────────────────────
    timestamp:          str   = ""
    time_str:           str   = ""
    date_str:           str   = ""

    # ── Spot ─────────────────────────────────────────────────────────────────
    spot:               float = 0.0
    spot_candle:        CandleBar = field(default_factory=CandleBar)

    # ── Option Prices & Candles ───────────────────────────────────────────────
    option_prices:      Dict[str, float]     = field(default_factory=dict)
    option_candles:     Dict[str, CandleBar] = field(default_factory=dict)

    # ── Indicators ───────────────────────────────────────────────────────────
    indicators:         Dict[str, Any]       = field(default_factory=dict)

    # ── Greeks ───────────────────────────────────────────────────────────────
    greeks:             Dict[str, GreekSnapshot] = field(default_factory=dict)

    # ── TradingView ──────────────────────────────────────────────────────────
    tv_signal:          Optional[str] = None

    # ── Expiry Metadata ───────────────────────────────────────────────────────
    expiry_date:        str   = ""
    days_to_expiry:     float = 0.0
    minutes_to_expiry:  float = 0.0

    # ── Mode Flag (logging only) ──────────────────────────────────────────────
    is_live:            bool  = False

    # ── Convenience Methods ───────────────────────────────────────────────────

    def get_price(self, symbol: str, default: float = 0.0) -> float:
        """
        Get current price for a trading symbol.

        Safe — returns default (0.0) if symbol not found rather than raising.
        Matches the get_price() helper pattern from iron_straddle_v2.py.

        Usage:
            ce_price = tick.get_price("NIFTY26FEB24000CE")
        """
        return self.option_prices.get(symbol, default)

    def get_greek(self, symbol: str) -> Optional[GreekSnapshot]:
        """
        Get the GreekSnapshot for a trading symbol.

        Returns None if Greeks not available for this symbol.

        Usage:
            g = tick.get_greek("NIFTY26FEB24000CE")
            if g and g.iv < 15:
                ...
        """
        return self.greeks.get(symbol)

    def get_indicator(self, name: str, default: Any = None) -> Any:
        """
        Get a pre-computed indicator value by name.

        Usage:
            ema9  = tick.get_indicator("EMA_9", default=0.0)
            trend = tick.get_indicator("SUPERTREND", default="NEUTRAL")
        """
        return self.indicators.get(name, default)

    def is_entry_window(self, entry_time: str, window_mins: int = 5) -> bool:
        """
        True if this tick falls within the strategy entry window.

        Args:
            entry_time  : Configured entry time as 'HH:MM' (from YAML config).
            window_mins : How many minutes after entry_time to allow entry.
                          e.g. entry_time='09:30', window_mins=5
                          → allows entry between 09:30 and 09:35 inclusive.

        Usage:
            if tick.is_entry_window(self.config['session']['entry_time']):
                return TradeSignal.entry(...)
        """
        if not self.time_str:
            return False
        tick_h, tick_m = map(int, self.time_str.split(":"))
        entry_h, entry_m = map(int, entry_time.split(":"))
        tick_mins  = tick_h * 60 + tick_m
        entry_mins = entry_h * 60 + entry_m
        return entry_mins <= tick_mins <= entry_mins + window_mins

    def is_past_square_off(self, square_off_time: str) -> bool:
        """
        True if this tick is at or past the configured square-off time.

        Usage:
            if tick.is_past_square_off(self.config['session']['square_off_time']):
                return TradeSignal.square_off(...)
        """
        if not self.time_str:
            return False
        tick_h, tick_m   = map(int, self.time_str.split(":"))
        soff_h, soff_m   = map(int, square_off_time.split(":"))
        return (tick_h * 60 + tick_m) >= (soff_h * 60 + soff_m)

    def is_market_hours(self) -> bool:
        """True if this tick falls within regular NSE market hours (09:15–15:30)."""
        if not self.time_str:
            return False
        h, m = map(int, self.time_str.split(":"))
        t = dt_time(h, m)
        return _MARKET_OPEN <= t <= _MARKET_CLOSE

    def combined_premium(self, sell_symbols: list) -> float:
        """
        Sum of current prices for a list of sell-leg symbols.

        Useful for checking combined premium vs SL/target in on_adjustment().

        Usage:
            total = tick.combined_premium(["NIFTY26FEB24000CE", "NIFTY26FEB24000PE"])
            if total >= entry_combined * (1 + SL_PCT):
                return TradeSignal.exit(...)
        """
        return sum(self.get_price(sym) for sym in sell_symbols)

    @classmethod
    def from_backtest_row(cls, timestamp: str, spot: float,
                          option_prices: Dict[str, float],
                          option_candles: Dict[str, CandleBar] = None,
                          indicators: Dict[str, Any] = None,
                          greeks: Dict[str, GreekSnapshot] = None,
                          expiry_date: str = "",
                          days_to_expiry: float = 0.0) -> "MarketTick":
        """
        Construct a MarketTick from a backtest SQLite row.

        Called by BacktestExecutionHandler's tick replay loop.

        Args:
            timestamp    : Full ISO timestamp from SQLite.
                           e.g. '2026-02-11T09:30:00+05:30'
            spot         : Spot close price at this timestamp.
            option_prices: {symbol: close_price} from options_ohlc query.
            option_candles: Full OHLCV per symbol (optional).
            indicators   : Pre-computed indicators dict (optional).
            greeks       : Pre-computed GreekSnapshots (optional).
            expiry_date  : Weekly expiry date string.
            days_to_expiry: Float days to expiry.
        """
        time_str = timestamp[11:16] if len(timestamp) >= 16 else ""
        date_str = timestamp[:10]   if len(timestamp) >= 10 else ""
        mins_to_exp = days_to_expiry * 375.0  # ~375 trading minutes per day

        return cls(
            timestamp=timestamp,
            time_str=time_str,
            date_str=date_str,
            spot=spot,
            option_prices=option_prices,
            option_candles=option_candles or {},
            indicators=indicators or {},
            greeks=greeks or {},
            expiry_date=expiry_date,
            days_to_expiry=days_to_expiry,
            minutes_to_expiry=mins_to_exp,
            is_live=False,
        )

    @classmethod
    def from_live_feed(cls, timestamp: str, spot: float,
                       option_prices: Dict[str, float],
                       indicators: Dict[str, Any] = None,
                       greeks: Dict[str, GreekSnapshot] = None,
                       tv_signal: Optional[str] = None,
                       expiry_date: str = "",
                       days_to_expiry: float = 0.0) -> "MarketTick":
        """
        Construct a MarketTick from a live WebSocket feed event.

        Called by PaperExecutionHandler and LiveExecutionHandler.

        Args:
            timestamp   : IST timestamp string from broker WebSocket.
            spot        : Live spot price.
            option_prices: {symbol: ltp} from broker feed.
            indicators  : Indicators computed on rolling candle buffer.
            greeks      : Greeks from broker feed or BS fallback.
            tv_signal   : TradingView alert string if webhook received.
            expiry_date : Current weekly expiry.
            days_to_expiry: Float days to expiry.
        """
        time_str = timestamp[11:16] if len(timestamp) >= 16 else timestamp[:5]
        date_str = timestamp[:10]   if len(timestamp) >= 10 else ""
        mins_to_exp = days_to_expiry * 375.0

        return cls(
            timestamp=timestamp,
            time_str=time_str,
            date_str=date_str,
            spot=spot,
            option_prices=option_prices,
            indicators=indicators or {},
            greeks=greeks or {},
            tv_signal=tv_signal,
            expiry_date=expiry_date,
            days_to_expiry=days_to_expiry,
            minutes_to_expiry=mins_to_exp,
            is_live=True,
        )

    def __repr__(self) -> str:
        return (f"MarketTick({self.time_str} | spot={self.spot:.1f} | "
                f"options={len(self.option_prices)} | "
                f"indicators={len(self.indicators)} | "
                f"tv={self.tv_signal or 'none'})")
