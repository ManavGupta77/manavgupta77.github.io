# ==============================================================================
# BUILDING BLOCKS / TRADE_SIGNAL.PY
# ==============================================================================
# The object a strategy emits when it wants to act on the market.
#
# CORE DESIGN PRINCIPLE:
#   A strategy never places orders. It emits TradeSignals.
#   The PortfolioCoordinator receives the signal, passes it through RiskGuard,
#   and if approved, hands it to the ExecutionHandler (Backtest/Paper/Live).
#   The strategy only sees the resulting LegFill — it never touches a broker.
#
# USAGE — Entry signal:
#   signal = TradeSignal.entry(
#       legs=[ce_sell_leg, pe_sell_leg, ce_buy_leg, pe_buy_leg],
#       reason="ATM Iron Straddle entry at 09:30",
#       state_after="NEUTRAL",
#   )
#
# USAGE — Exit signal:
#   signal = TradeSignal.exit(
#       legs=[portfolio.get_leg("CE_SELL")],
#       reason="CE_SELL SL hit at 09:47 — price 188.50 >= sl 188.50",
#       state_after="ADJUSTED",
#   )
#
# USAGE — Square-off all:
#   signal = TradeSignal.square_off(
#       legs=portfolio.get_open_legs(),
#       reason="EOD square-off at 15:20",
#   )
# ==============================================================================

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from .options_leg import OptionsLeg


class SignalType(Enum):
    """
    The intent of a TradeSignal.

    ENTRY       — Open new legs (initial position or re-entry after G1).
    EXIT        — Close specific legs (SL hit, target reached, adjustment).
    ADJUSTMENT  — Close some legs and open replacements in the same signal.
    SQUARE_OFF  — Close all open legs unconditionally (EOD or risk breach).
    """
    ENTRY      = "ENTRY"
    EXIT       = "EXIT"
    ADJUSTMENT = "ADJUSTMENT"
    SQUARE_OFF = "SQUARE_OFF"


class SignalUrgency(Enum):
    """
    Execution urgency hint for the ExecutionHandler.

    NORMAL  — Allows limit orders in live mode. Backtest/paper use close price.
    URGENT  — Forces market orders in live mode (SL hits, EOD square-off).
              Backtest/paper use the same close price regardless.
    """
    NORMAL = "NORMAL"
    URGENT = "URGENT"


