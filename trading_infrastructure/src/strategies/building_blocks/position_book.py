# ==============================================================================
# BUILDING BLOCKS / POSITION_BOOK.PY
# ==============================================================================
# Tracks all open and closed legs for a strategy, computes MTM and
# realised PnL, and provides a structured record for logging and reporting.
#
# MIGRATION NOTE:
#   Promoted from the `Portfolio` class in iron_straddle_v2.py.
#   All existing method names and behaviours are preserved.
#   Key additions:
#     - record_fill()      → replaces manual portfolio.add_leg() after fills
#     - get_mtm_pnl()      → accepts MarketTick (was inline in loop)
#     - get_net_delta()    → uses GreekSnapshots from tick
#     - to_dict()          → full serialisation for PDF report and CSV export
#     - sl_breached_legs() → replaces manual SL check loops in strategy
#
# USAGE:
#   book = PositionBook(strategy_name="Iron Straddle", lot_size=65)
#
#   # After a fill arrives via on_order_update():
#   book.record_fill(leg_fill)
#
#   # Check SL hits on every tick:
#   for leg in book.sl_breached_legs(tick):
#       signal = TradeSignal.exit([leg], reason=f"{leg.key} SL hit")
#
#   # Get current PnL:
#   print(book.get_total_pnl(tick))
# ==============================================================================

from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

from .options_leg import OptionsLeg, LegStatus
from .leg_fill import LegFill

if TYPE_CHECKING:
    from .market_tick import MarketTick


