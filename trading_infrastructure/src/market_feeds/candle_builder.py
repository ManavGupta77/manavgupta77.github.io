"""
Aggregates raw ticks into OHLCV candles at any timeframe.
Supports 1min, 5min, 15min, 1hr intervals.
Publishes completed candle events to the event bus.
"""
