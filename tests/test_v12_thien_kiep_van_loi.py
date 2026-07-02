"""v12 constitution — Thiên Kiếp Vạn Lôi Thể (Heaven Tribulation Myriad Thunder Body, Lôi).

The 2nd Lôi body: a TRIBULATION CC-LOCKDOWN EXECUTIONER (vs Thiên Lôi Cường's
shock-DoT speed nuker). Identity: eat lightning, lock the foe down, never let
them slip away, execute the weakened.

  L1 Lôi Điện Thân Hòa — res_loi +60% (capped, NOT immunity). Vạn Lôi stacks
                         (cap 10, never decay): +1 per own landed Lôi CAST,
                         +2 when STRUCK by a Lôi skill. Per stack +20
                         crit_dmg_rating +2% spd via BuffVanLoi scaling_rules.
  L3 Dẫn Lôi Luyện Thể — per landed hit: 70% Sốc Điện; 30% Tê Liệt 2t —
                         upgraded to 40% Choáng 2t once stacks ≥ 5.
  L6 Thiên Phạt        — attacker-side evasion shred: target's whole effective
                         evasion ×(1−0.60) before the dodge roll (probabilistic,
                         never bypass).
  L9 Thiên Kiếp        — Lôi dmg +35% (element_dmg_bonus); +1 sustained extra
                         hit per cast; Chấp Hành execute: common-rank foes
                         (pho_thong/cuong_gia/hung_manh/tinh_anh, never
                         dai_nang/chi_ton/beasts/world-boss/players) below 20%
                         HP → 30% on-hit execute; at 10 stacks the roll is
                         skipped and all stacks are spent.

Reuses: the on-hit proc seam (run_thien_kiep_procs, dmg>0-gated, element-aware
via apply_reactive_damage's skill_element), BuffVanLoi scaling_rules
(stack:tk_van_loi), the hit_count fold (sustained sibling of Thái Bạch's
one-shot bonus), and build_defense_stats' attacker param for the shred.

Flag hygiene: every flag-ON test flips the global via ``monkeypatch.setattr`` so
it auto-reverts; the dormant default is never left mutated.
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import EFFECTS, get_combat_modifiers
from src.game.engine.damage.combat_hit import build_defense_stats, spd_evasion_bonus
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.casting import cast_skill
from src.game.systems.combat.procs import run_thien_kiep_procs
from src.game.systems.constitution_process import effective_effects
from src.utils.config import settings

_BODY = "TheChat_ThienKiepVanLoi"
_LOI_ATTACK = "SkillAtkLoi3"        # loi, category attack — feeds the accrual
_KIM_ATTACK = "SkillKimTripleStrike"  # non-loi control skill
_ENEMY = "TinhKimTho"               # rank tinh_anh — executable tier


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


def _body_data():
    data = registry.get_constitution(_BODY)
    assert data is not None and data.get("process")
    return data


def _enemy(hp: int = 10**9, rank: str | None = None):
    e = build_enemy_combatant(_ENEMY, player_realm_total=15)
    assert e is not None
    e.hp = e.hp_max = hp
    e.shield = 0
    e.resistances = {}
    e.evasion_rating = 0
    e.final_dmg_reduce = 0.0
    e.spd = 0
    if rank is not None:
        e.rank = rank
    return e


def _player(level: int):
    char = Character(
        player_id=1, discord_id=1, name="ThienKiepTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["loi"], linh_can_levels={"loi": 5},
        constitution_levels={_BODY: level}, stats=CharacterStats(),
    )
    return build_player_combatant(char, player_skill_keys=[_LOI_ATTACK])


def _session(player, enemy, *, seed: int = 0) -> CombatSession:
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=[player.skill_keys[0]],
        rng=random.Random(seed), max_turns=200,
    )


# ── 1. Composition ──────────────────────────────────────────────────────────


def test_body_registered_and_shape() -> None:
    data = _body_data()
    assert data["element"] == "loi"
    assert data["rarity"] == "legendary"
    assert data["cost"]["merit"] == 60000
    assert data["process"]["milestones"] == [1, 3, 6, 9]
    flat = data["stat_bonuses"]
    for cfg in ("tk_van_loi_cap", "tk_te_liet_chance", "tk_evasion_shred_pct",
                "tk_execute_chance"):
        assert cfg not in flat
    assert flat["matk_pct"] == 0.06


def test_composition_effects() -> None:
    data = _body_data()
    assert effective_effects(data, 1) == ["BuffLoiDienThanHoa", "BuffVanLoi"]
    assert effective_effects(data, 9) == [
        "BuffLoiDienThanHoa", "BuffVanLoi",
        "BuffDanLoiLuyenThe", "BuffThienPhat", "BuffThienKiep",
    ]


def test_new_effects_registered() -> None:
    for key in ("BuffLoiDienThanHoa", "BuffVanLoi", "BuffDanLoiLuyenThe",
                "BuffThienPhat", "BuffThienKiep"):
        assert EFFECTS.get(key) is not None


def test_config_flags_per_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    p1 = _player(1)
    assert p1.tk_van_loi_cap == 10
    assert p1.tk_stack_on_cast == 1
    assert p1.tk_stack_on_struck == 2
    p3 = _player(3)
    # L3's Sốc Điện rides the GENERIC shock_on_hit_pct lane (additive with
    # any linh-căn / gear shock contribution).
    assert p3.shock_on_hit_pct >= 0.70
    assert p3.tk_te_liet_chance == pytest.approx(0.30)
    assert p3.tk_stun_chance == pytest.approx(0.40)
    assert p3.tk_stun_stack_gate == 5
    assert p3.tk_stun_turns == 2
    p6 = _player(6)
    assert p6.tk_evasion_shred_pct == pytest.approx(0.60)
    p9 = _player(9)
    assert p9.tk_extra_hits == 1
    assert p9.tk_execute_chance == pytest.approx(0.30)
    assert p9.tk_execute_hp_pct == pytest.approx(0.20)
    assert p9.tk_execute_stack_gate == 10
    assert p9.element_dmg_bonus.get("loi") == pytest.approx(0.35)


# ── 2. L1 Lôi Điện Thân Hòa — capped resist + Vạn Lôi engine ────────────────


def test_l1_res_loi_capped_not_immune(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    assert player.resistances.get("loi") == pytest.approx(0.60)  # high, NOT 1.0


def test_van_loi_scaling_rules(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.tk_van_loi_stacks = 5
    mods = get_combat_modifiers(player)
    assert mods.get("crit_dmg_rating", 0.0) == pytest.approx(5 * 20)
    assert mods.get("spd_pct", 0.0) == pytest.approx(5 * 0.02)
    player.tk_van_loi_stacks = 10
    mods = get_combat_modifiers(player)
    assert mods.get("crit_dmg_rating", 0.0) == pytest.approx(200)  # max_output
    assert mods.get("spd_pct", 0.0) == pytest.approx(0.20)


def test_stack_accrues_on_own_loi_cast_once_per_cast(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99  # no crits/procs interference
    # A 3-hit Lôi skill charges exactly +1 (per CAST, not per hit).
    skill = {
        "key": "TestLoiMulti", "vi": "Test Lôi", "element": "loi",
        "attack_type": "magical", "base_dmg": 200, "mp_cost": 0,
        "cooldown": 1, "dmg_scale": {"atk": 0.0, "matk": 0.1},
        "hit_count": 3, "effects": [], "effect_chances": {},
    }
    cast_skill(session, player, enemy, "TestLoiMulti", skill, mp_cost=0)
    assert player.tk_van_loi_stacks == 1


def test_no_stack_on_non_loi_cast(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    kim = registry.get_skill(_KIM_ATTACK)
    cast_skill(session, player, enemy, _KIM_ATTACK, dict(kim), mp_cost=0)
    assert player.tk_van_loi_stacks == 0


def test_stack_accrues_when_struck_by_loi(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    # Defender side: the HOLDER (player) is the target being struck.
    run_thien_kiep_procs(session, enemy, player, dmg=1_000, skill_element="loi")
    assert player.tk_van_loi_stacks == 2  # +2 per Lôi hit taken
    run_thien_kiep_procs(session, enemy, player, dmg=1_000, skill_element="kim")
    assert player.tk_van_loi_stacks == 2  # non-Lôi hits charge nothing


def test_stacks_cap_and_negated_hit_charges_nothing(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(1)
    enemy = _enemy()
    session = _session(player, enemy)
    for _ in range(8):
        run_thien_kiep_procs(session, enemy, player, dmg=1_000, skill_element="loi")
    assert player.tk_van_loi_stacks == 10  # capped, not 16
    before = player.tk_van_loi_stacks
    run_thien_kiep_procs(session, enemy, player, dmg=0, skill_element="loi")
    assert player.tk_van_loi_stacks == before  # dmg<=0 → no charge


# ── 3. L3 Dẫn Lôi Luyện Thể — CC riders ─────────────────────────────────────


def test_l3_riders_apply_soc_dien_and_te_liet(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0  # every rider roll lands
    # Tê Liệt (conditional roll) lives in the body proc…
    run_thien_kiep_procs(session, player, enemy, dmg=1_000, skill_element="loi")
    assert enemy.has_effect("DebuffTeLiet")     # below the 5-stack gate
    assert not enemy.has_effect("CCStun")
    # …while Sốc Điện rides the generic shock_on_hit_pct lane on a real cast.
    player.crit_rating = 0
    skill = registry.get_skill(_LOI_ATTACK)
    cast_skill(session, player, enemy, _LOI_ATTACK, dict(skill), 0)
    assert enemy.has_effect("DebuffSocDien")


def test_l3_stun_upgrade_at_stack_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    player.tk_van_loi_stacks = 5                 # at the breakpoint
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    run_thien_kiep_procs(session, player, enemy, dmg=1_000, skill_element="loi")
    assert enemy.has_effect("CCStun")            # upgraded
    assert not enemy.has_effect("DebuffTeLiet")  # replaced, not stacked


def test_l3_riders_skip_negated_hit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(3)
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    run_thien_kiep_procs(session, player, enemy, dmg=0, skill_element="loi")
    assert not enemy.has_effect("DebuffSocDien")
    assert not enemy.has_effect("DebuffTeLiet")


# ── 4. L6 Thiên Phạt — attacker-side evasion shred ──────────────────────────


def test_l6_evasion_shred(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    attacker = _player(6)
    target = _enemy()
    target.evasion_rating = 1_000
    ds = build_defense_stats(target, {}, attacker, spd_evasion_bonus)
    base = build_defense_stats(target, {}, _enemy(), spd_evasion_bonus)
    assert ds.evasion_rating == int(base.evasion_rating * 0.40)  # −60%


def test_l6_shred_absent_below_level(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    attacker = _player(3)  # no L6 yet
    target = _enemy()
    target.evasion_rating = 1_000
    ds = build_defense_stats(target, {}, attacker, spd_evasion_bonus)
    base = build_defense_stats(target, {}, _enemy(), spd_evasion_bonus)
    assert ds.evasion_rating == base.evasion_rating


# ── 5. L9 Thiên Kiếp — extra hit + Chấp Hành execute ────────────────────────


def test_l9_sustained_extra_hit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.crit_rating = 0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99
    skill = registry.get_skill(_LOI_ATTACK)
    cast_skill(session, player, enemy, _LOI_ATTACK, dict(skill), mp_cost=0)
    # A 1-hit skill lands 2 strikes; sustained — a second cast repeats it.
    follow_ups = [l for l in session.log if "liên kích" in l]
    assert len(follow_ups) == 1
    cast_skill(session, player, enemy, _LOI_ATTACK, dict(skill), mp_cost=0)
    follow_ups = [l for l in session.log if "liên kích" in l]
    assert len(follow_ups) == 2


def test_l9_execute_below_hp_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy(hp=100_000)
    enemy.hp = 15_000                       # below the 20% gate
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0        # roll lands
    assert enemy.rank == "tinh_anh"         # executable tier
    run_thien_kiep_procs(session, player, enemy, dmg=1_000, skill_element="loi")
    assert enemy.hp == 0                    # executed


def test_l9_no_execute_above_hp_gate(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    enemy = _enemy(hp=100_000)
    enemy.hp = 30_000                       # above the 20% gate
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    run_thien_kiep_procs(session, player, enemy, dmg=1_000, skill_element="loi")
    assert enemy.hp == 30_000


def test_l9_execute_respects_rank_and_world_boss(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    session_rng = lambda: 0.0
    # Elite / boss ranks are exempt.
    for protected_rank in ("dai_nang", "chi_ton", "than_thu", ""):
        enemy = _enemy(hp=100_000, rank=protected_rank)
        enemy.hp = 10_000
        session = _session(player, enemy)
        session.rng.random = session_rng
        run_thien_kiep_procs(session, player, enemy, dmg=1_000, skill_element="loi")
        assert enemy.hp == 10_000, protected_rank
    # World boss flag is exempt even on an executable rank.
    enemy = _enemy(hp=100_000)
    enemy.hp = 10_000
    enemy.is_world_boss = True
    session = _session(player, enemy)
    session.rng.random = session_rng
    run_thien_kiep_procs(session, player, enemy, dmg=1_000, skill_element="loi")
    assert enemy.hp == 10_000


def test_l9_ten_stack_auto_execute_spends_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.tk_van_loi_stacks = 10
    enemy = _enemy(hp=100_000)
    enemy.hp = 15_000
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99       # the 30% roll FAILS...
    run_thien_kiep_procs(session, player, enemy, dmg=1_000, skill_element="loi")
    assert enemy.hp == 0                    # ...but 10 stacks guarantee it
    assert player.tk_van_loi_stacks == 0    # spent


def test_l9_failed_roll_no_execute_below_gate_stacks(monkeypatch) -> None:
    monkeypatch.setattr(settings, "constitution_process_enabled", True)
    player = _player(9)
    player.tk_van_loi_stacks = 9            # one short of the guarantee
    enemy = _enemy(hp=100_000)
    enemy.hp = 15_000
    session = _session(player, enemy)
    session.rng.random = lambda: 0.99       # roll fails
    run_thien_kiep_procs(session, player, enemy, dmg=1_000, skill_element="loi")
    assert enemy.hp == 15_000
    assert player.tk_van_loi_stacks == 9    # not spent


# ── 6. Dormancy — flag OFF leaves everything inert ──────────────────────────


def test_flag_off_is_inert() -> None:
    assert settings.constitution_process_enabled is False
    player = _player(9)
    assert player.tk_van_loi_cap == 0
    assert player.tk_te_liet_chance == 0.0
    assert player.tk_evasion_shred_pct == 0.0
    assert player.tk_extra_hits == 0
    assert player.tk_execute_chance == 0.0
    enemy = _enemy()
    session = _session(player, enemy)
    session.rng.random = lambda: 0.0
    enemy.hp = 1_000
    run_thien_kiep_procs(session, player, enemy, dmg=1_000, skill_element="loi")
    assert enemy.hp == 1_000                # no riders, no execute
    run_thien_kiep_procs(session, enemy, player, dmg=1_000, skill_element="loi")
    assert player.tk_van_loi_stacks == 0    # no accrual
