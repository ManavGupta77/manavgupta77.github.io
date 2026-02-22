"""
Internal publish-subscribe event system.
Decouples all modules from each other.
Events: TICK, CANDLE, SIGNAL, EXTERNAL_SIGNAL, ORDER, FILL, RISK_BREACH.
signal_hub publishes EXTERNAL_SIGNAL events here.
Strategies subscribe and respond without knowing the signal origin.
Replace with cloud pubsub (AWS SNS, GCP Pub/Sub) when porting to cloud
without changing any subscriber code.
"""
