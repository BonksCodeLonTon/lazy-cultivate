"""Combat stat DTOs — offensive and defensive views of a combatant for one hit.

These are the only objects the damage pipeline depends on. They aggregate:
  - Base cultivation stats
  - Formation / constitution / linh_can bonuses
  - Active buff / debuff modifiers
  - Equipment bonuses (future: fold into Combatant before building these)

Design rationale
----------------
Splitting into AttackStats (attacker-side) and DefenseStats (defender-side)
removes the ambiguity of the old CharacterStats-as-mock-stats pattern and puts
evasion where it belongs — on the defender.

Adding equipment is straightforward:
  1. Add bonus fields to Combatant (e.g. Combatant.atk += weapon.atk_bonus)
  2. The existing build helpers in combat.py pick them up automatically.
  No pipeline changes needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AttackStats:
    """Attacker's offensive stats for a single hit."""
    crit_rating: int = 0
    crit_dmg_rating: int = 0
    final_dmg_bonus: float = 0.0
    atk: int = 0    # physical attack power (scales physical skills)
    matk: int = 0   # magic attack power   (scales magical skills)
    # Skip the crit roll and force a guaranteed crit. Set when the target
    # has a debuff that makes them helpless — currently only Đông Băng
    # (freeze): the next skill landing on a frozen enemy auto-crits and
    # the freeze is consumed by the hit.
    force_crit: bool = False
    # Hybrid crit→dmg scaling: ``crit_dmg_rating × crit_dmg_rating_to_dmg_pct``
    # is added as flat damage to the base roll. Lets a crit-stacked build
    # turn part of its rating into reliable per-hit floor damage instead
    # of relying purely on crit RNG. Applied in ``base.roll_base``.
    crit_dmg_rating_to_dmg_pct: float = 0.0
    # Counter to the defender's evasion_rating. Subtracted from evasion
    # chance via the same rating→pct curve (mirrors crit ↔ crit_res),
    # consumed in ``damage/evasion.check_evasion``.
    accuracy_rating: int = 0
    # Per-element ATTACKER amp — applied at the elemental step (post-
    # resistance, mirrors the defender-side ``damage_taken_by_element``).
    # Lets a passive like Vạn Kiếm's Sword Heart amplify ONE element
    # without touching the generic ``final_dmg_bonus`` pool. Stacks
    # additively across sources.
    element_dmg_amp: dict[str, float] = field(default_factory=dict)
    # Conditional, target-state crit amps threaded into ``apply_critical``.
    # Set only when a build's "vs <state>" hunt is active for THIS hit (e.g.
    # Thái Bạch Canh Kim's +40% crit chance / +20% crit dmg vs a bleeding
    # target). Both default 0.0 → the pipeline call resolves byte-identically
    # for every build that doesn't arm them.
    bonus_crit_chance: float = 0.0
    bonus_crit_dmg_mult: float = 0.0


@dataclass(frozen=True)
class DefenseStats:
    """Defender's defensive stats for a single hit."""
    evasion_rating: int = 0              # dodge chance (rating formula)
    crit_res_rating: int = 0             # reduces incoming crit chance
    def_stat: int = 0                    # physical defense (→ resist formula)
    resistances: dict[str, int] = field(default_factory=dict)  # elemental flat reduction
    # Per-element damage-taken multiplier (e.g. {"hoa": 0.15} ⇒ +15% fire
    # damage taken). Sourced from active effects via ``<element>_damage_taken``
    # stat_bonus keys (see apply_elemental for the consumer). Stacks ADDITIVELY
    # across sources and is applied AFTER elemental resistance.
    damage_taken_by_element: dict[str, float] = field(default_factory=dict)
    # Hộ Pháp armor-extension lane — when > 0, the physical defense formula
    # ALSO mitigates non-physical hits, scaled by this pct (0.50 = half the
    # armor DR applies to elemental; 1.0 = full armor DR applies). Stacks
    # additively from active effects (e.g. BuffThoNguyenHoPhap) + permanent
    # actor field (formation gem ladder). Read by ``apply_armor_to_elemental``
    # in the damage pipeline. Capped via the same MAX_PHYS_REDUCTION used by
    # apply_physical_defense, so the lane has identical diminishing returns
    # and never exceeds the physical cap. Separate from elemental resistance
    # — both apply multiplicatively when the formation is active.
    def_applies_to_elemental_pct: float = 0.0
