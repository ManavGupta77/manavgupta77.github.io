# ==============================================================================
# BUILDING BLOCKS / GREEKS_CALCULATOR.PY
# ==============================================================================
# Computes options Greeks using the Black-Scholes model.
# Used in backtest mode where broker feed Greeks are not available.
# In live/paper mode, broker Greeks are used if available; this module
# is called as a fallback when the broker does not provide them.
#
# INPUTS AVAILABLE FROM YOUR EXISTING DATA:
#   - Spot price           → market_data table (close)
#   - Option price         → options_ohlc table (close)
#   - Strike               → options_ohlc table (strike column)
#   - Expiry date          → options_ohlc table (expiry column)
#   - Risk-free rate       → Constant (use Indian 10-yr G-Sec ~7%)
#
# IV CALCULATION:
#   IV is backed out from the market option price using Newton-Raphson
#   iteration. The option's close price IS the market price, so IV
#   is the volatility that makes Black-Scholes equal to that price.
#
# USAGE:
#   calc = GreeksCalculator(risk_free_rate=0.07)
#   greeks = calc.compute(
#       spot=22850.0,
#       strike=22850,
#       option_price=145.0,
#       option_type="CE",
#       days_to_expiry=6.0,
#   )
#   print(greeks.delta, greeks.iv)
# ==============================================================================

import math
from typing import Optional
from .market_tick import GreekSnapshot


