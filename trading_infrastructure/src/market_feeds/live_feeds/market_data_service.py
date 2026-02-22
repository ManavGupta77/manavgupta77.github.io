# ==========================================
# CORE/MARKET_DATA.PY
# ==========================================
from broker_gateway.connection_manager import handler
from utilities.logger import get_logger
# Assuming INDEX_CONFIG is moved to core.config for centralization
from config_loader.settings import INDEX_CONFIG 

logger = get_logger("market_data")

class MarketData:
    """
    Market data service providing a clean interface for all price-related queries.
    Uses the Broker Handler for automatic failover.
    """

    def __init__(self):
        self._index_config = INDEX_CONFIG

    def get_spot_price(self, instrument):
        """
        Fetch current spot price with automatic broker failover.
        """
        config = self._index_config.get(instrument.upper())
        if not config:
            logger.error("Unknown instrument", instrument=instrument)
            return 0.0

        # Construct the Universal Token Dict for the Handler
        token_dict = {
            'ANGEL': config.get("angel_token"),
            'SHOONYA': config.get("shoonya_token")
        }

        # Let the handler decide which broker to use
        ltp = handler.get_ltp(
            exchange=config["spot_exchange"],
            symbol=config["spot_symbol"],
            token_dict=token_dict
        )

        if ltp and ltp > 0:
            logger.info("Spot price fetched", instrument=instrument, price=ltp)
            return ltp
        
        logger.warning("Spot price unavailable", instrument=instrument)
        return 0.0

    def get_atm_strike(self, instrument, spot_price=None):
        """
        Calculate ATM strike by rounding to the nearest strike gap.
        """
        config = self._index_config.get(instrument.upper())
        if not config: return 0

        if spot_price is None:
            spot_price = self.get_spot_price(instrument)

        if not spot_price or spot_price <= 0: return 0

        gap = config["strike_gap"]
        atm = round(spot_price / gap) * gap
        return int(atm)

    def get_option_ltp(self, exchange, symbol, token_dict):
        """
        Fetch LTP for an option. Pass a dict of tokens for failover support.
        """
        return handler.get_ltp(exchange, symbol, token_dict)

# Singleton Instance
market = MarketData()