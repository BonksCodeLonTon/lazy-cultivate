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

    # ── Fire DoT build support ────────────────────────────────────────────────
    # Burn stacks: multiple stacks pile on the same target, each contributing
    # per-stack damage to the DebuffThieuDot tick. Stacks decay when the burn
    # effect fully expires.
    burn_stacks: int = 0
    # Max concurrent stacks. Base 5; fire-build items raise this.
    burn_stack_cap: int = 5
    # Bonus damage multiplier vs targets that currently have burn stacks.
    # e.g. 0.25 → +25% final damage if target has any burn stack.
    bonus_dmg_vs_burn: float = 0.0
    # Flat fire-resistance shred applied by this actor when landing a hit
    # on a target. Stacks additively with debuffs in the same hit.
    fire_res_shred: float = 0.0
    # Whether DoTs ticking on the opposing combatant can roll crits.
    # Set on the attacker; combat engine reads the attacker's flag each tick.
    dot_can_crit: bool = False
    # Per-stack burn damage fraction of hp_max (applied by get_periodic_damage).
    burn_per_stack_pct: float = 0.012

    # ── Kim (Bleed) build support ─────────────────────────────────────────────
    # Bleed stacks: like burn, but physical (kim element) and slows healing.
    bleed_stacks: int = 0
    bleed_stack_cap: int = 5
    # Per-stack bleed damage fraction of hp_max.
    bleed_per_stack_pct: float = 0.010
    # When holder has bleed stacks, incoming heals are multiplied by (1 - bleed_heal_reduce).
    # e.g. 0.40 means heals on a bleeding target are reduced by 40%.
    bleed_heal_reduce: float = 0.0
    # On-hit: chance actor applies a bleed stack.
    bleed_on_hit_pct: float = 0.0
    # Bonus crit chance vs bleeding targets (flat rating added to crit_rating).
    crit_rating_vs_bleed: int = 0
    # Bonus crit damage vs bleeding targets (flat rating added to crit_dmg_rating).
    crit_dmg_vs_bleed: int = 0
    # True damage: percentage of target hp_max applied unblockable on each hit.
    # Max per-hit cap is TRUE_DMG_PCT_CAP in the damage pipeline.
    true_dmg_pct: float = 0.0

    # ── Mộc (Wood / Poison Leech) build ──────────────────────────────────────
    # When any DoT ticks on the opposing combatant, this combatant leeches
    # this fraction of the tick damage as HP + MP.
    dot_leech_pct: float = 0.0
    # Flat shred on target's moc resistance (mirror of fire_res_shred).
    moc_res_shred: float = 0.0
    # Damage-from-heal: every heal this combatant receives queues
    # heal × damage_from_heal_pct bonus damage on their next hit.
    damage_from_heal_pct: float = 0.0
    queued_heal_dmg: int = 0
    # Damage scaling with hp_max: flat damage bonus = hp_max × this per attack.
    damage_bonus_from_hp_pct: float = 0.0

    # ── Thủy (Water / Mana) build ────────────────────────────────────────────
    # Fraction of incoming damage reflected back to the attacker after HP loss.
    reflect_pct: float = 0.0
    # When True, the defender's own on-hit procs (freeze_on_skill, slow, etc.)
    # fire on the attacker as part of the reflected hit — the mirror carries
    # not just damage but the defender's build effects.
    reflect_applies_effects: bool = False
    # Flat damage bonus per attack = mp_max × this. Grows damage with mana pool.
    damage_bonus_from_mp_pct: float = 0.0
    # On-hit: leech this fraction of damage dealt as MP for the attacker.
    mp_leech_pct: float = 0.0
    # Mana-stack accumulator. Gained per attack/skill cast; each stack adds
    # mana_stack_dmg_bonus × final_dmg_bonus, or can be burst-consumed.
    mana_stacks: int = 0
    mana_stack_cap: int = 10
    mana_stack_per_attack: int = 0      # passive: stacks gained each turn
    mana_stack_dmg_bonus: float = 0.0   # per-stack final-damage bonus
    # Flat shred on target's thủy resistance (mirror of fire_res_shred).
    thuy_res_shred: float = 0.0

    # ── Thổ (Earth / Shield) build ──────────────────────────────────────────
    # Per-turn shield regen as a fraction of hp_max (e.g. 0.05 = +5% hp_max/turn).
    shield_regen_pct: float = 0.0
    # Flat shield gained per turn (stacks with shield_regen_pct).
    shield_regen_flat: int = 0
    # Maximum shield that can be held at any time, as a fraction of hp_max.
    # 1.0 = can stack shield up to 100% of hp_max. Default 0.30 (old Hộ Thể cap).
    shield_cap_pct: float = 0.30
    # Every hit adds flat damage = current shield × this fraction.
    damage_bonus_from_shield_pct: float = 0.0
    # Thorn: fraction of incoming damage reflected as physical thorn damage.
    # Differs from reflect_pct by always dealing physical kim-style damage
    # regardless of defender element.
    thorn_pct: float = 0.0
    # Shield takes the reflected damage before thorn bleeds into HP.
    thorn_from_shield: bool = False
    # On-hit: chance to stun the target (any hit, not just bạo kích).
    stun_on_hit_pct: float = 0.0

    def is_alive(self) -> bool:
        return self.hp > 0

    def add_burn_stack(self, count: int = 1) -> int:
        """Add burn stacks, clamped by ``burn_stack_cap``. Returns stacks gained."""
        before = self.burn_stacks
        self.burn_stacks = min(self.burn_stack_cap, self.burn_stacks + count)
        return self.burn_stacks - before

    def consume_burn_stacks(self) -> int:
        """Remove all burn stacks and return how many were consumed."""
        stacks = self.burn_stacks
        self.burn_stacks = 0
        return stacks

    def add_bleed_stack(self, count: int = 1) -> int:
        """Add bleed stacks, clamped by ``bleed_stack_cap``. Returns stacks gained."""
        before = self.bleed_stacks
        self.bleed_stacks = min(self.bleed_stack_cap, self.bleed_stacks + count)
        return self.bleed_stacks - before

    def consume_bleed_stacks(self) -> int:
        stacks = self.bleed_stacks
        self.bleed_stacks = 0
        return stacks

    def add_mana_stack(self, count: int = 1) -> int:
        before = self.mana_stacks
        self.mana_stacks = min(self.mana_stack_cap, self.mana_stacks + count)
        return self.mana_stacks - before

    def consume_mana_stacks(self) -> int:
        stacks = self.mana_stacks
        self.mana_stacks = 0
        return stacks

    def shield_cap(self) -> int:
        """Maximum shield value this combatant can hold."""
        return int(self.hp_max * self.shield_cap_pct)

    def add_shield(self, amount: int) -> int:
        """Add shield capped at shield_cap. Returns actual gained."""
        if amount <= 0:
            return 0
        cap = self.shield_cap()
        before = self.shield
        self.shield = min(cap, self.shield + amount)
        return self.shield - before

    def consume_shield(self) -> int:
        """Empty the shield pool, return the consumed amount."""
        amt = self.shield
        self.shield = 0
        return amt

    def has_effect(self, effect: str) -> bool:
        return self.effects.get(effect, 0) > 0

    def apply_effect(self, effect: str, duration: int) -> None:
        self.effects[effect] = max(self.effects.get(effect, 0), duration)

    def tick_effects(self) -> list[str]:
        expired = [k for k, v in self.effects.items() if v <= 1]
        self.effects = {k: v - 1 for k, v in self.effects.items() if v > 1}
        # Burn stacks decay with the burn debuff: when DebuffThieuDot expires,
        # all remaining stacks are cleared.
        if "DebuffThieuDot" in expired:
            self.burn_stacks = 0
        if "DebuffChayMau" in expired:
            self.bleed_stacks = 0
        return expired

    def tick_cooldowns(self) -> None:
        self.cooldowns = {k: max(0, v - 1) for k, v in self.cooldowns.items()}

    def skill_on_cooldown(self, skill_key: str) -> bool:
        return self.cooldowns.get(skill_key, 0) > 0

    def set_cooldown(self, skill_key: str, turns: int) -> None:
        effective = max(1, int(turns * (1.0 - self.cooldown_reduce)))
        self.cooldowns[skill_key] = effective
