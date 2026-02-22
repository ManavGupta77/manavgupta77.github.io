# =============================================================================
# INDICATORS / INDICATOR_ENGINE.PY
# =============================================================================
# Sprint 7 — IndicatorEngine
#
# Computes technical and options-market indicators from a MarketTick every
# minute. Produces an IndicatorSnapshot dataclass that strategies can consume
# to make smarter entry/exit/adjustment decisions.
#
# DESIGN PRINCIPLES:
#   - Pure computation: no DB access, no broker calls, no side effects.
#   - Stateful only for rolling calculations (e.g. EMA, rolling PCR).
#     State is reset via reset_day() at market open.
#   - Injected into strategies via inject_indicator_engine() (Sprint 7 hook
#     on BaseStrategy). Strategies that don't call it are unaffected.
#   - All indicators are OPTIONAL within the snapshot. A strategy checks
#     snapshot.pcr_available before using pcr, etc. This makes it safe
#     to use IndicatorEngine even when some symbols aren't loaded.
#   - The engine must NEVER change trade outcomes when a strategy ignores
#     its output — the Rs.-932.75 benchmark must reproduce exactly.
#
# INDICATORS COMPUTED:
#   Spot-derived:
#     - spot_change_pct  : % change from session open spot
#     - spot_vs_atm      : spot - atm_strike (how far OTM/ITM)
#
#   Options-derived (require option_prices dict with CE + PE at ATM):
#     - atm_iv_ce        : Implied volatility for ATM CE (Black-Scholes)
#     - atm_iv_pe        : Implied volatility for ATM PE (Black-Scholes)
#     - atm_iv_avg       : Average of CE and PE IV
#     - atm_premium_ce   : Current ATM CE price from tick
#     - atm_premium_pe   : Current ATM PE price from tick
#     - combined_premium : CE + PE combined premium
#     - premium_decay_pct: % of original entry premium that has decayed
#
#   Put-Call Ratio (rolling, requires both CE + PE prices):
#     - pcr_current      : Instantaneous CE/PE premium ratio
#     - pcr_rolling      : Rolling average PCR over last N ticks (default 5)
#
#   Time-derived:
#     - minutes_since_open  : Minutes elapsed since 09:15
#     - minutes_to_expiry   : Minutes from now to 15:30 on expiry day
#     - time_decay_fraction : 0.0 at open → 1.0 at 15:30 (session fraction)
#
# USAGE:
#   engine = IndicatorEngine(
#       opening_spot        = 25976.05,
#       atm_ce_symbol       = "NIFTY17FEB2626000CE",
#       atm_pe_symbol       = "NIFTY17FEB2626000PE",
#       entry_ce_premium    = 130.3,    # original entry prices for decay calc
#       entry_pe_premium    = 115.5,
#       pcr_window          = 5,        # rolling window in ticks
#   )
#
#   # Called by MarketSession once per tick per strategy slot:
#   snapshot = engine.compute(tick=tick, days_to_expiry=6.0, atm_strike=26000)
#
#   # In IronStraddleStrategy.on_entry_signal():
#   if self._indicator_engine is not None:
#       snap = self._indicator_engine.compute(...)
#       if snap.atm_iv_avg > 15.0:
#           return None   # skip entry when IV too high
#
# SPRINT 9 NOTE:
#   Thresholds (pcr_window, iv_entry_max, etc.) will be YAML-configured.
#   Hard-code sensible defaults here; override via constructor kwargs.
# =============================================================================

from __future__ import annotations

import math
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("indicator_engine")


# ── Constants ──────────────────────────────────────────────────────────────────

TRADING_START_MINUTES = 9 * 60 + 15    # 09:15 in minutes from midnight
TRADING_END_MINUTES   = 15 * 60 + 30   # 15:30 in minutes from midnight
TRADING_MINUTES_TOTAL = TRADING_END_MINUTES - TRADING_START_MINUTES   # 375


# =============================================================================
# Black-Scholes IV solver (bisection method)
# =============================================================================

