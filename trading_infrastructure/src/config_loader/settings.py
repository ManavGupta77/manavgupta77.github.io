# ==========================================
# CORE/CONFIG.PY
# ==========================================
import os
from pathlib import Path
from dotenv import load_dotenv

# Path setup
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = PROJECT_ROOT / "config" / ".env"
load_dotenv(ENV_PATH)

# --- 2026 Index Specifications ---
# Updated for NSE January 2026 Lot Size Revisions
INDEX_CONFIG = {
    "NIFTY": {
        "spot_exchange": "NSE",
        "spot_symbol": "Nifty 50",
        "angel_token": "99926000",
        "shoonya_token": "26000",
        "strike_gap": 50,
        "lot_size": 65,
        "option_exchange": "NFO",
        "option_prefix": "NIFTY",
        "expiry_day": 1,        # Tuesday (0=Mon)
    },
    "BANKNIFTY": {
        "spot_exchange": "NSE",
        "spot_symbol": "Nifty Bank",
        "angel_token": "99926009",
        "shoonya_token": "26009",
        "strike_gap": 100,
        "lot_size": 30,
        "option_exchange": "NFO",
        "option_prefix": "BANKNIFTY",
        "expiry_day": 2,        # Wednesday
    },
    "FINNIFTY": {
        "spot_exchange": "NSE",
        "spot_symbol": "Nifty Fin Service",
        "angel_token": "99926037",
        "shoonya_token": "26037",
        "strike_gap": 50,
        "lot_size": 60,
        "option_exchange": "NFO",
        "option_prefix": "FINNIFTY",
        "expiry_day": 1,        # Tuesday
    },
    "SENSEX": {
        "spot_exchange": "BSE",
        "spot_symbol": "SENSEX",
        "angel_token": "99919000",
        "shoonya_token": "1",
        "strike_gap": 100,
        "lot_size": 20,
        "option_exchange": "BFO",
        "option_prefix": "SENSEX",
        "expiry_day": 3,        # Thursday
    }
}

