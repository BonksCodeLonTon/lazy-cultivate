"""Create the configured Postgres database if it doesn't exist (dev helper).

``init_db()`` / ``create_all`` only builds TABLES inside an existing database;
it cannot create the database catalog itself. Run this once after pointing
``db_name`` at a fresh database:

    python scripts/create_db.py

Connects to the ``postgres`` maintenance DB with the same credentials the bot
uses (from ``.env`` via ``settings``) and issues ``CREATE DATABASE`` if needed.
"""
import asyncio
import sys
from pathlib import Path

import asyncpg

# Allow running as `python scripts/create_db.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode emoji/diacritics.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from src.utils.config import settings  # noqa: E402


async def main() -> None:
    sys_conn = await asyncpg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database="postgres",
    )
    try:
        exists = await sys_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", settings.db_name
        )
        if exists:
            print(f"ℹ️  Database {settings.db_name!r} already exists — nothing to do.")
        else:
            # CREATE DATABASE can't run inside a transaction; asyncpg.execute
            # autocommits a bare statement, so this is fine.
            await sys_conn.execute(f'CREATE DATABASE "{settings.db_name}"')
            print(f"✅ Created database {settings.db_name!r}. Now run: python main.py")
    finally:
        await sys_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
