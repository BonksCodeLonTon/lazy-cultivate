"""Constitution Process — Phase 5 pure-resolver + data unit tests.

Covers the two new pure resolvers in
``src.game.systems.constitution_process`` (breakthrough + body-swap), the three
trial enemies (build + realm scaling), the six drop-only materials (registry
load), and a regression anchor that the trial-combat loop was extracted without
breaking tribulation's public surface.

RNG is always injected (``roll`` arg) so the breakthrough tests are
deterministic. Nothing here touches the DB or Discord — the resolvers are pure
and the data tests read the loaded registry only.
"""
from __future__ import annotations

import pytest

from src.data.registry import registry
from src.game.constants.constitution_process import GATES
from src.game.systems.combat import build_enemy_combatant
from src.game.systems.constitution_process import (
    breakthrough_chance,
    resolve_constitution_breakthrough,
    resolve_constitution_swap,
)
from src.game.systems.the_chat import HON_DON_KEY

# Gate 2 → 3 is the cheapest gate (ConsProcThangThe ×3, base 0.85). Used as the
# canonical breakthrough fixture; gate 5/8 differ only in numbers.
_GATE = 2
_GATE_ITEM = GATES[_GATE]["item_key"]          # "ConsProcThangThe"
_GATE_QTY = GATES[_GATE]["qty"]                 # 3
_HO_THE_PHU = "ConsProcHoThePhu"
_DINH_THE_CHAU = "ConsProcDinhTheChau"

_TRIAL_KEYS = [
    "cons_trial_default_t1",
    "cons_trial_default_t2",
    "cons_trial_default_t3",
]
_MATERIAL_KEYS = [
    "ConsProcThangThe",
    "ConsProcLuyenThe",
    "ConsProcDaoThe",
    "ConsProcHoThePhu",
    "ConsProcDinhTheChau",
    "ConsProcHoanTheTinh",
]


# ── Breakthrough resolver ───────────────────────────────────────────────────


def test_breakthrough_trial_lost_consumes_nothing() -> None:
    """A lost trial forfeits nothing — no level/fail/consumption fields at all."""
    out = resolve_constitution_breakthrough(
        _GATE, gate_fails=0, owned_gate_qty=99,
        use_ho_the_phu=True, use_dinh_the_chau=True,
        trial_won=False, roll=0.0,
    )
    assert out == {"outcome": "TRIAL_LOST"}


def test_breakthrough_not_at_gate() -> None:
    """A non-ceiling level rejects with NOT_AT_GATE and no consumption."""
    out = resolve_constitution_breakthrough(
        3, gate_fails=0, owned_gate_qty=99,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, roll=0.0,
    )
    assert out == {"outcome": "NOT_AT_GATE"}


def test_breakthrough_not_enough_materials() -> None:
    """Owning fewer than the gate qty rejects with zero consumption."""
    out = resolve_constitution_breakthrough(
        _GATE, gate_fails=0, owned_gate_qty=_GATE_QTY - 1,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, roll=0.0,
    )
    assert out == {"outcome": "NOT_ENOUGH_MATERIALS"}


def test_breakthrough_success_levels_up_and_consumes_gate_plus_phu() -> None:
    """SUCCESS: level +1, xp reset, fails reset, gate mats + Hộ Thể Phù spent."""
    out = resolve_constitution_breakthrough(
        _GATE, gate_fails=0, owned_gate_qty=_GATE_QTY,
        use_ho_the_phu=True, use_dinh_the_chau=False,
        trial_won=True, roll=0.0,  # roll 0 < any chance → guaranteed success
    )
    assert out["outcome"] == "SUCCESS"
    assert out["new_level"] == _GATE + 1
    assert out["new_xp"] == 0
    assert out["new_fails"] == 0
    assert out["consumed"][_GATE_ITEM] == _GATE_QTY
    assert out["consumed"][_HO_THE_PHU] == 1
    assert out["consumed"][_DINH_THE_CHAU] == 0
    assert out["refunded_gate_mats"] is False


def test_breakthrough_fail_without_dinh_burns_mats_no_level_loss() -> None:
    """FAIL no-Định: gate mats burned, fails +1, level UNCHANGED (no drop)."""
    # No Hộ Thể Phù so the chance stays at base 0.85; roll 0.99 fails.
    out = resolve_constitution_breakthrough(
        _GATE, gate_fails=0, owned_gate_qty=_GATE_QTY,
        use_ho_the_phu=False, use_dinh_the_chau=False,
        trial_won=True, roll=0.99,
    )
    assert out["outcome"] == "FAIL"
    assert out["new_level"] == _GATE  # NO level/realm loss on failure
    assert out["new_fails"] == 1
    assert out["consumed"][_GATE_ITEM] == _GATE_QTY  # burned
    assert out["consumed"][_DINH_THE_CHAU] == 0
    assert out["refunded_gate_mats"] is False


def test_breakthrough_fail_with_dinh_refunds_mats_consumes_pearl() -> None:
    """FAIL +Định: gate mats refunded, Định Thể Châu consumed, fails +1."""
    out = resolve_constitution_breakthrough(
        _GATE, gate_fails=0, owned_gate_qty=_GATE_QTY,
        use_ho_the_phu=False, use_dinh_the_chau=True,
        trial_won=True, roll=0.99,
    )
    assert out["outcome"] == "FAIL"
    assert out["new_level"] == _GATE
    assert out["new_fails"] == 1
    assert out["consumed"][_GATE_ITEM] == 0          # refunded
    assert out["consumed"][_DINH_THE_CHAU] == 1      # pearl still spent
    assert out["refunded_gate_mats"] is True


