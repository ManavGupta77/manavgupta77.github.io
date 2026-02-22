# =============================================================================
# STRATEGIES / OPTIONS_SELLING / IRON_STRADDLE.PY
# =============================================================================
# Class-based Iron Straddle with Dynamic Gamma Adjustments.
# Migrated from iron_straddle_v2.py (procedural) to IronStraddleStrategy.
#
# BENCHMARK: Rs.-932.75 net PnL on 2026-02-11 (verified against v2 output).
#
# STATE MACHINE:
#   NEUTRAL  -> Full iron straddle active (CE_SELL + PE_SELL + CE_BUY + PE_BUY)
#   ADJUSTED -> One side SL'd; extra lot + hedge added on untested side
#   FLIPPED  -> Adjustment leg also SL'd; flipped to originally-tested side
#   ALL_OUT  -> Both sides SL'd simultaneously; waiting for G1 re-entry
#   DONE     -> Final square-off complete
#
# DESIGN RULES (inherited from BaseStrategy):
#   - Never imports from broker_gateway directly
#   - Never queries the database directly
#   - Never knows which ExecutionMode it is running in
#   - Returns TradeSignal objects — never places orders itself
#   - All parameters passed via __init__ (YAML wiring comes in Sprint 9)
#
# SPRINT 5: RiskGuard injected via inject_services().
#   - check_entry()      called once before the initial 4-leg entry
#   - check_adjustment() called before every adjustment cycle
#   - SQUARE_OFF         -> strategy emits immediate square-off signal
#   - BLOCK              -> strategy skips the action, position runs to EOD
# =============================================================================

from enum import Enum
from typing import Optional

from strategies.base_strategy import BaseStrategy
from strategies.building_blocks import (
    OptionsLeg, LegStatus,
    TradeSignal, SignalType, SignalUrgency,
    LegFill, FillStatus,
    MarketTick,
    PositionBook,
)
from strategies.risk.risk_guard import RiskAction


# ── State Machine ──────────────────────────────────────────────────────────────

class StraddleState(Enum):
    NEUTRAL  = "NEUTRAL"
    ADJUSTED = "ADJUSTED"
    FLIPPED  = "FLIPPED"
    ALL_OUT  = "ALL_OUT"
    DONE     = "DONE"


# ── Strategy ───────────────────────────────────────────────────────────────────

