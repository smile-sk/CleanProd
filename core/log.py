"""Единая настройка логов для всех ботов: файл в data/logs + stdout."""
import logging
from logging.handlers import RotatingFileHandler

import config


def setup_logger(name: str, filename: str, level: int = logging.INFO) -> logging.Logger:
    config.ensure_dirs()
    logger = logging.getLogger(name)
    logger.setLevel(level)
    if logger.handlers:  # уже настроен
        return logger
    handler = RotatingFileHandler(
        config.LOG_DIR / filename,
        maxBytes=5 * 1024 * 1024,
        backupCount=1,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger
