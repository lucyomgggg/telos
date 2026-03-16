import logging
import sys
from logging.handlers import RotatingFileHandler
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

    # File handler (DEBUG+, 10MB x 5 rotations = 50MB max)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(str(LOG_FILE), maxBytes=10 * 1024 * 1024, backupCount=5)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except (PermissionError, OSError) as e:
        logger.warning("Could not create log file at %s: %s", LOG_FILE, e)

    # Suppress verbose third-party logs
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
    logging.getLogger("transformers").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("docker").setLevel(logging.WARNING)
    
    # Silence Transformers load reports
    import os
    os.environ["TRANSFORMERS_VERBOSITY"] = "error"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    
    # Python's default logging for some libraries is too chatty
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)
    
    return logger
