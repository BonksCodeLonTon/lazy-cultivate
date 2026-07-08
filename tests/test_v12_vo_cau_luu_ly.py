"""v12 constitution — Vô Cấu Lưu Ly Thể (Spotless Lapis Lazuli Body, universal mythic).

The purity tank (sheet row 27): L1 Minh Tâm Kiến Tánh — 75% chance to resist
every CC class EXCEPT stun (the designed flaw) + digests pills without đan
độc; L3 Lưu Ly Tịnh Hỏa — modest offense/DR plus resonance with the real
``SkillDefLuuLyTinhHoa_R7`` aura (guaranteed cleanse, each cleanse strips one
enemy buff); L6 Vô Cấu Kim Thân — 35% MAGICAL-only reflect (true/physical
exempt; per hit ≤10% of the attacker's max HP) + reuses body #15's
``phys_dmg_reduce_pct`` lane; L9 Vạn Pháp Bất Triêm — 75% chance-shrug of
every NON-CC negative effect except stun (CC is exclusively the L1 lane — no
double-roll), each successful block banking +1 Vô Cấu stack (cap 7) that
ramps the reflect 35%→56% via BuffVanPhapBatTriem scaling. NO revive.

L1/L9 gates live in ``inflict_interceptors`` (pre-stamp registry); the reflect
rides ``apply_reactive_damage(attack_type=...)``; pill purity gates
``alchemy.consume_pill`` off the FLAT ``pill_toxin_immune`` flag (works with
the process flag OFF).
"""
from __future__ import annotations

import random

import pytest

from src.data.registry import registry
from src.game.engine.effects import EFFECTS
from src.game.models.character import Character, CharacterStats
from src.game.systems.combat import (
    CombatSession, build_enemy_combatant, build_player_combatant,
)
from src.game.systems.combat.casting import inflict_debuff
from src.game.systems.combat.procs import apply_reactive_damage
from src.utils.config import settings

_BODY = "TheChat_VoCauLuuLy"
_ENEMY = "TinhKimTho"

_CC_IMMUNE_KEYS = (
    "DebuffDongBang", "CCMuted", "CCInterrupt",
    "DebuffTeLiet", "DebuffLamCham", "DebuffCuonBay",
)


class _ZeroRng(random.Random):
    """Every draw rolls 0.0 — chance gates always fire."""
    def random(self) -> float:  # noqa: D102
        return 0.0


class _OneRng(random.Random):
    """Every draw rolls ~1.0 — chance gates never fire."""
    def random(self) -> float:  # noqa: D102
        return 0.999999


@pytest.fixture(scope="session", autouse=True)
def _load_registry():
    registry.load()


@pytest.fixture()
def _flag_on(monkeypatch):
    monkeypatch.setattr(settings, "constitution_process_enabled", True)


def _make_char(level: int | None = 9) -> Character:
    return Character(
        player_id=1, discord_id=1, name="VoCauTester",
        body_realm=6, body_level=1, qi_realm=6, qi_level=1,
        formation_realm=6, formation_level=1, active_axis="qi",
        constitution_type=_BODY, linh_can=["kim"], linh_can_levels={"kim": 3},
        constitution_levels=({_BODY: level} if level is not None else {}),
        stats=CharacterStats(),
    )


def _holder(level: int = 9):
    p = build_player_combatant(_make_char(level), ["SkillAtkKim1"])
    p.mp = p.mp_max = 99_999
    return p


def _session(player, rng=None):
    enemy = build_enemy_combatant(_ENEMY, player_realm_total=18)
    assert enemy is not None
    return CombatSession(
        player=player, enemy=enemy, player_skill_keys=["SkillAtkKim1"],
        rng=rng or random.Random(1), max_turns=5,
    ), enemy


# ── Data sanity ──────────────────────────────────────────────────────────────


def test_body_registered_universal_mythic():
    data = registry.get_constitution(_BODY)
    assert data is not None
    assert data["rarity"] == "mythic"
    assert data["element"] == "universal"
    process = data["process"]
    assert process["milestones"] == [1, 3, 6, 9]
    for lv in ("1", "3", "6", "9"):
        block = process["levels"][lv]
        assert block["passive_vi"] and block["description_vi"]
        for eff in block["effects"]:
            assert eff in EFFECTS, eff


def test_flat_carries_pill_purity_flag():
    """Pill purity is a FLAT flag — the out-of-combat read must not depend on
    the process flag or stored levels."""
    data = registry.get_constitution(_BODY)
    assert data["stat_bonuses"].get("pill_toxin_immune") is True


# ── L1 Minh Tâm Kiến Tánh — 75% CC resist except stun ───────────────────────


