# ==============================================================================
# INTEGRATION TEST — SPRINT 5
# RiskGuard
#
# Limits are expressed PER LOT, matching how traders set risk thresholds.
# RiskGuard multiplies by lot_size internally:
#   max_daily_loss_per_lot=-3000, lot_size=65 → total threshold = Rs.-1,95,000
#   max_trade_loss_per_lot=-1500, lot_size=65 → total threshold = Rs.-97,500
#
# Two benchmark scenarios on 2026-02-11:
#
#   SCENARIO A — Clean run (production limits — wide enough not to fire):
#     max_daily_loss_per_lot = -3000  →  Rs.-1,95,000 total
#     max_trade_loss_per_lot = -1500  →  Rs.-97,500 total
#     max_adj_cycles         = 2
#     Expected: Rs.-932.75 reproduced exactly. RiskGuard silent throughout.
#
#   SCENARIO B — Breach run (tight cap to force hard stop on 2026-02-11):
#     max_daily_loss_per_lot = -15    →  Rs.-975 total  (< Rs.-932.75 session loss)
#     max_trade_loss_per_lot = -1500  →  Rs.-97,500 total (won't fire)
#     max_adj_cycles         = 2
#     Expected: Hard stop fires, position squared off early, guard halted.
#
# Unicode fix: rewrap stdout/stderr to utf-8 so Windows cp1252 console
# handles the → arrow character in log messages without crashing.
#
# Run from project root:
#   cd C:\Rajat\trading_infrastructure
#   set PYTHONPATH=src
#   python integration_test_sprint5.py
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

# ── Fix logging stream handlers for Windows cp1252 (handles → arrow etc.) ─────
import logging
for _handler in logging.root.handlers:
    if hasattr(_handler, 'stream') and hasattr(_handler.stream, 'buffer'):
        _handler.stream = io.TextIOWrapper(
            _handler.stream.buffer, encoding="utf-8",
            errors="replace", line_buffering=True,
        )
# Ensure future handlers added by imported modules also use utf-8
logging.root.addHandler(logging.NullHandler())  # prevents "no handlers" warning
_utf8_handler = logging.StreamHandler(sys.stdout)
_utf8_handler.setFormatter(logging.Formatter(
    "[%(levelname)s] %(name)s: %(message)s"
))

# ── Helpers ────────────────────────────────────────────────────────────────────

PASS_COUNT = 0
FAIL_COUNT = 0

LOT_SIZE = 65   # NIFTY lot size — shared across all test scenarios

DATE    = "2026-02-11"
EXPIRY  = "2026-02-17"
STRIKES = [25800, 26000, 26200]


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


def make_handler(date=None, expiry=None, strikes=None):
    """
    Construct a BacktestExecutionHandler, adapting to whichever constructor
    signature is currently in use:
      - New style: BacktestExecutionHandler(date, expiry)
      - Old style: BacktestExecutionHandler()  then  .load_data(date, expiry, strikes)
    """
    from execution import BacktestExecutionHandler
    params = list(inspect.signature(BacktestExecutionHandler.__init__).parameters.keys())
    # params[0] is always 'self'
    if 'date' in params:
        # New-style constructor requires date + expiry
        h = BacktestExecutionHandler(date, expiry)
        # Load candle data — required even for new-style handlers
        if strikes is not None:
            h.load_data(strikes)
    else:
        # Old-style constructor — load_data separately
        h = BacktestExecutionHandler()
        if date is not None:
            h.load_data(date, expiry, strikes)
    return h

def make_tick(handler, timestamp: str):
    """
    Call handler.build_tick(), adapting to whichever signature is active:
      - New style: build_tick(timestamp, expiry_date, days_to_expiry)
      - Old style: build_tick(timestamp)
    """
    from datetime import date as _date
    params = list(inspect.signature(handler.build_tick).parameters.keys())
    if 'expiry_date' in params:
        session_dt = _date.fromisoformat(DATE)
        expiry_dt  = _date.fromisoformat(EXPIRY)
        dte        = (expiry_dt - session_dt).days
        return handler.build_tick(timestamp, EXPIRY, dte)
    return handler.build_tick(timestamp)


