import logging
import sys
from pathlib import Path
from .config import TELOS_HOME

LOG_FILE = TELOS_HOME / "agent.log"

def get_logger(name: str) -> logging.Logger:
    """Get a configured logger for the given module name."""
    logger = logging.getLogger(f"telos.{name}")
    
    if logger.handlers:
        return logger  # Already configured
    
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler
    from .config import settings
    log_level = getattr(logging, settings.logging.level.upper(), logging.INFO)
    
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File handler (DEBUG+)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(LOG_FILE))
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except (PermissionError, OSError):
        logger.warning("Could not create log file at %s", LOG_FILE)

    return logger
