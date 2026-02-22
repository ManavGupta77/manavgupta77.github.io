# =============================================================================
# SIMULATION_LAB / MARKET_SESSION.PY
# =============================================================================
# Sprint 7 — MarketSession
#
# Extracts the tick-loop logic that was embedded in both BacktestRunner
# (Sprint 4) and PortfolioCoordinator (Sprint 6) into a single, reusable
# session driver.
#
# RESPONSIBILITIES:
#   - Own the canonical tick loop (get_timestamps → build_tick → route hooks)
#   - Route signals to the execution handler and fills back to the strategy
#   - Enforce the RiskGuard circuit breaker on every tick
#   - Compute IndicatorEngine snapshots per tick and make them available
#     to strategies (when an engine is registered)
#   - Return a SessionResult per strategy slot
#
# DESIGN PRINCIPLES:
#   - BacktestRunner and PortfolioCoordinator are NOT modified (Sprint 4/6 are
#     frozen). MarketSession is a PARALLEL entry point used via integration
#     tests and future paper/live wiring (Sprints 8/10).
#   - IndicatorEngine is OPTIONAL — strategies that don't use it are unaffected.
#     The pattern mirrors RiskGuard: inject via add_strategy(), ignored if None.
#   - MarketSession is mode-agnostic: it drives whatever handler is passed in.
#     Passing a BacktestExecutionHandler → backtest mode.
#     Passing a LiveExecutionHandler (Sprint 10) → live mode.
#     No conditionals inside MarketSession for mode switching.
#   - The Rs.-932.75 benchmark MUST reproduce exactly when IndicatorEngine
#     is not used. Any indicator logic that filters entry must be opt-in.
#
# USAGE (backtest, single strategy):
#   from simulation_lab.market_session import MarketSession, SessionConfig
#   from indicators.indicator_engine import IndicatorEngine
#
#   engine = IndicatorEngine()
#   session = MarketSession(
#       date="2026-02-11",
#       expiry="2026-02-17",
#   )
#   session.add_strategy(
#       strategy        = IronStraddleStrategy(),
#       handler         = BacktestExecutionHandler("2026-02-11", "2026-02-17"),
#       strikes         = [25800, 26000, 26200],
#       risk_guard      = RiskGuard(max_daily_loss_per_lot=-3000),
#       indicator_engine= engine,           # Optional — pass None to skip
#   )
#   results = session.run()
#   for r in results:
#       print(r)
#
# SPRINT 9 NOTE:
#   When YAML config lands, add_strategy() will accept a config dict and
#   construct handler + risk_guard + indicator_engine internally.
#
# SPRINT 8 NOTE:
#   Paper trading will pass a PaperExecutionHandler instead of
#   BacktestExecutionHandler. MarketSession requires no changes.
# =============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from strategies.building_blocks import PositionBook, TradeSignal
from strategies.risk.risk_guard import RiskGuard, RiskAction
from execution import BacktestExecutionHandler
from utilities.logger import get_logger

logger = logging.getLogger("market_session")


# ── Session result ─────────────────────────────────────────────────────────────

