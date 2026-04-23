"""Combatant dataclass — live combat state for a player or enemy."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Combatant:
    """Live combatant state during combat — player or enemy."""
    key: str
    name: str
    hp: int
    hp_max: int
    mp: int
    mp_max: int
    spd: int
    element: Optional[str]
    resistances: dict[str, float] = field(default_factory=dict)
    # Attack and defense stats — computed from cultivation path progression
    atk: int = 0       # Physical attack power (Luyện Thể path gives most ATK)
    matk: int = 0      # Magic attack power (Luyện Khí path gives most MATK)
    def_stat: int = 0  # Physical defense (Luyện Thể path gives most DEF)
    crit_rating: int = 0
    crit_dmg_rating: int = 0
    evasion_rating: int = 0
    crit_res_rating: int = 0
    final_dmg_bonus: float = 0.0
    # Damage reduction on received hits (from formation/constitution)
    final_dmg_reduce: float = 0.0
    # Per-turn HP regen as fraction of hp_max (e.g. 0.02 = 2%/turn)
    hp_regen_pct: float = 0.0
    # Per-turn flat HP regen, stacks with hp_regen_pct
    hp_regen_flat: int = 0
    # Per-turn MP regen as fraction of mp_max (e.g. 0.03 = 3%/turn)
    mp_regen_pct: float = 0.0
    # Per-turn flat MP regen, stacks with mp_regen_pct
    mp_regen_flat: int = 0
    # Healing effectiveness multiplier (from Quang Linh Căn passive)
    heal_pct: float = 0.0
    # Cooldown reduction multiplier (e.g. 0.15 → CD * 0.85)
    cooldown_reduce: float = 0.0
    # On-hit proc chances (from threshold bonuses)
    burn_on_hit_pct: float = 0.0
    slow_on_hit_pct: float = 0.0
    paralysis_on_crit: bool = False
    freeze_on_skill: bool = False
    poison_immunity: bool = False
    # Flat chance (0.0–1.0) to resist any incoming debuff or CC proc.
    # e.g. 0.20 → 20% of debuffs that would land are blocked.
    # Stacks additively from formation, constitution, and linh_can bonuses.
    debuff_immune_pct: float = 0.0
    # Total immunity to hard CC (stun/freeze/silence/interrupt/knock-up).
    # Used by world bosses — soft debuffs (slow, DoT, armor break) still apply.
    immune_hard_cc: bool = False
    # Immunity / special flags
    active_flags: dict[str, bool] = field(default_factory=dict)
    # Active effects: effect_key → turns_remaining
    effects: dict[str, int] = field(default_factory=dict)
    # Skill cooldowns: skill_key → turns_remaining
    cooldowns: dict[str, int] = field(default_factory=dict)
    skill_keys: list[str] = field(default_factory=list)
    # Linh Căn keys (spiritual roots)
    linh_can: list[str] = field(default_factory=list)
    # Thổ Hộ Thể shield (absorbs damage)
    shield: int = 0
    ho_the_used: bool = False

    def is_alive(self) -> bool:
        return self.hp > 0

    def has_effect(self, effect: str) -> bool:
        return self.effects.get(effect, 0) > 0

    def apply_effect(self, effect: str, duration: int) -> None:
        self.effects[effect] = max(self.effects.get(effect, 0), duration)

    def tick_effects(self) -> list[str]:
        expired = [k for k, v in self.effects.items() if v <= 1]
        self.effects = {k: v - 1 for k, v in self.effects.items() if v > 1}
        return expired

    def tick_cooldowns(self) -> None:
        self.cooldowns = {k: max(0, v - 1) for k, v in self.cooldowns.items()}

    def skill_on_cooldown(self, skill_key: str) -> bool:
        return self.cooldowns.get(skill_key, 0) > 0

    def set_cooldown(self, skill_key: str, turns: int) -> None:
        effective = max(1, int(turns * (1.0 - self.cooldown_reduce)))
        self.cooldowns[skill_key] = effective
