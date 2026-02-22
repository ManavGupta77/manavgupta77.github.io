# ==============================================================================
# INTEGRATION TEST — SPRINT 6
# PortfolioCoordinator — per-strategy RiskGuard, multi-strategy backtest
#
# Tests the PortfolioCoordinator on 2026-02-11 with three scenarios:
#
#   SCENARIO A — Single strategy through Coordinator, no RiskGuard
#     Expected: Rs.-932.75 (same benchmark as Sprint 4/5). Proves the
#     Coordinator's tick loop is equivalent to BacktestRunner.
#
#   SCENARIO B — Single strategy through Coordinator, production RiskGuard
#     max_daily_loss_per_lot=-3000, lot_size=65 → Rs.-195,000 limit (won't fire)
#     Expected: Rs.-932.75. Guard stays silent. RiskGuard NOT halted.
#
#   SCENARIO C — Two strategies through Coordinator, one with tight RiskGuard
#     Strategy 1: production limits (Rs.-3000/lot) — should run to EOD
#     Strategy 2: tight cap (Rs.-15/lot = Rs.-975) — hard stop fires
#     Expected:
#       - Strategy 1: Rs.-932.75 (unaffected by Strategy 2's halt)
#       - Strategy 2: hard stop fires, guard halted, PnL != Rs.-932.75
#       - strategies_halted = 1
#       - total_pnl = Strategy1_pnl + Strategy2_pnl
#
# This proves per-strategy isolation: one halt does not affect the other.
#
# Run from project root:
#   cd C:\Rajat\trading_infrastructure
#   set PYTHONPATH=src
#   python integration_test_sprint6.py
# ==============================================================================

import sys
import io
import inspect

# ── Unicode fix for Windows cp1252 console ────────────────────────────────────
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

# ── Fix logging stream handlers for Windows cp1252 ────────────────────────────
import logging
for _handler in logging.root.handlers:
    if hasattr(_handler, 'stream') and hasattr(_handler.stream, 'buffer'):
        _handler.stream = io.TextIOWrapper(
            _handler.stream.buffer, encoding="utf-8",
            errors="replace", line_buffering=True,
        )

# ── Helpers ───────────────────────────────────────────────────────────────────

PASS_COUNT = 0
FAIL_COUNT = 0

LOT_SIZE = 65
DATE     = "2026-02-11"
EXPIRY   = "2026-02-17"
STRIKES  = [25800, 26000, 26200]

BENCHMARK_PNL = -932.75
TOLERANCE     =    1.00


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


def make_handler(date=None, expiry=None):
    """Construct a BacktestExecutionHandler (new-style or old-style)."""
    from execution import BacktestExecutionHandler
    params = list(inspect.signature(BacktestExecutionHandler.__init__).parameters.keys())
    if 'date' in params:
        return BacktestExecutionHandler(date, expiry)
    h = BacktestExecutionHandler()
    return h


# ==============================================================================
# SPRINT 6 TEST
# ==============================================================================