@dataclass
class SessionResult:
    """
    Result for a single strategy after a completed MarketSession run.

    Mirrors BacktestResult (Sprint 4) closely so existing downstream code
    (PortfolioResult, reporting) can consume either interchangeably.

    Attributes:
        date              : Session date 'YYYY-MM-DD'
        strategy_name     : Strategy's display name
        entry_spot        : Spot price at market open (09:15 or 09:30)
        atm_strike        : ATM strike resolved at on_market_open()
        realised_pnl      : Total closed-leg PnL for the session
        total_legs_traded : Count of all legs opened (open + closed)
        adjustment_cycles : Number of adjustment cycles executed
        g1_triggered      : Whether the G1 (both-sides-SL) event occurred
        open_legs_at_eod  : Legs still open at session end (should be 0)
        halted_by_risk    : True if RiskGuard fired a hard stop this session
        indicator_ticks   : Number of ticks where indicator snapshot was computed
        position_dict     : Raw PositionBook.to_dict() for downstream use
    """
    date:               str
    strategy_name:      str
    entry_spot:         float
    atm_strike:         int
    realised_pnl:       float
    total_legs_traded:  int
    adjustment_cycles:  int
    g1_triggered:       bool
    open_legs_at_eod:   int
    halted_by_risk:     bool        = False
    indicator_ticks:    int         = 0
    position_dict:      dict        = field(default_factory=dict)

    def __str__(self) -> str:
        sign = "+" if self.realised_pnl >= 0 else ""
        halt_str = "  [HALTED by RiskGuard]" if self.halted_by_risk else ""
        lines = [
            f"{'='*60}",
            f"  SESSION RESULT — {self.strategy_name}{halt_str}",
            f"  Date          : {self.date}",
            f"  Entry Spot    : {self.entry_spot:.2f}  |  ATM: {self.atm_strike}",
            f"  Adj Cycles    : {self.adjustment_cycles}",
            f"  G1 Triggered  : {'Yes' if self.g1_triggered else 'No'}",
            f"  Legs Traded   : {self.total_legs_traded}",
            f"  Open at EOD   : {self.open_legs_at_eod}",
            f"  Indicator Ticks: {self.indicator_ticks}",
            f"  {'─'*40}",
            f"  NET PNL       : Rs.{sign}{self.realised_pnl:,.2f}",
            f"{'='*60}",
        ]
        return "\n".join(lines)


# ── Strategy slot ──────────────────────────────────────────────────────────────

@dataclass
class _SessionSlot:
    """
    Internal container for one strategy and all its wired dependencies.
    Created by add_strategy() and consumed by run().
    """
    strategy:         object                      # BaseStrategy subclass
    handler:          object                      # BacktestExecutionHandler or LiveExecutionHandler
    strikes:          list
    risk_guard:       Optional[RiskGuard]
    indicator_engine: Optional[object]            # IndicatorEngine or None
    position_book:    Optional[PositionBook] = None
    active:           bool                   = True
    result:           Optional[SessionResult]= None
    indicator_ticks:  int                    = 0


# ── Market session ─────────────────────────────────────────────────────────────

