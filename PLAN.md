# Discord Cultivation Bot — Game Design & Implementation Plan

## Overview

A Discord-based **Idle / AFK Turn-based RPG** set in a **Tiên Hiệp (Xianxia)** cultivation universe.
Players cultivate their characters automatically over real time (1440 turns/day = 24 hours), fight enemies
in turn-based combat when encountered, and interact through Discord slash commands.

**Hardcore progression**: 6 months – 1 year to max all systems. No shortcuts except special pills purchased
with in-game currency.

---

## System Architecture

```
cultivation-bot/
├── PLAN.md                     # This file
├── README.md
├── requirements.txt
├── .env.example
├── main.py                     # Bot entry point
├── src/
│   ├── bot/                    # Discord bot layer
│   │   ├── __init__.py
│   │   ├── client.py           # Bot client setup
│   │   └── cogs/               # Discord command groups (Cogs)
│   │       ├── cultivation.py  # /cultivate, /status, /breakthrough
│   │       ├── combat.py       # /fight, /skills, /flee
│   │       ├── inventory.py    # /inventory, /use, /equip
│   │       ├── shop.py         # /shop, /buy, /sell
│   │       ├── trade.py        # /market, /list, /purchase
│   │       └── admin.py        # Admin commands
│   ├── game/                   # Core game logic
│   │   ├── __init__.py
│   │   ├── constants/
│   │   │   ├── realms.py       # Cultivation realms (9×9 per axis)
│   │   │   ├── elements.py     # 8 elements: Kim/Mộc/Thủy/Hỏa/Thổ/Lôi/Băng/Phong
│   │   │   ├── grades.py       # Item grades: Hoàng/Huyền/Địa/Thiên
│   │   │   └── currencies.py   # Công Đức, Nghiệp Lực, Hỗn Nguyên Thạch
│   │   ├── models/
│   │   │   ├── character.py    # Player character model
│   │   │   ├── enemy.py        # Enemy model (4 ranks)
│   │   │   ├── item.py         # Item model with grade system
│   │   │   ├── skill.py        # Skill model (50 skills, 4 types)
│   │   │   ├── artifact.py     # Pháp Bảo (3 slots)
│   │   │   └── formation.py    # Trận Pháp (10 formations, 81 gem slots)
│   │   ├── systems/
│   │   │   ├── cultivation.py  # 3-axis cultivation: Luyện Thể / Luyện Khí / Trận Đạo
│   │   │   ├── combat.py       # Turn-based combat engine
│   │   │   ├── economy.py      # Currency, turn system (1440/day)
│   │   │   ├── shop.py         # Shop logic (Đạo Thương + Quỷ Thị)
│   │   │   ├── trade.py        # P2P trading marketplace
│   │   │   ├── titles.py       # Danh Hiệu system
│   │   │   └── constitution.py # Thể Chất system (12 types)
│   │   └── engine/
│   │       ├── effects.py      # Combat effects (23 buff + 19 debuff/CC)
│   │       ├── damage.py       # Damage formula: DMG = Base Skill + MP Cost
│   │       ├── rating.py       # Rating → % formula (Crit/Evasion/CritDmg/CritRes)
│   │       └── tick.py         # Offline tick processor (AFK calculation)
│   ├── data/                   # Static game data (loaded from JSON/YAML)
│   │   ├── skills.json
│   │   ├── enemies.json
│   │   ├── items.json
│   │   ├── formations.json
│   │   ├── constitutions.json
│   │   ├── elixirs.json
│   │   ├── artifacts.json
│   │   └── titles.json
│   ├── db/                     # Database layer
│   │   ├── __init__.py
│   │   ├── connection.py       # DB connection (PostgreSQL / SQLite)
│   │   ├── migrations/         # DB schema migrations
│   │   └── repositories/
│   │       ├── player_repo.py
│   │       ├── inventory_repo.py
│   │       └── market_repo.py
│   └── utils/
│       ├── embed_builder.py    # Discord embed formatting
│       ├── localization.py     # VI/EN/CN text (from LocalText sheet)
│       └── validators.py       # Input validation
├── tests/
│   ├── test_combat.py
│   ├── test_cultivation.py
│   ├── test_economy.py
│   └── test_trade.py
└── docs/
    └── game-design.md          # Detailed game design reference
```

---

## Phase 1 — Foundation (Week 1–2)

