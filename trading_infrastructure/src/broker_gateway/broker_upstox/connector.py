# ==========================================
# CORE/BROKERS/UPSTOX.PY
# ==========================================
import requests
import urllib.parse
import webbrowser
import json
import csv
import gzip
from pathlib import Path
from typing import Optional, List, Dict, Any
from config_loader.settings import cfg
from utilities.logger import get_logger
from broker_gateway.base_broker import BrokerBase

# Standard Platform Logger
logger = get_logger("upstox")

TOKEN_FILE = cfg.PROJECT_ROOT / "data" / "upstox_token.json"
CONTRACT_FILE = cfg.PROJECT_ROOT / "data" / "upstox_master.csv"

class UpstoxBroker(BrokerBase):
    def __init__(self):
        self.base_url = "https://api.upstox.com/v2"
        self.access_token = None
        self.refresh_token = None
        self.valid_session = False
        self._load_or_login()
        self._ensure_contract()

    # ------------------------------------------------------------------
    # BrokerBase: Identity & Connection
    # ------------------------------------------------------------------

    @property
    def broker_name(self) -> str:
        return "UPSTOX"

    def is_connected(self) -> bool:
        return self.valid_session

    def reconnect(self) -> bool:
        """Re-attempt token load/refresh/OAuth flow."""
        logger.info("Attempting reconnection")
        self.valid_session = False
        self._load_or_login()
        return self.valid_session

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _load_or_login(self):
        """Load existing token from file or perform full OAuth flow."""
        if TOKEN_FILE.exists():
            try:
                with open(TOKEN_FILE, 'r') as f:
                    data = json.load(f)
                self.access_token = data.get('access_token')
                self.refresh_token = data.get('refresh_token')
                if self._check_token_valid():
                    self.valid_session = True
                    logger.info("Session restored from saved token")
                    return
                elif self.refresh_token:
                    if self._refresh_access_token():
                        self.valid_session = True
                        logger.info("Token refreshed successfully")
                        return
            except Exception as e:
                logger.error("Failed to load token", error=str(e))

        self._login()

    def _check_token_valid(self):
        if not self.access_token:
            return False
        try:
            headers = {'Authorization': f'Bearer {self.access_token}'}
            r = requests.get(f"{self.base_url}/user/profile", headers=headers)
            return r.status_code == 200
        except:
            return False

    def _refresh_access_token(self):
        url = "https://api.upstox.com/v2/login/refresh/token"
        headers = {'Content-Type': 'application/json'}
        payload = {
            'refresh_token': self.refresh_token,
            'client_id': cfg.UPSTOX_API_KEY,
            'client_secret': cfg.UPSTOX_API_SECRET,
            'grant_type': 'refresh_token'
        }
        try:
            r = requests.post(url, json=payload, headers=headers)
            data = r.json()
            if data.get('access_token'):
                self.access_token = data['access_token']
                self.refresh_token = data.get('refresh_token', self.refresh_token)
                self._save_token()
                return True
            else:
                logger.error("Token refresh failed", response=str(data))
                return False
        except Exception as e:
            logger.error("Token refresh exception", error=str(e))
            return False

    def _save_token(self):
        with open(TOKEN_FILE, 'w') as f:
            json.dump({
                'access_token': self.access_token,
                'refresh_token': self.refresh_token
            }, f)

    def _login(self):
        logger.info("Starting OAuth2 login flow")
        auth_url = (
            f"https://api.upstox.com/v2/login/authorization/dialog"
            f"?response_type=code"
            f"&client_id={cfg.UPSTOX_API_KEY}"
            f"&redirect_uri={urllib.parse.quote(cfg.UPSTOX_REDIRECT_URI)}"
        )
        # Print for user interaction (must be visible in terminal)
        print(f"\n[Upstox OAuth2] Open this URL in your browser and authorize:\n{auth_url}\n")
        logger.info("OAuth2 URL generated", url=auth_url)
        webbrowser.open(auth_url)
        redirect_url = input("[Upstox OAuth2] Paste the FULL redirect URL here: ").strip()

        parsed = urllib.parse.urlparse(redirect_url)
        query = urllib.parse.parse_qs(parsed.query)
        code = query.get('code', [None])[0]
        if not code:
            logger.error("No authorization code found in redirect URL")
            return

        token_url = "https://api.upstox.com/v2/login/authorization/token"
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        payload = {
            'code': code,
            'client_id': cfg.UPSTOX_API_KEY,
            'client_secret': cfg.UPSTOX_API_SECRET,
            'redirect_uri': cfg.UPSTOX_REDIRECT_URI,
            'grant_type': 'authorization_code'
        }
        try:
            r = requests.post(token_url, data=payload, headers=headers)
            data = r.json()
            if data.get('access_token'):
                self.access_token = data['access_token']
                self.refresh_token = data.get('refresh_token')
                self.valid_session = True
                self._save_token()
                logger.info("Login successful, token saved")
            else:
                logger.error("Token exchange failed", response=str(data))
        except Exception as e:
            logger.error("Login exception", error=str(e))

    # ------------------------------------------------------------------
    # BrokerBase: Market Data
    # ------------------------------------------------------------------

    def get_ltp(self, exchange: str, symbol: str, token: Optional[str] = None) -> Optional[float]:
        """
        Adapter for base class. token is the instrument key.
        exchange and symbol are ignored (kept for interface compatibility).
        """
        if token is None:
            return None
        return self._get_ltp_original(token)

    def _get_ltp_original(self, instrument_key):
        """Internal LTP method — takes instrument key directly."""
        if not self.valid_session:
            return None
        try:
            encoded_key = urllib.parse.quote(instrument_key)
            url = f"{self.base_url}/market-quote/ltp?instrument_key={encoded_key}"
            headers = {'Authorization': f'Bearer {self.access_token}'}
            r = requests.get(url, headers=headers)
            data = r.json()
            if data.get('status') == 'success':
                return list(data['data'].values())[0]['last_price']
            else:
                logger.warning("LTP fetch failed", instrument=instrument_key, response=str(data))
                return None
        except Exception as e:
            logger.error("LTP exception", instrument=instrument_key, error=str(e))
            return None

    def search_instruments(self, exchange: str, search_text: str) -> List[Dict[str, Any]]:
        """
        Search master contract for instruments containing search_text.
        Returns list of dicts with 'token' (instrument_key) and 'tradingsymbol'.
        """
        if not CONTRACT_FILE.exists():
            self._download_contracts()
        results = []
        try:
            with open(CONTRACT_FILE, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if exchange and row.get('exchange') != exchange:
                        continue
                    ts = row.get('tradingsymbol', '')
                    if search_text.lower() in ts.lower():
                        results.append({
                            'token': row.get('instrument_key'),
                            'tradingsymbol': ts
                        })
            return results
        except Exception as e:
            logger.error("Search exception", query=search_text, error=str(e))
            return []

    # ------------------------------------------------------------------
    # Extended Methods (Upstox-specific)
    # ------------------------------------------------------------------

    def get_option_chain(self, expiry_date):
        """Fetch option chain for Nifty 50."""
        if not self.valid_session:
            return None
        url = f"{self.base_url}/option/chain"
        params = {
            'instrument_key': 'NSE_INDEX|Nifty 50',
            'expiry_date': expiry_date.strftime('%Y-%m-%d')
        }
        headers = {'Authorization': f'Bearer {self.access_token}'}
        try:
            r = requests.get(url, params=params, headers=headers)
            data = r.json()
            if data.get('status') == 'success':
                return data.get('data', [])
            else:
                logger.warning("Option chain error", response=str(data))
                return None
        except Exception as e:
            logger.error("Option chain exception", error=str(e))
            return None

    def _ensure_contract(self, force=False):
        if not CONTRACT_FILE.exists() or force:
            self._download_contracts()

    def _download_contracts(self):
        url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
        logger.info("Downloading master contract file")
        try:
            r = requests.get(url, stream=True)
            gz_path = CONTRACT_FILE.with_suffix('.csv.gz')
            with open(gz_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            with gzip.open(gz_path, 'rt') as gz:
                with open(CONTRACT_FILE, 'w') as out:
                    out.write(gz.read())
            gz_path.unlink()
            logger.info("Contracts saved", path=str(CONTRACT_FILE))
        except Exception as e:
            logger.error("Contract download failed", error=str(e))

    def find_instrument_key(self, trading_symbol, exchange=None, exact=True):
        """Find instrument key by trading symbol from master contract."""
        if not CONTRACT_FILE.exists():
            self._download_contracts()
        try:
            with open(CONTRACT_FILE, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = row.get('tradingsymbol', '')
                    if exchange and row.get('exchange') != exchange:
                        continue
                    if exact:
                        if ts == trading_symbol:
                            return row.get('instrument_key')
                    else:
                        if trading_symbol.lower() in ts.lower():
                            return row.get('instrument_key')
        except Exception as e:
            logger.error("Contract search error", symbol=trading_symbol, error=str(e))
        return None

    def find_all_indices(self):
        """List all available indices from master contract."""
        indices = []
        if not CONTRACT_FILE.exists():
            self._download_contracts()
        try:
            with open(CONTRACT_FILE, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('exchange') == 'NSE_INDEX':
                        indices.append({
                            'tradingsymbol': row.get('tradingsymbol'),
                            'instrument_key': row.get('instrument_key')
                        })
            indices.sort(key=lambda x: x['tradingsymbol'])
            return indices
        except Exception as e:
            logger.error("Error reading contract file", error=str(e))
            return []

    def get_historical_data(self, instrument_token, from_date, to_date, interval="1minute"):
        """
        Fetch historical candles.
        Dates should be strings 'YYYY-MM-DD'.
        """
        if not self.valid_session:
            logger.warning("Session invalid, cannot fetch history")
            return []

        url = f"{self.base_url}/historical-candle/{instrument_token}/{interval}/{to_date}/{from_date}"
        headers = {'Authorization': f'Bearer {self.access_token}'}
        
        try:
            r = requests.get(url, headers=headers)
            data = r.json()
            
            if data.get('status') == 'success' and 'candles' in data.get('data', {}):
                return data['data']['candles']
            else:
                logger.warning("Historical data error", instrument=instrument_token, response=str(data))
                return []
        except Exception as e:
            logger.error("Historical data exception", instrument=instrument_token, error=str(e))
            return []

broker = UpstoxBroker()


