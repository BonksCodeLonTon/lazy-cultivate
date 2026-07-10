"""Player character model."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CharacterStats:
    """Computed stats — derived each time from realm levels + formation + constitution."""
    hp_max: int = 0
    mp_max: int = 0
    spd: int = 10

    # Rating-based combat stats
    crit_rating: int = 0
    crit_dmg_rating: int = 0
    evasion_rating: int = 0
    crit_res_rating: int = 0

    # Attack and defense stats — scale with cultivation path
    atk: int = 0       # Physical attack (Luyện Thể path focused)
    matk: int = 0      # Magic attack (Luyện Khí path focused)
    def_stat: int = 0  # Physical defense → converted to physical resistance via formula

    # Direct modifiers
    final_dmg_bonus: float = 0.0   # additive %
    true_dmg: int = 0
    reflect_dmg: float = 0.0

    # Elemental resistances (percentage, 0.0–MAX_ELEMENTAL_RES) — one per Element (9 total, mirrors Linh Căn)
    res_kim: float = 0.0
    res_moc: float = 0.0
    res_thuy: float = 0.0
    res_hoa: float = 0.0
    res_tho: float = 0.0
    res_loi: float = 0.0
    res_phong: float = 0.0
    res_quang: float = 0.0
    res_am: float = 0.0

    # Utility
    luck: int = 0
    comprehension: int = 0
    merit_bonus: float = 0.0
    karma_reduce: float = 0.0
    cooldown_reduce: float = 0.0


@dataclass
class Character:
    """Full player character state (persisted to DB)."""
    player_id: int
    discord_id: int
    name: str

    # Cultivation realm per axis (0-based realm index, 1-based level)
    body_realm: int = 0
    body_level: int = 1
    qi_realm: int = 0
    qi_level: int = 1
    formation_realm: int = 0
    formation_level: int = 1

    # Constitution
    constitution_type: str = "ConstitutionPhamThe"  # Phàm Thể default (mortal baseline)
    # Comma-separated unlock history (mirrors Player.constitution_tracker) —
    # read by Thôn Thiên Ma Thể's L3 devour-copy to pick a donor body.
    constitution_tracker: str = ""
    dao_ti_unlocked: bool = False  # True once Nhập Thánh Cấp 9 breakthrough done

    # Currencies
    merit: int = 0
    karma_accum: int = 0     # Tích Lũy — only goes up
    karma_usable: int = 0    # Khả Dụng — can be spent
    primordial_stones: int = 0

    # Đan Độc — accumulated pill toxicity. Past 0 it scales linearly into
    # combat + cultivation penalties (see ``src.game.systems.toxicity``).
    # Mirrors ``Player.dan_doc`` on the ORM.
    dan_doc: int = 0

    # Permanent combat-buff pill counters (see ``src.game.systems.pill_buffs``).
    # Mirrors the parsed form of ``Player.pill_buff_counts``. Each consume
    # of a buff_*/buff_element_* pill increments the matching key (capped
    # by ``PILL_BUFF_CAP``); ``compute_combat_stats`` reads it to apply
    # the matching permanent stat bonus.
    pill_buff_counts: dict[str, int] = field(default_factory=dict)

    # Active cultivation axis
    active_axis: str = "qi"  # "body" | "qi" | "formation"

    # Cultivation XP — accumulated toward the current realm's bậc table.
    # body/qi advance via turns × realm.base_exp_rate; formation advances
    # only via Công Đức conversion (see study_formation_with_merit).
    body_xp: int = 0
    qi_xp: int = 0
    formation_xp: int = 0

    # Turn tracking
    turns_today: int = 0
    bonus_turns_remaining: int = 440

    # Active formation key
    active_formation: Optional[str] = None

    # Titles
    main_title: Optional[str] = None
    sub_title: Optional[str] = None
    evil_title: Optional[str] = None   # Auto-assigned, unremovable

    # Linh Căn (Spiritual Roots) — randomly assigned on registration.
    # ``linh_can`` is the bare element list (e.g. ["kim", "hoa"]) used for
    # membership checks (``"kim" in actor.linh_can``). ``linh_can_levels``
    # carries the per-element progression level (1..9) consumed by stat
    # scaling and effect modules.
    linh_can: list[str] = field(default_factory=list)
    linh_can_levels: dict[str, int] = field(default_factory=dict)

    # Constitution Process levels — per equipped Thể Chất progression level
    # (1..9), keyed by constitution_key. Drives the flag-gated process-level
    # read in ``character_stats.compute_combat_stats``. Empty by default and
    # only populated (inside an active session) when the feature is enabled, so
    # the dormant path stays allocation-free and byte-identical.
    constitution_levels: dict[str, int] = field(default_factory=dict)

    # Bách Thể Chú Linh — body-part infusions {part_key: essence_key}.
    # Parsed from ``Player.body_part_infusions`` (JSON column). Bonuses only
    # apply while ``active_axis == "body"`` — see ``systems/body_parts.py``.
    body_part_infusions: dict[str, str] = field(default_factory=dict)

    # Tông Môn facility buffs — {buff_key: value}, e.g.
    # {"cultivation_speed_bonus": 0.09}. NOT populated by ``_player_to_model``;
    # repo-coupled callers that need it (offline tick, formation study,
    # alchemy craft) call ``sect.attach_sect_buffs`` explicitly. Empty for
    # sect-less players and for every combat path — sect buffs are
    # utility-only in v1, so ``compute_combat_stats`` never reads this.
    sect_buffs: dict[str, float] = field(default_factory=dict)

    # HP/MP/shield current (session state — persisted on the Player row)
    hp_current: int = 0
    mp_current: int = 0
    shield_current: int = 0

    stats: CharacterStats = field(default_factory=CharacterStats)

    def is_alive(self) -> bool:
        return self.hp_current > 0

    def is_currency_capped(self, currency: str) -> bool:
        from src.game.constants.currencies import CURRENCY_CAP
        return getattr(self, currency, 0) >= CURRENCY_CAP
