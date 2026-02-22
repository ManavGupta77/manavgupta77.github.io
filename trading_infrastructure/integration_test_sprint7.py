# ==============================================================================
# INTEGRATION TEST — SPRINT 7
# MarketSession + IndicatorEngine
#
# Tests:
#   PART 1 — MarketSession: construction and registration
#   PART 2 — SessionResult: fields and __str__
#   PART 3 — SCENARIO A: Single strategy, no RiskGuard, no IndicatorEngine
#              Expected: Rs.-932.75 benchmark reproduced
#   PART 4 — SCENARIO B: Single strategy, production RiskGuard, no IndicatorEngine
#              Expected: Rs.-932.75, guard silent
#   PART 5 — SCENARIO C: Two strategies, isolation test
#              Strategy 1: production limits — runs to EOD
#              Strategy 2: tight cap (Rs.-15/lot) — halted
#   PART 6 — IndicatorEngine: unit tests (IV, PCR, decay, time, edge cases)
#   PART 7 — SCENARIO D: IndicatorEngine injected — benchmark still reproduced
#
# Run from project root:
#   cd C:\Rajat\trading_infrastructure
#   set PYTHONPATH=src
#   python integration_test_sprint7.py
# ==============================================================================

import sys
import io
import math

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

import logging
for _handler in logging.root.handlers:
    if hasattr(_handler, "stream") and hasattr(_handler.stream, "buffer"):
        _handler.stream = io.TextIOWrapper(
            _handler.stream.buffer, encoding="utf-8",
            errors="replace", line_buffering=True,
        )

# ── Helpers ───────────────────────────────────────────────────────────────────

PASS_COUNT = 0
FAIL_COUNT = 0

LOT_SIZE      = 65
DATE          = "2026-02-11"
EXPIRY        = "2026-02-17"
STRIKES       = [25800, 26000, 26200]
BENCHMARK_PNL = -932.75
TOLERANCE     =   1.00


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


def make_handler(date=DATE, expiry=EXPIRY):
    import inspect
    from execution import BacktestExecutionHandler
    params = list(inspect.signature(BacktestExecutionHandler.__init__).parameters.keys())
    if "date" in params:
        return BacktestExecutionHandler(date, expiry)
    h = BacktestExecutionHandler()
    return h


# ==============================================================================
# MAIN TEST
# ==============================================================================

