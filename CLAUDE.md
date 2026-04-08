# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the bot
python main.py

# Run all tests
pytest

# Run a single test file
pytest tests/test_cultivation.py

# Run a single test by name
pytest tests/test_cultivation.py::test_function_name

# Run with coverage
pytest --cov=src --cov-report=term-missing

# Database migrations (Alembic)
alembic upgrade head          # apply all migrations
alembic revision --autogenerate -m "description"  # create new migration
alembic downgrade -1          # roll back one step
```

## Environment Setup

Copy `.env.example` to `.env` and fill in:
- `DISCORD_TOKEN` — bot token
- `DISCORD_GUILD_ID` — set during dev for instant slash command sync (skip global propagation delay)
- `DB_HOST/PORT/USER/PASSWORD/DB_NAME` — PostgreSQL connection (SQLite not used; asyncpg requires PostgreSQL)

The bot uses `src/utils/config.py` (Pydantic Settings) to load all config from env vars.

## Architecture

```
main.py                     Entry point: init DB, start bot
src/bot/client.py           CultivationBot — loads all cogs, syncs slash commands
src/bot/cogs/               One cog per feature domain (Discord slash commands only, no logic)
src/game/
  constants/                Immutable game rules: realms, elements, grades, currencies, linh_can
  models/                   Pure Python dataclasses (no DB) — Character, Enemy, Item, Skill
  systems/                  Core game logic — cultivation, combat, economy, trade, dungeon
  engine/                   Low-level computation: damage pipeline, rating formula, tick processor
    damage/                 Modular pipeline: evasion → base roll → crit → elemental → final bonus
    linh_can_effects/       One module per element (am/hoa/kim/loi/phong/quang/tho/thuy)
src/db/
  models/                   SQLAlchemy ORM models (async)
  repositories/             Data access layer — player_repo, inventory_repo, market_repo, formation_repo
  migrations/               Alembic versioned migrations
src/data/                   Static JSON files loaded at startup via src/data/registry.py
```

**Key data flows:**
- Cogs receive Discord interactions → call `game/systems/` → use `db/repositories/` for persistence
- `game/models/` are runtime objects (not ORM); populated from DB rows via repositories
- `game/engine/tick.py` computes offline AFK progress when a player reconnects
- `game/engine/damage/pipeline.py` is the single entry point for all damage calculations — chains evasion → roll → crit → elemental → final_bonus

## Game Domain Concepts

- **3-axis cultivation**: Luyện Thể (body/tank), Luyện Khí (qi/balanced), Trận Đạo (formation/mage) — 9 realms × 9 levels each
- **Turn system**: 1440 turns/day (1 turn = 1 real minute); first 440 turns = bonus (2× Công Đức, 0 Nghiệp Lực)
- **Currencies**: Công Đức (merit, main spend), Nghiệp Lực (karma, two pools: accumulated vs usable), Hỗn Nguyên Thạch (premium, drop-only)
- **Damage formula**: `DMG = BaseSkill + MPCost` (no ATK/DEF stats — skills are the only damage source)
- **Rating formula**: `% = Rating / (Rating + 1300)` — applies to crit, evasion, crit-dmg, crit-res
- **Linh Căn (spiritual root)**: 8 elements, each with its own effect module in `engine/linh_can_effects/`

## Database Notes

- All DB access is async (SQLAlchemy 2.x + asyncpg)
- Use `get_session()` context manager from `src/db/connection.py` for all DB operations
- `init_db()` (create_all) is for dev only — use Alembic migrations in production
- New models must be imported in `src/db/connection.py` so `Base.metadata` discovers them
