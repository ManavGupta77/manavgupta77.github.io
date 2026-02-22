"""
Routes a validated Signal object to the correct strategy instance.
Reads strategy_name from the signal and publishes to the event bus.
signal_hub never imports strategies directly — all routing via event_bus.
Logs every routed signal to storage/logs/signals/ for audit.
"""
