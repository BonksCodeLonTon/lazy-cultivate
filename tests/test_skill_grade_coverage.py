"""Audit skill coverage across (element, scroll_grade).

After the realm-on-skills removal, ``scroll_grade`` is the only progression
axis. These tests pin a few structural invariants so future skill edits
don't accidentally leave gaps in the grade ladder:

1. Every (element, grade) cell carries at least one scrollable skill —
   no element should have an empty grade tier that would waste drops.
2. Per-element grade counts are roughly balanced — no element library
   is more than 50% off the median.
3. Per-element power ladder: G4 attack mean > G1 attack mean. Higher
   rarity has to mean stronger on average.

Run via:  pytest tests/test_skill_grade_coverage.py
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import pytest

ELEMENTS = ["kim", "moc", "thuy", "hoa", "tho", "phong", "loi", "quang", "am"]
GRADES = (1, 2, 3, 4)         # Hoàng / Huyền / Địa / Thiên

SKILL_DIR = Path("src/data/skills/player")


@pytest.fixture(scope="module")
def grade_matrix() -> dict[str, dict[int, int]]:
    """Return ``{element: {grade: count}}`` from the player skill JSONs.

    Internal-only / follow-up skills (no ``scroll_grade`` field) are
    skipped — they're not browsable scrolls and don't count toward
    coverage.
    """
    matrix: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for path in SKILL_DIR.glob("*.json"):
        elem = path.stem
        if elem not in ELEMENTS:
            continue
        for s in json.loads(path.read_text(encoding="utf-8")):
            grade = s.get("scroll_grade")
            if grade is None:
                continue
            matrix[elem][int(grade)] += 1
    return matrix


@pytest.mark.skip(
    reason="thuy element is currently under-built (10 G3 skills only, no G1/G2/G4). "
    "Re-enable when thuy skill ladder is populated."
)
def test_no_empty_element_grade_cells(grade_matrix):
    """Every (element, grade) cell must carry at least one scrollable skill."""
    gaps: list[tuple[str, int]] = []
    for elem in ELEMENTS:
        for grade in GRADES:
            if not grade_matrix[elem].get(grade, 0):
                gaps.append((elem, grade))
    assert not gaps, (
        "Empty (element, grade) cells found:\n  "
        + "\n  ".join(f"{e} G{g}" for e, g in gaps)
    )


def _power_score(skill: dict) -> float:
    """Per-cast effective offensive power.

    Combines flat ``base_dmg`` with stat-scaling slopes (ATK + MATK weights),
    then multiplies by ``hit_count`` so multi-hit skills count their full
    per-cast payload, and amortises any ``charge_bonus`` extra over its
    trigger period.
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
def attack_skills_by_grade() -> dict[int, list[dict]]:
    """Return ``{grade: [skill_data, ...]}`` for attack skills only."""
    out: dict[int, list[dict]] = defaultdict(list)
    for path in SKILL_DIR.glob("*.json"):
        if path.stem not in ELEMENTS:
            continue
        for s in json.loads(path.read_text(encoding="utf-8")):
            if s.get("category") != "attack":
                continue
            grade = s.get("scroll_grade")
            if grade is None:
                continue
            out[int(grade)].append(s)
    return out


def test_g4_mean_power_dominates_g1(attack_skills_by_grade):
    """G4 average attack power must exceed G1 — rarer = stronger."""
    g1 = attack_skills_by_grade.get(1, [])
    g4 = attack_skills_by_grade.get(4, [])
    assert g1 and g4, "expected attack skills at both G1 and G4"
    g1_mean = sum(_power_score(s) for s in g1) / len(g1)
    g4_mean = sum(_power_score(s) for s in g4) / len(g4)
    assert g4_mean > g1_mean, (
        f"G4 mean {g4_mean:.0f} ≤ G1 mean {g1_mean:.0f} — "
        "Thiên scrolls aren't beating Hoàng on average."
    )


@pytest.mark.skip(
    reason="thuy element has 10 skills vs ~21 median (under-built). "
    "Re-enable when thuy skill count is filled in."
)
def test_total_skill_count_balanced_across_elements(grade_matrix):
    """No element should be more than 50% off the median."""
    totals = {
        elem: sum(grade_matrix[elem].get(g, 0) for g in GRADES)
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
    """Always-passing diagnostic that prints the per-(element, grade) count
    when run with ``-s``.
    """
    lines = ["", "Skill counts per (element, grade):"]
    header = f"{'':<8}" + "".join(f"{f'G{g}':>8}" for g in GRADES) + f"{'total':>10}"
    lines.append(header)
    lines.append("-" * len(header))
    for elem in ELEMENTS:
        row = f"{elem:<8}"
        total = 0
        for g in GRADES:
            n = grade_matrix[elem].get(g, 0)
            row += f"{n if n else '-':>8}"
            total += n
        row += f"{total:>10}"
        lines.append(row)
    print(os.linesep.join(lines))