class MarketSession:
    """
    Canonical tick-loop driver for a single trading session.

    Drives one or more strategies in lock-step through a day:
        1. Load data for each strategy slot
        2. Create a fresh PositionBook per slot
        3. Inject services (handler, position_book, risk_guard, indicator_engine)
        4. Call on_market_open() for every strategy
        5. Drive the shared tick loop — each tick routed to all active slots
        6. Compute IndicatorEngine snapshot per tick (if registered)
        7. Collect and return SessionResult per strategy

    Each strategy slot is fully isolated:
        - Own ExecutionHandler  → separate data load, no shared state
        - Own PositionBook      → no cross-strategy position contamination
        - Own RiskGuard         → halt of one never affects others
        - Own IndicatorEngine   → strategies can use different indicator sets

    Args:
        date   : Session date 'YYYY-MM-DD'
        expiry : Options expiry date 'YYYY-MM-DD'
    """

    def __init__(self, date: str, expiry: str) -> None:
        self.date   = date
        self.expiry = expiry
        self._slots: List[_SessionSlot] = []
        self.logger = get_logger("market_session")

    # ── Public API ──────────────────────────────────────────────────────────────

    def add_strategy(
        self,
        strategy,
        handler,
        strikes:          list,
        risk_guard:       Optional[RiskGuard] = None,
        indicator_engine: Optional[object]   = None,
    ) -> None:
        """
        Register a strategy and its dependencies for the next run().

        Each call creates one independent slot. Call once per strategy
        before run(). Slots are processed in registration order.

        Args:
            strategy         : Any BaseStrategy subclass instance.
            handler          : ExecutionHandler (backtest or live) for this strategy.
            strikes          : Strike list to pass to handler.load_data().
            risk_guard       : RiskGuard for this strategy's limits. None = no enforcement.
            indicator_engine : IndicatorEngine for this strategy. None = no indicators.
        """
        self._slots.append(_SessionSlot(
            strategy         = strategy,
            handler          = handler,
            strikes          = strikes,
            risk_guard       = risk_guard,
            indicator_engine = indicator_engine,
        ))

        rg_summary = "None"
        if risk_guard is not None:
            rg_summary = (
                f"daily={risk_guard.max_daily_loss_per_lot:.0f}/lot  "
                f"trade={risk_guard.max_trade_loss_per_lot:.0f}/lot  "
                f"cycles={risk_guard.max_adj_cycles}"
            )

        ie_summary = (
            type(indicator_engine).__name__ if indicator_engine is not None else "None"
        )

        self.logger.info(
            "Strategy registered",
            strategy         = strategy.name,
            strikes          = strikes,
            risk_guard       = rg_summary,
            indicator_engine = ie_summary,
        )

    def run(self) -> List[SessionResult]:
        """
        Execute a full session for all registered strategies.

        Steps:
            1. Load data for every slot
            2. Create fresh PositionBook per slot
            3. Inject services into each strategy
            4. on_market_open() for every strategy
            5. Shared tick loop — each tick routed to all active slots
            6. Collect SessionResult per slot and return list

        Returns:
            List[SessionResult] in add_strategy() registration order.

        Raises:
            RuntimeError if no strategies registered, or any handler
            fails to load data.
        """
        if not self._slots:
            raise RuntimeError(
                "MarketSession.run() called with no strategies registered."
            )

        self.logger.info(
            "Session starting",
            date       = self.date,
            expiry     = self.expiry,
            strategies = len(self._slots),
        )

        # ── 1. Load data ─────────────────────────────────────────────────────
        for slot in self._slots:
            ok = slot.handler.load_data(slot.strikes)
            if not ok:
                raise RuntimeError(
                    f"Handler failed to load data for strategy '{slot.strategy.name}' "
                    f"[date={self.date}  expiry={self.expiry}  strikes={slot.strikes}]"
                )

        # ── 2. Fresh PositionBook per slot ───────────────────────────────────
        for slot in self._slots:
            slot.position_book = PositionBook(strategy_name=slot.strategy.name)

        # ── 3. Inject services ───────────────────────────────────────────────
        for slot in self._slots:
            # Base injection (execution_handler, position_book, risk_guard)
            slot.strategy.inject_services(
                execution_handler = slot.handler,
                position_book     = slot.position_book,
                risk_guard        = slot.risk_guard,
            )
            # IndicatorEngine injection (Sprint 7) — optional extra service.
            # Strategies that support it implement inject_indicator_engine().
            # Strategies that don't are unaffected — we check for the method.
            if slot.indicator_engine is not None:
                inject_fn = getattr(slot.strategy, "inject_indicator_engine", None)
                if callable(inject_fn):
                    inject_fn(slot.indicator_engine)
                    self.logger.info(
                        "IndicatorEngine injected",
                        strategy = slot.strategy.name,
                        engine   = type(slot.indicator_engine).__name__,
                    )
                else:
                    self.logger.warning(
                        "IndicatorEngine supplied but strategy has no "
                        "inject_indicator_engine() method — engine ignored",
                        strategy = slot.strategy.name,
                    )

        # ── 4. Market open ───────────────────────────────────────────────────
        for slot in self._slots:
            opening_spot = (
                slot.handler.get_spot_price("09:15") or
                slot.handler.get_spot_price("09:30")
            )
            slot.strategy.on_market_open(
                session_date = self.date,
                spot_price   = opening_spot,
            )
            if slot.risk_guard is not None:
                slot.risk_guard.reset_day()

            self.logger.info(
                "Market open",
                strategy     = slot.strategy.name,
                opening_spot = opening_spot,
                entry_time   = slot.strategy.entry_time,
                exit_time    = slot.strategy.exit_time,
            )

        # ── 5. Shared tick loop ──────────────────────────────────────────────
        days_to_expiry = self._compute_days_to_expiry(self.date, self.expiry)
        timestamps     = self._slots[0].handler.get_timestamps()

        # Earliest entry across all strategies — skip ticks before this
        earliest_entry = min(s.strategy.entry_time for s in self._slots)

        for ts_iso in timestamps:
            time_str = ts_iso[11:16]   # 'HH:MM'

            if time_str < earliest_entry:
                continue

            if all(not s.active for s in self._slots):
                break

            for slot in self._slots:
                if not slot.active:
                    continue

                # Build tick from this slot's handler
                tick = slot.handler.build_tick(
                    timestamp      = ts_iso,
                    expiry_date    = self.expiry,
                    days_to_expiry = days_to_expiry,
                )
                if tick is None:
                    continue

                # ── IndicatorEngine: compute snapshot for this tick ───────
                if slot.indicator_engine is not None:
                    try:
                        snapshot = slot.indicator_engine.compute(
                            tick           = tick,
                            days_to_expiry = days_to_expiry,
                            atm_strike     = getattr(slot.strategy, "_atm_strike", 0),
                        )
                        # Attach snapshot to tick so strategy can read it
                        # via tick.indicators (IndicatorEngine sets this attr)
                        tick.indicators = snapshot
                        slot.indicator_ticks += 1
                    except Exception as exc:
                        self.logger.warning(
                            "IndicatorEngine.compute() raised — snapshot skipped",
                            strategy = slot.strategy.name,
                            time     = time_str,
                            error    = str(exc),
                        )
                        tick.indicators = None
                else:
                    tick.indicators = None

                spot   = tick.spot
                prices = tick.option_prices

                # ── Square-off at exit time ──────────────────────────────
                if time_str >= slot.strategy.exit_time:
                    signal = slot.strategy.on_market_close(time_str, spot, prices)
                    if signal:
                        self._execute_and_update(slot, signal, ts_iso)
                    slot.active = False
                    self.logger.info(
                        "Strategy closed at EOD",
                        strategy     = slot.strategy.name,
                        time         = time_str,
                        realised_pnl = slot.position_book.get_realised_pnl(),
                    )
                    continue

                # ── Entry signal ─────────────────────────────────────────
                if (not slot.strategy.in_position
                        and time_str == slot.strategy.entry_time):
                    signal = slot.strategy.on_entry_signal(time_str, spot, prices)
                    if signal:
                        self._execute_and_update(slot, signal, ts_iso)
                    continue

                # ── Adjustment / SL / G1 — with RiskGuard circuit breaker
                if slot.strategy.in_position:

                    # Continuous circuit breaker: realised + MTM vs daily limit
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
                                    legs      = open_legs,
                                    reason    = (
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
                                strategy = slot.strategy.name,
                                time     = time_str,
                                reason   = rg_decision.reason,
                            )
                            continue   # Other slots keep running

                    if slot.strategy.in_position:
                        signal = slot.strategy.on_adjustment(time_str, spot, prices)
                        if signal:
                            self._execute_and_update(slot, signal, ts_iso)

        # ── 6. Collect results ───────────────────────────────────────────────
        results: List[SessionResult] = []
        for slot in self._slots:
            pb_dict      = slot.position_book.to_dict()
            open_legs    = slot.position_book.get_open_legs()
            opening_spot = (
                slot.handler.get_spot_price("09:15") or
                slot.handler.get_spot_price("09:30")
            )
            halted = slot.risk_guard is not None and slot.risk_guard.is_halted

            result = SessionResult(
                date              = self.date,
                strategy_name     = slot.strategy.name,
                entry_spot        = opening_spot,
                atm_strike        = getattr(slot.strategy, "_atm_strike", 0),
                realised_pnl      = pb_dict.get("realised_pnl", 0.0),
                total_legs_traded = pb_dict.get("total_legs_traded", 0),
                adjustment_cycles = slot.strategy.adjustment_cycles,
                g1_triggered      = slot.strategy.g1_triggered,
                open_legs_at_eod  = len(open_legs),
                halted_by_risk    = halted,
                indicator_ticks   = slot.indicator_ticks,
                position_dict     = pb_dict,
            )
            slot.result = result
            results.append(result)

            self.logger.info(
                "Strategy complete",
                strategy     = slot.strategy.name,
                realised_pnl = result.realised_pnl,
                halted       = halted,
                legs         = result.total_legs_traded,
                ind_ticks    = result.indicator_ticks,
            )

        self.logger.info(
            "Session complete",
            date              = self.date,
            strategies        = len(results),
            total_pnl         = sum(r.realised_pnl for r in results),
            strategies_halted = sum(1 for r in results if r.halted_by_risk),
        )

        return results

    # ── Private helpers ─────────────────────────────────────────────────────────

    def _execute_and_update(self, slot: _SessionSlot,
                             signal: TradeSignal, ts_iso: str) -> None:
        """Execute a TradeSignal and route all fills back to the strategy."""
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