### 1.1 Project Setup
- [ ] Init Python project with `discord.py` (v2.x) + `python-dotenv`
- [ ] Setup database (SQLite for dev, PostgreSQL for prod)
- [ ] Create bot client with basic slash command framework
- [ ] Implement localization system (VI primary, EN secondary)

### 1.2 Core Data Models
- [ ] **Character model**: stats (HP, MP, SPD), currencies, realm levels per axis
- [ ] **Cultivation Realms**: 3 axes × 9 realms × 9 levels = 243 stages each
  - Luyện Thể: Luyện Huyết → Luyện Bì → Luyện Cân → Luyện Cốt → Luyện Phủ → Pháp Tướng → Kim Thân → Siêu Phàm → Nhập Thánh
  - Luyện Khí: Luyện Khí → Trúc Cơ → Kim Đan → Nguyên Anh → Hóa Thần → Luyện Hư → Hợp Đạo → Đại Thừa → Đăng Tiên
  - Trận Đạo: Khai Huyền → Nhập Huyền → Luyện Huyền → Dung Huyền → Hóa Huyền → Thông Huyền → Động Huyền → Chí Huyền → Quy Nhất
- [ ] **Item model**: 4 grades (Hoàng/Huyền/Địa/Thiên), type enum
- [ ] **Effects registry**: 52 total (23 buff + 19 debuff/CC + special)

### 1.3 Turn System (Economy Core)
- [ ] 1440 turns/day (= real 24 hours, 1 turn = 1 minute)
- [ ] First 440 turns: Công Đức ×2, 0 Nghiệp Lực → incentivize daily login
- [ ] Remaining 1000 turns: +3 Công Đức +7 Nghiệp Lực per turn
- [ ] Currency caps: 10,000,000 per type → forces spending
- [ ] Offline tick calculation when player reconnects

---

## Phase 2 — Cultivation Systems (Week 3–4)

### 2.1 Three-Axis Cultivation
- [ ] Inverse stat distribution across axes:
  - Luyện Thể: 50% HP, 16% MP (tank focus)
  - Luyện Khí: 34% HP, 31% MP (balanced)
  - Trận Đạo: 16% HP, 52% MP (mage focus) — costs Công Đức per level
- [ ] Realm breakthrough mechanic (requires materials + Công Đức)
- [ ] **Đạo Thể** unlock: Nhập Thánh Cấp 9 + breakthrough → unlocks Constitution system

### 2.2 Constitution System (Thể Chất)
- [ ] 12 constitutions: 8 Bát Quái + Thái Dương + Thái Âm + Vạn Tượng + Hỗn Độn Đạo Thể
- [ ] Vạn Tượng free to change; others cost 50,000 Công Đức
- [ ] Passive unlocks only after reaching Đạo Thể
- [ ] **Hỗn Độn Đạo Thể** (endgame): requires ALL 10 constitutions at Đạo Thể (no early Thái Cực)
  → unlocks all passives ×1.5

### 2.3 Formation System (Trận Pháp)
- [ ] 10 formations: 8 elemental Nhất Nguyên + Kiếm Trận + Cửu Cung Bát Quái Trận
- [ ] 81 gem slots per formation with bonus thresholds: 9/27 → stat; 36/49/81 → stat + effects
- [ ] Only 1 active formation at a time; switching locks old formation (progress saved)
- [ ] Mastery progression: Chân Nhân → Chân Quân → Tiên Tôn → Đạo Tổ
  - Đạo Tổ: treats all 81 slots as inlaid + passive ×1.5

---

## Phase 3 — Combat System (Week 5–6)

### 3.1 Turn-Based Combat Engine
- [ ] Idle auto-encounter enemies; combat resolves per turn
- [ ] SPD stat determines action order
- [ ] **No base ATK/DEF** — damage entirely from skills
- [ ] Damage formula: `DMG = BaseSkill + MPCost` (MP cost is flat, not %)
  - MP cost range: 2–81 points per skill
- [ ] Defense via elemental resistance and shields only

### 3.2 Rating System
- [ ] 4 rating stats: CritRating, CritDmgRating, EvasionRating, CritResRating
- [ ] Formula: `% = Rating / (Rating + K)` where K = 1300 (soft cap)
- [ ] Example: 300 CritRating → 300 / (300 + 1300) = 18.75% crit chance

### 3.3 Skill System (50 skills)
- [ ] 4 types: Thiên (attack) / Địa (defense) / Nhân (support/CC) / Trận Pháp (formation-specific)
- [ ] Skills unlocked via Ngọc Giản (Scrolls) only — no level-based unlock
- [ ] No skill grades — differentiated by MP cost and cooldown