def _bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    European call option price via Black-Scholes.

    Args:
        S     : Spot price
        K     : Strike price
        T     : Time to expiry in years
        r     : Risk-free rate (annualised, decimal)
        sigma : Volatility (annualised, decimal)

    Returns:
        Theoretical call price. Returns 0.0 on math errors.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _bs_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """European put option price via Black-Scholes (put-call parity)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    try:
        call = _bs_call_price(S, K, T, r, sigma)
        return call - S + K * math.exp(-r * T)
    except Exception:
        return 0.0


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _implied_vol(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str,
    tol: float = 1e-5,
    max_iter: int = 100,
) -> Optional[float]:
    """
    Compute implied volatility via bisection on the Black-Scholes formula.

    Args:
        market_price : Observed market price of the option
        S            : Current spot price
        K            : Strike price
        T            : Time to expiry in years (must be > 0)
        r            : Risk-free rate
        option_type  : 'CE' or 'PE'
        tol          : Convergence tolerance
        max_iter     : Maximum bisection iterations

    Returns:
        Implied volatility as a decimal (0.20 = 20%), or None if it cannot
        be computed (e.g. deep ITM/OTM, zero time, zero price).
    """
    if market_price <= 0 or T <= 0 or S <= 0 or K <= 0:
        return None

    pricer = _bs_call_price if option_type == "CE" else _bs_put_price

    lo, hi = 1e-6, 10.0   # Search between 0.0001% and 1000% vol

    # Check that market_price is within Black-Scholes bounds
    lo_price = pricer(S, K, T, r, lo)
    hi_price = pricer(S, K, T, r, hi)
    if market_price < lo_price or market_price > hi_price:
        return None

    for _ in range(max_iter):
        mid       = (lo + hi) / 2.0
        mid_price = pricer(S, K, T, r, mid)

        if abs(mid_price - market_price) < tol:
            return mid

        if mid_price < market_price:
            lo = mid
        else:
            hi = mid

    return (lo + hi) / 2.0   # Best estimate after max_iter


# =============================================================================
# IndicatorSnapshot — one snapshot per tick per strategy
# =============================================================================

@dataclass
class IndicatorSnapshot:
    """
    All computed indicators for a single market tick.

    All fields are Optional — strategies must guard with None checks.
    Fields are None when computation was not possible (missing prices,
    zero time, first tick before history builds up, etc.).

    Attributes:
        timestamp          : 'HH:MM' of the tick this snapshot belongs to
        spot               : Spot price at this tick

    Spot-derived:
        spot_change_pct    : % change from session-open spot  (+ = up, - = down)
        spot_vs_atm        : spot - atm_strike (negative = below ATM)

    Options premiums:
        atm_premium_ce     : ATM CE price at this tick
        atm_premium_pe     : ATM PE price at this tick
        combined_premium   : atm_premium_ce + atm_premium_pe
        premium_decay_pct  : % of (entry_ce + entry_pe) that has decayed
                             Positive = premium fell (good for seller)
                             Negative = premium rose (seller losing)

    Implied volatility (annualised, as decimal e.g. 0.18 = 18%):
        atm_iv_ce          : IV of ATM CE
        atm_iv_pe          : IV of ATM PE
        atm_iv_avg         : Average of CE and PE IV

    Put-Call Ratio:
        pcr_current        : CE_premium / PE_premium at this tick
                             > 1.0 means CE more expensive than PE (upside fear)
                             < 1.0 means PE more expensive (downside fear)
        pcr_rolling        : Rolling average of pcr_current over last N ticks

    Time:
        minutes_since_open    : Minutes elapsed since 09:15
        minutes_to_close      : Minutes remaining until 15:30
        time_decay_fraction   : 0.0 at 09:15 → 1.0 at 15:30
    """
    timestamp:           str
    spot:                float

    # Spot-derived
    spot_change_pct:     Optional[float]  = None
    spot_vs_atm:         Optional[float]  = None

    # Options premiums
    atm_premium_ce:      Optional[float]  = None
    atm_premium_pe:      Optional[float]  = None
    combined_premium:    Optional[float]  = None
    premium_decay_pct:   Optional[float]  = None

    # Implied volatility
    atm_iv_ce:           Optional[float]  = None
    atm_iv_pe:           Optional[float]  = None
    atm_iv_avg:          Optional[float]  = None

    # Put-Call Ratio
    pcr_current:         Optional[float]  = None
    pcr_rolling:         Optional[float]  = None

    # Time
    minutes_since_open:  Optional[int]    = None
    minutes_to_close:    Optional[int]    = None
    time_decay_fraction: Optional[float]  = None

    def __repr__(self) -> str:
        iv_str  = f"{self.atm_iv_avg*100:.1f}%" if self.atm_iv_avg is not None else "N/A"
        pcr_str = f"{self.pcr_rolling:.3f}"      if self.pcr_rolling is not None else "N/A"
        dec_str = f"{self.premium_decay_pct:+.1f}%" if self.premium_decay_pct is not None else "N/A"
        return (
            f"IndicatorSnapshot({self.timestamp} | "
            f"spot={self.spot:.2f} | "
            f"iv_avg={iv_str} | "
            f"pcr={pcr_str} | "
            f"decay={dec_str})"
        )


# =============================================================================
# IndicatorEngine
# =============================================================================

class IndicatorEngine:
    """
    Computes IndicatorSnapshot for each market tick.

    Stateful for rolling calculations (PCR rolling average).
    Must call reset_day() at market open to clear state.

    Args:
        opening_spot      : Spot price at market open (09:15 or 09:30).
                            Used to compute spot_change_pct.
        atm_ce_symbol     : Trading symbol for ATM CE (e.g. 'NIFTY17FEB2626000CE').
        atm_pe_symbol     : Trading symbol for ATM PE.
        entry_ce_premium  : ATM CE premium at trade entry. Used for premium_decay_pct.
                            Pass 0.0 if not yet known (update via set_entry_premiums()).
        entry_pe_premium  : ATM PE premium at trade entry.
        risk_free_rate    : Annualised risk-free rate for IV calc. Default 0.065 (6.5%).
        pcr_window        : Number of ticks for rolling PCR average. Default 5.
    """

    def __init__(
        self,
        opening_spot:     float = 0.0,
        atm_ce_symbol:    str   = "",
        atm_pe_symbol:    str   = "",
        entry_ce_premium: float = 0.0,
        entry_pe_premium: float = 0.0,
        risk_free_rate:   float = 0.065,
        pcr_window:       int   = 5,
    ) -> None:
        self.opening_spot      = opening_spot
        self.atm_ce_symbol     = atm_ce_symbol
        self.atm_pe_symbol     = atm_pe_symbol
        self.entry_ce_premium  = entry_ce_premium
        self.entry_pe_premium  = entry_pe_premium
        self.risk_free_rate    = risk_free_rate
        self.pcr_window        = pcr_window

        # Rolling PCR history — deque of (pcr_current,) values
        self._pcr_history: deque = deque(maxlen=pcr_window)

        # Tick counter
        self._ticks_computed: int = 0

        logger.info(
            "IndicatorEngine initialised",
            ce_sym      = atm_ce_symbol or "(not set)",
            pe_sym      = atm_pe_symbol or "(not set)",
            rfr         = f"{risk_free_rate*100:.1f}%",
            pcr_window  = pcr_window,
        )

    # ── Public API ──────────────────────────────────────────────────────────────

    def reset_day(self) -> None:
        """
        Clear all rolling state. Call once at market open before the tick loop.
        Also call when opening_spot or atm symbols change between sessions.
        """
        self._pcr_history.clear()
        self._ticks_computed = 0
        logger.info("IndicatorEngine: day reset")

    def set_entry_premiums(
        self,
        ce_premium: float,
        pe_premium: float,
    ) -> None:
        """
        Update the entry premiums used for premium_decay_pct calculation.

        Call this from the strategy's on_order_update() after the entry fill
        is confirmed, or from on_entry_signal() once entry prices are known.

        Args:
            ce_premium : Filled price of the ATM CE sell leg
            pe_premium : Filled price of the ATM PE sell leg
        """
        self.entry_ce_premium = ce_premium
        self.entry_pe_premium = pe_premium
        logger.info(
            "IndicatorEngine: entry premiums updated",
            ce = ce_premium,
            pe = pe_premium,
            combined = ce_premium + pe_premium,
        )

    def set_atm_symbols(
        self,
        ce_symbol: str,
        pe_symbol: str,
        opening_spot: float = 0.0,
    ) -> None:
        """
        Update ATM symbols. Call from on_market_open() after symbols are resolved.

        Args:
            ce_symbol    : Trading symbol for ATM CE
            pe_symbol    : Trading symbol for ATM PE
            opening_spot : Session opening spot price (for spot_change_pct)
        """
        self.atm_ce_symbol = ce_symbol
        self.atm_pe_symbol = pe_symbol
        if opening_spot > 0:
            self.opening_spot = opening_spot
        logger.info(
            "IndicatorEngine: ATM symbols updated",
            ce_sym = ce_symbol,
            pe_sym = pe_symbol,
            opening_spot = self.opening_spot,
        )

    def compute(
        self,
        tick,
        days_to_expiry: float,
        atm_strike:     int = 0,
    ) -> IndicatorSnapshot:
        """
        Compute all indicators for the given MarketTick.

        This is called once per tick per strategy slot by MarketSession.
        Strategies can also call it directly in their hooks.

        Args:
            tick           : MarketTick with .spot and .option_prices dict
            days_to_expiry : Calendar days from today to expiry. Used for IV.
            atm_strike     : Integer ATM strike. Used for spot_vs_atm.

        Returns:
            IndicatorSnapshot with all computed fields (some may be None).
        """
        spot      = tick.spot
        prices    = tick.option_prices
        timestamp = getattr(tick, "timestamp", "")
        time_str  = timestamp[11:16] if len(timestamp) >= 16 else timestamp

        snap = IndicatorSnapshot(timestamp=time_str, spot=spot)

        # ── Spot-derived ───────────────────────────────────────────────────
        if self.opening_spot > 0:
            snap.spot_change_pct = round(
                (spot - self.opening_spot) / self.opening_spot * 100, 4
            )
        if atm_strike > 0:
            snap.spot_vs_atm = round(spot - atm_strike, 2)

        # ── Time-derived ───────────────────────────────────────────────────
        minutes_from_midnight = self._time_str_to_minutes(time_str)
        if minutes_from_midnight is not None:
            elapsed                  = minutes_from_midnight - TRADING_START_MINUTES
            snap.minutes_since_open  = max(elapsed, 0)
            snap.minutes_to_close    = max(TRADING_END_MINUTES - minutes_from_midnight, 0)
            snap.time_decay_fraction = round(
                min(max(elapsed / TRADING_MINUTES_TOTAL, 0.0), 1.0), 4
            )

        # ── Options premiums ───────────────────────────────────────────────
        ce_price: Optional[float] = None
        pe_price: Optional[float] = None

        if self.atm_ce_symbol and self.atm_ce_symbol in prices:
            ce_price = prices[self.atm_ce_symbol]
            if ce_price and ce_price > 0:
                snap.atm_premium_ce = round(ce_price, 2)

        if self.atm_pe_symbol and self.atm_pe_symbol in prices:
            pe_price = prices[self.atm_pe_symbol]
            if pe_price and pe_price > 0:
                snap.atm_premium_pe = round(pe_price, 2)

        if snap.atm_premium_ce is not None and snap.atm_premium_pe is not None:
            snap.combined_premium = round(
                snap.atm_premium_ce + snap.atm_premium_pe, 2
            )

            # Premium decay % relative to entry combined premium
            entry_combined = self.entry_ce_premium + self.entry_pe_premium
            if entry_combined > 0:
                snap.premium_decay_pct = round(
                    (entry_combined - snap.combined_premium) / entry_combined * 100, 3
                )

            # Put-Call Ratio (CE / PE — using premiums as proxy for demand)
            if snap.atm_premium_pe > 0:
                pcr = round(snap.atm_premium_ce / snap.atm_premium_pe, 4)
                snap.pcr_current = pcr
                self._pcr_history.append(pcr)
                if len(self._pcr_history) > 0:
                    snap.pcr_rolling = round(
                        sum(self._pcr_history) / len(self._pcr_history), 4
                    )

        # ── Implied Volatility (Black-Scholes bisection) ───────────────────
        T = max(days_to_expiry / 365.0, 1 / (365.0 * 24))   # Minimum: 1 hour

        if ce_price and ce_price > 0 and atm_strike > 0:
            iv_ce = _implied_vol(
                market_price = ce_price,
                S            = spot,
                K            = float(atm_strike),
                T            = T,
                r            = self.risk_free_rate,
                option_type  = "CE",
            )
            if iv_ce is not None:
                snap.atm_iv_ce = round(iv_ce, 6)

        if pe_price and pe_price > 0 and atm_strike > 0:
            iv_pe = _implied_vol(
                market_price = pe_price,
                S            = spot,
                K            = float(atm_strike),
                T            = T,
                r            = self.risk_free_rate,
                option_type  = "PE",
            )
            if iv_pe is not None:
                snap.atm_iv_pe = round(iv_pe, 6)

        if snap.atm_iv_ce is not None and snap.atm_iv_pe is not None:
            snap.atm_iv_avg = round((snap.atm_iv_ce + snap.atm_iv_pe) / 2.0, 6)
        elif snap.atm_iv_ce is not None:
            snap.atm_iv_avg = snap.atm_iv_ce
        elif snap.atm_iv_pe is not None:
            snap.atm_iv_avg = snap.atm_iv_pe

        self._ticks_computed += 1

        logger.debug(
            "Snapshot computed",
            time   = time_str,
            spot   = spot,
            iv_avg = f"{snap.atm_iv_avg*100:.2f}%" if snap.atm_iv_avg else "N/A",
            pcr    = snap.pcr_rolling,
            decay  = snap.premium_decay_pct,
        )

        return snap

    @property
    def ticks_computed(self) -> int:
        """Total number of snapshots computed since last reset_day()."""
        return self._ticks_computed

    # ── Private helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _time_str_to_minutes(time_str: str) -> Optional[int]:
        """
        Convert 'HH:MM' to minutes from midnight.
        Returns None if the string is malformed.
        """
        try:
            parts = time_str.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except (ValueError, IndexError, AttributeError):
            return None
