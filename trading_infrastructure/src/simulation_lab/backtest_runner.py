# ==============================================================================
# SIMULATION_LAB / BACKTEST_RUNNER.PY
# ==============================================================================
# Thin orchestrator that drives a single strategy through a backtest day.
#
# RESPONSIBILITIES:
#   - Wire strategy + execution handler + position book together
#   - Drive the tick loop (get_timestamps → build_tick → route to hooks)
#   - Execute TradeSignals via the handler and route fills back
#   - Produce a BacktestResult summary
#
# OUT OF SCOPE (Sprint 4):
#   - Multi-strategy / portfolio coordination  → Sprint 6 (PortfolioCoordinator)
#   - RiskGuard enforcement                   → Sprint 5
#   - Paper / live execution                  → Sprints 8 / 10
#
# USAGE:
#   handler  = BacktestExecutionHandler(date="2026-02-11", expiry="2026-02-17")
#   handler.load_data(strikes=[25800, 26000, 26200])
#   strategy = IronStraddleStrategy()
#   runner   = BacktestRunner(strategy, handler)
#   result   = runner.run()
#   print(result)
# ==============================================================================

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from strategies.building_blocks import PositionBook, TradeSignal, SignalType
from execution import BacktestExecutionHandler
from utilities.logger import get_logger


# ── Result container ───────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    """Summary of a completed backtest run."""
    date:              str
    strategy_name:     str
    entry_spot:        float
    atm_strike:        int
    realised_pnl:      float
    total_legs_traded: int
    adjustment_cycles: int
    g1_triggered:      bool
    open_legs_at_eod:  int
    position_dict:     dict = field(default_factory=dict)

    def __str__(self) -> str:
        sign = "+" if self.realised_pnl >= 0 else ""
        lines = [
            f"{'='*60}",
            f"  BACKTEST RESULT — {self.strategy_name}",
            f"  Date          : {self.date}",
            f"  Entry Spot    : {self.entry_spot:.2f}  |  ATM: {self.atm_strike}",
            f"  Adj Cycles    : {self.adjustment_cycles}",
            f"  G1 Triggered  : {'Yes' if self.g1_triggered else 'No'}",
            f"  Legs Traded   : {self.total_legs_traded}",
            f"  Open at EOD   : {self.open_legs_at_eod}",
            f"  {'─'*40}",
            f"  NET PNL       : Rs.{sign}{self.realised_pnl:,.2f}",
            f"{'='*60}",
        ]
        return "\n".join(lines)


# ── Runner ─────────────────────────────────────────────────────────────────────

