"""Rebalance moc.json — re-grade existing skills based on power, add new skills.

Run with: python scripts/rebalance_moc.py

Re-grading rationale:
- Grade indicates power tier within a realm, not realm itself.
- Grade 1: basic, single effect or none, low mp/dmg relative to realm.
- Grade 2: standard, 1-2 effects, moderate scaling.
- Grade 3: rare specialty, 2-3 effects with overrides, drop-only.
- Grade 4: signature/best-in-tier, multi-effect with strong overrides, drop-only.

For each realm we ensure g4 > g3 > g2 > g1 in raw output (or signature uniqueness)
so the grade label is meaningful in combat, not just rarity.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path("src/data/skills/player/moc.json")

# scroll_grade overrides for existing skills (key → new grade)
REGRADE = {
    "SkillAtkMoc1":          1,
    "SkillSupMoc":           1,
    "SkillAtkMoc_R2":        1,
    "SkillAtkMoc2":          1,  # was 2; no effects, basic vanilla R3
    "SkillDefMoc":           2,
    "SkillAtkMoc_R4":        2,
    "SkillMocPoison":        3,  # was 2; penetration is rare specialty
    "SkillAtkMoc3":          2,  # was 3; vanilla mid R5
    "SkillMocHeal_R5":       3,
    "SkillAtkMoc_R6":        3,
    "SkillAtkMoc_R7":        3,  # was 4; vanilla strong R7
    "SkillMocSoulDrain_R7":  4,  # signature lifesteal
    "SkillAtkMoc_R8":        3,  # was 4; vanilla high R8
    "SkillDefMoc_R8":        3,  # was 4; solid def, not signature
    "SkillMocTitan_R8":      4,  # signature titan form
    "SkillAtkMoc_R9":        3,  # was 4; vanilla peak
    "SkillMocWorldTree_R9":  4,  # signature endgame (true_dmg)
}

# Stat tweaks to keep g4 strictly stronger than g3 within the same realm.
# Each entry maps skill_key → dict of fields to overwrite.
STAT_TWEAKS = {
    # R7: g4 SoulDrain was mp 75 dmg 290; AtkMoc_R7 (g3) is mp 82 dmg 360.
    # Bump SoulDrain so g4 > g3 in raw output.
    "SkillMocSoulDrain_R7": {
        "mp_cost": 80,
        "base_dmg": 380,
    },
}

# New skills authored for this rebalance pass (grade 3-4 loot variety).
NEW_SKILLS = [
    {
        "key": "SkillMocVineCage_R1",
        "vi": "Mộc Lao Cấm Tù",
        "en": "Wood Cage Imprisonment",
        "realm": 1,
        "scroll_grade": 3,
        "category": "attack",
        "element": "moc",
        "attack_type": "magical",
        "dmg_scale": {"atk": 0.0, "matk": 1.0},
        "mp_cost": 16,
        "cooldown": 3,
        "base_dmg": 32,
        "effects": ["DebuffDocTo", "DebuffTroBuoc"],
        "formation_key": None,
        "description_vi": "Dây leo độc trói chặt địch — chồng Độc Tố và Trói Buộc cùng lúc.",
        "effect_overrides": {
            "DebuffDocTo":  {"dot_pct": 0.035},
            "DebuffTroBuoc": {"stat_bonus": {"spd_pct": -0.45}},
        },
    },
    {
        "key": "SkillMocBloodLeaf_R2",
        "vi": "Lá Mộc Đẫm Huyết",
        "en": "Blood-Soaked Wood Leaf",
        "realm": 2,
        "scroll_grade": 3,
        "category": "attack",
        "element": "moc",
        "attack_type": "magical",
        "dmg_scale": {"atk": 0.0, "matk": 1.0},
        "mp_cost": 22,
        "cooldown": 3,
        "base_dmg": 70,
        "effects": ["DebuffChayMau", "DebuffDocTo"],
        "formation_key": None,
        "description_vi": "Lá đao Mộc cứa thân — gây Chảy Máu và Độc Tố cùng lúc.",
        "effect_overrides": {
            "DebuffChayMau": {"duration": 4},
            "DebuffDocTo":   {"dot_pct": 0.045, "duration": 3},
        },
    },
    {
        "key": "SkillMocAncestralOak_R3",
        "vi": "Tổ Mộc Cổ Áp",
        "en": "Ancestral Oak Crash",
        "realm": 3,
        "scroll_grade": 4,
        "category": "attack",
        "element": "moc",
        "attack_type": "magical",
        "dmg_scale": {"atk": 0.3, "matk": 1.1},
        "mp_cost": 38,
        "cooldown": 4,
        "base_dmg": 115,
        "effects": ["DebuffDocTo", "DebuffTroBuoc", "DebuffBaoMon"],
        "formation_key": None,
        "description_vi": "Tổ mộc giáng thế — đè độc, trói chặt, bào mòn kháng tính.",
        "effect_overrides": {
            "DebuffDocTo":  {"dot_pct": 0.052, "duration": 3},
            "DebuffTroBuoc": {"stat_bonus": {"spd_pct": -0.55}},
            "DebuffBaoMon":  {"stat_bonus": {"res_all": -0.065}},
        },
    },
    {
        "key": "SkillMocPrimalVerdance_R4",
        "vi": "Sơ Khai Lục Đạo",
        "en": "Primal Verdance",
        "realm": 4,
        "scroll_grade": 4,
        "category": "attack",
        "element": "moc",
        "attack_type": "magical",
        "dmg_scale": {"atk": 0.3, "matk": 1.15},
        "mp_cost": 52,
        "cooldown": 4,
        "base_dmg": 180,
        "effects": ["DebuffDocTo", "DebuffMocXuyenThau", "DebuffTroBuoc", "HpRegen"],
        "formation_key": None,
        "description_vi": "Sơ khai lục đạo — xuyên 20% Kháng Mộc, hồi 10% HP cho chính mình.",
        "effect_overrides": {
            "DebuffDocTo":         {"dot_pct": 0.055, "duration": 4},
            "DebuffMocXuyenThau":  {"stat_bonus": {"res_moc": -0.20}, "duration": 4},
            "DebuffTroBuoc":       {"stat_bonus": {"spd_pct": -0.65}},
        },
    },
    {
        "key": "SkillMocLifeReap_R5",
        "vi": "Sinh Cơ Cướp Phách",
        "en": "Life Reap",
        "realm": 5,
        "scroll_grade": 4,
        "category": "attack",
        "element": "moc",
        "attack_type": "magical",
        "dmg_scale": {"atk": 0.4, "matk": 1.2},
        "mp_cost": 68,
        "cooldown": 4,
        "base_dmg": 195,
        "effects": ["DebuffDocTo", "DebuffCatDut", "DebuffMocXuyenThau", "HpRegen"],
        "formation_key": None,
        "description_vi": "Sinh cơ cướp phách — chặn hồi phục địch (-42%) và hồi 10% HP cho mình.",
        "effect_overrides": {
            "DebuffDocTo":        {"dot_pct": 0.058, "duration": 4},
            "DebuffCatDut":       {"stat_bonus": {"hp_regen_pct": -0.42}, "duration": 3},
            "DebuffMocXuyenThau": {"stat_bonus": {"res_moc": -0.21}, "duration": 4},
        },
    },
    {
        "key": "SkillMocChthonianGrove_R6",
        "vi": "U Mộc Cấm Khí",
        "en": "Chthonian Grove",
        "realm": 6,
        "scroll_grade": 4,
        "category": "attack",
        "element": "moc",
        "attack_type": "magical",
        "dmg_scale": {"atk": 0.4, "matk": 1.25},
        "mp_cost": 80,
        "cooldown": 5,
        "base_dmg": 320,
        "effects": ["DebuffDocTo", "DebuffTroBuoc", "DebuffBaoMon", "DebuffXeRach", "DebuffCatDut"],
        "formation_key": None,
        "description_vi": "U mộc cấm khí — chồng 5 debuff Mộc, đỉnh sát thương cảnh giới Luyện Hư.",
        "effect_overrides": {
            "DebuffDocTo":   {"dot_pct": 0.062, "duration": 4},
            "DebuffTroBuoc": {"stat_bonus": {"spd_pct": -0.72}},
            "DebuffBaoMon":  {"stat_bonus": {"res_all": -0.075}, "duration": 3},
            "DebuffXeRach":  {"stat_bonus": {"res_all": -0.13}, "duration": 3},
            "DebuffCatDut":  {"stat_bonus": {"hp_regen_pct": -0.45}, "duration": 3},
        },
    },
    {
        "key": "SkillMocAncestralForest_R8",
        "vi": "Tổ Mộc Vạn Diệp Trận",
        "en": "Ancestral Forest Array",
        "realm": 8,
        "scroll_grade": 4,
        "category": "attack",
        "element": "moc",
        "attack_type": "magical",
        "dmg_scale": {"atk": 0.5, "matk": 1.2},
        "mp_cost": 122,
        "cooldown": 6,
        "base_dmg": 525,
        "effects": ["DebuffDocTo", "DebuffTroBuoc", "DebuffBaoMon", "DebuffXeRach", "DebuffCatDut"],
        "formation_key": None,
        "description_vi": "Tổ Mộc Vạn Diệp Trận — kế thừa Tổ Mộc, đỉnh độc tố và bào mòn cảnh giới Đại Thừa.",
        "effect_overrides": {
            "DebuffDocTo":   {"dot_pct": 0.068, "duration": 4},
            "DebuffTroBuoc": {"stat_bonus": {"spd_pct": -0.85}},
            "DebuffBaoMon":  {"stat_bonus": {"res_all": -0.085}, "duration": 4},
            "DebuffXeRach":  {"stat_bonus": {"res_all": -0.136}, "duration": 3},
            "DebuffCatDut":  {"stat_bonus": {"hp_regen_pct": -0.55}, "duration": 4},
        },
    },
]


def main() -> None:
    data = json.loads(PATH.read_text(encoding="utf-8"))

    # Apply re-grading
    for skill in data:
        new_grade = REGRADE.get(skill["key"])
        if new_grade is not None:
            skill["scroll_grade"] = new_grade
        # Apply stat tweaks (mp_cost, base_dmg, etc.)
        tweaks = STAT_TWEAKS.get(skill["key"])
        if tweaks:
            for k, v in tweaks.items():
                skill[k] = v

    # Add new skills
    existing_keys = {s["key"] for s in data}
    for ns in NEW_SKILLS:
        if ns["key"] in existing_keys:
            continue
        data.append(ns)

    # Sort: by realm asc, then category (attack < defense < movement < passive < formation),
    # then scroll_grade asc, then mp_cost asc, then key alphabetical (deterministic).
    cat_order = {"attack": 0, "defense": 1, "movement": 2, "passive": 3, "formation": 4}
    data.sort(key=lambda s: (
        s.get("realm", 0),
        cat_order.get(s.get("category", "?"), 99),
        s.get("scroll_grade", 0),
        s.get("mp_cost", 0),
        s.get("key", ""),
    ))

    PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(data)} moc skills ({len(data) - 17} new).")

    # Distribution report
    from collections import Counter
    grades = Counter(s["scroll_grade"] for s in data)
    print(f"Grade distribution: g1={grades[1]}, g2={grades[2]}, g3={grades[3]}, g4={grades[4]}")


if __name__ == "__main__":
    main()
