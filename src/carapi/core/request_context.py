import logging
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str) -> None:
    logger = logging.getLogger("carapi")
    logger.setLevel(level.upper())
    if not any(getattr(h, "_carapi", False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler._carapi = True  # type: ignore[attr-defined]
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.propagate = False
