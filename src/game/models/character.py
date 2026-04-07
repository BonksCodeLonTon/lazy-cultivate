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

    # Direct modifiers
    final_dmg_bonus: float = 0.0   # additive %
    true_dmg: int = 0
    reflect_dmg: float = 0.0

    # Elemental resistances (flat reduction) — one per Element (9 total, mirrors Linh Căn)
    res_kim: int = 0
    res_moc: int = 0
    res_thuy: int = 0
    res_hoa: int = 0
    res_tho: int = 0
    res_loi: int = 0
    res_phong: int = 0
    res_quang: int = 0
    res_am: int = 0

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
    constitution_type: str = "ConstitutionVanTuong"  # Vạn Tượng default (free)
    dao_ti_unlocked: bool = False  # True once Nhập Thánh Cấp 9 breakthrough done

    # Currencies
    merit: int = 0
    karma_accum: int = 0     # Tích Lũy — only goes up
    karma_usable: int = 0    # Khả Dụng — can be spent
    primordial_stones: int = 0

    # Active cultivation axis
    active_axis: str = "qi"  # "body" | "qi" | "formation"

    # Cultivation XP — turns accumulated toward the next level on each axis.
    # Caps at TURNS_PER_CULT_LEVEL when level == 9 (awaiting manual breakthrough).
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

    # Linh Căn (Spiritual Roots) — randomly assigned on registration
    linh_can: list[str] = field(default_factory=list)

    # HP/MP current (session state — not persisted separately)
    hp_current: int = 0
    mp_current: int = 0

    stats: CharacterStats = field(default_factory=CharacterStats)

    def is_alive(self) -> bool:
        return self.hp_current > 0

    def is_currency_capped(self, currency: str) -> bool:
        from src.game.constants.currencies import CURRENCY_CAP
        return getattr(self, currency, 0) >= CURRENCY_CAP
