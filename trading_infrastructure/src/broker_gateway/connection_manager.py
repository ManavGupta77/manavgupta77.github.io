# ==========================================
# CORE/HANDLER.PY
# ==========================================
import importlib
from typing import Optional, List, Dict, Any
from config_loader.settings import cfg
from utilities.logger import get_logger
from broker_gateway import BROKER_REGISTRY
from broker_gateway.base_broker import BrokerBase

# Standard Platform Logger
logger = get_logger("handler")


class BrokerHandler:
    """
    Traffic Controller: Routes requests through the broker failover chain.

    Behavior:
    - Reads BROKER_PRIORITY from config (e.g., "SHOONYA,ANGEL,UPSTOX")
    - Dynamically imports each broker from BROKER_REGISTRY
    - Loads ALL listed brokers at init (doesn't fail if some can't connect)
    - Routes API calls through the chain, skipping disconnected brokers
    - Supports automatic reconnection before giving up on a broker
    """

    def __init__(self):
        self.brokers: Dict[str, BrokerBase] = {}    # name → instance (ordered)
        self.priority_order: List[str] = []          # ordered broker names
        self._load_brokers()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _load_brokers(self):
        """Load all brokers listed in BROKER_PRIORITY from the registry."""
        for name in cfg.broker_priority_list:
            module_path = BROKER_REGISTRY.get(name)
            if not module_path:
                logger.warning(f"Handler: Unknown broker '{name}' in BROKER_PRIORITY, skipping")
                continue
            try:
                module = importlib.import_module(module_path)
                broker_instance = getattr(module, 'broker')
                self.brokers[name] = broker_instance
                status = "CONNECTED" if broker_instance.is_connected() else "LOADED (not connected)"
                logger.info(f"Handler: {name} -> {status}")
            except Exception as e:
                logger.error(f"Handler: Failed to load {name}: {e}")

        # Build priority order from successfully loaded brokers
        self.priority_order = [n for n in cfg.broker_priority_list if n in self.brokers]
        logger.info(f"Handler: Failover chain -> {' -> '.join(self.priority_order) or 'EMPTY'}")

        # Report connection status
        connected = self.get_connected_brokers()
        if not connected:
            logger.warning("Handler: NO BROKERS CONNECTED — data and order calls will fail")
        else:
            logger.info(f"Handler: Active brokers -> {', '.join(connected)}")

    # ------------------------------------------------------------------
    # Health & Status
    # ------------------------------------------------------------------

    def health_check(self) -> Dict[str, bool]:
        """Status of all loaded brokers. Returns {name: is_connected}."""
        return {name: b.is_connected() for name, b in self.brokers.items()}

    def is_any_connected(self) -> bool:
        """At least one broker in the chain is alive."""
        return any(b.is_connected() for b in self.brokers.values())

    def get_connected_brokers(self) -> List[str]:
        """Names of currently connected brokers, in priority order."""
        return [n for n in self.priority_order if self.brokers[n].is_connected()]

    # ------------------------------------------------------------------
    # Internal: Failover Router
    # ------------------------------------------------------------------

    def _try_with_failover(self, method_name: str, *args, **kwargs):
        """
        Generic failover router. Tries each broker in priority order.
        If a broker is disconnected, attempts reconnect ONCE before skipping.

        Returns:
            First non-None result from a broker, or None if all fail.
        """
        for name in self.priority_order:
            broker = self.brokers[name]

            # Skip if disconnected and reconnect fails
            if not broker.is_connected():
                logger.info(f"Handler: {name} disconnected, attempting reconnect")
                if not broker.reconnect():
                    logger.warning(f"Handler: {name} reconnect failed, skipping")
                    continue
                logger.info(f"Handler: {name} reconnected successfully")

            # Try the actual method call
            try:
                method = getattr(broker, method_name)
                result = method(*args, **kwargs)
                if result is not None:
                    return result
                logger.warning(f"Handler: {name}.{method_name} returned None")
            except NotImplementedError:
                logger.debug(f"Handler: {name}.{method_name} not implemented, skipping")
                continue
            except Exception as e:
                logger.warning(f"Handler: {name}.{method_name} failed: {e}")
                continue

        logger.error(f"Handler: ALL BROKERS FAILED for {method_name}")
        return None

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_ltp(self, exchange: str, symbol: str, token_dict=None) -> Optional[float]:
        """
        Get Last Traded Price with automatic failover.

        Args:
            exchange:   Exchange code ('NSE', 'NFO', 'BFO')
            symbol:     Trading symbol ('Nifty 50', 'NIFTY11FEB2625800CE')
            token_dict: Broker-specific tokens. Can be:
                        - None:  each broker uses symbol for lookup
                        - str:   treated as generic token (backward compat)
                        - dict:  {'ANGEL': '99926000', 'SHOONYA': '26000', ...}
        Returns:
            LTP as float, or None if all brokers fail.
        """
        # Normalize token_dict
        if isinstance(token_dict, str):
            token_dict = {name: token_dict for name in self.priority_order}

        for name in self.priority_order:
            broker = self.brokers[name]

            # Check connection, attempt reconnect if needed
            if not broker.is_connected():
                if not broker.reconnect():
                    continue

            try:
                # Resolve broker-specific token
                token = (token_dict or {}).get(name, symbol) if token_dict else symbol
                ltp = broker.get_ltp(exchange, symbol, token)
                if ltp is not None:
                    return ltp
            except NotImplementedError:
                continue
            except Exception as e:
                logger.warning(f"Handler: {name} LTP failed for {symbol}: {e}")
                continue

        logger.error(f"Handler: ALL BROKERS FAILED for LTP {symbol}")
        return None

    def search_instruments(self, exchange: str, search_text: str) -> List[Dict[str, Any]]:
        """Search instruments with failover. Returns first successful result."""
        result = self._try_with_failover("search_instruments", exchange, search_text)
        return result if result else []

    # ------------------------------------------------------------------
    # Order Management (Stage 10+)
    # ------------------------------------------------------------------

    def place_order(self, **kwargs) -> Optional[str]:
        """Place order with failover. Returns order ID or None."""
        return self._try_with_failover("place_order", **kwargs)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel order — tries each connected broker until success."""
        for name in self.priority_order:
            broker = self.brokers[name]
            if not broker.is_connected():
                continue
            try:
                return broker.cancel_order(order_id)
            except NotImplementedError:
                continue
            except Exception as e:
                logger.warning(f"Handler: {name} cancel_order failed: {e}")
                continue
        return False

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get positions from first connected broker that supports it."""
        for name in self.priority_order:
            broker = self.brokers[name]
            if not broker.is_connected():
                continue
            try:
                return broker.get_positions()
            except NotImplementedError:
                continue
            except Exception as e:
                logger.warning(f"Handler: {name} get_positions failed: {e}")
                continue
        return []


# Singleton Instance
handler = BrokerHandler()
