# ==========================================
# CORE/BROKERS/SHOONYA.PY
# ==========================================
import pyotp
import time
from typing import Optional, List, Dict, Any
from NorenRestApiPy.NorenApi import NorenApi
from config_loader.settings import cfg
from utilities.logger import get_logger
from broker_gateway.base_broker import BrokerBase

# Standard Platform Logger
logger = get_logger("shoonya")

class ShoonyaBroker(BrokerBase):
    def __init__(self):
        # Initialize API with endpoints from config
        self.api = NorenApi(host='https://api.shoonya.com/NorenWClientTP/', 
                            websocket='wss://api.shoonya.com/NorenWSTP/')
        self.valid_session = False
        self._login()

    # ------------------------------------------------------------------
    # BrokerBase: Identity & Connection
    # ------------------------------------------------------------------

    @property
    def broker_name(self) -> str:
        return "SHOONYA"

    def is_connected(self) -> bool:
        return self.valid_session

    def reconnect(self) -> bool:
        """Re-authenticate with fresh TOTP."""
        logger.info("Attempting reconnection", user_id=cfg.SHOONYA_USER_ID)
        self.valid_session = False
        self.api = NorenApi(host='https://api.shoonya.com/NorenWClientTP/', 
                            websocket='wss://api.shoonya.com/NorenWSTP/')
        self._login()
        return self.valid_session

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _login(self):
        """Authenticate with Shoonya using TOTP and Vendor credentials."""
        logger.info("Initializing Shoonya session", user_id=cfg.SHOONYA_USER_ID)
        try:
            # 1. Generate TOTP
            totp = pyotp.TOTP(cfg.SHOONYA_TOTP_KEY).now()
            
            # 2. Execute Login
            ret = self.api.login(
                userid=cfg.SHOONYA_USER_ID,
                password=cfg.SHOONYA_PASSWORD,
                twoFA=totp,
                vendor_code=cfg.SHOONYA_VENDOR_CODE,
                api_secret=cfg.SHOONYA_API_SECRET,
                imei=cfg.SHOONYA_IMEI
            )
            
            if ret and ret.get('stat') == 'Ok':
                self.valid_session = True
                token_preview = ret.get('susertoken', '')[:10]
                logger.info("Login Success", token_start=f"{token_preview}...")
            else:
                logger.error("Login Failed", message=ret.get('emsg', 'Unknown Error'))
        except Exception as e:
            logger.error("Login Exception", error=str(e))

    # ------------------------------------------------------------------
    # BrokerBase: Market Data
    # ------------------------------------------------------------------

    def get_ltp(self, exchange: str, symbol: str, token: Optional[str] = None) -> Optional[float]:
        """Fetch last traded price with standardized return."""
        if not self.valid_session:
            return None
        try:
            time.sleep(0.2) # Rate limit protection
            res = self.api.get_quotes(exchange=exchange, token=token)
            
            if res and res.get('stat') == 'Ok' and 'lp' in res:
                return float(res['lp'])
            
            logger.warning("LTP fetch failed", symbol=symbol, response=res.get('emsg'))
            return None
        except Exception as e:
            logger.error("LTP exception", symbol=symbol, error=str(e))
            return None

    def search_instruments(self, exchange: str, search_text: str) -> List[Dict[str, Any]]:
        """
        Standardized search for instruments. 
        Maps Shoonya's 'tsym' to 'tradingsymbol' for cross-broker consistency.
        """
        if not self.valid_session: return []
        try:
            res = self.api.searchscrip(exchange=exchange, searchtext=search_text)
            if res and 'values' in res:
                return [{
                    'token': item.get('token'),
                    'tradingsymbol': item.get('tsym')
                } for item in res['values']]
            return []
        except Exception as e:
            logger.error("Search exception", query=search_text, error=str(e))
            return []

    # ------------------------------------------------------------------
    # BrokerBase: Order Management (overrides default NotImplementedError)
    # ------------------------------------------------------------------

    def place_order(self, trading_symbol: str, transaction_type: str, quantity: int, 
                    price: float = 0, product_type: str = 'M', 
                    order_type: str = 'LMT', exchange: str = 'NFO') -> Optional[str]:
        """Standardized order placement."""
        if not self.valid_session: return None
        try:
            buy_or_sell = 'B' if transaction_type.upper() == 'BUY' else 'S'
            ret = self.api.place_order(
                buy_or_sell=buy_or_sell,
                product_type=product_type,
                exchange=exchange,
                tradingsymbol=trading_symbol,
                quantity=quantity,
                discloseqty=0,
                price_type=order_type,
                price=price,
                retention='DAY',
                remarks='AlgoPlatform'
            )
            if ret and ret.get('stat') == 'Ok' and 'norenordno' in ret:
                order_id = ret.get('norenordno')
                logger.info("Order Placed", side=transaction_type, symbol=trading_symbol, id=order_id)
                return order_id
            return None
        except Exception as e:
            logger.error("Order Exception", symbol=trading_symbol, error=str(e))
            return None

# Singleton instance
broker = ShoonyaBroker()