@dataclass
class TradeSignal:
    """
    Emitted by a strategy to express a trading intention.

    The strategy constructs this object and returns it from a lifecycle hook.
    It never executes the trade itself. The coordinator handles routing.

    Fields:
        signal_type  : What kind of action is being requested.
        legs_to_open : OptionsLeg objects to be filled as new positions.
        legs_to_close: OptionsLeg objects (already in PositionBook) to be exited.
        reason       : Human-readable explanation — appears in event log and PDF.
        state_before : Strategy state when signal was generated (for logging).
        state_after  : Expected strategy state after signal is executed.
        urgency      : NORMAL or URGENT. Affects order type in live mode.
        timestamp    : IST time string when this signal was generated.
        cycle        : Adjustment cycle number (0 for initial entry).
    """

    signal_type:   SignalType
    legs_to_open:  List[OptionsLeg] = field(default_factory=list)
    legs_to_close: List[OptionsLeg] = field(default_factory=list)
    reason:        str = ""
    state_before:  str = ""
    state_after:   str = ""
    urgency:       SignalUrgency = SignalUrgency.NORMAL
    timestamp:     str = ""
    cycle:         int = 0

    # ── Convenience Constructors ─────────────────────────────────────────────
    # These are the recommended way to create signals inside strategy hooks.
    # They make the intent clear and reduce boilerplate.

    @classmethod
    def entry(cls, legs: List[OptionsLeg], reason: str,
              state_after: str = "NEUTRAL", timestamp: str = "",
              cycle: int = 0) -> "TradeSignal":
        """
        Open new legs. Used for initial straddle entry and G1 re-entries.

        Example:
            signal = TradeSignal.entry(
                legs=[ce_sell, pe_sell, ce_buy, pe_buy],
                reason="Iron Straddle entry at 09:30 | ATM=24000",
                state_after="NEUTRAL",
                timestamp="09:30",
            )
        """
        return cls(
            signal_type=SignalType.ENTRY,
            legs_to_open=legs,
            reason=reason,
            state_after=state_after,
            timestamp=timestamp,
            cycle=cycle,
        )

    @classmethod
    def exit(cls, legs: List[OptionsLeg], reason: str,
             state_before: str = "", state_after: str = "",
             urgency: SignalUrgency = SignalUrgency.URGENT,
             timestamp: str = "", cycle: int = 0) -> "TradeSignal":
        """
        Close specific legs. Used for SL hits and targeted exits.

        Example:
            signal = TradeSignal.exit(
                legs=[portfolio.get_leg("CE_SELL")],
                reason="CE_SELL SL hit — price 188.50",
                state_before="NEUTRAL",
                state_after="ADJUSTED",
                timestamp="10:15",
            )
        """
        return cls(
            signal_type=SignalType.EXIT,
            legs_to_close=legs,
            reason=reason,
            state_before=state_before,
            state_after=state_after,
            urgency=urgency,
            timestamp=timestamp,
            cycle=cycle,
        )

    @classmethod
    def adjustment(cls, legs_to_close: List[OptionsLeg],
                   legs_to_open: List[OptionsLeg], reason: str,
                   state_before: str = "", state_after: str = "",
                   timestamp: str = "", cycle: int = 0) -> "TradeSignal":
        """
        Close some legs and open replacements atomically.

        Used for adjustments (rolling a tested leg) and flip-backs.
        The ExecutionHandler always closes legs_to_close BEFORE opening
        legs_to_open, ensuring buy-before-sell discipline.

        Example:
            signal = TradeSignal.adjustment(
                legs_to_close=[ce_sell_leg, ce_buy_leg],
                legs_to_open=[pe_buy_adj_leg, pe_sell_adj_leg],
                reason="CE SL hit — adjustment cycle 1",
                state_before="NEUTRAL",
                state_after="ADJUSTED",
                timestamp="10:15",
                cycle=1,
            )
        """
        return cls(
            signal_type=SignalType.ADJUSTMENT,
            legs_to_close=legs_to_close,
            legs_to_open=legs_to_open,
            reason=reason,
            state_before=state_before,
            state_after=state_after,
            urgency=SignalUrgency.URGENT,
            timestamp=timestamp,
            cycle=cycle,
        )

    @classmethod
    def square_off(cls, legs: List[OptionsLeg], reason: str,
                   timestamp: str = "") -> "TradeSignal":
        """
        Close all provided legs unconditionally.

        Used for EOD square-off (15:20) and RiskGuard emergency exits.
        Always URGENT — forces market orders in live mode.

        Example:
            signal = TradeSignal.square_off(
                legs=portfolio.get_open_legs(),
                reason="EOD square-off at 15:20",
                timestamp="15:20",
            )
        """
        return cls(
            signal_type=SignalType.SQUARE_OFF,
            legs_to_close=legs,
            reason=reason,
            state_after="DONE",
            urgency=SignalUrgency.URGENT,
            timestamp=timestamp,
        )

    # ── Inspection Helpers ───────────────────────────────────────────────────

    @property
    def has_opens(self) -> bool:
        """True if this signal opens any new legs."""
        return len(self.legs_to_open) > 0

    @property
    def has_closes(self) -> bool:
        """True if this signal closes any existing legs."""
        return len(self.legs_to_close) > 0

    @property
    def is_urgent(self) -> bool:
        """True if this signal should be executed as a market order in live mode."""
        return self.urgency == SignalUrgency.URGENT

    def all_legs(self) -> List[OptionsLeg]:
        """Returns all legs involved in this signal (opens + closes combined)."""
        return self.legs_to_close + self.legs_to_open

    def __repr__(self) -> str:
        return (
            f"TradeSignal({self.signal_type.value} | "
            f"open={len(self.legs_to_open)} close={len(self.legs_to_close)} | "
            f"cycle={self.cycle} urgency={self.urgency.value} | "
            f"'{self.reason[:50]}')"
        )
