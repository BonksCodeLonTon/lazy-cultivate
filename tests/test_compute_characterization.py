"""Golden-master characterization of ``compute_combat_stats``.

Pins the FULL output of the stat pipeline across a broad, deterministic matrix
of characters so a behaviour-preserving refactor (decomposing the ~860-line
``compute_combat_stats`` into staged helpers) can be proven byte-identical.

The matrix exercises every code path the decomposition touches:
  * every constitution body at L9 with the process flag ON (so milestone
    ``stat_bonuses`` — incl. the per-body config flags read by
    ``_read_constitution_flags`` — are actually applied), axis cycled;
  * equipment-merge variants (``equip_stats`` dicts: flat, pct, nested);
  * linh-căn breadth/depth variants;
  * formation + gem variants (when the registry ships formations/gems);
  * a pill-buff + toxicity variant.

The golden snapshot in ``tests/golden/compute_combat_stats.json`` was captured
on the pre-decomposition tree. Regenerate intentionally with::

    python -c "import tests.test_compute_characterization as t; t.regen()"

A drift in ANY field of ANY case fails the test — exactly the signal a stat
refactor must not trip.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from src.data.registry import registry
from src.game.models.character import Character, CharacterStats
from src.game.systems.character_stats import compute_combat_stats

GOLDEN = Path(__file__).parent / "golden" / "compute_combat_stats.json"
_AXES = ("body", "qi", "formation")


def _char(**kw) -> Character:
    base = dict(
        player_id=1, discord_id=1, name="char",
        body_realm=6, body_level=9, qi_realm=6, qi_level=9,
        formation_realm=6, formation_level=9,
        stats=CharacterStats(),
    )
    base.update(kw)
    return Character(**base)


def _build_cases() -> dict[str, tuple[Character, dict]]:
    """Deterministic {case_id: (char, compute_kwargs)} matrix."""
    cases: dict[str, tuple[Character, dict]] = {}

    # 1. Every constitution body at L9 (process applied), axis cycled, with a
    #    mixed linh-căn so the linh_can + constitution + base-stat stages all run.
    for i, key in enumerate(sorted(registry.constitutions)):
        cases[f"body::{key}"] = (
            _char(
                constitution_type=key,
                constitution_levels={key: 9},
                active_axis=_AXES[i % 3],
                linh_can=["kim", "hoa", "thuy"],
                linh_can_levels={"kim": 9, "hoa": 5, "thuy": 3},
            ),
            {},
        )

    # 2. Equipment-merge coverage — flat / pct / nested-dict equip stats.
    eq_variants = [
        {"hp_pct": 0.2, "atk_pct": 0.1, "crit_rating": 50, "res_hoa": 0.2},
        {"element_dmg_all": 0.15, "shield_max_flat": 500, "final_dmg_reduce": 0.1},
        {"matk_pct": 0.25, "life_steal_pct": 0.05, "dot_dmg_bonus": 0.1,
         "element_pen": {"kim": 0.1}},
        {"bleed_on_hit_pct": 0.3, "spd_pct": 0.1, "cooldown_reduce": 0.15},
    ]
    for i, eq in enumerate(eq_variants):
        cases[f"equip::{i}"] = (
            _char(constitution_type="ConstitutionPhamThe", active_axis=_AXES[i % 3]),
            {"equip_stats": eq},
        )

    # 3. Linh-căn breadth/depth.
    cases["lc::broad"] = (
        _char(
            linh_can=["kim", "moc", "thuy", "hoa", "tho"],
            linh_can_levels={"kim": 9, "moc": 9, "thuy": 9, "hoa": 9, "tho": 9},
            active_axis="qi",
        ),
        {},
    )
    cases["lc::single"] = (
        _char(linh_can=["am"], linh_can_levels={"am": 9}, active_axis="body"),
        {},
    )

    # 4. Formation + gems (best-effort — only if the registry ships them).
    form_keys = sorted(getattr(registry, "formations", {}) or {})[:2]
    gem_keys = sorted(getattr(registry, "gems", {}) or {})[:3]
    for fk in form_keys:
        cases[f"formation::{fk}"] = (
            _char(active_formation=fk, active_axis="formation"),
            {"gem_keys": gem_keys},
        )

    # 5. Pill buffs (real ``PILL_BUFF_STATS`` effect keys) and toxicity penalty.
    cases["pills"] = (
        _char(
            pill_buff_counts={
                "buff_speed": 3, "buff_def": 5, "buff_sword_dmg": 4,
                "buff_element_hoa": 6, "buff_element_kim": 2,
            },
            active_axis="qi",
        ),
        {},
    )
    cases["toxicity"] = (
        _char(dan_doc=120, active_axis="body", pill_buff_counts={"buff_speed": 2}),
        {},
    )

    return cases


def _snapshot_all() -> dict:
    """Compute every case with the constitution-process flag ON (restored after)."""
    from src.utils.config import settings

    prev = settings.constitution_process_enabled
    settings.constitution_process_enabled = True
    try:
        out: dict = {}
        for cid, (char, kw) in _build_cases().items():
            out[cid] = dataclasses.asdict(compute_combat_stats(char, **kw))
    finally:
        settings.constitution_process_enabled = prev
    # Normalise through JSON so float reprs match the loaded golden exactly.
    return json.loads(json.dumps(out, sort_keys=True))


def regen() -> None:
    """Re-capture the golden snapshot (run intentionally, never in CI)."""
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(
        json.dumps(_snapshot_all(), indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {GOLDEN} ({len(_build_cases())} cases)")


def test_compute_combat_stats_matches_golden() -> None:
    assert GOLDEN.exists(), "golden missing — run tests.test_compute_characterization.regen()"
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    snap = _snapshot_all()

    assert set(snap) == set(golden), (
        f"case set drift: +{set(snap) - set(golden)} -{set(golden) - set(snap)}"
    )
    for cid in sorted(golden):
        assert snap[cid] == golden[cid], f"compute_combat_stats drift in case {cid!r}"


def test_matrix_is_non_trivial() -> None:
    """Guard the guard: ensure the matrix actually covers every body + extras."""
    cases = _build_cases()
    assert len(cases) >= len(registry.constitutions) + 5
    assert any(c.startswith("equip::") for c in cases)
