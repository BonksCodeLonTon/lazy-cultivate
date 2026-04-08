# cultivation-bot — AI Context Map

> **Stack:** raw-http | sqlalchemy | unknown | python

> 0 routes | 7 models | 0 components | 66 lib files | 11 env vars | 0 middleware | 8 import links
> **Token savings:** this file is ~3,200 tokens. Without it, AI exploration would cost ~27,900 tokens. **Saves ~24,700 tokens per conversation.**

---

# Schema

### CharacterArtifact
- id: Integer (pk)
- player_id: Integer (fk)
- slot: String
- artifact_key: String
- _relations_: player: Player

### CharacterFormation
- id: int (pk)
- player_id: int (fk)
- formation_key: str
- formation_realm: int (default)
- formation_level: int (default)
- mastery: str | None
- gem_slots: dict (default)
- is_locked: bool (default)

### InventoryItem
- id: int (pk)
- player_id: int (fk)
- item_key: str
- grade: int
- quantity: int (default)

### MarketListing
- id: int (pk)
- seller_id: int (fk)
- item_key: str
- grade: int
- quantity: int
- price: int
- shop_ref_price: int
- currency_type: str
- expires_at: datetime

### Player
- id: Integer (pk)
- discord_id: BigInteger (unique, index)
- name: String
- body_realm: Integer (default)
- body_level: Integer (default)
- qi_realm: Integer (default)
- qi_level: Integer (default)
- formation_realm: Integer (default)
- formation_level: Integer (default)
- constitution_type: String (default)
- dao_ti_unlocked: Boolean (default)
- merit: Integer (default)
- karma_accum: Integer (default)
- karma_usable: Integer (default)
- primordial_stones: Integer (default)
- active_axis: String (default)
- body_xp: Integer (default)
- qi_xp: Integer (default)
- formation_xp: Integer (default)
- hp_current: Integer (default)
- mp_current: Integer (default)
- active_formation: String (nullable)
- linh_can: String (default)
- main_title: String (nullable)
- sub_title: String (nullable)
- evil_title: String (nullable)
- _relations_: turn_tracker: TurnTracker, inventory: InventoryItem, skills: CharacterSkill, artifacts: CharacterArtifact, formations: CharacterFormation, market_listings: MarketListing

### CharacterSkill
- id: Integer (pk)
- player_id: Integer (fk)
- skill_key: String
- slot_index: SmallInteger
- _relations_: player: Player

### TurnTracker
- player_id: int (fk)
- turns_today: int (default)
- bonus_turns_remaining: int (default)
- last_tick_at: datetime | None
- merit_bonus_expires_at: datetime | None

---

# Libraries

- `main.py` — function main: () -> None
- `src\bot\client.py` — class CultivationBot
- `src\bot\cogs\admin.py` — function setup: (bot) -> None, class AdminCog
- `src\bot\cogs\combat.py`
  - function setup: (bot) -> None
  - class FightRankView
  - class FightResultView
  - class SkillListView
  - class SkillLearnView
  - class SkillsView
  - _...1 more_
- `src\bot\cogs\cultivation.py`
  - function setup: (bot) -> None
  - class StatusView
  - class CultivateView
  - class BreakthroughView
  - class CultivationCog
- `src\bot\cogs\dungeon.py`
  - function setup: (bot) -> None
  - class DungeonSelect
  - class DungeonListView
  - class DungeonDetailView
  - class DungeonResultView
  - class DungeonCog
- `src\bot\cogs\inventory.py` — function setup: (bot) -> None, class InventoryCog
- `src\bot\cogs\shop.py`
  - function setup: (bot) -> None
  - class ShopItemSelect
  - class ShopView
  - class ShopBuyView
  - class ShopCog
- `src\bot\cogs\trade.py` — function setup: (bot) -> None, class TradeCog
- `src\data\registry.py` — class GameRegistry
- `src\db\connection.py` — function get_session: () -> AsyncGenerator[AsyncSession, None], function init_db: () -> None
- `src\db\migrations\env.py` — function run_migrations_offline: () -> None, function run_migrations_online: () -> None
- `src\db\migrations\versions\0001_initial_schema.py` — function upgrade: () -> None, function downgrade: () -> None
- `src\db\migrations\versions\0002_cultivation_xp.py` — function upgrade: () -> None, function downgrade: () -> None
- `src\db\migrations\versions\0003_linh_can.py` — function upgrade: () -> None, function downgrade: () -> None
- `src\db\models\artifact.py` — class CharacterArtifact
- `src\db\models\base.py` — class Base, class TimestampMixin
- `src\db\models\formation.py` — class CharacterFormation
- `src\db\models\inventory.py` — class InventoryItem
- `src\db\models\market.py` — class MarketListing
- `src\db\models\player.py` — class Player
- `src\db\models\skill.py` — class CharacterSkill
- `src\db\models\turn_tracker.py` — class TurnTracker
- `src\db\repositories\formation_repo.py` — class FormationRepository
- `src\db\repositories\inventory_repo.py` — class InventoryRepository
- `src\db\repositories\market_repo.py` — class MarketRepository
- `src\db\repositories\player_repo.py` — class PlayerRepository
- `src\game\constants\elements.py` — class Element
- `src\game\constants\grades.py` — class Grade
- `src\game\constants\linh_can.py`
  - function compute_linh_can_bonuses: (linh_can_list) -> dict
  - function parse_linh_can: (raw) -> list[str]
  - function format_linh_can: (linh_can_list) -> str
