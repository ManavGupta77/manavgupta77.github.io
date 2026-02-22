# ==========================================
# CORE/BROKERS/DHAN.PY — STUB
# ==========================================
# Status: Not yet implemented. Fill credentials in .env and
# implement _login(), get_ltp(), search_instruments() when ready.
# Library: dhanhq (pip install dhanhq)

from typing import Optional, List, Dict, Any
from config_loader.settings import cfg
from utilities.logger import get_logger
from broker_gateway.base_broker import BrokerBase

logger = get_logger("dhan")


class DhanBroker(BrokerBase):
    """Dhan broker integration — STUB (not yet implemented)."""

    def __init__(self):
        self.valid_session = False
        if not cfg.DHAN_CLIENT_ID:
            logger.info("Dhan: No credentials configured, skipping login")
            return
        self._login()

    def _login(self):
        """TODO: Implement Dhan authentication."""
        logger.warning("Dhan: Login not yet implemented")
        # When implementing:
        # 1. pip install dhanhq
        # 2. from dhanhq import dhanhq
        # 3. dhan = dhanhq(cfg.DHAN_CLIENT_ID, cfg.DHAN_ACCESS_TOKEN)
        # 4. Dhan uses a pre-generated access token (no TOTP)
        # 5. Validate with a simple API call
        # 6. Set self.valid_session = True

    # ------------------------------------------------------------------
    # BrokerBase: Identity & Connection
    # ------------------------------------------------------------------

    @property
    def broker_name(self) -> str:
        return "DHAN"

    def is_connected(self) -> bool:
        return self.valid_session

    def reconnect(self) -> bool:
        self.valid_session = False
        if not cfg.DHAN_CLIENT_ID:
            return False
        self._login()
        return self.valid_session

    # ------------------------------------------------------------------
    # BrokerBase: Market Data
    # ------------------------------------------------------------------

    def get_ltp(self, exchange: str, symbol: str, token: Optional[str] = None) -> Optional[float]:
        if not self.valid_session:
            return None
        raise NotImplementedError("Dhan: get_ltp not yet implemented")

    def search_instruments(self, exchange: str, search_text: str) -> List[Dict[str, Any]]:
        if not self.valid_session:
            return []
        raise NotImplementedError("Dhan: search_instruments not yet implemented")


# Singleton instance
broker = DhanBroker()


