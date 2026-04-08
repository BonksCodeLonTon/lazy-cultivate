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
