# ==============================================================================
# EXECUTION PACKAGE
# ==============================================================================
# ExecutionHandlers — translate TradeSignals into LegFills.
# One handler per execution mode: Backtest, Paper, Live.
#
# Usage:
#   from execution import BacktestExecutionHandler
#   from execution import PaperExecutionHandler
# ==============================================================================

from .backtest_execution_handler import BacktestExecutionHandler
from .paper_handler import PaperExecutionHandler

__all__ = ["BacktestExecutionHandler", "PaperExecutionHandler"]