# ==============================================================================
# SPRINT 5 TEST
# ==============================================================================

def run_sprint5_test():
    global PASS_COUNT, FAIL_COUNT

    print()
    print("=" * 70)
    print("  INTEGRATION TEST — Sprint 5")
    print("  RiskGuard — limits expressed per lot, two scenarios on 2026-02-11")
    print("=" * 70)

    # ── Imports ────────────────────────────────────────────────────────────────
    try:
        from strategies.risk.risk_guard import RiskGuard, RiskDecision, RiskAction
        from strategies.options_selling.iron_straddle import (
            IronStraddleStrategy, StraddleState
        )
        from simulation_lab.backtest_runner import BacktestRunner, BacktestResult
        from execution import BacktestExecutionHandler
        from strategies.building_blocks import PositionBook
        print("\n  [INFO] All imports successful")

        # Report which constructor signature is active
        params = list(inspect.signature(BacktestExecutionHandler.__init__).parameters.keys())
        style  = "new-style (date, expiry args)" if 'date' in params else "old-style (load_data)"
        print(f"  [INFO] BacktestExecutionHandler signature: {style}\n")

    except ImportError as e:
        print(f"\n  [FATAL] Import failed: {e}")
        sys.exit(1)

    # ==========================================================================
    # PART 1 — RiskGuard: construction and defaults
    # ==========================================================================
    section("PART 1 — RiskGuard: construction, defaults, and derived thresholds")

    rg = RiskGuard()
    check("Default max_daily_loss_per_lot = -3000",
          rg.max_daily_loss_per_lot == -3000.0)
    check("Default max_trade_loss_per_lot = -1500",
          rg.max_trade_loss_per_lot == -1500.0)
    check("Default max_adj_cycles = 2",
          rg.max_adj_cycles == 2)
    check("Default lot_size = 65",
          rg.lot_size == 65)
    check("Default max_daily_loss = -3000 × 65 = -195,000",
          rg.max_daily_loss == -195_000.0,
          f"got {rg.max_daily_loss}")
    check("Default max_trade_loss = -1500 × 65 = -97,500",
          rg.max_trade_loss == -97_500.0,
          f"got {rg.max_trade_loss}")
    check("Initial daily_pnl = 0.0",
          rg.daily_pnl == 0.0)
    check("Initial is_halted = False",
          rg.is_halted == False)

    section("PART 1b — Custom per-lot parameters scale correctly")

    custom = RiskGuard(
        max_daily_loss_per_lot=-5000,
        max_trade_loss_per_lot=-2000,
        max_adj_cycles=3,
        lot_size=75,
    )
    check("Custom max_daily_loss_per_lot = -5000",   custom.max_daily_loss_per_lot == -5000.0)
    check("Custom max_trade_loss_per_lot = -2000",   custom.max_trade_loss_per_lot == -2000.0)
    check("Custom max_adj_cycles = 3",               custom.max_adj_cycles == 3)
    check("Custom lot_size = 75",                    custom.lot_size == 75)
    check("Custom max_daily_loss = -5000 × 75 = -375,000",
          custom.max_daily_loss == -375_000.0,       f"got {custom.max_daily_loss}")
    check("Custom max_trade_loss = -2000 × 75 = -150,000",
          custom.max_trade_loss == -150_000.0,       f"got {custom.max_trade_loss}")

    # ==========================================================================
    # PART 2 — RiskDecision dataclass
    # ==========================================================================
    section("PART 2 — RiskDecision: factories and fields")

    allow_dec = RiskDecision.allow()
    check("allow() → allowed = True",               allow_dec.allowed == True)
    check("allow() → action = ALLOW",               allow_dec.action  == RiskAction.ALLOW)
    check("allow() → reason is empty str",          allow_dec.reason  == "")

    block_dec = RiskDecision.block("test reason")
    check("block() → allowed = False",              block_dec.allowed == False)
    check("block() → action = BLOCK",               block_dec.action  == RiskAction.BLOCK)
    check("block() → reason captured",              block_dec.reason  == "test reason")

    sq_dec = RiskDecision.square_off("limit hit")
    check("square_off() → allowed = False",         sq_dec.allowed == False)
    check("square_off() → action = SQUARE_OFF",     sq_dec.action  == RiskAction.SQUARE_OFF)
    check("square_off() → reason captured",         sq_dec.reason  == "limit hit")

    # ==========================================================================
    # PART 3 — record_pnl and reset_day
    # ==========================================================================
    section("PART 3 — record_pnl and reset_day")

    rg3 = RiskGuard(max_daily_loss_per_lot=-10, lot_size=LOT_SIZE)
    # max_daily_loss = -10 × 65 = -650

    rg3.record_pnl(-300.0)
    check("After -300: daily_pnl = -300",
          abs(rg3.daily_pnl - (-300.0)) < 0.01)
    check("Not yet halted",
          rg3.is_halted == False)

    rg3.record_pnl(-200.0)
    check("After another -200: daily_pnl = -500",
          abs(rg3.daily_pnl - (-500.0)) < 0.01)

    rg3.reset_day()
    check("After reset_day: daily_pnl = 0",         rg3.daily_pnl == 0.0)
    check("After reset_day: is_halted = False",     rg3.is_halted == False)

    # ==========================================================================
    # PART 4 — check_entry
    # ==========================================================================
    section("PART 4 — check_entry: allow and daily-loss-breach")

    # Use a loaded handler for the PositionBook tests that need a tick later
    handler4 = make_handler(DATE, EXPIRY, STRIKES)
    pb4 = PositionBook("Iron Straddle")

    # 4a: clean — should allow
    rg4 = RiskGuard(max_daily_loss_per_lot=-3000, lot_size=LOT_SIZE)
    dec4a = rg4.check_entry("Iron Straddle", pb4)
    check("check_entry clean → ALLOW",              dec4a.action  == RiskAction.ALLOW)
    check("check_entry clean → allowed",            dec4a.allowed == True)

    # 4b: daily loss already breached
    rg4.record_pnl(rg4.max_daily_loss - 1)   # push just below threshold
    dec4b = rg4.check_entry("Iron Straddle", pb4)
    check("check_entry breached → SQUARE_OFF",      dec4b.action  == RiskAction.SQUARE_OFF)
    check("check_entry breached → not allowed",     dec4b.allowed == False)
    check("Guard halted after breach",              rg4.is_halted == True)

    # 4c: halted guard → BLOCK on next call
    dec4c = rg4.check_entry("Iron Straddle", pb4)
    check("check_entry while halted → BLOCK",       dec4c.action  == RiskAction.BLOCK)

    # ==========================================================================
    # PART 5 — check_adjustment: all three sub-limits
    # ==========================================================================
    section("PART 5 — check_adjustment: daily loss / trade loss / max cycles")

    # 5a: daily loss fires
    rg5a = RiskGuard(max_daily_loss_per_lot=-5, max_trade_loss_per_lot=-1500,
                     max_adj_cycles=2, lot_size=LOT_SIZE)
    # threshold = -5 × 65 = -325
    rg5a.record_pnl(-400.0)
    dec5a = rg5a.check_adjustment("Iron Straddle", 0, pb4)
    check("check_adjustment: daily loss → SQUARE_OFF",
          dec5a.action == RiskAction.SQUARE_OFF)
    check("check_adjustment: daily loss → halted",
          rg5a.is_halted == True)

    # 5b: max cycles fires (soft — no halt)
    rg5b = RiskGuard(max_daily_loss_per_lot=-3000, max_trade_loss_per_lot=-1500,
                     max_adj_cycles=2, lot_size=LOT_SIZE)
    dec5b = rg5b.check_adjustment("Iron Straddle", 2, pb4)
    check("check_adjustment: max cycles → BLOCK",
          dec5b.action == RiskAction.BLOCK)
    check("check_adjustment: max cycles → NOT halted",
          rg5b.is_halted == False)

    # 5c: clean — all under limits
    rg5c = RiskGuard(max_daily_loss_per_lot=-3000, max_trade_loss_per_lot=-1500,
                     max_adj_cycles=2, lot_size=LOT_SIZE)
    dec5c = rg5c.check_adjustment("Iron Straddle", 0, pb4)
    check("check_adjustment: clean → ALLOW",
          dec5c.action == RiskAction.ALLOW)

    # 5d: trade (MTM) loss fires
    #     max_trade_loss_per_lot=+1 → threshold = +65
    #     MTM of empty position book = 0.0, which <= +65 → fires
    handler5d = make_handler(DATE, EXPIRY, STRIKES)
    tick5d    = make_tick(handler5d, f"{DATE}T12:00:00+05:30")
    pb5d      = PositionBook("Iron Straddle")

    rg5d = RiskGuard(max_daily_loss_per_lot=-3000,
                     max_trade_loss_per_lot=1,    # +Rs.1/lot → threshold = +65
                     max_adj_cycles=2,
                     lot_size=LOT_SIZE)
    dec5d = rg5d.check_adjustment("Iron Straddle", 0, pb5d, tick5d)
    check("check_adjustment: trade loss → SQUARE_OFF",
          dec5d.action == RiskAction.SQUARE_OFF)
    check("check_adjustment: trade loss → halted",
          rg5d.is_halted == True)

    # ==========================================================================
    # PART 6 — SCENARIO A: Clean backtest (production limits — guard silent)
    # ==========================================================================
    section("PART 6 — SCENARIO A: Production limits — Rs.-932.75 reproduced")

    daily_limit_per_lot = -3000
    trade_limit_per_lot = -1500
    daily_limit_total   = daily_limit_per_lot * LOT_SIZE    # -195,000
    trade_limit_total   = trade_limit_per_lot * LOT_SIZE    # -97,500

    print(f"\n  [INFO] Running full day backtest on {DATE}")
    print(f"  [INFO] max_daily_loss = {daily_limit_per_lot}/lot × {LOT_SIZE} lots"
          f" = Rs.{daily_limit_total:,.0f}")
    print(f"  [INFO] max_trade_loss = {trade_limit_per_lot}/lot × {LOT_SIZE} lots"
          f" = Rs.{trade_limit_total:,.0f}")
    print(f"  [INFO] max_adj_cycles = 2\n")

    rg_clean      = RiskGuard(max_daily_loss_per_lot=daily_limit_per_lot,
                              max_trade_loss_per_lot=trade_limit_per_lot,
                              max_adj_cycles=2, lot_size=LOT_SIZE)
    strat_clean   = IronStraddleStrategy()
    handler_clean = make_handler(DATE, EXPIRY, STRIKES)
    runner_clean  = BacktestRunner(strat_clean, handler_clean, risk_guard=rg_clean)

    result_clean = runner_clean.run(date=DATE, expiry=EXPIRY, strikes=STRIKES)
    print(f"\n{result_clean}")

    check("Scenario A: is BacktestResult",
          isinstance(result_clean, BacktestResult))
    check("Scenario A: date correct",
          result_clean.date == DATE)
    check("Scenario A: ATM = 26000",
          result_clean.atm_strike == 26000)
    check("Scenario A: 0 open legs at EOD",
          result_clean.open_legs_at_eod == 0,
          f"got {result_clean.open_legs_at_eod}")
    check("Scenario A: 1 adjustment cycle",
          result_clean.adjustment_cycles == 1,
          f"got {result_clean.adjustment_cycles}")
    check("Scenario A: RiskGuard NOT halted",
          rg_clean.is_halted == False)

    BENCHMARK_PNL = -932.75
    TOLERANCE     = 1.00
    pnl_diff = abs(result_clean.realised_pnl - BENCHMARK_PNL)
    check(
        f"Scenario A: BENCHMARK Rs.{BENCHMARK_PNL:,.2f} reproduced "
        f"(tolerance Rs.{TOLERANCE:.2f})",
        pnl_diff <= TOLERANCE,
        f"got Rs.{result_clean.realised_pnl:,.2f}  diff={pnl_diff:.2f}",
    )

    # ==========================================================================
    # PART 7 — SCENARIO B: Breach run (daily cap = Rs.-15/lot × 65 = Rs.-975)
    # ==========================================================================
    section("PART 7 — SCENARIO B: Tight cap — hard stop fires before EOD")

    breach_limit_per_lot = -15
    breach_limit_total   = breach_limit_per_lot * LOT_SIZE    # -975

    print(f"\n  [INFO] Running full day backtest on {DATE}")
    print(f"  [INFO] max_daily_loss = {breach_limit_per_lot}/lot × {LOT_SIZE} lots"
          f" = Rs.{breach_limit_total:,.0f}  <- hard stop target")
    print(f"  [INFO] max_trade_loss = {trade_limit_per_lot}/lot × {LOT_SIZE} lots"
          f" = Rs.{trade_limit_total:,.0f}  (wide, won't fire)")
    print(f"  [INFO] max_adj_cycles = 2\n")

    rg_breach      = RiskGuard(max_daily_loss_per_lot=breach_limit_per_lot,
                               max_trade_loss_per_lot=trade_limit_per_lot,
                               max_adj_cycles=2, lot_size=LOT_SIZE)
    strat_breach   = IronStraddleStrategy()
    handler_breach = make_handler(DATE, EXPIRY, STRIKES)
    runner_breach  = BacktestRunner(strat_breach, handler_breach, risk_guard=rg_breach)

    result_breach = runner_breach.run(date=DATE, expiry=EXPIRY, strikes=STRIKES)
    print(f"\n{result_breach}")

    check("Scenario B: is BacktestResult",
          isinstance(result_breach, BacktestResult))
    check("Scenario B: date correct",
          result_breach.date == DATE)
    check("Scenario B: RiskGuard IS halted",
          rg_breach.is_halted == True)
    check("Scenario B: 0 open legs at EOD",
          result_breach.open_legs_at_eod == 0,
          f"got {result_breach.open_legs_at_eod}")
    check("Scenario B: PnL is negative",
          result_breach.realised_pnl < 0,
          f"got Rs.{result_breach.realised_pnl:.2f}")
    check("Scenario B: PnL differs from Scenario A (hard stop changed outcome)",
          abs(result_breach.realised_pnl - result_clean.realised_pnl) > 0.01,
          f"breach={result_breach.realised_pnl:.2f}  clean={result_clean.realised_pnl:.2f}")
    check(f"Scenario B: daily_pnl <= Rs.{breach_limit_total:,.0f} (limit was hit)",
          rg_breach.daily_pnl <= breach_limit_total,
          f"got {rg_breach.daily_pnl:.2f}")

    # ==========================================================================
    # FINAL SUMMARY
    # ==========================================================================
    total = PASS_COUNT + FAIL_COUNT
    print(f"\n{'='*70}")
    print(f"  TEST RESULTS — {PASS_COUNT}/{total} PASSED")
    print(f"{'='*70}\n")

    if FAIL_COUNT == 0:
        print("  ALL TESTS PASSED\n")
        print("  Sprint 5 complete.")
        print("  RiskGuard correctly enforces per-lot limits:")
        print(f"    Daily loss  : Rs.{daily_limit_per_lot:,.0f}/lot "
              f"× {LOT_SIZE} lots = Rs.{daily_limit_total:,.0f}")
        print(f"    Trade loss  : Rs.{trade_limit_per_lot:,.0f}/lot "
              f"× {LOT_SIZE} lots = Rs.{trade_limit_total:,.0f}")
        print(f"    Adj cycles  : max 2")
        print()
        print("  Scenario A: Rs.-932.75 benchmark reproduced — guard stayed silent.")
        print("  Scenario B: Hard stop fired — guard halted, position squared off.")
        print("  READY FOR SPRINT 6: PortfolioCoordinator\n")
        print(f"  [DEBUG] RiskGuard daily_pnl after run : {rg_clean.daily_pnl:.2f}")
        print(f"  [DEBUG] PositionBook realised_pnl      : {result_clean.realised_pnl:.2f}")
    else:
        print(f"  {FAIL_COUNT} TEST(S) FAILED — see above for details\n")

    return FAIL_COUNT == 0


if __name__ == "__main__":
    success = run_sprint5_test()
    sys.exit(0 if success else 1)
