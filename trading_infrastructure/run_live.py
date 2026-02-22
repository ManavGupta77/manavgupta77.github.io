"""
Entry point for live and paper trading engine.
Reads system_rules.yaml to determine mode (live vs paper).
Initialises scheduler, broker gateway, market feeds,
signal_hub webhook server, and strategy engine.
"""
