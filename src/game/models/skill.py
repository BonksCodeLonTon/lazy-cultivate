"""Skill model."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Optional


class SkillType(StrEnum):
    THIEN = "thien"       # Attack (Thiên)
    DIA = "dia"           # Defense (Địa)
    NHAN = "nhan"         # Support/CC (Nhân)
    FORMATION = "tran_phap"  # Formation-specific


class AttackType(StrEnum):
    PHYSICAL = "physical"  # Scales with ATK, reduced by physical defense
    MAGICAL = "magical"    # Scales with MATK, reduced by elemental resistance only
    TRUE = "true"          # Ignores all resistance


@dataclass(frozen=True)
class Skill:
    key: str
    vi: str
    en: str
    skill_type: SkillType
    mp_cost: int          # 2–81, flat cost; also used as flat damage contribution
    cooldown: int         # turns
    base_dmg: int         # base skill damage
    element: Optional[str] = None
    effect_keys: tuple[str, ...] = ()
    formation_key: Optional[str] = None  # required if type == FORMATION
    attack_type: AttackType = AttackType.MAGICAL  # physical or magical scaling
    atk_scale: float = 1.0  # multiplier applied to ATK/MATK before adding to base
