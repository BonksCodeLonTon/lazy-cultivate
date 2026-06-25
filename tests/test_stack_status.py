"""Stack-status engine: characterization of the legacy expiry-reset behavior
plus the new declarative stacking layer (EffectMeta.stackable / max_stack /
expire + Combatant.effect_stacks).

``test_legacy_*`` pins the CURRENT behavior of ``tick_effects`` — every
per-mechanic ``<kind>_stacks`` / charge field zeroes when its backing effect
ends — so the refactor that replaces the hand-written ``if`` block with a
data-driven table stays byte-identical.

``test_declarative_*`` exercises the new generic path: a ``stackable`` effect
banks stacks (capped at ``max_stack``) on each application and drops them on
expiry, with zero bespoke Combatant fields.
"""
from __future__ import annotations

import pytest

from src.game.engine import effects as effects_mod
from src.game.engine.effects import EffectMeta
from src.game.systems.combatant import Combatant


def _combatant() -> Combatant:
    return Combatant(
        key="t", name="T", hp=100, hp_max=100, mp=50, mp_max=50,
        spd=10, element="hoa",
    )


# (effect_key, fields zeroed on its expiry) — derived from the pre-refactor
# hand-written block in tick_effects; this is the contract being frozen.
_LEGACY_BINDINGS = [
    ("DebuffThieuDot",          ("burn_stacks",)),
    ("DebuffChanHoa",           ("chan_hoa_stacks",)),
    ("DebuffNghiepHoa",         ("nghiep_hoa_stacks",)),
    ("DebuffNghiepHoaHongLien", ("nghiep_hoa_stacks",)),
    ("DebuffUMinh",             ("u_minh_stacks",)),
    ("DebuffPhuongHoa",         ("phuong_hoa_stacks",)),
    ("DebuffHoaVan",            ("hoa_van_stacks",)),
    ("BuffLuuLyTinhHoa",        ("luu_ly_tinh_hoa_stacks",)),
    ("DebuffChayMau",           ("bleed_stacks",)),
    ("DebuffSocDien",           ("shock_stacks",)),
    ("DebuffDocTo",             ("poison_stacks",)),
    ("DebuffNhuocThuyAn",       ("thuy_mark_stacks",)),
    ("DebuffCuuKhuc", ("cuu_khuc_stacks", "cuu_khuc_atk_reduce_active",
                       "cuu_khuc_res_shred_active")),
    ("BuffPhuDao",              ("phu_dao_altitude",)),
    ("DebuffPhongNhanThuc",     ("phong_nhan_thuc_stacks",)),
    ("DebuffTranSonHa",         ("tran_son_ha_stacks",)),
    ("DebuffLoiKiepAn",         ("loi_kiep_an_stacks",)),
    ("BuffVoTuongPhong",        ("vo_tuong_phong_charges",)),
]


@pytest.mark.parametrize("effect_key,fields", _LEGACY_BINDINGS,
                         ids=[b[0] for b in _LEGACY_BINDINGS])
def test_legacy_stack_fields_reset_on_expiry(effect_key, fields):
    c = _combatant()
    for f in fields:
        setattr(c, f, 5)
    c.apply_effect(effect_key, 1)          # duration 1 → expires this tick
    expired = c.tick_effects()
    assert effect_key in expired
    for f in fields:
        assert getattr(c, f) == 0, f"{effect_key}: {f} not reset on expiry"


def test_legacy_field_not_reset_while_effect_active():
    """A still-active effect must NOT zero its stack field (only expiry does)."""
    c = _combatant()
    c.burn_stacks = 4
    c.apply_effect("DebuffThieuDot", 3)    # lasts past this tick
    c.tick_effects()
    assert c.burn_stacks == 4              # still burning
    assert c.has_effect("DebuffThieuDot")


# ── New declarative stacking layer ───────────────────────────────────────────

_STACK_KEY = "TestStackBuff"


@pytest.fixture
def synthetic_stack_effect():
    """Register a synthetic ``stackable`` effect (max_stack 3) for the test."""
    kind = next(iter(effects_mod.EFFECTS.values())).kind  # borrow a valid kind
    effects_mod.EFFECTS[_STACK_KEY] = EffectMeta(
        key=_STACK_KEY, vi="Test", en="Test", kind=kind,
        description_vi="synthetic stackable buff", stackable=True, max_stack=3,
    )
    try:
        yield _STACK_KEY
    finally:
        effects_mod.EFFECTS.pop(_STACK_KEY, None)


def test_declarative_stack_accumulates_and_caps(synthetic_stack_effect):
    c = _combatant()
    c.apply_effect(_STACK_KEY, 5)
    assert c.stacks_of(_STACK_KEY) == 1
    c.apply_effect(_STACK_KEY, 5)
    assert c.stacks_of(_STACK_KEY) == 2
    for _ in range(5):
        c.apply_effect(_STACK_KEY, 5)
    assert c.stacks_of(_STACK_KEY) == 3          # capped at max_stack


def test_declarative_stack_custom_increment(synthetic_stack_effect):
    c = _combatant()
    c.apply_effect(_STACK_KEY, 5, stacks=2)
    assert c.stacks_of(_STACK_KEY) == 2


def test_declarative_stack_dropped_on_expiry(synthetic_stack_effect):
    c = _combatant()
    c.apply_effect(_STACK_KEY, 1, stacks=3)
    assert c.stacks_of(_STACK_KEY) == 3
    expired = c.tick_effects()
    assert _STACK_KEY in expired
    assert c.stacks_of(_STACK_KEY) == 0          # whole stack dropped on expiry
    assert _STACK_KEY not in c.effect_stacks


def test_declarative_stack_scaling_source(synthetic_stack_effect):
    """``stack:<effect_key>`` resolves to effect_stacks for declarative stacks."""
    from src.game.engine.effects import _resolve_scaling_source as _resolve  # type: ignore
    c = _combatant()
    c.apply_effect(_STACK_KEY, 5, stacks=2)
    assert _resolve(c, f"stack:{_STACK_KEY}") == 2.0


def test_non_stackable_effect_does_not_bank():
    """An ordinary (non-stackable) effect must not touch effect_stacks."""
    c = _combatant()
    c.apply_effect("DebuffThieuDot", 3)
    assert c.effect_stacks == {}


# ── Free-floating runtime counters (separate stack_counters store) ───────────


def test_free_floating_counter_uses_separate_store():
    c = _combatant()
    c.thanh_quang_stacks = 4
    c.harmony_stacks = 3
    assert c.thanh_quang_stacks == 4 and c.harmony_stacks == 3
    assert c.stack_counters == {"thanh_quang": 4, "harmony": 3}
    assert c.effect_stacks == {}              # kept apart from effect-bound stacks


def test_free_floating_counter_survives_effect_expiry():
    c = _combatant()
    c.lietdiem_burn_stacks = 9
    c.apply_effect("DebuffThieuDot", 1)       # unrelated effect expires this tick
    c.tick_effects()
    assert c.lietdiem_burn_stacks == 9        # free counters skip the expiry sweep


def test_free_floating_counter_scaling_source():
    from src.game.engine.effects import _resolve_scaling_source as _resolve
    c = _combatant()
    c.harmony_stacks = 5
    assert _resolve(c, "stat:harmony_stacks") == 5.0
    assert _resolve(c, "stack:harmony") == 5.0
