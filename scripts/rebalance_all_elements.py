"""Rebalance all elemental player skill files (kim/thuy/hoa/tho/loi/phong/am/quang).

Two passes per element:
1. Auto-regrade existing skills via a power-score heuristic, ranking within
   each (realm, category) bucket. Larger buckets distribute across grades;
   single-skill buckets default to grade 2 unless the skill is very basic
   (no effects, no overrides → grade 1) or signature (multi-effect + override
   + description_vi → grade 3+).
2. Add element-specific new grade 3-4 skills for loot-chase variety. Each
   element gets ~3 new skills targeting realms 3-8 (the meat of the loot
   progression curve).

Run: python scripts/rebalance_all_elements.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


# ── Power scoring & auto-regrading ──────────────────────────────────────────

CC_EFFECTS = {"CCStun", "CCMuted", "CCInterrupt", "CCLockBreak",
              "DebuffDongBang", "DebuffTeLiet"}


def power_score(s: dict) -> float:
    base_dmg = s.get("base_dmg", 0)
    scale = s.get("dmg_scale", {})
    total_scale = scale.get("atk", 0) + scale.get("matk", 0)
    effects = s.get("effects", [])
    has_overrides = bool(s.get("effect_overrides"))
    has_cc = any(e in CC_EFFECTS for e in effects)
    has_lore = bool(s.get("description_vi"))
    has_true_dmg = s.get("true_dmg_pct", 0) > 0
    mp = s.get("mp_cost", 0)
    cd = s.get("cooldown", 1)
    return (
        base_dmg * total_scale
        + len(effects) * 30
        + (100 if has_cc else 0)
        + (50 if has_overrides else 0)
        + (80 if has_lore else 0)
        + (120 if has_true_dmg else 0)
        + mp * 1.5
        - (cd - 1) * 8
    )


def auto_regrade(skills: list[dict]) -> None:
    """Re-grade skills in-place based on power-score percentile within (realm, category)."""
    buckets: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for s in skills:
        buckets[(s.get("realm", 0), s.get("category", "?"))].append(s)

    for (_realm, _cat), bucket in buckets.items():
        bucket.sort(key=power_score)
        n = len(bucket)
        for i, skill in enumerate(bucket):
            score = power_score(skill)
            if n == 1:
                # Single skill in bucket: judge by absolute markers
                effects = skill.get("effects", [])
                has_ovr = bool(skill.get("effect_overrides"))
                has_lore = bool(skill.get("description_vi"))
                has_true_dmg = skill.get("true_dmg_pct", 0) > 0
                if not effects and not has_ovr:
                    grade = 1
                elif has_lore and len(effects) >= 3 and has_ovr:
                    grade = 4
                elif has_true_dmg or (len(effects) >= 3 and has_ovr):
                    grade = 3
                elif has_ovr or len(effects) >= 2:
                    grade = 2
                else:
                    grade = 1
            elif n == 2:
                grade = 1 if i == 0 else 2
                # Promote to 3 if upper skill has signature markers
                if i == 1:
                    has_lore = bool(skill.get("description_vi"))
                    if has_lore and len(skill.get("effects", [])) >= 3:
                        grade = 3
            elif n == 3:
                grade = (1, 2, 3)[i]
            else:
                # 4+ skills: linear distribution across 1..4
                grade = min(4, max(1, int(round(1 + 3 * i / (n - 1)))))

            # Hard upgrade to g4 for skills with strong signature markers
            has_lore = bool(skill.get("description_vi"))
            has_true_dmg = skill.get("true_dmg_pct", 0) > 0
            n_effects = len(skill.get("effects", []))
            has_ovr = bool(skill.get("effect_overrides"))
            if has_true_dmg or (has_lore and n_effects >= 4 and has_ovr):
                grade = 4

            skill["scroll_grade"] = grade


# ── New skill definitions per element ────────────────────────────────────────
# Each list = ~3 new skills/element targeting realms 3-8 (loot chase sweet spot).
# Skills are grade 3 (rare specialty) or grade 4 (signature epic).

NEW_SKILLS_BY_ELEMENT: dict[str, list[dict]] = {
    "kim": [
        {
            "key": "SkillKimDragonSlayer_R5",
            "vi": "Trảm Long Kiếm Quyết",
            "en": "Dragon-Slayer Sword Art",
            "realm": 5, "scroll_grade": 4, "category": "attack",
            "element": "kim", "attack_type": "physical",
            "dmg_scale": {"atk": 1.2, "matk": 0.0},
            "mp_cost": 65, "cooldown": 4, "base_dmg": 200,
            "effects": ["DebuffPhaGiap", "DebuffChayMau", "DebuffXeRach"],
            "formation_key": None,
            "description_vi": "Kiếm khí trảm long — phá giáp 22%, áp Chảy Máu và Xé Rách cùng lúc.",
            "effect_overrides": {
                "DebuffPhaGiap": {"stat_bonus": {"final_dmg_reduce": -0.22}, "duration": 4},
                "DebuffChayMau": {"duration": 4},
                "DebuffXeRach":  {"stat_bonus": {"res_all": -0.12}, "duration": 3},
            },
        },
        {
            "key": "SkillKimVoidEdge_R7",
            "vi": "Hư Không Kiếm Phong",
            "en": "Void Sword Edge",
            "realm": 7, "scroll_grade": 3, "category": "attack",
            "element": "kim", "attack_type": "physical",
            "dmg_scale": {"atk": 1.2, "matk": 0.2},
            "mp_cost": 85, "cooldown": 4, "base_dmg": 360,
            "effects": ["DebuffPhaGiap", "DebuffXeRach"],
            "formation_key": None,
            "description_vi": "Kiếm phong xé hư không — phá giáp sâu, gây Xé Rách lan rộng.",
            "effect_overrides": {
                "DebuffPhaGiap": {"stat_bonus": {"final_dmg_reduce": -0.22}, "duration": 4},
                "DebuffXeRach":  {"stat_bonus": {"res_all": -0.13}, "duration": 3},
            },
        },
        {
            "key": "SkillKimEternal_R9",
            "vi": "Vĩnh Hằng Kim Quang",
            "en": "Eternal Golden Light",
            "realm": 9, "scroll_grade": 4, "category": "attack",
            "element": "kim", "attack_type": "physical",
            "dmg_scale": {"atk": 1.2, "matk": 0.4},
            "mp_cost": 150, "cooldown": 7, "base_dmg": 700,
            "true_dmg_pct": 0.07,
            "effects": ["DebuffPhaGiap", "DebuffChayMau", "DebuffXeRach", "DebuffBaoMon"],
            "formation_key": None,
            "description_vi": "Vĩnh hằng kim quang — chân thương 7% HP, kết hợp 4 debuff Kim đỉnh cao.",
            "effect_overrides": {
                "DebuffPhaGiap": {"stat_bonus": {"final_dmg_reduce": -0.26}, "duration": 4},
                "DebuffChayMau": {"duration": 4},
                "DebuffXeRach":  {"stat_bonus": {"res_all": -0.135}, "duration": 3},
                "DebuffBaoMon":  {"stat_bonus": {"res_all": -0.085}, "duration": 4},
            },
        },
    ],
    "thuy": [
        {
            "key": "SkillThuyTidalGrasp_R4",
            "vi": "Triều Thủy Khóa Hồn",
            "en": "Tidal Soul Grasp",
            "realm": 4, "scroll_grade": 4, "category": "attack",
            "element": "thuy", "attack_type": "magical",
            "dmg_scale": {"atk": 0.0, "matk": 1.2},
            "mp_cost": 50, "cooldown": 4, "base_dmg": 175,
            "effects": ["DebuffDongBang", "DebuffThuyXuyenThau", "DebuffLamCham"],
            "formation_key": None,
            "description_vi": "Triều thủy khóa hồn — đóng băng tạm thời, xuyên Kháng Thủy, làm chậm.",
            "effect_overrides": {
                "DebuffDongBang":     {"duration": 1},
                "DebuffThuyXuyenThau": {"stat_bonus": {"res_thuy": -0.20}, "duration": 4},
                "DebuffLamCham":       {"stat_bonus": {"spd_pct": -0.30}, "duration": 3},
            },
        },
        {
            "key": "SkillThuyDeepFreeze_R6",
            "vi": "Vực Sâu Hàn Băng",
            "en": "Abyssal Deep Freeze",
            "realm": 6, "scroll_grade": 4, "category": "attack",
            "element": "thuy", "attack_type": "magical",
            "dmg_scale": {"atk": 0.0, "matk": 1.25},
            "mp_cost": 78, "cooldown": 5, "base_dmg": 310,
            "effects": ["DebuffDongBang", "DebuffThuyXuyenThau", "DebuffBaoMon", "DebuffLamCham"],
            "formation_key": None,
            "description_vi": "Vực sâu hàn băng — đóng băng 2 lượt, xuyên kháng, bào mòn chỉ số tổng.",
            "effect_overrides": {
                "DebuffDongBang":      {"duration": 2},
                "DebuffThuyXuyenThau": {"stat_bonus": {"res_thuy": -0.22}, "duration": 4},
                "DebuffBaoMon":        {"stat_bonus": {"res_all": -0.075}, "duration": 4},
                "DebuffLamCham":       {"stat_bonus": {"spd_pct": -0.40}, "duration": 3},
            },
        },
        {
            "key": "SkillThuyOceanKing_R8",
            "vi": "Hải Vương Thủy Triều",
            "en": "Ocean King's Tide",
            "realm": 8, "scroll_grade": 4, "category": "attack",
            "element": "thuy", "attack_type": "magical",
            "dmg_scale": {"atk": 0.4, "matk": 1.25},
            "mp_cost": 120, "cooldown": 6, "base_dmg": 540,
            "effects": ["DebuffDongBang", "DebuffThuyXuyenThau", "DebuffBaoMon",
                        "DebuffLamCham", "CCMuted"],
            "formation_key": None,
            "description_vi": "Hải Vương thủy triều — đóng băng + câm lặng, đỉnh sát thương Thủy R8.",
            "effect_overrides": {
                "DebuffDongBang":      {"duration": 2},
                "DebuffThuyXuyenThau": {"stat_bonus": {"res_thuy": -0.24}, "duration": 4},
                "DebuffBaoMon":        {"stat_bonus": {"res_all": -0.085}, "duration": 4},
                "DebuffLamCham":       {"stat_bonus": {"spd_pct": -0.50}, "duration": 3},
                "CCMuted":             {"duration": 2},
            },
        },
    ],
    "hoa": [
        {
            "key": "SkillHoaSoulFire_R3",
            "vi": "Linh Hồn Hỏa Diệm",
            "en": "Soul Flame",
            "realm": 3, "scroll_grade": 4, "category": "attack",
            "element": "hoa", "attack_type": "magical",
            "dmg_scale": {"atk": 0.0, "matk": 1.15},
            "mp_cost": 35, "cooldown": 4, "base_dmg": 110,
            "effects": ["DebuffThieuDot", "DebuffDotChay", "DebuffHoaXuyenThau"],
            "formation_key": None,
            "description_vi": "Linh hồn hỏa diệm — chồng Thiêu Đốt và Đốt Cháy, xuyên Kháng Hỏa 18%.",
            "effect_overrides": {
                "DebuffThieuDot":      {"dot_pct": 0.05, "duration": 4},
                "DebuffDotChay":       {"dot_pct": 0.04, "duration": 3},
                "DebuffHoaXuyenThau":  {"stat_bonus": {"res_hoa": -0.18}, "duration": 4},
            },
        },
        {
            "key": "SkillHoaPhoenixCry_R5",
            "vi": "Phượng Hoàng Khốc Diệm",
            "en": "Phoenix Cry Flame",
            "realm": 5, "scroll_grade": 4, "category": "attack",
            "element": "hoa", "attack_type": "magical",
            "dmg_scale": {"atk": 0.3, "matk": 1.2},
            "mp_cost": 65, "cooldown": 4, "base_dmg": 200,
            "effects": ["DebuffThieuDot", "DebuffDotChay", "DebuffHoaXuyenThau", "DebuffBaoMon"],
            "formation_key": None,
            "description_vi": "Phượng hoàng khóc — đỉnh Hỏa Đạo R5, chồng 4 hiệu ứng thiêu hủy.",
            "effect_overrides": {
                "DebuffThieuDot":      {"dot_pct": 0.058, "duration": 4},
                "DebuffDotChay":       {"dot_pct": 0.046, "duration": 3},
                "DebuffHoaXuyenThau":  {"stat_bonus": {"res_hoa": -0.21}, "duration": 4},
                "DebuffBaoMon":        {"stat_bonus": {"res_all": -0.07}, "duration": 4},
            },
        },
        {
            "key": "SkillHoaInfernoLord_R8",
            "vi": "Diệm Vực Ma Quân",
            "en": "Inferno Lord",
            "realm": 8, "scroll_grade": 4, "category": "attack",
            "element": "hoa", "attack_type": "magical",
            "dmg_scale": {"atk": 0.4, "matk": 1.25},
            "mp_cost": 122, "cooldown": 6, "base_dmg": 530,
            "effects": ["DebuffThieuDot", "DebuffDotChay", "DebuffHoaXuyenThau",
                        "DebuffBaoMon", "DebuffXeRach"],
            "formation_key": None,
            "description_vi": "Diệm vực ma quân — đỉnh Hỏa R8, kết hợp 5 hiệu ứng thiêu rụi.",
            "effect_overrides": {
                "DebuffThieuDot":      {"dot_pct": 0.068, "duration": 4},
                "DebuffDotChay":       {"dot_pct": 0.054, "duration": 4},
                "DebuffHoaXuyenThau":  {"stat_bonus": {"res_hoa": -0.25}, "duration": 4},
                "DebuffBaoMon":        {"stat_bonus": {"res_all": -0.085}, "duration": 4},
                "DebuffXeRach":        {"stat_bonus": {"res_all": -0.135}, "duration": 3},
            },
        },
    ],
    "tho": [
        {
            "key": "SkillThoLandShaker_R4",
            "vi": "Đại Địa Chấn Hung",
            "en": "Land Shaker",
            "realm": 4, "scroll_grade": 4, "category": "attack",
            "element": "tho", "attack_type": "physical",
            "dmg_scale": {"atk": 1.2, "matk": 0.0},
            "mp_cost": 52, "cooldown": 4, "base_dmg": 175,
            "effects": ["DebuffLunDat", "DebuffPhaGiap", "DebuffTroBuoc"],
            "formation_key": None,
            "description_vi": "Đại địa chấn hung — lún đất, phá giáp, trói buộc địch tại chỗ.",
            "effect_overrides": {
                "DebuffLunDat":  {"duration": 3},
                "DebuffPhaGiap": {"stat_bonus": {"final_dmg_reduce": -0.20}, "duration": 4},
                "DebuffTroBuoc": {"stat_bonus": {"spd_pct": -0.65}},
            },
        },
        {
            "key": "SkillThoIronWill_R5",
            "vi": "Thiết Tâm Bất Phá",
            "en": "Iron Will Unbroken",
            "realm": 5, "scroll_grade": 4, "category": "defense",
            "element": "tho", "attack_type": "magical",
            "dmg_scale": {"atk": 0.0, "matk": 0.0},
            "mp_cost": 60, "cooldown": 5, "base_dmg": 0,
            "effects": ["BuffDaiDia", "BuffTrongTo", "BuffCanCo", "HpRegen"],
            "formation_key": None,
            "description_vi": "Thiết Tâm Bất Phá — tổ hợp Đại Địa + Trọng Thổ + Căn Cơ + hồi 10% HP.",
            "effect_overrides": {
                "BuffDaiDia":  {"stat_bonus": {"final_dmg_reduce": 0.32}, "duration": 4},
                "BuffTrongTo": {"stat_bonus": {"final_dmg_reduce": 0.13, "res_all": 0.07}, "duration": 4},
                "BuffCanCo":   {"stat_bonus": {"final_dmg_reduce": 0.18}, "duration": 4},
            },
        },
        {
            "key": "SkillThoSacredEarth_R8",
            "vi": "Thánh Địa Vĩnh Hằng",
            "en": "Sacred Earth Eternal",
            "realm": 8, "scroll_grade": 4, "category": "attack",
            "element": "tho", "attack_type": "physical",
            "dmg_scale": {"atk": 1.2, "matk": 0.4},
            "mp_cost": 125, "cooldown": 6, "base_dmg": 530,
            "effects": ["DebuffLunDat", "DebuffPhaGiap", "DebuffTroBuoc", "DebuffBaoMon"],
            "formation_key": None,
            "description_vi": "Thánh địa vĩnh hằng — đỉnh Thổ R8, lún đất sâu, phá giáp triệt để.",
            "effect_overrides": {
                "DebuffLunDat":  {"duration": 4},
                "DebuffPhaGiap": {"stat_bonus": {"final_dmg_reduce": -0.26}, "duration": 4},
                "DebuffTroBuoc": {"stat_bonus": {"spd_pct": -0.85}},
                "DebuffBaoMon":  {"stat_bonus": {"res_all": -0.085}, "duration": 4},
            },
        },
    ],
    "loi": [
        {
            "key": "SkillLoiTrueLightning_R3",
            "vi": "Chân Lôi Cuồng Phách",
            "en": "True Lightning Soul-Strike",
            "realm": 3, "scroll_grade": 4, "category": "attack",
            "element": "loi", "attack_type": "magical",
            "dmg_scale": {"atk": 0.0, "matk": 1.15},
            "mp_cost": 38, "cooldown": 4, "base_dmg": 115,
            "effects": ["DebuffSocDien", "DebuffTeLiet", "DebuffSetDanh"],
            "formation_key": None,
            "description_vi": "Chân Lôi đỉnh đạo — sốc điện, tê liệt, sét đánh chí mạng.",
            "effect_overrides": {
                "DebuffSocDien": {"dot_pct": 0.045, "duration": 3},
                "DebuffTeLiet":  {"duration": 1},
                "DebuffSetDanh": {"dot_pct": 0.05, "duration": 3},
            },
        },
        {
            "key": "SkillLoiThunderGod_R7",
            "vi": "Lôi Thần Phẫn Nộ",
            "en": "Thunder God's Wrath",
            "realm": 7, "scroll_grade": 4, "category": "attack",
            "element": "loi", "attack_type": "magical",
            "dmg_scale": {"atk": 0.3, "matk": 1.25},
            "mp_cost": 95, "cooldown": 5, "base_dmg": 400,
            "effects": ["DebuffSocDien", "DebuffTeLiet", "DebuffSetDanh", "DebuffBaoMon"],
            "formation_key": None,
            "description_vi": "Lôi Thần phẫn nộ — đỉnh Lôi R7, chồng 4 hiệu ứng sét và bào mòn.",
            "effect_overrides": {
                "DebuffSocDien": {"dot_pct": 0.062, "duration": 4},
                "DebuffTeLiet":  {"duration": 2},
                "DebuffSetDanh": {"dot_pct": 0.07, "duration": 3},
                "DebuffBaoMon":  {"stat_bonus": {"res_all": -0.075}, "duration": 4},
            },
        },
        {
            "key": "SkillLoiSetMortal_R9",
            "vi": "Tận Thế Lôi Đình",
            "en": "Apocalypse Thunder",
            "realm": 9, "scroll_grade": 4, "category": "attack",
            "element": "loi", "attack_type": "magical",
            "dmg_scale": {"atk": 0.5, "matk": 1.3},
            "mp_cost": 158, "cooldown": 7, "base_dmg": 720,
            "true_dmg_pct": 0.08,
            "effects": ["DebuffSocDien", "DebuffTeLiet", "DebuffSetDanh",
                        "DebuffBaoMon", "CCStun"],
            "formation_key": None,
            "description_vi": "Tận thế lôi đình — chân thương 8%, choáng + tê liệt, đỉnh Lôi Đạo Đăng Tiên.",
            "effect_overrides": {
                "DebuffSocDien": {"dot_pct": 0.072, "duration": 4},
                "DebuffTeLiet":  {"duration": 2},
                "DebuffSetDanh": {"dot_pct": 0.085, "duration": 4},
                "DebuffBaoMon":  {"stat_bonus": {"res_all": -0.085}, "duration": 4},
                "CCStun":        {"duration": 2},
            },
        },
    ],
    "phong": [
        {
            "key": "SkillPhongVortex_R3",
            "vi": "Phong Toàn Cuồng Vũ",
            "en": "Wind Vortex",
            "realm": 3, "scroll_grade": 4, "category": "attack",
            "element": "phong", "attack_type": "physical",
            "dmg_scale": {"atk": 1.1, "matk": 0.0},
            "mp_cost": 36, "cooldown": 4, "base_dmg": 110,
            "effects": ["DebuffCuonBay", "DebuffPhongAn", "DebuffLamCham"],
            "formation_key": None,
            "description_vi": "Phong toàn cuồng vũ — cuốn bay, phong ấn né tránh, làm chậm địch.",
            "effect_overrides": {
                "DebuffCuonBay":  {"duration": 1},
                "DebuffPhongAn":  {"stat_bonus": {"evasion_rating": -180}, "duration": 4},
                "DebuffLamCham":  {"stat_bonus": {"spd_pct": -0.32}, "duration": 3},
            },
        },
        {
            "key": "SkillPhongStormGod_R7",
            "vi": "Phong Bão Thần Thoại",
            "en": "Storm God Mythic",
            "realm": 7, "scroll_grade": 4, "category": "attack",
            "element": "phong", "attack_type": "physical",
            "dmg_scale": {"atk": 1.25, "matk": 0.2},
            "mp_cost": 90, "cooldown": 5, "base_dmg": 395,
            "effects": ["DebuffCuonBay", "DebuffPhongAn", "DebuffLamCham", "DebuffBaoMon"],
            "formation_key": None,
            "description_vi": "Phong bão thần thoại — đỉnh Phong R7, cuốn bay liên hoàn, bào mòn kháng tính.",
            "effect_overrides": {
                "DebuffCuonBay":  {"duration": 2},
                "DebuffPhongAn":  {"stat_bonus": {"evasion_rating": -260}, "duration": 4},
                "DebuffLamCham":  {"stat_bonus": {"spd_pct": -0.55}, "duration": 3},
                "DebuffBaoMon":   {"stat_bonus": {"res_all": -0.075}, "duration": 4},
            },
        },
        {
            "key": "SkillPhongTempest_R8",
            "vi": "Cuồng Phong Hủy Diệt",
            "en": "Annihilating Tempest",
            "realm": 8, "scroll_grade": 4, "category": "attack",
            "element": "phong", "attack_type": "physical",
            "dmg_scale": {"atk": 1.3, "matk": 0.3},
            "mp_cost": 120, "cooldown": 6, "base_dmg": 525,
            "effects": ["DebuffCuonBay", "DebuffPhongAn", "DebuffLamCham",
                        "DebuffBaoMon", "DebuffXeRach"],
            "formation_key": None,
            "description_vi": "Cuồng phong hủy diệt — đỉnh Phong R8, kết hợp 5 hiệu ứng quét sạch chiến trường.",
            "effect_overrides": {
                "DebuffCuonBay":  {"duration": 2},
                "DebuffPhongAn":  {"stat_bonus": {"evasion_rating": -300}, "duration": 4},
                "DebuffLamCham":  {"stat_bonus": {"spd_pct": -0.65}, "duration": 3},
                "DebuffBaoMon":   {"stat_bonus": {"res_all": -0.085}, "duration": 4},
                "DebuffXeRach":   {"stat_bonus": {"res_all": -0.135}, "duration": 3},
            },
        },
    ],
    "am": [
        {
            "key": "SkillAmSoulCage_R4",
            "vi": "Hồn Lao U Cấm",
            "en": "Soul Cage Imprisonment",
            "realm": 4, "scroll_grade": 4, "category": "attack",
            "element": "am", "attack_type": "magical",
            "dmg_scale": {"atk": 0.0, "matk": 1.2},
            "mp_cost": 52, "cooldown": 4, "base_dmg": 175,
            "effects": ["ApplySoulDrain", "DebuffXeRach", "DebuffCatDut"],
            "formation_key": None,
            "description_vi": "Hồn lao u cấm — Hồn Phệ giảm HP max, Xé Rách + Cắt Đứt linh khí.",
            "effect_overrides": {
                "DebuffXeRach": {"stat_bonus": {"res_all": -0.13}, "duration": 3},
                "DebuffCatDut": {"stat_bonus": {"hp_regen_pct": -0.42}, "duration": 4},
            },
        },
        {
            "key": "SkillAmGhostKing_R8",
            "vi": "Quỷ Vương Hồn Phệ",
            "en": "Ghost King's Devouring",
            "realm": 8, "scroll_grade": 4, "category": "attack",
            "element": "am", "attack_type": "magical",
            "dmg_scale": {"atk": 0.4, "matk": 1.2},
            "mp_cost": 122, "cooldown": 6, "base_dmg": 525,
            "effects": ["ApplySoulDrain", "ApplyStatSteal", "DebuffXeRach",
                        "DebuffCatDut", "DebuffBaoMon"],
            "formation_key": None,
            "description_vi": "Quỷ vương hồn phệ — đỉnh Âm R8, Hồn Phệ + Đạo Pháp cướp chỉ số.",
            "effect_overrides": {
                "DebuffXeRach": {"stat_bonus": {"res_all": -0.135}, "duration": 3},
                "DebuffCatDut": {"stat_bonus": {"hp_regen_pct": -0.55}, "duration": 4},
                "DebuffBaoMon": {"stat_bonus": {"res_all": -0.085}, "duration": 4},
            },
        },
        {
            "key": "SkillAmVoidLord_R9",
            "vi": "Hư Vô Ma Tôn",
            "en": "Void Demon Sovereign",
            "realm": 9, "scroll_grade": 4, "category": "attack",
            "element": "am", "attack_type": "magical",
            "dmg_scale": {"atk": 0.5, "matk": 1.3},
            "mp_cost": 158, "cooldown": 7, "base_dmg": 700,
            "true_dmg_pct": 0.07,
            "effects": ["ApplySoulDrain", "ApplyStatSteal", "DebuffXeRach",
                        "DebuffCatDut", "DebuffBaoMon", "CCMuted"],
            "formation_key": None,
            "description_vi": "Hư vô ma tôn — chân thương 7%, Hồn Phệ + Đạo Pháp + câm lặng.",
            "effect_overrides": {
                "DebuffXeRach": {"stat_bonus": {"res_all": -0.14}, "duration": 3},
                "DebuffCatDut": {"stat_bonus": {"hp_regen_pct": -0.60}, "duration": 4},
                "DebuffBaoMon": {"stat_bonus": {"res_all": -0.09}, "duration": 4},
                "CCMuted":      {"duration": 2},
            },
        },
    ],
    "quang": [
        {
            "key": "SkillQuangHolyShield_R3",
            "vi": "Thánh Quang Hộ Thuẫn",
            "en": "Holy Light Shield",
            "realm": 3, "scroll_grade": 4, "category": "defense",
            "element": "quang", "attack_type": "magical",
            "dmg_scale": {"atk": 0.0, "matk": 0.0},
            "mp_cost": 32, "cooldown": 4, "base_dmg": 0,
            "effects": ["BuffHoangKim", "BuffHoPhap", "HpRegen"],
            "formation_key": None,
            "description_vi": "Thánh quang hộ thuẫn — Hoàng Kim Hộ + Hộ Pháp + hồi 10% HP.",
            "effect_overrides": {
                "BuffHoangKim": {"stat_bonus": {"final_dmg_reduce": 0.16, "res_all": 0.10}, "duration": 4},
                "BuffHoPhap":   {"stat_bonus": {"final_dmg_reduce": 0.22, "crit_res_rating": 90}, "duration": 4},
            },
        },
        {
            "key": "SkillQuangAngelHand_R6",
            "vi": "Thiên Sứ Thần Thủ",
            "en": "Angel's Divine Hand",
            "realm": 6, "scroll_grade": 4, "category": "attack",
            "element": "quang", "attack_type": "magical",
            "dmg_scale": {"atk": 0.0, "matk": 1.25},
            "mp_cost": 80, "cooldown": 5, "base_dmg": 320,
            "effects": ["DebuffPhaGiap", "DebuffBaoMon", "HpRegen"],
            "formation_key": None,
            "description_vi": "Thiên sứ thần thủ — phá giáp địch và hồi 10% HP cho mình mỗi lần đánh.",
            "effect_overrides": {
                "DebuffPhaGiap": {"stat_bonus": {"final_dmg_reduce": -0.22}, "duration": 4},
                "DebuffBaoMon":  {"stat_bonus": {"res_all": -0.075}, "duration": 4},
            },
        },
        {
            "key": "SkillQuangSunGod_R8",
            "vi": "Thái Dương Thần Quang",
            "en": "Sun God's Radiance",
            "realm": 8, "scroll_grade": 4, "category": "attack",
            "element": "quang", "attack_type": "magical",
            "dmg_scale": {"atk": 0.4, "matk": 1.25},
            "mp_cost": 122, "cooldown": 6, "base_dmg": 530,
            "effects": ["DebuffPhaGiap", "DebuffBaoMon", "DebuffXeRach", "CCMuted", "HpRegen"],
            "formation_key": None,
            "description_vi": "Thái Dương Thần Quang — đỉnh Quang R8, phá giáp triệt để + câm lặng.",
            "effect_overrides": {
                "DebuffPhaGiap": {"stat_bonus": {"final_dmg_reduce": -0.26}, "duration": 4},
                "DebuffBaoMon":  {"stat_bonus": {"res_all": -0.085}, "duration": 4},
                "DebuffXeRach":  {"stat_bonus": {"res_all": -0.135}, "duration": 3},
                "CCMuted":       {"duration": 2},
            },
        },
    ],
}


# ── Main ────────────────────────────────────────────────────────────────────

CAT_ORDER = {"attack": 0, "defense": 1, "movement": 2, "passive": 3, "formation": 4}


def process_element(elem: str) -> tuple[int, int, dict[int, int]]:
    path = Path(f"src/data/skills/player/{elem}.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    original_n = len(data)

    auto_regrade(data)

    existing_keys = {s["key"] for s in data}
    added = 0
    for ns in NEW_SKILLS_BY_ELEMENT.get(elem, []):
        if ns["key"] in existing_keys:
            continue
        data.append(ns)
        added += 1

    data.sort(key=lambda s: (
        s.get("realm", 0),
        CAT_ORDER.get(s.get("category", "?"), 99),
        s.get("scroll_grade", 0),
        s.get("mp_cost", 0),
        s.get("key", ""),
    ))

    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    grades = defaultdict(int)
    for s in data:
        grades[s["scroll_grade"]] += 1
    return original_n, added, dict(grades)


def main() -> None:
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    elements = ["kim", "thuy", "hoa", "tho", "loi", "phong", "am", "quang"]
    for elem in elements:
        orig, added, grades = process_element(elem)
        total = orig + added
        g_str = " ".join(f"g{g}={grades.get(g, 0)}" for g in sorted(grades))
        print(f"{elem:6s}: {orig}->{total} (+{added} new) | {g_str}")


if __name__ == "__main__":
    main()