class IronStraddleStrategy(BaseStrategy):
    """
    NIFTY Iron Straddle with Dynamic Gamma Adjustments.

    Sells ATM CE + PE, buys OTM hedges. Manages adjustments, flips,
    reversion and G1 (both-sides-SL) re-entry through a 5-state machine.

    Parameters (all have production defaults matching iron_straddle_v2.py):
        lot_size         : Shares per lot. Default 65 (NIFTY).
        sl_pct           : Stop-loss as fraction of sell premium. Default 0.30.
        hedge_offset     : Points away from ATM for hedge strikes. Default 200.
        reversion_buffer : Points from ATM to trigger reversion. Default 15.
        entry_time       : Entry candle time as 'HH:MM'. Default '09:30'.
        exit_time        : Square-off time as 'HH:MM'. Default '15:20'.
        strike_step      : Nifty strike grid spacing. Default 50.

    RiskGuard wiring (Sprint 5):
        Pass a RiskGuard instance to inject_services(). Its lot_size should
        match this strategy's lot_size so per-lot limits scale correctly.
        If risk_guard=None, all risk checks are skipped silently.
    """

    def __init__(
        self,
        lot_size:         int   = 65,
        sl_pct:           float = 0.30,
        hedge_offset:     int   = 200,
        reversion_buffer: int   = 15,
        entry_time:       str   = "09:30",
        exit_time:        str   = "15:20",
        strike_step:      int   = 50,
    ):
        super().__init__(name="Iron Straddle", instrument="NIFTY")

        # ── Parameters ────────────────────────────────────────────────────────
        self.lot_size         = lot_size
        self.sl_pct           = sl_pct
        self.hedge_offset     = hedge_offset
        self.reversion_buffer = reversion_buffer
        self.entry_time       = entry_time
        self.exit_time        = exit_time
        self.strike_step      = strike_step

        # ── Session State (reset each day in on_market_open) ──────────────────
        self._state:          StraddleState = StraddleState.NEUTRAL
        self._tested_side:    Optional[str] = None   # 'CE' or 'PE'
        self._adj_cycle:      int           = 0
        self._g1_active:      bool          = False
        self._entered:        bool          = False

        # ── Strike / Symbol anchors (set in on_market_open) ───────────────────
        self._atm_strike:       int = 0
        self._ce_hedge_strike:  int = 0
        self._pe_hedge_strike:  int = 0
        self._sym: dict = {}

        # Original entry premiums for G1 re-entry condition
        self._orig_ce_premium: float = 0.0
        self._orig_pe_premium: float = 0.0

    # ==========================================================================
    # SERVICE INJECTION
    # ==========================================================================

    def inject_services(self, execution_handler, position_book,
                        risk_guard=None) -> None:
        """
        Called by BacktestRunner / Coordinator before on_market_open.

        Args:
            execution_handler : BacktestExecutionHandler or LiveExecutionHandler
            position_book     : PositionBook instance for this strategy
            risk_guard        : RiskGuard instance (optional).
                                Its lot_size should match self.lot_size.
                                Pass None to disable all risk checks.
        """
        super().inject_services(execution_handler, position_book, risk_guard)

        rg_summary = "None"
        if risk_guard is not None:
            rg_summary = (
                f"daily={risk_guard.max_daily_loss_per_lot:.0f}/lot  "
                f"trade={risk_guard.max_trade_loss_per_lot:.0f}/lot  "
                f"cycles={risk_guard.max_adj_cycles}"
            )

        self.logger.info(
            "Services injected",
            mode=type(execution_handler).__name__,
            risk_guard=rg_summary,
        )

    # ==========================================================================
    # SESSION LIFECYCLE HOOKS
    # ==========================================================================

    def on_market_open(self, session_date: str, spot_price: float) -> None:
        """
        09:15 IST. Resolve strikes and symbols. Reset all session state.
        """
        # Reset session state
        self._state           = StraddleState.NEUTRAL
        self._tested_side     = None
        self._adj_cycle       = 0
        self._g1_active       = False
        self._entered         = False
        self._sym             = {}
        self._orig_ce_premium = 0.0
        self._orig_pe_premium = 0.0

        # Reset RiskGuard for the new session
        if self._risk_guard is not None:
            self._risk_guard.reset_day()

        # Compute strikes
        self._atm_strike      = int(round(spot_price / self.strike_step) * self.strike_step)
        self._ce_hedge_strike = self._atm_strike + self.hedge_offset
        self._pe_hedge_strike = self._atm_strike - self.hedge_offset

        handler = self._execution_handler

        self._sym = {
            'CE_ATM':   handler.find_symbol(self._atm_strike, 'CE'),
            'PE_ATM':   handler.find_symbol(self._atm_strike, 'PE'),
            'CE_HEDGE': handler.find_symbol(self._ce_hedge_strike, 'CE'),
            'PE_HEDGE': handler.find_symbol(self._pe_hedge_strike, 'PE'),
        }

        missing = [k for k, v in self._sym.items() if not v]
        if missing:
            self.logger.error("Symbol resolution failed", missing=missing)
            return

        self.logger.info(
            "Market open — symbols resolved",
            date=session_date, spot=spot_price, atm=self._atm_strike,
            ce_atm=self._sym['CE_ATM'], pe_atm=self._sym['PE_ATM'],
        )

    def on_entry_signal(self, timestamp: str, spot: float,
                        prices: dict) -> Optional[TradeSignal]:
        """
        Called each tick during the entry window.
        Returns a 4-leg iron straddle entry signal exactly once at entry_time.

        RiskGuard check: if daily loss already breached or guard is halted,
        entry is silently skipped (no position opened).
        """
        if self._entered:
            return None
        if timestamp != self.entry_time:
            return None
        if not all(self._sym.values()):
            return None

        # ── RiskGuard: entry check ─────────────────────────────────────────
        if self._risk_guard is not None:
            decision = self._risk_guard.check_entry(self.name, self._position_book)
            if not decision.allowed:
                self.logger.warning(
                    "Entry blocked by RiskGuard",
                    action=decision.action.value,
                    reason=decision.reason,
                )
                return None

        ce_sell_pr = prices.get(self._sym['CE_ATM'], 0.0)
        pe_sell_pr = prices.get(self._sym['PE_ATM'], 0.0)
        if ce_sell_pr <= 0 or pe_sell_pr <= 0:
            self.logger.warning("Zero entry prices — skipping entry", ts=timestamp)
            return None

        self._orig_ce_premium = ce_sell_pr
        self._orig_pe_premium = pe_sell_pr

        legs_to_open = [
            OptionsLeg(
                key="CE_BUY", symbol=self._sym['CE_HEDGE'],
                strike=self._ce_hedge_strike, option_type='CE',
                qty=self.lot_size, sl_price=9999.0,
            ),
            OptionsLeg(
                key="PE_BUY", symbol=self._sym['PE_HEDGE'],
                strike=self._pe_hedge_strike, option_type='PE',
                qty=self.lot_size, sl_price=9999.0,
            ),
            OptionsLeg(
                key="CE_SELL", symbol=self._sym['CE_ATM'],
                strike=self._atm_strike, option_type='CE',
                qty=-self.lot_size,
                sl_price=round(ce_sell_pr * (1 + self.sl_pct), 2),
            ),
            OptionsLeg(
                key="PE_SELL", symbol=self._sym['PE_ATM'],
                strike=self._atm_strike, option_type='PE',
                qty=-self.lot_size,
                sl_price=round(pe_sell_pr * (1 + self.sl_pct), 2),
            ),
        ]

        self._entered    = True
        self.in_position = True
        self._state      = StraddleState.NEUTRAL

        self.logger.info(
            "Entry signal",
            ts=timestamp, spot=spot, atm=self._atm_strike,
            ce_sell=ce_sell_pr, pe_sell=pe_sell_pr,
            combined=round(ce_sell_pr + pe_sell_pr, 2),
        )

        return TradeSignal.entry(
            legs=legs_to_open,
            reason=f"Iron Straddle entry at {timestamp} | ATM={self._atm_strike}",
            state_after="NEUTRAL",
            timestamp=timestamp,
        )

    def on_adjustment(self, timestamp: str, spot: float,
                      prices: dict) -> Optional[TradeSignal]:
        """
        Called every tick while in position.
        Runs the full state machine: SL checks, reversion, G1 re-entry.
        """
        if not self.in_position:
            return None
        if self._state == StraddleState.DONE:
            return None

        if self._state == StraddleState.ALL_OUT:
            return self._check_g1_reentry(timestamp, spot, prices)
        if self._state == StraddleState.NEUTRAL:
            return self._check_neutral_sl(timestamp, spot, prices)
        if self._state == StraddleState.ADJUSTED:
            return self._check_adjusted(timestamp, spot, prices)
        if self._state == StraddleState.FLIPPED:
            return self._check_flipped(timestamp, spot, prices)

        return None

    def on_market_close(self, timestamp: str, spot: float,
                        prices: dict) -> Optional[TradeSignal]:
        """15:20 IST. Square off all open legs."""
        if not self.in_position:
            return None

        open_legs = self._position_book.get_open_legs()
        if not open_legs:
            self.in_position = False
            self._state = StraddleState.DONE
            return None

        self.logger.info(
            "Square-off signal",
            ts=timestamp, open_legs=len(open_legs),
            realised_pnl=self._position_book.get_realised_pnl(),
        )

        self._state      = StraddleState.DONE
        self.in_position = False
        return TradeSignal.square_off(
            legs=open_legs,
            reason=f"EOD square-off at {timestamp}",
            timestamp=timestamp,
        )

    def on_order_update(self, fill: LegFill) -> None:
        """
        Called for each fill. Updates PositionBook and reports closing PnL
        to RiskGuard so the daily accumulator stays current.
        """
        if fill.is_filled:
            self._position_book.record_fill(fill)
            self.logger.debug(
                "Fill recorded",
                key=fill.leg.key, price=fill.fill_price,
                status=fill.status.value,
            )

            # Report closing-fill PnL to RiskGuard
            if self._risk_guard is not None and fill.leg.is_closing:
                leg_pnl = fill.leg.compute_pnl(fill.fill_price)
                self._risk_guard.record_pnl(leg_pnl)

        elif fill.is_rejected:
            self.logger.error("Fill rejected", key=fill.leg.key)

    # ==========================================================================
    # STATE MACHINE — PRIVATE HELPERS
    # ==========================================================================

    def _sl_hit(self, prices: dict, leg_key: str) -> bool:
        pb = self._position_book
        if not pb.has_leg(leg_key):
            return False
        leg = pb.get_leg(leg_key)
        if not leg.is_sell:
            return False
        return leg.sl_breached(prices.get(leg.symbol, 0.0))

    def _close_legs(self, timestamp: str, prices: dict, *keys) -> list:
        pb = self._position_book
        return [pb.get_leg(k) for k in keys if pb.has_leg(k)]

    def _make_sell_leg(self, key, symbol, strike, opt_type, price) -> OptionsLeg:
        return OptionsLeg(
            key=key, symbol=symbol, strike=strike, option_type=opt_type,
            qty=-self.lot_size,
            sl_price=round(price * (1 + self.sl_pct), 2),
        )

    def _make_buy_leg(self, key, symbol, strike, opt_type) -> OptionsLeg:
        return OptionsLeg(
            key=key, symbol=symbol, strike=strike, option_type=opt_type,
            qty=self.lot_size, sl_price=9999.0,
        )

    # ── NEUTRAL ───────────────────────────────────────────────────────────────

    def _check_neutral_sl(self, timestamp: str, spot: float,
                          prices: dict) -> Optional[TradeSignal]:
        ce_hit = self._sl_hit(prices, "CE_SELL")
        pe_hit = self._sl_hit(prices, "PE_SELL")

        if not ce_hit and not pe_hit:
            return None

        # G1 — both sides simultaneously
        if ce_hit and pe_hit:
            self._adj_cycle += 1
            self._g1_active  = True
            self._state      = StraddleState.ALL_OUT
            self.logger.info("G1: Both SLs hit simultaneously",
                             ts=timestamp, spot=spot, cycle=self._adj_cycle)
            legs_to_close = self._close_legs(
                timestamp, prices, "CE_SELL", "PE_SELL", "CE_BUY", "PE_BUY"
            )
            return TradeSignal.exit(
                legs=legs_to_close,
                reason="Both sides SL hit simultaneously -> ALL_OUT",
                state_before="NEUTRAL", state_after="ALL_OUT",
                urgency=SignalUrgency.URGENT,
            )

        # ── RiskGuard: check before executing adjustment ───────────────────
        if self._risk_guard is not None:
            decision = self._risk_guard.check_adjustment(
                self.name, self._adj_cycle, self._position_book, current_tick=None
            )
            if not decision.allowed:
                self.logger.warning(
                    "Adjustment blocked by RiskGuard",
                    action=decision.action.value,
                    reason=decision.reason, ts=timestamp,
                )
                if decision.action == RiskAction.SQUARE_OFF:
                    open_keys     = [leg.key for leg in self._position_book.get_open_legs()]
                    legs_to_close = self._close_legs(timestamp, prices, *open_keys)
                    self._state      = StraddleState.DONE
                    self.in_position = False
                    self.logger.warning(
                        "RiskGuard hard stop — squaring off all legs",
                        ts=timestamp, legs=len(legs_to_close),
                    )
                    return TradeSignal.square_off(
                        legs=legs_to_close,
                        reason=f"RiskGuard hard stop at {timestamp}: {decision.reason}",
                        timestamp=timestamp,
                    )
                return None   # BLOCK — skip adjustment, let position run

        # Single side SL — execute adjustment
        self._tested_side = 'CE' if ce_hit else 'PE'
        untested          = 'PE' if self._tested_side == 'CE' else 'CE'
        self._adj_cycle  += 1

        tested_sell_key  = f"{self._tested_side}_SELL"
        tested_buy_key   = f"{self._tested_side}_BUY"
        adj_sell_key     = f"{untested}_SELL_ADJ"
        adj_buy_key      = f"{untested}_BUY_ADJ"
        adj_hedge_strike = (self._atm_strike - self.hedge_offset if untested == 'PE'
                            else self._atm_strike + self.hedge_offset)
        adj_hedge_sym    = self._execution_handler.find_symbol(adj_hedge_strike, untested)
        adj_sell_sym     = self._sym[f'{untested}_ATM']
        adj_sell_pr      = prices.get(adj_sell_sym, 0.0)

        legs_to_close = self._close_legs(timestamp, prices, tested_sell_key, tested_buy_key)
        legs_to_open  = []
        if adj_hedge_sym:
            legs_to_open.append(
                self._make_buy_leg(adj_buy_key, adj_hedge_sym, adj_hedge_strike, untested)
            )
        if adj_sell_sym and adj_sell_pr > 0:
            legs_to_open.append(
                self._make_sell_leg(adj_sell_key, adj_sell_sym,
                                    self._atm_strike, untested, adj_sell_pr)
            )

        self._state = StraddleState.ADJUSTED
        self.logger.info(
            "NEUTRAL -> ADJUSTED",
            ts=timestamp, spot=spot,
            tested=self._tested_side, cycle=self._adj_cycle,
        )

        return TradeSignal.adjustment(
            legs_to_close=legs_to_close, legs_to_open=legs_to_open,
            reason=f"{self._tested_side} SL hit -> ADJUSTED (cycle {self._adj_cycle})",
            state_before="NEUTRAL", state_after="ADJUSTED",
            cycle=self._adj_cycle,
        )

    # ── ADJUSTED ──────────────────────────────────────────────────────────────

    def _check_adjusted(self, timestamp: str, spot: float,
                        prices: dict) -> Optional[TradeSignal]:
        untested     = 'PE' if self._tested_side == 'CE' else 'CE'
        adj_sell_key = f"{untested}_SELL_ADJ"
        adj_buy_key  = f"{untested}_BUY_ADJ"

        if abs(spot - self._atm_strike) <= self.reversion_buffer:
            return self._reversion_to_neutral(
                timestamp, spot, prices, adj_sell_key, adj_buy_key
            )
        if self._sl_hit(prices, adj_sell_key):
            return self._flip(timestamp, spot, prices, adj_sell_key, adj_buy_key)
        return None

    def _reversion_to_neutral(self, timestamp, spot, prices,
                               adj_sell_key, adj_buy_key) -> Optional[TradeSignal]:
        tested_sell_key     = f"{self._tested_side}_SELL"
        tested_buy_key      = f"{self._tested_side}_BUY"
        tested_hedge_strike = (self._atm_strike + self.hedge_offset
                               if self._tested_side == 'CE'
                               else self._atm_strike - self.hedge_offset)
        tested_hedge_sym = self._execution_handler.find_symbol(
            tested_hedge_strike, self._tested_side
        )
        tested_sell_sym = self._sym[f'{self._tested_side}_ATM']
        tested_sell_pr  = prices.get(tested_sell_sym, 0.0)

        legs_to_close = self._close_legs(timestamp, prices, adj_sell_key, adj_buy_key)
        legs_to_open  = []
        if tested_hedge_sym:
            legs_to_open.append(
                self._make_buy_leg(tested_buy_key, tested_hedge_sym,
                                   tested_hedge_strike, self._tested_side)
            )
        if tested_sell_sym and tested_sell_pr > 0:
            legs_to_open.append(
                self._make_sell_leg(tested_sell_key, tested_sell_sym,
                                    self._atm_strike, self._tested_side, tested_sell_pr)
            )

        self._state       = StraddleState.NEUTRAL
        self._tested_side = None
        self.logger.info("ADJUSTED -> NEUTRAL (reversion)", ts=timestamp, spot=spot)

        return TradeSignal.adjustment(
            legs_to_close=legs_to_close, legs_to_open=legs_to_open,
            reason=f"Reversion to ATM -> NEUTRAL (cycle {self._adj_cycle})",
            state_before="ADJUSTED", state_after="NEUTRAL",
            cycle=self._adj_cycle,
        )

    def _flip(self, timestamp, spot, prices,
              adj_sell_key, adj_buy_key) -> Optional[TradeSignal]:
        flip_sell_key     = f"{self._tested_side}_SELL"
        flip_buy_key      = f"{self._tested_side}_BUY"
        flip_hedge_strike = (self._atm_strike + self.hedge_offset
                             if self._tested_side == 'CE'
                             else self._atm_strike - self.hedge_offset)
        flip_hedge_sym = self._execution_handler.find_symbol(
            flip_hedge_strike, self._tested_side
        )
        flip_sell_sym = self._sym[f'{self._tested_side}_ATM']
        flip_sell_pr  = prices.get(flip_sell_sym, 0.0)

        legs_to_close = self._close_legs(timestamp, prices, adj_sell_key, adj_buy_key)
        legs_to_open  = []
        if flip_hedge_sym:
            legs_to_open.append(
                self._make_buy_leg(flip_buy_key, flip_hedge_sym,
                                   flip_hedge_strike, self._tested_side)
            )
        if flip_sell_sym and flip_sell_pr > 0:
            legs_to_open.append(
                self._make_sell_leg(flip_sell_key, flip_sell_sym,
                                    self._atm_strike, self._tested_side, flip_sell_pr)
            )

        self._state = StraddleState.FLIPPED
        self.logger.info("ADJUSTED -> FLIPPED", ts=timestamp, spot=spot,
                         tested=self._tested_side)

        return TradeSignal.adjustment(
            legs_to_close=legs_to_close, legs_to_open=legs_to_open,
            reason=f"ADJ SL hit -> FLIPPED to {self._tested_side} (cycle {self._adj_cycle})",
            state_before="ADJUSTED", state_after="FLIPPED",
            cycle=self._adj_cycle,
        )

    # ── FLIPPED ───────────────────────────────────────────────────────────────

    def _check_flipped(self, timestamp: str, spot: float,
                       prices: dict) -> Optional[TradeSignal]:
        untested          = 'PE' if self._tested_side == 'CE' else 'CE'
        flipped_sell_key  = f"{self._tested_side}_SELL"
        untested_sell_key = f"{untested}_SELL"

        if abs(spot - self._atm_strike) <= self.reversion_buffer:
            untested_hedge_strike = (self._atm_strike + self.hedge_offset
                                     if untested == 'CE'
                                     else self._atm_strike - self.hedge_offset)
            untested_hedge_sym = self._execution_handler.find_symbol(
                untested_hedge_strike, untested
            )
            untested_hedge_key = f"{untested}_BUY"
            legs_to_open = []
            if untested_hedge_sym and not self._position_book.has_leg(untested_hedge_key):
                legs_to_open.append(
                    self._make_buy_leg(untested_hedge_key, untested_hedge_sym,
                                       untested_hedge_strike, untested)
                )
            if self._position_book.has_leg(flipped_sell_key):
                leg    = self._position_book.get_leg(flipped_sell_key)
                curr_pr = prices.get(leg.symbol, 0.0)
                if curr_pr > 0:
                    leg.sl_price = round(curr_pr * (1 + self.sl_pct), 2)

            self._state       = StraddleState.NEUTRAL
            self._tested_side = None
            self.logger.info("FLIPPED -> NEUTRAL (reversion)", ts=timestamp, spot=spot)

            if legs_to_open:
                return TradeSignal.adjustment(
                    legs_to_close=[], legs_to_open=legs_to_open,
                    reason=f"FLIPPED -> NEUTRAL (reversion) at {timestamp}",
                    state_before="FLIPPED", state_after="NEUTRAL",
                    cycle=self._adj_cycle,
                )
            return None

        if self._sl_hit(prices, flipped_sell_key):
            flipped_buy_key  = f"{self._tested_side}_BUY"
            new_adj_side     = self._tested_side
            self._tested_side = untested
            adj_sell_key     = f"{new_adj_side}_SELL_ADJ"
            adj_buy_key      = f"{new_adj_side}_BUY_ADJ"
            adj_hedge_strike = (self._atm_strike + self.hedge_offset
                                if new_adj_side == 'CE'
                                else self._atm_strike - self.hedge_offset)
            adj_hedge_sym    = self._execution_handler.find_symbol(
                adj_hedge_strike, new_adj_side
            )
            adj_sell_sym = self._sym[f'{new_adj_side}_ATM']
            adj_sell_pr  = prices.get(adj_sell_sym, 0.0)

            legs_to_close = self._close_legs(
                timestamp, prices, flipped_sell_key, flipped_buy_key
            )
            legs_to_open = []
            if adj_hedge_sym:
                legs_to_open.append(
                    self._make_buy_leg(adj_buy_key, adj_hedge_sym,
                                       adj_hedge_strike, new_adj_side)
                )
            if adj_sell_sym and adj_sell_pr > 0:
                legs_to_open.append(
                    self._make_sell_leg(adj_sell_key, adj_sell_sym,
                                        self._atm_strike, new_adj_side, adj_sell_pr)
                )

            self._adj_cycle += 1
            self._state      = StraddleState.ADJUSTED
            self.logger.info("FLIPPED -> ADJUSTED (reversed)",
                             ts=timestamp, spot=spot,
                             new_tested=self._tested_side, cycle=self._adj_cycle)

            return TradeSignal.adjustment(
                legs_to_close=legs_to_close, legs_to_open=legs_to_open,
                reason=f"FLIPPED SL hit -> RE-ADJUSTED (cycle {self._adj_cycle})",
                state_before="FLIPPED", state_after="ADJUSTED",
                cycle=self._adj_cycle,
            )

        if self._sl_hit(prices, untested_sell_key):
            self._g1_active = True
            self._state     = StraddleState.ALL_OUT
            self.logger.info("FLIPPED -> ALL_OUT", ts=timestamp, spot=spot)
            open_keys     = [leg.key for leg in self._position_book.get_open_legs()]
            legs_to_close = self._close_legs(timestamp, prices, *open_keys)
            return TradeSignal.exit(
                legs=legs_to_close,
                reason="FLIPPED: untested SL hit -> ALL_OUT",
                state_before="FLIPPED", state_after="ALL_OUT",
                urgency=SignalUrgency.URGENT,
            )

    # ── G1 RE-ENTRY ───────────────────────────────────────────────────────────

    def _check_g1_reentry(self, timestamp: str, spot: float,
                           prices: dict) -> Optional[TradeSignal]:
        ce_curr = prices.get(self._sym['CE_ATM'], 0.0)
        pe_curr = prices.get(self._sym['PE_ATM'], 0.0)

        if ce_curr <= 0 or pe_curr <= 0:
            return None
        if ce_curr > self._orig_ce_premium or pe_curr > self._orig_pe_premium:
            return None

        legs_to_open = [
            self._make_buy_leg("CE_BUY", self._sym['CE_HEDGE'],
                               self._ce_hedge_strike, 'CE'),
            self._make_buy_leg("PE_BUY", self._sym['PE_HEDGE'],
                               self._pe_hedge_strike, 'PE'),
            self._make_sell_leg("CE_SELL", self._sym['CE_ATM'],
                                self._atm_strike, 'CE', ce_curr),
            self._make_sell_leg("PE_SELL", self._sym['PE_ATM'],
                                self._atm_strike, 'PE', pe_curr),
        ]

        self._adj_cycle  += 1
        self._g1_active   = False
        self._tested_side = None
        self._state       = StraddleState.NEUTRAL
        self.in_position  = True

        self.logger.info(
            "G1 RE-ENTRY",
            ts=timestamp, spot=spot,
            ce=ce_curr, pe=pe_curr, cycle=self._adj_cycle,
        )

        return TradeSignal.adjustment(
            legs_to_open=legs_to_open, legs_to_close=[],
            reason=(f"G1 re-entry at {timestamp} | "
                    f"CE={ce_curr:.2f} PE={pe_curr:.2f} (cycle {self._adj_cycle})"),
            state_before="ALL_OUT", state_after="NEUTRAL",
            cycle=self._adj_cycle,
        )

    # ==========================================================================
    # REPORTING HELPERS
    # ==========================================================================

    @property
    def state(self) -> StraddleState:
        return self._state

    @property
    def adjustment_cycles(self) -> int:
        return self._adj_cycle

    @property
    def g1_triggered(self) -> bool:
        return self._g1_active

    def __repr__(self) -> str:
        return (f"IronStraddleStrategy("
                f"state={self._state.value}, "
                f"cycles={self._adj_cycle}, "
                f"in_position={self.in_position})")
