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


@dataclass(frozen=True)
class DefenseStats:
    """Defender's defensive stats for a single hit."""
    evasion_rating: int = 0              # dodge chance (rating formula)
    crit_res_rating: int = 0             # reduces incoming crit chance
    def_stat: int = 0                    # physical defense (→ resist formula)
    resistances: dict[str, int] = field(default_factory=dict)  # elemental flat reduction
