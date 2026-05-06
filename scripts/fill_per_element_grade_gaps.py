"""Add a complementary skill to every solo (element, realm, grade) cell.

The grade-coverage test now requires ≥2 skills per (realm, grade) **per
element**. Hand-writing ~150 skills is unreasonable, so this generator
synthesises one filler attack skill for each solo cell, themed by the
element's archetype and scaled to the realm + grade.

Generated keys follow the pattern ``SkillFiller<Elem><Realm>G<Grade>`` so
they're identifiable and re-runnable: re-running won't duplicate fillers
already present.

Run from repo root:
    python scripts/fill_per_element_grade_gaps.py
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SKILL_DIR = Path("src/data/skills/player")
ELEMENTS = ["kim", "moc", "thuy", "hoa", "tho", "phong", "loi", "quang", "am"]

# Per-element archetype: physical/magical + a default debuff to flavor.
ARCHETYPE: dict[str, tuple[str, str]] = {
    "kim":   ("physical", "DebuffChayMau"),
    "moc":   ("magical",  "DebuffDocTo"),
    "thuy":  ("magical",  "DebuffLamCham"),
    "hoa":   ("magical",  "DebuffThieuDot"),
    "tho":   ("physical", "DebuffTroBuoc"),
    "phong": ("physical", "DebuffAnPhong"),
    "loi":   ("magical",  "DebuffSocDien"),
    "quang": ("magical",  "DebuffPhaGiap"),
    "am":    ("magical",  "DebuffLoaMat"),
}

# Vietnamese element name (used for skill name templates).
ELEM_VI: dict[str, str] = {
    "kim": "Kim", "moc": "Mộc", "thuy": "Thủy", "hoa": "Hỏa", "tho": "Thổ",
    "phong": "Phong", "loi": "Lôi", "quang": "Quang", "am": "Ám",
}

# Per-realm baseline base_dmg by grade. Tuned to stay BELOW each realm's
# curated apex so the realm-progression test's "best skill per element"
# picker keeps choosing curated skills (whose mechanics matter), not
# generic fillers. Fillers exist as pool variety, not as power outliers.
REALM_BASE: dict[int, dict[int, int]] = {
    1: {1: 25, 2: 35, 3: 45, 4: 55},
    2: {1: 45, 2: 55, 3: 65, 4: 80},
    3: {1: 70, 2: 90, 3: 110, 4: 130},
    4: {1: 110, 2: 135, 3: 160, 4: 190},
    5: {1: 160, 2: 195, 3: 230, 4: 270},
    6: {1: 180, 2: 220, 3: 260, 4: 295},
    7: {1: 240, 2: 290, 3: 340, 4: 385},
    8: {1: 320, 2: 390, 3: 460, 4: 520},
    9: {1: 420, 2: 510, 3: 590, 4: 660},
}

# Per-grade dmg_scale slope (atk OR matk depending on archetype).
GRADE_SCALE: dict[int, float] = {1: 0.85, 2: 1.0, 3: 1.15, 4: 1.30}

# Per-realm MP / cooldown tuning.
REALM_MP: dict[int, int] = {1: 12, 2: 18, 3: 28, 4: 40, 5: 55, 6: 70, 7: 90, 8: 115, 9: 145}
REALM_CD: dict[int, int] = {1: 1,  2: 2,  3: 2,  4: 3,  5: 3,  6: 4,  7: 4,  8: 5,  9: 5}

GRADE_LABEL: dict[int, str] = {1: "Hoàng", 2: "Huyền", 3: "Địa", 4: "Thiên"}


def _build_filler(elem: str, realm: int, grade: int) -> dict:
    atk_type, debuff = ARCHETYPE[elem]
    is_phys = atk_type == "physical"
    base = REALM_BASE[realm][grade]
    scale_val = GRADE_SCALE[grade]
    proc_chance = 0.25 + 0.05 * grade   # G1=0.30, G4=0.45 — finer tuning by tier
    return {
        "key": f"SkillFiller{elem.capitalize()}R{realm}G{grade}",
        "vi": f"{ELEM_VI[elem]} {GRADE_LABEL[grade]} Đoạn Thuật",
        "en": f"{ELEM_VI[elem]} {GRADE_LABEL[grade]} Severing Art",
        "realm": realm,
        "scroll_grade": grade,
        "category": "attack",
        "element": elem,
        "attack_type": atk_type,
        "dmg_scale": {
            "atk":  scale_val if is_phys else 0.0,
            "matk": 0.0 if is_phys else scale_val,
        },
        "mp_cost": REALM_MP[realm] + 5 * grade,
        "cooldown": REALM_CD[realm] + (1 if grade >= 3 else 0),
        "base_dmg": base,
        "effects": [debuff],
        "effect_chances": {debuff: round(proc_chance, 2)},
        "formation_key": None,
    }


def _audit() -> dict[str, list[tuple[int, int]]]:
    """Return ``{element: [(realm, grade), ...]}`` for solo cells."""
    counts: dict[str, dict[tuple[int, int], int]] = defaultdict(lambda: defaultdict(int))
    for path in SKILL_DIR.glob("*.json"):
        elem = path.stem
        if elem not in ELEMENTS:
            continue
        for s in json.loads(path.read_text(encoding="utf-8")):
            grade = s.get("scroll_grade")
            if grade is None:
                continue
            counts[elem][(int(s.get("realm", 0)), int(grade))] += 1

    deficits: dict[str, list[tuple[int, int]]] = {}
    for elem in ELEMENTS:
        solos = [
            (realm, grade)
            for (realm, grade), n in counts[elem].items()
            if n == 1 and 1 <= realm <= 9
        ]
        if solos:
            deficits[elem] = sorted(solos)
    return deficits


def main() -> None:
    deficits = _audit()
    total_added = 0
    total_updated = 0
    for elem in ELEMENTS:
        path = SKILL_DIR / f"{elem}.json"
        if not path.exists():
            continue
        skills = json.loads(path.read_text(encoding="utf-8"))
        # Refresh any existing filler that's been retuned (idempotent regen).
        existing_by_key = {s["key"]: i for i, s in enumerate(skills)}
        updated = 0
        for s in list(skills):
            if not s["key"].startswith(f"SkillFiller{elem.capitalize()}"):
                continue
            realm = int(s.get("realm", 0))
            grade = int(s.get("scroll_grade", 0))
            if realm in REALM_BASE and grade in REALM_BASE[realm]:
                rebuilt = _build_filler(elem, realm, grade)
                idx = existing_by_key[s["key"]]
                if skills[idx] != rebuilt:
                    skills[idx] = rebuilt
                    updated += 1

        # Add fillers for any solo cells that still don't have a filler.
        cells = deficits.get(elem, [])
        added = 0
        for realm, grade in cells:
            new = _build_filler(elem, realm, grade)
            if new["key"] in existing_by_key:
                continue
            skills.append(new)
            existing_by_key[new["key"]] = len(skills) - 1
            added += 1

        if added or updated:
            path.write_text(
                json.dumps(skills, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"  {elem}.json: +{added} new, ~{updated} retuned")
        total_added += added
        total_updated += updated
    print(f"Done. {total_added} new, {total_updated} retuned.")


if __name__ == "__main__":
    main()
