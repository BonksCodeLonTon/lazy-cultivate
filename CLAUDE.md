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
alembic upgrade head                              # apply all migrations
alembic revision --autogenerate -m "description" # create new migration
alembic downgrade -1                             # roll back one step
```

## Environment Setup

Copy `.env.example` to `.env` and fill in:
- `DISCORD_TOKEN` — bot token
- `DISCORD_GUILD_ID` — set during dev for instant slash command sync (skip global propagation delay); read as `GUILD_ID` inside `src/bot/client.py`
- `DB_HOST/PORT/USER/PASSWORD/DB_NAME` — PostgreSQL connection components (asyncpg; no raw URL needed)
- `DEBUG` / `LOG_LEVEL` — optional; `DEBUG=true` enables SQLAlchemy echo

Config is loaded via `src/utils/config.py` (Pydantic `BaseSettings`); `settings.database_url` builds the asyncpg URL automatically.

## Architecture

```
main.py                     Entry point: init DB, start bot
src/bot/client.py           CultivationBot — loads cogs, syncs slash commands
src/bot/cogs/               One cog per feature (Discord layer only, no game logic)
                              cultivation, combat, equipment, dungeon,
                              inventory, shop, trade, admin
src/game/
  constants/                Immutable game rules: realms, elements, grades, currencies, linh_can
  models/                   Pure Python dataclasses (no DB) — Character, Enemy, Item, Skill
  systems/                  Core game logic — cultivation, combat, economy, trade, dungeon
  engine/                   Low-level computation
    damage/                 Pipeline: evasion → base roll → crit → elemental → final_bonus
    linh_can_effects/       One module per element (am/hoa/kim/loi/phong/quang/tho/thuy)
    tick.py                 Offline AFK progress computed on reconnect
src/db/
  models/                   SQLAlchemy ORM models (async) — must be imported in connection.py
  repositories/             Data access: player_repo, inventory_repo, market_repo, formation_repo
  migrations/               Alembic versioned migrations
src/data/                   Static JSON loaded at startup via GameRegistry singleton
  items/                    chests, elixirs, gems, materials, scrolls, specials
  skills/                   thien, dia, nhan, tran_phap, enemy
  enemies/                  realm_*.json — drop new file to add a realm, no registry change
  loot_tables/              zone_*.json + bosses, chests — drop file to add farm zone
  equipment/                bases, affixes, uniques
  formations.json, constitutions.json, dungeons.json
src/utils/
  config.py                 Pydantic Settings singleton (`settings`)
  embed_builder.py          Discord embed helpers
  localization.py           Vietnamese string helpers
```

**Key data flows:**
- Cogs receive Discord interactions → call `game/systems/` → use `db/repositories/` for persistence
- `game/models/` are runtime objects (not ORM); populated from DB rows via repositories
- `game/engine/damage/pipeline.py` is the single entry point for all damage — chains evasion → roll → crit → elemental → final_bonus
- `src/data/registry.py` exposes a module-level `registry` singleton (`GameRegistry.get()`); import and call `registry.get_item(key)` etc.

## Database Notes

- All DB access is async (SQLAlchemy 2.x + asyncpg)
- Use `get_session()` context manager from `src/db/connection.py` for all DB operations (auto-commits on exit, rolls back on exception)
- `init_db()` (`create_all`) is called in `main.py` for dev convenience — use Alembic migrations in production
- New ORM models must be imported in `src/db/connection.py` so `Base.metadata` discovers them

## Testing Notes

- Tests are synchronous by default; use `@pytest.mark.asyncio` for async tests (pytest-asyncio)
- No DB in unit tests — repositories are mocked; `src/game/models/` dataclasses are constructed directly
- `pytest-mock` is available for patching

## Game Domain Concepts

- **3-axis cultivation**: Luyện Thể (body/tank), Luyện Khí (qi/balanced), Trận Đạo (formation/mage) — 9 realms × 9 levels each
- **Turn system**: 1440 turns/day (1 turn = 1 real minute); first 440 turns = bonus (2× Công Đức, 0 Nghiệp Lực)
- **Currencies**: Công Đức (merit, main spend), Nghiệp Lực (karma, two pools: Tích Lũy accumulated + Khả Dụng usable), Hỗn Nguyên Thạch (premium, drop-only)
- **Damage formula**: `DMG = BaseSkill + MPCost` (no ATK/DEF stats — skills are the only damage source)
- **Rating formula**: `% = Rating / (Rating + 1300)` — applies to crit, evasion, crit-dmg, crit-res
- **Linh Căn (spiritual root)**: 9 elements (Kim/Mộc/Thủy/Hỏa/Thổ/Lôi/Phong/Quang/Âm), each with its own effect module in `engine/linh_can_effects/`
- **Item grades**: Hoàng < Huyền < Địa < Thiên
- **Skill types**: Thiên (attack) / Địa (defense) / Nhân (support/CC) / Trận Pháp
- **Constitution (Thể Chất)**: unlocked at Nhập Thánh Cấp 9 (`dao_ti_unlocked` flag on Character)
