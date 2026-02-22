# =============================================================================
# STRATEGIES / COORDINATOR.PY
# =============================================================================
# Sprint 6 — PortfolioCoordinator
#
# Drives multiple strategies through a single backtest day in lock-step.
# Each strategy runs independently with its own:
#   - ExecutionHandler  (loads its own data)
#   - PositionBook      (no cross-strategy position contamination)
#   - RiskGuard         (per-strategy limits — halt of one never affects others)
#
# The Coordinator is the multi-strategy equivalent of BacktestRunner.
# BacktestRunner remains the single-strategy entry point and is unchanged.
#
# DESIGN DECISIONS (Sprint 6):
#   - Per-strategy RiskGuard: each strategy's daily loss limit is independent.
#     One strategy breaching its cap does not halt others.
#   - Shared daily accumulator (portfolio-level) is deferred to Sprint 9 (YAML).
#   - Tick loop is driven by the FIRST strategy's handler timestamps. All
#     strategies share the same date/expiry/timestamps — this is safe because
#     BacktestExecutionHandler always returns minute-aligned ISO timestamps for
#     a given date, regardless of which strikes were loaded.
#   - A strategy slot becomes INACTIVE once its RiskGuard fires a hard stop OR
#     its on_market_close() has executed. Inactive slots are skipped each tick.
#
# USAGE:
#   coord = PortfolioCoordinator(date="2026-02-11", expiry="2026-02-17")
#
#   rg1 = RiskGuard(max_daily_loss_per_lot=-3000, lot_size=65)
#   coord.add_strategy(
#       strategy = IronStraddleStrategy(),
#       handler  = BacktestExecutionHandler("2026-02-11", "2026-02-17"),
#       strikes  = [25800, 26000, 26200],
#       risk_guard = rg1,
#   )
#
#   # Add more strategies here with their own handlers + risk guards
#
#   portfolio_result = coord.run()
#   print(portfolio_result)
#   for r in portfolio_result.strategy_results:
#       print(r)
#
# SPRINT 9 NOTE:
#   When YAML config lands, add_strategy() will accept a config dict and
#   construct handler + risk_guard internally. The shared portfolio-level
#   daily loss limit will be implemented here as an optional parameter.
# =============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from strategies.building_blocks import PositionBook, TradeSignal
from strategies.risk.risk_guard import RiskGuard, RiskDecision, RiskAction
from simulation_lab.backtest_runner import BacktestResult
from execution import BacktestExecutionHandler
from utilities.logger import get_logger


# ── Strategy slot ─────────────────────────────────────────────────────────────

@dataclass
class _StrategySlot:
    """
    Internal container for one strategy and all its wired dependencies.
    Created by add_strategy() and consumed by run().
    """
    strategy:       object                          # BaseStrategy subclass
    handler:        BacktestExecutionHandler
    strikes:        list
    risk_guard:     Optional[RiskGuard]
    position_book:  Optional[PositionBook]  = None  # created fresh on run()
    active:         bool                    = True   # False once halted or closed
    result:         Optional[BacktestResult]= None   # populated at EOD


# ── Portfolio result ──────────────────────────────────────────────────────────

@dataclass
class PortfolioResult:
    """
    Aggregated result for a full-day portfolio backtest.

    Attributes:
        date             : Session date (YYYY-MM-DD)
        strategy_results : One BacktestResult per strategy, in add_strategy() order
        total_pnl        : Sum of all strategies' realised_pnl
        strategies_halted: Count of strategies whose RiskGuard fired a hard stop
    """
    date:              str
    strategy_results:  List[BacktestResult]
    total_pnl:         float
    strategies_halted: int

    def __str__(self) -> str:
        sign = "+" if self.total_pnl >= 0 else ""
        lines = [
            f"{'='*60}",
            f"  PORTFOLIO RESULT — {self.date}",
            f"  Strategies      : {len(self.strategy_results)}",
            f"  Strategies halted: {self.strategies_halted}",
            f"  {'─'*40}",
            f"  TOTAL PNL       : Rs.{sign}{self.total_pnl:,.2f}",
            f"{'='*60}",
        ]
        return "\n".join(lines)


# ── Coordinator ───────────────────────────────────────────────────────────────

