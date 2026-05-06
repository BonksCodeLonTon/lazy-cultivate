"""Reassign ``scroll_grade`` on player skills so the per-realm averages
satisfy G4 > G3 > G2 > G1 (matches the new ``test_skill_grade_coverage``
invariants). Targeted edits only — most skills keep their existing
grade; this script just moves the obvious outliers.

Run from repo root:
    python scripts/rebalance_skill_grades.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

SKILL_DIR = Path("src/data/skills/player")

# Direct (key → new_grade) reassignments. Picked to lift high-power
# new-mechanic skills out of the wrong grade and slot them at the rarity
# their per-cast power deserves, so the per-realm apex monotonically
# increases G1 → G2 → G3 → G4.
GRADE_OVERRIDES: dict[str, int] = {
    # ── R9 apex multi-hit / charge skills → G4 (Thiên) ────────────────
    "SkillKimSevenSwords_R9":     4,
    "SkillPhongHurricaneStorm_R9":4,
    "SkillFireDetonate_R9":       4,
    # ── R9 mid-power mechanic skills → G3 (Địa) ───────────────────────
    "SkillThoEarthQuakeMulti":    3,
    "SkillThuyTidalCharge":       3,
    "SkillAmNineHellsChain_R9":   3,
    "SkillLoiShockFinisher_R9":   3,
    # ── R9 utility / summon skills → G2 (Huyền) ───────────────────────
    "SkillMocAncientTreeSummon":  2,
    "SkillQuangDivineLightSummon":2,
    # ── R3 multi-hit too strong for G2 → G3 ──────────────────────────
    "SkillThuyDoubleStrike":      3,
    # ── Multi-stack burst skills hugely outpace their grade — bump up
    "SkillAtkLoiBurstR5":         3,    # R5 was G2, ~385 power
    "SkillAtkLoiShockBurstR7":    3,    # R7 was G2, ~640 power
    # Loi G3 skills push above G3 averages too — keep at G3 but the bump
    # above pulls G3 max above G2 max.
}


def main() -> None:
    total = 0
    for path in sorted(SKILL_DIR.glob("*.json")):
        skills = json.loads(path.read_text(encoding="utf-8"))
        changed = 0
        for s in skills:
            new_grade = GRADE_OVERRIDES.get(s.get("key"))
            if new_grade is None:
                continue
            if s.get("scroll_grade") == new_grade:
                continue
            s["scroll_grade"] = new_grade
            changed += 1
        if changed:
            path.write_text(
                json.dumps(skills, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        total += changed
        print(f"  {path.name}: {changed} skill(s) regraded")
    print(f"Done. {total} skills reassigned.")


if __name__ == "__main__":
    main()
