# Database

> **Navigation aid.** Schema shapes and field types extracted via AST. Read the actual schema source files before writing migrations or query logic.

**sqlalchemy** — 7 models

### CharacterArtifact

pk: `id` (Integer) · fk: player_id

- `id`: Integer _(pk)_
- `player_id`: Integer _(fk)_
- `slot`: String
- `artifact_key`: String
- _relations_: player: Player

### CharacterFormation

pk: `id` (int) · fk: player_id

- `id`: int _(pk)_
- `player_id`: int _(fk)_
- `formation_key`: str
- `formation_realm`: int _(default)_
- `formation_level`: int _(default)_
- `mastery`: str | None
- `gem_slots`: dict _(default)_
- `is_locked`: bool _(default)_

### InventoryItem

pk: `id` (int) · fk: player_id

- `id`: int _(pk)_
- `player_id`: int _(fk)_
- `item_key`: str
- `grade`: int
- `quantity`: int _(default)_

### MarketListing

pk: `id` (int) · fk: seller_id

- `id`: int _(pk)_
- `seller_id`: int _(fk)_
- `item_key`: str
- `grade`: int
- `quantity`: int
- `price`: int
- `shop_ref_price`: int
- `currency_type`: str
- `expires_at`: datetime

### Player

pk: `id` (Integer)

- `id`: Integer _(pk)_
- `discord_id`: BigInteger _(unique, index)_
- `name`: String
- `body_realm`: Integer _(default)_
- `body_level`: Integer _(default)_
- `qi_realm`: Integer _(default)_
- `qi_level`: Integer _(default)_
- `formation_realm`: Integer _(default)_
- `formation_level`: Integer _(default)_
- `constitution_type`: String _(default)_
- `dao_ti_unlocked`: Boolean _(default)_
- `merit`: Integer _(default)_
- `karma_accum`: Integer _(default)_
- `karma_usable`: Integer _(default)_
- `primordial_stones`: Integer _(default)_
- `active_axis`: String _(default)_
- `body_xp`: Integer _(default)_
- `qi_xp`: Integer _(default)_
- `formation_xp`: Integer _(default)_
- `hp_current`: Integer _(default)_
- `mp_current`: Integer _(default)_
- `active_formation`: String _(nullable)_
- `linh_can`: String _(default)_
- `main_title`: String _(nullable)_
- `sub_title`: String _(nullable)_
- `evil_title`: String _(nullable)_
- _relations_: turn_tracker: TurnTracker, inventory: InventoryItem, skills: CharacterSkill, artifacts: CharacterArtifact, formations: CharacterFormation, market_listings: MarketListing

### CharacterSkill

pk: `id` (Integer) · fk: player_id

- `id`: Integer _(pk)_
- `player_id`: Integer _(fk)_
- `skill_key`: String
- `slot_index`: SmallInteger
- _relations_: player: Player

### TurnTracker

fk: player_id

- `player_id`: int _(fk)_
- `turns_today`: int _(default)_
- `bonus_turns_remaining`: int _(default)_
- `last_tick_at`: datetime | None
- `merit_bonus_expires_at`: datetime | None

## Schema Source Files

Search for ORM schema declarations:
- Drizzle: `pgTable` / `mysqlTable` / `sqliteTable`
- Prisma: `prisma/schema.prisma`
- TypeORM: `@Entity()` decorator
- SQLAlchemy: class inheriting `Base`

---
_Back to [overview.md](./overview.md)_