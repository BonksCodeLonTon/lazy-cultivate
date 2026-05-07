"""Ensure every (element, realm, grade) cell carries ≥2 skills.

The grade-coverage test requires ≥2 skills per (realm, grade) **per
element**. Hand-writing the missing ~100 skills is unreasonable, so this
generator synthesises filler attack skills for any cell that has fewer
than two, themed by the element's archetype and scaled to the realm +
grade.

Each cell can host up to two distinct fillers:
  * Slot A — primary archetype debuff, "Đoạn Thuật" naming.
  * Slot B — secondary archetype debuff, "Trảm Quyết" naming.

Generated keys follow the pattern ``SkillFiller<Elem>R<Realm>G<Grade>``
(slot A) and ``SkillFiller<Elem>R<Realm>G<Grade>B`` (slot B) so they're
identifiable and re-runnable: re-running won't duplicate fillers
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
SLOTS = ("A", "B")

# Per-element archetype: physical/magical + (primary, secondary) debuffs.
# Both slots keep the same attack_type so elemental identity stays intact;
# slot B picks a different, lore-adjacent debuff for player choice.
ARCHETYPE: dict[str, tuple[str, str, str]] = {
    "kim":   ("physical", "DebuffChayMau",  "DebuffXeRach"),
    "moc":   ("magical",  "DebuffDocTo",    "DebuffTroBuoc"),
    "thuy":  ("magical",  "DebuffLamCham",  "DebuffDongBang"),
    "hoa":   ("magical",  "DebuffThieuDot", "DebuffLoaMat"),
    "tho":   ("physical", "DebuffTroBuoc",  "DebuffLunDat"),
    "phong": ("physical", "DebuffAnPhong",  "DebuffCuonBay"),
    "loi":   ("magical",  "DebuffSocDien",  "DebuffPhaGiap"),
    "quang": ("magical",  "DebuffPhaGiap",  "DebuffLoaMat"),
    "am":    ("magical",  "DebuffLoaMat",   "DebuffBaoMon"),
}

# Vietnamese element name (used for skill name templates).
ELEM_VI: dict[str, str] = {
    "kim": "Kim", "moc": "Mộc", "thuy": "Thủy", "hoa": "Hỏa", "tho": "Thổ",
    "phong": "Phong", "loi": "Lôi", "quang": "Quang", "am": "Ám",
}

# Per-realm baseline base_dmg by grade. Tuned to stay BELOW the *weakest*
# element's curated apex at each realm so the realm-progression test's
# "best skill per element" picker keeps choosing curated skills (whose
# mechanics matter), not generic fillers. Fillers exist as pool variety,
# not as power outliers — they fill empty (realm, grade) cells without
# upgrading any element's effective top-end skill.
#
# Caps per realm (G4 ≤ ~95 % of min curated apex across the 9 elements):
#   R1≤28  R2≤52  R3≤70  R4≤128  R5≤180  R6≤237  R7≤342  R8≤437  R9≤627
REALM_BASE: dict[int, dict[int, int]] = {
    1: {1: 15, 2: 20, 3: 24, 4: 28},
    2: {1: 30, 2: 38, 3: 45, 4: 50},
    3: {1: 42, 2: 53, 3: 62, 4: 70},
    4: {1: 75, 2: 95, 3: 110, 4: 128},
    5: {1: 110, 2: 140, 3: 160, 4: 180},
    6: {1: 145, 2: 180, 3: 210, 4: 235},
    7: {1: 210, 2: 265, 3: 305, 4: 340},
    8: {1: 270, 2: 340, 3: 395, 4: 435},
    9: {1: 380, 2: 480, 3: 555, 4: 625},
}

# Per-grade dmg_scale slope (atk OR matk depending on archetype).
GRADE_SCALE: dict[int, float] = {1: 0.85, 2: 1.0, 3: 1.15, 4: 1.30}

# Per-realm MP / cooldown tuning.
REALM_MP: dict[int, int] = {1: 12, 2: 18, 3: 28, 4: 40, 5: 55, 6: 70, 7: 90, 8: 115, 9: 145}
REALM_CD: dict[int, int] = {1: 1,  2: 2,  3: 2,  4: 3,  5: 3,  6: 4,  7: 4,  8: 5,  9: 5}

GRADE_LABEL: dict[int, str] = {1: "Hoàng", 2: "Huyền", 3: "Địa", 4: "Thiên"}

# Per-slot naming variants — gives slot B a distinct identity in skill picker.
SLOT_NAME_VI: dict[str, str] = {"A": "Đoạn Thuật",     "B": "Trảm Quyết"}
SLOT_NAME_EN: dict[str, str] = {"A": "Severing Art",   "B": "Cleaving Edict"}


def _filler_key(elem: str, realm: int, grade: int, slot: str) -> str:
    """Slot A keeps the legacy keyless suffix for backward compatibility;
    slot B appends ``B`` so existing JSON entries don't get re-keyed.
    """
    suffix = "" if slot == "A" else "B"
    return f"SkillFiller{elem.capitalize()}R{realm}G{grade}{suffix}"


def _build_filler(elem: str, realm: int, grade: int, slot: str = "A") -> dict:
    atk_type, primary, secondary = ARCHETYPE[elem]
    debuff = primary if slot == "A" else secondary
    is_phys = atk_type == "physical"
    base = REALM_BASE[realm][grade]
    scale_val = GRADE_SCALE[grade]
    proc_chance = 0.25 + 0.05 * grade   # G1=0.30, G4=0.45 — finer tuning by tier
    return {
        "key": _filler_key(elem, realm, grade, slot),
        "vi": f"{ELEM_VI[elem]} {GRADE_LABEL[grade]} {SLOT_NAME_VI[slot]}",
        "en": f"{ELEM_VI[elem]} {GRADE_LABEL[grade]} {SLOT_NAME_EN[slot]}",
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


def _audit() -> dict[str, dict[tuple[int, int], int]]:
    """Return ``{element: {(realm, grade): current_count}}`` for cells with
    fewer than 2 skills — the deficit per cell is ``2 - current_count``.
    """
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

    deficits: dict[str, dict[tuple[int, int], int]] = {}
    for elem in ELEMENTS:
        cells: dict[tuple[int, int], int] = {}
        for realm in range(1, 10):
            for grade in (1, 2, 3, 4):
                n = counts[elem].get((realm, grade), 0)
                if n < 2:
                    cells[(realm, grade)] = n
        if cells:
            deficits[elem] = dict(sorted(cells.items()))
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
        # Index existing entries by key; build set of all filler keys so we
        # know which slots are already occupied per cell.
        existing_by_key = {s["key"]: i for i, s in enumerate(skills)}

        # Refresh any existing filler that's been retuned (idempotent regen).
        updated = 0
        prefix = f"SkillFiller{elem.capitalize()}"
        for s in list(skills):
            if not s["key"].startswith(prefix):
                continue
            realm = int(s.get("realm", 0))
            grade = int(s.get("scroll_grade", 0))
            if realm not in REALM_BASE or grade not in REALM_BASE[realm]:
                continue
            slot = "B" if s["key"].endswith("B") else "A"
            rebuilt = _build_filler(elem, realm, grade, slot)
            idx = existing_by_key[s["key"]]
            if skills[idx] != rebuilt:
                skills[idx] = rebuilt
                updated += 1

        # Add fillers until each deficit cell carries 2 skills total.
        cells = deficits.get(elem, {})
        added = 0
        for (realm, grade), current in cells.items():
            need = 2 - current
            for slot in SLOTS:
                if need <= 0:
                    break
                key = _filler_key(elem, realm, grade, slot)
                if key in existing_by_key:
                    continue   # this slot is already filled
                skills.append(_build_filler(elem, realm, grade, slot))
                existing_by_key[key] = len(skills) - 1
                added += 1
                need -= 1

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