class Config:
    """Platform configuration loaded from .env file."""

    # ------------------------------------------
    # PLATFORM & SECURITY
    # ------------------------------------------
    PRIMARY_BROKER  = os.getenv("PRIMARY_BROKER", "SHOONYA").upper()
    TRADING_MODE    = os.getenv("TRADING_MODE", "PAPER").upper()
    WEBHOOK_PORT    = int(os.getenv("WEBHOOK_PORT", "8000"))
    WEBHOOK_SECRET  = os.getenv("WEBHOOK_SECRET", "ALGO_KEY_99")
    DASHBOARD_PORT  = int(os.getenv("DASHBOARD_PORT", "8501"))

    # Failover chain (ordered, comma-separated)
    BROKER_PRIORITY = os.getenv("BROKER_PRIORITY", "SHOONYA,ANGEL,UPSTOX")

    @property
    def broker_priority_list(self):
        """Parse BROKER_PRIORITY into ordered list: ['SHOONYA', 'ANGEL', 'UPSTOX']"""
        return [b.strip().upper() for b in self.BROKER_PRIORITY.split(",") if b.strip()]

    # ------------------------------------------
    # BROKER CREDENTIALS: Angel One (SmartAPI)
    # ------------------------------------------
    ANGEL_API_KEY    = os.getenv("ANGEL_API_KEY", "")
    ANGEL_CLIENT_ID  = os.getenv("ANGEL_CLIENT_ID", "")
    ANGEL_PASSWORD   = os.getenv("ANGEL_PASSWORD", "")
    ANGEL_TOTP_KEY   = os.getenv("ANGEL_TOTP_KEY", "")

    # ------------------------------------------
    # BROKER CREDENTIALS: Shoonya (Finvasia)
    # ------------------------------------------
    SHOONYA_USER_ID     = os.getenv("SHOONYA_USER_ID", "")
    SHOONYA_PASSWORD    = os.getenv("SHOONYA_PASSWORD", "")
    SHOONYA_API_SECRET  = os.getenv("SHOONYA_API_SECRET", "")
    SHOONYA_TOTP_KEY    = os.getenv("SHOONYA_TOTP_KEY", "")
    SHOONYA_VENDOR_CODE = os.getenv("SHOONYA_VENDOR_CODE", "")
    SHOONYA_IMEI        = os.getenv("SHOONYA_IMEI", "abc1234")

    # ------------------------------------------
    # BROKER CREDENTIALS: Upstox (OAuth2)
    # ------------------------------------------
    UPSTOX_API_KEY      = os.getenv("UPSTOX_API_KEY", "")
    UPSTOX_API_SECRET   = os.getenv("UPSTOX_API_SECRET", "")
    UPSTOX_REDIRECT_URI = os.getenv("UPSTOX_REDIRECT_URI", "http://127.0.0.1:8000/")

    # ------------------------------------------
    # BROKER CREDENTIALS: Kotak Neo (stub)
    # ------------------------------------------
    KOTAK_USER_ID   = os.getenv("KOTAK_USER_ID", "")
    KOTAK_PASSWORD  = os.getenv("KOTAK_PASSWORD", "")
    KOTAK_API_KEY   = os.getenv("KOTAK_API_KEY", "")
    KOTAK_TOTP_KEY  = os.getenv("KOTAK_TOTP_KEY", "")

    # ------------------------------------------
    # BROKER CREDENTIALS: Zerodha Kite (stub)
    # ------------------------------------------
    ZERODHA_USER_ID     = os.getenv("ZERODHA_USER_ID", "")
    ZERODHA_PASSWORD    = os.getenv("ZERODHA_PASSWORD", "")
    ZERODHA_API_KEY     = os.getenv("ZERODHA_API_KEY", "")
    ZERODHA_API_SECRET  = os.getenv("ZERODHA_API_SECRET", "")
    ZERODHA_TOTP_KEY    = os.getenv("ZERODHA_TOTP_KEY", "")

    # ------------------------------------------
    # BROKER CREDENTIALS: Dhan (stub)
    # ------------------------------------------
    DHAN_CLIENT_ID    = os.getenv("DHAN_CLIENT_ID", "")
    DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

    # ------------------------------------------
    # BROKER CREDENTIALS: FlatTrade (stub)
    # ------------------------------------------
    FLATTRADE_USER_ID     = os.getenv("FLATTRADE_USER_ID", "")
    FLATTRADE_PASSWORD    = os.getenv("FLATTRADE_PASSWORD", "")
    FLATTRADE_API_KEY     = os.getenv("FLATTRADE_API_KEY", "")
    FLATTRADE_API_SECRET  = os.getenv("FLATTRADE_API_SECRET", "")
    FLATTRADE_TOTP_KEY    = os.getenv("FLATTRADE_TOTP_KEY", "")

    # ------------------------------------------
    # DATA SOURCE: ICICI Breeze (Historical Data Only)
    # ------------------------------------------
    BREEZE_API_KEY    = os.getenv("BREEZE_API_KEY", "")
    BREEZE_SECRET_KEY = os.getenv("BREEZE_SECRET_KEY", "")

    # ------------------------------------------
    # SYSTEM PATHS & LOGGING
    # ------------------------------------------
    LOG_LEVEL    = os.getenv("LOG_LEVEL", "INFO").upper()
    DB_NAME      = os.getenv("DB_NAME", "algo_trading.db")
    DB_FULL_PATH = PROJECT_ROOT / "storage" / "databases" / DB_NAME
    LOG_DIR = PROJECT_ROOT / "storage" / "logs" / "live_trading"
    INSTRUMENT_MASTER = PROJECT_ROOT / "storage" / "instrument_masters" / "derivatives" / "instrument_master.json"

    PROJECT_ROOT = PROJECT_ROOT

    def print_summary(self):
        print("=" * 60)
        print("  ALGO SYSTEM CONFIGURATION (2026 STANDARDS)")
        print("=" * 60)
        print(f"  Primary Broker:    {self.PRIMARY_BROKER}")
        print(f"  Broker Priority:   {' → '.join(self.broker_priority_list)}")
        print(f"  Trading Mode:      {self.TRADING_MODE}")
        print(f"  Log Level:         {self.LOG_LEVEL}")
        print(f"  DB Path:           {self.DB_FULL_PATH}")
        print("=" * 60)

cfg = Config()

if __name__ == "__main__":
    cfg.print_summary()
