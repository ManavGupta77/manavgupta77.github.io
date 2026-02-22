# ==========================================
# BROKER_GATEWAY/__INIT__.PY
# ==========================================
"""
Broker Registry — maps canonical broker names to their module paths.
BrokerHandler uses this to dynamically import brokers listed in BROKER_PRIORITY.

To add a new broker:
  1. Create src/broker_gateway/broker_new/connector.py (inheriting BrokerBase)
  2. Export singleton as `broker = NewBrokerClass()`
  3. Add entry here: "NEWBROKER": "broker_gateway.broker_new.connector"
  4. Add credentials to .env and config_loader/settings.py
  5. Add to BROKER_PRIORITY in .env
"""

BROKER_REGISTRY = {
    "SHOONYA":    "broker_gateway.broker_shoonya.connector",
    "ANGEL":      "broker_gateway.broker_angel.connector",
    "UPSTOX":     "broker_gateway.broker_upstox.connector",
    "KOTAK":      "broker_gateway.broker_kotak.connector",
    "ZERODHA":    "broker_gateway.broker_zerodha.connector",
    "DHAN":       "broker_gateway.broker_dhan.connector",
    "FLATTRADE":  "broker_gateway.broker_flattrade.connector",
}
