# ==============================================================================
# BUILDING BLOCKS / OPTIONS_LEG.PY
# ==============================================================================
# Represents a single option contract in a trade — one CE or PE, buy or sell.
# This is the atomic unit every options strategy is built from.
#
# MIGRATION NOTE:
#   Promoted from the `Leg` dataclass in iron_straddle_v2.py.
#   Drop-in compatible — all existing field names are preserved.
#   New fields added: status (replaces active bool), instrument_key, urgency.
#
# USAGE:
#   leg = OptionsLeg(
#       key="CE_SELL",
#       symbol="NIFTY26FEB24000CE",
#       instrument_key="BREEZE|NIFTY|24000|CE|2026-02-27",
#       strike=24000,
#       option_type="CE",
#       entry_price=145.0,
#       qty=-65,               # Negative = SELL, Positive = BUY
#       sl_price=188.5,        # 145 * 1.30 for a 30% SL
#       entry_time="09:30",
#   )
# ==============================================================================

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class LegStatus(Enum):
    """Lifecycle status of a single option leg."""
    OPEN     = "OPEN"      # Leg is active in the portfolio
    CLOSED   = "CLOSED"    # Exited at target or strategy decision
    SL_HIT   = "SL_HIT"    # Exited because stop-loss was triggered
    ROLLED   = "ROLLED"    # Closed as part of an adjustment/roll


class LegAction(Enum):
    """Whether this leg is a sell (premium collection) or buy (hedge)."""
    SELL = "SELL"
    BUY  = "BUY"


@dataclass
class OptionsLeg:
    """
    A single option contract in a trade.

    The `qty` field encodes direction:
      - Negative qty = SELL (e.g. -65 = sell 1 lot of Nifty)
      - Positive qty = BUY  (e.g. +65 = buy 1 lot hedge)

    This matches the original Leg dataclass convention in iron_straddle_v2.py
    exactly, so existing PnL logic (compute_pnl) is preserved unchanged.
    """

    # ── Identity ────────────────────────────────────────────────────────────
    key: str
    """Unique role key within the strategy (e.g. 'CE_SELL', 'PE_BUY_ADJ').
    Used by PositionBook to look up and close specific legs."""

    symbol: str
    """Broker-facing trading symbol (e.g. 'NIFTY26FEB24000CE').
    Used for live/paper order placement and display."""

    instrument_key: str = ""
    """Database instrument key matching options_ohlc table format.
    Format: 'BREEZE|NIFTY|<strike>|CE/PE|<expiry>'
    Used by BacktestExecutionHandler to query fill prices from SQLite."""

    # ── Contract Details ─────────────────────────────────────────────────────
    strike: int = 0
    """Strike price (e.g. 24000). Always an integer — rounded to STRIKE_STEP."""

    option_type: str = ""
    """'CE' or 'PE'."""

    # ── Position Details ─────────────────────────────────────────────────────
    qty: int = 0
    """Signed quantity. Negative = SELL, Positive = BUY.
    Magnitude = number of shares (lots * lot_size)."""

    entry_price: float = 0.0
    """Price at which this leg was filled on entry."""

    sl_price: float = 9999.0
    """Stop-loss trigger price. For buy hedges, set to 9999 (no SL).
    For sell legs: sl_price = entry_price * (1 + SL_PCT)."""

    # ── Timing ───────────────────────────────────────────────────────────────
    entry_time: str = ""
    """IST time string when this leg was filled (e.g. '09:30')."""

    # ── Fill & Exit State ────────────────────────────────────────────────────
    exit_price: float = 0.0
    """Price at which this leg was closed. 0.0 while still open."""

    exit_time: str = ""
    """IST time string when this leg was closed. Empty while open."""

    realized_pnl: float = 0.0
    """Realised PnL after close. Computed by compute_pnl() on exit."""

    # ── Status ───────────────────────────────────────────────────────────────
    status: LegStatus = LegStatus.OPEN
    """Current lifecycle status. Replaces the old `active: bool` field.
    Use leg.is_active property for boolean checks (backward compatible)."""

    # ── Execution Hint ───────────────────────────────────────────────────────
    urgency: str = "NORMAL"
    """'NORMAL' or 'URGENT'. Passed to ExecutionHandler.
    URGENT triggers market orders in live mode; NORMAL allows limit orders."""

    # ── Notes ────────────────────────────────────────────────────────────────
    notes: str = ""
    """Optional human-readable note (e.g. 'Adjustment leg — cycle 2')."""

    # ── Computed Properties ──────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """True if leg is currently open. Backward-compatible with old `active` bool."""
        return self.status == LegStatus.OPEN

    @property
    def is_sell(self) -> bool:
        """True if this is a short (sold) leg. Negative qty = sell."""
        return self.qty < 0

    @property
    def action(self) -> LegAction:
        """Returns LegAction.SELL or LegAction.BUY based on qty sign."""
        return LegAction.SELL if self.qty < 0 else LegAction.BUY

    @property
    def is_closing(self) -> bool:
        """
        True when this leg has been closed by PositionBook.record_fill().

        Evaluated AFTER position_book.record_fill() inside on_order_update().
        record_fill() calls leg.mark_closed(), which sets status to CLOSED or
        SL_HIT.  Opening fills leave status as OPEN.

        Used by IronStraddleStrategy.on_order_update() to report realised PnL
        to RiskGuard only for closing fills — not for opening fills:

            if self._risk_guard is not None and fill.leg.is_closing:
                self._risk_guard.record_pnl(fill.leg.compute_pnl(fill.fill_price))
        """
        return self.status != LegStatus.OPEN

    # ── Core Methods ─────────────────────────────────────────────────────────

    def compute_pnl(self, exit_pr: float) -> float:
        """
        Calculate realised PnL for this leg at a given exit price.

        For SELL legs: profit when price falls  → (entry - exit) * abs(qty)
        For BUY  legs: profit when price rises  → (exit - entry) * abs(qty)

        This formula is preserved exactly from iron_straddle_v2.py.
        """
        if self.is_sell:
            return (self.entry_price - exit_pr) * abs(self.qty)
        else:
            return (exit_pr - self.entry_price) * abs(self.qty)

    def mark_closed(self, exit_price: float, exit_time: str,
                    status: LegStatus = LegStatus.CLOSED) -> float:
        """
        Close this leg in-place and compute realised PnL.

        Called by PositionBook.close_leg() — not called directly by strategies.

        Returns:
            Realised PnL for this leg.
        """
        self.exit_price  = exit_price
        self.exit_time   = exit_time
        self.realized_pnl = self.compute_pnl(exit_price)
        self.status      = status
        return self.realized_pnl

    def sl_breached(self, current_price: float) -> bool:
        """
        True if the current market price has hit or crossed the stop-loss.

        For sell legs: SL breaches when price rises above sl_price.
        Buy hedge legs have sl_price=9999 so this always returns False.
        """
        return self.is_sell and current_price >= self.sl_price

    def __repr__(self) -> str:
        action_str = "SELL" if self.is_sell else "BUY "
        return (
            f"OptionsLeg({self.key} | {action_str} {self.option_type} "
            f"strike={self.strike} qty={self.qty} "
            f"entry={self.entry_price:.2f} sl={self.sl_price:.2f} "
            f"status={self.status.value})"
        )
