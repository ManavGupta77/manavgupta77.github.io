# ==========================================
# CORE/ORDER_ENGINE.PY
# ==========================================
# Purpose:
#   Order execution engine. Single entry point for all order placement.
#   Handles both PAPER and LIVE modes through the same code path.
#   Every order is logged to the database regardless of mode.
#
# Usage:
#   from trade_desk.order_management.order_manager import order_engine
#
#   result = order_engine.place_order(
#       strategy_id="STRAT_001",
#       session_id=1,
#       position_id="POS_0001",
#       symbol="NIFTY17FEB2625400CE",
#       token="48094",
#       exchange="NFO",
#       side="SELL",
#       quantity=75,
#       order_type="MARKET",
#   )
#
# Paper Mode:
#   Fetches real LTP from broker, uses it as fill price.
#   No order hits the exchange. Logged as mode=PAPER.
#
# Live Mode:
#   Places real order via broker.place_order().
#   Waits for fill confirmation. Logged as mode=LIVE.
#
# Cloud Migration:
#   No changes needed. Broker abstraction handles the rest.
# ==========================================

from config_loader.settings import cfg
from broker_gateway.connection_manager import handler
from trading_records.db_connector import db
from utilities.logger import get_logger

logger = get_logger("order_engine")


class OrderEngine:
    """
    Order execution engine.
    Paper and live use the same interface.
    Every order is recorded in the database.
    """

    def __init__(self):
        self._db = db

    # ------------------------------------------
    # MAIN ORDER METHOD
    # ------------------------------------------

    def place_order(self, strategy_id, session_id, position_id,
                    symbol, token, exchange, side, quantity,
                    order_type="MARKET", price=None, trigger_price=None,
                    leg_id=None, mode=None):
        """
        Place an order (paper or live) and log to database.

        Args:
            strategy_id (str): Which strategy owns this order
            session_id (int): Current trading session
            position_id (str): Position this order belongs to
            symbol (str): Trading symbol (e.g., "NIFTY17FEB2625400CE")
            token (str): Broker instrument token
            exchange (str): "NFO" / "BFO"
            side (str): "BUY" / "SELL"
            quantity (int): Total quantity (lots × lot_size)
            order_type (str): "MARKET" / "LIMIT" / "SL" / "SLM"
            price (float): For LIMIT/SL orders
            trigger_price (float): For SL/SLM orders
            leg_id (str): If already created
            mode (str): Override mode. Default: from config.

        Returns:
            dict: {
                "order_id": str,
                "status": "FILLED" / "REJECTED" / "PENDING",
                "fill_price": float or None,
                "broker_order_id": str or None,
                "error_message": str or None,
            }
        """
        mode = mode or cfg.TRADING_MODE

        # Generate order ID
        order_id = self._db.generate_id("ORD", "orders", "order_id")

        logger.info("Order initiated",
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    qty=quantity,
                    mode=mode,
                    order_type=order_type)

        # Route to paper or live
        if mode == "PAPER":
            result = self._execute_paper(
                symbol, token, exchange, side, quantity,
                order_type, price
            )
        elif mode == "LIVE":
            result = self._execute_live(
                symbol, token, exchange, side, quantity,
                order_type, price, trigger_price
            )
        else:
            result = {
                "status": "REJECTED",
                "fill_price": None,
                "broker_order_id": None,
                "error_message": f"Unknown mode: {mode}",
            }

        # Log order to database
        order_data = {
            "order_id":        order_id,
            "leg_id":          leg_id,
            "position_id":     position_id,
            "strategy_id":     strategy_id,
            "session_id":      session_id,
            "order_type":      order_type,
            "symbol":          symbol,
            "token":           token,
            "exchange":        exchange,
            "side":            side,
            "quantity":        quantity,
            "requested_price": price,
            "fill_price":      result["fill_price"],
            "status":          result["status"],
            "broker_order_id": result["broker_order_id"],
            "mode":            mode,
            "error_message":   result["error_message"],
        }
        self._db.insert_order(order_data)

        # Add order_id to result
        result["order_id"] = order_id

        logger.info("Order completed",
                    order_id=order_id,
                    status=result["status"],
                    fill_price=result["fill_price"],
                    mode=mode)

        return result

    # ------------------------------------------
    # PAPER EXECUTION
    # ------------------------------------------

    def _execute_paper(self, symbol, token, exchange, side, quantity,
                       order_type, price):
        """
        Paper mode: fetch real LTP, use as fill price.
        No order hits the exchange.
        """
        try:
            # For market orders, fetch live price
            if order_type == "MARKET":
                fill_price = handler.get_ltp(exchange, symbol, token)

                if fill_price is None or fill_price <= 0:
                    return {
                        "status": "REJECTED",
                        "fill_price": None,
                        "broker_order_id": None,
                        "error_message": "Could not fetch LTP for paper order",
                    }
            else:
                # For limit/SL orders in paper mode, use requested price
                fill_price = price or 0.0

            logger.info("Paper order filled",
                        symbol=symbol,
                        side=side,
                        fill_price=fill_price)

            return {
                "status": "FILLED",
                "fill_price": fill_price,
                "broker_order_id": None,  # No broker order in paper mode
                "error_message": None,
            }

        except Exception as e:
            logger.error("Paper order failed",
                         symbol=symbol, error=str(e))
            return {
                "status": "REJECTED",
                "fill_price": None,
                "broker_order_id": None,
                "error_message": str(e),
            }

    # ------------------------------------------
    # LIVE EXECUTION
    # ------------------------------------------

    def _execute_live(self, symbol, token, exchange, side, quantity,
                      order_type, price, trigger_price):
        """
        Live mode: place real order via broker.
        """
        try:
            broker_result = broker.place_order({
                "symbol":        symbol,
                "token":         token,
                "exchange":      exchange,
                "side":          side,
                "quantity":      quantity,
                "order_type":    order_type,
                "price":         price,
                "trigger_price": trigger_price,
            })

            broker_order_id = broker_result.get("broker_order_id")

            if broker_result["status"] == "FAILED":
                return {
                    "status": "REJECTED",
                    "fill_price": None,
                    "broker_order_id": broker_order_id,
                    "error_message": broker_result.get("message", "Order failed"),
                }

            # Check fill status
            fill_price = None
            if broker_order_id:
                order_status = broker.get_order_status(broker_order_id)
                if order_status:
                    fill_price = order_status.get("fill_price")
                    status = order_status.get("status", "PENDING")

                    # Map broker status to our status
                    status_map = {
                        "complete":  "FILLED",
                        "rejected":  "REJECTED",
                        "cancelled": "CANCELLED",
                    }
                    mapped = status_map.get(status.lower(), "PENDING")

                    return {
                        "status": mapped,
                        "fill_price": fill_price,
                        "broker_order_id": broker_order_id,
                        "error_message": None,
                    }

            # Order placed but status not yet available
            return {
                "status": "PENDING",
                "fill_price": fill_price,
                "broker_order_id": broker_order_id,
                "error_message": None,
            }

        except Exception as e:
            logger.error("Live order failed",
                         symbol=symbol, error=str(e))
            return {
                "status": "REJECTED",
                "fill_price": None,
                "broker_order_id": None,
                "error_message": str(e),
            }

    # ------------------------------------------
    # CONVENIENCE METHODS
    # ------------------------------------------

    def sell(self, strategy_id, session_id, position_id,
             symbol, token, exchange, quantity, leg_id=None, mode=None):
        """Shorthand for SELL MARKET order."""
        return self.place_order(
            strategy_id=strategy_id,
            session_id=session_id,
            position_id=position_id,
            symbol=symbol,
            token=token,
            exchange=exchange,
            side="SELL",
            quantity=quantity,
            order_type="MARKET",
            leg_id=leg_id,
            mode=mode,
        )

    def buy(self, strategy_id, session_id, position_id,
            symbol, token, exchange, quantity, leg_id=None, mode=None):
        """Shorthand for BUY MARKET order."""
        return self.place_order(
            strategy_id=strategy_id,
            session_id=session_id,
            position_id=position_id,
            symbol=symbol,
            token=token,
            exchange=exchange,
            side="BUY",
            quantity=quantity,
            order_type="MARKET",
            leg_id=leg_id,
            mode=mode,
        )


