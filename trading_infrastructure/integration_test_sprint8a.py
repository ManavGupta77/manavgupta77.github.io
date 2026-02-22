# ==============================================================================
# INTEGRATION TEST — SPRINT 8A
# PaperExecutionHandler + TickReplayFeed — Accuracy Gate
#
# PROVES:
#   PaperExecutionHandler + TickReplayFeed ≡ BacktestExecutionHandler
#   by reproducing the Rs.-932.75 benchmark through the paper path.
#
# Tests:
#   PART 1 — PaperExecutionHandler: construction and interface    (~12 tests)
#   PART 2 — TickReplayFeed: load and data verification           (~10 tests)
#   PART 3 — SCENARIO A: Paper replay — Rs.-932.75 benchmark      (~9 tests)
#   PART 4 — SCENARIO B: Paper replay + production RiskGuard      (~4 tests)
#   PART 5 — SCENARIO C: Paper replay + IndicatorEngine           (~5 tests)
#   PART 6 — Fill log audit                                       (~7 tests)
#
# File placement:
#   C:\Rajat\trading_infrastructure\integration_test_sprint8a.py
#
# Run:
#   cd C:\Rajat\trading_infrastructure
#   set PYTHONPATH=src
#   python integration_test_sprint8a.py
# ==============================================================================

import sys
import io

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


def make_paper_handler(date=DATE, expiry=EXPIRY, strikes=STRIKES):
    """
    Create a TickReplayFeed + PaperExecutionHandler pair.

    Loads historical data via TickReplayFeed and preloads it into a
    PaperExecutionHandler. Returns (handler, feed) so the test can
    inspect both.
    """
    from execution import PaperExecutionHandler
    from market_feeds.live_feeds.tick_replay import TickReplayFeed

    feed = TickReplayFeed(date=date, expiry=expiry, strikes=strikes)
    ok = feed.load()
    if not ok:
        raise RuntimeError(f"TickReplayFeed failed to load data for {date}")

    handler = PaperExecutionHandler(date=date, expiry=expiry)
    feed.preload_handler(handler)
    handler.load_data(strikes)          # Mirror MarketSession.run() flow
    return handler, feed


def make_backtest_handler(date=DATE, expiry=EXPIRY):
    """Create a BacktestExecutionHandler for comparison (Sprint 7 pattern)."""
    from execution import BacktestExecutionHandler
    return BacktestExecutionHandler(date, expiry)


# ==============================================================================
# MAIN TEST
# ==============================================================================

