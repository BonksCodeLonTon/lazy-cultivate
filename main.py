import asyncio
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.bot.client import CultivationBot
from src.db.connection import init_db
from src.utils.config import settings

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format=_LOG_FORMAT,
)

# Persist anything WARNING+ to a rotating file so unhandled errors and
# unexpected fallbacks survive past the terminal scrollback. Logs are
# captured uniformly from every module that uses ``logging.getLogger``,
# including registry loaders, cogs, and the combat phase pipeline.
_LOG_DIR = Path(__file__).parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_error_handler = RotatingFileHandler(
    _LOG_DIR / "error.log",
    maxBytes=2_000_000,
    backupCount=5,
    encoding="utf-8",
)
_error_handler.setLevel(logging.WARNING)
_error_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
logging.getLogger().addHandler(_error_handler)

log = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    async with CultivationBot() as bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Graceful Ctrl+C — not an error worth persisting.
        pass
    except Exception:
        # Any unhandled crash gets a stack trace written to logs/error.log
        # via the rotating-file handler attached above before re-raising.
        log.exception("Unhandled error in main()")
        raise