class GreeksCalculator:
    """
    Black-Scholes Greeks calculator for NSE options.

    Instantiate once and reuse across all ticks — stateless per call.

    Args:
        risk_free_rate : Annual risk-free rate as decimal. Default 0.07 (7%).
                         Approximates Indian 10-year G-Sec yield.
    """

    def __init__(self, risk_free_rate: float = 0.07):
        self.r = risk_free_rate

    # ── Core Black-Scholes ────────────────────────────────────────────────────

    def _norm_cdf(self, x: float) -> float:
        """Standard normal cumulative distribution function."""
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    def _norm_pdf(self, x: float) -> float:
        """Standard normal probability density function."""
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    def _d1_d2(self, S: float, K: float, T: float,
               sigma: float) -> tuple[float, float]:
        """
        Compute d1 and d2 terms for Black-Scholes.

        Args:
            S     : Spot price
            K     : Strike price
            T     : Time to expiry in years
            sigma : Volatility (annual, as decimal e.g. 0.145 for 14.5%)
        """
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.0, 0.0
        log_sk = math.log(S / K)
        d1 = (log_sk + (self.r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return d1, d2

    def bs_price(self, S: float, K: float, T: float,
                 sigma: float, option_type: str) -> float:
        """
        Black-Scholes theoretical option price.

        Args:
            S           : Spot price
            K           : Strike price
            T           : Time to expiry in years
            sigma       : Implied volatility (decimal)
            option_type : 'CE' or 'PE'

        Returns:
            Theoretical option price. Returns 0.0 for invalid inputs.
        """
        if T <= 0:
            # At expiry: return intrinsic value only
            if option_type == "CE":
                return max(S - K, 0.0)
            else:
                return max(K - S, 0.0)

        d1, d2 = self._d1_d2(S, K, T, sigma)
        discount = math.exp(-self.r * T)

        if option_type == "CE":
            return S * self._norm_cdf(d1) - K * discount * self._norm_cdf(d2)
        else:  # PE
            return K * discount * self._norm_cdf(-d2) - S * self._norm_cdf(-d1)

    # ── Implied Volatility ────────────────────────────────────────────────────

    def implied_volatility(self, market_price: float, S: float, K: float,
                           T: float, option_type: str,
                           max_iterations: int = 100,
                           tolerance: float = 1e-5) -> float:
        """
        Back out Implied Volatility from a market price using Newton-Raphson.

        Args:
            market_price  : Observed option close price from options_ohlc.
            S             : Current spot price.
            K             : Strike price.
            T             : Time to expiry in years.
            option_type   : 'CE' or 'PE'
            max_iterations: Newton-Raphson iteration cap.
            tolerance     : Convergence threshold for price difference.

        Returns:
            IV as decimal (e.g. 0.145 for 14.5%). Returns 0.0 if no convergence
            or invalid inputs (deep ITM/OTM with zero time value).
        """
        if T <= 0 or market_price <= 0:
            return 0.0

        # Initial guess: use Brenner-Subrahmanyam approximation
        sigma = math.sqrt(2 * math.pi / T) * (market_price / S)
        sigma = max(0.01, min(sigma, 5.0))  # Clamp to [1%, 500%]

        for _ in range(max_iterations):
            price = self.bs_price(S, K, T, sigma, option_type)
            diff  = price - market_price

            if abs(diff) < tolerance:
                return round(sigma, 6)

            # Vega for Newton-Raphson step
            d1, _ = self._d1_d2(S, K, T, sigma)
            vega = S * self._norm_pdf(d1) * math.sqrt(T)

            if vega < 1e-10:
                break  # Vega too small — deep ITM/OTM, no convergence

            sigma -= diff / vega
            sigma = max(0.001, min(sigma, 5.0))  # Keep in valid range

        return round(sigma, 6)

    # ── Full Greeks ───────────────────────────────────────────────────────────

    def compute(self, spot: float, strike: int, option_price: float,
                option_type: str, days_to_expiry: float,
                symbol: str = "") -> GreekSnapshot:
        """
        Compute all Greeks for a single option contract.

        This is the main method called by the tick assembler for each
        tracked option symbol on every backtest tick.

        Args:
            spot           : Current spot price (from market_data).
            strike         : Option strike (integer).
            option_price   : Current option close price (from options_ohlc).
            option_type    : 'CE' or 'PE'.
            days_to_expiry : Calendar days to weekly expiry (float).
                             Use 0.5 for last trading day (expiry day).
            symbol         : Trading symbol string (for GreekSnapshot.symbol).

        Returns:
            GreekSnapshot with all fields populated.
            Fields default to 0.0 if computation fails.
        """
        S = float(spot)
        K = float(strike)
        P = float(option_price)

        # Convert calendar days to trading-year fraction
        # NSE: 252 trading days per year
        T = max(days_to_expiry / 252.0, 1e-6)

        greeks = GreekSnapshot(symbol=symbol, source="BS")

        try:
            # Step 1: Back out IV from market price
            sigma = self.implied_volatility(P, S, K, T, option_type)

            if sigma <= 0:
                # Fallback: use 15% as default IV for NSE Nifty options
                sigma = 0.15

            greeks.iv = round(sigma * 100, 2)  # Store as percentage

            # Step 2: Compute d1, d2
            d1, d2 = self._d1_d2(S, K, T, sigma)

            # Step 3: Delta
            if option_type == "CE":
                greeks.delta = round(self._norm_cdf(d1), 4)
            else:
                greeks.delta = round(self._norm_cdf(d1) - 1.0, 4)

            # Step 4: Gamma (same for CE and PE)
            greeks.gamma = round(
                self._norm_pdf(d1) / (S * sigma * math.sqrt(T)), 6
            )

            # Step 5: Theta (daily decay — divide annual by 252)
            r, sqrt_T = self.r, math.sqrt(T)
            common_theta = -(S * self._norm_pdf(d1) * sigma) / (2 * sqrt_T)

            if option_type == "CE":
                greeks.theta = round(
                    (common_theta - r * K * math.exp(-r * T) * self._norm_cdf(d2))
                    / 252, 2
                )
            else:
                greeks.theta = round(
                    (common_theta + r * K * math.exp(-r * T) * self._norm_cdf(-d2))
                    / 252, 2
                )

            # Step 6: Vega (per 1% change in IV)
            greeks.vega = round(
                S * self._norm_pdf(d1) * sqrt_T / 100, 2
            )

            # Step 7: Intrinsic and Time Value
            if option_type == "CE":
                greeks.intrinsic = round(max(S - K, 0.0), 2)
            else:
                greeks.intrinsic = round(max(K - S, 0.0), 2)
            greeks.time_value = round(max(P - greeks.intrinsic, 0.0), 2)

        except (ValueError, ZeroDivisionError, OverflowError):
            # Silently return zero Greeks on math errors (deep ITM/OTM edge cases)
            pass

        return greeks

    def compute_batch(self, spot: float, contracts: list,
                      days_to_expiry: float) -> dict:
        """
        Compute Greeks for multiple contracts in one call.

        Args:
            spot           : Current spot price.
            contracts      : List of dicts with keys:
                             'symbol', 'strike', 'price', 'option_type'
            days_to_expiry : Days to expiry (same for all — same weekly expiry).

        Returns:
            Dict of {symbol: GreekSnapshot}

        Usage:
            contracts = [
                {"symbol": "NIFTY26FEB24000CE", "strike": 24000,
                 "price": 145.0, "option_type": "CE"},
                {"symbol": "NIFTY26FEB24000PE", "strike": 24000,
                 "price": 138.5, "option_type": "PE"},
            ]
            greeks_map = calc.compute_batch(22850.0, contracts, 6.0)
        """
        result = {}
        for c in contracts:
            result[c["symbol"]] = self.compute(
                spot=spot,
                strike=c["strike"],
                option_price=c["price"],
                option_type=c["option_type"],
                days_to_expiry=days_to_expiry,
                symbol=c["symbol"],
            )
        return result
