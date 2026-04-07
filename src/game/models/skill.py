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
