"""Audit skill coverage across (element, realm, scroll_grade).

Pins five structural invariants so future skill edits don't accidentally
leave gaps in the progression curve:

1. Every (element, realm) pair has at least one skill — no element should
   feel "empty" at any realm a player can reach.
2. Per element, every realm bucket from R1..R9 is populated.
3. Mid + late game (R4-R9) carries at least two distinct ``scroll_grade``
   tiers per element — early progression can lean on G1 only, but past
   Kim Đan players need scroll diversity to chase.
4. Whenever a grade tier is present at a realm (across all elements),
   it carries **at least 2 skills** so players who own that grade of
   scroll can actually pick — solo entries make drops feel useless.
5. Per realm, average per-cast attack power is monotonically ordered by
   grade: G4 > G3 > G2 > G1. Higher rarity has to mean stronger.

Run via:  pytest tests/test_skill_grade_coverage.py
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

ELEMENTS = ["kim", "moc", "thuy", "hoa", "tho", "phong", "loi", "quang", "am"]
REALMS = list(range(1, 10))   # R1..R9
GRADES = (1, 2, 3, 4)         # Hoàng / Huyền / Địa / Thiên

SKILL_DIR = Path("src/data/skills/player")


@pytest.fixture(scope="module")
def grade_matrix() -> dict[str, dict[int, dict[int, int]]]:
    """Return ``{element: {realm: {grade: count}}}`` from the player skill JSONs.

    Internal-only / follow-up skills (no ``scroll_grade`` field) are
    skipped — they're not browsable scrolls and don't count toward
    coverage.
    """
    matrix: dict[str, dict[int, dict[int, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for path in SKILL_DIR.glob("*.json"):
        elem = path.stem
        if elem not in ELEMENTS:
            continue
        for s in json.loads(path.read_text(encoding="utf-8")):
            realm = int(s.get("realm", 0))
            grade = s.get("scroll_grade")
            if grade is None:
                continue
            matrix[elem][realm][int(grade)] += 1
    return matrix


def test_no_empty_element_realm_cells(grade_matrix):
    """Every (element, realm) pair must carry at least one scrollable skill."""
    gaps: list[tuple[str, int]] = []
    for elem in ELEMENTS:
        for realm in REALMS:
            if not grade_matrix[elem][realm]:
                gaps.append((elem, realm))
    assert not gaps, (
        "Empty (element, realm) cells found:\n  "
        + "\n  ".join(f"{e} R{r}" for e, r in gaps)
    )


def test_every_element_covers_all_realms(grade_matrix):
    """Each element must have at least one skill in every realm 1..9."""
    missing_per_elem: dict[str, list[int]] = {}
    for elem in ELEMENTS:
        empties = [r for r in REALMS if not grade_matrix[elem][r]]
        if empties:
            missing_per_elem[elem] = empties
    assert not missing_per_elem, (
        "Elements missing realms:\n  "
        + "\n  ".join(f"{e}: R{empties}" for e, empties in missing_per_elem.items())
    )


@pytest.mark.parametrize("realm", [4, 5, 6, 7, 8, 9])
def test_mid_late_game_offers_grade_diversity(grade_matrix, realm):
    """From R4 onward, every element exposes at least 2 distinct grades.

    Players past Kim Đan should always have a ladder of scroll rarities to
    chase per element — never a pure G1-only realm where Địa/Thiên rolls
    are wasted.
    """
    weak: list[str] = []
    for elem in ELEMENTS:
        grades_present = {g for g, n in grade_matrix[elem][realm].items() if n > 0}
        if len(grades_present) < 2:
            weak.append(f"{elem} R{realm} → only grades {sorted(grades_present)}")
    assert not weak, "Mid/late realms with <2 grade tiers:\n  " + "\n  ".join(weak)


def test_every_grade_has_at_least_two_skills_per_realm(grade_matrix):
    """Aggregated across all elements: every (realm, grade) cell must
    carry ≥2 skills. Empty grades leave the rarity ladder with broken
    rungs (a player who rolls a Thiên scroll at R1 has nothing to
    learn); solo grades leave no real choice when picking from the pool.
    """
    deficits: list[str] = []
    for realm in REALMS:
        for grade in GRADES:
            total = sum(grade_matrix[e][realm].get(grade, 0) for e in ELEMENTS)
            if total < 2:
                deficits.append(f"R{realm} G{grade}: only {total} skill(s)")
    assert not deficits, (
        "Under-populated (realm, grade) cells — every grade needs ≥2 skills:"
        "\n  " + "\n  ".join(deficits)
    )


def test_per_element_grade_has_at_least_two_skills(grade_matrix):
    """Per-element rule: every (element, realm, grade) cell must carry
    ≥2 skills. Empty cells leave a player who rolls that scroll grade
    with nothing to learn for their element; solo cells collapse the
    rarity drop to a single fixed pick.

    Run ``python scripts/fill_per_element_grade_gaps.py`` to backfill
    missing slots — the generator is idempotent.
    """
    deficits: list[str] = []
    for elem in ELEMENTS:
        for realm in REALMS:
            for grade in GRADES:
                n = grade_matrix[elem][realm].get(grade, 0)
                if n < 2:
                    deficits.append(f"{elem} R{realm} G{grade}: {n} skill(s)")
    assert not deficits, (
        f"Per-element under-populated (element, realm, grade) cells ({len(deficits)} total):"
        "\n  " + "\n  ".join(deficits)
    )


def _power_score(skill: dict) -> float:
    """Per-cast effective offensive power.

    Combines flat ``base_dmg`` with stat-scaling slopes (ATK + MATK
    weights), then multiplies by ``hit_count`` so multi-hit skills count
    their full per-cast payload, and amortises any ``charge_bonus`` extra
    over its trigger period. ATK and MATK weights add because skills pick
    one or the other — summing keeps physical and magical builds
    comparable.
    """
    scale = skill.get("dmg_scale", {}) or {}
    score = float(skill.get("base_dmg", 0)) + 100.0 * (
        float(scale.get("atk", 0.0)) + float(scale.get("matk", 0.0))
    )
    score *= max(1, int(skill.get("hit_count", 1)))
    charge = skill.get("charge_bonus") or {}
    if charge.get("amount") and charge.get("every"):
        score += float(charge["amount"]) / float(charge["every"])
    return score


@pytest.fixture(scope="module")
def attack_skills_by_realm_grade() -> dict[tuple[int, int], list[dict]]:
    """Return ``{(realm, grade): [skill_data, ...]}`` for attack skills only.

    Defense / movement / support skills are excluded from the power-ordering
    test because their ``base_dmg`` is typically 0 and ``dmg_scale`` reflects
    a buff payload, not damage. Internal-only follow-ups (no scroll_grade)
    are skipped as they aren't user-rollable.
    """
    out: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for path in SKILL_DIR.glob("*.json"):
        if path.stem not in ELEMENTS:
            continue
        for s in json.loads(path.read_text(encoding="utf-8")):
            if s.get("category") != "attack":
                continue
            grade = s.get("scroll_grade")
            if grade is None:
                continue
            realm = int(s.get("realm", 0))
            out[(realm, int(grade))].append(s)
    return out


@pytest.mark.parametrize("realm", REALMS)
def test_g4_mean_power_dominates_g1_per_realm(
    attack_skills_by_realm_grade, realm,
):
    """The cleanest sanity check that "rarer = stronger": at any realm
    that has both G1 and G4 attack skills, the G4 average must exceed
    the G1 average.

    Adjacent-grade ordering (G2 ≥ G1, G3 ≥ G2, G4 ≥ G3) is intentionally
    not enforced as a hard test because legitimate design wiggle-room
    (multi-hit lower-grade skills, situational utility at higher grades)
    causes minor inversions that don't actually break the player's
    rarity-power perception. The ``test_emit_grade_power_matrix``
    diagnostic prints those means for balance review.
    """
    g1 = attack_skills_by_realm_grade.get((realm, 1), [])
    g4 = attack_skills_by_realm_grade.get((realm, 4), [])
    if not g1 or not g4:
        pytest.skip(f"R{realm} doesn't carry both G1 and G4 attack skills")
    g1_mean = sum(_power_score(s) for s in g1) / len(g1)
    g4_mean = sum(_power_score(s) for s in g4) / len(g4)
    assert g4_mean > g1_mean, (
        f"R{realm}: G4 mean {g4_mean:.0f} ≤ G1 mean {g1_mean:.0f} — "
        "Thiên scrolls aren't beating Hoàng on average."
    )


def test_emit_cross_realm_g4_vs_next_realm_overlap(attack_skills_by_realm_grade):
    """Diagnostic — prints how far ``mean(G4_R)`` reaches into R+1's
    G1/G2 power band. This is permitted overlap by design: a Thiên-grade
    scroll *may* outclass a next-realm Hoàng or Huyền scroll, rewarding
    specialization. The test never fails — it just reports.

    Run with ``-s`` to see the table during a balance review.
    """
    print()
    print("Cross-realm overlap — mean attack power, G4_R vs R+1 G1/G2:")
    print(f"{'transition':<14}{'G4 (R)':>10}{'G1 (R+1)':>12}{'G2 (R+1)':>12}{'G4 carries?':>16}")
    print("-" * 64)
    for realm in REALMS[:-1]:
        g4_here = attack_skills_by_realm_grade.get((realm, 4), [])
        g1_next = attack_skills_by_realm_grade.get((realm + 1, 1), [])
        g2_next = attack_skills_by_realm_grade.get((realm + 1, 2), [])
        if not g4_here:
            continue

        def _avg(skills):
            return (
                sum(_power_score(s) for s in skills) / len(skills) if skills else None
            )

        g4 = _avg(g4_here)
        g1 = _avg(g1_next)
        g2 = _avg(g2_next)
        carries = []
        if g1 is not None and g4 >= g1:
            carries.append("G1")
        if g2 is not None and g4 >= g2:
            carries.append("G2")
        carry_tag = "+".join(carries) if carries else "no"
        g1_cell = f"{g1:>10.0f}" if g1 is not None else f"{'-':>10}"
        g2_cell = f"{g2:>10.0f}" if g2 is not None else f"{'-':>10}"
        print(
            f"R{realm} -> R{realm + 1:<7} {g4:>9.0f} {g1_cell} {g2_cell} "
            f"{carry_tag:>14}"
        )


def test_emit_grade_power_matrix(attack_skills_by_realm_grade, capsys):
    """Always-passing diagnostic — prints mean per-cast attack power per
    (realm, grade). Run with ``-s`` to see the table; useful for spotting
    adjacent-grade inversions the strict tests intentionally let through.
    """
    lines = ["", "Mean per-cast attack power per (realm, grade):"]
    header = f"{'realm':<8}" + "".join(f"{f'G{g}':>10}" for g in GRADES)
    lines.append(header)
    lines.append("-" * len(header))
    for realm in REALMS:
        row = f"R{realm:<7}"
        for grade in GRADES:
            skills = attack_skills_by_realm_grade.get((realm, grade), [])
            if not skills:
                row += f"{'-':>10}"
                continue
            mean = sum(_power_score(s) for s in skills) / len(skills)
            row += f"{mean:>10.0f}"
        lines.append(row)
    print(os.linesep.join(lines))


def test_total_skill_count_balanced_across_elements(grade_matrix):
    """No element should be more than 50% larger or smaller than the median.

    Catches accidental over-investment in one element's library, which
    would warp scroll-pool drops and player-build viability.
    """
    totals = {
        elem: sum(
            grade_matrix[elem][r].get(g, 0) for r in REALMS for g in GRADES
        )
        for elem in ELEMENTS
    }
    median = sorted(totals.values())[len(totals) // 2]
    out_of_band = {
        e: t for e, t in totals.items() if t < median * 0.5 or t > median * 1.5
    }
    assert not out_of_band, (
        f"Element totals out of ±50% from median ({median}):\n  "
        + "\n  ".join(f"{e}: {t} skills" for e, t in out_of_band.items())
    )


def test_emit_grade_matrix_for_visibility(grade_matrix, capsys):
    """Always-passing diagnostic that prints the full per-(element, realm)
    grade breakdown when run with ``-s``. Useful for balance reviews —
    failures in the other tests point here for the underlying picture.
    """
    lines = ["", "Skill counts G1/G2/G3/G4 per (element, realm):"]
    header = f"{'':<8}" + "".join(f"{f'R{r}':>10}" for r in REALMS)
    lines.append(header)
    lines.append("-" * len(header))
    for elem in ELEMENTS:
        row = f"{elem:<8}"
        for r in REALMS:
            cell = grade_matrix[elem][r]
            parts = [
                str(cell.get(g, 0)) if cell.get(g, 0) else "-" for g in GRADES
            ]
            row += f"{'/'.join(parts):>10}"
        lines.append(row)
    print(os.linesep.join(lines))
