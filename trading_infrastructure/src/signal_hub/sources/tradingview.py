"""
TradingView-specific signal source implementation.
Validates the secret token embedded in the alert payload.
Maps TradingView alert message format to the internal Signal schema.

TradingView alert message JSON format expected:
{
  "token": "your_secret_token",
  "strategy": "straddle",
  "symbol": "NIFTY",
  "action": "BUY",
  "price": "{{close}}",
  "timeframe": "15"
}
"""
