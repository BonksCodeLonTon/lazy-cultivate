# Lazy Cultivate

A Discord-based **Idle / AFK Turn-based RPG** set in a **Tiên Hiệp (Xianxia)** cultivation universe.

Players cultivate automatically over real time (1440 turns/day = 24 hours), fight enemies in turn-based combat, and interact through Discord slash commands.

**Hardcore progression** — 6 months to 1 year to reach the peak. No shortcuts except rare pills.

---

## Features

### Cultivation System
Three independent axes, each with 9 realms × 9 levels (81 stages per axis):

| Axis | Focus | Realms |
|------|-------|--------|
| **Luyện Thể** | Tank — high HP, low MP | Luyện Huyết → … → Nhập Thánh |
| **Luyện Khí** | Balanced | Luyện Khí → … → Đăng Tiên |
| **Trận Đạo** | Mage — low HP, high MP | Khai Huyền → … → Quy Nhất |

### Combat Engine
- No base ATK/DEF — all damage from skills: `DMG = BaseDMG + MPCost`
- Turn order by SPD stat; rating formula: `% = Rating / (Rating + 1300)`
- **42 combat effects**: 23 buffs + 19 debuffs/CC — buffs actively modify stats in combat
- 50 learnable skills in 4 types: **Thiên** (attack) / **Địa** (defense) / **Nhân** (support/CC) / **Trận Pháp**

### Linh Căn (Spiritual Roots)
9 elemental roots assigned randomly at registration, each providing passive combat procs:
`Kim · Mộc · Thủy · Hỏa · Thổ · Lôi · Phong · Quang · Âm`

### Formation System (Trận Pháp)
- 10 formations: 8 elemental Nhất Nguyên + Kiếm Trận + Cửu Cung Bát Quái
- 81 gem slots with stat thresholds at 9 / 27 / 36 / 49 / 81 gems
- Mastery path: Chân Nhân → Chân Quân → Tiên Tôn → **Đạo Tổ**

### Constitution System (Thể Chất)
12 constitutions unlocked after reaching Đạo Thể (Nhập Thánh Cấp 9):
8 elemental Bát Quái + Thái Dương + Thái Âm + Vạn Tượng + **Hỗn Độn Đạo Thể** (endgame — requires all 10 at Đạo Thể)

### Economy

| Currency | Source | Use |
|----------|--------|-----|
| **Công Đức** ✨ | Cultivation, kills, daily bonus | Breakthrough, shop, constitution |
| **Nghiệp Lực** ☯️ | Combat (Tích Lũy + Khả Dụng pools) | Dark market, evil title path |
| **Hỗn Nguyên Thạch** 💎 | Legendary boss drops only | Thiên-grade market |

Turn economy: first 440 turns/day give 2× Công Đức + 0 Nghiệp Lực (daily login incentive).

### Item System
- **4 grades**: Hoàng < Huyền < Địa < Thiên
- **Ngọc Giản** (Scrolls) — only way to learn skills
- **Pháp Bảo** (Artifacts) — 3 slots: Sword / Armor / Artifact
- **Đan Dược** (Elixirs) — HP/MP recovery, stat buffs, karma reduction
- **Ngọc Khảm** (Gems) — 17 types for formation inlay

### Shop & Trading
- **Đạo Thương**: fixed shop + 6h rotating slots + Hỗn Nguyên Thạch section
- **Quỷ Thị** (Dark Market): FOMO mechanics, 4–8h random reset
- **P2P Marketplace**: same-grade trading, 10% fee anti-inflation sink, 72h listing expiry

---

## Discord Commands

| Command | Description |
|---------|-------------|
| `/status` | View character stats, realm, currency |
| `/cultivate` | Start / resume AFK cultivation |
| `/breakthrough` | Attempt realm breakthrough |
| `/fight` | Enter turn-based combat (rank selector UI) |
| `/skills` | View equipped skills (6 slots) |
| `/skilllist [type] [element]` | Browse all 50 learnable skills |
| `/learn <scroll> <skill> [slot]` | Learn a skill from a Ngọc Giản |
| `/forget <slot>` | Remove skill from a slot |
| `/inventory [type]` | View items in bag |
| `/use <item> [qty]` | Use an elixir / consumable |
| `/equip <slot> <item>` | Equip an artifact |
| `/formation <key>` | Switch active formation |
| `/inlay <slot> <gem>` | Inlay gem into current formation |
| `/constitution <key>` | Change constitution type |
| `/shop` | Browse Đạo Thương |
| `/darkmarket` | Browse Quỷ Thị |
| `/market list/browse/buy` | P2P trading marketplace |
| `/dungeon` | Enter dungeon runs |
| `/healup` | Restore HP/MP to max |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Discord | discord.py 2.x (slash commands) |
| Database | SQLite (dev) / PostgreSQL (prod) |
| ORM | SQLAlchemy 2.x (async) |
| Migrations | Alembic |
| Task queue | APScheduler (turn ticks, shop resets) |
| Testing | pytest + pytest-asyncio |

---

## Project Structure

```
cultivation-bot/
├── main.py
├── src/
│   ├── bot/
│   │   ├── client.py
│   │   └── cogs/           # Discord command groups
│   │       ├── combat.py   # /fight /skills /skilllist /forget
│   │       ├── cultivation.py
│   │       ├── inventory.py
│   │       ├── shop.py
│   │       ├── trade.py
│   │       ├── dungeon.py
│   │       └── admin.py
│   ├── game/
│   │   ├── constants/      # Realms, elements, grades, currencies, Linh Căn
│   │   ├── engine/
│   │   │   ├── effects.py  # 42-effect registry (23 buff + 19 debuff/CC)
│   │   │   ├── damage/     # Damage pipeline (base → crit → elemental → final)
│   │   │   └── linh_can_effects/   # Per-element passive procs
│   │   ├── models/         # Character, skill, enemy, item dataclasses
│   │   └── systems/        # Combat, cultivation, economy, dungeon, trade
│   ├── data/               # Static JSON game data (skills, enemies, items …)
│   ├── db/
│   │   ├── models/         # SQLAlchemy ORM models
│   │   ├── repositories/   # Data access layer
│   │   └── migrations/     # Alembic versions
│   └── utils/              # Embed builder, localization, config
└── tests/
```

---

## Setup

```bash
git clone https://github.com/BonksCodeLonTon/lazy-cultivate.git
cd lazy-cultivate
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in DISCORD_TOKEN and DATABASE_URL
alembic upgrade head
python main.py
```

---

## Development Status

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | Foundation, DB models, turn system | ✅ Done |
| 2 | Cultivation axes, constitution, formation | ✅ Done |
| 3 | Combat engine, 50 skills, 42 effects | ✅ Done |
| 4 | Economy, items, shop, P2P trading | ✅ Done |
| 5 | Title system, endgame content | 🔲 Planned |
| 6 | Discord UX polish, leaderboard | 🔲 Planned |

---

## License

MIT
