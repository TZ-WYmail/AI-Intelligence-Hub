import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Only allow controlled messages. External exception strings may contain secrets/URLs.
        return json.dumps(
            {
                "time": datetime.now(UTC).isoformat(),
                "module": record.name,
                "level": record.levelname,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