class PositionBook:
    """
    Real-time record of all open and closed legs for one strategy instance.

    One PositionBook per strategy instance. The PortfolioCoordinator
    injects it via BaseStrategy.inject_services().

    Thread-safety: Not thread-safe. All access from coordinator event loop only.
    """

    def __init__(self, strategy_name: str = "", lot_size: int = 1):
        """
        Args:
            strategy_name : For logging and PDF report labelling.
            lot_size      : Lot size for the instrument (e.g. 65 for Nifty).
                            Used in summary calculations.
        """
        self.strategy_name = strategy_name
        self.lot_size      = lot_size

        # ── Open Legs ─────────────────────────────────────────────────────────
        # Key = OptionsLeg.key (e.g. "CE_SELL", "PE_BUY_ADJ")
        # Only OPEN legs are here. Closed legs move to closed_legs list.
        self.legs: Dict[str, OptionsLeg] = {}

        # ── Closed Legs ───────────────────────────────────────────────────────
        # Ordered list of all legs that have been closed, in close sequence.
        # Preserved from Portfolio.closed_legs in v2 — PDF report reads this.
        self.closed_legs: List[OptionsLeg] = []

        # ── PnL Tracking ──────────────────────────────────────────────────────
        self.total_realized_pnl: float = 0.0

        # ── Fill History ──────────────────────────────────────────────────────
        # Full ordered record of every LegFill received. For audit and logging.
        self.fill_history: List[LegFill] = []

    # ==========================================================================
    # FILL RECORDING  (called from strategy.on_order_update())
    # ==========================================================================

    def record_fill(self, fill: LegFill) -> None:
        """
        Process a fill confirmation from the ExecutionHandler.

        For opening fills: adds the leg to the open legs dict.
        For closing fills: closes the leg and records realised PnL.

        Args:
            fill : LegFill returned by ExecutionHandler and passed via
                   on_order_update(). Must be a FILLED fill (not REJECTED).

        Usage (in strategy.on_order_update):
            def on_order_update(self, fill):
                if fill.is_filled:
                    self.position_book.record_fill(fill)
        """
        if not fill.is_filled:
            return  # Rejected fills don't change position state

        self.fill_history.append(fill)

        if fill.is_open:
            # New position — add to open legs
            self.legs[fill.leg.key] = fill.leg

        elif fill.is_close:
            # Closing an existing leg
            self.close_leg(fill.leg.key,
                           exit_price=fill.fill_price,
                           exit_time=fill.fill_time,
                           status=LegStatus.CLOSED)

    # ==========================================================================
    # LEG MANAGEMENT
    # ==========================================================================

    def add_leg(self, leg: OptionsLeg) -> None:
        """
        Directly add an open leg to the book.

        Preserved from Portfolio.add_leg() for backward compatibility
        with any code that creates legs without going through LegFill.
        Prefer record_fill() for new code.
        """
        self.legs[leg.key] = leg

    def close_leg(self, key: str, exit_price: float, exit_time: str,
                  status: LegStatus = LegStatus.CLOSED) -> float:
        """
        Close an open leg and record its realised PnL.

        Preserved from Portfolio.close_leg() — same signature and return value.

        Args:
            key        : The leg's key string (e.g. "CE_SELL").
            exit_price : Price at which the leg was closed.
            exit_time  : IST time string of the close.
            status     : LegStatus.CLOSED or LegStatus.SL_HIT.

        Returns:
            Realised PnL for this leg.

        Raises:
            KeyError if key is not in open legs.
        """
        if key not in self.legs:
            raise KeyError(f"PositionBook: cannot close '{key}' — not in open legs. "
                           f"Open legs: {list(self.legs.keys())}")

        leg = self.legs[key]
        pnl = leg.mark_closed(exit_price, exit_time, status)
        self.total_realized_pnl += pnl
        self.closed_legs.append(leg)
        del self.legs[key]
        return pnl

    def has_leg(self, key: str) -> bool:
        """
        True if the key is an open leg.

        Preserved from Portfolio.has_leg() — same name and behaviour.
        """
        return key in self.legs and self.legs[key].is_active

    def get_leg(self, key: str) -> Optional[OptionsLeg]:
        """
        Get an open leg by key. Returns None if not found.

        Usage:
            ce = book.get_leg("CE_SELL")
            if ce:
                print(ce.entry_price)
        """
        return self.legs.get(key)

    def get_open_legs(self) -> List[OptionsLeg]:
        """
        Return all currently open OptionsLeg objects as a list.

        Used to build SQUARE_OFF signals:
            signal = TradeSignal.square_off(
                legs=self.position_book.get_open_legs(), ...
            )
        """
        return list(self.legs.values())

    def active_keys(self) -> List[str]:
        """
        Return keys of all open legs.

        Preserved from Portfolio.active_keys() — same name.
        """
        return list(self.legs.keys())

    def is_flat(self) -> bool:
        """True if no legs are currently open."""
        return len(self.legs) == 0

    def open_sell_legs(self) -> List[OptionsLeg]:
        """Return only open legs that are short (sell) positions."""
        return [l for l in self.legs.values() if l.is_sell]

    def open_buy_legs(self) -> List[OptionsLeg]:
        """Return only open legs that are long (buy/hedge) positions."""
        return [l for l in self.legs.values() if not l.is_sell]

    # ==========================================================================
    # SL MONITORING  (replaces manual SL check loops in strategy)
    # ==========================================================================

    def sl_breached_legs(self, tick: "MarketTick") -> List[OptionsLeg]:
        """
        Return all open sell legs whose SL has been breached at this tick.

        Replaces the manual per-leg SL price checks in iron_straddle_v2.py.
        The strategy calls this on every tick in on_adjustment() and acts
        on the returned list.

        Args:
            tick : Current MarketTick — option_prices are read from it.

        Returns:
            List of OptionsLeg objects where current price >= sl_price.
            Empty list if no SL breaches.

        Usage (in strategy.on_adjustment):
            breached = self.position_book.sl_breached_legs(tick)
            if breached:
                # Handle SL — build adjustment signal
        """
        breached = []
        for leg in self.legs.values():
            if leg.is_sell:
                current_price = tick.get_price(leg.symbol)
                if current_price > 0 and leg.sl_breached(current_price):
                    breached.append(leg)
        return breached

    def all_sells_sl_breached(self, tick: "MarketTick") -> bool:
        """
        True if ALL open sell legs have simultaneously breached their SL.

        Corresponds to the G1 (BOTH SIDES SL HIT) condition in v2 state machine.

        Usage:
            if self.position_book.all_sells_sl_breached(tick):
                # Transition to ALL_OUT state
        """
        sell_legs = self.open_sell_legs()
        if not sell_legs:
            return False
        return all(
            tick.get_price(leg.symbol) >= leg.sl_price
            for leg in sell_legs
            if tick.get_price(leg.symbol) > 0
        )

    # ==========================================================================
    # PnL CALCULATIONS
    # ==========================================================================

    def get_mtm_pnl(self, tick: "MarketTick") -> float:
        """
        Compute unrealised (Mark-to-Market) PnL across all open legs.

        Uses current option prices from the tick's option_prices dict.
        For sell legs: profit = entry_price - current_price (per unit * qty)
        For buy legs:  profit = current_price - entry_price (per unit * qty)

        Args:
            tick : Current MarketTick with option_prices populated.

        Returns:
            Total unrealised PnL across all open legs in rupees.
            Returns 0.0 if no open legs or prices unavailable.
        """
        mtm = 0.0
        for leg in self.legs.values():
            current_price = tick.get_price(leg.symbol)
            if current_price > 0:
                mtm += leg.compute_pnl(current_price)
        return round(mtm, 2)

    def get_realised_pnl(self) -> float:
        """
        Total realised PnL from all closed legs.

        Equivalent to Portfolio.total_realized_pnl in v2.
        """
        return round(self.total_realized_pnl, 2)

    def get_total_pnl(self, tick: "MarketTick") -> float:
        """
        Total PnL = Realised + Unrealised (MTM).

        Args:
            tick : Current MarketTick for MTM calculation.

        Returns:
            Combined PnL in rupees.
        """
        return round(self.get_realised_pnl() + self.get_mtm_pnl(tick), 2)

    def get_net_delta(self, tick: "MarketTick") -> float:
        """
        Net portfolio delta across all open legs.

        Requires Greeks to be populated in the tick (by GreeksCalculator
        or broker feed). Returns 0.0 if Greeks are not available.

        Positive delta = net long bias (portfolio profits if spot rises).
        Negative delta = net short bias.

        Usage (in on_adjustment for delta-based adjustments):
            if abs(self.position_book.get_net_delta(tick)) > threshold:
                return self._build_delta_adjustment_signal(tick)
        """
        net = 0.0
        for leg in self.legs.values():
            greek = tick.get_greek(leg.symbol)
            if greek:
                # Delta is signed by leg direction:
                # SELL leg contributes -qty * delta (we're short the option)
                # BUY  leg contributes +qty * delta (we're long the option)
                net += leg.qty * greek.delta
        return round(net, 4)

    def get_combined_premium(self) -> float:
        """
        Sum of entry premiums collected from all open sell legs.

        Used to compute SL targets (e.g. combined SL = 2x combined premium).
        """
        return round(
            sum(leg.entry_price for leg in self.open_sell_legs()), 2
        )

    # ==========================================================================
    # SERIALISATION  (for PDF report and CSV export)
    # ==========================================================================

    def to_dict(self) -> dict:
        """
        Serialise the full position book to a dict.

        Used by:
          - report_builder.py to generate the PDF trade summary
          - PortfolioCoordinator to log end-of-day summary
          - CSV export if reporting.save_position_book_csv = true in YAML

        Returns:
            Dict with keys: strategy_name, realised_pnl, open_legs,
            closed_legs, fill_count, total_legs_traded.
        """
        return {
            "strategy_name":    self.strategy_name,
            "realised_pnl":     self.get_realised_pnl(),
            "open_leg_count":   len(self.legs),
            "open_legs": [
                {
                    "key":         leg.key,
                    "symbol":      leg.symbol,
                    "strike":      leg.strike,
                    "option_type": leg.option_type,
                    "action":      "SELL" if leg.is_sell else "BUY",
                    "qty":         leg.qty,
                    "entry_price": leg.entry_price,
                    "sl_price":    leg.sl_price,
                    "entry_time":  leg.entry_time,
                    "status":      leg.status.value,
                }
                for leg in self.legs.values()
            ],
            "closed_legs": [
                {
                    "key":          leg.key,
                    "symbol":       leg.symbol,
                    "strike":       leg.strike,
                    "option_type":  leg.option_type,
                    "action":       "SELL" if leg.is_sell else "BUY",
                    "qty":          leg.qty,
                    "entry_price":  leg.entry_price,
                    "exit_price":   leg.exit_price,
                    "sl_price":     leg.sl_price,
                    "entry_time":   leg.entry_time,
                    "exit_time":    leg.exit_time,
                    "realized_pnl": round(leg.realized_pnl, 2),
                    "status":       leg.status.value,
                }
                for leg in self.closed_legs
            ],
            "total_legs_traded": len(self.closed_legs),
            "fill_count":        len(self.fill_history),
        }

    # ==========================================================================
    # DISPLAY
    # ==========================================================================

    def print_summary(self) -> None:
        """
        Print a formatted position summary to console.

        Matches the style of iron_straddle_v2.py's TRADE SUMMARY output.
        """
        sep = "─" * 90
        print(f"\n{'='*90}")
        print(f"  POSITION BOOK — {self.strategy_name}")
        print(f"{'='*90}")

        if self.legs:
            print(f"\n  OPEN LEGS ({len(self.legs)}):")
            print(f"  {'KEY':<16} | {'SYMBOL':<24} | {'ENTRY':>8} | {'SL':>8} | {'QTY':>6}")
            print(f"  {sep}")
            for leg in self.legs.values():
                sl = f"{leg.sl_price:.2f}" if leg.sl_price < 9999 else "N/A"
                print(f"  {leg.key:<16} | {leg.symbol:<24} | "
                      f"{leg.entry_price:>8.2f} | {sl:>8} | {leg.qty:>6}")

        if self.closed_legs:
            print(f"\n  CLOSED LEGS ({len(self.closed_legs)}):")
            print(f"  {'KEY':<16} | {'ENTRY':>8} | {'EXIT':>8} | "
                  f"{'QTY':>6} | {'PNL':>10} | {'IN':>6} | {'OUT':>6}")
            print(f"  {sep}")
            for leg in self.closed_legs:
                pnl_str = f"Rs.{int(leg.realized_pnl):+,}"
                print(f"  {leg.key:<16} | {leg.entry_price:>8.2f} | "
                      f"{leg.exit_price:>8.2f} | {leg.qty:>6} | "
                      f"{pnl_str:>10} | {leg.entry_time:>6} | {leg.exit_time:>6}")

        print(f"\n  {'TOTAL REALISED PnL':.<40} Rs.{self.total_realized_pnl:,.2f}")
        print(f"{'='*90}\n")

    def __repr__(self) -> str:
        return (f"PositionBook({self.strategy_name} | "
                f"open={len(self.legs)} closed={len(self.closed_legs)} | "
                f"realised=Rs.{self.total_realized_pnl:,.2f})")
