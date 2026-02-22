# ==========================================
# CORE/BROKERS/BROKER_BASE.PY (v2)
# ==========================================

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BrokerBase(ABC):
    """
    Abstract base class for all broker implementations (v2).

    Required methods (must implement):
        is_connected       — Lightweight session health check
        reconnect          — Re-authenticate if session expired
        get_ltp            — Last traded price
        search_instruments — Find instruments by text

    Optional methods (override when ready):
        place_order, cancel_order, get_positions, get_order_status
        → Default: raise NotImplementedError with broker name

    Properties (must implement):
        broker_name — Canonical name matching BROKER_PRIORITY key
                      (e.g., 'SHOONYA', 'ANGEL', 'UPSTOX', 'KOTAK')
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def broker_name(self) -> str:
        """Canonical name: 'SHOONYA', 'ANGEL', 'UPSTOX', 'KOTAK', etc.
        Must match the key used in BROKER_PRIORITY and BROKER_REGISTRY."""
        pass

    # ------------------------------------------------------------------
    # Connection Management
    # ------------------------------------------------------------------

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the broker session is valid and ready for API calls.
        Should be lightweight — use a cached flag, not a full API call."""
        pass

    @abstractmethod
    def reconnect(self) -> bool:
        """Re-authenticate and restore the session.

        Returns:
            True if reconnection succeeded, False otherwise.

        Called by BrokerHandler when is_connected() returns False.
        Implementation should reset state and re-run the login flow.
        """
        pass

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    @abstractmethod
    def get_ltp(self, exchange: str, symbol: str, token: Optional[str] = None) -> Optional[float]:
        """Fetch the last traded price.

        Args:
            exchange: Exchange code (e.g., 'NSE', 'NFO', 'BFO').
            symbol:   Trading symbol (e.g., 'Nifty 50', 'NIFTY11FEB2625800CE').
            token:    Broker-specific instrument token/key (optional for some brokers).

        Returns:
            LTP as float, or None on failure.
        """
        pass

    @abstractmethod
    def search_instruments(self, exchange: str, search_text: str) -> List[Dict[str, Any]]:
        """Search for instruments matching the given text.

        Args:
            exchange:    Exchange to search in (e.g., 'NSE', 'NFO').
            search_text: Text to search for (e.g., 'NIFTY').

        Returns:
            List of dicts, each with at minimum:
                'token' (str):          Broker-specific instrument token
                'tradingsymbol' (str):  Standard NSE-style trading symbol
            Additional broker-specific fields may be present.
        """
        pass

    # ------------------------------------------------------------------
    # Order Management (Stage 10+ — override when ready)
    # ------------------------------------------------------------------

    def place_order(self, trading_symbol: str, transaction_type: str, quantity: int,
                    price: float = 0, product_type: str = 'M',
                    order_type: str = 'LMT', exchange: str = 'NFO') -> Optional[str]:
        """Place an order. Returns order ID string or None on failure."""
        raise NotImplementedError(f"{self.broker_name}: place_order not implemented")

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order. Returns True if cancellation succeeded."""
        raise NotImplementedError(f"{self.broker_name}: cancel_order not implemented")

    def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch current open positions."""
        raise NotImplementedError(f"{self.broker_name}: get_positions not implemented")

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Fetch status of a specific order by order ID."""
        raise NotImplementedError(f"{self.broker_name}: get_order_status not implemented")

    # ------------------------------------------------------------------
    # Representation
    # ------------------------------------------------------------------

    def __repr__(self):
        status = "CONNECTED" if self.is_connected() else "DISCONNECTED"
        return f"<{self.broker_name} [{status}]>"