### 3.4 Enemy System
- [ ] 4 ranks: Phổ Thông < Cường Giả < Đại Năng < Chí Tôn
- [ ] Stats scale ±1 realm relative to player's realm
- [ ] Drop table with weight system per item
- [ ] 8 elemental affinities matching the element system

### 3.5 Combat Effects
- [ ] **23 Buffs**: Kiếm Khí, Kiếm Ý, Vô Ngã Kiếm Tâm, Nhiệt Tình, Hỏa Thần Giáng Lâm, Liệt Diệm Hộ Thể, Băng Giáp, Thủy Kính Thân, Hàn Khí Tỏa Thân, Lôi Thần Giáng, Tốc Lôi, Ngự Phong, Phong Vũ Tương Hòa, Sinh Cơ Sung Mãn, Căn Cơ Bất Động, Kim Cương Thể, Hoàng Kim Hộ, Đại Địa Thần Hộ, Trọng Thổ, Bất Tử, Tăng Tốc, Hộ Pháp, Hư Không Thân
- [ ] **19 Debuff/CC**: Thiêu Đốt, Tê Liệt, Đốt Cháy Nội Tạng, Độc Tố, Bào Mòn, Trói Buộc, Lún Đất, Làm Chậm, Đóng Băng, Chảy Máu, Phá Giáp, Xé Rách, Cuốn Bay, Cắt Đứt Linh Khí, Câm Lặng, Choáng, Ngắt Kỹ Năng, Khóa Đột Phá, Sét Đánh
- [ ] Stack mechanics for applicable effects

---

## Phase 4 — Economy & Items (Week 7–8)

### 4.1 Currency System
- [ ] **Công Đức** (merit): earned from cultivation, kills, exploration, quests → used at Đạo Thương + breakthrough
- [ ] **Nghiệp Lực** (karma): two-pool system
  - Tích Lũy (accumulated): cap 500,000 — triggers evil titles at thresholds
  - Khả Dụng (usable): separate pool — spending doesn't reduce accumulation
  - Evil title path: Vạn Ác → Vô Gian → Cửu U Ma Tôn → Diệt Thế Ma Thần (+20% FinalDmg but heavy debuffs)
- [ ] **Hỗn Nguyên Thạch**: only from legendary boss drops or P2P trading → premium market item

### 4.2 Item System
- [ ] 4 grades: Hoàng < Huyền < Địa < Thiên
- [ ] **Cultivation materials**: 9 types for Luyện Thể, 9 for Luyện Khí, 2 for Thể Chất
- [ ] **Gems (Ngọc Khảm)**: 17 types for formation inlay (elemental + special)
- [ ] **Scrolls (Ngọc Giản)**: 13 types (4 grades × 3 types + Formation) — only skill unlock method
- [ ] **Artifacts (Pháp Bảo)**: 3 slots — Sword / Shield-Armor / Artifact-Instrument (29 total, no stats, only passives/skills)
- [ ] **Elixirs (Đan Dược)**: 36 types across 7 groups — HP recovery, MP recovery, dual recovery, stat buff, CC/debuff, cultivation, special

### 4.3 Shop System
- [ ] **Đạo Thương** (3 sections):
  - Gian Cố Định: 13 base items, no reset, Công Đức currency
  - Gian Luân Chuyển: 6–8 random slots, reset every 6h, Công Đức
  - Gian Hỗn Nguyên: 3–5 Thiên grade slots, reset every 24h, Hỗn Nguyên Thạch
- [ ] **Quỷ Thị** (dark market):
  - 1 fixed slot: Thiên Đạo Phù Nghịch (99,000 KD) = ×2 Công Đức for 30 days
  - 5–8 random slots (elixirs/CC/rare materials), random reset 4–8h (FOMO mechanic)

### 4.4 P2P Trading
- [ ] Same-grade only: Hoàng↔Hoàng, Huyền↔Huyền, etc.
- [ ] Seller sets price; buyer pays price + 10% fee (based on shop reference price × quantity)
- [ ] Fee goes to system sink (Công Đức drain → anti-inflation)
- [ ] Max 5 active listings per player; listings expire after 72 hours
- [ ] Thiên grade paid in Hỗn Nguyên Thạch; others in Công Đức

---

## Phase 5 — Title System & Endgame (Week 9–10)