def test_breakthrough_pity_raises_chance_across_fails() -> None:
    """chance_used strictly increases as gate_fails accumulates (pity)."""
    chances = [
        resolve_constitution_breakthrough(
            _GATE, gate_fails=f, owned_gate_qty=_GATE_QTY,
            use_ho_the_phu=False, use_dinh_the_chau=False,
            trial_won=True, roll=0.0,
        )["chance_used"]
        for f in range(3)
    ]
    assert chances == sorted(chances)
    assert chances[0] < chances[-1]


def test_breakthrough_ho_the_phu_adds_twenty_points() -> None:
    """Hộ Thể Phù lifts the realized chance by exactly +0.20 (pre-clamp)."""
    # Use gate 8 (base 0.40) so +0.20 stays well under the 1.0 clamp.
    gate = 8
    without = breakthrough_chance(gate, fails=0, ho_the_phu=False)
    with_phu = breakthrough_chance(gate, fails=0, ho_the_phu=True)
    assert with_phu == pytest.approx(without + 0.20)


# ── Swap resolver ───────────────────────────────────────────────────────────


def test_swap_rejects_unequipped_target() -> None:
    out = resolve_constitution_swap(
        "ConstitutionA", "ConstitutionA,ConstitutionB", "ConstitutionB", 1
    )
    assert out == {"outcome": "NOT_EQUIPPED"}


def test_swap_rejects_unlocked_but_not_in_tracker() -> None:
    out = resolve_constitution_swap(
        "ConstitutionA,ConstitutionB", "ConstitutionA", "ConstitutionB", 1
    )
    assert out == {"outcome": "NOT_UNLOCKED"}


def test_swap_rejects_already_primary() -> None:
    out = resolve_constitution_swap(
        "ConstitutionA,ConstitutionB", "ConstitutionA,ConstitutionB",
        "ConstitutionA", 1,
    )
    assert out == {"outcome": "ALREADY_PRIMARY"}


def test_swap_rejects_hon_don_target() -> None:
    out = resolve_constitution_swap(
        f"ConstitutionA,{HON_DON_KEY}", f"ConstitutionA,{HON_DON_KEY}",
        HON_DON_KEY, 1,
    )
    assert out == {"outcome": "INVALID_TARGET"}


def test_swap_rejects_no_essence() -> None:
    out = resolve_constitution_swap(
        "ConstitutionA,ConstitutionB", "ConstitutionA,ConstitutionB",
        "ConstitutionB", 0,
    )
    assert out == {"outcome": "NO_ESSENCE"}


def test_swap_success_reorders_and_consumes_essence() -> None:
    """SWAPPED: target moves to slot 0, essence spent, no progress mutation."""
    out = resolve_constitution_swap(
        "ConstitutionA,ConstitutionB,ConstitutionC",
        "ConstitutionA,ConstitutionB,ConstitutionC",
        "ConstitutionC", 1,
    )
    assert out["outcome"] == "SWAPPED"
    assert out["new_constitution_type"] == "ConstitutionC,ConstitutionA,ConstitutionB"
    assert out["consumed"] == {"ConsProcHoanTheTinh": 1}
    # The resolver returns NO level/xp/fails/progress field — the
    # swap-retains-progress invariant lives in storage, never touched here.
    assert "new_level" not in out
    assert "new_xp" not in out


def test_swap_keeps_hon_don_pinned_last() -> None:
    """Hỗn Độn (special 9th slot) stays at the tail after a standard reorder."""
    out = resolve_constitution_swap(
        f"ConstitutionA,ConstitutionB,{HON_DON_KEY}",
        f"ConstitutionA,ConstitutionB,{HON_DON_KEY}",
        "ConstitutionB", 1,
    )
    assert out["outcome"] == "SWAPPED"
    assert out["new_constitution_type"] == f"ConstitutionB,ConstitutionA,{HON_DON_KEY}"


# ── Data: trial enemies + materials ─────────────────────────────────────────


@pytest.mark.parametrize("key", _TRIAL_KEYS)
def test_trial_enemy_builds_and_scales(key: str) -> None:
    """Each trial enemy builds via build_enemy_combatant and scales with realm."""
    low = build_enemy_combatant(key, player_realm_total=15)
    high = build_enemy_combatant(key, player_realm_total=72)
    assert low is not None
    assert high is not None
    assert high.hp > low.hp           # realm scaling lifts the HP pool
    assert high.atk > low.atk
    # The trial blocks declare crit/fdb — confirm the builder consumed them.
    assert low.crit_rating > 0
    assert low.final_dmg_bonus > 0


@pytest.mark.parametrize("key", _MATERIAL_KEYS)
def test_constitution_process_material_loads(key: str) -> None:
    """All six drop-only materials load with the new type and 0 shop price."""
    item = registry.get_item(key)
    assert item is not None, f"{key} must be in the registry"
    assert item["type"] == "constitution_process_material"
    assert item["shop_price_merit"] == 0


# ── Regression: trial-loop extraction ───────────────────────────────────────


def test_run_trial_combat_exposed_and_tribulation_intact() -> None:
    """The extracted helper exists and tribulation still imports/uses it.

    Anchors the Phase-5 engine refactor: ``run_trial_combat`` is a public
    helper, and ``run_tribulation`` is still a coroutine on the manager that
    references it — without coupling to any embed text.
    """
    import inspect

    from src.game.systems.combat.trial_loop import run_trial_combat
    from src.game.systems.tribulation import TribulationManager

    assert inspect.iscoroutinefunction(run_trial_combat)
    assert inspect.iscoroutinefunction(TribulationManager.run_tribulation)
    # run_tribulation delegates the fight to the shared loop.
    src = inspect.getsource(TribulationManager.run_tribulation)
    assert "run_trial_combat" in src