- `src\game\constants\realms.py` — function realm_label: (realms, realm_idx, level) -> str, class Realm
- `src\game\engine\damage\base.py` — function roll_base: (base_dmg, mp_cost, rng) -> int
- `src\game\engine\damage\critical.py` — function apply_critical: (raw, crit_rating, crit_res_rating, crit_dmg_rating, rng) -> tuple[int, bool]
- `src\game\engine\damage\elemental.py` — function apply_elemental: (dmg, element, defender_res, int], pen_pct) -> int
- `src\game\engine\damage\evasion.py` — function check_evasion: (evasion_rating, rng) -> bool
- `src\game\engine\damage\final_bonus.py` — function apply_final_bonus: (dmg, final_dmg_bonus) -> int
- `src\game\engine\damage\pipeline.py` — function calculate_damage: (skill, attacker, defender_res, int], defender_crit_res_rating, rng, pen_pct) -> DamageResult
- `src\game\engine\damage\result.py` — class DamageResult
- `src\game\engine\effects.py`
  - function default_duration: (effect_key) -> int
  - function get_combat_modifiers: (combatant) -> dict[str, float]
  - function get_periodic_damage: (combatant) -> list[tuple[str, int]]
  - function check_cc_skip_turn: (combatant, rng) -> str | None
  - function check_prevents_skills: (combatant) -> str | None
  - function format_active_effects: (combatant) -> str
  - _...2 more_
- `src\game\engine\linh_can_effects\am.py` — function on_hit: (actor, target, dmg, rng, log) -> None
- `src\game\engine\linh_can_effects\hoa.py` — function on_hit: (actor, target, dmg, rng, log) -> None
- `src\game\engine\linh_can_effects\kim.py` — function get_pen_pct: (actor, rng, log) -> float
- `src\game\engine\linh_can_effects\loi.py` — function on_hit: (actor, target, dmg, is_crit, rng, log) -> None
- `src\game\engine\linh_can_effects\phong.py` — function try_dodge: (target, rng, log) -> bool
- `src\game\engine\linh_can_effects\quang.py` — function try_cleanse: (actor, rng, log) -> None
- `src\game\engine\linh_can_effects\tho.py` — function check_shield: (combatant, log) -> None
- `src\game\engine\linh_can_effects\thuy.py` — function on_hit: (actor, target, dmg, rng, log) -> None
- `src\game\engine\rating.py`
  - function rating_to_pct: (rating) -> float
  - function crit_chance: (crit_rating, crit_res_rating) -> float
  - function evasion_chance: (evasion_rating) -> float
  - function crit_dmg_multiplier: (crit_dmg_rating) -> float
- `src\game\engine\tick.py` — function compute_offline_ticks: (character, last_tick_at) -> dict
- `src\game\models\character.py` — class CharacterStats, class Character
- `src\game\models\enemy.py`
  - class EnemyRank
  - class DropEntry
  - class EnemyTemplate
  - class EnemyInstance
- `src\game\models\item.py`
  - class ItemType
  - class ItemTemplate
  - class InventoryEntry
- `src\game\models\skill.py` — class SkillType, class Skill
- `src\game\systems\combat.py`
  - function build_player_combatant: (char, player_skill_keys, gem_count) -> Combatant
  - function build_enemy_combatant: (enemy_key, player_realm_total) -> Combatant | None
  - class CombatEndReason
  - class CombatAction
  - class CombatResult
  - class CombatSession
- `src\game\systems\combatant.py` — class Combatant
- `src\game\systems\cultivation.py`
  - function compute_hp_max: (character, bonuses) -> int
  - function compute_mp_max: (character, bonuses) -> int
  - function compute_formation_bonuses: (formation_key, gem_count) -> dict
  - function compute_constitution_bonuses: (constitution_type) -> dict
  - function merge_bonuses: (*dicts) -> dict
  - function get_breakthrough_requirements: (axis, realm) -> dict
  - _...3 more_