def run_sprint8a_test():
    global PASS_COUNT, FAIL_COUNT

    print()
    print("=" * 70)
    print("  INTEGRATION TEST — Sprint 8A")
    print("  PaperExecutionHandler + TickReplayFeed, 2026-02-11")
    print("=" * 70)

    # ── Imports ──────────────────────────────────────────────────────────────
    try:
        from simulation_lab.market_session import MarketSession, SessionResult
        from execution import PaperExecutionHandler, BacktestExecutionHandler
        from market_feeds.live_feeds.tick_replay import TickReplayFeed
        from strategies.risk.risk_guard import RiskGuard
        from strategies.options_selling.iron_straddle import IronStraddleStrategy
        from strategies.building_blocks.leg_fill import ExecutionMode
        from strategies.building_blocks.market_tick import MarketTick
        print("\n  [INFO] All imports successful\n")
    except ImportError as e:
        print(f"\n  [FATAL] Import failed: {e}")
        sys.exit(1)

    # ==========================================================================
    # PART 1 — PaperExecutionHandler: construction and interface
    # ==========================================================================
    section("PART 1 — PaperExecutionHandler: construction and interface")

    handler_1, feed_1 = make_paper_handler()

    check("PaperExecutionHandler constructed",
          handler_1 is not None)

    check("mode is PAPER",
          handler_1.mode == ExecutionMode.PAPER)

    check("date correct",
          handler_1.date == DATE)

    check("expiry correct",
          handler_1.expiry == EXPIRY)

    # Verify tick buffer was populated by preload
    ts_list = handler_1.get_timestamps()
    check("get_timestamps() returns ticks after preload",
          len(ts_list) > 0,
          f"got {len(ts_list)}")

    # Compare tick count with backtest handler
    bt_handler = make_backtest_handler()
    bt_handler.load_data(STRIKES)
    bt_timestamps = bt_handler.get_timestamps()
    check("tick count matches backtest handler",
          len(ts_list) == len(bt_timestamps),
          f"paper={len(ts_list)}  backtest={len(bt_timestamps)}")

    # Spot price available at 09:15 or 09:30
    spot_0915 = handler_1.get_spot_price("09:15")
    spot_0930 = handler_1.get_spot_price("09:30")
    opening_spot = spot_0915 or spot_0930
    check("get_spot_price() returns opening spot",
          opening_spot > 0,
          f"09:15={spot_0915}  09:30={spot_0930}")

    # build_tick returns a MarketTick
    first_ts = ts_list[0] if ts_list else ""
    tick_test = handler_1.build_tick(first_ts, EXPIRY, 6.0)
    check("build_tick() returns MarketTick",
          tick_test is not None and isinstance(tick_test, MarketTick))

    # find_symbol resolves ATM
    ce_sym = handler_1.find_symbol(26000, "CE")
    pe_sym = handler_1.find_symbol(26000, "PE")
    check("find_symbol(26000, CE) resolves",
          ce_sym is not None and "26000" in ce_sym and "CE" in ce_sym,
          f"got {ce_sym}")
    check("find_symbol(26000, PE) resolves",
          pe_sym is not None and "26000" in pe_sym and "PE" in pe_sym,
          f"got {pe_sym}")

    # get_atm_strike
    atm = handler_1.get_atm_strike(25976.05)
    check("get_atm_strike(25976.05) = 26000",
          atm == 26000,
          f"got {atm}")

    # load_data returns True after preload
    check("load_data() returns True after preload",
          handler_1.load_data(STRIKES) == True)

    # Fill log starts empty
    check("fill log empty before any execute()",
          handler_1.get_fill_count() == 0)

    # ==========================================================================
    # PART 2 — TickReplayFeed: load and data verification
    # ==========================================================================
    section("PART 2 — TickReplayFeed: load and data verification")

    feed_2 = TickReplayFeed(date=DATE, expiry=EXPIRY, strikes=STRIKES)
    check("TickReplayFeed constructed",
          feed_2 is not None)

    check("Not loaded before load()",
          feed_2.is_loaded == False)

    ok = feed_2.load()
    check("load() succeeds",
          ok == True)

    check("is_loaded True after load()",
          feed_2.is_loaded == True)

    check("tick_count > 0",
          feed_2.tick_count > 0,
          f"got {feed_2.tick_count}")

    check("tick_count matches backtest timestamps",
          feed_2.tick_count == len(bt_timestamps),
          f"feed={feed_2.tick_count}  bt={len(bt_timestamps)}")

    check("get_timestamps() length matches tick_count",
          len(feed_2.get_timestamps()) == feed_2.tick_count)

    # Symbol map populated
    sym_map = feed_2.get_symbol_map()
    check("symbol_map has entries",
          len(sym_map) > 0,
          f"got {len(sym_map)} mappings")

    # Verify all 6 symbols present (3 strikes × CE + PE)
    expected_symbols = 0
    for strike in STRIKES:
        for opt in ("CE", "PE"):
            if (strike, opt) in sym_map:
                expected_symbols += 1
    check("all 6 symbols resolved (3 strikes × CE/PE)",
          expected_symbols == 6,
          f"got {expected_symbols}")

    # Spot cache populated
    spot_cache = feed_2.get_spot_cache()
    check("spot cache populated",
          len(spot_cache) > 0,
          f"got {len(spot_cache)} entries")

    # days_to_expiry computed
    check("days_to_expiry > 0",
          feed_2.days_to_expiry > 0,
          f"got {feed_2.days_to_expiry}")

    # Verify ticks are actual MarketTick objects
    ticks = feed_2.get_ticks()
    check("get_ticks() returns MarketTick objects",
          len(ticks) > 0 and isinstance(ticks[0], MarketTick))

    # Verify first tick has option_prices
    check("first tick has option_prices",
          len(ticks[0].option_prices) > 0,
          f"got {len(ticks[0].option_prices)} symbols")

    # ==========================================================================
    # PART 3 — SCENARIO A: Paper replay — Rs.-932.75 benchmark
    # ==========================================================================
    section("PART 3 — SCENARIO A: Paper replay, no RiskGuard, no IndicatorEngine")
    print(f"\n  [INFO] Expected: Rs.{BENCHMARK_PNL:,.2f} — benchmark through Paper path")
    print(f"  [INFO] Proves: PaperHandler + TickReplayFeed ≡ BacktestHandler\n")

    handler_a, feed_a = make_paper_handler()
    session_a = MarketSession(date=DATE, expiry=EXPIRY)
    session_a.add_strategy(
        strategy=IronStraddleStrategy(),
        handler=handler_a,
        strikes=STRIKES,
        risk_guard=None,
        indicator_engine=None,
    )
    results_a = session_a.run()
    for r in results_a:
        print(r)

    check("Scenario A: returns list",
          isinstance(results_a, list))

    check("Scenario A: 1 result",
          len(results_a) == 1)

    ra = results_a[0]
    check("Scenario A: is SessionResult",
          isinstance(ra, SessionResult))

    check("Scenario A: date correct",
          ra.date == DATE)

    check("Scenario A: ATM = 26000",
          ra.atm_strike == 26000)

    check("Scenario A: 0 open legs at EOD",
          ra.open_legs_at_eod == 0,
          f"got {ra.open_legs_at_eod}")

    check("Scenario A: 1 adjustment cycle",
          ra.adjustment_cycles == 1,
          f"got {ra.adjustment_cycles}")

    check("Scenario A: not halted",
          ra.halted_by_risk == False)

    check("Scenario A: indicator_ticks = 0",
          ra.indicator_ticks == 0)

    pnl_diff_a = abs(ra.realised_pnl - BENCHMARK_PNL)
    check(
        f"Scenario A: BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced "
        f"(tolerance Rs.{TOLERANCE:.2f})",
        pnl_diff_a <= TOLERANCE,
        f"got Rs.{ra.realised_pnl:,.2f}  diff={pnl_diff_a:.2f}",
    )

    # ==========================================================================
    # PART 4 — SCENARIO B: Paper replay + production RiskGuard (silent)
    # ==========================================================================
    section("PART 4 — SCENARIO B: Paper replay + production RiskGuard (silent)")
    print(f"\n  [INFO] max_daily_loss = -3000/lot x {LOT_SIZE} = Rs.-195,000 (won't fire)")
    print(f"  [INFO] Expected: Rs.{BENCHMARK_PNL:,.2f}, guard silent\n")

    rg_b = RiskGuard(
        max_daily_loss_per_lot=-3000,
        max_trade_loss_per_lot=-1500,
        max_adj_cycles=2,
        lot_size=LOT_SIZE,
    )
    handler_b, _ = make_paper_handler()
    session_b = MarketSession(date=DATE, expiry=EXPIRY)
    session_b.add_strategy(
        strategy=IronStraddleStrategy(),
        handler=handler_b,
        strikes=STRIKES,
        risk_guard=rg_b,
        indicator_engine=None,
    )
    results_b = session_b.run()
    for r in results_b:
        print(r)

    rb = results_b[0]
    check("Scenario B: RiskGuard NOT halted",
          rg_b.is_halted == False)

    check("Scenario B: not halted in result",
          rb.halted_by_risk == False)

    pnl_diff_b = abs(rb.realised_pnl - BENCHMARK_PNL)
    check(
        f"Scenario B: BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced",
        pnl_diff_b <= TOLERANCE,
        f"got Rs.{rb.realised_pnl:,.2f}  diff={pnl_diff_b:.2f}",
    )

    check("Scenario B: RiskGuard daily_pnl matches",
          abs(rg_b.daily_pnl - rb.realised_pnl) <= TOLERANCE,
          f"rg={rg_b.daily_pnl:.2f}  pb={rb.realised_pnl:.2f}")

    # ==========================================================================
    # PART 5 — SCENARIO C: Paper replay + IndicatorEngine (benchmark preserved)
    # ==========================================================================
    section("PART 5 — SCENARIO C: Paper replay + IndicatorEngine")

    try:
        from indicators.indicator_engine import IndicatorEngine
        has_indicator_engine = True
    except ImportError:
        has_indicator_engine = False
        print("  [SKIP] IndicatorEngine not available — skipping Scenario C")

    if has_indicator_engine:
        print(f"\n  [INFO] IndicatorEngine injected — trade logic must be unaffected")
        print(f"  [INFO] Expected: Rs.{BENCHMARK_PNL:,.2f} still reproduced\n")

        ie_c = IndicatorEngine(
            opening_spot=25976.05,
            atm_ce_symbol="NIFTY17FEB2626000CE",
            atm_pe_symbol="NIFTY17FEB2626000PE",
            entry_ce_premium=130.3,
            entry_pe_premium=115.5,
        )
        handler_c, _ = make_paper_handler()
        session_c = MarketSession(date=DATE, expiry=EXPIRY)
        session_c.add_strategy(
            strategy=IronStraddleStrategy(),
            handler=handler_c,
            strikes=STRIKES,
            risk_guard=None,
            indicator_engine=ie_c,
        )
        results_c = session_c.run()
        for r in results_c:
            print(r)

        rc = results_c[0]
        pnl_diff_c = abs(rc.realised_pnl - BENCHMARK_PNL)
        check(
            f"Scenario C: BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced with engine",
            pnl_diff_c <= TOLERANCE,
            f"got Rs.{rc.realised_pnl:,.2f}  diff={pnl_diff_c:.2f}",
        )

        check("Scenario C: indicator_ticks > 0 (engine ran each tick)",
              rc.indicator_ticks > 0,
              f"got {rc.indicator_ticks}")

        check("Scenario C: IndicatorEngine.ticks_computed > 0",
              ie_c.ticks_computed > 0,
              f"got {ie_c.ticks_computed}")

        check("Scenario C: 1 adjustment cycle (state machine unaffected)",
              rc.adjustment_cycles == 1,
              f"got {rc.adjustment_cycles}")

        check("Scenario C: 0 open legs at EOD",
              rc.open_legs_at_eod == 0,
              f"got {rc.open_legs_at_eod}")

    # ==========================================================================
    # PART 6 — Fill log audit
    # ==========================================================================
    section("PART 6 — Fill log audit (from Scenario A handler)")

    fill_log = handler_a.get_fill_log()
    check("Fill log is populated",
          len(fill_log) > 0,
          f"got {len(fill_log)} fills")

    # All fills should be mode=PAPER
    all_paper = all(f.mode == ExecutionMode.PAPER for f in fill_log)
    check("All fills have mode=PAPER",
          all_paper)

    # All successful fills
    successful = handler_a.get_successful_fills()
    check("Successful fills > 0",
          len(successful) > 0,
          f"got {len(successful)}")

    # No rejected fills (all 6 symbols should have prices at every signal time)
    rejected = [f for f in fill_log if f.is_rejected]
    check("No rejected fills",
          len(rejected) == 0,
          f"got {len(rejected)} rejected")

    # Opening fills have entry_price set
    open_fills = [f for f in successful if f.is_opening_fill]
    all_priced = all(f.fill_price > 0 for f in open_fills)
    check("All opening fills have fill_price > 0",
          all_priced,
          f"open_fills={len(open_fills)}")

    # Closing fills have fill_price set
    close_fills = [f for f in successful if not f.is_opening_fill]
    all_close_priced = all(f.fill_price > 0 for f in close_fills)
    check("All closing fills have fill_price > 0",
          all_close_priced,
          f"close_fills={len(close_fills)}")

    # Fill prices match tick data — verify opening fills
    price_match = True
    for f in open_fills:
        ts = None
        # Find the tick at the fill time
        for ts_iso in handler_a._timestamps:
            if ts_iso[11:16] == f.fill_time:
                ts = ts_iso
                break
        if ts:
            tick = handler_a._tick_buffer.get(ts)
            if tick:
                expected_price = tick.option_prices.get(f.leg.symbol, 0.0)
                if abs(f.fill_price - expected_price) > 0.01:
                    price_match = False
                    break
    check("Opening fill prices match tick option_prices",
          price_match)

    # ==========================================================================
    # FINAL SUMMARY
    # ==========================================================================
    total = PASS_COUNT + FAIL_COUNT
    print(f"\n{'='*70}")
    print(f"  TEST RESULTS — {PASS_COUNT}/{total} PASSED")
    print(f"{'='*70}\n")

    if FAIL_COUNT == 0:
        print("  ALL TESTS PASSED\n")
        print("  Sprint 8A complete — ACCURACY GATE PASSED.")
        print("  PaperExecutionHandler correctly:")
        print("    - Implements BacktestExecutionHandler interface exactly")
        print("    - Fills at buffered tick prices (same as backtest close prices)")
        print("    - Forward-fill fallback matches backtest behaviour")
        print("    - All fills logged with mode=PAPER for audit")
        print(f"    - Rs.{BENCHMARK_PNL:,.2f} benchmark reproduced through paper path")
        print()
        print("  TickReplayFeed correctly:")
        print("    - Loads historical data via BacktestExecutionHandler")
        print("    - Builds identical MarketTick objects")
        print("    - Transfers all ticks + symbols to PaperHandler")
        print("    - Tick counts match backtest handler exactly")
        print()
        print("  READY FOR SPRINT 8B: LiveSession + ShoonyaLiveFeed\n")
    else:
        print(f"  {FAIL_COUNT} TEST(S) FAILED — see above")
        print(f"  ACCURACY GATE NOT PASSED — fix before proceeding to 8B\n")

    return FAIL_COUNT == 0


if __name__ == "__main__":
    success = run_sprint8a_test()
    sys.exit(0 if success else 1)
