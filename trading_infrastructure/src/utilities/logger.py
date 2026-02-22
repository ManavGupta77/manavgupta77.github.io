# ==========================================
# CORE/LOGGER.PY
# ==========================================
import logging
import sys
from pathlib import Path
from config_loader.settings import cfg

# Create logs directory if it doesn't exist
cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)

class StructuredLoggerAdapter(logging.LoggerAdapter):
    """
    Wraps standard logger to support structured syntax:
    logger.info("Event", key=value) -> "Event [key=value]"
    """
    def process(self, msg, kwargs):
        if not kwargs:
            return msg, kwargs
        
        # Format extra keywords as string: "key1=val1 key2=val2"
        context_str = " ".join(f"{k}={v}" for k, v in kwargs.items())
        
        # Append to message
        new_msg = f"{msg} [{context_str}]"
        
        # Return modified message and EMPTY kwargs (so standard logger doesn't crash)
        return new_msg, {}

def get_logger(name):
    """
    Creates a structured logger.
    Output: Console (Clean) + File (Detailed)
    """
    logger = logging.getLogger(name)
    
    # Set level from config
    level = getattr(logging, cfg.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)

    # Only add handlers if they don't exist (prevents duplicate logs)
    if not logger.hasHandlers():
        # 1. File Handler (Detailed)
        log_file = cfg.LOG_DIR / "system.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        file_handler.setFormatter(file_formatter)

        # 2. Console Handler (Clean)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = logging.Formatter(
            '[%(levelname)s] %(name)s: %(message)s'
        )
        console_handler.setFormatter(console_formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    # Return the Adapter, not the raw logger
    return StructuredLoggerAdapter(logger, {})