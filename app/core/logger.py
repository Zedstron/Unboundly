import logging
from pathlib import Path
from app.core.config import settings
from logging.handlers import RotatingFileHandler

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    if name != "root" and name != __name__:
        logger.propagate = False
    
    logger.setLevel(getattr(logging, settings.log_level))
    
    if not settings.logging_enabled:
        logger.addHandler(logging.NullHandler())
        return logger
    
    try:
        import colorlog
        console_handler = colorlog.StreamHandler()
        formatter = colorlog.ColoredFormatter(
            fmt='%(log_color)s[%(asctime)s]%(reset)s - %(name)s - %(levelname)s - %(message)s',
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            }
        )
    except ImportError:
        console_handler = logging.StreamHandler()
        formatter = logging.Formatter('[%(asctime)s] - %(name)s - %(levelname)s - %(message)s')
    
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, settings.log_level))

    logger.addHandler(console_handler)

    if hasattr(settings, 'log_file_path') and settings.log_file_path:
        log_file = Path(settings.log_file_path)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5
        )
        file_formatter = logging.Formatter('[%(asctime)s] - %(name)s - %(levelname)s - %(message)s')

        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(getattr(logging, settings.log_level))

        logger.addHandler(file_handler)
    
    return logger