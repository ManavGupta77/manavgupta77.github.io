# =============================================================================
# risk_guard.py  —  Sprint 5
# src/strategies/risk/risk_guard.py
#
# System-wide risk enforcement layer.
# Injected into strategies via inject_services(). A single shared instance
# can be passed to multiple strategies by the PortfolioCoordinator (Sprint 6).
#
# Limits are expressed PER LOT (human-readable) and multiplied by lot_size
# internally to derive the actual Rs. thresholds. This matches how traders
# think about risk ("I'm risking Rs.3000 per lot") and keeps YAML config
# readable regardless of lot size changes.
#
# Enforces three limits:
#   1. max_daily_loss_per_lot  — cumulative realised PnL floor per lot
#   2. max_trade_loss_per_lot  — MTM PnL floor for a single open position per lot
#   3. max_adj_cycles          — maximum adjustment cycles per strategy per day
#
# When a hard-stop limit (1 or 2) fires → RiskAction.SQUARE_OFF
# When a soft limit (3) fires           → RiskAction.BLOCK
# After any SQUARE_OFF the guard is halted for the rest of the session.
# Call reset_day() on market open to clear state for the new session.
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger("risk_guard")


# ---------------------------------------------------------------------------
# Enums & dataclasses
# ---------------------------------------------------------------------------

class RiskAction(Enum):
    ALLOW      = "ALLOW"
    BLOCK      = "BLOCK"       # skip action, keep position running to EOD exit
    SQUARE_OFF = "SQUARE_OFF"  # exit all open legs immediately


@dataclass
class RiskDecision:
    allowed: bool
    action:  RiskAction
    reason:  str = ""

    @classmethod
    def allow(cls) -> "RiskDecision":
        return cls(allowed=True, action=RiskAction.ALLOW)

    @classmethod
    def block(cls, reason: str) -> "RiskDecision":
        return cls(allowed=False, action=RiskAction.BLOCK, reason=reason)

    @classmethod
    def square_off(cls, reason: str) -> "RiskDecision":
        return cls(allowed=False, action=RiskAction.SQUARE_OFF, reason=reason)


# ---------------------------------------------------------------------------
# RiskGuard
# ---------------------------------------------------------------------------

