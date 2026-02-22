# ==========================================
# CORE/BROKERS/ANGELONE.PY
# ==========================================
import pyotp
import os
import time
from contextlib import contextmanager
from typing import List, Dict, Any, Optional
from SmartApi import SmartConnect
from config_loader.settings import cfg
from utilities.logger import get_logger
from broker_gateway.base_broker import BrokerBase

# Standard Platform Logger
logger = get_logger("angel")

@contextmanager
def suppress_fd_output(fd=1):  # 1 = stdout, 2 = stderr
    """
    Redirect a file descriptor to /dev/null for the duration of the context.
    Suppresses C-level prints from the SmartApi library.
    """
    original_fd = os.dup(fd)
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, fd)
        os.close(devnull)
        yield
    finally:
        os.dup2(original_fd, fd)
        os.close(original_fd)

class AngelBroker(BrokerBase):
    def __init__(self):
        self.api = SmartConnect(api_key=cfg.ANGEL_API_KEY)
        self.access_token = None
        self.refresh_token = None
        self.valid_session = False
        self._login()

    # ------------------------------------------------------------------
    # BrokerBase: Identity & Connection
    # ------------------------------------------------------------------

    @property
    def broker_name(self) -> str:
        return "ANGEL"

    def is_connected(self) -> bool:
        return self.valid_session

    def reconnect(self) -> bool:
        """Re-authenticate by running the full login flow again."""
        logger.info("Attempting reconnection", client_id=cfg.ANGEL_CLIENT_ID)
        self.valid_session = False
        self.api = SmartConnect(api_key=cfg.ANGEL_API_KEY)
        self._login()
        return self.valid_session

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _login(self):
        """Authenticate using double-token logic."""
        logger.info("Initializing Angel One session", client_id=cfg.ANGEL_CLIENT_ID)
        try:
            # 1. Generate TOTP
            totp = pyotp.TOTP(cfg.ANGEL_TOTP_KEY).now()
            
            # 2. Generate Session
            data = self.api.generateSession(
                cfg.ANGEL_CLIENT_ID, cfg.ANGEL_PASSWORD, totp
            )
            
            if data and data.get('status'):
                self.refresh_token = data['data']['refreshToken']
                
                # 3. Explicit Token Generation
                token_res = self.api.generateToken(self.refresh_token)
                
                if token_res and token_res.get('status'):
                    self.access_token = token_res['data']['jwtToken']
                    self.api.setAccessToken(self.access_token)
                    logger.info("Session active: Token generated successfully")
                else:
                    # Fallback to initial token if generateToken fails
                    self.access_token = data['data']['jwtToken']
                    self.api.setAccessToken(self.access_token)
                    logger.warning("Session active: Using fallback JWT")
                
                self.valid_session = True
            else:
                logger.error("Login failed", msg=data.get('message'))
                
        except Exception as e:
            logger.error("Login exception", error=str(e))

    # ------------------------------------------------------------------
    # BrokerBase: Market Data
    # ------------------------------------------------------------------

    def get_ltp(self, exchange: str, symbol: str, token: Optional[str] = None) -> Optional[float]:
        """Standardized LTP fetcher with platform logging."""
        if not self.valid_session:
            logger.warning("LTP skipped: No valid session")
            return None
            
        try:
            # Respect Angel One rate limits
            time.sleep(0.3) 
            
            resp = self.api.ltpData(exchange, symbol, token)
            
            if resp and resp.get('status'):
                return float(resp['data']['ltp'])
            
            # Log exact failure reason to data/logs/system.log
            logger.error("LTP fetch failed", symbol=symbol, response=resp.get('message'))
            return None
        except Exception as e:
            logger.error("LTP exception", symbol=symbol, error=str(e))
            return None

    def search_instruments(self, exchange: str, search_text: str) -> List[Dict[str, Any]]:
        """Search instruments while silencing library noise."""
        if not self.valid_session: return []
        try:
            with suppress_fd_output(1), suppress_fd_output(2):
                resp = self.api.searchScrip(exchange, search_text)
            
            if resp and resp.get('status'):
                return resp.get('data', [])
            return []
        except Exception as e:
            logger.error("Search exception", query=search_text, error=str(e))
            return []

# Singleton instance
broker = AngelBroker()


