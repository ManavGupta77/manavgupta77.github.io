# ==============================================================================
# STRATEGIES / BASE_STRATEGY.PY
# ==============================================================================
# The skeleton every strategy inherits from.
#
# MIGRATION NOTE:
#   This replaces the original base_strategy.py.
#   Original on_start / on_update / on_stop hooks are preserved for backward
#   compatibility with any existing live-trading scripts.
#   Six new trading-session lifecycle hooks are added for the class-based
#   strategy architecture (used by IronStraddleStrategy and all future strategies).
#
# TWO HOOK SETS:
#
#   SET 1 — Session Lifecycle (new, used by IronStraddleStrategy):
#     on_market_open()     → 09:15 IST. Load instruments, initialise state.
#     on_entry_signal()    → Each tick in entry window. Return TradeSignal or None.
#     on_adjustment()      → Each tick while in position. Return TradeSignal or None.
#     on_exit_signal()     → Each tick while in position. Return TradeSignal or None.
#     on_market_close()    → 15:15 IST. Return square-off TradeSignal.
#     on_order_update()    → Called for each LegFill. Update PositionBook.
#
#   SET 2 — Run Loop (original, kept for live trading scripts):
#     on_start()           → One-time setup.
#     on_update()          → Called in polling loop.
#     on_stop()            → Cleanup on shutdown.
#
# DESIGN RULES:
#   - Strategies never import from broker_gateway directly.
#   - Strategies never query the database directly.
#   - Strategies never know which ExecutionMode they are running in.
#   - All parameters come from the YAML config loaded via load_config().
#   - Strategies return TradeSignal objects — they never place orders.
# ==============================================================================

import time
from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING
from utilities.logger import get_logger

if TYPE_CHECKING:
    # Imported for type hints only — no runtime dependency on building_blocks
    # from within base_strategy itself.
    from strategies.building_blocks import TradeSignal, LegFill