@pytest.mark.parametrize("cc_key", _CC_IMMUNE_KEYS)
def test_l1_resists_cc_when_roll_lands(cc_key, _flag_on):
    p = _holder(1)
    session, enemy = _session(p, rng=_ZeroRng())  # rolls inside the 75% window
    inflict_debuff(session, cc_key, EFFECTS[cc_key], p, actor=enemy)
    assert not p.has_effect(cc_key)


def test_l1_cc_resist_is_chance_not_absolute(_flag_on):
    """A roll above 75% lets the CC land — even at L9 the L9 shrug must NOT
    give CC a second roll (CC is exclusively the L1 lane)."""
    p = _holder(9)
    session, enemy = _session(p, rng=_OneRng())
    inflict_debuff(
        session, "DebuffDongBang", EFFECTS["DebuffDongBang"], p, actor=enemy,
    )
    assert p.has_effect("DebuffDongBang")


def test_l9_cc_block_banks_vo_cau(_flag_on):
    """At L9 (cap set) a successful CC resist also tempers the body."""
    p = _holder(9)
    session, enemy = _session(p, rng=_ZeroRng())
    inflict_debuff(
        session, "DebuffDongBang", EFFECTS["DebuffDongBang"], p, actor=enemy,
    )
    assert not p.has_effect("DebuffDongBang")
    assert p.vo_cau_stacks == 1


def test_l1_stun_still_lands(_flag_on):
    """CCStun is the designed flaw — it must land even at L9."""
    p = _holder(9)
    session, enemy = _session(p, rng=_ZeroRng())
    inflict_debuff(session, "CCStun", EFFECTS["CCStun"], p, actor=enemy)
    assert p.has_effect("CCStun")


def test_l1_soft_debuffs_still_land(_flag_on):
    """L1 only covers CC — a plain DoT (bleed) lands on an L1 body."""
    p = _holder(1)
    session, enemy = _session(p)
    inflict_debuff(
        session, "DebuffChayMau", EFFECTS["DebuffChayMau"], p, actor=enemy,
    )
    assert p.has_effect("DebuffChayMau")


def test_dormant_body_takes_cc_normally():
    """Flag OFF / no levels — the interceptors are inert (dormancy guard)."""
    p = _holder(9)
    p.vc_cc_resist_pct = 0.0
    p.vc_bat_triem_immune_pct = 0.0
    session, enemy = _session(p, rng=_ZeroRng())
    inflict_debuff(
        session, "DebuffDongBang", EFFECTS["DebuffDongBang"], p, actor=enemy,
    )
    assert p.has_effect("DebuffDongBang")


# ── L9 Vạn Pháp Bất Triêm — chance shrug + Vô Cấu stacks ─────────────────────


def test_l9_shrugs_non_cc_debuffs_and_banks_stacks(_flag_on):
    p = _holder(9)
    session, enemy = _session(p, rng=_ZeroRng())
    for _ in range(9):
        inflict_debuff(
            session, "DebuffChayMau", EFFECTS["DebuffChayMau"], p, actor=enemy,
        )
    assert not p.has_effect("DebuffChayMau")
    assert p.vo_cau_stacks == 7  # capped at vo_cau_cap, not 9


def test_l9_shrug_is_chance_not_absolute(_flag_on):
    """Roll above the 75% window → the debuff lands (no total immunity)."""
    p = _holder(9)
    session, enemy = _session(p, rng=_OneRng())
    inflict_debuff(
        session, "DebuffChayMau", EFFECTS["DebuffChayMau"], p, actor=enemy,
    )
    assert p.has_effect("DebuffChayMau")
    assert p.vo_cau_stacks == 0


# ── L6 Vô Cấu Kim Thân — magical-only reflect + phys reduce ──────────────────


def test_l6_reflects_magical_hits_at_35pct(_flag_on):
    p = _holder(6)
    session, enemy = _session(p)
    hp0 = enemy.hp
    apply_reactive_damage(session, enemy, p, 1000, attack_type="magical")
    assert hp0 - enemy.hp == 350


def test_l6_ignores_physical_and_true_hits(_flag_on):
    p = _holder(6)
    session, enemy = _session(p)
    hp0 = enemy.hp
    apply_reactive_damage(session, enemy, p, 1000, attack_type="physical")
    apply_reactive_damage(session, enemy, p, 1000, attack_type="true")
    assert enemy.hp == hp0


def test_l9_stacks_ramp_reflect_to_56(_flag_on):
    p = _holder(9)
    session, enemy = _session(p)
    # Big attacker pool so the 10%-of-attacker-HP clamp doesn't bite here
    # (the clamp itself is pinned by the dedicated cap test below).
    enemy.hp = enemy.hp_max = 100_000
    p.vo_cau_stacks = 7
    hp0 = enemy.hp
    apply_reactive_damage(session, enemy, p, 1000, attack_type="magical")
    # 0.35 + 7 × 0.03 = 0.56 — mirror the engine's float expression so IEEE
    # rounding (0.5599…) can't flake the exact-int comparison.
    assert hp0 - enemy.hp == int(1000 * (0.35 + 7 * 0.03))


