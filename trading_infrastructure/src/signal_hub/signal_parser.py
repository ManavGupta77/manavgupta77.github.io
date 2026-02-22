"""
Validates and parses raw webhook JSON into a clean internal Signal object.
Signal schema: symbol, action (BUY/SELL/EXIT), strategy_name, price, timeframe, timestamp.
Rejects malformed payloads, unauthorised tokens, and unknown strategy names.
Returns a structured Signal dataclass — never raw JSON downstream.
"""