class RiskGuard:
    """
    Stateful, session-scoped risk enforcement layer.

    ALL MONETARY LIMITS ARE EXPRESSED PER LOT.
    RiskGuard multiplies by lot_size internally to compute the actual Rs.
    thresholds used for comparisons. This means:

      - YAML config stays human-readable ("Rs.3000 per lot")
      - Lot-size changes (SEBI revises NIFTY lots periodically) only require
        updating lot_size in one place — limit values stay unchanged
      - Per-strategy risk tolerance is expressed in the same units traders use

    Parameters
    ----------
    max_daily_loss_per_lot : float
        Cumulative realised loss floor for the session, per lot. Negative Rs.
        Default: -3000  →  total threshold = -3000 × lot_size
    max_trade_loss_per_lot : float
        MTM loss floor for a single strategy's open position, per lot. Negative Rs.
        Default: -1500  →  total threshold = -1500 × lot_size
    max_adj_cycles : int
        Maximum adjustment cycles per strategy per session. Default: 2
    lot_size : int
        Shares per lot for the instrument. Default: 65 (NIFTY, Feb 2026)

    Derived thresholds (read-only properties):
        max_daily_loss  = max_daily_loss_per_lot × lot_size   e.g. -195,000
        max_trade_loss  = max_trade_loss_per_lot × lot_size   e.g.  -97,500

    YAML config shape (Sprint 9)
    ----------------------------
        risk:
          max_daily_loss_per_lot: -3000
          max_trade_loss_per_lot: -1500
          max_adj_cycles: 2
          lot_size: 65          # mirrors strategy.lot_size
    """

    def __init__(
        self,
        max_daily_loss_per_lot: float = -3000.0,
        max_trade_loss_per_lot: float = -1500.0,
        max_adj_cycles:         int   = 2,
        lot_size:               int   = 65,
    ) -> None:
        self.lot_size                = lot_size
        self.max_daily_loss_per_lot  = max_daily_loss_per_lot
        self.max_trade_loss_per_lot  = max_trade_loss_per_lot
        self.max_adj_cycles          = max_adj_cycles

        # Derived total Rs. thresholds
        self._max_daily_loss = max_daily_loss_per_lot * lot_size   # e.g. -195,000
        self._max_trade_loss = max_trade_loss_per_lot * lot_size   # e.g.  -97,500

        # Session state
        self._daily_pnl: float = 0.0
        self._halted:    bool  = False

        logger.info(
            "RiskGuard initialised "
            "[%.0f/lot × %d lots = %.0f daily | %.0f/lot × %d lots = %.0f trade | "
            "max_adj_cycles=%d]",
            max_daily_loss_per_lot, lot_size, self._max_daily_loss,
            max_trade_loss_per_lot, lot_size, self._max_trade_loss,
            max_adj_cycles,
        )

    # ------------------------------------------------------------------
    # Read-only threshold properties
    # ------------------------------------------------------------------

    @property
    def max_daily_loss(self) -> float:
        """Total Rs. daily loss threshold (max_daily_loss_per_lot × lot_size)."""
        return self._max_daily_loss

    @property
    def max_trade_loss(self) -> float:
        """Total Rs. trade loss threshold (max_trade_loss_per_lot × lot_size)."""
        return self._max_trade_loss

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def is_halted(self) -> bool:
        return self._halted

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def reset_day(self) -> None:
        """Call once on market open to clear all session state."""
        self._daily_pnl = 0.0
        self._halted    = False
        logger.info("RiskGuard: day reset [daily_pnl=0.0  halted=False]")

    def record_pnl(self, realised_pnl: float) -> None:
        """
        Accumulate realised PnL. Call after each closing fill batch.
        realised_pnl is the incremental change for that fill, not the
        running total.
        """
        self._daily_pnl += realised_pnl
        logger.info(
            "RiskGuard: PnL recorded "
            "[delta=%.2f  daily_total=%.2f  limit=%.0f  (%.0f/lot)]",
            realised_pnl, self._daily_pnl,
            self._max_daily_loss, self.max_daily_loss_per_lot,
        )

    def check_entry(self, strategy_name: str, position_book) -> RiskDecision:
        """
        Called by a strategy before placing an initial entry.
        Blocks if the guard is halted or the daily loss limit is already breached.
        """
        if self._halted:
            reason = "RiskGuard halted — daily loss limit previously triggered"
            logger.warning("RiskGuard [%s]: entry BLOCKED — %s", strategy_name, reason)
            return RiskDecision.block(reason)

        if self._daily_pnl <= self._max_daily_loss:
            reason = (
                f"Daily loss limit breached "
                f"[daily_pnl={self._daily_pnl:.2f}  "
                f"limit={self._max_daily_loss:.0f} "
                f"({self.max_daily_loss_per_lot:.0f}/lot × {self.lot_size} lots)]"
            )
            self._halt(strategy_name, reason)
            return RiskDecision.square_off(reason)

        logger.debug(
            "RiskGuard [%s]: entry ALLOWED [daily_pnl=%.2f  limit=%.0f]",
            strategy_name, self._daily_pnl, self._max_daily_loss,
        )
        return RiskDecision.allow()

    def check_adjustment(
        self,
        strategy_name: str,
        adj_cycles:    int,
        position_book,
        current_tick=None,
    ) -> RiskDecision:
        """
        Called by a strategy before executing an adjustment cycle.

        Checks in priority order:
          1. Guard already halted        → BLOCK
          2. Daily loss limit breached   → SQUARE_OFF + halt
          3. Trade MTM loss breached     → SQUARE_OFF + halt
          4. Max adj cycles reached      → BLOCK (soft, no halt)
        """
        if self._halted:
            reason = "RiskGuard halted — daily loss limit previously triggered"
            logger.warning("RiskGuard [%s]: adjustment BLOCKED — %s", strategy_name, reason)
            return RiskDecision.block(reason)

        # 1. Daily loss hard stop
        if self._daily_pnl <= self._max_daily_loss:
            reason = (
                f"Daily loss limit breached "
                f"[daily_pnl={self._daily_pnl:.2f}  "
                f"limit={self._max_daily_loss:.0f} "
                f"({self.max_daily_loss_per_lot:.0f}/lot × {self.lot_size} lots)]"
            )
            self._halt(strategy_name, reason)
            return RiskDecision.square_off(reason)

        # 2. Trade (MTM) loss hard stop
        if current_tick is not None:
            try:
                mtm = position_book.get_mtm_pnl(current_tick)
                if mtm is None:
                    mtm = 0.0
            except Exception:
                mtm = 0.0
            if mtm <= self._max_trade_loss:
                reason = (
                    f"Trade loss limit breached "
                    f"[mtm={mtm:.2f}  "
                    f"limit={self._max_trade_loss:.0f} "
                    f"({self.max_trade_loss_per_lot:.0f}/lot × {self.lot_size} lots)]"
                )
                self._halt(strategy_name, reason)
                return RiskDecision.square_off(reason)

        # 3. Max adjustment cycles (soft — BLOCK, no halt, position runs to EOD)
        if adj_cycles >= self.max_adj_cycles:
            reason = (
                f"Max adjustment cycles reached "
                f"[cycles={adj_cycles}  max={self.max_adj_cycles}]"
            )
            logger.warning("RiskGuard [%s]: adjustment BLOCKED — %s", strategy_name, reason)
            return RiskDecision.block(reason)

        logger.debug(
            "RiskGuard [%s]: adjustment ALLOWED "
            "[daily_pnl=%.2f  adj_cycles=%d  daily_limit=%.0f]",
            strategy_name, self._daily_pnl, adj_cycles, self._max_daily_loss,
        )
        return RiskDecision.allow()

    def check_position(
        self,
        strategy_name: str,
        position_book,
        current_tick,
    ) -> RiskDecision:
        """
        Called by BacktestRunner every tick while a position is open.
        Checks the daily loss limit using realised PnL + current MTM so that
        an unrealised drawdown triggers a hard stop before EOD.

        This is separate from check_adjustment (which fires at SL trigger points).
        check_position fires continuously — it is the intraday circuit breaker.

        Returns SQUARE_OFF + halts if (realised + mtm) <= max_daily_loss.
        Returns BLOCK if already halted.
        Returns ALLOW otherwise.
        """
        if self._halted:
            return RiskDecision.block(
                "RiskGuard halted — daily loss limit previously triggered"
            )

        try:
            mtm = position_book.get_mtm_pnl(current_tick)
            if mtm is None:
                mtm = 0.0
        except Exception:
            mtm = 0.0

        effective = self._daily_pnl + mtm

        if effective <= self._max_daily_loss:
            reason = (
                f"Daily loss limit breached "
                f"[realised={self._daily_pnl:.2f}  mtm={mtm:.2f}  "
                f"total={effective:.2f}  "
                f"limit={self._max_daily_loss:.0f} "
                f"({self.max_daily_loss_per_lot:.0f}/lot × {self.lot_size} lots)]"
            )
            self._halt(strategy_name, reason)
            return RiskDecision.square_off(reason)

        return RiskDecision.allow()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _halt(self, strategy_name: str, reason: str) -> None:
        self._halted = True
        logger.warning("RiskGuard [%s]: HALTED — %s", strategy_name, reason)