def test_reflect_capped_at_10pct_attacker_hp(_flag_on):
    """A huge nuke reflects at most 10% of the ATTACKER's own max HP —
    the reflect chips casters, it never one-shots them."""
    p = _holder(6)
    session, enemy = _session(p)
    hp0 = enemy.hp
    apply_reactive_damage(session, enemy, p, 10_000_000, attack_type="magical")
    assert hp0 - enemy.hp == int(enemy.hp_max * 0.10)


def test_l6_reuses_phys_reduce_lane(_flag_on):
    """The L6 physical reduction rides body #15's existing
    ``phys_dmg_reduce_pct`` lane — no new field."""
    p = _holder(6)
    assert p.phys_dmg_reduce_pct == pytest.approx(0.20)


# ── L3 Lưu Ly Cộng Hưởng — namesake-skill resonance ──────────────────────────


def test_l3_resonance_guarantees_cleanse_and_strips_enemy_buff(_flag_on):
    from src.game.systems.combat.auras.luu_ly import _process_luu_ly_tinh_hoa
    from src.game.systems.combat.context import TurnContext

    p = _holder(3)
    session, enemy = _session(p, rng=_OneRng())  # rolls that would FAIL at 30%
    # Arm the namesake skill's buff + a cleansable debuff on the holder,
    # a fire DoT on the enemy (the aura's trigger), and an enemy buff to strip.
    p.apply_effect("BuffLuuLyTinhHoa", 4)
    p.apply_effect("DebuffLamCham", 3)  # cleansable target — L1 absent at L3? (L1<3 → present)
    enemy.apply_effect("DebuffThieuDot", 3)
    enemy.apply_effect("BuffNhietTinh", 3)

    ctx = TurnContext(actor=p, target=enemy, session=session)
    _process_luu_ly_tinh_hoa(ctx)
    # Resonance forces the 30% roll to 100%: the holder's debuff is cleansed
    # AND the enemy loses its buff.
    assert not p.has_effect("DebuffLamCham")
    assert not enemy.has_effect("BuffNhietTinh")


def test_no_resonance_without_the_milestone(_flag_on):
    from src.game.systems.combat.auras.luu_ly import _process_luu_ly_tinh_hoa
    from src.game.systems.combat.context import TurnContext

    p = _holder(1)  # L1 — resonance unlocks at L3
    session, enemy = _session(p, rng=_OneRng())
    p.apply_effect("BuffLuuLyTinhHoa", 4)
    enemy.apply_effect("DebuffThieuDot", 3)
    enemy.apply_effect("BuffNhietTinh", 3)
    ctx = TurnContext(actor=p, target=enemy, session=session)
    _process_luu_ly_tinh_hoa(ctx)
    assert enemy.has_effect("BuffNhietTinh")  # 30% roll failed, no strip


# ── L1 pill purity (out-of-combat, flag-independent) ─────────────────────────


def _toxic_pill_for(char: Character) -> dict:
    """A pill with dan_doc > 0 that a Phàm-Thể twin of ``char`` can actually
    digest (realm-gated pills return delta 0 as a REFUSAL, which would make
    the purity assertion a false positive)."""
    from src.game.systems.alchemy import consume_pill

    probe = _make_char(level=None)
    probe.constitution_type = "ConstitutionPhamThe"
    for recipe in registry.pill_recipes.values():
        pill = registry.get_pill(recipe["output_pill"])
        if not pill or int(pill.get("dan_doc", 0)) <= 0:
            continue
        result = consume_pill(probe, pill["key"], quality_tier=1)
        if result.applied and result.dan_doc_delta > 0:
            return pill
    pytest.skip("no realm-compatible toxic pill in data")


def test_pill_purity_zeroes_dan_doc():
    from src.game.systems.alchemy import consume_pill

    char = _make_char(level=None)  # no process levels — flat flag only
    pill = _toxic_pill_for(char)
    result = consume_pill(char, pill["key"], quality_tier=1)
    assert result.applied
    assert result.dan_doc_delta == 0


def test_other_bodies_still_accumulate_dan_doc():
    from src.game.systems.alchemy import consume_pill

    char = _make_char(level=None)
    char.constitution_type = "ConstitutionPhamThe"
    pill = _toxic_pill_for(char)
    result = consume_pill(char, pill["key"], quality_tier=1)
    assert result.applied
    assert result.dan_doc_delta > 0