def run_sprint6_test():
    global PASS_COUNT, FAIL_COUNT

    print()
    print("=" * 70)
    print("  INTEGRATION TEST — Sprint 6")
    print("  PortfolioCoordinator — per-strategy RiskGuard, 2026-02-11")
    print("=" * 70)

    # ── Imports ────────────────────────────────────────────────────────────────
    try:
        from strategies.coordinator import PortfolioCoordinator, PortfolioResult
        from strategies.risk.risk_guard import RiskGuard
        from strategies.options_selling.iron_straddle import IronStraddleStrategy
        from simulation_lab.backtest_runner import BacktestResult
        print("\n  [INFO] All imports successful\n")
    except ImportError as e:
        print(f"\n  [FATAL] Import failed: {e}")
        sys.exit(1)

    # ==========================================================================
    # PART 1 — PortfolioCoordinator: construction and add_strategy
    # ==========================================================================
    section("PART 1 — PortfolioCoordinator: construction and registration")

    coord = PortfolioCoordinator(date=DATE, expiry=EXPIRY)
    check("Coordinator constructed",          coord is not None)
    check("Coordinator.date correct",         coord.date == DATE)
    check("Coordinator.expiry correct",       coord.expiry == EXPIRY)
    check("No slots before add_strategy()",   len(coord._slots) == 0)

    # Add one strategy (don't run yet)
    h_test   = make_handler(DATE, EXPIRY)
    strat_t  = IronStraddleStrategy()
    rg_test  = RiskGuard(max_daily_loss_per_lot=-3000, lot_size=LOT_SIZE)
    coord.add_strategy(strat_t, h_test, STRIKES, risk_guard=rg_test)
    check("One slot after add_strategy()",    len(coord._slots) == 1)

    # Add a second strategy without risk guard
    h_test2  = make_handler(DATE, EXPIRY)
    strat_t2 = IronStraddleStrategy()
    coord.add_strategy(strat_t2, h_test2, STRIKES, risk_guard=None)
    check("Two slots after second add",       len(coord._slots) == 2)
    check("Slot 0 has risk_guard",            coord._slots[0].risk_guard is not None)
    check("Slot 1 risk_guard is None",        coord._slots[1].risk_guard is None)

    # ==========================================================================
    # PART 2 — PortfolioResult dataclass
    # ==========================================================================
    section("PART 2 — PortfolioResult: fields and __str__")

    dummy_result = BacktestResult(
        date="2026-02-11", strategy_name="Test", entry_spot=26000.0,
        atm_strike=26000, realised_pnl=-500.0, total_legs_traded=4,
        adjustment_cycles=0, g1_triggered=False, open_legs_at_eod=0,
    )
    pr = PortfolioResult(
        date="2026-02-11",
        strategy_results=[dummy_result],
        total_pnl=-500.0,
        strategies_halted=0,
    )
    check("PortfolioResult constructed",      pr is not None)
    check("PortfolioResult.date correct",     pr.date == "2026-02-11")
    check("PortfolioResult.total_pnl",        abs(pr.total_pnl - (-500.0)) < 0.01)
    check("PortfolioResult.strategy_results", len(pr.strategy_results) == 1)
    check("PortfolioResult.__str__ works",    "PORTFOLIO RESULT" in str(pr))

    # ==========================================================================
    # PART 3 — SCENARIO A: Single strategy, no RiskGuard → benchmark preserved
    # ==========================================================================
    section("PART 3 — SCENARIO A: Single strategy, no RiskGuard")

    print(f"\n  [INFO] Coordinator with 1 strategy, risk_guard=None")
    print(f"  [INFO] Expected: Rs.{BENCHMARK_PNL:,.2f} (same as BacktestRunner)\n")

    coord_a = PortfolioCoordinator(date=DATE, expiry=EXPIRY)
    coord_a.add_strategy(
        strategy   = IronStraddleStrategy(),
        handler    = make_handler(DATE, EXPIRY),
        strikes    = STRIKES,
        risk_guard = None,
    )
    result_a = coord_a.run()
    print(f"\n{result_a}")
    for r in result_a.strategy_results:
        print(r)

    check("Scenario A: is PortfolioResult",
          isinstance(result_a, PortfolioResult))
    check("Scenario A: 1 strategy result",
          len(result_a.strategy_results) == 1)
    check("Scenario A: strategies_halted = 0",
          result_a.strategies_halted == 0)

    r = result_a.strategy_results[0]
    check("Scenario A: is BacktestResult",        isinstance(r, BacktestResult))
    check("Scenario A: date correct",             r.date == DATE)
    check("Scenario A: ATM = 26000",              r.atm_strike == 26000)
    check("Scenario A: 0 open legs at EOD",       r.open_legs_at_eod == 0,
          f"got {r.open_legs_at_eod}")
    check("Scenario A: 1 adjustment cycle",       r.adjustment_cycles == 1,
          f"got {r.adjustment_cycles}")

    pnl_diff_a = abs(r.realised_pnl - BENCHMARK_PNL)
    check(
        f"Scenario A: BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced "
        f"(tolerance Rs.{TOLERANCE:.2f})",
        pnl_diff_a <= TOLERANCE,
        f"got Rs.{r.realised_pnl:,.2f}  diff={pnl_diff_a:.2f}",
    )
    pnl_diff_total = abs(result_a.total_pnl - BENCHMARK_PNL)
    check("Scenario A: total_pnl matches single strategy",
          pnl_diff_total <= TOLERANCE,
          f"got Rs.{result_a.total_pnl:,.2f}")

    # ==========================================================================
    # PART 4 — SCENARIO B: Single strategy, production RiskGuard (silent)
    # ==========================================================================
    section("PART 4 — SCENARIO B: Single strategy, production RiskGuard")

    print(f"\n  [INFO] max_daily_loss = -3000/lot x {LOT_SIZE} lots = Rs.-195,000 (won't fire)")
    print(f"  [INFO] Expected: Rs.{BENCHMARK_PNL:,.2f}, guard silent\n")

    rg_b = RiskGuard(max_daily_loss_per_lot=-3000,
                     max_trade_loss_per_lot=-1500,
                     max_adj_cycles=2, lot_size=LOT_SIZE)

    coord_b = PortfolioCoordinator(date=DATE, expiry=EXPIRY)
    coord_b.add_strategy(
        strategy   = IronStraddleStrategy(),
        handler    = make_handler(DATE, EXPIRY),
        strikes    = STRIKES,
        risk_guard = rg_b,
    )
    result_b = coord_b.run()
    print(f"\n{result_b}")
    for r in result_b.strategy_results:
        print(r)

    check("Scenario B: strategies_halted = 0",    result_b.strategies_halted == 0)
    check("Scenario B: RiskGuard NOT halted",      rg_b.is_halted == False)

    rb = result_b.strategy_results[0]
    pnl_diff_b = abs(rb.realised_pnl - BENCHMARK_PNL)
    check(
        f"Scenario B: BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced "
        f"(tolerance Rs.{TOLERANCE:.2f})",
        pnl_diff_b <= TOLERANCE,
        f"got Rs.{rb.realised_pnl:,.2f}  diff={pnl_diff_b:.2f}",
    )
    check("Scenario B: RiskGuard daily_pnl matches realised",
          abs(rg_b.daily_pnl - rb.realised_pnl) <= TOLERANCE,
          f"rg={rg_b.daily_pnl:.2f}  pb={rb.realised_pnl:.2f}")

    # ==========================================================================
    # PART 5 — SCENARIO C: Two strategies, one halted, one runs to EOD
    # ==========================================================================
    section("PART 5 — SCENARIO C: Two strategies — isolation test")

    TIGHT_LIMIT = -15   # Rs.-975 total — will fire on 2026-02-11

    print(f"\n  [INFO] Strategy 1: production limits (Rs.-3000/lot) — runs to EOD")
    print(f"  [INFO] Strategy 2: tight cap (Rs.{TIGHT_LIMIT}/lot ="
          f" Rs.{TIGHT_LIMIT * LOT_SIZE:,.0f}) — hard stop fires")
    print(f"  [INFO] Expected: Strategy 1 unaffected, Strategy 2 halted\n")

    rg_c1 = RiskGuard(max_daily_loss_per_lot=-3000,
                      max_trade_loss_per_lot=-1500,
                      max_adj_cycles=2, lot_size=LOT_SIZE)

    rg_c2 = RiskGuard(max_daily_loss_per_lot=TIGHT_LIMIT,
                      max_trade_loss_per_lot=-1500,
                      max_adj_cycles=2, lot_size=LOT_SIZE)

    coord_c = PortfolioCoordinator(date=DATE, expiry=EXPIRY)
    coord_c.add_strategy(
        strategy   = IronStraddleStrategy(),
        handler    = make_handler(DATE, EXPIRY),
        strikes    = STRIKES,
        risk_guard = rg_c1,
    )
    coord_c.add_strategy(
        strategy   = IronStraddleStrategy(),
        handler    = make_handler(DATE, EXPIRY),
        strikes    = STRIKES,
        risk_guard = rg_c2,
    )

    result_c = coord_c.run()
    print(f"\n{result_c}")
    for r in result_c.strategy_results:
        print(r)

    check("Scenario C: is PortfolioResult",       isinstance(result_c, PortfolioResult))
    check("Scenario C: 2 strategy results",        len(result_c.strategy_results) == 2)
    check("Scenario C: strategies_halted = 1",     result_c.strategies_halted == 1,
          f"got {result_c.strategies_halted}")

    rc1 = result_c.strategy_results[0]   # production limits
    rc2 = result_c.strategy_results[1]   # tight cap

    # Strategy 1: must be unaffected — benchmark reproduced
    pnl_diff_c1 = abs(rc1.realised_pnl - BENCHMARK_PNL)
    check(
        f"Scenario C: Strategy 1 BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced",
        pnl_diff_c1 <= TOLERANCE,
        f"got Rs.{rc1.realised_pnl:,.2f}  diff={pnl_diff_c1:.2f}",
    )
    check("Scenario C: Strategy 1 RiskGuard NOT halted",
          rg_c1.is_halted == False)
    check("Scenario C: Strategy 1 — 0 open legs",
          rc1.open_legs_at_eod == 0,
          f"got {rc1.open_legs_at_eod}")

    # Strategy 2: hard stop must have fired
    check("Scenario C: Strategy 2 RiskGuard IS halted",
          rg_c2.is_halted == True)
    check("Scenario C: Strategy 2 PnL is negative",
          rc2.realised_pnl < 0,
          f"got Rs.{rc2.realised_pnl:.2f}")
    check("Scenario C: Strategy 2 PnL differs from Strategy 1 (hard stop changed outcome)",
          abs(rc2.realised_pnl - rc1.realised_pnl) > 0.01,
          f"strat2={rc2.realised_pnl:.2f}  strat1={rc1.realised_pnl:.2f}")
    check(f"Scenario C: Strategy 2 daily_pnl <= Rs.{TIGHT_LIMIT * LOT_SIZE:,.0f}",
          rg_c2.daily_pnl <= TIGHT_LIMIT * LOT_SIZE,
          f"got {rg_c2.daily_pnl:.2f}")
    check("Scenario C: Strategy 2 — 0 open legs at EOD",
          rc2.open_legs_at_eod == 0,
          f"got {rc2.open_legs_at_eod}")

    # Portfolio total
    expected_total = rc1.realised_pnl + rc2.realised_pnl
    check("Scenario C: total_pnl = sum of both strategies",
          abs(result_c.total_pnl - expected_total) < 0.01,
          f"got {result_c.total_pnl:.2f}  expected {expected_total:.2f}")

    # ==========================================================================
    # FINAL SUMMARY
    # ==========================================================================
    total = PASS_COUNT + FAIL_COUNT
    print(f"\n{'='*70}")
    print(f"  TEST RESULTS — {PASS_COUNT}/{total} PASSED")
    print(f"{'='*70}\n")

    if FAIL_COUNT == 0:
        print("  ALL TESTS PASSED\n")
        print("  Sprint 6 complete.")
        print("  PortfolioCoordinator correctly:")
        print("    - Drives multiple strategies in lock-step through one tick loop")
        print("    - Isolates each strategy's RiskGuard (halt of one never affects others)")
        print("    - Preserves the Rs.-932.75 benchmark through the Coordinator")
        print("    - Returns PortfolioResult with per-strategy BacktestResult list")
        print()
        print("  READY FOR SPRINT 7: market_session.py + indicator_engine.py\n")
    else:
        print(f"  {FAIL_COUNT} TEST(S) FAILED — see above for details\n")

    return FAIL_COUNT == 0


if __name__ == "__main__":
    success = run_sprint6_test()
    sys.exit(0 if success else 1)
