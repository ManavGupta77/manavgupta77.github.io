# ==========================================
# CORE/BROKERS/FLATTRADE.PY — STUB
# ==========================================
# Status: Not yet implemented. Fill credentials in .env and
# implement _login(), get_ltp(), search_instruments() when ready.
# Library: NorenRestApiPy (same as Shoonya — FlatTrade uses NorenAPI)

from typing import Optional, List, Dict, Any
from config_loader.settings import cfg
from utilities.logger import get_logger
from broker_gateway.base_broker import BrokerBase

logger = get_logger("flattrade")


class FlatTradeBroker(BrokerBase):
    """FlatTrade broker integration — STUB (not yet implemented)."""

    def __init__(self):
        self.valid_session = False
        if not cfg.FLATTRADE_USER_ID:
            logger.info("FlatTrade: No credentials configured, skipping login")
            return
        self._login()

    def _login(self):
        """TODO: Implement FlatTrade authentication."""
        logger.warning("FlatTrade: Login not yet implemented")
        # When implementing:
        # 1. FlatTrade uses the same NorenAPI as Shoonya
        # 2. from NorenRestApiPy.NorenApi import NorenApi
        # 3. api = NorenApi(host='https://piconnect.flattrade.in/PiConnectTP/',
        #                   websocket='wss://piconnect.flattrade.in/PiConnectWSTTP/')
        # 4. Generate TOTP from cfg.FLATTRADE_TOTP_KEY
        # 5. api.login(userid=cfg.FLATTRADE_USER_ID, password=cfg.FLATTRADE_PASSWORD,
        #              twoFA=totp, vendor_code=..., api_secret=cfg.FLATTRADE_API_SECRET,
        #              imei='abc1234')
        # 6. Set self.valid_session = True
        # Note: Very similar to Shoonya — consider extracting shared NorenAPI base class

    # ------------------------------------------------------------------
    # BrokerBase: Identity & Connection
    # ------------------------------------------------------------------

    @property
    def broker_name(self) -> str:
        return "FLATTRADE"

    def is_connected(self) -> bool:
        return self.valid_session

    def reconnect(self) -> bool:
        self.valid_session = False
        if not cfg.FLATTRADE_USER_ID:
            return False
        self._login()
        return self.valid_session

    # ------------------------------------------------------------------
    # BrokerBase: Market Data
    # ------------------------------------------------------------------

    def get_ltp(self, exchange: str, symbol: str, token: Optional[str] = None) -> Optional[float]:
        if not self.valid_session:
            return None
        raise NotImplementedError("FlatTrade: get_ltp not yet implemented")

    def search_instruments(self, exchange: str, search_text: str) -> List[Dict[str, Any]]:
        if not self.valid_session:
            return []
        raise NotImplementedError("FlatTrade: search_instruments not yet implemented")


# Singleton instance
broker = FlatTradeBroker()


