"""Enemy model."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


class EnemyRank(StrEnum):
    PHO_THONG = "pho_thong"   # Common
    CUONG_GIA = "cuong_gia"   # Strong
    DAI_NANG = "dai_nang"     # Elite
    CHI_TON = "chi_ton"       # Boss / Supreme


@dataclass
class DropEntry:
    item_key: str
    weight: int         # relative weight for drop table
    quantity_min: int = 1
    quantity_max: int = 1


@dataclass
class EnemyTemplate:
    key: str
    vi: str
    en: str
    rank: EnemyRank
    element: str
    base_hp: int
    base_spd: int
    skill_keys: list[str] = field(default_factory=list)
    drop_table: list[DropEntry] = field(default_factory=list)
    # Stat scale applied relative to player realm
    realm_offset: int = 0   # ±1 relative to player realm


@dataclass
class EnemyInstance:
    """Live enemy in combat."""
    template: EnemyTemplate
    hp_max: int
    hp_current: int
    spd: int
    active_effects: dict = field(default_factory=dict)

    def is_alive(self) -> bool:
        return self.hp_current > 0
