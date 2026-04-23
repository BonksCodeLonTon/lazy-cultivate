"""Skill model."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


class SkillCategory(StrEnum):
    ATTACK = "attack"        # Damage + offensive CC/debuff
    DEFENSE = "defense"      # Shields, heals, damage-reduce buffs, cleanses
    MOVEMENT = "movement"    # Speed, evasion, turn-steal, action advantage
    PASSIVE = "passive"      # Always-on passives
    FORMATION = "formation"  # Formation (trận pháp) skills


class AttackType(StrEnum):
    PHYSICAL = "physical"  # Reduced by physical defense
    MAGICAL = "magical"    # Reduced by elemental resistance only
    TRUE = "true"          # Ignores all resistance


@dataclass(frozen=True)
class DmgScale:
    """Damage scaling per stat — a skill can scale with ATK, MATK, or both."""
    atk: float = 0.0
    matk: float = 0.0

    @classmethod
    def from_raw(cls, raw: dict | float | int | None) -> "DmgScale":
        """Build from JSON (object {atk, matk} or legacy scalar)."""
        if raw is None:
            return cls()
        if isinstance(raw, dict):
            return cls(
                atk=float(raw.get("atk", 0.0)),
                matk=float(raw.get("matk", 0.0)),
            )
        # Legacy scalar: applied as a single scale; caller decides stat via attack_type
        return cls(atk=float(raw), matk=float(raw))


@dataclass(frozen=True)
class Skill:
    key: str
    vi: str
    en: str
    realm: int                      # 1–9 (cultivation realm that gates the skill)
    category: SkillCategory
    mp_cost: int
    cooldown: int                   # turns
    base_dmg: int                   # flat base damage (0 for non-damaging skills)
    element: Optional[str] = None
    effect_keys: tuple[str, ...] = ()
    formation_key: Optional[str] = None  # required if category == FORMATION
    attack_type: AttackType = AttackType.MAGICAL
    dmg_scale: DmgScale = field(default_factory=DmgScale)