### 5.1 Title System (Danh Hiệu)
- [ ] Equip: 1 Main Title + 1 Sub Title simultaneously
- [ ] **4 title sources**:
  1. Formation mastery (Main): Chân Nhân → Chân Quân → Tiên Tôn → Đạo Tổ
  2. Công Đức accumulation (Sub/Main): Thiện Nhân → ... → Chứng Đạo Thành Tiên
  3. Nghiệp Lực accumulation (auto, unremovable): Vạn Ác Bất Xá → Vô Gian → Cửu U Ma Tôn → Diệt Thế Ma Thần
  4. Cultivation realm milestones: Nhập Thánh → Đăng Tiên → Hỗn Độn Khai Tịch
- [ ] Evil titles: heavy debuffs + hidden bonus (Diệt Thế Ma Thần: FinalDmg +20%) → "demon path" build

### 5.2 Endgame Content
- [ ] Legendary boss fights (drop Hỗn Nguyên Thạch)
- [ ] Hỗn Độn Đạo Thể unlock quest (all 10 constitutions at Đạo Thể)
- [ ] Server-wide leaderboard: realm ranking, Công Đức ranking, kill count

---

## Phase 6 — Discord UX & Polish (Week 11–12)

### 6.1 Discord Commands (Slash Commands)
| Command | Description |
|---------|-------------|
| `/status` | View character stats, realm, currency |
| `/cultivate` | Start/resume cultivation session |
| `/breakthrough` | Attempt realm breakthrough |
| `/fight` | Enter combat encounter |
| `/skills` | View/manage skill loadout |
| `/inventory` | View items |
| `/use <item>` | Use elixir/consumable |
| `/equip <slot> <item>` | Equip artifact to slot |
| `/formation <name>` | Switch active formation |
| `/inlay <slot> <gem>` | Inlay gem into formation slot |
| `/shop` | Browse Đạo Thương |
| `/darkmarket` | Browse Quỷ Thị |
| `/market list <item> <qty> <price>` | List item on P2P market |
| `/market browse [grade]` | Browse P2P listings |
| `/market buy <id>` | Buy P2P listing |
| `/constitution` | View/change constitution |
| `/titles` | View/equip titles |
| `/leaderboard` | Server rankings |

### 6.2 Embeds & UX
- [ ] Rich Discord embeds with color coding per element
- [ ] Progress bars for cultivation levels
- [ ] Combat log in collapsible embed fields
- [ ] Daily login reminder (DM or channel ping)

---

## Technical Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.11+ |
| Discord | discord.py 2.x (slash commands) |
| Database | SQLite (dev) / PostgreSQL (prod) |
| ORM | SQLAlchemy 2.x (async) |
| Task queue | APScheduler (turn ticks, shop resets) |
| Cache | Redis (optional, for hot data) |
| Config | python-dotenv + Pydantic Settings |
| Testing | pytest + pytest-asyncio |

---

## Database Schema (Core Tables)

```sql
-- Players
players (id, discord_id, name, constitution_type, created_at)

-- Character Stats
character_stats (player_id, hp_max, mp_max, spd,
                 body_realm, body_level,
                 qi_realm, qi_level,
                 formation_realm, formation_level,
                 merit, karma_accum, karma_usable, primordial_stones)

-- Active Formation
character_formations (player_id, formation_id, mastery_level,
                      slot_data JSON, is_active)

-- Inventory
inventory (id, player_id, item_key, quantity, grade)

-- Skills
character_skills (player_id, skill_key, slot_index)

-- Artifacts
character_artifacts (player_id, slot, artifact_key)

-- Market Listings
market_listings (id, seller_id, item_key, grade, quantity,
                 price, currency_type, expires_at, created_at)

-- Titles
character_titles (player_id, main_title, sub_title)

-- Turn Tracking
turn_tracker (player_id, turns_today, last_tick_at, bonus_ends_at)
```

---

## Key Design Principles

1. **No ATK/DEF/EXP/Linh Thạch** — replaced by skill-based DMG, elemental resistance, Công Đức
2. **Nghiệp Lực is bidirectional** — not just "bad currency"; high accumulation unlocks demon path with unique rewards
3. **Inverse stat distribution** across 3 cultivation axes gives each path a distinct combat identity
4. **P2P trading creates natural economy** — Hỗn Nguyên Thạch can't be farmed normally → organic market
5. **10% trade fee = Công Đức sink** — prevents inflation when market is active
6. **Hỗn Độn Đạo Thể** — endgame reward only for true hardcore players (all 10 constitutions at Đạo Thể)
