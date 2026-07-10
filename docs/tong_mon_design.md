# Tông Môn (Sect) Module — Design & Implementation Plan

> Status: **DESIGN v2 · ALL 6 PHASES IMPLEMENTED** (2026-07-09). Locked: creation gate = any-axis
> realm ≥ 3; Tụ Linh Trận +3%/level; donation cap scales with sect level; sect boss = new
> enemy family; **Kho Tàng shared storage** and **Đại Chiến Khoáng Mạch mine wars** in scope.
> Numbers marked ⚙ are tunable knobs; everything else is a structural decision.
> Phase 1 shipped: migration `0021_sects.py`, `game/systems/sect.py`, `db/repositories/sect_repo.py`,
> `bot/cogs/tong_mon.py`, status `[TAG]`, admin moderation, `tests/test_sect.py`.
> Phase 2 shipped: `src/data/sects/facilities.json`, `Character.sect_buffs` +
> `sect.attach_sect_buffs` plumbing (offline tick · formation study · alchemy craft),
> `upgrade_facility_atomic`, `/tongmon nangcap`, `tests/test_sect_facilities.py`.
> Phase 3 shipped: migration `0022_sect_storage.py`, deposit/request/approve atomics
> (raw-grade inventory round-trip), Kho Tàng gated to sect L2 via `min_sect_level`,
> `tests/test_sect_storage.py`.
> Phase 4 shipped: migration `0023_sect_shop_purchases.py`, `sect_shop.py` (fixed/rotating/
> scroll shelves, crc32 weekly rotation) + `sect_missions.py` (4 auto-tracked events),
> `/tongmon diemdanh·nhiemvu` + `cuahang xem|mua`, storage restructured into the `kho`
> subgroup (`/tongmon kho xem|gui|xin|huyxin|duyet`), `tests/test_sect_shop_missions.py`.
> Phase 5 shipped: migration `0024_sect_boss.py`, new enemy family `sects/sect_bosses.json`
> (Trấn Sơn Thạch Quỷ L3 / Hắc Phong Lang Vương L6 / Cửu U Huyết Giao L9), `sect_boss.py` +
> `sect_boss_repo.py` (atomic damage clone, 3 attacks/wk, 10% cap), `/tongmon boss·bxh`,
> 5-min expiry sweeper, `tests/test_sect_boss.py`. ⚙ HP pools launch-soft (1.5M/6M/15M) —
> retune from live damage data after the first weeks.
> Phase 6 shipped: migration `0025_sect_mines.py`, `sects/mines.json` (9 mines, 4 tiers,
> per-mine NPC garrisons ⚙ 0.8M–12M HP launch-soft), `sect_mine.py` + `sect_mine_repo.py`
> (declaration gauntlet, per-attempt/point atomics, garrison siege w/ instant capture,
> window resolution, whole-hour payout accrual lazy + sweep), `/tongmon mo
> xem|tuyenchien|xuatchinh|chienbao`, `tests/test_sect_mine.py`. Lock-order contract:
> sect→mine and war→mine.

---

## 1. Vision

A social + long-term progression layer: players band into **Tông Môn** (sects), donate
Công Đức to grow the sect, earn **Cống Hiến** (contribution points), unlock shared
**facilities**, pool items in a **shared storage**, fight a weekly **sect boss**, and wage
war over **linh thạch mines** that pay the sect treasury hourly.

Three problems this solves:

1. **No merit sink at scale.** Passive income is ~3,880 Công Đức/day AFK (440×2 + 1,000×3),
   more with active play and the ×2 buff. Sect funds + 5 facility ladders ≈ **19M sunk
   merit per sect** — a multi-month collective sink.
2. **No social glue.** Trade and arena are transactional; world boss is parallel-solo.
   Sects add identity (`[TAG]`), shared goals, item pooling, officer roles.
3. **No group prestige ladder.** Sect level, weekly boss ranking, and mine ownership create
   server-visible competition without touching individual combat balance.

