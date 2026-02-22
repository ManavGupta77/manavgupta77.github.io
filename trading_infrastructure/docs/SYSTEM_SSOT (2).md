# SYSTEM SOURCE OF TRUTH (SSOT)
## RGPAS — Rajat Gupta Proprietary Algo System
### Algo Trading Infrastructure — NSE/BSE Options

---

**Last Updated:** 2026-02-22
**Updated By:** Pre-Sprint 8B Cleanup + Baseline Verification
**Current Status:** Sprint 8A ✅ Complete | Baseline ✅ 267/267 | Sprint 8B 🔲 Ready to Start
**Document Purpose:** Complete A-to-Z handover reference. Read this before touching any code.

---

## TABLE OF CONTENTS

1. [System Overview and Design Philosophy](#1-system-overview-and-design-philosophy)
2. [Architecture](#2-architecture)
3. [Folder Structure](#3-folder-structure)
4. [All Scripts — Purpose Table](#4-all-scripts--purpose-table)
5. [Execution Modes](#5-execution-modes)
6. [Database Schema](#6-database-schema)
7. [Brokers and Connection Status](#7-brokers-and-connection-status)
8. [Broker WebSocket and Token Reference](#8-broker-websocket-and-token-reference)
9. [Strategy — Iron Straddle](#9-strategy--iron-straddle)
10. [What is Working](#10-what-is-working)
11. [What is Not Working](#11-what-is-not-working)
12. [Known Issues and Bugs](#12-known-issues-and-bugs)
13. [Tech Debt Register](#13-tech-debt-register)
14. [Sprint History and Status](#14-sprint-history-and-status)
15. [Accuracy Benchmark](#15-accuracy-benchmark)
16. [Action Planned — Sprint 8B](#16-action-planned--sprint-8b)
17. [Action Pending — Sprint 9 and Beyond](#17-action-pending--sprint-9-and-beyond)
18. [Cleanup Required](#18-cleanup-required)
19. [Immediate Next Steps](#19-immediate-next-steps)
20. [Configuration Reference](#20-configuration-reference)
21. [API Contracts and Design Decisions](#21-api-contracts-and-design-decisions)
22. [Handover Notes for New Developers](#22-handover-notes-for-new-developers)
23. [Changelog](#23-changelog)

---

## 1. System Overview and Design Philosophy

### What This System Is

RGPAS is a Python-based algorithmic trading system for NSE/BSE options.
It supports three execution modes — **Backtest**, **Paper Trading**, and **Live Trading** — through
a single shared strategy interface. The same strategy code runs identically in all three modes.

**Primary instrument:** NIFTY weekly options (Nifty 50 index, NSE)
**Primary strategy:** Iron Straddle — delta-neutral options selling with systematic adjustment logic
**Exchange:** NSE (NFO segment for options)
**Data source:** SQLite database populated from ICICI Breeze historical API

### Core Design Principles

These principles are non-negotiable. Any change that violates them must be explicitly reviewed.

| Principle | What It Means |
|-----------|--------------|
| **Accuracy first** | Every new execution path must reproduce Rs.-932.75 before being accepted |
| **Mode-agnostic strategies** | `IronStraddleStrategy` never knows if it is backtesting, paper trading, or live |
| **One interface, three handlers** | `BacktestExecutionHandler`, `PaperExecutionHandler`, `LiveExecutionHandler` (Sprint 10) |
| **No surprises in production** | RiskGuard, fill logging, and kill switch are mandatory in all live sessions |
| **Zero files outside scope** | Each sprint creates exactly the declared files, nothing more |
| **SSOT always current** | This document is updated at the end of every sprint, before the next begins |

### The Two Systems Problem (Critical Context)

The codebase contains **two independently-built trading systems** that co-exist:

**Legacy system** (built before Sprint 1):
`signal_hub/webhook_receiver.py` → `trade_desk/order_manager.py` → broker gateway → `positions.json` + `paper_ledger.csv`
The Streamlit dashboard reads these flat files. This system was built for TradingView webhook signals.

**Sprint system** (current, canonical — Sprints 1–8A):
`MarketSession` / `BacktestRunner` → `PaperExecutionHandler` / `BacktestExecutionHandler` → `PositionBook` → SQLite

The two systems share: `config_loader/settings.py`, `utilities/logger.py`, `trading_records/db_connector.py`, and all broker connectors.
They are otherwise completely independent. **The sprint system is canonical. No new code should be built on the legacy path.**
The legacy system is preserved for reference only, until the dashboard is migrated in Sprint 9.

---

## 2. Architecture

### 2.1 High-Level System Map

```
+----------------------------------------------------------------------+
|                         RGPAS ARCHITECTURE                           |
|                                                                      |
|  DATA LAYER           EXECUTION LAYER        STRATEGY LAYER         |
|  ----------           ---------------        ---------------         |
|                                                                      |
|  SQLite DB      -->   BacktestExecution  -->                         |
|  (options_ohlc)       Handler                                        |
|                                              IronStraddle            |
|  TickReplay     -->   PaperExecution     --> Strategy                |
|  Feed                 Handler                |                       |
|                                              +-- PositionBook        |
|  Shoonya        -->   LiveExecution      --> +-- RiskGuard          |
|  WebSocket (8B)       Handler (S10)          +-- IndicatorEngine    |
|                                                                      |
|  ORCHESTRATION LAYER          BROKER LAYER                          |
|  --------------------         ------------                           |
|                                                                      |
|  BacktestRunner               ShoonyaBroker  (primary, orders)      |
|  MarketSession                AngelBroker    (LTP, market data)     |
|  LiveSession (8B)             UpstoxBroker   (historical, chain)    |
|  PortfolioCoordinator         ConnectionManager (failover)          |
+----------------------------------------------------------------------+
```

### 2.2 Dependency Flow (Sprint System Only)

```
config_loader/settings.py          <- loaded by everything, no sprint deps
utilities/logger.py                <- loaded by everything, no sprint deps
trading_records/db_connector.py    <- loaded by execution handlers

strategies/building_blocks/        <- no external deps, pure data classes
    OptionsLeg, TradeSignal, LegFill, MarketTick, PositionBook

strategies/risk/risk_guard.py      <- depends on building_blocks only
indicators/indicator_engine.py     <- depends on building_blocks only

execution/
    BacktestExecutionHandler       <- depends on db_connector, building_blocks
    PaperExecutionHandler          <- depends on BacktestHandler (data loading)

market_feeds/live_feeds/
    TickReplayFeed                 <- depends on BacktestHandler (data loading)
    ShoonyaLiveFeed (8B)           <- depends on broker_gateway, instruments/

strategies/
    BaseStrategy                   <- depends on building_blocks
    IronStraddleStrategy           <- depends on BaseStrategy, building_blocks

simulation_lab/
    BacktestRunner                 <- depends on execution, strategies
    MarketSession                  <- depends on execution, strategies, indicators
    LiveSession (8B)               <- depends on execution, strategies, market_feeds

strategies/coordinator.py          <- depends on execution, strategies
```

### 2.3 Import Boundary Rules

| Rule | Detail |
|------|--------|
| Sprint modules never import legacy | `execution/`, `simulation_lab/`, `strategies/`, `indicators/`, `market_feeds/` must not import from `signal_hub/`, `trade_desk/order_management/`, or `analytics/` |
| Building blocks have no upstream deps | `strategies/building_blocks/` imports only from `utilities/` and stdlib |
| Handlers import db_connector, not strategies | Execution handlers never import strategy classes |
| Live feeds import broker_gateway | `market_feeds/live_feeds/` uses broker connectors directly |

---

## 3. Folder Structure

```
C:\Rajat\trading_infrastructure\
|
+-- config/
|   +-- .env                              NEVER COMMIT -- all credentials live here
|   +-- broker_profiles.yaml              STUB -- future per-broker config
|   +-- signal_sources.yaml               STUB -- future TradingView config
|   +-- strategy_parameters.yaml          STUB -- Sprint 9 YAML-driven strategy params
|   +-- system_rules.yaml                 STUB -- Sprint 9 system-level toggles
|
+-- data/
|   +-- upstox_token.json                 Upstox OAuth2 token (auto-refreshed, do not edit)
|   +-- upstox_master.csv                 Upstox instrument master (auto-downloaded daily)
|
+-- docs/                                 Architecture notes, sprint plans (informal)
|
+-- logs/                                 Dated runtime log folders (e.g. logs/2026-02-21/)
|
+-- maintenance/
|   +-- backups/
|       +-- src_backup_YYYYMMDD_HHMMSS.zip   Full src/ backup before each cleanup run
|
+-- src/                                  ALL Python source code (see Section 4)
|
+-- storage/
|   +-- databases/
|   |   +-- algo_trading.db               Main SQLite database (see Section 6)
|   +-- historical/
|   |   +-- options/                      1-min OHLC options candles (CSV cache)
|   |   +-- futures/                      Not yet populated
|   |   +-- equities/                     Not yet populated
|   +-- instrument_masters/
|   |   +-- derivatives/
|   |       +-- instrument_master.json    NSE F&O symbol master from Angel One
|   +-- live_cache/                       Future: ShoonyaLiveFeed intraday tick buffer
|   +-- logs/
|   |   +-- audit_trail/                  Fill log exports (CSV)
|   |   +-- live_trading/                 Structured logger output
|   |   +-- errors/                       Error logs
|   |   +-- signals/                      Signal event logs
|   +-- sync_cache/                       Broker session/token persistence files
|
+-- tests/
|   +-- conftest.py                       Shared pytest fixtures (mostly empty -- Sprint 9)
|   +-- integration/                      Empty -- integration tests live at project root
|   +-- unit/                             Empty -- not yet populated
|
+-- integration_test_sprint4.py           Sprint 4 tests (passing)
+-- integration_test_sprint5.py           Sprint 5 tests (passing)
+-- integration_test_sprint6.py           Sprint 6 tests (passing)
+-- integration_test_sprint7.py           Sprint 7 tests -- 76/76 passed
+-- integration_test_sprint8a.py          Sprint 8A tests -- 52/52 passed
+-- integration_test_master_audit.py      Full system audit -- 252/252 passed
+-- cleanup_trading_system.py             Cleanup utility (always --dry-run first)
+-- all_source_files.txt                  Auto-generated file dump -- not source code
```

### src/ Detailed Structure

```
src/
|
+-- broker_gateway/
|   +-- base_broker.py                    BrokerBase ABC -- all connectors implement this
|   +-- connection_manager.py             ConnectionHandler: SHOONYA->ANGEL->UPSTOX failover
|   +-- broker_shoonya/
|   |   +-- connector.py                  LIVE -- ShoonyaBroker (NorenAPI, TOTP)
|   +-- broker_angel/
|   |   +-- connector.py                  LIVE -- AngelBroker (SmartAPI, double-token)
|   +-- broker_upstox/
|   |   +-- connector.py                  LIVE -- UpstoxBroker (OAuth2, token persistence)
|   +-- broker_dhan/
|   |   +-- connector.py                  STUB -- credentials not configured
|   +-- broker_flattrade/
|   |   +-- connector.py                  STUB -- NorenAPI variant, not configured
|   +-- broker_kotak/
|   |   +-- connector.py                  STUB -- neo-api-client, not configured
|   +-- broker_zerodha/
|       +-- connector.py                  STUB -- kiteconnect, not configured
|
+-- config_loader/
|   +-- settings.py                       Config singleton (cfg) + INDEX_CONFIG dict
|
+-- execution/
|   +-- __init__.py                       Exports BacktestExecutionHandler, PaperExecutionHandler
|   +-- backtest_execution_handler.py     FROZEN -- SQLite-based fill simulation
|   +-- paper_handler.py                  FROZEN -- tick-buffer fill simulation
|
+-- indicators/
|   +-- indicator_engine.py               FROZEN -- IV, PCR, time decay per tick
|
+-- instruments/
|   +-- derivatives/
|   |   +-- options_chain.py              Contract resolution from Angel One master JSON
|   +-- equities/                         Not yet implemented
|
+-- market_feeds/
|   +-- live_feeds/
|       +-- feed_base.py                  Sprint 8B -- AbstractLiveFeed ABC
|       +-- shoonya_feed.py               Sprint 8B -- ShoonyaLiveFeed (WebSocket)
|       +-- tick_replay.py                FROZEN -- replays SQLite data as live ticks
|       +-- market_data_service.py        Legacy spot price fetcher (REST polling)
|
+-- scheduler/                            STUB -- automated session start/stop
|
+-- signal_hub/
|   +-- sources/
|       +-- webhook_receiver.py           LEGACY -- Flask server for TradingView webhooks
|       +-- base_source.py                STUB
|       +-- tradingview.py                STUB
|
+-- simulation_lab/
|   +-- backtest_runner.py                FROZEN -- single-strategy backtest driver
|   +-- live_session.py                   Sprint 8B -- push-based live session
|   +-- market_session.py                 FROZEN -- pull-based session orchestrator
|   +-- legacy/                           DEAD CODE -- confirm no imports, then DELETE
|
+-- strategies/
|   +-- base_strategy.py                  FROZEN -- BaseStrategy with inject_services() hooks
|   +-- coordinator.py                    FROZEN -- PortfolioCoordinator (multi-strategy)
|   +-- building_blocks/
|   |   +-- greeks_calculator.py          Black-Scholes Greeks (BS formula)
|   |   +-- leg_fill.py                   LegFill, FillStatus, ExecutionMode enum
|   |   +-- market_tick.py                MarketTick, CandleBar, GreekSnapshot
|   |   +-- options_leg.py                OptionsLeg, LegStatus (see Section 21.1)
|   |   +-- position_book.py              PositionBook -- open/closed legs + realised PnL
|   |   +-- trade_signal.py               TradeSignal, SignalType, SignalUrgency
|   +-- options_selling/
|   |   +-- iron_straddle.py              FROZEN -- IronStraddleStrategy state machine
|   +-- risk/
|       +-- risk_guard.py                 FROZEN -- daily/trade loss + cycle limits
|
+-- trade_desk/
|   +-- order_management/
|   |   +-- order_manager.py              LEGACY -- OrderEngine (paper + live orders)
|   +-- position_management/
|   |   +-- portfolio_tracker.py          STUB -- live MTM tracking
|   +-- risk_management/
|       +-- risk_manager.py               STUB -- pre-trade hard checks
|
+-- trading_records/
|   +-- db_connector.py                   Database singleton (db) -- full SQLite CRUD
|
+-- utilities/
    +-- logger.py                         Structured logger -- [key=value] format
    +-- enums.py                          STUB -- OrderType, TradingMode (Sprint 9)
    +-- event_bus.py                      STUB -- internal pub/sub (Sprint 9+)
    +-- notifier.py                       STUB -- Telegram/email/SMS alerts (Sprint 9+)
    +-- time_utils.py                     STUB -- IST timezone, market hours (Sprint 9)
```

---

## 4. All Scripts — Purpose Table

### 4.1 Project Root Scripts

| Script | Status | Purpose | Run Command |
|--------|--------|---------|-------------|
| `integration_test_sprint4.py` | Passing | Tests BacktestRunner + BacktestExecutionHandler | `python integration_test_sprint4.py` |
| `integration_test_sprint5.py` | Passing | Tests RiskGuard scenarios A and B | `python integration_test_sprint5.py` |
| `integration_test_sprint6.py` | Passing | Tests PortfolioCoordinator isolation | `python integration_test_sprint6.py` |
| `integration_test_sprint7.py` | 76/76 | Tests MarketSession + IndicatorEngine | `python integration_test_sprint7.py` |
| `integration_test_sprint8a.py` | 52/52 | Tests PaperExecutionHandler + TickReplayFeed | `python integration_test_sprint8a.py` |
| `integration_test_master_audit.py` | 252/252 | Full system audit across all sprints 1-8A | `python integration_test_master_audit.py` |
| `cleanup_trading_system.py` | Working | Finds dead code, duplicate inits, path issues | `python cleanup_trading_system.py --dry-run` |
| `all_source_files.txt` | Output only | Auto-generated dump of all source file paths | Not a script |

> **Rule:** Always run `integration_test_master_audit.py` before and after every sprint.
> All 252 tests must pass before Sprint 8B work begins.

### 4.2 Core Sprint Modules

| File | Class / Singleton | Status | Sprint | Purpose |
|------|------------------|--------|--------|---------|
| `execution/backtest_execution_handler.py` | `BacktestExecutionHandler` | FROZEN | 1 | Reads 1-min OHLC from SQLite, fills at candle close price |
| `execution/paper_handler.py` | `PaperExecutionHandler` | FROZEN | 8A | Fills at buffered tick prices, logs all fills with mode=PAPER |
| `strategies/building_blocks/options_leg.py` | `OptionsLeg`, `LegStatus` | FROZEN | 2 | Immutable data class for one option position |
| `strategies/building_blocks/trade_signal.py` | `TradeSignal`, `SignalType`, `SignalUrgency` | FROZEN | 2 | Signal emitted by strategy to orchestrator |
| `strategies/building_blocks/leg_fill.py` | `LegFill`, `FillStatus`, `ExecutionMode` | FROZEN | 2 | Fill confirmation returned by handler |
| `strategies/building_blocks/market_tick.py` | `MarketTick`, `CandleBar` | FROZEN | 2 | One 1-minute market snapshot |
| `strategies/building_blocks/position_book.py` | `PositionBook` | FROZEN | 2 | Tracks all open/closed legs and computes realised PnL |
| `strategies/building_blocks/greeks_calculator.py` | `GreeksCalculator` | FROZEN | 2 | Black-Scholes IV and Greeks |
| `strategies/options_selling/iron_straddle.py` | `IronStraddleStrategy` | FROZEN | 3 | Full straddle state machine NEUTRAL to DONE |
| `strategies/risk/risk_guard.py` | `RiskGuard`, `RiskAction`, `RiskDecision` | FROZEN | 5 | Per-lot daily/trade loss limits + max adjustment cycles |
| `strategies/base_strategy.py` | `BaseStrategy` | FROZEN | 7 | ABC with inject_services(), on_market_open(), on_tick() hooks |
| `strategies/coordinator.py` | `PortfolioCoordinator`, `PortfolioResult` | FROZEN | 6 | Runs multiple strategies in isolation on same data |
| `simulation_lab/backtest_runner.py` | `BacktestRunner`, `BacktestResult` | FROZEN | 4 | Single-strategy full-day backtest with result object |
| `simulation_lab/market_session.py` | `MarketSession`, `SessionResult` | FROZEN | 7 | Pull-based tick loop, multi-strategy, supports IndicatorEngine |
| `indicators/indicator_engine.py` | `IndicatorEngine`, `IndicatorSnapshot` | FROZEN | 7 | Per-tick IV, PCR, premium decay, time decay |
| `market_feeds/live_feeds/tick_replay.py` | `TickReplayFeed` | FROZEN | 8A | Loads SQLite data as MarketTick objects, preloads into PaperHandler |

### 4.3 Sprint 8B Modules (Pending)

| File | Class | Status | Purpose |
|------|-------|--------|---------|
| `market_feeds/live_feeds/feed_base.py` | `AbstractLiveFeed` | To Build | ABC: connect(), disconnect(), subscribe(), is_connected(), set_tick_callback() |
| `market_feeds/live_feeds/shoonya_feed.py` | `ShoonyaLiveFeed` | To Build | Shoonya NorenAPI WebSocket, 1-min candle aggregation, spot from index token |
| `simulation_lab/live_session.py` | `LiveSession`, `LiveSessionResult` | To Build | Push-based orchestrator, watchdog thread, stop() kill switch |
| `integration_test_sprint8b.py` | -- | To Build | ~60 tests including Rs.-932.75 accuracy gate through LiveSession |

### 4.4 Broker Gateway

| File | Class | Status | Auth Method | Primary Use |
|------|-------|--------|-------------|-------------|
| `base_broker.py` | `BrokerBase` (ABC) | Active | -- | Abstract interface all brokers implement |
| `connection_manager.py` | `ConnectionHandler` | Active | -- | Failover chain, unified get_ltp() across brokers |
| `broker_shoonya/connector.py` | `ShoonyaBroker` | LIVE | TOTP + vendor code | Order execution, WebSocket (Sprint 8B) |
| `broker_angel/connector.py` | `AngelBroker` | LIVE | TOTP + double-token | LTP lookups, instrument search |
| `broker_upstox/connector.py` | `UpstoxBroker` | LIVE | OAuth2 persisted | Historical OHLC, option chain |
| `broker_dhan/connector.py` | `DhanBroker` | STUB | -- | Not configured |
| `broker_flattrade/connector.py` | `FlatTradeBroker` | STUB | NorenAPI variant | Not configured |
| `broker_kotak/connector.py` | `KotakBroker` | STUB | neo-api-client | Not configured |
| `broker_zerodha/connector.py` | `ZerodhaBroker` | STUB | kiteconnect | Not configured |

### 4.5 Supporting Infrastructure

| File | Singleton | Status | Purpose |
|------|-----------|--------|---------|
| `config_loader/settings.py` | `cfg`, `INDEX_CONFIG` | Active | Loads .env, exposes all credentials and system paths. INDEX_CONFIG is a module-level dict |
| `trading_records/db_connector.py` | `db` | Active | Full SQLite CRUD for all 7 tables |
| `instruments/derivatives/options_chain.py` | `option_chain` | Active | Contract resolution, expiry calc, ATM strike, token lookup from Angel One master JSON |
| `market_feeds/live_feeds/market_data_service.py` | `market` | Active (legacy) | REST polling for spot price via ConnectionHandler failover |
| `utilities/logger.py` | `get_logger()` | Active | Structured logger -- [key=value] format, IST-dated log folders |
| `utilities/enums.py` | -- | STUB | Planned Sprint 9 -- shared enum definitions |
| `utilities/time_utils.py` | -- | STUB | Planned Sprint 9 -- IST timezone helpers, market hours |
| `utilities/notifier.py` | -- | STUB | Planned Sprint 9 -- Telegram/email alerts |
| `utilities/event_bus.py` | -- | STUB | Planned Sprint 9+ -- internal pub/sub |

### 4.6 Legacy System Scripts (Do Not Build On These)

| File | Status | Purpose | Fate |
|------|--------|---------|------|
| `signal_hub/sources/webhook_receiver.py` | LEGACY | Flask server receiving TradingView webhooks | Fate undecided -- Sprint 9 decision |
| `signal_hub/sources/tradingview.py` | STUB | TradingView signal parser | Depends on webhook_receiver fate |
| `signal_hub/sources/base_source.py` | STUB | Abstract signal source | Depends on webhook_receiver fate |
| `trade_desk/order_management/order_manager.py` | LEGACY | OrderEngine for paper + live orders (pre-sprint) | Merge into sprint system in Sprint 10 |
| `trade_desk/position_management/portfolio_tracker.py` | STUB | Live MTM tracking | Rebuild in Sprint 10 |
| `trade_desk/risk_management/risk_manager.py` | STUB | Pre-trade hard checks | Rebuild in Sprint 10 |
| `analytics/dashboard.py` | LEGACY | Streamlit dashboard reading positions.json | Migrate to SQLite in Sprint 9 |
| `analytics/performance/metrics_calculator.py` | STUB | Sharpe, drawdown, win rate | Sprint 9/10 |
| `analytics/performance/report_builder.py` | STUB | PDF report (ReportLab) | Sprint 9/10 |
| `analytics/trade_journal.py` | STUB | Trade journal | Sprint 9/10 |
| `simulation_lab/legacy/` | DEAD CODE | Old orchestration code -- no active imports | DELETE before Sprint 8B |

---

## 5. Execution Modes

### Mode 1 — Backtest (Working)

```
SQLite (options_ohlc, market_data)
    --> BacktestExecutionHandler
        --> BacktestRunner  OR  MarketSession  (pull-based loop)
            --> IronStraddleStrategy
                +-- PositionBook
                +-- RiskGuard (optional)
                +-- IndicatorEngine (optional)
```

- **Fill price:** Close price of the 1-minute candle at signal timestamp
- **Data granularity:** 1-minute OHLC
- **Entry point:** `BacktestRunner.run()` or `MarketSession.run()`
- **Result:** `BacktestResult` or `SessionResult` with PnL, legs, cycles
- **Verified:** Rs.-932.75 on 2026-02-11

### Mode 2 — Paper Trading Replay (Working)

```
SQLite (same data as backtest)
    --> TickReplayFeed  (loads and replays as MarketTick objects)
        --> PaperExecutionHandler  (buffers ticks, fills at tick prices)
            --> MarketSession (pull-based, same loop as backtest)
                --> IronStraddleStrategy
```

- **Fill price:** Tick buffer price at signal time (identical to backtest close -- proven)
- **Purpose:** Proves paper path is equivalent to backtest; dry-run before live
- **Entry point:** `MarketSession.run()` with `PaperExecutionHandler`
- **Result:** `SessionResult` with mode=PAPER fills
- **Verified:** Rs.-932.75 on 2026-02-11

### Mode 3 — Paper Trading Live (Sprint 8B -- Pending)

```
Shoonya WebSocket  (real-time LTP ticks)
    --> ShoonyaLiveFeed  (aggregates sub-second ticks into 1-min MarketTick)
        --> PaperExecutionHandler  (fills at current tick price, no real orders)
            --> LiveSession  (push-based, callback-driven)
                --> IronStraddleStrategy
```

- **Fill price:** Current tick price at signal time (simulated, no real orders placed)
- **Purpose:** Full live dry-run before committing real money
- **Kill switch:** `LiveSession.stop()` -- squares off all open legs, disconnects feed
- **Accuracy gate:** Must reproduce Rs.-932.75 through TickReplayFeed + LiveSession before accepting

### Mode 4 — Live Trading (Sprint 10 -- Planned)

```
ShoonyaLiveFeed  (same as Mode 3)
    --> LiveExecutionHandler  (places real orders via ShoonyaBroker.place_order())
        --> LiveSession
            --> IronStraddleStrategy
                +-- RiskGuard (mandatory)
```

- **Fill price:** Actual exchange fill price
- **Orders:** Real Shoonya orders, logged to `orders` SQLite table
- **Pre-trade checks:** `trade_desk/risk_management/risk_manager.py` (to be built Sprint 10)
- **Failover:** SHOONYA --> ANGEL --> UPSTOX via ConnectionManager

---

## 6. Database Schema

**File:** `storage/databases/algo_trading.db` (SQLite 3)
**Connector:** `src/trading_records/db_connector.py` -- singleton `db`
**Data source:** ICICI Breeze API (historical download, separate script, not in broker gateway)

### Tables

| Table | Rows Represent | Key Columns |
|-------|---------------|-------------|
| `strategies` | Registered strategy configurations | `strategy_id` PK, `name`, `structure`, `params` (JSON) |
| `sessions` | One trading day per strategy | `session_id` PK, `strategy_id` FK, `date`, `status`, `realized_pnl` |
| `positions` | One straddle/structure per entry | `position_id` PK, `session_id` FK, `atm_strike`, `entry_premium` |
| `legs` | One option leg per position | `leg_id` PK, `position_id` FK, `symbol`, `strike`, `option_type`, `side`, `entry_price`, `exit_price`, `realized_pnl` |
| `orders` | Every order attempt | `order_id` PK, `leg_id` FK, `symbol`, `side`, `fill_price`, `status`, `mode`, `broker_order_id` |
| `options_ohlc` | 1-min OHLC per option symbol | `timestamp`, `tradingsymbol`, `expiry`, `strike`, `option_type`, `close` |
| `market_data` | 1-min OHLC for spot index | `timestamp`, `symbol`, `close` |

### Critical Note on leg_id

`leg_id` in the `legs` table is a **database integer primary key**.
It is **NOT** an attribute on the `OptionsLeg` Python object.
Writing `fill.leg.leg_id` will raise `AttributeError`. See Section 21.1 for the correct pattern.

---

## 7. Brokers and Connection Status

### 7.1 Connection Summary

| Broker | Status | Auth Type | Connects On | Primary Role |
|--------|--------|-----------|-------------|-------------|
| Shoonya (Finvasia) | LIVE | TOTP + vendor code | App import | Order execution, WebSocket (Sprint 8B) |
| Angel One | LIVE | TOTP + double-token | App import | LTP lookups, instrument master |
| Upstox | LIVE | OAuth2 (token saved to file) | App import | Historical OHLC, option chain |
| Dhan | STUB | -- | -- | Not configured |
| FlatTrade | STUB | NorenAPI variant | -- | Not configured |
| Kotak Neo | STUB | neo-api-client | -- | Not configured |
| Zerodha | STUB | kiteconnect | -- | Not configured |
| ICICI Breeze | External | API key | Manual script | Historical data download only |

### 7.2 Broker Capabilities

| Capability | Shoonya | Angel One | Upstox | Breeze (ICICI) |
|-----------|---------|-----------|--------|----------------|
| Order placement | Yes | Yes | Yes | No |
| LTP -- REST poll | Yes | Yes | Yes | No |
| WebSocket LTP feed | Yes (Sprint 8B) | Yes (Sprint 9) | No | No |
| Option chain lookup | Yes | Yes | Yes | No |
| Historical OHLC | No | No | Yes | Yes |
| Instrument master | Yes (via search) | Yes (JSON master) | Yes (CSV) | No |
| Index spot price | Yes | Yes | Yes | No |

### 7.3 Failover Chain

```
Primary: SHOONYA  --> (if fails) ANGEL  --> (if fails) UPSTOX
Configured in: .env  BROKER_PRIORITY=SHOONYA,ANGEL,UPSTOX
Managed by: src/broker_gateway/connection_manager.py  ConnectionHandler
```

### 7.4 Known Issue -- Eager Broker Authentication

All three live brokers authenticate on import of their connector module. Because
`connection_manager.py` is imported transitively by almost every strategy module,
**every test run triggers 3 broker logins** -- burning TOTP tokens and adding 5-10 seconds
of latency to every test.

- **Current workaround:** None -- tests run with live auth every time
- **Fix:** Lazy init behind environment flag `LAZY_BROKER_INIT=1` -- planned Sprint 9

---

## 8. Broker WebSocket and Token Reference

This section documents the exact token values and API patterns for Sprint 8B.
All values confirmed from source code review of `settings.py`, `options_chain.py`, and `connector.py`.

### 8.1 INDEX_CONFIG -- Confirmed Values

```python
# From src/config_loader/settings.py
# Import: from config_loader.settings import INDEX_CONFIG
# Note: INDEX_CONFIG is MODULE-LEVEL, not inside the Config class

INDEX_CONFIG = {
    "NIFTY": {
        "spot_exchange":   "NSE",
        "spot_symbol":     "Nifty 50",
        "angel_token":     "99926000",
        "shoonya_token":   "26000",     # WebSocket spot subscription: "NSE|26000"
        "strike_gap":      50,
        "lot_size":        65,
        "option_exchange": "NFO",
        "option_prefix":   "NIFTY",
        "expiry_day":      1,           # Tuesday (0=Monday)
    },
    "BANKNIFTY": {
        "shoonya_token":   "26009",     # "NSE|26009"
        "option_exchange": "NFO",
        "expiry_day":      2,           # Wednesday
        "lot_size":        30,
        "strike_gap":      100,
    },
    "FINNIFTY": {
        "shoonya_token":   "26037",     # "NSE|26037"
        "option_exchange": "NFO",
        "expiry_day":      1,           # Tuesday
        "lot_size":        60,
        "strike_gap":      50,
    },
    "SENSEX": {
        "spot_exchange":   "BSE",
        "shoonya_token":   "1",         # "BSE|1"
        "option_exchange": "BFO",
        "expiry_day":      3,           # Thursday
        "lot_size":        20,
        "strike_gap":      100,
    },
}
```

### 8.2 WebSocket Subscription Format

```python
# Spot index (NIFTY): exchange|shoonya_token
spot_subscription = "NSE|26000"

# Options: exchange|token  (token from option_chain.get_contract())
option_subscription = "NFO|43215"   # example token

# Full subscription list for one straddle (4 options + 1 spot):
instruments = [
    "NSE|26000",    # NIFTY spot
    "NFO|43215",    # NIFTY17FEB2626000CE
    "NFO|43216",    # NIFTY17FEB2626000PE
    "NFO|43100",    # NIFTY17FEB2626200CE (CE hedge)
    "NFO|43101",    # NIFTY17FEB2625800PE (PE hedge)
]
```

### 8.3 NorenAPI WebSocket Methods

The current `ShoonyaBroker` connector has **no WebSocket methods** -- only REST.
`ShoonyaLiveFeed` accesses the underlying `NorenApi` object directly:

```python
from broker_gateway.broker_shoonya.connector import broker as shoonya_broker
self._api = shoonya_broker.api   # the raw NorenApi instance

# Start WebSocket (spawns internal thread -- callbacks fire on that thread)
self._api.start_websocket(
    order_update_callback = self._on_order_update,
    subscribe_callback    = self._on_tick,
    socket_open_callback  = self._on_open,
    socket_close_callback = self._on_close,
    socket_error_callback = self._on_error,
)

# Subscribe to instruments after socket opens
self._api.subscribe(instruments)   # list of "EXCHANGE|TOKEN" strings

# Unsubscribe on stop
self._api.unsubscribe(instruments)
```

### 8.4 WebSocket Tick Payload Format

```python
# Tick dict received in subscribe_callback:
{
    "t":  "tk",        # "tk" = quote tick, "df" = depth
    "e":  "NFO",       # exchange
    "tk": "43215",     # token (string)
    "lp": "130.25",    # last price (STRING -- must cast to float)
    "ts": "09:30:01",  # timestamp HH:MM:SS IST (not always present)
    "v":  "1250",      # volume (optional, may be absent)
}

# For spot index tick:
{
    "t":  "tk",
    "e":  "NSE",
    "tk": "26000",
    "lp": "25977.20",
}
```

### 8.5 Token Resolution for Live Sessions

`options_chain.get_contract()` provides tokens. Requires master file loaded first.

```python
from instruments.derivatives.options_chain import option_chain
from datetime import datetime

# Step 1: Load master (once per session)
option_chain.load_master()

# Step 2: Convert sprint expiry format to options_chain format
expiry_sprint = "2026-02-17"                              # sprint system format
expiry_oc     = datetime.strptime(expiry_sprint, "%Y-%m-%d").strftime("%d%b%Y").upper()
#               "17FEB2026"                               # options_chain format

# Step 3: Get contract with token
contract = option_chain.get_contract("NIFTY", "17FEB2026", 26000, "CE")
# Returns: {"symbol": "NIFTY17FEB2626000CE", "token": "43215", "exchange": "NFO", ...}

# Step 4: Build subscription string
subscription = f"{contract['exchange']}|{contract['token']}"
# Result: "NFO|43215"
```

### 8.6 Threading Model for ShoonyaLiveFeed

```
Main Thread
    --> LiveSession.start()
        --> option_chain.load_master()
        --> feed.connect()
            --> NorenApi.start_websocket()  [spawns WebSocket thread internally]
                 |
                 v
        WebSocket Thread (owned by NorenApi)
            --> _on_open()      sets _connected = True
            --> _on_tick()      updates _price_buffer (Lock protected)
                                detects minute boundary
                                emits MarketTick to LiveSession callback
            --> _on_close()     sets _connected = False

Watchdog Thread (threading.Thread, daemon=True)
    --> polls feed.is_connected() every 10 seconds
    --> if False for >30s: calls LiveSession.stop()

stop() is re-entrant safe via _stopping flag.
threading.Lock protects _price_buffer (written by WS thread, read by main/watchdog).
threading.Event used for watchdog stop signal.
No asyncio needed.
```

---

## 9. Strategy -- Iron Straddle

**File:** `src/strategies/options_selling/iron_straddle.py`
**Class:** `IronStraddleStrategy(BaseStrategy)`
**Status:** FROZEN -- do not modify without new integration test

### 9.1 Parameters -- Actual Live Defaults

> All values confirmed from source code by master audit 2026-02-21.
> Earlier SSOT versions incorrectly listed sl_pct=0.60 and reversion_buffer=75.
> Those were aspirational target values, not actual code defaults.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `lot_size` | 65 | NIFTY lot size (NSE Jan 2026 revision) |
| `strike_step` | 50 | NIFTY strike interval |
| `hedge_offset` | 200 | Distance from ATM to hedge strike |
| `sl_pct` | 0.30 | Stop-loss as fraction of combined premium (30%) |
| `reversion_buffer` | 15 | Points from ATM to trigger reversion check |
| `entry_time` | "09:30" | Entry candle timestamp (HH:MM) |
| `exit_time` | "15:20" | EOD square-off candle timestamp (HH:MM) |

### 9.2 Leg Structure at Entry

| Internal Key | Option Type | Strike | Direction |
|--------------|-------------|--------|-----------|
| `CE_SELL` | CE | ATM | SELL (premium collected) |
| `PE_SELL` | PE | ATM | SELL (premium collected) |
| `CE_BUY` | CE | ATM + 200 | BUY (hedge) |
| `PE_BUY` | PE | ATM - 200 | BUY (hedge) |

4 legs opened at entry. Adjustment opens 2 more. Maximum 6 legs per day.

### 9.3 State Machine

```
NEUTRAL  (initial state after market open)
  |
  +-- CE SL breached (spot moved up)
  |     --> close CE_SELL + CE_BUY, open new PE_ADJ position
  |     --> state: ADJUSTED
  |
  +-- PE SL breached (spot moved down)
  |     --> close PE_SELL + PE_BUY, open new CE_ADJ position
  |     --> state: ADJUSTED
  |
  +-- Both SLs breached simultaneously
        --> close all 4 legs
        --> state: ALL_OUT (G1 re-entry watch)

ADJUSTED
  |
  +-- Spot reverts toward ATM (within reversion_buffer)
  |     --> close ADJ legs, reopen original tested side
  |     --> state: NEUTRAL (new entry, cycle count unchanged)
  |
  +-- ADJ SL breached
        --> close ADJ legs only
        --> state: FLIPPED

FLIPPED
  --> EOD --> square off remaining legs --> DONE

ALL_OUT (G1 triggered)
  --> Spot reverts into original entry band --> re-enter --> NEUTRAL

DONE  (terminal)
  --> All legs closed, session ended
```

### 9.4 inject_services() Pattern

Called by every orchestrator (BacktestRunner, MarketSession, LiveSession):

```python
strategy.inject_services(handler)
strategy.inject_services(handler, risk_guard=rg)   # second call, overwrites first
```

The double-call produces two INFO log lines. This is intentional and correct -- not a bug.

---

## 10. What is Working

### Backtest Pipeline
- `BacktestExecutionHandler` loads from SQLite, fills at 1-min close prices
- `BacktestRunner` runs a full day, returns `BacktestResult` with accurate PnL
- `MarketSession` orchestrates multi-strategy sessions with RiskGuard and IndicatorEngine
- `PortfolioCoordinator` runs two independent strategies in isolation on the same data
- Rs.-932.75 reproduced on 2026-02-11 across all 5 execution paths simultaneously

### Strategy Engine
- `IronStraddleStrategy` state machine: all 5 states, all transitions working correctly
- Adjustment cycle logic: open, adjust, revert, flip -- all paths verified
- EOD square-off with correct PnL calculation
- inject_services() with and without RiskGuard

### Risk Management
- `RiskGuard` enforces daily loss limit (per lot), trade loss limit (per lot), max adjustment cycles
- Scenario A (clean, limits not hit) and Scenario B (hard stop fires) both verified
- BLOCK vs SQUARE_OFF actions correctly differentiated (see Section 21.3)

### Paper Trading
- `TickReplayFeed` loads SQLite data and replays as `MarketTick` objects
- `PaperExecutionHandler` fills at buffered tick prices, identical to backtest
- Fill log audit: all fills have mode=PAPER, prices match tick buffer exactly
- Rs.-932.75 reproduced through full paper path

### Indicator Engine
- IV calculated per tick using Black-Scholes formula
- PCR rolling window computed correctly
- Premium decay and time decay metrics working
- Does not affect PnL -- confirmed by Scenario D in master audit

### Brokers
- Shoonya: authenticates via TOTP, get_ltp() works, place_order() works
- Angel One: authenticates via double-token, get_ltp() works, search_instruments() works
- Upstox: OAuth2 token persists to file, reconnects without fresh TOTP

### Infrastructure
- `settings.py`: all paths PROJECT_ROOT-relative, no hardcoded Windows paths
- `db_connector.py`: full CRUD on all 7 tables confirmed working
- `option_chain`: contract resolution, expiry calc, ATM strike, token lookup from JSON
- `logger.py`: structured [key=value] format, IST-dated log folders
- `connection_manager.py`: failover chain SHOONYA->ANGEL->UPSTOX working

### Test Suite
- 252/252 tests passing across master audit
- 5 execution paths all reproduce Rs.-932.75 exactly
- Import health check verifies all sprint modules load without error

---

## 11. What is Not Working

### Sprint 8B Features (Not Yet Built)
- `ShoonyaLiveFeed` -- no WebSocket subscription exists yet
- `LiveSession` -- no push-based orchestrator yet
- `AbstractLiveFeed` -- interface not yet defined
- Real-time paper trading during market hours -- not possible until Sprint 8B

### Legacy System (Broken or Untested)
- Streamlit dashboard reads `positions.json` which is not updated by the sprint system
- TradingView webhook flow is untested since the 2026-02-21 cleanup run
- Dashboard performance metrics (Sharpe, drawdown) are STUB -- not implemented

### Data Gaps
- Historical data for BANKNIFTY, FINNIFTY, SENSEX not in SQLite (only NIFTY populated)
- Futures table empty -- no data
- Equities table empty -- no data
- Multi-day backtest runner not yet built (Sprint 9)

### Broker Limitations
- Shoonya connector has no WebSocket subscription code (REST only -- WebSocket added Sprint 8B)
- Upstox has no WebSocket support at all (REST only, by design)
- No order execution in any automated test (manual smoke testing only)
- No pre-trade risk checks (`risk_manager.py` is STUB)
- `portfolio_tracker.py` (live MTM) is STUB

---

## 12. Known Issues and Bugs

| ID | Severity | Description | File | Found |
|----|----------|-------------|------|-------|
| BUG-01 | High | Brokers authenticate eagerly on every import -- burns TOTP tokens, adds 5-10s to every test | `config_loader/settings.py` + connectors | 2026-02-21 |
| BUG-02 | Medium | `OptionsLeg` does not expose `OptionType` or `LegSide` as public module names -- caused Section 1 crash in master audit | `building_blocks/options_leg.py` | 2026-02-21 |
| BUG-03 | Medium | `OptionsLeg` may or may not accept `leg_id` constructor argument depending on version | `building_blocks/options_leg.py` | 2026-02-21 |
| BUG-04 | Medium | `simulation_lab/legacy/` contains dead code -- not yet confirmed whether any active imports exist | `simulation_lab/legacy/` | 2026-02-21 |
| BUG-05 | Medium | Dashboard reads `positions.json` not SQLite -- sprint system trades are invisible to dashboard | `analytics/dashboard.py` | 2026-02-21 |
| BUG-06 | Low | `cleanup_trading_system.py` raises SyntaxWarning for `\w` in a docstring comment | `cleanup_trading_system.py` | 2026-02-21 |
| BUG-07 | Low | `ExecutionMode` enum defined in `leg_fill.py` instead of `utilities/enums.py` -- wrong location | `building_blocks/leg_fill.py` | 2026-02-21 |
| BUG-08 | Low | `utilities/enums.py`, `time_utils.py`, `notifier.py` are completely empty stubs | `utilities/` | 2026-02-21 |
| BUG-09 | Low | `tests/integration/` and `tests/unit/` are empty -- all tests live at project root | `tests/` | 2026-02-21 |
| BUG-10 | Low | `signal_hub/webhook_receiver.py` fate undefined -- legacy or future TradingView entry point | `signal_hub/` | 2026-02-21 |

---

## 13. Tech Debt Register

| ID | Priority | Item | Current State | Planned Resolution |
|----|----------|------|--------------|-------------------|
| TD-01 | High | Lazy broker initialisation | Eager on import | Sprint 9 -- env flag `LAZY_BROKER_INIT=1` |
| TD-02 | High | Delete `simulation_lab/legacy/` | Not deleted yet | Before Sprint 8B starts |
| TD-03 | Medium | Consolidate enums | `ExecutionMode` in wrong file, others missing | Sprint 9 -- populate `utilities/enums.py` |
| TD-04 | Medium | Implement `utilities/time_utils.py` | Empty stub | Sprint 9 -- IST timezone, market hours, expiry |
| TD-05 | Medium | Migrate dashboard to SQLite | Reads flat files | Sprint 9 |
| TD-06 | Medium | Move integration tests to `tests/integration/` | All at project root | Sprint 9 |
| TD-07 | Medium | Populate `utilities/notifier.py` | Empty stub | Sprint 9 -- Telegram alert on risk halt |
| TD-08 | Low | Merge `trade_desk/OrderEngine` with sprint system | Parallel paper trading paths | Sprint 10 |
| TD-09 | Low | Decide fate of `signal_hub/webhook_receiver.py` | Undefined scope | Sprint 9 -- decide and document |
| TD-10 | Low | Fix SyntaxWarning in cleanup script | `\w` in docstring | Next cleanup version |
| TD-11 | Low | Multi-day backtest runner | Single-day only | Sprint 9 |
| TD-12 | Low | BANKNIFTY / FINNIFTY / SENSEX historical data | Not in SQLite | Sprint 9 -- download via Breeze |

---

## 14. Sprint History and Status

| Sprint | Status | Modules | Tests | Key Achievement |
|--------|--------|---------|-------|----------------|
| Sprint 1 | Done | `BacktestExecutionHandler` | -- | SQLite fill simulation -- Rs.-932.75 baseline established |
| Sprint 2 | Done | `OptionsLeg`, `TradeSignal`, `LegFill`, `MarketTick`, `PositionBook` | -- | All core building block data classes |
| Sprint 3 | Done | `IronStraddleStrategy` | -- | Complete state machine NEUTRAL->ADJUSTED->FLIPPED->ALL_OUT->DONE |
| Sprint 4 | Done | `BacktestRunner`, `BacktestResult` | -- | Full-day backtest driver, Rs.-932.75 confirmed |
| Sprint 5 | Done | `RiskGuard`, `RiskAction`, `RiskDecision` | -- | Per-lot loss limits, max cycles, BLOCK vs SQUARE_OFF |
| Sprint 6 | Done | `PortfolioCoordinator`, `PortfolioResult` | -- | Multi-strategy isolation -- one halt does not stop others |
| Sprint 7 | Done | `MarketSession`, `SessionResult`, `IndicatorEngine`, `IndicatorSnapshot` | 76/76 | Production orchestrator + full IV/PCR/decay indicators |
| Sprint 8A | Done | `PaperExecutionHandler`, `TickReplayFeed` | 52/52 | Accuracy gate: Rs.-932.75 through paper path identical to backtest |
| Audit | Done | `integration_test_master_audit.py` | 252/252 | Full system verification, SSOT corrected, API contracts documented |
| Sprint 8B | Ready | `AbstractLiveFeed`, `ShoonyaLiveFeed`, `LiveSession` | ~60 planned | Push-based paper trading via Shoonya WebSocket |
| Sprint 9 | Planned | YAML config, multi-day runner, lazy broker init | TBD | Config-driven execution, infrastructure improvements |
| Sprint 10 | Planned | `LiveExecutionHandler`, `OrderEngine` integration | TBD | Real money trading via Shoonya with full audit trail |

**Cumulative tests passing: 252/252**

---

## 15. Accuracy Benchmark

The Rs.-932.75 benchmark is the **non-negotiable accuracy gate**.
Any new execution path must reproduce this number within Rs.+/-1.00 before it is considered complete.

### Benchmark Trade Details

| Field | Value |
|-------|-------|
| Date | 2026-02-11 |
| Expiry | 2026-02-17 |
| ATM Strike | 26000 |
| Opening Spot | 25976.05 |
| Entry Time | 09:30 |
| CE Sell Price | Rs.130.30 |
| PE Sell Price | Rs.115.50 |
| Combined Premium | Rs.245.80 |
| Adjustment | 1 cycle at 12:42 (spot 25902.45) |
| Adjustment Direction | PE SL hit -- CE_ADJ opened |
| Total Legs Traded | 6 |
| Open Legs at EOD | 0 |
| Net Realised PnL | Rs.-932.75 |
| Tolerance | Rs.+/-1.00 |

### Verified Execution Paths

| Path | Status | Sprint |
|------|--------|--------|
| `BacktestExecutionHandler` direct | Verified | 1 |
| `BacktestRunner` | Verified | 4 |
| `PortfolioCoordinator` single strategy | Verified | 6 |
| `PortfolioCoordinator` multi-strategy (strategy 1) | Verified | 6 |
| `MarketSession` + `BacktestExecutionHandler` | Verified | 7 |
| `MarketSession` + `PaperExecutionHandler` + `TickReplayFeed` | Verified | 8A |
| `MarketSession` + `PaperExecutionHandler` + `RiskGuard` (silent) | Verified | 8A |
| `MarketSession` + `PaperExecutionHandler` + `IndicatorEngine` | Verified | 8A |
| All 5 paths simultaneously (benchmark matrix) | Verified | Audit |
| `LiveSession` + `TickReplayFeed` + `PaperExecutionHandler` | Pending | Sprint 8B |

---

## 16. Action Planned -- Sprint 8B

**Goal:** Push-based live paper trading via Shoonya WebSocket.
A complete trading session runs automatically from 09:15 to 15:30 without manual intervention.

### Pre-Sprint Checklist (Complete Before Writing Any Code)

- [ ] Confirm `simulation_lab/legacy/` has no active imports: `grep -r "simulation_lab.legacy" src/`
- [ ] Delete `simulation_lab/legacy/` after confirming zero results
- [ ] Run `integration_test_master_audit.py` -- must show 252/252 as clean baseline
- [ ] Re-read Section 21 (API Contracts) before designing any interfaces

### Files to Create -- Exactly These 4, Nothing Else

| File | Class(es) | Estimated Lines |
|------|-----------|----------------|
| `src/market_feeds/live_feeds/feed_base.py` | `AbstractLiveFeed` (ABC) | ~60 |
| `src/market_feeds/live_feeds/shoonya_feed.py` | `ShoonyaLiveFeed` | ~200 |
| `src/simulation_lab/live_session.py` | `LiveSession`, `LiveSessionResult` | ~250 |
| `integration_test_sprint8b.py` | -- | ~400 |

No utility files. No helpers. No `__init__.py` changes. No frozen file modifications.

### Design Decisions (Confirmed Before Coding)

1. **Candle aggregation:** `ShoonyaLiveFeed` aggregates sub-second LTP ticks into 1-minute close prices. Emits `MarketTick` only at each minute boundary. Matches backtest granularity exactly.

2. **Spot subscription:** Subscribe `"NSE|26000"` on the WebSocket alongside option tokens. When `tk=="26000"` and `e=="NSE"`, update `_spot`. No REST calls for spot during session.

3. **Token resolution:** `LiveSession.start()` calls `option_chain.load_master()` then `option_chain.get_contract()` per symbol. Converts `"2026-02-17"` to `"17FEB2026"` inline.

4. **Threading:** NorenApi owns the WebSocket thread. One `threading.Lock` for `_price_buffer`. One `threading.Event` for watchdog. No asyncio.

5. **Watchdog:** `threading.Thread` (daemon) polls `feed.is_connected()` every 10s. If False for >30s, calls `LiveSession.stop()`.

6. **Replay accuracy gate:** `LiveSession` detects `hasattr(feed, 'get_ticks')` to enter synchronous replay mode. No changes to `TickReplayFeed`. This is how Rs.-932.75 is verified in automated tests.

7. **`stop()` is re-entrant safe:** Sets `_stopping` flag immediately. All subsequent calls are no-ops. Safe to call from any thread.

8. **`LiveSessionResult`** mirrors `SessionResult` and adds `feed_name: str`.

### Acceptance Criteria

- [ ] `integration_test_sprint8b.py` -- all ~60 tests pass
- [ ] Rs.-932.75 reproduced: `TickReplayFeed` --> `LiveSession` --> `PaperExecutionHandler`
- [ ] `ShoonyaLiveFeed.connect()` succeeds and `is_connected()` returns True
- [ ] Subscribing 6 option tokens + 1 spot token completes without error
- [ ] `LiveSession.stop()` -- all open legs squared off, fill log populated
- [ ] `integration_test_master_audit.py` still 252/252 after Sprint 8B merges

---

## 17. Action Pending -- Sprint 9 and Beyond

### Sprint 9 -- Config, Infrastructure, Multi-Day

| Item | Purpose | Priority |
|------|---------|---------|
| YAML strategy config | Parameters per strategy without code changes | High |
| YAML system rules | Kill switches, mode toggles | High |
| Lazy broker init (`LAZY_BROKER_INIT=1`) | Skip auth in tests, save TOTP tokens | High |
| Multi-day backtest runner | Loop over date range, aggregate results | High |
| `AngelLiveFeed` | WebSocket backup to Shoonya | Medium |
| Move integration tests to `tests/integration/` | Project structure | Medium |
| `utilities/time_utils.py` | IST timezone, market hours, expiry date helpers | Medium |
| `utilities/enums.py` | Consolidate all enums | Medium |
| `utilities/notifier.py` | Telegram alert on risk halt or session end | Medium |
| Dashboard migration to SQLite | Sprint system trades visible in Streamlit | Medium |
| Decide `signal_hub/` fate | Legacy or future TradingView entry point | Low |
| BANKNIFTY/FINNIFTY/SENSEX data | Download via Breeze and populate SQLite | Low |

### Sprint 10 -- Live Trading

| Item | Purpose | Priority |
|------|---------|---------|
| `LiveExecutionHandler` | Places real orders via `ShoonyaBroker.place_order()` | Critical |
| `trade_desk/risk_management/risk_manager.py` | Pre-trade hard checks before order placement | Critical |
| Order logging to SQLite `orders` table | Full audit trail for real orders | Critical |
| Merge `trade_desk/OrderEngine` with sprint system | Eliminate parallel paper trading path | High |
| Live position MTM tracking (`portfolio_tracker.py`) | Real-time PnL during live session | High |
| Emergency kill switch | Command-line halt of all open positions immediately | Critical |

---

## 18. Cleanup Required

### Before Sprint 8B (Mandatory)

| # | Action | Location | How to Verify |
|---|--------|----------|---------------|
| 1 | Confirm zero active imports from `simulation_lab/legacy/` | `simulation_lab/legacy/` | `grep -r "simulation_lab.legacy" src/` returns nothing |
| 2 | Delete `simulation_lab/legacy/` folder | `simulation_lab/legacy/` | Folder does not exist |
| 3 | Run master audit as clean baseline | `integration_test_master_audit.py` | 252/252 PASSED |

### Sprint 9 Cleanup

| # | Action | Location | Reason |
|---|--------|----------|--------|
| 4 | Fix `\w` SyntaxWarning | `cleanup_trading_system.py` | Noisy output |
| 5 | Move `ExecutionMode` enum to `utilities/enums.py` | `building_blocks/leg_fill.py` | Wrong location |
| 6 | Move integration tests to `tests/integration/` | Project root | Project hygiene |
| 7 | Populate `utilities/enums.py` | `utilities/enums.py` | Currently empty stub |
| 8 | Populate `utilities/time_utils.py` | `utilities/time_utils.py` | Currently empty stub |
| 9 | Consider deleting stub broker connectors (Dhan, FlatTrade, Kotak, Zerodha) | `broker_gateway/` | Reduce confusion for new developers |

### Post-Sprint 10 Cleanup

| # | Action | Location | Reason |
|---|--------|----------|--------|
| 10 | Archive or remove legacy system files | `signal_hub/`, `trade_desk/order_management/` | Replaced by sprint system |
| 11 | Archive old `analytics/dashboard.py` | `analytics/` | Replaced by SQLite-backed dashboard |

---

## 19. Immediate Next Steps

In strict priority order. Do not start the next item until the current one is confirmed complete.

### Step 1 -- Delete simulation_lab/legacy/

```bash
cd C:\Rajat\trading_infrastructure
grep -r "simulation_lab.legacy" src/
# Must return nothing -- if it finds imports, investigate before deleting

rmdir /s /q src\simulation_lab\legacy
python integration_test_master_audit.py
# Must still show 252/252 PASSED
```

### Step 2 -- Run Master Audit as Clean Baseline

```bash
cd C:\Rajat\trading_infrastructure
set PYTHONPATH=src
python integration_test_master_audit.py
# Expected: 252/252 PASSED -- exit code 0
# This is the baseline that must be preserved throughout Sprint 8B
```

### Step 3 -- Build Sprint 8B

Follow Section 16 exactly. Create only the 4 declared files. Read Section 21 first.
Run `integration_test_master_audit.py` before starting and after completing each file.

### Step 4 -- Run Both Audits After Sprint 8B

```bash
python integration_test_master_audit.py    # must still be 252/252
python integration_test_sprint8b.py        # new tests must all pass
```

### Step 5 -- Update This SSOT

After every sprint:
- Update Section 4 (script table) with new modules
- Update Section 10 (what is working) with new capabilities
- Update Section 14 (sprint history) -- move 8B to Done, update test count
- Update Section 15 (benchmark) -- add LiveSession path to verified list
- Update Sections 16/17 -- move 8B to completed, update 9 as next
- Update the header date and status line

---

## 20. Configuration Reference

### 20.1 Environment Variables (`.env`)

| Variable | Example Value | Purpose |
|----------|--------------|---------|
| `TRADING_MODE` | `PAPER` | `PAPER` or `LIVE` -- controls `OrderEngine` mode |
| `PRIMARY_BROKER` | `SHOONYA` | Primary broker for order execution |
| `BROKER_PRIORITY` | `SHOONYA,ANGEL,UPSTOX` | Failover chain (comma-separated, ordered) |
| `DB_NAME` | `algo_trading.db` | SQLite database filename |
| `LOG_LEVEL` | `INFO` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |
| `WEBHOOK_PORT` | `8000` | Legacy Flask webhook server port |
| `DASHBOARD_PORT` | `8501` | Streamlit dashboard port |
| `SHOONYA_USER_ID` | -- | Shoonya account ID (Finvasia) |
| `SHOONYA_PASSWORD` | -- | Shoonya password |
| `SHOONYA_API_SECRET` | -- | API secret |
| `SHOONYA_TOTP_KEY` | -- | TOTP seed for pyotp |
| `SHOONYA_VENDOR_CODE` | -- | Vendor code |
| `SHOONYA_IMEI` | -- | Device identifier |
| `ANGEL_API_KEY` | -- | Angel One SmartAPI key |
| `ANGEL_CLIENT_ID` | -- | Angel One client ID |
| `ANGEL_PASSWORD` | -- | Angel One password |
| `ANGEL_TOTP_KEY` | -- | TOTP seed for pyotp |
| `UPSTOX_API_KEY` | -- | Upstox API key |
| `UPSTOX_API_SECRET` | -- | Upstox API secret |
| `UPSTOX_REDIRECT_URI` | `http://127.0.0.1:8000/` | OAuth2 redirect URI |

### 20.2 Computed System Paths

All paths computed from `PROJECT_ROOT = Path(__file__).parent.parent.parent`
(three levels up from `src/config_loader/settings.py`):

| Variable | Resolves To |
|----------|-------------|
| `cfg.DB_FULL_PATH` | `PROJECT_ROOT/storage/databases/algo_trading.db` |
| `cfg.LOG_DIR` | `PROJECT_ROOT/storage/logs/live_trading` |
| `cfg.INSTRUMENT_MASTER` | `PROJECT_ROOT/storage/instrument_masters/derivatives/instrument_master.json` |
| `cfg.PROJECT_ROOT` | `C:\Rajat\trading_infrastructure` |

### 20.3 Runtime Commands

```bash
# Required for all scripts -- PYTHONPATH must include src/
cd C:\Rajat\trading_infrastructure
set PYTHONPATH=src

# Full system audit (run before and after every sprint)
python integration_test_master_audit.py

# Individual sprint tests
python integration_test_sprint8a.py
python integration_test_sprint8b.py    # Sprint 8B (once built)

# Legacy dashboard (reads positions.json, not sprint system data)
streamlit run src/analytics/dashboard.py

# Legacy webhook server
python src/signal_hub/sources/webhook_receiver.py

# Cleanup utility
python cleanup_trading_system.py --dry-run   # always dry-run first
python cleanup_trading_system.py

# Quick backtest (no test harness)
# Create a small runner script or Python REPL:
#   from simulation_lab.backtest_runner import BacktestRunner
#   from strategies.options_selling.iron_straddle import IronStraddleStrategy
#   r = BacktestRunner(date="2026-02-11", expiry="2026-02-17", strikes=[25800,26000,26200])
#   print(r.run())   # should show Rs.-932.75
```

---

## 21. API Contracts and Design Decisions

Critical reference for anyone writing tests or new modules.
Read this section before writing code that interacts with any sprint module.
These are lessons learned the hard way -- they caused real test failures.

### 21.1 -- OptionsLeg: Public API and Limitations

**File:** `src/strategies/building_blocks/options_leg.py`
**Public exports:** `OptionsLeg`, `LegStatus`

**Names that do NOT exist at module level (will raise ImportError):**
```python
from strategies.building_blocks.options_leg import OptionType  # FAILS
from strategies.building_blocks.options_leg import LegSide     # FAILS
```

**Safe way to check option_type and side** (works whether value is enum or string):
```python
assert "CE" in str(leg.option_type)
assert "SELL" in str(leg.side)
```

**`leg_id` does not exist on `OptionsLeg`:**
`leg_id` is a database primary key in the `legs` SQLite table -- an integer assigned by SQLite.
The Python `OptionsLeg` object has no such attribute. `fill.leg.leg_id` raises `AttributeError`.

**Correct way to identify a fill by leg type:**
```python
pe_sell = next(
    (f for f in fills
     if "26000PE" in f.leg.symbol and abs(f.fill_price - 115.50) < 5.0),
    None
)
```

**Constructor `leg_id` may or may not be accepted** (version-dependent):
```python
try:
    leg = OptionsLeg(leg_id="CE_SELL", symbol=..., strike=..., ...)
except TypeError:
    leg = OptionsLeg(symbol=..., strike=..., ...)
```

### 21.2 -- RiskGuard: Halting is Explicit, Not Passive

`record_pnl(amount)` only accumulates `daily_pnl`. It **never** sets `is_halted`.

`is_halted` is only set when `check_entry()` or `check_adjustment()` detects a breach.
This is intentional -- the guard evaluates limits at decision points, not continuously.

```python
rg = RiskGuard(max_daily_loss_per_lot=-5, lot_size=65)   # threshold = -325
rg.record_pnl(-400.0)
assert rg.is_halted == False    # still False -- no check_* called yet
assert rg.daily_pnl == -400.0  # accumulated correctly

decision = rg.check_entry("Iron Straddle", position_book)
assert rg.is_halted == True     # NOW halted -- check_entry detected breach
assert decision.allowed == False
```

### 21.3 -- RiskGuard: BLOCK vs SQUARE_OFF

| Action | Returned When | Effect on Guard | Effect on Positions |
|--------|--------------|----------------|---------------------|
| `ALLOW` | All limits within bounds | No change | None |
| `BLOCK` | `check_adjustment()` -- max cycles reached | Not halted | Do not open new legs; keep existing |
| `SQUARE_OFF` | `check_entry()` or `check_adjustment()` -- loss limit breached | Halted | Close all open positions |

`check_entry()` on a breached guard always returns `SQUARE_OFF`, never `BLOCK`.
`BLOCK` is exclusively returned by `check_adjustment()` for the max-cycles soft stop.

### 21.4 -- inject_services() Two-Call Pattern

Every orchestrator calls inject_services twice per strategy per session:

```
[INFO] iron_straddle: Services injected [mode=BacktestExecutionHandler]
[INFO] iron_straddle: Services injected [mode=BacktestExecutionHandler risk_guard=None]
```

This is expected and correct. The second call overwrites the first. Not a bug.

### 21.5 -- Expiry Format: Sprint System vs options_chain

The sprint system uses ISO format: `"2026-02-17"` (YYYY-MM-DD)
`options_chain.py` uses: `"17FEB2026"` (DDMMMYYYY, uppercase)

Convert when calling `option_chain.get_contract()`:
```python
from datetime import datetime
expiry_oc = datetime.strptime("2026-02-17", "%Y-%m-%d").strftime("%d%b%Y").upper()
# Result: "17FEB2026"
```

### 21.6 -- options_chain Must Be Loaded First

`option_chain` is a singleton. `load_master()` is not called automatically.

```python
from instruments.derivatives.options_chain import option_chain
option_chain.load_master()   # reads from cfg.INSTRUMENT_MASTER (JSON file)
contract = option_chain.get_contract("NIFTY", "17FEB2026", 26000, "CE")
# Returns: {"symbol": "NIFTY17FEB2626000CE", "token": "43215", "exchange": "NFO", ...}
```

If `load_master()` is not called, `_ensure_loaded()` attempts it automatically
but may raise `RuntimeError` if the JSON file is not found.

### 21.7 -- Sprint vs Legacy Import Boundary

| Allowed | Forbidden |
|---------|-----------|
| Sprint modules importing `config_loader`, `utilities`, `trading_records` | Sprint modules importing `signal_hub`, `trade_desk/order_management`, `analytics` |
| `market_feeds/live_feeds/` importing `broker_gateway` | Any circular imports |
| `building_blocks/` importing only stdlib and `utilities/` | `building_blocks/` importing any higher-level module |

### 21.8 -- TickReplayFeed Interface (Reference for Sprint 8B)

```python
feed = TickReplayFeed(date="2026-02-11", expiry="2026-02-17", strikes=[25800, 26000, 26200])
feed.load()                    # loads from SQLite
feed.is_loaded                 # True after load()
feed.tick_count                # total number of ticks
feed.days_to_expiry            # float: days from session date to expiry
feed.get_ticks()               # list[MarketTick]
feed.get_timestamps()          # list[str] ISO timestamp strings
feed.get_symbol_map()          # dict: (strike, opt_type) -> symbol
feed.get_spot_cache()          # dict: timestamp -> spot price float
feed.preload_handler(handler)  # transfers tick buffer to PaperExecutionHandler
```

`LiveSession` detects `hasattr(feed, 'get_ticks')` to decide whether to use replay mode.

---

## 22. Handover Notes for New Developers

### What to Read First

1. This document (SSOT) -- complete overview of the system
2. `src/strategies/options_selling/iron_straddle.py` -- the core strategy
3. `integration_test_master_audit.py` -- shows exactly how every component is used
4. `src/simulation_lab/market_session.py` -- the pull-based orchestrator (model for LiveSession)

### Environment Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt   # if present, otherwise pip install manually

# 2. Configure credentials
# Copy config/.env.example to config/.env (if example exists)
# Edit config/.env with your broker credentials

# 3. Verify system health
cd C:\Rajat\trading_infrastructure
set PYTHONPATH=src
python integration_test_master_audit.py
# Must show 252/252 PASSED
```

### Mental Model -- The Three Layers

Understanding these three layers is the key to understanding the entire system.

```
LAYER 1 -- WHAT (Strategy)
    IronStraddleStrategy decides WHEN to enter, adjust, and exit.
    It emits TradeSignal objects. It never touches prices or orders directly.
    It is identical regardless of execution mode.

LAYER 2 -- HOW (Handler)
    BacktestExecutionHandler / PaperExecutionHandler / LiveExecutionHandler
    receives TradeSignal from the strategy, executes it, and returns LegFill objects.
    The strategy has no knowledge of which handler it is using.

LAYER 3 -- WHEN (Orchestrator)
    BacktestRunner / MarketSession / LiveSession
    drives the tick loop (pull or push), injects services into strategies,
    calls strategy hooks on each tick, manages RiskGuard and IndicatorEngine.
```

### Things That Will Confuse You Until You Know

| Thing | The Truth |
|-------|-----------|
| `leg_id` on `OptionsLeg` | Does not exist. `leg_id` is a database column only. Use symbol + price proximity to identify fills. |
| `record_pnl()` halts the guard | It does not. You must call `check_entry()` or `check_adjustment()` to trigger halt detection. |
| Two inject_services() calls per session | Normal. The second overwrites the first. Not a bug. |
| Expiry "2026-02-17" vs "17FEB2026" | Sprint system uses ISO. options_chain uses DDMMMYYYY. Convert with strptime/strftime. |
| Brokers auth on every test | Yes. BUG-01. Every test run authenticates all 3 brokers. Lazy init is Sprint 9. |
| Two trading systems in codebase | Legacy (webhook->flat files) and Sprint (MarketSession->SQLite). Only sprint system is canonical. |
| `BLOCK` vs `SQUARE_OFF` in RiskGuard | BLOCK is soft stop (max cycles). SQUARE_OFF is hard stop (loss limit). Different methods return different actions. |
| Rs.-932.75 | This number must reproduce exactly in every execution path. It is the health check of the entire system. |

### Golden Rules for New Development

1. Run `integration_test_master_audit.py` before touching anything. 252/252 must pass.
2. Declare the exact files your sprint will create. Write this down before coding.
3. Do not modify frozen modules. If you think you need to, raise it for review first.
4. Do not create utility files or helpers outside declared sprint scope.
5. Update this SSOT at the end of every sprint, before the next begins.
6. The benchmark is the final arbiter. Rs.-932.75 must reproduce. If it does not, the sprint is not done.
7. Assume any file marked FROZEN is correct and tested. Bugs are more likely in your new code.

---

*This document is the single source of truth for the RGPAS project.*
*It covers architecture, execution modes, all scripts, what works, what does not work,*
*known bugs, tech debt, sprint status, broker configuration, WebSocket tokens, API contracts,*
*and complete handover guidance for new developers.*

*Update this document at the end of every sprint, before the next one begins.*
*When in doubt about any API or design decision, read Section 21 first.*

---

## 23. Changelog

This log records every significant change to the system and this document.
Add an entry at the end of every sprint and every significant housekeeping session.
Format: date, author, type, description. Never delete old entries.

### Entry Format

```
Date        | Type         | Description
------------|--------------|--------------------------------------------------
YYYY-MM-DD  | Sprint / Fix / Cleanup / Docs / Bug  | What changed and why
```

Types:
- **Sprint** — new production module(s) delivered
- **Fix** — bug fixed in existing module
- **Cleanup** — dead code removed, structure improved
- **Docs** — SSOT or documentation updated only
- **Bug** — bug identified and logged (not yet fixed)
- **Test** — test suite added or corrected

---

### Log

| Date | Type | Entry |
|------|------|-------|
| Pre-2026-02-18 | Sprint | Sprint 1: `BacktestExecutionHandler` built. Rs.-932.75 benchmark established on 2026-02-11. |
| Pre-2026-02-18 | Sprint | Sprint 2: Core building blocks built — `OptionsLeg`, `TradeSignal`, `LegFill`, `MarketTick`, `PositionBook`, `GreeksCalculator`. |
| Pre-2026-02-18 | Sprint | Sprint 3: `IronStraddleStrategy` built. Full state machine NEUTRAL→ADJUSTED→FLIPPED→ALL_OUT→DONE. |
| Pre-2026-02-18 | Sprint | Sprint 4: `BacktestRunner` built. Single-strategy full-day backtest driver. Rs.-932.75 confirmed via runner. |
| Pre-2026-02-18 | Sprint | Sprint 5: `RiskGuard` built. Per-lot daily/trade loss limits, max adjustment cycles, BLOCK vs SQUARE_OFF. |
| Pre-2026-02-18 | Sprint | Sprint 6: `PortfolioCoordinator` built. Multi-strategy isolation — one halt does not stop others. |
| 2026-02-20 | Sprint | Sprint 7: `MarketSession` + `IndicatorEngine` built. Pull-based session orchestrator with IV/PCR/decay indicators. 76/76 tests passing. |
| 2026-02-21 | Sprint | Sprint 8A: `PaperExecutionHandler` + `TickReplayFeed` built. Accuracy gate passed — Rs.-932.75 reproduced through full paper path. 52/52 tests passing. |
| 2026-02-21 | Test | Master audit created: `integration_test_master_audit.py`. 252/252 tests passing across all sprints 1–8A. 5 execution paths verified simultaneously. |
| 2026-02-21 | Fix | Section 1 of master audit was crashing due to incorrect `OptionsLeg` constructor assumptions (`leg_id`, `OptionType`, `LegSide`). Fixed test patterns — production code was correct. |
| 2026-02-21 | Fix | Master audit Section 2 had stale default values (`sl_pct=0.60`, `reversion_buffer=75`). Corrected to actual code values `0.30` and `15`. SSOT updated to match. |
| 2026-02-21 | Fix | Master audit Section 4 had incorrect `RiskGuard` assumptions: `record_pnl()` does not auto-halt, and `check_entry()` returns `SQUARE_OFF` not `BLOCK`. Test logic corrected. |
| 2026-02-21 | Cleanup | Removed `src/strategies/base_strategy_backup_20260221_063934.py` — old backup file. |
| 2026-02-21 | Cleanup | Removed `src/broker_gateway/paper_trading/` folder (3 files) — legacy virtual broker superseded by `execution/paper_handler.py`. |
| 2026-02-21 | Cleanup | Removed duplicate singleton instantiations in 6 broker connectors (Angel, Upstox, Dhan, FlatTrade, Kotak, Zerodha — each had `broker = BrokerClass()` twice). |
| 2026-02-21 | Fix | Fixed `cfg.LOG_DIR` and `cfg.INSTRUMENT_MASTER` in `settings.py` — both had hardcoded `C:\Rajat\...` paths. Replaced with `PROJECT_ROOT`-relative paths. |
| 2026-02-21 | Docs | SSOT comprehensively rewritten — 640 lines expanded to cover architecture, all scripts, execution modes, broker tokens, WebSocket API, known bugs, tech debt, sprint history, API contracts, handover notes. |
| 2026-02-21 | Bug | BUG-01 identified and logged: brokers authenticate eagerly on import, burning TOTP tokens and adding 5-10s latency to every test run. Fix planned Sprint 9. |
| 2026-02-22 | Cleanup | Reviewed `simulation_lab/` undocumented files: `data_feed.py`, `simulation_engine.py`, `virtual_portfolio.py`. All confirmed superseded by sprint system. `data_feed.py` archived to `docs/data_feed_archived_20260222.py` before deletion. |
| 2026-02-22 | Cleanup | Confirmed zero active imports from `simulation_lab/legacy/` via `findstr`. Deleted `simulation_lab/legacy/` folder containing `__init__.py`, `iron_straddle_v2_prototype.py` (39KB), `st_pro_backtest.py` (9KB). |
| 2026-02-22 | Cleanup | Deleted `simulation_lab/data_feed.py`, `simulation_lab/simulation_engine.py`, `simulation_lab/virtual_portfolio.py` — all dead code confirmed by zero import references. |
| 2026-02-22 | Test | Master audit re-run after cleanup: **267/267 tests passing** (up from 252 — Section 1 Building Blocks now runs cleanly, contributing 15 previously-hidden tests). Confirmed clean baseline for Sprint 8B. |
| 2026-02-22 | Docs | SSOT updated: header test count corrected to 267/267, changelog section added (this section), document version updated to Pre-Sprint-8B Verified Baseline. |

---

### How to Add an Entry

At the end of every sprint or significant change, append a row to the table above:

```
| YYYY-MM-DD | Type | Brief description of what changed and why |
```

Keep entries concise — one or two sentences maximum. The goal is a scannable audit trail,
not a detailed narrative. For detailed reasoning, see the relevant SSOT section instead.

---
**Document version:** Pre-Sprint-8B Verified Baseline — 267/267 tests passing
**Last updated:** 2026-02-22