- `src\game\systems\dungeon.py`
  - function check_can_enter: (char, dungeon_key) -> tuple[bool, str]
  - function run_dungeon: (char, dungeon_key, skill_keys, gem_count) -> DungeonResult
  - class DungeonResult
- `src\game\systems\economy.py`
  - function get_fixed_shop: () -> list[ShopSlot]
  - function get_rotating_shop: (seed) -> list[ShopSlot]
  - function get_dark_market: (seed) -> tuple[ShopSlot, list[ShopSlot]]
  - function purchase: (player, # ORM Player
    slot, quantity) -> PurchaseResult
  - class ShopSlot
  - class PurchaseResult
- `src\game\systems\trade.py`
  - function compute_trade_fee: (shop_ref_price, quantity) -> int
  - function buyer_total_cost: (listing) -> int
  - function create_listing: (seller_id, item_key, grade, quantity, price, shop_ref_price, listing_id) -> MarketListing
  - function validate_listing: (current_listings, seller_grade, item_grade) -> tuple[bool, str]
  - class MarketListing
- `src\utils\config.py` — class Settings
- `src\utils\embed_builder.py`
  - function progress_bar: (current, maximum, length) -> str
  - function battle_embed: (wave_label, wave_idx, total_waves, player_name, player_hp, player_hp_max, player_mp, player_mp_max, enemy_name, enemy_hp, enemy_hp_max, turn, turn_log) -> discord.Embed
  - function base_embed: (title, description, color) -> discord.Embed
  - function error_embed: (message) -> discord.Embed
  - function success_embed: (message) -> discord.Embed
  - function character_embed: (player_name, stats, avatar_url) -> discord.Embed
- `src\utils\localization.py` — function load_texts: (data) -> None, function t: (key, lang) -> str
- `tests\test_cultivation.py`
  - function make_char: () -> Character
  - function test_cannot_breakthrough_before_level_9: ()
  - function test_can_breakthrough_at_level_9: ()
  - function test_breakthrough_advances_realm: ()
  - function test_dao_ti_unlocked_on_body_breakthrough_nhap_thanh: ()
  - function test_hp_increases_with_body_level: ()
  - _...1 more_
- `tests\test_economy.py`
  - function make_char: () -> Character
  - function test_bonus_turns_give_more_merit: ()
  - function test_normal_turns_give_karma: ()
  - function test_merit_capped: ()
  - function test_evil_title_at_threshold: ()
- `tests\test_rating.py`
  - function test_rating_to_pct_example: ()
  - function test_crit_chance_with_zero_rating: ()
  - function test_crit_chance_with_res_reduces: ()
  - function test_crit_chance_capped_at_75: ()
  - function test_evasion_zero: ()
  - function test_crit_dmg_base: ()
- `tests\test_trade.py`
  - function test_trade_fee_10_pct: ()
  - function test_buyer_total_cost: ()
  - function test_validate_listing_max_exceeded: ()
  - function test_validate_listing_grade_mismatch: ()
  - function test_validate_listing_ok: ()

---

# Config

## Environment Variables

- `DATABASE_URL` **required** — src\db\migrations\env.py
- `DB_HOST` (has default) — .env.example
- `DB_NAME` (has default) — .env.example
- `DB_PASSWORD` (has default) — .env.example
- `DB_PORT` (has default) — .env.example
- `DB_USER` (has default) — .env.example
- `DEBUG` (has default) — .env.example
- `DISCORD_GUILD_ID` (has default) — .env.example
- `DISCORD_TOKEN` (has default) — .env.example
- `GUILD_ID` **required** — src\bot\client.py
- `LOG_LEVEL` (has default) — .env.example

## Config Files

- `.env.example`

---

# Dependency Graph

## Most Imported Files (change these carefully)

- `/result.py` — imported by **2** files
- `/base.py` — imported by **1** files
- `/critical.py` — imported by **1** files
- `/elemental.py` — imported by **1** files
- `/evasion.py` — imported by **1** files
- `/final_bonus.py` — imported by **1** files
- `/pipeline.py` — imported by **1** files

## Import Map (who imports what)

- `/result.py` ← `src\game\engine\damage\pipeline.py`, `src\game\engine\damage\__init__.py`
- `/base.py` ← `src\game\engine\damage\pipeline.py`
- `/critical.py` ← `src\game\engine\damage\pipeline.py`
- `/elemental.py` ← `src\game\engine\damage\pipeline.py`
- `/evasion.py` ← `src\game\engine\damage\pipeline.py`
- `/final_bonus.py` ← `src\game\engine\damage\pipeline.py`
- `/pipeline.py` ← `src\game\engine\damage\__init__.py`

---

_Generated by [codesight](https://github.com/Houseofmvps/codesight) — see your codebase clearly_