def run_sprint7_test():
    global PASS_COUNT, FAIL_COUNT

    print()
    print("=" * 70)
    print("  INTEGRATION TEST — Sprint 7")
    print("  MarketSession + IndicatorEngine, 2026-02-11")
    print("=" * 70)

    # ── Imports ────────────────────────────────────────────────────────────────
    try:
        from simulation_lab.market_session import MarketSession, SessionResult
        from indicators.indicator_engine import IndicatorEngine, IndicatorSnapshot
        from strategies.risk.risk_guard import RiskGuard
        from strategies.options_selling.iron_straddle import IronStraddleStrategy
        print("\n  [INFO] All imports successful\n")
    except ImportError as e:
        print(f"\n  [FATAL] Import failed: {e}")
        sys.exit(1)

    # ==========================================================================
    # PART 1 — MarketSession: construction and registration
    # ==========================================================================
    section("PART 1 — MarketSession: construction and registration")

    session = MarketSession(date=DATE, expiry=EXPIRY)
    check("MarketSession constructed",       session is not None)
    check("MarketSession.date correct",      session.date == DATE)
    check("MarketSession.expiry correct",    session.expiry == EXPIRY)
    check("No slots before add_strategy()",  len(session._slots) == 0)

    rg_test = RiskGuard(max_daily_loss_per_lot=-3000, lot_size=LOT_SIZE)
    session.add_strategy(
        strategy         = IronStraddleStrategy(),
        handler          = make_handler(),
        strikes          = STRIKES,
        risk_guard       = rg_test,
        indicator_engine = None,
    )
    check("One slot after add_strategy()",   len(session._slots) == 1)
    check("Slot 0 has risk_guard",           session._slots[0].risk_guard is not None)
    check("Slot 0 indicator_engine is None", session._slots[0].indicator_engine is None)

    session.add_strategy(
        strategy         = IronStraddleStrategy(),
        handler          = make_handler(),
        strikes          = STRIKES,
        risk_guard       = None,
        indicator_engine = None,
    )
    check("Two slots after second add",      len(session._slots) == 2)
    check("Slot 1 risk_guard is None",       session._slots[1].risk_guard is None)

    # ==========================================================================
    # PART 2 — SessionResult: fields and __str__
    # ==========================================================================
    section("PART 2 — SessionResult: fields and __str__")

    sr = SessionResult(
        date="2026-02-11", strategy_name="Test", entry_spot=26000.0,
        atm_strike=26000, realised_pnl=-500.0, total_legs_traded=4,
        adjustment_cycles=0, g1_triggered=False, open_legs_at_eod=0,
        halted_by_risk=False, indicator_ticks=0,
    )
    check("SessionResult constructed",       sr is not None)
    check("SessionResult.date correct",      sr.date == "2026-02-11")
    check("SessionResult.realised_pnl",      abs(sr.realised_pnl - (-500.0)) < 0.01)
    check("SessionResult.halted_by_risk",    sr.halted_by_risk == False)
    check("SessionResult.indicator_ticks",   sr.indicator_ticks == 0)
    check("SessionResult.__str__ works",     "SESSION RESULT" in str(sr))

    # ==========================================================================
    # PART 3 — SCENARIO A: Single strategy, no RiskGuard, no IndicatorEngine
    # ==========================================================================
    section("PART 3 — SCENARIO A: Single strategy, no RiskGuard, no IndicatorEngine")
    print(f"\n  [INFO] Expected: Rs.{BENCHMARK_PNL:,.2f} — benchmark through MarketSession\n")

    session_a = MarketSession(date=DATE, expiry=EXPIRY)
    session_a.add_strategy(
        strategy=IronStraddleStrategy(), handler=make_handler(),
        strikes=STRIKES, risk_guard=None, indicator_engine=None,
    )
    results_a = session_a.run()
    for r in results_a:
        print(r)

    check("Scenario A: returns list",              isinstance(results_a, list))
    check("Scenario A: 1 result",                  len(results_a) == 1)
    ra = results_a[0]
    check("Scenario A: is SessionResult",          isinstance(ra, SessionResult))
    check("Scenario A: date correct",              ra.date == DATE)
    check("Scenario A: ATM = 26000",               ra.atm_strike == 26000)
    check("Scenario A: 0 open legs at EOD",        ra.open_legs_at_eod == 0,
          f"got {ra.open_legs_at_eod}")
    check("Scenario A: 1 adjustment cycle",        ra.adjustment_cycles == 1,
          f"got {ra.adjustment_cycles}")
    check("Scenario A: not halted",                ra.halted_by_risk == False)
    check("Scenario A: indicator_ticks = 0",       ra.indicator_ticks == 0)
    pnl_diff_a = abs(ra.realised_pnl - BENCHMARK_PNL)
    check(
        f"Scenario A: BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced "
        f"(tolerance Rs.{TOLERANCE:.2f})",
        pnl_diff_a <= TOLERANCE,
        f"got Rs.{ra.realised_pnl:,.2f}  diff={pnl_diff_a:.2f}",
    )

    # ==========================================================================
    # PART 4 — SCENARIO B: Production RiskGuard (silent)
    # ==========================================================================
    section("PART 4 — SCENARIO B: Single strategy, production RiskGuard (silent)")
    print(f"\n  [INFO] max_daily_loss = -3000/lot x {LOT_SIZE} = Rs.-195,000 (won't fire)")
    print(f"  [INFO] Expected: Rs.{BENCHMARK_PNL:,.2f}, guard silent\n")

    rg_b = RiskGuard(max_daily_loss_per_lot=-3000, max_trade_loss_per_lot=-1500,
                     max_adj_cycles=2, lot_size=LOT_SIZE)
    session_b = MarketSession(date=DATE, expiry=EXPIRY)
    session_b.add_strategy(IronStraddleStrategy(), make_handler(), STRIKES, rg_b, None)
    results_b = session_b.run()
    for r in results_b:
        print(r)

    rb = results_b[0]
    check("Scenario B: RiskGuard NOT halted",      rg_b.is_halted == False)
    check("Scenario B: not halted in result",      rb.halted_by_risk == False)
    pnl_diff_b = abs(rb.realised_pnl - BENCHMARK_PNL)
    check(f"Scenario B: BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced",
          pnl_diff_b <= TOLERANCE,
          f"got Rs.{rb.realised_pnl:,.2f}  diff={pnl_diff_b:.2f}")
    check("Scenario B: RiskGuard daily_pnl matches",
          abs(rg_b.daily_pnl - rb.realised_pnl) <= TOLERANCE,
          f"rg={rg_b.daily_pnl:.2f}  pb={rb.realised_pnl:.2f}")

    # ==========================================================================
    # PART 5 — SCENARIO C: Two strategies, isolation test
    # ==========================================================================
    section("PART 5 — SCENARIO C: Two strategies — isolation test")
    TIGHT_LIMIT = -15
    print(f"\n  [INFO] Strategy 1: production limits (Rs.-3000/lot) — runs to EOD")
    print(f"  [INFO] Strategy 2: tight cap (Rs.{TIGHT_LIMIT}/lot = "
          f"Rs.{TIGHT_LIMIT * LOT_SIZE:,.0f}) — hard stop fires")
    print(f"  [INFO] Expected: Strategy 1 unaffected, Strategy 2 halted\n")

    rg_c1 = RiskGuard(max_daily_loss_per_lot=-3000, max_trade_loss_per_lot=-1500,
                      max_adj_cycles=2, lot_size=LOT_SIZE)
    rg_c2 = RiskGuard(max_daily_loss_per_lot=TIGHT_LIMIT, max_trade_loss_per_lot=-1500,
                      max_adj_cycles=2, lot_size=LOT_SIZE)

    session_c = MarketSession(date=DATE, expiry=EXPIRY)
    session_c.add_strategy(IronStraddleStrategy(), make_handler(), STRIKES, rg_c1)
    session_c.add_strategy(IronStraddleStrategy(), make_handler(), STRIKES, rg_c2)
    results_c = session_c.run()
    for r in results_c:
        print(r)

    check("Scenario C: 2 results",                 len(results_c) == 2)
    rc1, rc2 = results_c[0], results_c[1]
    pnl_diff_c1 = abs(rc1.realised_pnl - BENCHMARK_PNL)
    check("Scenario C: Strategy 1 BENCHMARK reproduced",
          pnl_diff_c1 <= TOLERANCE,
          f"got Rs.{rc1.realised_pnl:,.2f}  diff={pnl_diff_c1:.2f}")
    check("Scenario C: Strategy 1 NOT halted",     rg_c1.is_halted == False)
    check("Scenario C: Strategy 1 0 open legs",    rc1.open_legs_at_eod == 0,
          f"got {rc1.open_legs_at_eod}")
    check("Scenario C: Strategy 2 IS halted",      rg_c2.is_halted == True)
    check("Scenario C: Strategy 2 halted in result", rc2.halted_by_risk == True)
    check("Scenario C: Strategy 2 PnL negative",  rc2.realised_pnl < 0,
          f"got Rs.{rc2.realised_pnl:.2f}")
    check(f"Scenario C: Strategy 2 daily_pnl <= Rs.{TIGHT_LIMIT * LOT_SIZE:,.0f}",
          rg_c2.daily_pnl <= TIGHT_LIMIT * LOT_SIZE,
          f"got {rg_c2.daily_pnl:.2f}")
    check("Scenario C: Strategy 2 differs from Strategy 1",
          abs(rc2.realised_pnl - rc1.realised_pnl) > 0.01,
          f"strat2={rc2.realised_pnl:.2f}  strat1={rc1.realised_pnl:.2f}")

    # ==========================================================================
    # PART 6 — IndicatorEngine: unit tests
    # ==========================================================================
    section("PART 6 — IndicatorEngine: unit tests")

    engine = IndicatorEngine(
        opening_spot=26000.0,
        atm_ce_symbol="NIFTY17FEB2626000CE",
        atm_pe_symbol="NIFTY17FEB2626000PE",
        entry_ce_premium=130.3,
        entry_pe_premium=115.5,
        pcr_window=3,
    )
    check("IndicatorEngine constructed",           engine is not None)
    check("opening_spot correct",                  engine.opening_spot == 26000.0)
    check("pcr_window correct",                    engine.pcr_window == 3)
    check("ticks_computed starts at 0",            engine.ticks_computed == 0)

    engine.reset_day()
    check("reset_day clears ticks",                engine.ticks_computed == 0)

    # Mock tick
    class _MockTick:
        def __init__(self, spot, prices, ts="2026-02-11T09:30:00+05:30"):
            self.spot          = spot
            self.option_prices = prices
            self.timestamp     = ts

    tick1 = _MockTick(
        spot=25977.2,
        prices={"NIFTY17FEB2626000CE": 130.3, "NIFTY17FEB2626000PE": 115.5},
        ts="2026-02-11T09:30:00+05:30",
    )
    snap1 = engine.compute(tick=tick1, days_to_expiry=6.0, atm_strike=26000)

    check("Snapshot is IndicatorSnapshot",         isinstance(snap1, IndicatorSnapshot))
    check("Snapshot timestamp = '09:30'",          snap1.timestamp == "09:30")
    check("Snapshot spot correct",                 abs(snap1.spot - 25977.2) < 0.01)
    check("spot_change_pct computed",              snap1.spot_change_pct is not None,
          f"got {snap1.spot_change_pct}")
    check("spot_vs_atm = spot - ATM",
          snap1.spot_vs_atm is not None and abs(snap1.spot_vs_atm - (25977.2 - 26000)) < 0.1,
          f"got {snap1.spot_vs_atm}")
    check("atm_premium_ce = 130.3",
          snap1.atm_premium_ce is not None and abs(snap1.atm_premium_ce - 130.3) < 0.01)
    check("atm_premium_pe = 115.5",
          snap1.atm_premium_pe is not None and abs(snap1.atm_premium_pe - 115.5) < 0.01)
    check("combined_premium = 245.8",
          snap1.combined_premium is not None and abs(snap1.combined_premium - 245.8) < 0.1)
    check("premium_decay_pct = 0 at entry",
          snap1.premium_decay_pct is not None and abs(snap1.premium_decay_pct) < 0.1,
          f"got {snap1.premium_decay_pct}")
    check("pcr_current = CE/PE",
          snap1.pcr_current is not None and
          abs(snap1.pcr_current - (130.3 / 115.5)) < 0.001)
    check("pcr_rolling = pcr_current (1st tick)",
          snap1.pcr_rolling is not None and
          abs(snap1.pcr_rolling - snap1.pcr_current) < 0.001)
    check("IV CE computed",                        snap1.atm_iv_ce is not None,
          f"got {snap1.atm_iv_ce}")
    check("IV PE computed",                        snap1.atm_iv_pe is not None,
          f"got {snap1.atm_iv_pe}")
    check("IV avg = mean(CE, PE)",
          snap1.atm_iv_avg is not None and
          abs(snap1.atm_iv_avg - (snap1.atm_iv_ce + snap1.atm_iv_pe) / 2) < 1e-5)
    check("IV avg is realistic (5%–200%)",
          snap1.atm_iv_avg is not None and 0.05 <= snap1.atm_iv_avg <= 2.0,
          f"got {snap1.atm_iv_avg * 100:.1f}%")
    check("minutes_since_open = 15",              snap1.minutes_since_open == 15,
          f"got {snap1.minutes_since_open}")
    check("time_decay_fraction > 0",
          snap1.time_decay_fraction is not None and snap1.time_decay_fraction > 0)
    check("ticks_computed = 1 after compute",      engine.ticks_computed == 1)

    # Second tick — rolling PCR and premium decay
    tick2 = _MockTick(
        spot=25950.0,
        prices={"NIFTY17FEB2626000CE": 125.0, "NIFTY17FEB2626000PE": 120.0},
        ts="2026-02-11T09:31:00+05:30",
    )
    snap2 = engine.compute(tick=tick2, days_to_expiry=6.0, atm_strike=26000)
    check("Tick 2: premium decayed (positive decay)",
          snap2.premium_decay_pct is not None and snap2.premium_decay_pct > 0,
          f"got {snap2.premium_decay_pct}")
    expected_rolling = ((snap1.pcr_current or 0) + (snap2.pcr_current or 0)) / 2
    check("Tick 2: pcr_rolling = avg of 2 ticks",
          snap2.pcr_rolling is not None and
          abs(snap2.pcr_rolling - expected_rolling) < 0.001,
          f"rolling={snap2.pcr_rolling}  expected={expected_rolling:.4f}")

    # set_entry_premiums
    engine.set_entry_premiums(130.3, 115.5)
    check("set_entry_premiums updates correctly",
          engine.entry_ce_premium == 130.3 and engine.entry_pe_premium == 115.5)

    # set_atm_symbols
    engine.set_atm_symbols("NEW_CE_SYM", "NEW_PE_SYM", opening_spot=26100.0)
    check("set_atm_symbols updates CE symbol",    engine.atm_ce_symbol == "NEW_CE_SYM")
    check("set_atm_symbols updates PE symbol",    engine.atm_pe_symbol == "NEW_PE_SYM")
    check("set_atm_symbols updates opening_spot", engine.opening_spot == 26100.0)

    # Edge case: no symbols, empty prices
    engine_empty = IndicatorEngine()
    tick_empty   = _MockTick(spot=26000.0, prices={})
    snap_empty   = engine_empty.compute(tick=tick_empty, days_to_expiry=6.0, atm_strike=26000)
    check("Empty prices → atm_premium_ce is None", snap_empty.atm_premium_ce is None)
    check("Empty prices → IV is None",             snap_empty.atm_iv_ce is None)
    check("Empty prices → PCR is None",            snap_empty.pcr_current is None)
    check("Spot always returned even with no syms", abs(snap_empty.spot - 26000.0) < 0.01)

    # ==========================================================================
    # PART 7 — SCENARIO D: IndicatorEngine injected, benchmark preserved
    # ==========================================================================
    section("PART 7 — SCENARIO D: IndicatorEngine injected — benchmark preserved")
    print(f"\n  [INFO] IronStraddleStrategy has no inject_indicator_engine()")
    print(f"  [INFO] Engine computes ticks — strategy trade logic is unaffected")
    print(f"  [INFO] Expected: Rs.{BENCHMARK_PNL:,.2f} still reproduced\n")

    ie_d = IndicatorEngine(
        opening_spot=25976.05,
        atm_ce_symbol="NIFTY17FEB2626000CE",
        atm_pe_symbol="NIFTY17FEB2626000PE",
        entry_ce_premium=130.3,
        entry_pe_premium=115.5,
    )
    session_d = MarketSession(date=DATE, expiry=EXPIRY)
    session_d.add_strategy(
        strategy=IronStraddleStrategy(), handler=make_handler(),
        strikes=STRIKES, risk_guard=None, indicator_engine=ie_d,
    )
    results_d = session_d.run()
    for r in results_d:
        print(r)

    rd = results_d[0]
    pnl_diff_d = abs(rd.realised_pnl - BENCHMARK_PNL)
    check(
        f"Scenario D: BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced with engine",
        pnl_diff_d <= TOLERANCE,
        f"got Rs.{rd.realised_pnl:,.2f}  diff={pnl_diff_d:.2f}",
    )
    check("Scenario D: indicator_ticks > 0 (engine ran each tick)",
          rd.indicator_ticks > 0, f"got {rd.indicator_ticks}")
    check("Scenario D: IndicatorEngine.ticks_computed > 0",
          ie_d.ticks_computed > 0, f"got {ie_d.ticks_computed}")
    check("Scenario D: 1 adjustment cycle (state machine unaffected)",
          rd.adjustment_cycles == 1, f"got {rd.adjustment_cycles}")
    check("Scenario D: 0 open legs at EOD",
          rd.open_legs_at_eod == 0, f"got {rd.open_legs_at_eod}")

    # ==========================================================================
    # FINAL SUMMARY
    # ==========================================================================
    total = PASS_COUNT + FAIL_COUNT
    print(f"\n{'='*70}")
    print(f"  TEST RESULTS — {PASS_COUNT}/{total} PASSED")
    print(f"{'='*70}\n")

    if FAIL_COUNT == 0:
        print("  ALL TESTS PASSED\n")
        print("  Sprint 7 complete.")
        print("  MarketSession correctly:")
        print("    - Drives strategies in lock-step with per-slot isolation")
        print("    - Preserves Rs.-932.75 benchmark (= Coordinator equivalence)")
        print("    - Injects IndicatorEngine per slot without affecting trade logic")
        print("    - Reports indicator_ticks in SessionResult")
        print()
        print("  IndicatorEngine correctly:")
        print("    - Computes IV (Black-Scholes bisection), PCR, decay, time")
        print("    - Rolling PCR window works correctly across ticks")
        print("    - Handles missing prices gracefully (None fields)")
        print("    - reset_day() / set_entry_premiums() / set_atm_symbols() all work")
        print()
        print("  READY FOR SPRINT 8: PaperExecutionHandler + live paper trading\n")
    else:
        print(f"  {FAIL_COUNT} TEST(S) FAILED — see above\n")

    return FAIL_COUNT == 0


if __name__ == "__main__":
    success = run_sprint7_test()
    sys.exit(0 if success else 1)
