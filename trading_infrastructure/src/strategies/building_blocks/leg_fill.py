# ==============================================================================
# BUILDING BLOCKS / LEG_FILL.PY
# ==============================================================================
# The fill confirmation returned by the ExecutionHandler to the strategy.
#
# FLOW:
#   Strategy emits TradeSignal
#     → PortfolioCoordinator → RiskGuard (approve/block)
#       → ExecutionHandler (Backtest / Paper / Live)
#         → Returns List[LegFill] back to coordinator
#           → Coordinator calls strategy.on_order_update(fill) for each fill
#             → Strategy updates PositionBook via position_book.record_fill(fill)
#
# KEY DESIGN RULE:
#   The strategy can never tell which ExecutionHandler produced a fill.
#   BacktestExecutionHandler, PaperExecutionHandler, and LiveExecutionHandler
#   all return identical LegFill objects. Mode-awareness lives in the handler,
#   not in the strategy.
#
# USAGE — Reading a fill in on_order_update:
#   def on_order_update(self, fill: LegFill):
#       if fill.is_open:
#           self.position_book.record_fill(fill)
#           self.logger.info(f"Filled {fill.leg.key} at {fill.fill_price}")
#       elif fill.is_rejected:
#           self.logger.error(f"Fill rejected: {fill.rejection_reason}")
# ==============================================================================

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from .options_leg import OptionsLeg, LegStatus


class FillStatus(Enum):
    """
    Outcome of an order sent to the ExecutionHandler.

    FILLED    — Order executed successfully. fill_price is valid.
    REJECTED  — Order was rejected (live mode: broker error; backtest: no data).
    PARTIAL   — Only part of qty was filled. Reserved for future use.
    """
    FILLED   = "FILLED"
    REJECTED = "REJECTED"
    PARTIAL  = "PARTIAL"    # Reserved — not implemented in current sprint


class ExecutionMode(Enum):
    """
    Which ExecutionHandler produced this fill.

    Present in LegFill for logging and audit purposes only.
    The strategy must never branch logic based on this field.
    """
    BACKTEST = "BACKTEST"
    PAPER    = "PAPER"
    LIVE     = "LIVE"


@dataclass
class LegFill:
    """
    Fill confirmation for a single OptionsLeg.

    Returned by the ExecutionHandler after an order is processed.
    The strategy receives one LegFill per leg via on_order_update().

    For OPEN fills (new positions):
        - leg.entry_price and leg.entry_time are set by the ExecutionHandler
          before returning the fill. The strategy does not set these.

    For CLOSE fills (exits):
        - fill_price is the exit price. PositionBook.close_leg() uses this
          to compute and record realised PnL.

    Fields:
        leg              : The OptionsLeg that was filled. For opens, this is
                           the leg with entry_price populated. For closes,
                           this is the leg being exited.
        fill_price       : Actual fill price.
                           Backtest: next candle Open from options_ohlc.
                           Paper:    live LTP at time of signal.
                           Live:     actual market fill price from broker.
        fill_qty         : Quantity filled. Currently always equals leg.qty
                           (full fills only). fill_qty field exists for future
                           partial fill support.
        fill_time        : IST time string of the fill (e.g. '09:30').
        status           : FILLED, REJECTED, or PARTIAL.
        mode             : BACKTEST, PAPER, or LIVE — for logging only.
        is_opening_fill  : True if this fill opens a new position.
                           False if it closes an existing leg.
        order_id         : Broker order ID. None in backtest and paper modes.
        slippage         : Difference between signal price and fill price.
                           0.0 in backtest. Actual slippage in live/paper.
        rejection_reason : Populated if status == REJECTED. Empty otherwise.
    """

    leg:             OptionsLeg
    fill_price:      float
    fill_qty:        int
    fill_time:       str
    status:          FillStatus      = FillStatus.FILLED
    mode:            ExecutionMode   = ExecutionMode.BACKTEST
    is_opening_fill: bool            = True
    order_id:        Optional[str]   = None
    slippage:        float           = 0.0
    rejection_reason: str            = ""

    # ── Convenience Properties ───────────────────────────────────────────────

    @property
    def is_filled(self) -> bool:
        """True if the order was executed successfully."""
        return self.status == FillStatus.FILLED

    @property
    def is_rejected(self) -> bool:
        """True if the order was rejected and no position change occurred."""
        return self.status == FillStatus.REJECTED

    @property
    def is_open(self) -> bool:
        """True if this fill opened a new leg (as opposed to closing one)."""
        return self.is_opening_fill and self.is_filled

    @property
    def is_close(self) -> bool:
        """True if this fill closed an existing leg."""
        return not self.is_opening_fill and self.is_filled

    @property
    def pnl_if_close(self) -> float:
        """
        Realised PnL if this is a closing fill. 0.0 for opening fills.

        Computed from the leg's entry_price vs this fill's fill_price.
        This matches PositionBook.close_leg() arithmetic exactly.
        """
        if not self.is_close:
            return 0.0
        return self.leg.compute_pnl(self.fill_price)

    # ── Factory Methods ──────────────────────────────────────────────────────
    # Used by ExecutionHandlers to construct fills. Not called by strategies.

    @classmethod
    def filled_open(cls, leg: OptionsLeg, fill_price: float,
                    fill_time: str, mode: ExecutionMode,
                    order_id: Optional[str] = None) -> "LegFill":
        """
        Construct a successful opening fill.

        Called by ExecutionHandler after a new leg is filled.
        Sets leg.entry_price and leg.entry_time in-place before returning.
        """
        leg.entry_price = fill_price
        leg.entry_time  = fill_time
        return cls(
            leg=leg,
            fill_price=fill_price,
            fill_qty=leg.qty,
            fill_time=fill_time,
            status=FillStatus.FILLED,
            mode=mode,
            is_opening_fill=True,
            order_id=order_id,
        )

    @classmethod
    def filled_close(cls, leg: OptionsLeg, fill_price: float,
                     fill_time: str, mode: ExecutionMode,
                     order_id: Optional[str] = None) -> "LegFill":
        """
        Construct a successful closing fill.

        Called by ExecutionHandler when an existing leg is exited.
        Does NOT modify leg state — PositionBook.close_leg() does that.
        """
        return cls(
            leg=leg,
            fill_price=fill_price,
            fill_qty=leg.qty,
            fill_time=fill_time,
            status=FillStatus.FILLED,
            mode=mode,
            is_opening_fill=False,
            order_id=order_id,
        )

    @classmethod
    def rejected(cls, leg: OptionsLeg, fill_time: str,
                 mode: ExecutionMode, reason: str) -> "LegFill":
        """
        Construct a rejection fill.

        Called by ExecutionHandler when an order cannot be placed
        (e.g. no price data in backtest, broker error in live).
        The strategy receives this via on_order_update() and can decide
        how to handle it (retry, abort, alert).
        """
        return cls(
            leg=leg,
            fill_price=0.0,
            fill_qty=0,
            fill_time=fill_time,
            status=FillStatus.REJECTED,
            mode=mode,
            is_opening_fill=True,
            rejection_reason=reason,
        )

    def __repr__(self) -> str:
        direction = "OPEN" if self.is_opening_fill else "CLOSE"
        return (
            f"LegFill({self.status.value} | {direction} | "
            f"{self.leg.key} @ {self.fill_price:.2f} | "
            f"qty={self.fill_qty} | {self.mode.value} | t={self.fill_time})"
        )
