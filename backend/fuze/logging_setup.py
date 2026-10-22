"""Enable application receipts without enabling verbose HTTP or SDK logging."""
import logging


def configure() -> None:
    logger = logging.getLogger("fuze")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
        logger.addHandler(handler)
    logger.propagate = False
