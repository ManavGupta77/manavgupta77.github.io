"""
Hard pre-trade checks before any order reaches the broker.
Enforces max daily drawdown, margin availability, and position exposure caps.
Applies to both internally generated orders and external signal-triggered orders.
Acts as the last gate before execution.
"""
