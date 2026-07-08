"""Generate Thập Vạn Đại Sơn content — essences, beasts, loot, dungeons.

Bakes four data surfaces:
  • src/data/items/vital_essences.json
  • src/data/enemies/thap_van_dai_son/r01|r03|r05|r07|r09.json
  • src/data/loot_tables/thap_van_dai_son.json
  • src/data/dungeons/thap_van_dai_son.json

Rarity ladder = **distinct beast types**, not re-skins: every rarity tier has
its own beast families with their own Tinh Huyết identity. Zone depth decides
which families spawn (and therefore which rarity drops):

    R1 Ngoại Vi    → 5 normal   families
    R3 Sơn Lâm     → 5 magic    families
    R5 Thâm Sơn    → 5 rare     families + 2 mythic
    R7 Cấm Sơn     → 5 legendary families + 2 mythic
    R9 Sơn Tâm     → 5 legendary (thái cổ variants) + all 4 mythic

Every essence carries an ``awakening`` block: feed a body part enough copies
of the same essence (``threshold`` + 2×part tier) and the part **Giác Tỉnh**
— unlocking the essence's special effect (scaled to that part's realm), and
for legendary/mythic essences a granted combat skill
(``skills/player/vital_awakening.json``, no_scroll).

Stat bands mirror the same-realm entries in ``enemies/normal/realm_XX.json``.
Re-run after any balance change instead of hand-editing the JSON:
    python scripts/gen_thap_van_dai_son.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "data"

# ── Tier scaffolding — mirrors enemies/normal/realm_XX.json bands ────────────
TIERS: dict[int, dict] = {
    1: {"rank": "pho_thong", "hp_scale": 1.5, "mp_regen": 0.04, "skill": "T1"},
    3: {"rank": "tinh_anh",  "hp_scale": 1.5, "mp_regen": 0.05, "skill": "T2"},
    5: {"rank": "hung_manh", "hp_scale": 1.5, "mp_regen": 0.06, "skill": "T3"},
    7: {"rank": "dai_nang",  "hp_scale": 1.6, "mp_regen": 0.07, "skill": "T3"},
    9: {"rank": "tien_thu",  "hp_scale": 1.5, "mp_regen": 0.10, "skill": "R9"},
}

# Base stat bands per tier: (hp, atk, matk, def, spd) for a "balanced" beast.
BANDS: dict[int, tuple[int, int, int, int, int]] = {
    1: (320,   16,  16,  27, 10),
    3: (1950,  55,  55,  55, 11),
    5: (4600, 100, 100,  82, 12),
    7: (4600, 190, 150, 120, 13),
    9: (13000, 130, 120, 165, 17),
}

RARITY_META: dict[str, dict] = {
    "normal":    {"grade": 1, "zones": [1],    "awaken_threshold": 30},
    "magic":     {"grade": 2, "zones": [3],    "awaken_threshold": 24},
    "rare":      {"grade": 3, "zones": [5],    "awaken_threshold": 18},
    "legendary": {"grade": 4, "zones": [7, 9], "awaken_threshold": 12},
}
MYTHIC_AWAKEN_THRESHOLD = 8

# ── Kit power tuning (2026-07 balance pass) ──────────────────────────────────
# The path-power benchmark (Thể Tu vs Khí Tu / Trận Tu) showed the original
# author-scale kits ~2.5× too weak: even the mythic 9/9 bloodline lost duels
# to both other paths' endgame builds. ``_KIT_POWER_MULT`` scales every
# numeric magnitude in base + awakening kits at bake time; threshold-like
# stats (endure, revive, overheal — mirrors body_parts.NO_SCALE_STATS) and
# bools keep their authored values.
_KIT_POWER_MULT = 2.5
_KIT_NO_SCALE = {
    "final_dmg_bonus", "true_dmg_pct", "cooldown_reduce", "final_dmg_reduce",
    "phoenix_revive_pct", "phoenix_revive_buff_pct",
    "endure_threshold_pct", "endure_cooldown", "overheal_to_shield_pct",
    # Conversion rates (see body_parts.NO_SCALE_STATS) — authored value IS
    # the per-part contribution.
    "reflect_pct",
    "bonus_base_dmg_per_self_def_pct", "bonus_base_dmg_per_self_shield_pct",
    "bonus_base_dmg_per_self_hp_pct", "damage_from_heal_pct",
    "damage_bonus_from_evasion_pct", "damage_bonus_from_hp_pct",
    "damage_bonus_from_mp_pct",
    "cleanse_retaliate_dmg_pct",
    # Permanent-mutation proc chances (soul drain / stat steal, 2026-07-08
    # nerf): part-scaling ballooned Đào Ngột's baked 0.10 to ~0.68/1.02 at a
    # full tier-9 stack. The effects are lifetime-capped so excess chance is
    # wasted, but the lanes still must not approach guaranteed from one
    # essence — authored value IS the final chance.
    "soul_drain_on_hit_pct", "stat_steal_on_hit_pct",
}
# Every essence also carries baseline bulk — Thể Tu is the body path and must
# scale HP like one. Injected into base kits (additive with any authored
# hp_pct AFTER the power mult).
_RARITY_HP_PCT = {
    "normal": 0.015, "magic": 0.02, "rare": 0.025,
    "legendary": 0.03, "mythic": 0.05,
}
# Mythic awakenings additionally harden the body (bulk element of the
# divine bloodline — benchmark fix #3).
_MYTHIC_AWAKEN_HP_PCT = 0.05

# ── Beast families ────────────────────────────────────────────────────────────
# One entry per farmable family. Per family:
#   rarity      — which ladder tier (decides zones, grade, threshold)
#   element / second — primary skill element + off-element (anti-whiff pair)
#   arch        — (hp, atk, matk, def, spd_delta) enemy archetype multipliers
#   names       — zone → (vi, en) enemy name
#   item / vi / en — essence item key + beast display name
#   stats       — essence base stat kit (× part_power_mult at read time)
#   awaken      — special-effect kit unlocked at the feed threshold
#   awaken_desc — one-line Vietnamese description of the special effect
#   skill       — optional granted-skill key (legendary tier; mythic below)
FAMILIES: dict[str, dict] = {
    # ── normal (R1) ──────────────────────────────────────────────────────
    "manh_ho": {
        "archetype": "cuong_luc",
        "rarity": "normal", "element": "kim", "second": "loi",
        "arch": (1.00, 1.35, 0.55, 1.00, 0),
        "item": "VitalEssManhHo", "vi": "Mãnh Hổ", "en": "Fierce Tiger",
        "names": {1: ("Xích Nha Hổ", "Red Fang Tiger")},
        "stats": {"atk_pct": 0.03, "crit_rating": 40},
        "desc": "Chú nhập để tăng công kích và bạo kích.",
        "awaken": {"atk_pct": 0.03, "crit_dmg_rating": 60},
        "awaken_desc": "Hổ Phách Nộ — công kích và sát thương bạo kích tăng vọt.",
    },
    "thiet_quy": {
        "archetype": "cuong_the",
        "rarity": "normal", "element": "tho", "second": "thuy",
        "arch": (1.30, 0.70, 0.60, 1.40, -2),
        "item": "VitalEssThietQuy", "vi": "Thiết Quy", "en": "Iron Turtle",
        "names": {1: ("Thạch Giáp Quy", "Stone Shell Turtle")},
        "stats": {"hp_pct": 0.03, "def_bonus": 30},
        "desc": "Chú nhập để cường hóa khí huyết và phòng ngự.",
        "awaken": {"endure_threshold_pct": 0.20, "endure_cooldown": 8,
                   "thorn_pct": 0.02},
        "awaken_desc": ("Quy Tức — đòn chí mạng không thể kết liễu: gồng lại "
                        "ở 20% HP (hồi 8 lượt), mai rùa mọc gai phản chấn."),
    },
    "phi_ung": {
        "archetype": "tan_tiet",
        "rarity": "normal", "element": "phong", "second": "loi",
        "arch": (0.85, 1.15, 0.85, 0.85, 3),
        "item": "VitalEssPhiUng", "vi": "Phi Ưng", "en": "Soaring Hawk",
        "names": {1: ("Thanh Vũ Ưng", "Azure Feather Hawk")},
        "stats": {"spd_bonus": 1, "evasion_rating": 45},
        "desc": "Chú nhập để thân pháp nhanh nhẹn, né tránh siêu phàm.",
        "awaken": {"evasion_rating": 45, "damage_bonus_from_evasion_pct": 0.02},
        "awaken_desc": "Ưng Nhãn Truy Phong — né tránh chuyển hóa thành sát thương.",
    },
    "doc_xa": {
        "archetype": "am_doc",
        "rarity": "normal", "element": "moc", "second": "am",
        "arch": (0.95, 0.90, 1.15, 0.95, 1),
        "item": "VitalEssDocXa", "vi": "Độc Xà", "en": "Venom Serpent",
        "names": {1: ("Thanh Lân Xà", "Green Scale Serpent")},
        "stats": {"poison_on_hit_pct": 0.04,
                  "dot_dmg_bonus_by_kind": {"poison": 0.06}},
        "desc": "Chú nhập để mỗi đòn đánh có cơ hội tẩm độc.",
        "awaken": {"poison_on_hit_pct": 0.04, "dot_leech_pct": 0.02},
        "awaken_desc": "Xà Độc Nhập Tủy — độc tố hút sinh lực về cho bản thân.",
    },
    "linh_ho": {
        "archetype": "linh_phap",
        "rarity": "normal", "element": "am", "second": "quang",
        "arch": (0.90, 0.60, 1.35, 0.90, 1),
        "item": "VitalEssLinhHo", "vi": "Linh Hồ", "en": "Spirit Fox",
        "names": {1: ("Hôi Vĩ Linh Hồ", "Grey-Tail Spirit Fox")},
        "stats": {"matk_pct": 0.03, "mp_regen_pct": 0.01},
        "desc": "Chú nhập để pháp lực thâm hậu, linh khí dồi dào.",
        "awaken": {"matk_pct": 0.02, "mp_leech_pct": 0.02},
        "awaken_desc": "Hồ Mị Đoạt Linh — mỗi đòn đánh hút linh lực đối thủ.",
    },
    # ── magic (R3) ───────────────────────────────────────────────────────
    "hoa_bao": {
        "archetype": "cuong_luc",
        "rarity": "magic", "element": "hoa", "second": "kim",
        "arch": (0.90, 1.30, 0.80, 0.90, 2),
        "item": "VitalEssHoaBao", "vi": "Hỏa Báo", "en": "Flame Leopard",
        "names": {3: ("Liệt Diệm Hỏa Báo", "Blazing Flame Leopard")},
        "stats": {"atk_pct": 0.04, "burn_on_hit_pct": 0.05},
        "desc": "Chú nhập để đòn đánh mang theo liệt diệm thiêu đốt.",
        "awaken": {"bonus_dmg_vs_burn": 0.06,
                   "dot_dmg_bonus_by_kind": {"burn": 0.06}},
        "awaken_desc": "Liệt Diệm Cuồng Bạo — sát thương tăng mạnh lên mục tiêu đang cháy.",
    },
    "bang_lang": {
        "archetype": "linh_phap",
        "rarity": "magic", "element": "thuy", "second": "phong",
        "arch": (0.95, 0.75, 1.25, 0.95, 1),
        "item": "VitalEssBangLang", "vi": "Băng Lang", "en": "Frost Wolf",
        "names": {3: ("Hàn Sương Băng Lang", "Rime Frost Wolf")},
        "stats": {"matk_pct": 0.04, "slow_on_hit_pct": 0.05},
        "desc": "Chú nhập để hàn khí làm chậm bước chân kẻ địch.",
        "awaken": {"matk_pct": 0.02, "freeze_on_skill_chance": 0.04},
        "awaken_desc": "Hàn Băng Phong Ấn — công pháp có cơ hội đóng băng đối thủ.",
    },
    "loi_hau": {
        "archetype": "linh_phap",
        "rarity": "magic", "element": "loi", "second": "phong",
        "arch": (0.95, 1.25, 0.95, 0.90, 2),
        "item": "VitalEssLoiHau", "vi": "Lôi Hầu", "en": "Thunder Ape",
        "names": {3: ("Tử Điện Lôi Hầu", "Violet Lightning Thunder Ape")},
        "stats": {"crit_rating": 55, "shock_on_hit_pct": 0.05},
        "desc": "Chú nhập để đòn đánh tích tụ điện quang tê dại.",
        "awaken": {"shock_on_hit_pct": 0.05, "crit_dmg_rating": 60},
        "awaken_desc": "Tử Điện Xuyên Tâm — điện giật dày hơn, bạo kích nặng hơn.",
    },
    "thach_te": {
        "archetype": "cuong_the",
        "rarity": "magic", "element": "tho", "second": "kim",
        "arch": (1.35, 0.90, 0.50, 1.45, -2),
        "item": "VitalEssThachTe", "vi": "Thạch Tê", "en": "Stone Rhino",
        "names": {3: ("Cự Giáp Thạch Tê", "Great Shell Stone Rhino")},
        "stats": {"def_bonus": 45, "hp_pct": 0.03},
        "desc": "Chú nhập để da thịt rắn như cự nham.",
        "awaken": {"stun_on_hit_pct": 0.04, "def_bonus": 30},
        "awaken_desc": "Tê Giáp Chấn Địa — đòn đánh có cơ hội choáng kẻ địch.",
    },
    "huyet_buc": {
        "archetype": "tan_tiet",
        "rarity": "magic", "element": "am", "second": "moc",
        "arch": (0.85, 1.05, 1.05, 0.85, 3),
        "item": "VitalEssHuyetBuc", "vi": "Huyết Bức", "en": "Blood Bat",
        "names": {3: ("U Dạ Huyết Bức", "Night Blood Bat")},
        "stats": {"life_steal_pct": 0.02, "spd_bonus": 1},
        "desc": "Chú nhập để mỗi đòn đánh hút một phần huyết khí.",
        "awaken": {"life_steal_pct": 0.02, "hp_regen_pct": 0.01},
        "awaken_desc": "Huyết Dạ Trùng Sinh — hút máu mạnh hơn, huyết khí tự hồi.",
    },
    # ── rare (R5) ────────────────────────────────────────────────────────
    "kim_cang_vien": {
        "archetype": "cuong_luc",
        "rarity": "rare", "element": "kim", "second": "tho",
        "arch": (1.10, 1.35, 0.60, 1.15, 0),
        "item": "VitalEssKimCangVien", "vi": "Kim Cang Viên", "en": "Vajra Ape",
        "names": {5: ("Kim Cang Cự Viên", "Great Vajra Ape")},
        "stats": {"atk_pct": 0.06, "crit_rating": 70},
        "desc": "Chú nhập để nắm đấm mang sức mạnh kim cang bất hoại.",
        "awaken": {"multi_strike_pct": 0.06, "atk_pct": 0.02},
        "awaken_desc": "Kim Cang Liên Kích — có cơ hội tung đòn đánh bồi.",
    },
    "phong_ma_dieu": {
        "archetype": "tan_tiet",
        "rarity": "rare", "element": "phong", "second": "loi",
        "arch": (0.85, 1.20, 0.95, 0.85, 3),
        "item": "VitalEssPhongMaDieu", "vi": "Phong Ma Điểu", "en": "Storm Demon Bird",
        "names": {5: ("Phong Ma Quái Điểu", "Storm Demon Strange Bird")},
        "stats": {"evasion_rating": 80, "spd_bonus": 2},
        "desc": "Chú nhập để thân pháp nhanh như cuồng phong.",
        "awaken": {"mark_on_hit_pct": 0.06, "damage_bonus_from_evasion_pct": 0.03},
        "awaken_desc": "Phong Ấn Truy Sát — đòn đánh in dấu phong ấn lên con mồi.",
    },
    "u_minh_ngo_cong": {
        "archetype": "am_doc",
        "rarity": "rare", "element": "moc", "second": "am",
        "arch": (1.00, 0.85, 1.20, 1.00, 1),
        "item": "VitalEssNgoCong", "vi": "U Minh Ngô Công", "en": "Nether Centipede",
        "names": {5: ("U Minh Ngô Công", "Nether Centipede")},
        "stats": {"poison_on_hit_pct": 0.07,
                  "dot_dmg_bonus_by_kind": {"poison": 0.10}},
        "desc": "Chú nhập để kịch độc u minh ăn mòn kẻ địch.",
        "awaken": {"dot_per_stack_pct_bonus": {"poison": 0.02}, "dot_leech_pct": 0.03},
        "awaken_desc": "Vạn Độc Phệ Tâm — mỗi tầng độc cắn sâu hơn, hút sinh lực về.",
    },
    "ngan_giac_loc": {
        "archetype": "thanh_linh",
        "rarity": "rare", "element": "quang", "second": "moc",
        "arch": (1.05, 0.70, 1.20, 1.00, 1),
        "item": "VitalEssNganGiacLoc", "vi": "Ngân Giác Lộc", "en": "Silver-Antler Deer",
        "names": {5: ("Ngân Giác Linh Lộc", "Silver-Antler Spirit Deer")},
        "stats": {"heal_pct": 0.04, "mp_regen_pct": 0.015},
        "desc": "Chú nhập để linh quang thánh khiết nuôi dưỡng cơ thể.",
        "awaken": {"overheal_to_shield_pct": 0.50, "shield_max_flat": 250,
                   "heal_can_crit": True, "cleanse_on_turn_pct": 0.05},
        "awaken_desc": ("Lộc Linh — hồi phục có thể bạo kích; phần hồi dư "
                        "ngưng tụ thành lá chắn thụy quang."),
    },
    "loi_dinh_ngac": {
        "archetype": "linh_phap",
        "rarity": "rare", "element": "loi", "second": "thuy",
        "arch": (1.20, 1.00, 1.15, 1.20, -1),
        "item": "VitalEssLoiDinhNgac", "vi": "Lôi Đình Ngạc", "en": "Thunder Crocodile",
        "names": {5: ("Lôi Đình Cự Ngạc", "Great Thunder Crocodile")},
        "stats": {"matk_pct": 0.05, "shock_on_hit_pct": 0.06},
        "desc": "Chú nhập để hàm răng mang sấm sét nghiền nát giáp trụ.",
        "awaken": {"paralysis_on_crit": True, "shock_on_hit_pct": 0.04},
        "awaken_desc": "Lôi Ngục Toái Giáp — bạo kích làm tê liệt kẻ địch.",
    },
    # ── legendary (R7 + R9) — Tứ Đại Hung Thú + Cửu Anh ─────────────────
    "thao_thiet": {
        "archetype": "cuong_the",
        "rarity": "legendary", "element": "tho", "second": "am",
        "arch": (1.40, 1.10, 0.80, 1.20, -1),
        "item": "VitalEssThaoThiet", "vi": "Thao Thiết", "en": "Taotie",
        "names": {7: ("Thao Thiết Hung Thú", "Taotie Fiend"),
                  9: ("Thao Thiết Thái Cổ", "Primordial Taotie")},
        "stats": {"hp_pct": 0.08, "life_steal_pct": 0.02},
        "desc": "Chú nhập để cơn đói vô tận chuyển hóa thành huyết khí.",
        "awaken": {"damage_bonus_from_hp_pct": 0.04, "hp_pct": 0.03},
        "awaken_desc": "Thôn Phệ Vạn Vật — huyết khí càng dày, sát thương càng nặng.",
        "skill": "VitalSkill_ThonPhe",
    },
    "cung_ky": {
        "archetype": "cuong_luc",
        "rarity": "legendary", "element": "kim", "second": "phong",
        "arch": (1.00, 1.40, 0.70, 1.00, 1),
        "item": "VitalEssCungKy", "vi": "Cùng Kỳ", "en": "Qiongqi",
        "names": {7: ("Cùng Kỳ Hung Thú", "Qiongqi Fiend"),
                  9: ("Cùng Kỳ Thái Cổ", "Primordial Qiongqi")},
        "stats": {"atk_pct": 0.10, "crit_dmg_rating": 150},
        "desc": "Chú nhập để móng vuốt hung thú xé toạc mọi phòng tuyến.",
        "awaken": {"bleed_on_hit_pct": 0.08, "crit_rating": 60},
        "awaken_desc": "Cùng Kỳ Liệt Trảo — đòn đánh xé rách da thịt gây chảy máu.",
        "skill": "VitalSkill_CungKyLietPhong",
    },
    "dao_ngot": {
        "archetype": "am_doc",
        "rarity": "legendary", "element": "am", "second": "tho",
        "arch": (1.05, 0.75, 1.35, 1.05, 0),
        "item": "VitalEssDaoNgot", "vi": "Đào Ngột", "en": "Taowu",
        "names": {7: ("Đào Ngột Hung Thú", "Taowu Fiend"),
                  9: ("Đào Ngột Thái Cổ", "Primordial Taowu")},
        "stats": {"matk_pct": 0.10, "soul_drain_on_hit_pct": 0.10},
        "desc": "Chú nhập để ma niệm cổ xưa gặm nhấm hồn phách kẻ địch.",
        "awaken": {"stat_steal_on_hit_pct": 0.10, "matk_pct": 0.02},
        "awaken_desc": "Đào Ngột Đoạt Phách — cướp đoạt sức mạnh đối thủ qua từng đòn.",
        "skill": "VitalSkill_DaoNgotMaDiem",
    },
    "tat_phuong": {
        "archetype": "am_doc",
        "rarity": "legendary", "element": "hoa", "second": "loi",
        "arch": (0.90, 0.95, 1.35, 0.90, 2),
        "item": "VitalEssTatPhuong", "vi": "Tất Phương", "en": "Bifang",
        "names": {7: ("Tất Phương Thần Điểu", "Bifang Divine Bird"),
                  9: ("Tất Phương Thái Cổ", "Primordial Bifang")},
        "stats": {"element_dmg_bonus": {"hoa": 0.06}, "burn_on_hit_pct": 0.07},
        "desc": "Chú nhập để ngọn lửa độc cước thiêu rụi vạn vật.",
        "awaken": {"dot_can_crit": True,
                   "dot_dmg_bonus_by_kind": {"burn": 0.08}},
        "awaken_desc": "Tất Phương Phần Thiên — thiêu đốt có thể bạo kích.",
        "skill": "VitalSkill_TatPhuongPhanDuc",
    },
    "cuu_anh": {
        "archetype": "linh_phap",
        "rarity": "legendary", "element": "thuy", "second": "am",
        "arch": (1.15, 0.80, 1.30, 1.10, 0),
        "item": "VitalEssCuuAnh", "vi": "Cửu Anh", "en": "Jiuying",
        "names": {7: ("Cửu Anh Thủy Quái", "Jiuying Water Fiend"),
                  9: ("Cửu Anh Thái Cổ", "Primordial Jiuying")},
        "stats": {"matk_pct": 0.07, "mp_regen_pct": 0.02},
        "desc": "Chú nhập để chín đầu thủy quái cuộn trào pháp lực.",
        "awaken": {"mp_leech_pct": 0.04, "damage_bonus_from_mp_pct": 0.01},
        "awaken_desc": "Cửu Anh Hồng Lưu — linh lực càng đầy, sóng dữ càng mạnh.",
        "skill": "VitalSkill_CuuAnhHongLuu",
    },
}

# Mythical divine beasts — bespoke essence kits + awakening + granted skill.
MYTHICS: dict[str, dict] = {
    "chan_long": {
        "archetype": "cuong_luc",
        "element": "loi", "second": "thuy",
        "arch": (1.20, 1.20, 1.20, 1.10, 1),
        "fallback": {5: ["kim_cang_vien", "phong_ma_dieu"],
                     9: ["cung_ky", "cuu_anh"]},
        "tiers": [5, 9],
        "names": {5: ("Giao Long Thập Vạn Sơn", "Flood Dragon of the Myriad Mountains"),
                  9: ("Chân Long Thái Cổ", "Primordial True Dragon")},
        "item": "VitalEssChanLong", "item_vi": "Long Huyết Chân Long",
        "item_en": "True Dragon Vital Essence",
        "stats": {"atk_pct": 0.05, "matk_pct": 0.05,
                  "true_dmg_pct": 0.03, "final_dmg_bonus": 0.02,
                  "accuracy_rating": 35, "crit_res_rating": 25},
        "desc": ("Một giọt long huyết của Chân Long. Bí thuật **Long Uy**: công pháp "
                 "song tu, mỗi đòn mang theo chân thương xuyên thấu mọi phòng ngự."),
        "awaken": {"final_dmg_bonus": 0.02, "atk_pct": 0.02, "matk_pct": 0.02,
                   "element_dmg_bonus": {"loi": 0.03}},
        "awaken_desc": "Long Tức Phần Thiên — hô hấp của Chân Long thức tỉnh trong huyết mạch.",
        "skill": "VitalSkill_LongTucPhanThien",
    },
    "phuong_hoang": {
        "archetype": "thanh_linh",
        "element": "hoa", "second": "phong",
        "arch": (1.05, 1.05, 1.35, 0.95, 2),
        "fallback": {5: ["ngan_giac_loc", "loi_dinh_ngac"],
                     9: ["tat_phuong", "cung_ky"]},
        "tiers": [5, 9],
        "names": {5: ("Hỏa Vũ Chu Điểu", "Flame-Feather Vermilion Bird"),
                  9: ("Phượng Hoàng Niết Bàn", "Nirvana Phoenix")},
        "item": "VitalEssPhuongHoang", "item_vi": "Tinh Huyết Phượng Hoàng",
        "item_en": "Phoenix Vital Essence",
        "stats": {"phoenix_revive_pct": 0.35, "phoenix_revive_buff_pct": 0.10,
                  "element_dmg_bonus": {"hoa": 0.035}, "hp_regen_pct": 0.015,
                  "accuracy_rating": 35, "crit_res_rating": 25},
        "desc": ("Tinh huyết bất diệt của Phượng Hoàng. Bí thuật **Niết Bàn Trùng "
                 "Sinh**: một lần mỗi trận, chết đi sống lại trong biển lửa."),
        "awaken": {"phoenix_revive_buff_pct": 0.10, "hp_regen_pct": 0.015,
                   "element_dmg_bonus": {"hoa": 0.015}},
        "awaken_desc": "Phượng Vũ Cửu Thiên — lửa niết bàn nung nóng từng bộ vị.",
        "skill": "VitalSkill_PhuongVuCuuThien",
    },
    "ky_lan": {
        "archetype": "thanh_linh",
        "element": "quang", "second": "tho",
        "arch": (1.15, 1.10, 1.15, 1.10, 1),
        "fallback": {7: ["tat_phuong", "thao_thiet"],
                     9: ["tat_phuong", "thao_thiet"]},
        "tiers": [7, 9],
        "names": {7: ("Thụy Quang Kỳ Lân", "Auspicious Light Qilin"),
                  9: ("Kỳ Lân Thánh Thú", "Sacred Qilin")},
        "item": "VitalEssKyLan", "item_vi": "Tinh Huyết Kỳ Lân",
        "item_en": "Qilin Vital Essence",
        "stats": {"heal_pct": 0.07, "cleanse_on_turn_pct": 0.06,
                  "res_all": 0.025, "loot_luck_bonus": 0.02,
                  "accuracy_rating": 35, "crit_res_rating": 25},
        "desc": ("Tinh huyết thánh khiết của Kỳ Lân. Bí thuật **Thánh Quang Tẩy "
                 "Lễ**: thanh tẩy tà khí mỗi lượt, vận may theo chân thánh thú."),
        "awaken": {"barrier_on_cleanse": True, "heal_pct": 0.06, "res_all": 0.01,
                   "damage_from_heal_pct": 0.10,
                   "cleanse_retaliate_dmg_pct": 0.12},
        "awaken_desc": "Kỳ Lân Thụy Quang — mỗi lần thanh tẩy sinh ra kết giới hộ thân.",
        "skill": "VitalSkill_KyLanThuyQuang",
    },
    "huyen_vu": {
        "archetype": "cuong_the",
        "element": "thuy", "second": "tho",
        "arch": (1.45, 0.85, 1.00, 1.45, -1),
        "fallback": {7: ["cuu_anh", "dao_ngot"],
                     9: ["cuu_anh", "dao_ngot"]},
        "tiers": [7, 9],
        "names": {7: ("Huyền Vũ Trấn Hải", "Sea-Guarding Black Tortoise"),
                  9: ("Huyền Vũ Thái Cổ", "Primordial Black Tortoise")},
        "item": "VitalEssHuyenVu", "item_vi": "Tinh Huyết Huyền Vũ",
        "item_en": "Black Tortoise Vital Essence",
        "stats": {"shield_max_pct": 0.09, "reflect_pct": 0.03,
                  "final_dmg_reduce": 0.015,
                  "accuracy_rating": 35, "crit_res_rating": 25},
        "desc": ("Tinh huyết trấn hải của Huyền Vũ. Bí thuật **Huyền Giáp Hộ "
                 "Thân**: lá chắn thần quy phản chấn mọi đòn tấn công."),
        "awaken": {"reflect_pct": 0.03, "shield_regen_pct": 0.03,
                   "bonus_base_dmg_per_self_def_pct": 0.05,
                   "bonus_base_dmg_per_self_shield_pct": 0.04},
        "awaken_desc": ("Huyền Vũ Trấn Hải — lá chắn tự tái sinh; phòng ngự và "
                        "lá chắn chính là vũ khí (cộng sát thương theo DEF/khiên)."),
        "skill": "VitalSkill_HuyenVuTranHai",
    },
}

MYTHIC_PREMIUM = (1.35, 1.25, 1.25, 1.15, 1)   # hp, atk, matk, def, spd

# ── Granted awakening skills (skills/player/vital_awakening.json) ─────────────
# ``no_scroll`` keeps these out of scroll synthesis / shop — they can only be
# gained by awakening the matching essence. Damage follows DMG = base + mp.
AWAKENING_SKILLS: list[dict] = [
    # Thôn Phệ: devours 30% of the damage as healing; targets under 30% HP
    # take a bonus true-damage execute chunk ("ăn tươi nuốt sống").
    {"key": "VitalSkill_ThonPhe", "vi": "Thôn Phệ Vạn Vật", "en": "Devour All Things",
     "element": "tho", "attack_type": "physical",
     "dmg_scale": {"atk": 1.5, "matk": 0.0}, "base_dmg": 320, "mp_cost": 150, "cooldown": 6,
     "vital_rider": {"label": "🍖 Thôn Phệ", "heal_pct_of_dmg": 0.30,
                     "execute_below_pct": 0.30, "execute_bonus_pct": 1.0}},
    # Liệt Phong Trảm: 3-slash flurry (each slash rolls its own crit/evade);
    # per-hit numbers sized so the full chain ≈ one heavy legendary nuke.
    {"key": "VitalSkill_CungKyLietPhong", "vi": "Cùng Kỳ Liệt Phong Trảm", "en": "Qiongqi Gale Slash",
     "element": "kim", "attack_type": "physical", "hit_count": 3,
     "dmg_scale": {"atk": 0.75, "matk": 0.0}, "base_dmg": 150, "mp_cost": 160, "cooldown": 6,
     "effects": ["DebuffChayMau"], "effect_chances": {"DebuffChayMau": 0.45}},
    # Đoạt Phách: cripples BOTH attack stats — the taowu gnaws at the
    # target's spirit (DebuffSuyKhi atk -20% + DebuffPhapNhuoc matk -20%).
    {"key": "VitalSkill_DaoNgotMaDiem", "vi": "Đào Ngột Ma Diệm", "en": "Taowu Demon Flame",
     "element": "am", "attack_type": "magical",
     "dmg_scale": {"atk": 0.0, "matk": 1.6}, "base_dmg": 330, "mp_cost": 165, "cooldown": 6,
     "effects": ["DebuffSuyKhi", "DebuffPhapNhuoc"],
     "effect_chances": {"DebuffSuyKhi": 0.75, "DebuffPhapNhuoc": 0.75}},
    # Phần Dực: stamps burn, then DETONATES the target's burn stacks for an
    # instant true-damage burst (15% of the hit per stack, cap 8).
    {"key": "VitalSkill_TatPhuongPhanDuc", "vi": "Tất Phương Phần Dực", "en": "Bifang Burning Wings",
     "element": "hoa", "attack_type": "magical",
     "dmg_scale": {"atk": 0.0, "matk": 1.6}, "base_dmg": 330, "mp_cost": 160, "cooldown": 6,
     "effects": ["DebuffThieuDot"], "effect_chances": {"DebuffThieuDot": 0.6},
     "vital_rider": {"label": "🔥 Phần Dực Bạo Liệt", "detonate_stack": "burn",
                     "detonate_pct_per_stack": 0.15, "detonate_stack_cap": 8}},
    # Hồng Lưu: burns 25% of remaining MP after the cast for bonus true
    # damage (2 dmg per MP); a kill refunds half of the surge.
    {"key": "VitalSkill_CuuAnhHongLuu", "vi": "Cửu Anh Hồng Lưu", "en": "Jiuying Deluge",
     "element": "thuy", "attack_type": "magical",
     "dmg_scale": {"atk": 0.0, "matk": 1.55}, "base_dmg": 320, "mp_cost": 155, "cooldown": 6,
     "vital_rider": {"label": "🌊 Hồng Lưu", "mp_surge_pct_of_current": 0.25,
                     "mp_surge_dmg_per_mp": 2.0, "mp_refund_on_kill_pct": 0.5}},
    # Long Tức: 30% of the breath re-lands as capped true damage (Long Uy).
    {"key": "VitalSkill_LongTucPhanThien", "vi": "Long Tức Phần Thiên", "en": "Dragon Breath Sears Heaven",
     "element": "loi", "attack_type": "magical",
     "dmg_scale": {"atk": 1.3, "matk": 1.3}, "base_dmg": 420, "mp_cost": 220, "cooldown": 7,
     "effects": ["DebuffSocDien"], "effect_chances": {"DebuffSocDien": 0.6},
     "vital_rider": {"label": "🐉 Long Uy", "true_dmg_pct_of_dmg": 0.30}},
    # Phượng Vũ: heals 12% max HP + cleanses 1 debuff each cast; once the
    # Niết Bàn revive has been consumed, adds a vengeance true-damage chunk.
    {"key": "VitalSkill_PhuongVuCuuThien", "vi": "Phượng Vũ Cửu Thiên", "en": "Phoenix Dance of Nine Heavens",
     "element": "hoa", "attack_type": "magical",
     "dmg_scale": {"atk": 0.0, "matk": 1.7}, "base_dmg": 400, "mp_cost": 210, "cooldown": 7,
     "effects": ["DebuffThieuDot"], "effect_chances": {"DebuffThieuDot": 0.7},
     "vital_rider": {"label": "🐦‍🔥 Niết Bàn Vũ", "self_heal_pct_max_hp": 0.12,
                     "cleanse_count": 1, "revenge_after_revive_pct": 0.40}},
    # Thụy Quang: raises a 15%-max-HP shield and smites +8% per debuff on
    # the target (cap 6) as capped true damage.
    {"key": "VitalSkill_KyLanThuyQuang", "vi": "Kỳ Lân Thụy Quang", "en": "Qilin Auspicious Light",
     "element": "quang", "attack_type": "magical",
     "dmg_scale": {"atk": 0.0, "matk": 1.6}, "base_dmg": 380, "mp_cost": 200, "cooldown": 7,
     "vital_rider": {"label": "🌟 Thụy Quang", "shield_pct_max_hp": 0.15,
                     "per_debuff_bonus_pct": 0.08, "per_debuff_cap": 6}},
    # The awakening kit adds bonus_base_dmg_per_self_def/shield — the tortoise
    # slam scales with the wall itself; the crash also binds (Trói Buộc).
    {"key": "VitalSkill_HuyenVuTranHai", "vi": "Huyền Vũ Trấn Hải", "en": "Black Tortoise Quells the Sea",
     "element": "thuy", "attack_type": "physical",
     "dmg_scale": {"atk": 1.0, "matk": 1.0}, "base_dmg": 380, "mp_cost": 200, "cooldown": 7,
     "effects": ["DebuffTroBuoc"], "effect_chances": {"DebuffTroBuoc": 0.5}},
]

# Skill-family element name in the Enemy<Elem>_T<n> ladder.
_SKILL_ELEM = {
    "kim": "Kim", "moc": "Moc", "thuy": "Thuy", "hoa": "Hoa", "tho": "Tho",
    "loi": "Loi", "phong": "Phong", "quang": "Quang", "am": "Am",
}
# EnemyQuang_R9 does not exist — Quang beasts fall back to T3 at tier 9.
_MISSING_R9 = {"quang"}

# Essence drop weight per rarity (out of 1,000,000). Deliberately very
# stingy — Bách Thể Chú Linh is a long-arc progression AND essences are
# blocked entirely on auto-repeat runs (see CombatSession._roll_loot), so
# these rates price deliberate, hands-on clears only. The late-realm
# bloods (legendary) sit a further 10× below the early ladder: the Tứ
# Hung's blood is a drop event, not a farm loop.
_ESSENCE_WEIGHT: dict[str, int] = {
    "normal":    150_000,   # 15%
    "magic":     150_000,   # 15%
    "rare":      150_000,   # 15%
    "legendary":  15_000,   # 1.5%
}
# Essence qty per zone tier.
_ESSENCE_QTY = {1: (1, 1), 3: (1, 1), 5: (1, 2), 7: (1, 2), 9: (1, 3)}
# Mythic essence drop weight per zone tier (out of 1,000,000).
_MYTHIC_WEIGHT = {5: 2_000, 7: 3_500, 9: 5_000}
# Mythic-beast fallback pool weight per zone tier (fires this often, then
# picks one same-zone family). R5 falls back to rare bloods (base ladder);
# R7/R9 fall back to legendary bloods — 10× rarer to match their own tables.
_FALLBACK_WEIGHT = {5: 120_000, 7: 12_000, 9: 12_000}


def _skills(element: str, second: str, tier: int, mythic: bool) -> list[str]:
    """Two skills minimum (anti-whiff rule); mythics add an Apex nuke at 7/9."""
    e = _SKILL_ELEM[element]
    s = _SKILL_ELEM[second]
    cfg = TIERS[tier]["skill"]
    if tier == 9:
        base = f"Enemy{e}_T3" if element in _MISSING_R9 else f"Enemy{e}_R9"
        out = [base, f"Enemy{s}_T3"]
        if mythic:
            out.append(
                "EnemyApex_R9_Magical"
                if element in ("am", "hoa", "quang", "thuy")
                else "EnemyApex_R9_Physical"
            )
        return out
    if tier == 7:
        out = [f"Enemy{e}_T3", f"Enemy{s}_T3"]
        if mythic:
            out.append(
                "EnemyApex_R7_Magical"
                if element in ("am", "hoa", "quang", "thuy")
                else "EnemyApex_R7_Physical"
            )
        return out
    return [f"Enemy{e}_{cfg}", f"Enemy{s}_{cfg}"]


def _scale(band: tuple, arch: tuple, premium: tuple | None = None) -> tuple[int, int, int, int, int]:
    hp, atk, matk, dfn, spd = band
    ah, aa, am, ad, ds = arch
    hp, atk, matk, dfn, spd = hp * ah, atk * aa, matk * am, dfn * ad, spd + ds
    if premium:
        ph, pa, pm, pd, ps = premium
        hp, atk, matk, dfn, spd = hp * ph, atk * pa, matk * pm, dfn * pd, spd + ps
    return int(round(hp / 10) * 10), int(round(atk)), int(round(matk)), int(round(dfn)), int(spd)


def _enemy(key: str, vi: str, en: str, element: str, second: str, tier: int,
           arch: tuple, loot_key: str, mythic: bool) -> dict:
    cfg = TIERS[tier]
    hp, atk, matk, dfn, spd = _scale(
        BANDS[tier], arch, MYTHIC_PREMIUM if mythic else None,
    )
    return {
        "key": key,
        "vi": vi,
        "en": en,
        "rank": cfg["rank"],
        "realm_level": tier,
        "element": element,
        "beast_type": "than_thu" if mythic else "hung_thu",
        "base_hp": hp,
        "base_atk": atk,
        "base_matk": matk,
        "base_def": dfn,
        "base_spd": spd,
        "hp_scale": cfg["hp_scale"],
        "mp_regen_pct": cfg["mp_regen"],
        "base_res": {element: 0.03 if not mythic else 0.06},
        "skill_keys": _skills(element, second, tier, mythic),
        "loot_table_key": loot_key,
    }


def _beast_key(family: str, tier: int) -> str:
    camel = "".join(p.capitalize() for p in family.split("_"))
    return f"Beast{camel}_R{tier}"


def _power_scale(kit: dict) -> dict:
    """Bake ``_KIT_POWER_MULT`` into a stat kit. Bools and ``_KIT_NO_SCALE``
    keys keep their authored values; nested dicts scale per-sub-key."""
    out: dict = {}
    for k, v in kit.items():
        if isinstance(v, bool) or k in _KIT_NO_SCALE:
            out[k] = v
        elif isinstance(v, dict):
            out[k] = {
                sk: (int(round(sv * _KIT_POWER_MULT)) if isinstance(sv, int)
                     else round(sv * _KIT_POWER_MULT, 4))
                if isinstance(sv, (int, float)) and not isinstance(sv, bool) else sv
                for sk, sv in v.items()
            }
        elif isinstance(v, int):
            out[k] = int(round(v * _KIT_POWER_MULT))
        elif isinstance(v, float):
            out[k] = round(v * _KIT_POWER_MULT, 4)
        else:
            out[k] = v
    return out


def _with_hp(kit: dict, hp_pct: float) -> dict:
    out = dict(kit)
    out["hp_pct"] = round(out.get("hp_pct", 0.0) + hp_pct, 4)
    return out


def build_essence_items() -> list[dict]:
    items: list[dict] = []
    for family, cfg in FAMILIES.items():
        meta = RARITY_META[cfg["rarity"]]
        awakening = {
            "threshold": meta["awaken_threshold"],
            "stat_bonuses": _power_scale(cfg["awaken"]),
            "desc_vi": cfg["awaken_desc"],
        }
        if cfg.get("skill"):
            awakening["granted_skill"] = cfg["skill"]
        items.append({
            "key": cfg["item"],
            "vi": f"Tinh Huyết {cfg['vi']}",
            "en": f"{cfg['en']} Vital Essence",
            "type": "vital_essence",
            "essence_key": family,
            "essence_tier": cfg["rarity"],
            "archetype": cfg["archetype"],
            "element": cfg["element"],
            "grade": meta["grade"],
            "shop_price_merit": 0,
            "stat_bonuses": _with_hp(_power_scale(cfg["stats"]),
                                     _RARITY_HP_PCT[cfg["rarity"]]),
            "awakening": awakening,
            "description_vi": f"Tinh huyết của {cfg['vi']} nơi Thập Vạn Đại Sơn. {cfg['desc']}",
        })
    for family, cfg in MYTHICS.items():
        items.append({
            "key": cfg["item"],
            "vi": cfg["item_vi"],
            "en": cfg["item_en"],
            "type": "vital_essence",
            "essence_key": family,
            "essence_tier": "mythic",
            "archetype": cfg["archetype"],
            "element": cfg["element"],
            "grade": 4,
            "shop_price_merit": 0,
            "stat_bonuses": _with_hp(_power_scale(cfg["stats"]),
                                     _RARITY_HP_PCT["mythic"]),
            "awakening": {
                "threshold": MYTHIC_AWAKEN_THRESHOLD,
                "stat_bonuses": _with_hp(_power_scale(cfg["awaken"]),
                                         _MYTHIC_AWAKEN_HP_PCT),
                "desc_vi": cfg["awaken_desc"],
                "granted_skill": cfg["skill"],
            },
            "description_vi": cfg["desc"],
        })
    return items


def build_awakening_skills() -> list[dict]:
    skills: list[dict] = []
    for s in AWAKENING_SKILLS:
        entry = {
            "key": s["key"],
            "vi": s["vi"],
            "en": s["en"],
            "scroll_grade": 4,
            "no_scroll": True,
            "category": "attack",
            "element": s["element"],
            "attack_type": s["attack_type"],
            "dmg_scale": s["dmg_scale"],
            "mp_cost": s["mp_cost"],
            "cooldown": s["cooldown"],
            "base_dmg": s["base_dmg"],
            "formation_key": None,
        }
        if s.get("effects"):
            entry["effects"] = s["effects"]
            entry["effect_chances"] = s["effect_chances"]
        # Optional signature mechanics — multi-hit flurry and the awakening
        # rider spec (see combat/skill_extras.apply_vital_rider).
        for opt in ("hit_count", "vital_rider"):
            if s.get(opt):
                entry[opt] = s[opt]
        skills.append(entry)
    return skills


def main() -> None:
    enemies_dir = ROOT / "enemies" / "thap_van_dai_son"
    enemies_dir.mkdir(parents=True, exist_ok=True)

    loot_tables: dict[str, list[dict]] = {}
    pools: dict[int, list[str]] = {t: [] for t in TIERS}
    entries_by_tier: dict[int, list[dict]] = {t: [] for t in TIERS}

    # Farmable families — spawn in their rarity's zones.
    for family, cfg in FAMILIES.items():
        meta = RARITY_META[cfg["rarity"]]
        for tier in meta["zones"]:
            vi, en = cfg["names"][tier]
            key = _beast_key(family, tier)
            loot_key = f"LootTVDS_{family}_R{tier}"
            qmin, qmax = _ESSENCE_QTY[tier]
            entries_by_tier[tier].append(_enemy(
                key, vi, en, cfg["element"], cfg["second"], tier,
                cfg["arch"], loot_key, mythic=False,
            ))
            pools[tier].append(key)
            loot_tables[loot_key] = [
                {"item_key": cfg["item"], "weight": _ESSENCE_WEIGHT[cfg["rarity"]],
                 "qty_min": qmin, "qty_max": qmax},
            ]

    # Mythical divine beasts.
    for family, cfg in MYTHICS.items():
        for tier in cfg["tiers"]:
            vi, en = cfg["names"][tier]
            key = _beast_key(family, tier)
            loot_key = f"LootTVDS_{family}_R{tier}"
            qmin, qmax = _ESSENCE_QTY[tier]
            entries_by_tier[tier].append(_enemy(
                key, vi, en, cfg["element"], cfg["second"], tier,
                cfg["arch"], loot_key, mythic=True,
            ))
            pools[tier].append(key)
            # Fallback drops are same-zone families so the loot always
            # matches the zone's rarity ladder.
            loot_tables[loot_key] = [
                {"item_key": cfg["item"], "weight": _MYTHIC_WEIGHT[tier],
                 "qty_min": 1, "qty_max": 1},
            ] + [
                {"item_key": FAMILIES[fb]["item"], "weight": _FALLBACK_WEIGHT[tier],
                 "qty_min": qmin, "qty_max": qmax, "pool_id": f"{family}_fallback"}
                for fb in cfg["fallback"][tier]
            ]

    for tier, entries in entries_by_tier.items():
        (enemies_dir / f"r{tier:02d}.json").write_text(
            json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # ── Dungeons — 5 tiers, entry recommendation every other realm ──────────
    zone_meta = {
        1: ("Ngoại Vi",           0, 4, 2,   150,   0),
        3: ("Sơn Lâm",            2, 4, 2,   600,   5),
        5: ("Thâm Sơn",           4, 4, 2,  2400,  20),
        7: ("Cấm Sơn",            6, 4, 3,  9000,  80),
        9: ("Sơn Tâm Thánh Địa",  8, 4, 3, 28000, 200),
    }
    dungeons: list[dict] = []
    for tier, (zone, req, waves, boss_min, merit, stones) in zone_meta.items():
        myth_note = (
            " Thần thú thượng cổ ẩn hiện — đánh bại mới có cơ hội thu được "
            "**Tinh Huyết thần thú**."
            if tier >= 5 else ""
        )
        dungeons.append({
            "key": f"DungeonTVDS_R{tier}",
            "vi": f"Thập Vạn Đại Sơn — {zone}",
            "en": f"Myriad Great Mountains — Tier {tier}",
            "description": (
                "Sơn mạch trùng điệp nơi vạn thú tranh hùng. Yêu thú tại đây "
                "rớt **Tinh Huyết** — nguyên liệu chú nhập Bộ Vị Cơ Thể của "
                f"Thể Tu.{myth_note}"
            ),
            "dungeon_type": "thap_van_dai_son",
            "required_qi_realm": req,
            "wave_count": waves,
            "boss_min_grade_idx": boss_min,
            "enemy_pool": pools[tier],
            "merit_reward": merit,
            "stone_reward": stones,
        })

    essence_items = build_essence_items()
    (ROOT / "items" / "vital_essences.json").write_text(
        json.dumps(essence_items, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    awakening_skills = build_awakening_skills()
    (ROOT / "skills" / "player" / "vital_awakening.json").write_text(
        json.dumps(awakening_skills, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (ROOT / "loot_tables" / "thap_van_dai_son.json").write_text(
        json.dumps(loot_tables, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (ROOT / "dungeons" / "thap_van_dai_son.json").write_text(
        json.dumps(dungeons, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    total_enemies = sum(len(v) for v in entries_by_tier.values())
    print(
        f"Generated {len(essence_items)} essences, {len(awakening_skills)} "
        f"awakening skills, {total_enemies} beasts, {len(loot_tables)} loot "
        f"tables, {len(dungeons)} dungeons."
    )


if __name__ == "__main__":
    main()