# ==========================================
# SINGLETON INSTANCE
# ==========================================
# Import this everywhere:  from trade_desk.order_management.order_manager import order_engine
order_engine = OrderEngine()


# ==========================================
# STANDALONE TEST
# ==========================================
if __name__ == "__main__":
    print("=" * 60)
    print("  ORDER ENGINE TEST (Paper Mode)")
    print("=" * 60)

    # Setup: connect DB
    db.connect()
    db.create_tables()

    # Register a test strategy
    from trading_records.db_connector import db as _db
    from instruments.derivatives.options_chain import option_chain
    from market_feeds.live_feeds.market_data_service import market

    test_config = {
        "strategy_id":   "STRAT_TEST",
        "name":          "Order Engine Test",
        "version":       "1.0",
        "strategy_type": "INTRADAY",
        "direction":     "NEUTRAL",
        "instrument":    "NIFTY",
        "expiry_type":   "WEEKLY",
        "structure":     "STRADDLE",
        "entry_triggers": ["MANUAL"],
        "params": {},
    }
    _db.insert_strategy(test_config)
    session_id = _db.create_session("STRAT_TEST")
    position_id = None

    print(f"\n   Strategy: STRAT_TEST")
    print(f"   Session:  {session_id}")
    print(f"   Mode:     {cfg.TRADING_MODE}")

    # Resolve LIVE contracts instead of hardcoded expired ones
    print(f"\n--- Resolving Live Contracts ---")
    spot = market.get_spot_price("NIFTY")
    print(f"   Nifty Spot: {spot}")

    if spot <= 0:
        print("   Cannot fetch spot. Market may be closed.")
        exit(1)

    option_chain.load_master()
    expiry = option_chain.get_next_expiry("NIFTY")
    atm = option_chain.get_atm_contracts("NIFTY", expiry, spot)

    if not atm:
        print(f"   No ATM contracts for {expiry}")
        exit(1)

    ce = atm["CE"]
    pe = atm["PE"]
    print(f"   Expiry: {expiry}")
    print(f"   ATM:    {atm['atm_strike']}")
    print(f"   CE:     {ce['symbol']} | Token: {ce['token']}")
    print(f"   PE:     {pe['symbol']} | Token: {pe['token']}")

    # Test 1: Paper SELL CE
    print(f"\n--- Test: Paper SELL CE ---")
    result = order_engine.sell(
        strategy_id="STRAT_TEST",
        session_id=session_id,
        position_id=position_id,
        symbol=ce["symbol"],
        token=ce["token"],
        exchange=ce["exchange"],
        quantity=65,
    )
    print(f"   Order ID:   {result['order_id']}")
    print(f"   Status:     {result['status']}")
    print(f"   Fill Price: {result['fill_price']}")

    # Test 2: Paper SELL PE
    print(f"\n--- Test: Paper SELL PE ---")
    result2 = order_engine.sell(
        strategy_id="STRAT_TEST",
        session_id=session_id,
        position_id=position_id,
        symbol=pe["symbol"],
        token=pe["token"],
        exchange=pe["exchange"],
        quantity=65,
    )
    print(f"   Order ID:   {result2['order_id']}")
    print(f"   Status:     {result2['status']}")
    print(f"   Fill Price: {result2['fill_price']}")

    # Test 3: Verify in DB
    print(f"\n--- Test: Verify Orders in DB ---")
    orders = _db.get_orders_for_session(session_id)
    print(f"   Orders found: {len(orders)}")
    for o in orders:
        print(f"   {o['order_id']} | {o['side']} {o['symbol']} | "
              f"Fill: {o['fill_price']} | Status: {o['status']}")

    db.close()
    print(f"\n   Order engine test complete")