**Core loop:** join sect → check-in + donate + missions → Cống Hiến (personal) + EXP/funds
(sect) → sect levels up → facilities + storage + shop improve → weekly boss + mine wars pay
out → repeat.

---

## 2. Scope decisions (locked)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Sect scope | **Bot-global** (not per-Discord-server) | `players` is global by `discord_id`. |
| D2 | Combat **stat buffs** from facilities | **None in v1** — utility only | Protects the combat balance envelope (`constitution_balance_bands.json`, power bench). Mine wars use players' *existing* builds — no new stats granted. |
| D3 | Cống Hiến on leaving | **Wiped**; ⚙ 24 h rejoin cooldown | Anti sect-hopping. Deposited storage items also stay with the sect. |
| D4 | Sect war form | **Đại Chiến Khoáng Mạch** (mine occupation) — Phase 6 | Replaces the earlier generic-war sketch, per user direction. |
| D5 | Membership storage | `sect_members.player_id UNIQUE` row; no column on `players` | Hot model untouched; no row = sect-less (dormant path free). |
| D6 | Shared storage item domain | **Stackable registry items only** in v1 (materials, pills, chests, scrolls, tinh huyết…) | Equipment `item_instances` need ownership-transfer surgery (nullable `player_id`, cascade changes); `direct_trade` already moves gear. Instance support = listed follow-up. |
| D7 | Mine occupancy cap | ⚙ **1 mine per sect** (config) | Keeps every mine contested; prevents the #1 sect sweeping the map. |

**User-locked answers (v2):** creation gate = **any axis** realm index ≥ 3 + ⚙ 200k merit fee ·
Tụ Linh Trận = **+3%/level** · donation cap **scales with sect level** · sect boss = **new
enemy family** (not a reskin).

---

## 3. Player-facing design

### 3.1 Creation & lifecycle

- `/tongmon tao <tên> <tag>` — ⚙ **200,000 Công Đức** (burned), requires
  **realm index ≥ 3 on ANY axis** (`max(body_realm, qi_realm, formation_realm) ≥ 3`).
- Name 3–32 chars (NFC-normalized, unique case-insensitive); tag 2–6 chars unique, shown
  as `[TAG]`. Admin `/admin tongmon rename|disband|transfer` handles abuse.
- Founder = **Tông Chủ**; sect starts L1 / 0 EXP / 0 funds / facilities L0.
- **Disband**: Tông Chủ only, modal requires typing sect name; funds/facilities/storage
  evaporate. **Transfer** before leaving is mandatory for Tông Chủ.
- Leader inactivity: v1 admin-only transfer; auto-succession (top-contribution Trưởng Lão
  after ⚙ 14 idle days) is a follow-up.

### 3.2 Membership & ranks

| Rank | Key | Cap | Permissions |
|------|-----|-----|-------------|
| Tông Chủ | `tong_chu` | 1 | everything |
| Trưởng Lão | `truong_lao` | ⚙ 2 + level//3 | approve joins, kick CS/ĐT, upgrade facilities, announcement, **approve storage requests**, **declare mine war** |
| Chấp Sự | `chap_su` | ⚙ 4 | approve joins, kick ĐT, **approve storage requests** |
| Đệ Tử | `de_tu` | rest | donate, shop, check-in, missions, boss, war attacks, storage deposit/request |

- Member cap ⚙ `10 + 2 × (sect_level − 1)` → 10 at L1, 28 at L10.
- Join via application (`xinvao`, ≤ ⚙ 3 pending/player); officer review UI; double-approve
  settled by `UNIQUE(player_id)`.
- Leave/kick deletes the member row (CH + storage claims gone) + ⚙ 24 h rejoin cooldown.

### 3.3 Donation & Cống Hiến economy

`/tongmon quyengop <số>` — split per 1,000 Công Đức donated (⚙ rates):
**+1,000 sect funds · +100 sect EXP · +100 Cống Hiến** to donor.

