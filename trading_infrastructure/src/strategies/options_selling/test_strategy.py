import pandas as pd
from strategies.base_strategy import BaseStrategy
from utilities.logger import get_logger

logger = get_logger("strategy")

class TestStrategy(BaseStrategy):
    """
    Simple Moving Average Crossover Strategy for Backtesting.
    """
    def __init__(self, config=None, *args, **kwargs):
        # 1. Setup Defaults
        config = config or {}
        name = config.get("name", "Backtest_Strategy")
        instrument = config.get("instrument", "NIFTY_INDEX")
        
        # 2. Initialize Parent (CRITICAL FIX: Pass required args)
        # We pass name and instrument because BaseStrategy requires them
        super().__init__(name, instrument)
        
        # 3. Handle Config locally
        self.config = config
        self.strategy_id = self.config.get("strategy_id", "TEST_STRAT")
        
        # 4. Strategy State
        self.fast_window = 10
        self.slow_window = 30
        self.prices = []  # To store history for MA calculation
        self.position = None # Track if we are LONG/SHORT/NONE

    def initialize(self):
        # This is called by BaseStrategy usually
        logger.info(f"{self.name} initialized for {self.instrument}.")

    # --- REQUIRED ABSTRACT METHODS ---
    def on_start(self):
        """Called when strategy starts."""
        pass

    def on_stop(self):
        """Called when strategy stops."""
        pass

    def on_update(self, data):
        """Called by live system updates."""
        pass
    # ---------------------------------

    def on_tick(self, tick_data):
        """
        Called by the Backtest Engine for every candle.
        tick_data: {'timestamp': ..., 'close': 21500, ...}
        """
        current_price = tick_data['close']
        symbol = tick_data['symbol']

        # 1. Store price history
        self.prices.append(current_price)
        
        # 2. Need enough data?
        if len(self.prices) < self.slow_window:
            return

        # 3. Calculate MA
        series = pd.Series(self.prices)
        fast_ma = series.tail(self.fast_window).mean()
        slow_ma = series.tail(self.slow_window).mean()
        
        # 4. Trading Logic
        
        # ENTRY SIGNAL: Fast MA crosses ABOVE Slow MA
        if self.position is None:
            if fast_ma > slow_ma:
                # Log without Emoji to be safe on Windows
                logger.info(f"[BUY] Signal at {current_price} (Fast: {fast_ma:.2f}, Slow: {slow_ma:.2f})")
                self.buy(symbol, 1, current_price)
                self.position = "LONG"
        
        # EXIT SIGNAL: Fast MA crosses BELOW Slow MA
        elif self.position == "LONG":
            if fast_ma < slow_ma:
                logger.info(f"[SELL] Signal at {current_price} (Fast: {fast_ma:.2f}, Slow: {slow_ma:.2f})")
                self.sell(symbol, 1, current_price)
                self.position = None

    # --- MOCK INTERFACE (Overridden by Runner) ---
    def buy(self, symbol, qty, price):
        pass

    def sell(self, symbol, qty, price):
        pass