class BacktestRunner:
    """
    Drives a BaseStrategy subclass through a single backtest day.

    Wires together:
        strategy          → IronStraddleStrategy (or any BaseStrategy)
        execution_handler → BacktestExecutionHandler (date + expiry set at construction)
        position_book     → PositionBook (created fresh each run)

    Tick routing logic:
        Before entry_time  → on_entry_signal() (returns entry signal or None)
        After entry        → on_adjustment()   (state machine — every tick)
        At exit_time       → on_market_close() (square-off)
        All fills          → on_order_update() (position book update)
    """

    def __init__(self, strategy, handler: BacktestExecutionHandler,
                 risk_guard=None):
        self.strategy    = strategy
        self.handler     = handler
        self.risk_guard  = risk_guard
        self.logger      = get_logger("backtest_runner")

    def run(self, strikes: Optional[list] = None,
            date: Optional[str] = None,
            expiry: Optional[str] = None) -> BacktestResult:
        """
        Execute a full backtest day for the wired strategy.

        The handler must already be constructed with date + expiry, OR
        date/expiry can be supplied here as keyword arguments:
            runner.run(date="2026-02-11", expiry="2026-02-17", strikes=[...])

        Args:
            strikes : Strike list to pass to handler.load_data().
                      If None, assumes data is already loaded externally.
            date    : Session date override (falls back to handler.date).
            expiry  : Expiry date override (falls back to handler.expiry).

        Returns:
            BacktestResult with final PnL and summary stats.
        """
        date   = date   or self.handler.date
        expiry = expiry or self.handler.expiry

        self.logger.info("Backtest run starting",
                         date=date, strategy=self.strategy.name)

        # ── 1. Load data ───────────────────────────────────────────────────
        if strikes is not None:
            ok = self.handler.load_data(strikes)
            if not ok:
                raise RuntimeError(
                    f"BacktestExecutionHandler failed to load data "
                    f"for {date} / {expiry} / {strikes}"
                )

        # ── 2. Fresh PositionBook for this run ─────────────────────────────
        position_book = PositionBook(strategy_name=self.strategy.name)

        # ── 3. Inject services into strategy ──────────────────────────────
        self.strategy.inject_services(
            execution_handler=self.handler,
            position_book=position_book,
            risk_guard=self.risk_guard,   # Sprint 5
        )

        # ── 4. Market open — resolve symbols, reset state ─────────────────
        # Use 09:15 spot as opening; fall back to 09:30 if unavailable
        opening_spot = (self.handler.get_spot_price("09:15") or
                        self.handler.get_spot_price("09:30"))
        self.strategy.on_market_open(session_date=date, spot_price=opening_spot)

        entry_time = self.strategy.entry_time   # '09:30'
        exit_time  = self.strategy.exit_time    # '15:20'

        self.logger.info("Session initialised",
                         opening_spot=opening_spot,
                         entry_time=entry_time,
                         exit_time=exit_time)

        # ── 5. Compute days to expiry for build_tick() Greeks ──────────────
        days_to_expiry = self._compute_days_to_expiry(date, expiry)

        # ── 6. Tick loop ───────────────────────────────────────────────────
        timestamps = self.handler.get_timestamps()   # sorted ISO strings

        for ts_iso in timestamps:
            time_str = ts_iso[11:16]   # 'HH:MM'

            # Skip ticks before entry window
            if time_str < entry_time:
                continue

            # Build MarketTick — handler requires expiry_date + days_to_expiry
            tick = self.handler.build_tick(
                timestamp=ts_iso,
                expiry_date=expiry,
                days_to_expiry=days_to_expiry,
            )
            if tick is None:
                continue

            spot   = tick.spot
            prices = tick.option_prices

            signal: Optional[TradeSignal] = None

            # ── Square-off at exit time ────────────────────────────────
            if time_str >= exit_time:
                signal = self.strategy.on_market_close(time_str, spot, prices)
                if signal:
                    self._execute_and_update(signal, ts_iso)
                break   # Session over

            # ── Entry signal (fires once at entry_time) ────────────────
            if not self.strategy.in_position and time_str == entry_time:
                signal = self.strategy.on_entry_signal(time_str, spot, prices)
                if signal:
                    self._execute_and_update(signal, ts_iso)
                continue

            # ── Adjustment / SL / G1 checks every tick in position ────
            if self.strategy.in_position:
                # Continuous daily-loss circuit breaker (realised + MTM every tick)
                if self.risk_guard is not None:
                    rg_decision = self.risk_guard.check_position(
                        self.strategy.name, position_book, tick
                    )
                    if not rg_decision.allowed:
                        # Hard stop — square off all open legs immediately
                        open_legs = position_book.get_open_legs()
                        if open_legs:
                            sq_signal = TradeSignal.square_off(
                                legs=open_legs,
                                reason=f"RiskGuard circuit breaker at {time_str}: "
                                       f"{rg_decision.reason}",
                                timestamp=time_str,
                            )
                            self._execute_and_update(sq_signal, ts_iso)
                        self.strategy.in_position = False
                        break   # Session halted — no more ticks

                if self.strategy.in_position:
                    signal = self.strategy.on_adjustment(time_str, spot, prices)
                    if signal:
                        self._execute_and_update(signal, ts_iso)

        # ── 7. Build result ────────────────────────────────────────────────
        pb_dict   = position_book.to_dict()
        open_legs = position_book.get_open_legs()

        result = BacktestResult(
            date              = date,
            strategy_name     = self.strategy.name,
            entry_spot        = opening_spot,
            atm_strike        = getattr(self.strategy, '_atm_strike', 0),
            realised_pnl      = pb_dict.get('realised_pnl', 0.0),
            total_legs_traded = pb_dict.get('total_legs_traded', 0),
            adjustment_cycles = self.strategy.adjustment_cycles,
            g1_triggered      = self.strategy.g1_triggered,
            open_legs_at_eod  = len(open_legs),
            position_dict     = pb_dict,
        )

        self.logger.info(
            "Backtest run complete",
            date=date,
            realised_pnl=result.realised_pnl,
            cycles=result.adjustment_cycles,
            legs=result.total_legs_traded,
        )

        return result

    # ── Private helpers ────────────────────────────────────────────────────────

    def _execute_and_update(self, signal: TradeSignal, ts_iso: str) -> None:
        """
        Execute a TradeSignal via the handler and route every fill
        back to the strategy via on_order_update().
        """
        fills = self.handler.execute(signal, ts_iso)
        for fill in fills:
            self.strategy.on_order_update(fill)

    @staticmethod
    def _compute_days_to_expiry(date: str, expiry: str) -> float:
        """
        Compute calendar days from trading date to expiry.
        Returns minimum 0.5 to avoid zero-division in Black-Scholes.
        """
        try:
            d1 = datetime.strptime(date,   "%Y-%m-%d")
            d2 = datetime.strptime(expiry, "%Y-%m-%d")
            days = max((d2 - d1).days, 0)
            return max(float(days), 0.5)
        except Exception:
            return 7.0   # Safe default — one week