- **Daily donation cap scales with sect level** (user-locked): ⚙
  `4,000 + 1,000 × sect_level` → **5k/day at L1 → 14k/day at L10** (UTC-date reset,
  mirrors `tick.py`). Higher-level sects open bigger pipes — growth compounds.
- Daily check-in `/tongmon diemdanh`: ⚙ +50 CH, +100 sect EXP.
- Missions (Phase 4): ~⚙ 150–300 CH/day. Active-member ceiling ≈ 700–1,700 CH/day by level.
- Cống Hiến is per-member, sect-shop-only, non-tradable.

### 3.4 Sect level curve

⚙ `src/data/sects/sect_levels.json` — EXP, member cap, donation cap, unlocks per level:

| Level | EXP to next | Cumul. | Donation cap/day | Unlocks |
|------:|------------:|-------:|-----------------:|---------|
| 1 | 30,000 | 0 | 5,000 | shop fixed slots, check-in |
| 2 | 60,000 | 30k | 6,000 | **Kho Tàng buildable**, missions |
| 3 | 110,000 | 90k | 7,000 | **sect boss** |
| 4 | 200,000 | 200k | 8,000 | **Đại Chiến Khoáng Mạch** (declare wars) |
| 5 | 300,000 | 400k | 9,000 | rotating shop slots |
| 6 | 400,000 | 700k | 10,000 | — |
| 7 | 550,000 | 1.1M | 11,000 | — |
| 8 | 750,000 | 1.65M | 12,000 | — |
| 9 | 1,100,000 | 2.4M | 13,000 | — |
| 10 | — (max) | 3.5M | 14,000 | emblem tier |

Facility max level = sect level throughout. Velocity: 15 active members ≈ 15–20k EXP/day
→ L3 ≈ 1 week, L5 ≈ 1 month, L10 ≈ 7–8 months (mine income accelerates late levels). ⚙

### 3.5 Facilities — 5 ladders, utility only (per D2)

Upgrades spend **sect funds**; officer+; `facility_level < sect_level`. ⚙ Shared cost
table per level: 20k · 40k · 80k · 140k · 220k · 320k · 450k · 620k · 830k · 1.1M
(≈ 3.82M per facility; **5 facilities ≈ 19.1M** total sink).

| Facility | Effect per level (⚙) | Max (L10) | Hook |
|----------|---------------------|-----------|------|
| **Tụ Linh Trận** | **+3% cultivation speed** (user-locked) | **+30%** | additive in `cultivation.cultivation_speed_mult` (same slot as constitution bonus). Note: +30% is deliberate endgame payoff for ~3.8M funds; if it later distorts realm pacing, cap the facility at L5 (+15%) in data — no code change. |
| **Luyện Đan Phòng** | +0.5% pill success | +5% | alchemy success roll |
| **Tàng Kinh Các** | L1: G1 scrolls for CH; L3: G2; L6+: ⚙ 10–20% CH discount | — | sect-shop catalog filter |
| **Tụ Bảo Các** | +1 rotating shop slot / 2 levels; better pool L5+ | +5 slots | shop rotation builder |
| **Kho Tàng** | +5 storage slots (base 15 at L1) | 65 slots | storage capacity gate |

No combat stats anywhere ⇒ `tests/golden/compute_combat_stats.json` + power bench stay
byte-identical in every phase.

### 3.6 Kho Tàng — shared sect storage (Phase 3)

Pooled item bank: any member deposits; **withdrawals require officer approval** (user-spec).