class BaseStrategy(ABC):
    """
    The Strategy Blueprint.

    Inherit from this class to build any strategy.
    Implement the session lifecycle hooks (on_market_open through on_order_update)
    for the new class-based architecture.

    The original on_start / on_update / on_stop hooks remain available for
    scripts that use the polling run() loop for live trading.
    """

    def __init__(self, name: str, instrument: str, config_path: str = ""):
        """
        Args:
            name        : Human-readable strategy name (e.g. 'Iron Straddle').
            instrument  : Primary instrument (e.g. 'NIFTY'). Multi-instrument
                          strategies override get_instruments() to return more.
            config_path : Path to the strategy YAML config file.
                          If provided, load_config() is called automatically.
        """
        self.name       = name
        self.instrument = instrument

        # Logger — name normalised to snake_case for log file naming
        self.logger = get_logger(name.lower().replace(" ", "_"))

        # ── Infrastructure (injected by PortfolioCoordinator) ────────────────
        # These are NOT set in __init__. The coordinator injects them after
        # instantiation via inject_services(). Strategies never import these
        # directly — this is the key decoupling from the original design.
        self._execution_handler = None   # ExecutionHandler (Backtest/Paper/Live)
        self._position_book     = None   # PositionBook (injected or self-managed)
        self._risk_guard        = None   # RiskGuard reference

        # ── Backward-Compatible Services (for on_start/on_update scripts) ───
        # These are kept for any existing scripts that use the polling loop.
        # New strategies using session hooks should use injected services above.
        try:
            from broker_gateway.connection_manager import handler
            from market_feeds.live_feeds.market_data_service import market
            from trading_records.db_connector import db
            self.broker = handler
            self.market = market
            self.db     = db
        except ImportError:
            # Safe fallback — backtest mode may not have live feed modules
            self.broker = None
            self.market = None
            self.db     = None

        # ── Operational State ─────────────────────────────────────────────────
        self.is_running  = False
        self.in_position = False

        # ── Config ───────────────────────────────────────────────────────────
        self.config      = {}          # Populated by load_config()
        self.config_path = config_path
        if config_path:
            self.load_config(config_path)

        # ── Performance Tracking (simple, backward-compatible) ───────────────
        self.entry_price = 0.0
        self.exit_price  = 0.0
        self.total_pnl   = 0.0

    # ==========================================================================
    # SESSION LIFECYCLE HOOKS  (implement these in your strategy)
    # ==========================================================================

    def on_market_open(self, session_date: str, spot_price: float) -> None:
        """
        Called once at market open (09:15 IST) before the first tick.

        Use this to:
          - Resolve instrument symbols and option keys for the day
          - Set up internal state (ATM strike, hedge strikes, etc.)
          - Validate that required data is available before proceeding

        Args:
            session_date : Trading date as 'YYYY-MM-DD' string.
            spot_price   : Opening spot price of the index.
        """
        pass

    def on_entry_signal(self, timestamp: str, spot: float,
                        prices: dict) -> Optional["TradeSignal"]:
        """
        Called on every tick during the entry window.

        Return a TradeSignal to open a position, or None to wait.
        Once a position is opened, the coordinator stops calling this hook
        until the strategy signals it is ready for re-entry.

        Args:
            timestamp : Current tick timestamp as IST string 'HH:MM'.
            spot      : Current spot price.
            prices    : Dict of {symbol: current_price} for all tracked legs.

        Returns:
            TradeSignal with signal_type=ENTRY, or None.
        """
        return None

    def on_adjustment(self, timestamp: str, spot: float,
                      prices: dict) -> Optional["TradeSignal"]:
        """
        Called on every tick while a position is open.

        Check if any adjustment conditions are met (SL hit, delta breach,
        reversion, flip-back). Return a TradeSignal to act, or None.

        Args:
            timestamp : Current tick timestamp as IST string 'HH:MM'.
            spot      : Current spot price.
            prices    : Dict of {symbol: current_price} for all tracked legs.

        Returns:
            TradeSignal with signal_type=ADJUSTMENT or EXIT, or None.
        """
        return None

    def on_exit_signal(self, timestamp: str, spot: float,
                       prices: dict) -> Optional["TradeSignal"]:
        """
        Called on every tick. Check target/time-based exit conditions.

        This hook is for clean exits (target reached, time exit).
        SL-based exits should be handled in on_adjustment() since they
        often involve replacing legs, not just closing them.

        Args:
            timestamp : Current tick timestamp as IST string 'HH:MM'.
            spot      : Current spot price.
            prices    : Dict of {symbol: current_price} for all tracked legs.

        Returns:
            TradeSignal with signal_type=EXIT or SQUARE_OFF, or None.
        """
        return None

    def on_market_close(self, timestamp: str, spot: float,
                        prices: dict) -> Optional["TradeSignal"]:
        """
        Called once at the configured square-off time (e.g. 15:20 IST).

        Must return a SQUARE_OFF signal for all open legs.
        If no legs are open, return None.

        Args:
            timestamp : Square-off timestamp as IST string 'HH:MM'.
            spot      : Final spot price.
            prices    : Dict of {symbol: current_price} for all tracked legs.

        Returns:
            TradeSignal with signal_type=SQUARE_OFF, or None if flat.
        """
        return None

    def on_order_update(self, fill: "LegFill") -> None:
        """
        Called by the coordinator when an order fill is confirmed.

        Update internal state (PositionBook, strategy state machine) here.
        This is the only place the strategy learns about actual fill prices.

        Args:
            fill : LegFill object containing fill price, qty, time, and mode.
        """
        pass

    # ==========================================================================
    # ORIGINAL POLLING LOOP HOOKS  (preserved for backward compatibility)
    # ==========================================================================

    def on_start(self) -> None:
        """Setup logic: Runs once when the strategy is launched via run()."""
        pass

    def on_update(self) -> None:
        """Execution logic: Runs in a loop via run(). Poll-based live trading."""
        pass

    def on_stop(self) -> None:
        """Cleanup logic: Runs when the strategy is terminated via stop()."""
        pass

    # ==========================================================================
    # CONFIGURATION
    # ==========================================================================

    def load_config(self, yaml_path: str) -> None:
        """
        Load strategy parameters from a YAML config file.

        Called automatically in __init__ if config_path is provided.
        Can be called again to reload config without restarting.

        Args:
            yaml_path : Path to the strategy YAML file.
                        e.g. 'src/strategies/configs/iron_straddle.yaml'
        """
        try:
            import yaml
            with open(yaml_path, 'r') as f:
                self.config = yaml.safe_load(f)
            self.config_path = yaml_path
            self.logger.info("Config loaded", path=yaml_path)
        except FileNotFoundError:
            self.logger.warning("Config file not found — using defaults", path=yaml_path)
        except Exception as e:
            self.logger.error("Config load failed", path=yaml_path, error=str(e))

    def get_instruments(self) -> list:
        """
        Returns the list of instruments this strategy trades.

        Override in multi-instrument strategies to return additional instruments.
        Default returns the single instrument passed to __init__.

        Returns:
            List of instrument name strings (e.g. ['NIFTY', 'BANKNIFTY']).
        """
        return [self.instrument]

    # ==========================================================================
    # SERVICE INJECTION  (called by PortfolioCoordinator, not by strategies)
    # ==========================================================================

    def inject_services(self, execution_handler, position_book, risk_guard) -> None:
        """
        Called by PortfolioCoordinator to inject infrastructure dependencies.

        Strategies should never call this themselves. The coordinator handles
        injection after instantiation, before the first tick is sent.

        Args:
            execution_handler : BacktestExecutionHandler / PaperExecutionHandler
                                / LiveExecutionHandler instance.
            position_book     : PositionBook instance for this strategy.
            risk_guard        : Shared RiskGuard instance.
        """
        self._execution_handler = execution_handler
        self._position_book     = position_book
        self._risk_guard        = risk_guard
        self.logger.info("Services injected",
                         mode=type(execution_handler).__name__ if execution_handler else "None")

    # ==========================================================================
    # ORIGINAL POLLING LOOP  (preserved for backward compatibility)
    # ==========================================================================

    def run(self, poll_interval: float = 1.0) -> None:
        """
        Starts the main polling loop. Used by live trading scripts.

        Calls on_start() once, then on_update() every poll_interval seconds.
        New class-based strategies use PortfolioCoordinator.run_*() instead.
        """
        self.logger.info("Strategy Starting", name=self.name, instrument=self.instrument)
        self.on_start()
        self.is_running = True

        try:
            while self.is_running:
                self.on_update()
                time.sleep(poll_interval)
        except KeyboardInterrupt:
            self.logger.warning("Strategy manually interrupted")
        except Exception as e:
            self.logger.error("Critical Strategy Error", error=str(e))
        finally:
            self.stop()

    def stop(self) -> None:
        """Gracefully stops the polling loop and triggers on_stop() cleanup."""
        if not self.is_running:
            return
        self.logger.info("Strategy Stopping", name=self.name)
        self.on_stop()
        self.is_running = False

    # ==========================================================================
    # SHARED UTILITIES  (available to all strategies)
    # ==========================================================================

    def log_trade(self, side: str, symbol: str, qty: int,
                  price: float, remarks: str = "") -> None:
        """
        Records a trade in the local SQLite database.

        Available for use in on_order_update() if direct DB logging is needed
        alongside the main PositionBook recording.
        """
        try:
            if self.db:
                self.db.log_order(
                    strategy_name=self.name,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    price=price,
                    remarks=remarks,
                )
                self.logger.info("Order logged to DB", symbol=symbol, side=side)
        except Exception as e:
            self.logger.error("DB logging failed", error=str(e))

    def calculate_pnl(self, buy_price: float, sell_price: float,
                      qty: int) -> float:
        """
        Utility for standardised PnL arithmetic.

        PnL = (Sell - Buy) * Quantity
        Preserved from original base_strategy.py.
        """
        return round((sell_price - buy_price) * qty, 2)

    def __repr__(self) -> str:
        return f"Strategy({self.name} | {self.instrument} | running={self.is_running})"
