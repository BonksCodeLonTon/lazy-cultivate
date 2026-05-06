"""Verify aura_on_hit accepts an optional duration override."""
import random

from src.game.engine.effects import EFFECTS, EffectKind, EffectMeta, default_duration
from src.game.systems.combat.session import CombatSession
from src.game.systems.combatant import Combatant


EFFECTS["BuffTestLoi5"] = EffectMeta(
    key="BuffTestLoi5", vi="Test Loi 5", en="Test Loi 5",
    kind=EffectKind.BUFF, description_vi="test",
    aura_on_hit=("DebuffTeLiet", 1.0, 5),
)
EFFECTS["BuffTestLoiD"] = EffectMeta(
    key="BuffTestLoiD", vi="Test Loi D", en="Test Loi D",
    kind=EffectKind.BUFF, description_vi="test",
    aura_on_hit=("DebuffTeLiet", 1.0),
)


def make_pair(buff_key):
    a = Combatant(
        key="a", name="A", hp=1000, hp_max=1000, mp=100, mp_max=100,
        spd=10, element=None, atk=100, matk=100, def_stat=20,
    )
    a.apply_effect(buff_key, 99)
    t = Combatant(
        key="t", name="T", hp=1000, hp_max=1000, mp=100, mp_max=100,
        spd=10, element=None,
    )
    return a, t


# 3-tuple override
a, t = make_pair("BuffTestLoi5")
sess = CombatSession(player=a, enemy=t, player_skill_keys=[], rng=random.Random(0), max_turns=1)
sess._run_on_hit_procs(a, t, is_crit=False)
got = t.effects.get("DebuffTeLiet")
print(f"3-tuple override (expects 5): {got}")
assert got == 5, got

# 2-tuple legacy
a, t = make_pair("BuffTestLoiD")
sess = CombatSession(player=a, enemy=t, player_skill_keys=[], rng=random.Random(0), max_turns=1)
sess._run_on_hit_procs(a, t, is_crit=False)
got = t.effects.get("DebuffTeLiet")
print(f"2-tuple legacy (default {default_duration('DebuffTeLiet')}): {got}")
assert got == default_duration("DebuffTeLiet"), got

# Existing BuffLoiThan untouched
a, t = make_pair("BuffLoiThan")
sess = CombatSession(player=a, enemy=t, player_skill_keys=[], rng=random.Random(0), max_turns=1)
sess.rng.random = lambda: 0.05  # force 20% gate
sess._run_on_hit_procs(a, t, is_crit=False)
got = t.effects.get("DebuffTeLiet")
print(f"BuffLoiThan (legacy 2-tuple): {got}")

print("OK — 3-tuple duration override works; 2-tuple legacy unchanged.")
