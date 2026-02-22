# ==============================================================================
# INTEGRATION TEST — SPRINT 4
# IronStraddleStrategy + BacktestRunner + BacktestExecutionHandler
#
# BENCHMARK: Rs.-932.75 net PnL on 2026-02-11
#
# Run from project root:
#   cd C:\Rajat\trading_infrastructure
#   set PYTHONPATH=src
#   python integration_test_sprint4.py
# ==============================================================================

import sys

# ── Helpers ────────────────────────────────────────────────────────────────────

PASS_COUNT = 0
FAIL_COUNT = 0

def check(label: str, condition: bool, extra: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    status = "PASS" if condition else "FAIL"
    tag    = f"  [{status}]  {label}"
    if extra:
        tag += f"  ({extra})"
    print(tag)
    if condition:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1

def section(title: str) -> None:
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ==============================================================================
# SPRINT 4 TEST
# ==============================================================================

def run_sprint4_test():
    global PASS_COUNT, FAIL_COUNT

    print()
    print("=" * 70)
    print("  INTEGRATION TEST — Sprint 4")
    print("  IronStraddleStrategy — Benchmark: Rs.-932.75 on 2026-02-11")
    print("=" * 70)

    # ── Imports ────────────────────────────────────────────────────────────────
    try:
        from strategies.options_selling.iron_straddle import (
            IronStraddleStrategy, StraddleState
        )
        from simulation_lab.backtest_runner import BacktestRunner, BacktestResult
        from execution import BacktestExecutionHandler
        from strategies.building_blocks import PositionBook
        print("\n  [INFO] All imports successful\n")
    except ImportError as e:
        print(f"\n  [FATAL] Import failed: {e}")
        sys.exit(1)

    # Constants for this session
    DATE       = "2026-02-11"
    EXPIRY     = "2026-02-17"
    ATM_STRIKE = 26000
    STRIKES    = [25800, 26000, 26200]   # pe_hedge, atm, ce_hedge

    # ──────────────────────────────────────────────────────────────────────────
    section("PART 1 — IronStraddleStrategy: construction and defaults")
    # ──────────────────────────────────────────────────────────────────────────

    strat = IronStraddleStrategy()

    check("Default lot_size = 65",            strat.lot_size == 65)
    check("Default sl_pct = 0.30",            strat.sl_pct == 0.30)
    check("Default hedge_offset = 200",       strat.hedge_offset == 200)
    check("Default reversion_buffer = 15",    strat.reversion_buffer == 15)
    check("Default entry_time = '09:30'",     strat.entry_time == "09:30")
    check("Default exit_time = '15:20'",      strat.exit_time == "15:20")
    check("Default strike_step = 50",         strat.strike_step == 50)
    check("Initial state = NEUTRAL",          strat.state == StraddleState.NEUTRAL)
    check("Initial in_position = False",      strat.in_position == False)
    check("Initial adjustment_cycles = 0",    strat.adjustment_cycles == 0)
    check("Initial g1_triggered = False",     strat.g1_triggered == False)
    check("Inherits name = 'Iron Straddle'",  strat.name == "Iron Straddle")
    check("Inherits instrument = 'NIFTY'",    strat.instrument == "NIFTY")

    section("PART 1b — Custom parameter override")

    custom = IronStraddleStrategy(lot_size=50, sl_pct=0.25, hedge_offset=150)
    check("Custom lot_size = 50",      custom.lot_size == 50)
    check("Custom sl_pct = 0.25",      custom.sl_pct == 0.25)
    check("Custom hedge_offset = 150", custom.hedge_offset == 150)

    # ──────────────────────────────────────────────────────────────────────────
    section("PART 2 — BacktestRunner: construction")
    # ──────────────────────────────────────────────────────────────────────────

    # Handler takes date + expiry at construction time
    handler = BacktestExecutionHandler(date=DATE, expiry=EXPIRY)
    runner  = BacktestRunner(IronStraddleStrategy(), handler)

    check("Runner has strategy",      runner.strategy is not None)
    check("Runner has handler",       runner.handler is not None)
    check("Runner strategy is IronStraddleStrategy",
          runner.strategy.__class__.__name__ == "IronStraddleStrategy")
    check("Handler date correct",     runner.handler.date == DATE)
    check("Handler expiry correct",   runner.handler.expiry == EXPIRY)

    # ──────────────────────────────────────────────────────────────────────────
    section("PART 3 — Data loading and handler readiness")
    # ──────────────────────────────────────────────────────────────────────────

    print(f"  [INFO] Loading: date={DATE}  expiry={EXPIRY}  strikes={STRIKES}")

    # load_data() takes only strikes — date/expiry already set on handler
    ok = handler.load_data(STRIKES)
    check("load_data() returns True",        ok == True)
    check("handler._data_loaded = True",     handler._data_loaded == True)

    timestamps = handler.get_timestamps()
    check("get_timestamps() returns list",   isinstance(timestamps, list))
    check("351+ timestamps loaded",          len(timestamps) >= 351,
          f"got {len(timestamps)}")

    opening_spot = (handler.get_spot_price("09:15") or
                    handler.get_spot_price("09:30"))
    check("Opening spot > 0",                opening_spot > 0,
          f"got {opening_spot}")

    atm = handler.get_atm_strike(opening_spot)
    check(f"ATM = {ATM_STRIKE}",             atm == ATM_STRIKE,
          f"got {atm}")

    ce_sym = handler.find_symbol(ATM_STRIKE, 'CE')
    pe_sym = handler.find_symbol(ATM_STRIKE, 'PE')
    check("CE ATM symbol found",             bool(ce_sym), f"got {ce_sym}")
    check("PE ATM symbol found",             bool(pe_sym), f"got {pe_sym}")
    check("CE symbol contains 26000CE",      ce_sym and "26000CE" in ce_sym)
    check("PE symbol contains 26000PE",      pe_sym and "26000PE" in pe_sym)

    # ──────────────────────────────────────────────────────────────────────────
    section("PART 4 — on_market_open: symbol resolution and state reset")
    # ──────────────────────────────────────────────────────────────────────────

    strategy      = IronStraddleStrategy()
    position_book = PositionBook(strategy_name="Iron Straddle")
    strategy.inject_services(
        execution_handler=handler,
        position_book=position_book,
        risk_guard=None,
    )

    strategy.on_market_open(session_date=DATE, spot_price=opening_spot)

    check("ATM strike set correctly",        strategy._atm_strike == ATM_STRIKE)
    check("CE hedge strike = ATM + 200",
          strategy._ce_hedge_strike == ATM_STRIKE + 200)
    check("PE hedge strike = ATM - 200",
          strategy._pe_hedge_strike == ATM_STRIKE - 200)
    check("CE_ATM symbol resolved",          bool(strategy._sym.get('CE_ATM')))
    check("PE_ATM symbol resolved",          bool(strategy._sym.get('PE_ATM')))
    check("CE_HEDGE symbol resolved",        bool(strategy._sym.get('CE_HEDGE')))
    check("PE_HEDGE symbol resolved",        bool(strategy._sym.get('PE_HEDGE')))
    check("State reset to NEUTRAL",          strategy.state == StraddleState.NEUTRAL)
    check("_entered reset to False",         strategy._entered == False)

    # ──────────────────────────────────────────────────────────────────────────
    section("PART 5 — on_entry_signal: 4-leg iron straddle at 09:30")
    # ──────────────────────────────────────────────────────────────────────────

    from datetime import datetime
    days_to_expiry = max((datetime.strptime(EXPIRY, "%Y-%m-%d") -
                          datetime.strptime(DATE,   "%Y-%m-%d")).days, 0.5)

    ENTRY_TS   = f"{DATE}T09:30:00+05:30"
    entry_tick = handler.build_tick(
        timestamp=ENTRY_TS,
        expiry_date=EXPIRY,
        days_to_expiry=days_to_expiry,
    )
    check("build_tick at 09:30 returns tick", entry_tick is not None)

    entry_prices = entry_tick.option_prices if entry_tick else {}
    entry_spot_v = entry_tick.spot if entry_tick else 0.0
    entry_signal = strategy.on_entry_signal("09:30", entry_spot_v, entry_prices)

    check("on_entry_signal returns TradeSignal",  entry_signal is not None)
    check("Signal type = ENTRY",
          entry_signal and entry_signal.signal_type.value == "ENTRY")
    check("Signal has 4 legs to open",
          entry_signal and len(entry_signal.legs_to_open) == 4)
    check("Strategy in_position = True",          strategy.in_position == True)
    check("Strategy _entered = True",             strategy._entered == True)

    # Second call must return None
    second_call = strategy.on_entry_signal("09:30", entry_spot_v, entry_prices)
    check("Second call to on_entry_signal returns None", second_call is None)

    # Execute and check fills
    fills = handler.execute(entry_signal, ENTRY_TS)
    check("execute() returns 4 fills",   len(fills) == 4)
    check("All fills are FILLED",
          all(f.status.value == "FILLED" for f in fills))

    for fill in fills:
        strategy.on_order_update(fill)

    check("PositionBook has 4 open legs", len(position_book.get_open_legs()) == 4)
    check("CE_SELL open",                 position_book.has_leg("CE_SELL"))
    check("PE_SELL open",                 position_book.has_leg("PE_SELL"))
    check("CE_BUY open",                  position_book.has_leg("CE_BUY"))
    check("PE_BUY open",                  position_book.has_leg("PE_BUY"))

    ce_fill = next((f for f in fills if "26000CE" in f.leg.symbol), None)
    pe_fill = next((f for f in fills if "26000PE" in f.leg.symbol), None)
    check("CE entry price = Rs.130.30",
          ce_fill and abs(ce_fill.fill_price - 130.30) < 0.01,
          f"got {ce_fill.fill_price if ce_fill else 'N/A'}")
    check("PE entry price = Rs.115.50",
          pe_fill and abs(pe_fill.fill_price - 115.50) < 0.01,
          f"got {pe_fill.fill_price if pe_fill else 'N/A'}")

    combined = sum(
        f.fill_price for f in fills
        if any(x in f.leg.symbol for x in ["26000CE", "26000PE"])
    )
    print(f"  [INFO] Combined sell premium: Rs.{combined:.2f}")

    # ──────────────────────────────────────────────────────────────────────────
    section("PART 6 — Full backtest run: benchmark Rs.-932.75")
    # ──────────────────────────────────────────────────────────────────────────

    print(f"  [INFO] Running full day backtest on {DATE}...")
    print(f"  [INFO] Benchmark target: Rs.-932.75\n")

    fresh_handler  = BacktestExecutionHandler(date=DATE, expiry=EXPIRY)
    fresh_strategy = IronStraddleStrategy()
    fresh_runner   = BacktestRunner(fresh_strategy, fresh_handler)

    result = fresh_runner.run(strikes=STRIKES)

    print(f"\n{result}")

    check("Result is BacktestResult",     isinstance(result, BacktestResult))
    check("Result date correct",          result.date == DATE)
    check("ATM strike = 26000",           result.atm_strike == ATM_STRIKE,
          f"got {result.atm_strike}")
    check("No open legs at EOD",          result.open_legs_at_eod == 0,
          f"got {result.open_legs_at_eod}")
    check("Adjustment cycles > 0",        result.adjustment_cycles > 0,
          f"got {result.adjustment_cycles}")

    # ── THE BENCHMARK ─────────────────────────────────────────────────────────
    BENCHMARK_PNL = -932.75
    TOLERANCE     = 1.00    # Rs.1 rounding tolerance
    pnl_diff      = abs(result.realised_pnl - BENCHMARK_PNL)
    check(
        f"BENCHMARK: Realised PnL = Rs.{BENCHMARK_PNL:,.2f}  "
        f"(tolerance Rs.{TOLERANCE:.2f})",
        pnl_diff <= TOLERANCE,
        f"got Rs.{result.realised_pnl:,.2f}  diff={pnl_diff:.2f}",
    )

    # ──────────────────────────────────────────────────────────────────────────
    # FINAL SUMMARY
    # ──────────────────────────────────────────────────────────────────────────
    total = PASS_COUNT + FAIL_COUNT
    print(f"\n{'='*70}")
    print(f"  TEST RESULTS — {PASS_COUNT}/{total} PASSED")
    print(f"{'='*70}\n")

    if FAIL_COUNT == 0:
        print("  ALL TESTS PASSED\n")
        print("  Sprint 4 complete.")
        print("  IronStraddleStrategy reproduces the Rs.-932.75 benchmark.")
        print("  READY FOR SPRINT 5: RiskGuard\n")
    else:
        print(f"  {FAIL_COUNT} TEST(S) FAILED — see above for details\n")

    return FAIL_COUNT == 0


if __name__ == "__main__":
    success = run_sprint4_test()
    sys.exit(0 if success else 1)
