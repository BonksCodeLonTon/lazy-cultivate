import asyncio
import logging
from src.bot.client import CultivationBot
from src.db.connection import init_db
from src.utils.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


async def main() -> None:
    await init_db()
    async with CultivationBot() as bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    asyncio.run(main())
