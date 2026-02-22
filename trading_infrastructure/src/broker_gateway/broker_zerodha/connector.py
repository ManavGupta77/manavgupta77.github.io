# ==========================================
# CORE/BROKERS/ZERODHA.PY — STUB
# ==========================================
# Status: Not yet implemented. Fill credentials in .env and
# implement _login(), get_ltp(), search_instruments() when ready.
# Library: kiteconnect (pip install kiteconnect)

from typing import Optional, List, Dict, Any
from config_loader.settings import cfg
from utilities.logger import get_logger
from broker_gateway.base_broker import BrokerBase

logger = get_logger("zerodha")


class ZerodhaBroker(BrokerBase):
    """Zerodha Kite broker integration — STUB (not yet implemented)."""

    def __init__(self):
        self.valid_session = False
        if not cfg.ZERODHA_USER_ID:
            logger.info("Zerodha: No credentials configured, skipping login")
            return
        self._login()

    def _login(self):
        """TODO: Implement Zerodha Kite authentication."""
        logger.warning("Zerodha: Login not yet implemented")
        # When implementing:
        # 1. pip install kiteconnect
        # 2. from kiteconnect import KiteConnect
        # 3. kite = KiteConnect(api_key=cfg.ZERODHA_API_KEY)
        # 4. Handle request_token flow (similar to Upstox OAuth)
        # 5. kite.generate_session(request_token, api_secret=cfg.ZERODHA_API_SECRET)
        # 6. Set self.valid_session = True

    # ------------------------------------------------------------------
    # BrokerBase: Identity & Connection
    # ------------------------------------------------------------------

    @property
    def broker_name(self) -> str:
        return "ZERODHA"

    def is_connected(self) -> bool:
        return self.valid_session

    def reconnect(self) -> bool:
        self.valid_session = False
        if not cfg.ZERODHA_USER_ID:
            return False
        self._login()
        return self.valid_session

    # ------------------------------------------------------------------
    # BrokerBase: Market Data
    # ------------------------------------------------------------------

    def get_ltp(self, exchange: str, symbol: str, token: Optional[str] = None) -> Optional[float]:
        if not self.valid_session:
            return None
        raise NotImplementedError("Zerodha: get_ltp not yet implemented")

    def search_instruments(self, exchange: str, search_text: str) -> List[Dict[str, Any]]:
        if not self.valid_session:
            return []
        raise NotImplementedError("Zerodha: search_instruments not yet implemented")


# Singleton instance
broker = ZerodhaBroker()


