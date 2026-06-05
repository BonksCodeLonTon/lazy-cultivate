"""Per-body breakthrough-gate override (Constitution Process engine extension).

A constitution's optional ``process`` block may override the GLOBAL gate tables
(``gates`` / ``ceilings`` / ``xp_to_next``) so a body can gate differently — the
Hoàng Cổ Thánh Thể capstone gates at EVERY level (ceilings 1-8) with its own
"Hoang Cổ" materials and PURE material/trial gating (empty ``xp_to_next``).

These tests pin BOTH paths:
  * the default (``process=None`` / a body without overrides) → the global
    tables, byte-identical to before the extension, and
  * the saint override → the per-body tables.
"""
from __future__ import annotations

from src.data.registry import registry
from src.game.constants.constitution_process import CEILINGS, GATES, XP_TO_NEXT
from src.game.systems.constitution_process import (
    apply_combat_xp,
    breakthrough_preview,
    ceilings_for,
    gates_for,
    is_ceiling,
    resolve_constitution_breakthrough,
    xp_to_next,
    xp_to_next_table_for,
)

_SAINT = "TheChat_HoangCoThanhThe"


def _saint_process() -> dict:
    data = registry.get_constitution(_SAINT)
    assert data is not None, f"{_SAINT!r} must be registered"
    return data["process"]


# ── 1. Resolver defaults — no override → global tables (byte-identity) ───────


def test_resolvers_default_to_globals() -> None:
    assert gates_for(None) is GATES
    assert ceilings_for(None) == CEILINGS
    assert xp_to_next_table_for(None) is XP_TO_NEXT
    # A process block WITHOUT override keys also falls through to the globals.
    plain = {"milestones": [1, 3, 6, 9], "levels": {}}
    assert gates_for(plain) is GATES
    assert ceilings_for(plain) == CEILINGS
    assert xp_to_next_table_for(plain) is XP_TO_NEXT


def test_legacy_signatures_unchanged() -> None:
    # The threaded functions, called the legacy way (no process), behave exactly
    # as before — global ceilings 2/5/8, global XP table.
    assert is_ceiling(2) is True
    assert is_ceiling(3) is False
    assert xp_to_next(1) == XP_TO_NEXT[1]
    assert xp_to_next(2) is None  # global ceiling


# ── 2. Saint override — per-body gates / ceilings / empty XP ─────────────────


def test_saint_gate_table_override() -> None:
    p = _saint_process()
    g = gates_for(p)
    # Keys normalized to int 1..8; the global gates (2/5/8) are NOT used.
    assert sorted(g.keys()) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert g[1]["item_key"] == "HoangCoPhuongVu"
    assert g[1]["qty"] == 2
    assert g[8]["item_key"] == "HoangCoThanhCot"
    assert g[8]["qty"] == 12
    assert g[8]["trial_tier"] == 4


def test_saint_ceilings_are_every_level() -> None:
    p = _saint_process()
    assert ceilings_for(p) == (1, 2, 3, 4, 5, 6, 7, 8)
    # Level 1 is a ceiling for the saint, but NOT under the global tables.
    assert is_ceiling(1, p) is True
    assert is_ceiling(1, None) is False
    assert is_ceiling(8, p) is True


def test_saint_pure_material_gating_no_xp_advance() -> None:
    p = _saint_process()
    assert xp_to_next_table_for(p) == {}
    # Empty XP table → every level is an immediate ceiling: XP never advances.
    assert apply_combat_xp(1, 0, 999, p) == (1, 0, True)
    assert apply_combat_xp(5, 0, 999, p) == (5, 0, True)
    assert xp_to_next(3, p) is None
    # Contrast: the global path DOES advance from a non-ceiling level.
    assert apply_combat_xp(1, 0, 999)[0] > 1


# ── 3. Breakthrough resolution uses the per-body gate ────────────────────────


def test_saint_breakthrough_preview_at_level_1() -> None:
    p = _saint_process()
    # Global preview at level 1 → not a gate; saint preview → the Phượng Vũ gate.
    assert breakthrough_preview(1, 0, 5, False, False)["at_gate"] is False
    pv = breakthrough_preview(1, 0, 5, False, False, p)
    assert pv["at_gate"] is True
    assert pv["gate_item_key"] == "HoangCoPhuongVu"
    assert pv["required_qty"] == 2
    assert pv["trial_tier"] == 1
    assert pv["has_enough"] is True  # owns 5 ≥ 2


def test_saint_breakthrough_resolves_on_its_gate() -> None:
    p = _saint_process()
    # Level 8 → the Holy-Bone seal (×12). Enough mats + won trial + roll under
    # chance (0.22 base) → SUCCESS to level 9, consuming 12 HoangCoThanhCot.
    res = resolve_constitution_breakthrough(
        level=8, gate_fails=0, owned_gate_qty=12,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, roll=0.0, process=p,
    )
    assert res["outcome"] == "SUCCESS"
    assert res["new_level"] == 9
    assert res["consumed"]["HoangCoThanhCot"] == 12

    # Not enough mats → guarded, nothing spent.
    short = resolve_constitution_breakthrough(
        level=8, gate_fails=0, owned_gate_qty=11,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, roll=0.0, process=p,
    )
    assert short["outcome"] == "NOT_ENOUGH_MATERIALS"


def test_saint_level_not_a_global_gate_still_resolves() -> None:
    p = _saint_process()
    # Level 3 is NOT a global gate (globals gate 2/5/8) — under the GLOBAL tables
    # this returns NOT_AT_GATE; under the saint override it's the Lân Tủy gate.
    glob = resolve_constitution_breakthrough(
        level=3, gate_fails=0, owned_gate_qty=99,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, roll=0.0,
    )
    assert glob["outcome"] == "NOT_AT_GATE"
    saint = resolve_constitution_breakthrough(
        level=3, gate_fails=0, owned_gate_qty=99,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, roll=0.0, process=p,
    )
    assert saint["outcome"] == "SUCCESS"
    assert saint["new_level"] == 4
    assert saint["consumed"]["HoangCoLanTuy"] == 4