- **Deposit** `/tongmon guikho <item> <số>` — free, instant, irreversible (items become
  sect property; leaving doesn't refund). Stackable registry items only (D6); one slot per
  `item_key`, stack cap ⚙ 9,999; slot count from Kho Tàng facility level.
- **Request** `/tongmon xinkho <item> <số>` — files a pending request. Limits: ⚙ 2 pending
  + ⚙ 5 approved withdrawals per member per week (anti-drain). Nothing is reserved —
  stock is checked at approval time.
- **Review** `/tongmon duyetkho` — Chấp Sự+ approve/reject via button UI.
  **No self-approval** (another officer must approve an officer's request; Tông Chủ may
  self-approve ⚙ — default no, keep symmetric). Approve = atomic: lock storage row →
  verify stock → deduct → grant to requester inventory → mark request → `sect_logs` entry.
  Insufficient stock at approve-time → approval fails gracefully, request stays pending.
- Requests auto-expire ⚙ 72 h (lazy); kicked/leaving members' requests are cascade-removed.
- `/tongmon kho` — paginated storage view with per-item request buttons.
- Every deposit/approve/reject is logged — the log IS the anti-collusion audit trail.
- **Follow-up (not v1):** equipment `item_instances` deposits (needs nullable
  `item_instances.player_id` + holder semantics).

### 3.7 Sect shop — Tàng Bảo Các (Phase 4)

`/tongmon cuahang`, priced in Cống Hiến, catalog in `src/data/sects/sect_shop.json`:
fixed slots (elixirs ⚙ 50–150 CH, `ChestHoang` ⚙ 400 CH, weekly-limited `ChestHuyen`
⚙ 1,200 CH) + rotating slots from Tụ Bảo Các (mid pills, `ChestDia` ⚙ 4,000 CH, furnace
chests, Tinh Huyết pity bundles ⚙) + scroll rows gated by Tàng Kinh Các
(⚙ `shop_price_merit / 10` CH). Atomic CH deduction on member row + inventory grant.

### 3.8 Missions — Tông Môn Nhiệm Vụ (Phase 4)

3 daily missions/member, auto-tracked via `sect_missions.record_event(session, player_id,
event_key)` called from ⚙ 4 sites — dungeon clear, world-boss attack, alchemy craft, arena
duel. No-op for sect-less players. Progress in `sect_members.mission_progress` JSON, lazy
UTC reset. Rewards ⚙ +50–100 CH, +100–200 sect EXP per mission. No event bus — 4 explicit
call sites.

### 3.9 Sect boss — Trấn Sơn Thú (Phase 5)

Weekly co-op guardian, unlocked at sect L3. **New enemy family** (user-locked):
`src/data/sects/sect_bosses.json` — per-sect-level kits, new art/flavor, not a realm-boss
reskin.

- Lazy spawn on first `/tongmon boss` open per ISO week (`week_key = "2026-W28"`);
  a `tasks.loop` sweep (world-boss-cog style) expires unkilled instances.
- HP ⚙ `base(sect_level) × (0.6 + 0.4 × member_count / cap)` — sized so ~60% of members
  using all attacks clears it (bench at build time).
- ⚙ 3 attacks/member/week; each attack = solo `CombatSession` vs the boss; damage lands
  via the **`apply_damage_atomic` pattern** (row lock, per-attack cap, credit `applied`).
- Rewards: participation ⚙ +300 CH + chest; top-3 extra chest; sect ⚙ +5,000 EXP +
  20,000 funds on kill. `/tongmon bxh` = weekly cross-sect damage leaderboard.

### 3.10 Đại Chiến Khoáng Mạch — spirit-stone mine wars (Phase 6)

World map holds ⚙ **N = 9 mines** in 4 tiers; occupying sects earn **funds hourly**
(user-spec: "cộng tài nguyên trực tiếp vào quỹ bang theo từng giờ").

| Tier | Count ⚙ | Funds/hour ⚙ | /day | Garrison (unoccupied) |
|------|--------:|-------------:|-----:|------------------------|
| Cấp Thấp | 4 | 250 | 6,000 | weakest guardian pool |
| Trung Cấp | 3 | 600 | 14,400 | mid |
| Cao Cấp | 1 | 1,250 | 30,000 | strong |
| Cực Phẩm | 1 | 2,500 | 60,000 | raid-tier |

Calibration: Cực Phẩm ≈ 80% of a 15-member sect's max donation income — worth bleeding
for, not economy-breaking. Mines are data-driven (`src/data/sects/mines.json`: key,
name_vi, tier, element flavor, funds_per_hour, garrison enemy key/HP).

**Rules:**

- Requirements to declare (`tuyên chiến`): sect level ≥ ⚙ 4, Trưởng Lão+, fee ⚙ 30,000
  funds, occupancy cap per D7 (⚙ 1 mine/sect).
- **Unoccupied mine** → PvE siege: declaration opens an exclusive ⚙ 24 h window (first
  declarer wins the slot; ⚙ alt: open race — flagged); members raid the **NPC garrison** —
  a shared HP pool, structural clone of the sect-boss/world-boss atomic-damage machinery.
  Garrison dead within window → sect occupies.
- **Occupied mine** → async PvP war over a ⚙ 24 h window:
  - Each member of both sects gets ⚙ 5 attempts (`/tongmon xuatchinh`). An attempt duels a
    **defense snapshot** of a random opposing member — the arena engine already builds a
    full Combatant from any player's persisted build, no presence needed.
  - Points ⚙: win +10, loss +3 (participation). Defenders counter-raid identically.
  - Window end: `attacker_points > defender_points` → mine flips (an AFK defender sect
    loses to any nonzero attack). Resolution + hourly payouts settle in the same 1-min
    `tasks.loop` + lazily on any interaction (no missed hours).
- Post-war: successful defense → ⚙ 48 h shield (`shield_until`); attacker → ⚙ 72 h
  re-declare cooldown vs the same mine. Occupier flips → payout accrual restarts for the
  new owner from the flip timestamp.
- `/tongmon khoangmach` — world-map embed: every mine, tier, owner `[TAG]`, shield/war
  state; `chienbao` — live war status/points.
- **Known gap (accepted for v1):** raw-power matchmaking — a whale sect's snapshots are
  hard walls. ⚙ Mitigations if needed later: realm-banded mines, point scaling by power
  delta. Listed under open questions.

---

## 4. Command surface (`/tongmon …`, one cog)

| Command | Who | Phase |
|---------|-----|-------|
| `tao` · `thongtin` · `tim` · `xinvao`/`huyxin` · `donxin` · `thanhvien` · `quyengop` · `roikhoi` · `trucxuat` · `thangchuc`/`giangchuc` · `nhuongvi` · `giaitan` · `thongbao` | (as v1 design) | 1 |
| `nangcap` (facility UI) | Trưởng Lão+ | 2 |
| `kho` · `guikho <item> <số>` · `xinkho <item> <số>` | member | 3 |
| `duyetkho` (approve/reject UI) | Chấp Sự+ | 3 |
| `diemdanh` · `cuahang` · `nhiemvu` | member | 4 |
| `boss` · `bxh` | member / anyone | 5 |
| `khoangmach` (map) · `chienbao` | anyone | 6 |
| `tuyenchien <mỏ>` | Trưởng Lão+ | 6 |
| `xuatchinh` (war attack run) | member | 6 |
| `/admin tongmon rename|disband|transfer|addexp|mine_reset` | admin | 1/6 |

Status-embed integration: `[TAG]` + sect line on `/status` (Phase 1).

---

## 5. Technical design

### 5.1 Tables (one migration per phase; numbers assigned at build time)

```text
# ── Phase 1 ──────────────────────────────────────────────────────────────
sects              id · name uq(lower) · tag uq · leader_player_id FK · level=1
                   · exp=0 · funds bigint=0 · announcement · emblem · Timestamps
sect_members       id · sect_id FK CASCADE ix · player_id FK CASCADE UNIQUE
                   · rank='de_tu' · contribution_points=0 · contribution_total=0
                   · donated_today=0 · donation_date · last_checkin_date
                   · mission_progress='{}' · Timestamps
sect_facilities    id · sect_id FK · facility_key · level=0 · UQ(sect_id, facility_key)
sect_applications  id · sect_id FK · player_id FK ix · message · UQ(sect_id, player_id)
sect_logs          id · sect_id FK ix(sect_id, created_at) · actor_player_id
                   · action · detail   (pruned to ⚙ last 100/sect)
sect_cooldowns     player_id PK · rejoin_after

# ── Phase 3 (storage) ────────────────────────────────────────────────────
sect_storage_items    id · sect_id FK ix · item_key · quantity · UQ(sect_id, item_key)
sect_storage_requests id · sect_id FK ix(sect_id, status) · requester_player_id FK CASCADE
                      · item_key · quantity · status(pending/approved/rejected/expired)
                      · reviewed_by_player_id NULL · resolved_at NULL · Timestamps

# ── Phase 5 (boss) ───────────────────────────────────────────────────────
sect_boss_instances      clone of world_boss_instances + sect_id · UQ(sect_id, week_key)
sect_boss_participations clone of world_boss_participations

# ── Phase 6 (mines) ──────────────────────────────────────────────────────
sect_mines        id · mine_key UNIQUE · occupier_sect_id FK NULL · occupied_since
                  · last_payout_at · shield_until NULL
mine_wars         id · mine_key ix · attacker_sect_id FK · defender_sect_id FK NULL
                  (NULL = NPC garrison siege) · window_start · window_end
                  · attacker_points=0 · defender_points=0 · garrison_hp_current NULL
                  · status(active/resolved) · winner_sect_id NULL
mine_war_attacks  id · war_id FK CASCADE · player_id FK · side · attempts_used=0
                  · points=0 · UQ(war_id, player_id)
```

All models in `src/db/models/sect.py` (+ `sect_mine.py` Phase 6), **imported in
`src/db/connection.py`**.

### 5.2 New files

| File | Contents |
|------|----------|
| `src/db/models/sect.py` | Phase 1/3/5 ORM models |
| `src/db/models/sect_mine.py` | Phase 6 ORM models |
| `src/db/repositories/sect_repo.py` | membership CRUD + `add_donation_atomic` / `spend_funds_atomic` / `spend_contribution_atomic` / `add_exp_atomic` / `storage_deposit_atomic` / `storage_withdraw_atomic` — all `SELECT…FOR UPDATE`, world-boss style. **Lock order: sect → member → storage item** (documented; prevents deadlock) |
| `src/db/repositories/sect_mine_repo.py` | mine occupancy, war lifecycle, `accrue_payout_atomic` (lazy hourly settle), garrison damage clone |
| `src/game/systems/sect.py` | pure rules: rank matrix, level curve, donation split + level-scaled cap, member cap, `get_member_buffs(session, player_id) -> dict` |
| `src/game/systems/sect_storage.py` | deposit/request/approve rules, weekly withdraw caps, expiry |
| `src/game/systems/sect_missions.py` | `record_event`, daily reset, claim |
| `src/game/systems/sect_mine.py` | payout math, war points, window resolution, snapshot-duel orchestration (reuses arena builder + `CombatSession`) |
| `src/data/sects/sect_levels.json` · `facilities.json` · `sect_shop.json` · `sect_bosses.json` · `mines.json` | data-driven content, registry-loaded |
| `src/bot/cogs/tong_mon.py` | the cog — **register in `client.py` COGS** (note: `constitution` is currently missing from that list; don't repeat) |
| `tests/test_sect.py` · `test_sect_storage.py` · `test_sect_shop.py` · `test_sect_missions.py` · `test_sect_boss.py` · `test_sect_mine.py` | per phase |

### 5.3 Modified files

| File | Change | Phase |
|------|--------|-------|
| `src/db/connection.py` | import sect models | 1 |
| `src/bot/client.py` | `COGS += ["src.bot.cogs.tong_mon"]` | 1 |
| `src/data/registry.py` | load `src/data/sects/*` | 1 |
| `src/bot/cogs/status.py` | `[TAG]` line | 1 |
| `src/game/models/character.py` | `sect_buffs: dict[str, float] = field(default_factory=dict)` | 2 |
| `src/game/systems/cultivation.py` | `cultivation_speed_mult` += sect term | 2 |
| `src/game/systems/cultivation_service.py` | `apply_offline_ticks`: one membership lookup → `char.sect_buffs` before tick (covers ALL AFK progress) | 2 |
| `src/game/systems/alchemy.py` | success roll += sect term | 2 |
| dungeon / world_boss / alchemy / arena | `sect_missions.record_event(...)` one-liners | 4 |

**Dormant-path guarantee:** empty `sect_buffs` default + zero combat-stat hooks ⇒ golden
combat stats and power bench untouched in every phase.

### 5.4 Concurrency rules (lifted from world-boss learnings)

- Every fund/EXP/CH/storage/payout mutation goes through a row-locking repo method;
  callers consume returned *applied* values.
- Double-join/approve → UNIQUE constraints; double-claim (check-in, mission, boss reward,
  war resolution, payout settle) → `WHERE <flag/state> = expected` guarded UPDATE
  checking rowcount, exactly like `claim_reward_atomic` / `flag_rewards_distributed`.
- Daily/weekly counters reset lazily on UTC-date comparison. The module's single
  `tasks.loop(minutes=1)` handles only: boss-week expiry, war-window resolution, mine
  payout sweep (payouts ALSO accrue lazily on interaction — the loop is a backstop).
- Mine payout accrual: `floor((now − last_payout_at) / 1h) × rate`, advance
  `last_payout_at` by whole hours only (same consumed-seconds trick as
  `apply_offline_ticks`).

---

## 6. Phased delivery & test plan

| Phase | Scope | Est. sessions | Key tests |
|------|-------|---------------|-----------|
| **1 — Core** ✅ SHIPPED | tables, repo, rules, cog (create/join/ranks/donate/info/list/logs), status tag, admin | 2–3 | rank matrix, donation split + level-scaled caps, curve, lifecycle edges (leader leave-block, disband modal, cooldown) |
| **2 — Facilities & buffs** ✅ SHIPPED | 5 facilities, upgrades, `get_member_buffs`, cultivation+alchemy hooks | 1–2 | buff aggregation, +3%/level additivity, **golden stats unchanged**, gating/costs |
| **3 — Kho Tàng storage** ✅ SHIPPED | storage tables, deposit/request/approve flows, capacity, weekly caps, audit log | 1–2 | approve atomicity + stock races, self-approval ban, expiry, capacity |
| **4 — Shop + check-in + missions** ✅ SHIPPED | sect_shop.json, purchases, diemdanh, missions + 4 call sites | 1–2 | purchase atomicity, weekly limits, mission reset/claim |
| **5 — Sect boss** ✅ SHIPPED | boss tables/repo, **new enemy family JSON**, lazy weekly spawn, attack flow, rewards, `bxh` | 2 | HP formula bench, atomic damage, claim races, week rollover |
| **6 — Đại Chiến Khoáng Mạch** ✅ SHIPPED | mines/wars tables + repo, map UI, PvE siege (garrison pool), async PvP war (snapshot duels), hourly payouts, shields/cooldowns | 2–3 | payout accrual math, window resolution races, occupancy cap, shield/cooldown gates, points math |

Each phase ships independently — Phase 1 alone is a complete feature.

---

## 7. Open questions (non-blocking; answers change numbers, not structure)

1. **Mine matchmaking fairness** — accept raw-power wars in v1, or realm-band the mine
   tiers / scale war points by power delta from day one?
2. **Unoccupied-mine sieges** — exclusive window for the first declarer (current design)
   or open race between multiple sects?
3. **Tông Chủ self-approval** in storage — currently banned like other officers; allow?
4. **Equipment instances in storage** — follow-up phase or drop entirely (direct_trade
   covers gear)?
5. Emblem/cosmetics — emoji-only in v1 (default assumption).