class PortfolioCoordinator:
    """
    Drives multiple strategies through a single backtest day in lock-step.

    Each strategy runs in complete isolation:
        - Its own ExecutionHandler (separate DB queries, separate data load)
        - Its own PositionBook (no shared leg state)
        - Its own RiskGuard (halt of one never affects another)

    The tick loop is driven by the first strategy's handler timestamps.
    All strategies are expected to share the same session date and expiry.

    Args:
        date   : Session date as 'YYYY-MM-DD'
        expiry : Options expiry date as 'YYYY-MM-DD'
    """

    def __init__(self, date: str, expiry: str) -> None:
        self.date   = date
        self.expiry = expiry
        self._slots: List[_StrategySlot] = []
        self.logger = get_logger("coordinator")

    # ── Public API ─────────────────────────────────────────────────────────────

    def add_strategy(
        self,
        strategy,
        handler:    BacktestExecutionHandler,
        strikes:    list,
        risk_guard: Optional[RiskGuard] = None,
    ) -> None:
        """
        Register a strategy and its dependencies for the next run().

        Each call creates one independent slot. Call this once per strategy
        before calling run(). Slots are processed in registration order.

        Args:
            strategy   : Any BaseStrategy subclass instance (pre-constructed).
            handler    : BacktestExecutionHandler constructed with the same
                         date + expiry as the Coordinator.
            strikes    : Strike list to load via handler.load_data(strikes).
            risk_guard : RiskGuard instance for this strategy's limits.
                         Pass None to run without risk enforcement.
        """
        self._slots.append(_StrategySlot(
            strategy   = strategy,
            handler    = handler,
            strikes    = strikes,
            risk_guard = risk_guard,
        ))
        rg_summary = "None"
        if risk_guard is not None:
            rg_summary = (
                f"daily={risk_guard.max_daily_loss_per_lot:.0f}/lot  "
                f"trade={risk_guard.max_trade_loss_per_lot:.0f}/lot  "
                f"cycles={risk_guard.max_adj_cycles}"
            )
        self.logger.info(
            "Strategy registered",
            strategy=strategy.name,
            strikes=strikes,
            risk_guard=rg_summary,
        )

    def run(self) -> PortfolioResult:
        """
        Execute a full backtest day for all registered strategies.

        Steps:
            1. Load data for every slot
            2. Create a fresh PositionBook per slot
            3. Inject services (handler, position_book, risk_guard) into each strategy
            4. Call on_market_open() for every strategy
            5. Drive the shared tick loop — each tick is routed to all active slots
            6. Collect results and return PortfolioResult

        Returns:
            PortfolioResult with per-strategy BacktestResult list and portfolio totals.

        Raises:
            RuntimeError if no strategies have been registered, or if any
            handler fails to load data.
        """
        if not self._slots:
            raise RuntimeError("PortfolioCoordinator.run() called with no strategies registered.")

        self.logger.info(
            "Portfolio run starting",
            date=self.date,
            expiry=self.expiry,
            strategies=len(self._slots),
        )

        # ── 1. Load data for every slot ────────────────────────────────────
        for slot in self._slots:
            ok = slot.handler.load_data(slot.strikes)
            if not ok:
                raise RuntimeError(
                    f"Handler failed to load data for strategy '{slot.strategy.name}' "
                    f"[date={self.date}  expiry={self.expiry}  strikes={slot.strikes}]"
                )

        # ── 2. Fresh PositionBook per slot ─────────────────────────────────
        for slot in self._slots:
            slot.position_book = PositionBook(strategy_name=slot.strategy.name)

        # ── 3. Inject services into each strategy ──────────────────────────
        for slot in self._slots:
            slot.strategy.inject_services(
                execution_handler = slot.handler,
                position_book     = slot.position_book,
                risk_guard        = slot.risk_guard,
            )

        # ── 4. Market open — opening spot from each slot's handler ─────────
        for slot in self._slots:
            opening_spot = (
                slot.handler.get_spot_price("09:15") or
                slot.handler.get_spot_price("09:30")
            )
            slot.strategy.on_market_open(
                session_date=self.date,
                spot_price=opening_spot,
            )
            self.logger.info(
                "Market open",
                strategy=slot.strategy.name,
                opening_spot=opening_spot,
                entry_time=slot.strategy.entry_time,
                exit_time=slot.strategy.exit_time,
            )

        # ── 5. Shared tick loop — driven by first slot's timestamps ────────
        # All handlers share the same date/expiry → same minute-aligned timestamps.
        # Using first slot's handler avoids redundant timestamp queries.
        days_to_expiry = self._compute_days_to_expiry(self.date, self.expiry)
        timestamps     = self._slots[0].handler.get_timestamps()

        for ts_iso in timestamps:
            time_str = ts_iso[11:16]   # 'HH:MM'

            # Skip ticks before the earliest entry window across all strategies
            earliest_entry = min(s.strategy.entry_time for s in self._slots)
            if time_str < earliest_entry:
                continue

            # Stop driving the loop once every slot is inactive
            if all(not s.active for s in self._slots):
                break

            # Route this tick to each active slot independently
            for slot in self._slots:
                if not slot.active:
                    continue

                # Build tick from this slot's handler (uses its loaded symbols)
                tick = slot.handler.build_tick(
                    timestamp      = ts_iso,
                    expiry_date    = self.expiry,
                    days_to_expiry = days_to_expiry,
                )
                if tick is None:
                    continue

                spot   = tick.spot
                prices = tick.option_prices

                # ── Square-off at this strategy's exit time ────────────
                if time_str >= slot.strategy.exit_time:
                    signal = slot.strategy.on_market_close(time_str, spot, prices)
                    if signal:
                        self._execute_and_update(slot, signal, ts_iso)
                    slot.active = False
                    continue

                # ── Entry signal ───────────────────────────────────────
                if (not slot.strategy.in_position
                        and time_str == slot.strategy.entry_time):
                    signal = slot.strategy.on_entry_signal(time_str, spot, prices)
                    if signal:
                        self._execute_and_update(slot, signal, ts_iso)
                    continue

                # ── Adjustment / SL / G1 — with continuous risk check ──
                if slot.strategy.in_position:

                    # Per-strategy circuit breaker (realised + MTM vs daily limit)
                    if slot.risk_guard is not None:
                        rg_decision = slot.risk_guard.check_position(
                            slot.strategy.name,
                            slot.position_book,
                            tick,
                        )
                        if not rg_decision.allowed:
                            open_legs = slot.position_book.get_open_legs()
                            if open_legs:
                                sq_signal = TradeSignal.square_off(
                                    legs   = open_legs,
                                    reason = (
                                        f"RiskGuard circuit breaker at {time_str}: "
                                        f"{rg_decision.reason}"
                                    ),
                                    timestamp = time_str,
                                )
                                self._execute_and_update(slot, sq_signal, ts_iso)
                            slot.strategy.in_position = False
                            slot.active = False
                            self.logger.warning(
                                "Strategy halted by RiskGuard",
                                strategy=slot.strategy.name,
                                time=time_str,
                                reason=rg_decision.reason,
                            )
                            continue   # Next slot — others keep running

                    if slot.strategy.in_position:
                        signal = slot.strategy.on_adjustment(time_str, spot, prices)
                        if signal:
                            self._execute_and_update(slot, signal, ts_iso)

        # ── 6. Collect results ─────────────────────────────────────────────
        strategy_results  = []
        total_pnl         = 0.0
        strategies_halted = 0

        for slot in self._slots:
            pb_dict   = slot.position_book.to_dict()
            open_legs = slot.position_book.get_open_legs()

            # Opening spot: re-read from handler for the result record
            opening_spot = (
                slot.handler.get_spot_price("09:15") or
                slot.handler.get_spot_price("09:30")
            )

            halted = (
                slot.risk_guard is not None and
                slot.risk_guard.is_halted
            )
            if halted:
                strategies_halted += 1

            result = BacktestResult(
                date              = self.date,
                strategy_name     = slot.strategy.name,
                entry_spot        = opening_spot,
                atm_strike        = getattr(slot.strategy, '_atm_strike', 0),
                realised_pnl      = pb_dict.get('realised_pnl', 0.0),
                total_legs_traded = pb_dict.get('total_legs_traded', 0),
                adjustment_cycles = slot.strategy.adjustment_cycles,
                g1_triggered      = slot.strategy.g1_triggered,
                open_legs_at_eod  = len(open_legs),
                position_dict     = pb_dict,
            )
            slot.result = result
            strategy_results.append(result)
            total_pnl += result.realised_pnl

            self.logger.info(
                "Strategy complete",
                strategy=slot.strategy.name,
                realised_pnl=result.realised_pnl,
                halted=halted,
                legs=result.total_legs_traded,
            )

        portfolio = PortfolioResult(
            date              = self.date,
            strategy_results  = strategy_results,
            total_pnl         = total_pnl,
            strategies_halted = strategies_halted,
        )

        self.logger.info(
            "Portfolio run complete",
            date=self.date,
            total_pnl=total_pnl,
            strategies=len(strategy_results),
            halted=strategies_halted,
        )

        return portfolio

    # ── Private helpers ────────────────────────────────────────────────────────

    def _execute_and_update(self, slot: _StrategySlot,
                             signal: TradeSignal, ts_iso: str) -> None:
        """Execute a TradeSignal via the slot's handler and route fills back."""
        fills = slot.handler.execute(signal, ts_iso)
        for fill in fills:
            slot.strategy.on_order_update(fill)

    @staticmethod
    def _compute_days_to_expiry(date: str, expiry: str) -> float:
        """Calendar days from session date to expiry. Minimum 0.5."""
        try:
            d1   = datetime.strptime(date,   "%Y-%m-%d")
            d2   = datetime.strptime(expiry, "%Y-%m-%d")
            days = max((d2 - d1).days, 0)
            return max(float(days), 0.5)
        except Exception:
            return 7.0
